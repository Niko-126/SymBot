"""从网络直装模块：面板「模块 → 下载安装」的后端。

用户视角只要三步：**贴地址 → 点安装 → 重启/热重载**，不用会 git、不用解压。
所以这里要替他把脏活干完：下载、防目录穿越、找 manifest、校验 id、落到 `modules/<id>/`。

安全边界（写死，不给面板传）：
- 只认 `https://`（以及本机 `http://127.0.0.1`，用于离线自测）；
- 压缩包 ≤ 40 MB，解出来 ≤ 200 MB，成员数 ≤ 4000；
- 拒绝绝对路径、`..`、符号链接式成员（zip 不支持链接但有些工具会塞）；
- 目标 id 必须是 kebab-case 且与目录名一致；已存在时默认拒绝，`force` 才覆盖（先备份）。

为什么放 `app/core/` 而不是 `scripts/`：面板要调它，脚本目录不进运行时导入路径。
﻿﻿​‍﻿​‍​‍‍‌﻿​‍‍‌﻿﻿‌‍‌‌‍‌‌‍‍﻿﻿‌‌‌‍﻿"""

from __future__ import annotations

import io
import json
import pathlib
import re
import shutil
import tempfile
import time
import zipfile
from typing import Any
from urllib.parse import urlparse

#: 压缩包上限（模块都是小脚本，40 MB 已经非常宽松）
MAX_ZIP_BYTES = 40 * 1024 * 1024
#: 解压后总字节上限（防 zip bomb）
MAX_EXTRACT_BYTES = 200 * 1024 * 1024
#: 成员数上限
MAX_MEMBERS = 4000
#: 单次下载超时（秒）
HTTP_TIMEOUT = 60.0
#: id 规则：kebab-case，2–64 字符（与 modules.py 的校验保持一致）
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


class InstallError(RuntimeError):
    """安装失败（参数不对 / 下载失败 / 包结构不对 / 已存在）。"""


def modules_dir() -> pathlib.Path:
    from app.core import modules as modules_mod

    modules_mod.ensure_ready()
    return pathlib.Path(modules_mod.MODULES_DIR)


# ---------------------------------------------------------------- 地址 --


def _allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        return True
    # 本机自测：只放开 127.0.0.1 / localhost 的 http
    return parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")


def normalize_url(raw: str) -> str:
    """把用户贴的东西变成可下载地址。

    支持三种写法：
    - 完整 https 链接（.zip 直链或 GitHub 页面链接）
    - `owner/repo`（GitHub 简写 → codeload 的 main 分支包）
    - `https://github.com/owner/repo`（自动转 codeload）
    """
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        raise InstallError("没给下载地址")
    if re.fullmatch(r"[\w.-]+/[\w.-]+", text):
        owner, repo = text.split("/")
        return f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/main"
    if text.startswith("github.com/"):
        text = "https://" + text
    if "github.com/" in text and not text.endswith(".zip"):
        parts = [p for p in urlparse(text).path.split("/") if p]
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1].removesuffix(".git")
            ref = "main"
            if len(parts) >= 4 and parts[2] in ("tree", "blob"):
                ref = parts[3]
            return f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{ref}"
    if not _allowed(text):
        raise InstallError("只允许 https 链接（本机 127.0.0.1 自测除外）")
    return text


def _download(url: str) -> bytes:
    """下到内存（40 MB 上限），失败抛人话。"""
    if not _allowed(url):
        raise InstallError("不允许的地址：只认 https")
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - httpx 是必装依赖
        raise InstallError(f"缺 httpx，装不了：{exc}") from exc
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True, trust_env=False) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code == 404:
                    raise InstallError("地址 404：仓库/分支名不对，或者不是公开仓库")
                if resp.status_code != 200:
                    raise InstallError(f"下载失败：HTTP {resp.status_code}")
                buf = io.BytesIO()
                total = 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > MAX_ZIP_BYTES:
                        raise InstallError(f"压缩包超过 {MAX_ZIP_BYTES // 1024 // 1024} MB，不装")
                    buf.write(chunk)
                return buf.getvalue()
    except InstallError:
        raise
    except Exception as exc:  # noqa: BLE001 - 超时/断网/DNS 全归到"下载失败"
        raise InstallError(f"下载失败：{type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------- 解包 --


def _safe_extract(zf: zipfile.ZipFile, dest: pathlib.Path) -> list[str]:
    """防目录穿越地解压；返回成员名列表。"""
    members = zf.infolist()
    if len(members) > MAX_MEMBERS:
        raise InstallError(f"包里有 {len(members)} 个文件，超过 {MAX_MEMBERS} 上限")
    root = dest.resolve()
    total = 0
    names: list[str] = []
    for item in members:
        name = item.filename.replace("\\", "/")
        if not name or name.endswith("/"):
            continue
        if name.startswith("/") or ".." in pathlib.PurePosixPath(name).parts:
            raise InstallError(f"包里这个路径不安全：{name}")
        if name.startswith("__MACOSX/"):
            continue
        total += int(item.file_size or 0)
        if total > MAX_EXTRACT_BYTES:
            raise InstallError("解压后超过 200 MB，不装")
        target = (root / name).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise InstallError(f"包里这个路径越界：{name}") from None
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(item) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
        names.append(name)
    return names


def _find_manifest(root: pathlib.Path) -> tuple[pathlib.Path, dict[str, Any]]:
    """在解包结果里找 manifest.json：根目录，或者唯一的那层子目录（GitHub zip 的 repo-main/）。"""
    candidates: list[pathlib.Path] = []
    if (root / "manifest.json").is_file():
        candidates.append(root)
    for child in root.iterdir():
        if child.is_dir() and (child / "manifest.json").is_file():
            candidates.append(child)
    if not candidates:
        raise InstallError("包里找不到 manifest.json（模块根目录或唯一子目录里必须有）")
    if len(candidates) > 1:
        raise InstallError("包里有多个 manifest.json，分不清哪个是模块")
    folder = candidates[0]
    try:
        data = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallError(f"manifest.json 读不了：{exc}") from exc
    if not isinstance(data, dict):
        raise InstallError("manifest.json 必须是一个 JSON 对象")
    return folder, data


def install_from_bytes(data: bytes, *, source: str = "", force: bool = False,
                       allow_id: str = "") -> dict[str, Any]:
    """从字节装一个模块。返回 {ok,id,path,files,backup}。失败抛 InstallError。"""
    if not data:
        raise InstallError("空文件")
    if len(data) > MAX_ZIP_BYTES:
        raise InstallError("压缩包太大")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise InstallError(f"不是有效的 zip：{exc}") from exc
    if zf.testzip() is not None:
        raise InstallError("zip 内有成员损坏")

    base = modules_dir()
    staging = pathlib.Path(tempfile.mkdtemp(prefix=".staging-", dir=str(base)))
    try:
        _safe_extract(zf, staging)
        folder, manifest = _find_manifest(staging)
        module_id = str(manifest.get("id") or "").strip()
        if not _ID_RE.fullmatch(module_id):
            raise InstallError(f"manifest 里的 id 不合法：{module_id!r}（要 kebab-case）")
        if allow_id and module_id != allow_id:
            raise InstallError(f"这个包声明的 id 是 {module_id}，不是 {allow_id}")
        entry = str(manifest.get("entry") or "").strip()
        if not entry:
            raise InstallError("manifest 缺 entry")
        entry_path = (folder / entry).resolve()
        try:
            entry_path.relative_to(folder.resolve())
        except ValueError:
            raise InstallError("entry 指向模块目录之外（不允许）") from None
        if not entry_path.is_file():
            raise InstallError(f"entry 文件不存在：{entry}")
        for key in ("name", "version", "kind", "requires"):
            if key not in manifest:
                raise InstallError(f"manifest 缺字段：{key}")

        target = base / module_id
        backup = ""
        if target.exists():
            if not force:
                raise InstallError(f"模块 {module_id} 已经装了；要覆盖请勾「覆盖」")
            backup_dir = base / ".backup"
            backup_dir.mkdir(exist_ok=True)
            backup_path = backup_dir / f"{module_id}-{time.strftime('%Y%m%d-%H%M%S')}"
            shutil.move(str(target), str(backup_path))
            backup = str(backup_path)
        # 同盘 move 是原子的；staging 在 modules/ 下，保证不跨卷
        shutil.move(str(folder), str(target))
        files = [str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()]
        return {
            "ok": True,
            "id": module_id,
            "name": str(manifest.get("name") or module_id),
            "version": str(manifest.get("version") or ""),
            "kind": str(manifest.get("kind") or ""),
            "path": str(target),
            "files": len(files),
            "backup": backup,
            "source": source,
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def install_from_url(raw: str, *, force: bool = False) -> dict[str, Any]:
    """下载 + 安装。GitHub `main` 404 时自动试 `master`。"""
    url = normalize_url(raw)
    try:
        data = _download(url)
    except InstallError as exc:
        # GitHub 默认分支可能是 master：只在 404 时兜一次
        if "404" in str(exc) and "/refs/heads/main" in url:
            alt = url.replace("/refs/heads/main", "/refs/heads/master")
            data = _download(alt)
            url = alt
        else:
            raise
    return install_from_bytes(data, source=url, force=force)


__all__ = [
    "InstallError", "MAX_ZIP_BYTES", "install_from_bytes", "install_from_url",
    "modules_dir", "normalize_url",
]
