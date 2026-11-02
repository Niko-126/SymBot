"""闭源记忆内核的运行时解封器（`app/memory/_vault.bin` → 临时目录 → sys.path）。

只做四件事，别的什么都不干：

1. **解密**：`_vault.bin` 是「加密 ZIP」容器，算法只用标准库——
   `PBKDF2-HMAC-SHA256(机器指纹 或 口令, 盐)` 出主密钥，再岔成 encrypt / mac 两把子钥；
   密文 = 明文字节 XOR `HMAC-SHA256` 计数器流（PRF-CTR），校验 = 先加密后 MAC
   （`HMAC-SHA256(mac_key, 头 + 密文)`）。没有 `cryptography` 也能跑，不引任何新依赖。
2. **落地**：解到 `%TEMP%\\symmem-*`，**绝不落到仓库目录**（`_guard_outside_repo()` 守着）。
3. **注入**：把解包根插到 `sys.path[0]`，这样 `import symvolve_memory` 就能用。
4. **擦除**：`atexit` 里整目录删掉，进程退出即回收。

为什么不在这里 import `symvolve_memory`：
解封「谁先谁后」是 `app/memory/api.py` 的事（它还要先试源码路径）。本模块保持纯工具，
被 `scripts/build_vendor_memory.py` 反过来复用同一套 `seal_payload()` / `open_payload()`——
格式只有一份实现，打包端和解包端不可能写出两个不一样的容器。

容器布局（全部小端）：

    ┌──────────── 59 字节头 ────────────┐
    │ magic(9) ver(H) iters(I) mode(B)  │
    │ pad(3) salt(16) nonce(16) ct_len(Q)│
    ├──────────── 密文 ct_len 字节 ──────┤
    ├──────────── tag(32) ──────────────┤
    └───────────────────────────────────┘

`mode`：0 = 绑定机器指纹（默认）｜1 = 绑定环境变量口令（换机搬运用，见 docs/07 §7）。

口令环境变量：`SYM_MEMORY_VAULT_PASSPHRASE`；强制只走容器（跳过源码路径）：
`SYM_MEMORY_FORCE_VAULT=1`（`--verify` 与演练用）。
﻿‍​﻿‌‌﻿‍‌​‌‌‌﻿‌‌‌﻿​‌‍​﻿﻿‌​﻿‌‌​​​‌﻿"""

from __future__ import annotations

import atexit
import hashlib
import hmac
import io
import os
import pathlib
import platform
import shutil
import struct
import sys
import tempfile
import threading
import uuid
import zipfile

__all__ = [
    "VAULT_NAME",
    "DEFAULT_ITERS",
    "PASSPHRASE_ENV",
    "FORCE_ENV",
    "VaultError",
    "VaultLocked",
    "VaultCorrupt",
    "VaultMissing",
    "MODE_MACHINE",
    "MODE_PASSPHRASE",
    "vault_path",
    "machine_secret",
    "fingerprint_id",
    "derive_keys",
    "seal_payload",
    "open_payload",
    "inspect_vault",
    "ensure_core",
    "cleanup",
    "extracted_root",
]

#: 产物名（与 `app/memory/api.py` 同目录）
VAULT_NAME = "_vault.bin"
#: 容器版本 / 布局版本
FORMAT_VERSION = 1
#: magic 定长 9 字节
MAGIC = b"SYMVAULT\x01"
_HEADER = struct.Struct("<9sHIB3x16s16sQ")
TAG_BYTES = 32
SALT_BYTES = 16
NONCE_BYTES = 16
#: PBKDF2 迭代数：本机实测 12 万次约 0.1 秒，够挡住"拿到产物直接暴力猜"
DEFAULT_ITERS = 120_000

MODE_MACHINE = 0
MODE_PASSPHRASE = 1
PASSPHRASE_ENV = "SYM_MEMORY_VAULT_PASSPHRASE"
FORCE_ENV = "SYM_MEMORY_FORCE_VAULT"
TMP_PREFIX = "symmem-"

_HERE = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]

_LOCK = threading.RLock()
_STATE: dict[str, pathlib.Path | None] = {"root": None}


class VaultError(RuntimeError):
    """容器相关错误的总根。"""


class VaultMissing(VaultError):
    """`_vault.bin` 不存在（还没打包，或者分发时漏了这个文件）。"""


class VaultLocked(VaultError):
    """密钥不对：换机了、口令没给、或者不是这台机器封的。"""


class VaultCorrupt(VaultError):
    """容器损坏 / 被改过（MAC 校验不过、头字段非法）。"""


# ---------------------------------------------------------------- 指纹 --

def machine_secret() -> bytes:
    """机器指纹原文（**只在本机内存里用，不写盘、不进文档**）。

    组成：系统 + 架构 + 主机名（小写）+ 网卡 MAC 的 48 位整数。
    主机名和 MAC 任一变了，指纹就变——换机 / 改计算机名 / 换网卡都要重新封一次，
    这是"绑定"的代价，迁移流程见 docs/07 §7.5。
    """
    node = uuid.getnode()
    raw = "|".join(
        (
            platform.system() or "",
            platform.machine() or "",
            (platform.node() or "").strip().lower(),
            f"{node:012x}",
        )
    )
    return raw.encode("utf-8")


def fingerprint_id() -> str:
    """指纹的短摘要（16 位十六进制）。诊断/授权对账用，反推不出原文。"""
    return hashlib.sha256(machine_secret()).hexdigest()[:16]


# ---------------------------------------------------------------- 密码学 --

def derive_keys(secret: bytes, salt: bytes, iters: int = DEFAULT_ITERS) -> tuple[bytes, bytes]:
    """PBKDF2-HMAC-SHA256 出主密钥，再岔成 (加密钥, 校验钥)。"""
    if not secret:
        raise VaultLocked("密钥材料为空：既没有机器指纹也没有口令")
    if len(salt) != SALT_BYTES:
        raise VaultCorrupt(f"盐长度非法：{len(salt)} != {SALT_BYTES}")
    if iters <= 0:
        raise VaultCorrupt(f"迭代数非法：{iters}")
    master = hashlib.pbkdf2_hmac("sha256", secret, salt, int(iters), dklen=32)
    enc_key = hmac.new(master, b"symvault/enc", hashlib.sha256).digest()
    mac_key = hmac.new(master, b"symvault/mac", hashlib.sha256).digest()
    return enc_key, mac_key


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """PRF-CTR 密钥流：HMAC-SHA256(key, nonce ‖ counter64)。"""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def _xor(data: bytes, stream: bytes) -> bytes:
    if len(data) != len(stream):
        raise VaultCorrupt("密钥流长度不一致")
    return bytes(a ^ b for a, b in zip(data, stream))


# ---------------------------------------------------------------- 封 / 解 --

def seal_payload(
    payload: bytes,
    *,
    secret: bytes | None = None,
    mode: int = MODE_MACHINE,
    iters: int = DEFAULT_ITERS,
    salt: bytes | None = None,
    nonce: bytes | None = None,
) -> bytes:
    """把明文载荷封成容器字节串（打包端用；`--mode seal` 调它）。

    `secret` 省略时按 `mode` 自动取：机器指纹或 `SYM_MEMORY_VAULT_PASSPHRASE`。
    """
    if mode not in (MODE_MACHINE, MODE_PASSPHRASE):
        raise VaultCorrupt(f"未知 mode：{mode}")
    if secret is None:
        secret = _secret_for(mode)
    salt = os.urandom(SALT_BYTES) if salt is None else salt
    nonce = os.urandom(NONCE_BYTES) if nonce is None else nonce
    enc_key, mac_key = derive_keys(secret, salt, iters)
    ct = _xor(payload, _keystream(enc_key, nonce, len(payload)))
    header = _HEADER.pack(MAGIC, FORMAT_VERSION, int(iters), int(mode), salt, nonce, len(ct))
    tag = hmac.new(mac_key, header + ct, hashlib.sha256).digest()
    return header + ct + tag


def _secret_for(mode: int) -> bytes:
    if mode == MODE_PASSPHRASE:
        phrase = os.environ.get(PASSPHRASE_ENV, "")
        if not phrase:
            raise VaultLocked(f"容器是口令模式，但环境变量 {PASSPHRASE_ENV} 没设置")
        return phrase.encode("utf-8")
    return machine_secret()


def parse_header(blob: bytes) -> dict[str, object]:
    """只读头，不解密（诊断用）。"""
    if len(blob) < _HEADER.size + TAG_BYTES:
        raise VaultCorrupt(f"容器太短：{len(blob)} 字节")
    magic, version, iters, mode, salt, nonce, ct_len = _HEADER.unpack_from(blob, 0)
    if magic != MAGIC:
        raise VaultCorrupt("magic 不对：这不是 symvolve_memory 的加密容器")
    if version != FORMAT_VERSION:
        raise VaultCorrupt(f"容器版本 {version} 不认识（本机支持 {FORMAT_VERSION}）")
    if mode not in (MODE_MACHINE, MODE_PASSPHRASE):
        raise VaultCorrupt(f"mode {mode} 不认识")
    expect = _HEADER.size + int(ct_len) + TAG_BYTES
    if len(blob) != expect:
        raise VaultCorrupt(f"长度对不上：头说 {expect}，实际 {len(blob)}")
    return {
        "version": int(version),
        "iters": int(iters),
        "mode": int(mode),
        "mode_name": "machine" if int(mode) == MODE_MACHINE else "passphrase",
        "salt": bytes(salt),
        "nonce": bytes(nonce),
        "ct_len": int(ct_len),
        "header_bytes": _HEADER.size,
        "blob_bytes": len(blob),
    }


def open_payload(blob: bytes, *, secret: bytes | None = None) -> bytes:
    """校验并解出明文载荷（先 MAC 后解密，篡改的容器一个字节都解不出来）。"""
    head = parse_header(blob)
    salt = head["salt"]
    nonce = head["nonce"]
    ct_len = head["ct_len"]
    mode = head["mode"]
    assert isinstance(salt, bytes) and isinstance(nonce, bytes)
    if secret is None:
        secret = _secret_for(int(mode))
    enc_key, mac_key = derive_keys(secret, salt, int(head["iters"]))
    header = blob[: _HEADER.size]
    ct = blob[_HEADER.size: _HEADER.size + ct_len]
    tag = blob[_HEADER.size + ct_len:]
    want = hmac.new(mac_key, header + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(want, tag):
        raise VaultLocked(
            "容器校验不过：机器指纹不匹配（换机了？）或产物被改过；"
            f"本机指纹 {fingerprint_id()}"
        )
    return _xor(ct, _keystream(enc_key, nonce, ct_len))


def inspect_vault(path: pathlib.Path | None = None) -> dict[str, object]:
    """产物自检：头字段 + 载荷摘要 + 文件条数（不解包到磁盘）。"""
    p = pathlib.Path(path or vault_path())
    blob = p.read_bytes()
    head = parse_header(blob)
    payload = open_payload(blob)
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = zf.namelist()
        bad = zf.testzip()
    if bad is not None:
        raise VaultCorrupt(f"ZIP 成员损坏：{bad}")
    return {
        "path": str(p),
        "bytes": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "files": len(names),
        "mode": head["mode_name"],
        "version": head["version"],
        "iters": head["iters"],
        "header_bytes": head["header_bytes"],
        "ct_len": head["ct_len"],
    }


# ---------------------------------------------------------------- 落地 --

def vault_path() -> pathlib.Path:
    return _HERE / VAULT_NAME


def _inside(path: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _guard_outside_repo(root: pathlib.Path) -> None:
    """明文只许待在仓库外面。这是硬约束，不是建议。"""
    if _inside(root, _REPO_ROOT):
        raise VaultError(f"拒绝把明文解到仓库目录里：{root}")


def extracted_root() -> pathlib.Path | None:
    return _STATE.get("root")


def ensure_core(vault: pathlib.Path | None = None) -> pathlib.Path | None:
    """确保临时目录里有一份可 import 的 `symvolve_memory`。

    返回解包根目录；如果内核已经由别处（源码路径）装进来了，返回 ``None`` 且什么都不做。
    同一个进程里重复调用是幂等的（只解一次）。
    """
    with _LOCK:
        root = _STATE.get("root")
        if root is not None:
            return root
        if "symvolve_memory" in sys.modules:
            # 源码路径已经先装好了，没必要再解一份（两份类对象会互相不认 isinstance）
            return None
        path = pathlib.Path(vault or vault_path())
        if not path.is_file():
            raise VaultMissing(f"找不到加密容器：{path}（先跑 --mode seal）")
        blob = path.read_bytes()
        payload = open_payload(blob)
        root = pathlib.Path(tempfile.mkdtemp(prefix=TMP_PREFIX))
        _guard_outside_repo(root)
        _STATE["root"] = root
        atexit.register(cleanup)
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                zf.extractall(root)
        except Exception:
            cleanup()
            raise
        if str(root) in sys.path:
            sys.path.remove(str(root))
        sys.path.insert(0, str(root))
        return root


def cleanup() -> None:
    """删掉临时解包目录并把路径摘出 `sys.path`（atexit 自动调，测试也可手动调）。"""
    with _LOCK:
        root = _STATE.get("root")
        if root is None:
            return
        _STATE["root"] = None
        sp = str(root)
        while sp in sys.path:
            sys.path.remove(sp)
        shutil.rmtree(sp, ignore_errors=True)
