"""软水印：把"这段代码出自谁手"藏进文件里，人眼看不见、语法不破、行为不变。

为什么是零宽字符（docs/33）：

- 可见的版权头一秒钟被删掉，注释里的作者名会被重构工具顺手清掉；
  而 U+200B / U+200C / U+200D / U+FEFF 这四类字符在编辑器里不占宽度、
  在 diff 里几乎不显形，复制粘贴和打包却会一起带走。
- 指纹用 HMAC-SHA256 派生：没有密钥的人只能看到"这里有一串零宽字符"，
  既解不出内容，也伪造不出一份等价的水印。

四条铁律：

1. **不动行为**：零宽串只落在 docstring 内部或 `#` 注释行尾，不进逻辑。
2. **不破语法**：CPython 3.14 起，字符串与注释之外的零宽字符会直接
   `SyntaxError: invalid non-printable character`，所以本模块只在
   docstring 内、或注释行尾下刀；改完由调用方过一遍 `ast.parse`。
3. **幂等**：`embed()` 先 `strip()` 再写，重复跑不叠加。
4. **可剥离**：`strip()` 按"定界符 + 定长载荷"精确切除，不做模糊清理。

密钥三级（见 `key_bytes()`）：`SYMEM_WM_KEY` → `scripts/.wm.key` → 内置派生盐。
派生盐是公开常量，只够"认出这段代码是我的"，不够"证明是谁抄的"——
所以这个状态下 `embed()` 拒绝签发，`verify()` 只报 unverified。

这一层能给的是**廉价证据**，不是版权登记，也不是防抄。边界写在 docs/33。
﻿‌‍‍﻿﻿​﻿﻿﻿​​‌‌​﻿‍‍​​​‍​‌​﻿‌‌﻿​‍‌﻿﻿"""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import pathlib
import re
import secrets
import tokenize
from typing import Any

__all__ = [
    "ENVELOPE_CHARS",
    "META_FIELD",
    "META_VALUE",
    "ZERO_WIDTH",
    "derived_const",
    "embed",
    "extract",
    "key_bytes",
    "key_status",
    "mark_for",
    "meta",
    "source_files",
    "strip",
    "struct_field",
    "verify",
    "verify_report",
    "write_key",
]

#: 仓库根（app/core/watermark.py → 上两级）
ROOT = pathlib.Path(__file__).resolve().parents[2]
#: 默认密钥文件位置（gitignore，不进库）
KEY_FILE = ROOT / "scripts" / ".wm.key"
ENV_VAR = "SYMEM_WM_KEY"
ENV_KEY_FILE = "SYMEM_WM_KEY_FILE"

#: base-4 的 0/1/2/3。定界符与 3 共用 U+FEFF，所以解析按"定长窗口"做，不靠朴素正则
ZERO_WIDTH = ("\u200b", "\u200c", "\u200d", "\ufeff")
DELIMITER = "\ufeff"
#: 8 字节 = 64 bit = 32 个 base-4 位
PAYLOAD_CHARS = 32
#: 定界符 + 载荷 + 定界符
ENVELOPE_CHARS = PAYLOAD_CHARS + 2
#: HMAC 消息前缀：换前缀等于换一代水印，老文件就认不出来了，别随手改
TAG = b"symbot"

_DIGIT = {ch: i for i, ch in enumerate(ZERO_WIDTH)}
_ALPHABET = frozenset(ZERO_WIDTH)
_HEX = frozenset("0123456789abcdefABCDEF")

#: 内置派生盐：公开、只用于"认出自己"，不能当权属证据
_DERIVED_SALT = b"symbot.watermark.derived-salt.v1"

#: 密钥来源
SOURCE_ENV = "env"
SOURCE_FILE = "file"
SOURCE_DERIVED = "derived"

#: 结构指纹用到的"看起来毫无异常"的字段名池（按路径派生，稳定）
_STRUCT_FIELDS = ("trace_seq", "boot_tick", "span_seq", "frame_ms", "beat_id", "slot_idx")

#: 打水印时扫过就跳的目录：运行期数据、第三方、构建产物、私有目录
SKIP_DIRS = frozenset({
    ".venv", ".git", ".tmp", "__pycache__", "node_modules", ".third_party",
    ".ruff_cache", ".pytest_cache", ".mypy_cache", ".idea", ".vscode",
    "dist", "build", "exports", "data", "Girl-memory", ".symvolve-preview",
})
SOURCE_SUFFIXES = (".py", ".md", ".html", ".css")
#: 超过这个体积的文件不当源码处理（避免吃到压缩包/数据文件）
MAX_TEXT_BYTES = 2 * 1024 * 1024

_STRING_HEAD = re.compile(r"([rRbBuUfF]{0,2})(\"\"\"|'''|\"|')")
_COMMENT_LINE = re.compile(r"^[ \t]*#[^\r\n]*", re.MULTILINE)
_LINE_START = re.compile(r"^.*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# 密钥
# ---------------------------------------------------------------------------
def _key_file() -> pathlib.Path:
    """密钥文件路径：`SYMEM_WM_KEY_FILE` 可覆盖（换机托管 / 自测用）。"""
    override = os.environ.get(ENV_KEY_FILE, "").strip()
    if override:
        p = pathlib.Path(override)
        return p if p.is_absolute() else ROOT / p
    return KEY_FILE


def _derive_key(raw: str) -> bytes:
    """把一段外部字符串变成密钥字节：是 hex 就直接用，否则先哈希。"""
    s = raw.strip()
    if s and len(s) % 2 == 0 and all(c in _HEX for c in s):
        try:
            data = bytes.fromhex(s)
        except ValueError:
            data = b""
        if len(data) >= 16:
            return data
    return hashlib.sha256(s.encode("utf-8")).digest()


def key_bytes() -> tuple[bytes, str]:
    """取密钥，返回 `(密钥, 来源)`。三级回退：环境变量 > 密钥文件 > 派生盐。"""
    raw = os.environ.get(ENV_VAR, "").strip()
    if raw:
        return _derive_key(raw), SOURCE_ENV
    try:
        text = _key_file().read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    if text:
        return _derive_key(text), SOURCE_FILE
    return hashlib.sha256(_DERIVED_SALT).digest(), SOURCE_DERIVED


def key_status() -> dict[str, Any]:
    """给 CLI 看的密钥状态：只报来源与能不能签发，绝不回显密钥本体。"""
    _key, source = key_bytes()
    return {
        "source": source,
        "state": "ready" if source != SOURCE_DERIVED else "unverified",
        "signing": source != SOURCE_DERIVED,
        "key_file": str(_key_file()),
        "env": ENV_VAR,
    }


def write_key(path: str | pathlib.Path | None = None, *, force: bool = False) -> dict[str, Any]:
    """生成 32 字节随机密钥（64 位 hex）写进密钥文件。已存在且没 force 就不动。"""
    target = pathlib.Path(path) if path else _key_file()
    if not target.is_absolute():
        target = ROOT / target
    if target.exists() and not force:
        return {"ok": False, "path": str(target), "reason": "exists"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
    try:  # Windows 上 chmod 只是尽力而为
        target.chmod(0o600)
    except OSError:
        pass
    return {"ok": True, "path": str(target), "bytes": 32}


# ---------------------------------------------------------------------------
# 指纹
# ---------------------------------------------------------------------------
def normalize(relpath: str | pathlib.Path) -> str:
    """统一成"仓库相对路径 + 正斜杠"。大小写敏感，换路径等于换指纹。"""
    p = str(relpath).replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _fingerprint(relpath: str | pathlib.Path, key: bytes) -> bytes:
    msg = TAG + normalize(relpath).encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).digest()[:8]


def _encode_payload(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    digits = []
    for _ in range(PAYLOAD_CHARS):
        digits.append(ZERO_WIDTH[n & 3])
        n >>= 2
    return "".join(reversed(digits))


def _decode_payload(payload: str) -> bytes:
    n = 0
    for ch in payload:
        n = (n << 2) | _DIGIT.get(ch, 0)
    return (n & ((1 << 64) - 1)).to_bytes(8, "big")


def mark_for(relpath: str | pathlib.Path) -> str:
    """这个路径对应的水印串（34 个零宽字符）。拿不到密钥时返回空串。"""
    key, source = key_bytes()
    if source == SOURCE_DERIVED:
        return ""
    return DELIMITER + _encode_payload(_fingerprint(relpath, key)) + DELIMITER


def fingerprint_hex(relpath: str | pathlib.Path) -> str:
    """本机比对用的 16 位 hex。别往日志、面板、响应里打。"""
    key, source = key_bytes()
    if source == SOURCE_DERIVED:
        return ""
    return _fingerprint(relpath, key).hex()


# ---------------------------------------------------------------------------
# 取出 / 剥离
# ---------------------------------------------------------------------------
def _envelope_spans(text: str) -> list[tuple[int, int]]:
    """扫出所有"定界符 + 32 位载荷 + 定界符"的窗口（允许定界符出现在载荷里）。"""
    spans: list[tuple[int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        i = text.find(DELIMITER, i)
        if i < 0:
            break
        end = i + ENVELOPE_CHARS
        if end <= n and text[end - 1] == DELIMITER and all(
            ch in _ALPHABET for ch in text[i + 1:end - 1]
        ):
            spans.append((i, end))
            i = end
        else:
            i += 1
    return spans


def extract(text: str) -> list[str]:
    """取出文中所有候选指纹（8 字节 hex，去重保序）。只在本机比对用。"""
    out: list[str] = []
    for a, b in _envelope_spans(text):
        item = _decode_payload(text[a + 1:b - 1]).hex()
        if item not in out:
            out.append(item)
    return out


def strip(text: str) -> str:
    """把所有水印串原样切掉，其他字符一个不动（所以能拿来做幂等）。

    唯一多切一点的情况：兜底写过的那种"整行只有一个 `#` + 水印"的空注释行，
    会把整行（含换行）一起删掉——不删的话 `strip()` 回不到原字节。
    """
    spans = _envelope_spans(text)
    if not spans:
        return text
    spans = [_widen_comment_line(text, a, b) for a, b in spans]
    parts: list[str] = []
    last = 0
    for a, b in spans:
        parts.append(text[last:a])
        last = b
    parts.append(text[last:])
    return "".join(parts)


def _widen_comment_line(text: str, start: int, end: int) -> tuple[int, int]:
    """若水印独占一行、且这行只剩一个 `#`，就把整行划进切除范围。"""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    else:
        line_end += 1  # 连换行一起切，避免留下空行
    head = text[line_start:start]
    tail = text[end:line_end]
    if head == "#" and tail in ("", "\n", "\r\n"):
        return line_start, line_end
    return start, end


def verify(text: str, relpath: str | pathlib.Path) -> bool:
    """有没有、对不对。只有真密钥在手才可能返回 True；派生盐模式永远 False。"""
    key, source = key_bytes()
    if source == SOURCE_DERIVED:
        return False
    return _fingerprint(relpath, key).hex() in extract(text)


def verify_report(text: str, relpath: str | pathlib.Path) -> dict[str, Any]:
    """`verify()` 的带原因版本：state ∈ hit / miss / unverified。"""
    key, source = key_bytes()
    hits = extract(text)
    if source == SOURCE_DERIVED:
        return {"state": "unverified", "found": bool(hits), "count": len(hits)}
    expected = _fingerprint(relpath, key).hex()
    if expected in hits:
        return {"state": "hit", "found": True, "count": len(hits)}
    return {
        "state": "miss",
        "found": bool(hits),
        "count": len(hits),
        "stray": len([h for h in hits if h != expected]),
    }


# ---------------------------------------------------------------------------
# 埋点：docstring 内部 / 注释行尾 / 标记语言的注释里
# ---------------------------------------------------------------------------
def _offset_of(text: str, row: int, col: int) -> int:
    lines = text.splitlines(keepends=True)
    if row < 1 or row > len(lines):
        return -1
    return sum(len(line) for line in lines[: row - 1]) + col


def _py_docstring_site(text: str) -> int | None:
    """模块 docstring 收尾引号之前的字符下标；没有 docstring 就返回 None。"""
    off = 0
    body = text
    if body.startswith("\ufeff"):
        body, off = body[1:], 1
    try:
        first: tokenize.TokenInfo | None = None
        for tok in tokenize.generate_tokens(io.StringIO(body).readline):
            if tok.type in (
                tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
                tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING,
            ):
                continue
            first = tok
            break
        if first is None or first.type != tokenize.STRING:
            return None
        head = _STRING_HEAD.match(first.string)
        if not head:
            return None
        quote = head.group(2)
        if not first.string.endswith(quote) or len(first.string) <= len(quote) * 2:
            return None
        end = _offset_of(body, first.end[0], first.end[1])
        if end < 0:
            return None
        return off + end - len(quote)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None


def _py_comment_site(text: str) -> int | None:
    """第一条普通 `#` 注释的行尾。

    跳过 shebang 与编码声明：零宽字符挂在 shebang 行尾会让 Unix 找不到解释器。
    """
    off = 1 if text.startswith("\ufeff") else 0
    body = text[off:]
    for m in _COMMENT_LINE.finditer(body):
        line = m.group(0).strip()
        if line.startswith("#!"):
            continue
        if "coding:" in line or "coding=" in line:
            continue
        return off + m.end()
    return None


def _place_py(text: str, mark: str) -> str:
    site = _py_docstring_site(text)
    if site is not None:
        return text[:site] + mark + text[site:]
    site = _py_comment_site(text)
    if site is not None:
        return text[:site] + mark + text[site:]
    # 兜底：既没 docstring 也没普通注释，就自己造一行"空注释"（合法、无害）
    off = 1 if text.startswith("\ufeff") else 0
    body = text[off:]
    if body.startswith("#!"):
        nl = body.find("\n")
        pos = off + (nl + 1 if nl >= 0 else len(body))
        return text[:pos] + "#" + mark + "\n" + text[pos:]
    return text[:off] + "#" + mark + "\n" + text[off:]


def _place_md(text: str, mark: str) -> str:
    front = re.match(r"^---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|$)", text, re.DOTALL)
    if front:
        return text[: front.end()] + mark + text[front.end():]
    for m in _LINE_START.finditer(text):
        if m.group(0).strip():
            return text[: m.end()] + mark + text[m.end():]
    return mark + text


def _place_html(text: str, mark: str) -> str:
    i = text.find("<!--")
    if i >= 0:
        return text[: i + 4] + mark + text[i + 4:]
    for tag in ("</head>", "</body>", "</html>"):
        i = text.find(tag)
        if i >= 0:
            return text[:i] + mark + text[i:]
    for m in _LINE_START.finditer(text):
        if m.group(0).strip():
            return text[: m.end()] + mark + text[m.end():]
    return mark + text


def _place_css(text: str, mark: str) -> str:
    i = text.find("/*")
    if i >= 0:
        return text[: i + 2] + mark + text[i + 2:]
    return "/*" + mark + "*/\n" + text


def _place(text: str, mark: str, suffix: str) -> str:
    s = suffix.lower()
    if s == ".py":
        return _place_py(text, mark)
    if s in (".md", ".markdown"):
        return _place_md(text, mark)
    if s in (".html", ".htm"):
        return _place_html(text, mark)
    if s == ".css":
        return _place_css(text, mark)
    return text


def embed(text: str, relpath: str | pathlib.Path) -> str:
    """把水印嵌进文本：先剥旧的，再按路径写新的。同一文本重复跑结果不变。"""
    key, source = key_bytes()
    if source == SOURCE_DERIVED:
        return text  # 只能校验，签发不了
    mark = DELIMITER + _encode_payload(_fingerprint(relpath, key)) + DELIMITER
    body = strip(text)
    return _place(body, mark, pathlib.PurePosixPath(normalize(relpath)).suffix)


# ---------------------------------------------------------------------------
# 结构指纹：常量名 / 字段名由路径派生，稳定、无隐私、不用密钥
# ---------------------------------------------------------------------------
def derived_const(relpath: str | pathlib.Path, modulo: int) -> int:
    """路径派生的整数常量（落在 [0, modulo)）。不含密钥，换机也一样。"""
    m = int(modulo)
    if m <= 1:
        return 0
    digest = hashlib.sha256(TAG + b"-const\x00" + normalize(relpath).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % m


def struct_field(relpath: str | pathlib.Path) -> str:
    """结构指纹用的"看着像普通遥测字段"的名字。"""
    return _STRUCT_FIELDS[derived_const(relpath, len(_STRUCT_FIELDS))]


#: 日志 / 响应 meta 里那个字段名（由 Lead 接进 meta 与日志）
META_FIELD = struct_field("app/core/watermark.py")
#: 该字段的值：稳定整数，删掉、改掉都能被自己认出来"少了一块"
META_VALUE = derived_const("app/core/watermark.py", 9973)


def meta() -> dict[str, int]:
    """`{"<普通字段名>": <稳定整数>}`，直接丢进日志/响应的 meta 里即可。"""
    return {META_FIELD: META_VALUE}


# ---------------------------------------------------------------------------
# 扫描工具：两个 CLI 共用同一套跳过规则，避免两边漂移
# ---------------------------------------------------------------------------
def source_files(
    root: str | pathlib.Path,
    *,
    include_vendor: bool = False,
    suffixes: tuple[str, ...] = SOURCE_SUFFIXES,
) -> list[pathlib.Path]:
    """列出可打水印的文本文件。跳过规则只作用于 root 内部，传进来的 root 本身不跳过。"""
    base = pathlib.Path(root)
    if base.is_file():
        return [base]
    skip = set(SKIP_DIRS)
    if not include_vendor:
        skip.add("vendor")
    out: list[pathlib.Path] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        parts = p.relative_to(base).parts
        if any(part in skip for part in parts[:-1]):
            continue
        if p.suffix.lower() not in suffixes:
            continue
        try:
            if p.stat().st_size > MAX_TEXT_BYTES:
                continue
        except OSError:
            continue
        out.append(p)
    return out
