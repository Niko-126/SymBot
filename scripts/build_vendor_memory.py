"""闭源记忆内核打包 / 校验 CLI（docs/07 §7 的实现）。

一条命令三种模式，跑不动就自动降级：

    .venv\\Scripts\\python.exe scripts\\build_vendor_memory.py                # auto：nuitka → pyarmor → seal
    .venv\\Scripts\\python.exe scripts\\build_vendor_memory.py --mode seal    # 兜底，必须能跑通
    .venv\\Scripts\\python.exe scripts\\build_vendor_memory.py --mode nuitka  # 环境不支持就退出码 3，不假装成功
    .venv\\Scripts\\python.exe scripts\\build_vendor_memory.py --mode pyarmor
    .venv\\Scripts\\python.exe scripts\\build_vendor_memory.py --verify      # 从产物侧导入自检
    .venv\\Scripts\\python.exe scripts\\build_vendor_memory.py --fingerprint # 只打印本机指纹摘要

三种模式的分工（细节见 docs/07 §7.3）：

- **nuitka**：把 `vendor/symvolve_memory/` 编成 C 扩展（`--module`）。源码不落盘，
  但产物是 `.pyd`，**跟 Python 小版本 + 架构死绑**，换解释器就得重编。
- **pyarmor**：字节码加密 + 运行时壳。跨平台，但要装 PyArmor，且免费版有文件数/体积限制。
- **seal**（兜底，纯标准库）：把整个包压成 ZIP，再用
  `PBKDF2-HMAC-SHA256(机器指纹, 盐)` 派生密钥做 PRF-CTR 流加密 + HMAC 校验，
  落成单文件 `app/memory/_vault.bin`；运行时 `app/memory/_loader.py` 解到临时目录、
  注入 `sys.path`、进程退出即删。**没有 `cryptography` 也能跑**，不引新依赖。

产物全部落在 `build/`（已 gitignore）与 `app/memory/_vault.bin`（`*.bin` 已 gitignore），
源码本体继续留在私有仓库。

退出码：0 成功 / 2 参数或文件问题 / 3 环境不支持该模式 / 4 执行失败。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

PKG_NAME = "symvolve_memory"
DEFAULT_PKG = ROOT / "vendor" / PKG_NAME
VAULT = ROOT / "app" / "memory" / "_vault.bin"
LOADER = ROOT / "app" / "memory" / "_loader.py"
BUILD_ROOT = ROOT / "build" / "vendor-memory"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNSUPPORTED = 3
EXIT_FAILED = 4

#: 源码文件后缀：只收需要保密的代码与 SQL，README/测试数据一律不进容器
SRC_SUFFIXES = (".py", ".sql")
#: 支持的 CPython 上限（Nuitka / PyArmor 上游口径）
TOOL_MAX_PY = (3, 13)


# ---------------------------------------------------------------- 工具 --

def _log(msg: str) -> None:
    print(msg, flush=True)


def _die(msg: str, code: int) -> int:
    print(f"[失败] {msg}", flush=True)
    return code


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _human(n: int) -> str:
    return f"{n / 1024 / 1024:.2f} MB" if n >= 1024 * 1024 else f"{n / 1024:.1f} KB"


def venv_python() -> str:
    """用当前解释器是唯一正确选择：产物必须跟运行时同一个 Python 小版本。"""
    return sys.executable


def _load_loader_module():
    """按文件路径加载 `app/memory/_loader.py`（不 import app 包，避免副作用）。

    格式只有一份实现：打包端和解包端共用 `seal_payload()` / `open_payload()`。
    """
    if not LOADER.is_file():
        raise SystemExit(_die(f"找不到解封器：{LOADER}", EXIT_USAGE))
    spec = importlib.util.spec_from_file_location("_symvault_loader", LOADER)
    if spec is None or spec.loader is None:
        raise SystemExit(_die(f"无法加载解封器：{LOADER}", EXIT_USAGE))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect_sources(pkg: pathlib.Path) -> list[tuple[str, bytes]]:
    """收集要封进容器的文件，返回 (容器内相对路径, 内容)，按路径排序保证确定性。"""
    if not pkg.is_dir():
        raise SystemExit(_die(f"找不到源码目录：{pkg}", EXIT_USAGE))
    files: list[tuple[str, bytes]] = []
    for path in sorted(pkg.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        if path.suffix.lower() not in SRC_SUFFIXES:
            continue
        rel = path.relative_to(pkg).as_posix()
        files.append((f"{PKG_NAME}/{rel}", path.read_bytes()))
    if not files:
        raise SystemExit(_die(f"{pkg} 下没有可打包的 {SRC_SUFFIXES} 文件", EXIT_USAGE))
    return files


def build_zip(files: list[tuple[str, bytes]], manifest: dict) -> bytes:
    """确定性 ZIP：固定时间戳、固定顺序、固定权限位（同样的输入出同样的字节）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for arcname, data in files:
            info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
        # 清单也封在密文里：文件名单属于"谁在内核里"，不能以明文躺在容器头里
        info = zipfile.ZipInfo("_vault_manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return buf.getvalue()


def _passphrase() -> str:
    return os.environ.get("SYM_MEMORY_VAULT_PASSPHRASE", "").strip()


# ---------------------------------------------------------------- seal --

def run_seal(pkg: pathlib.Path, iters: int) -> int:
    """兜底模式：源码 → 加密 ZIP → 单文件容器。这条必须能跑通。"""
    loader = _load_loader_module()
    started = time.perf_counter()
    files = collect_sources(pkg)
    passphrase = _passphrase()
    mode = loader.MODE_PASSPHRASE if passphrase else loader.MODE_MACHINE

    manifest = {
        "format": "symvolve-memory-vault",
        "version": loader.FORMAT_VERSION,
        "package": PKG_NAME,
        "files": [
            {"path": arc, "bytes": len(data), "sha256": _sha256(data)} for arc, data in files
        ],
        "file_count": len(files),
        "source_bytes": sum(len(data) for _, data in files),
        "tool": "scripts/build_vendor_memory.py",
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    payload = build_zip(files, manifest)
    secret = passphrase.encode("utf-8") if passphrase else loader.machine_secret()
    blob = loader.seal_payload(payload, secret=secret, mode=mode, iters=iters)

    VAULT.parent.mkdir(parents=True, exist_ok=True)
    VAULT.write_bytes(blob)
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path = BUILD_ROOT / "seal-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "vault": str(VAULT),
                "vault_bytes": len(blob),
                "vault_sha256": _sha256(blob),
                "payload_bytes": len(payload),
                "payload_sha256": _sha256(payload),
                "payload_zip_note": "payload 仅在内存里存在，不落盘",
                "mode": "passphrase" if passphrase else "machine",
                "fingerprint_id": "" if passphrase else loader.fingerprint_id(),
                "iters": iters,
                "manifest": manifest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

    _log("[OK] seal 完成")
    _log(f"  产物      {VAULT}")
    _log(f"  大小      {_human(len(blob))}（{len(blob)} 字节；明文 ZIP {_human(len(payload))} → 压缩 + 加密）")
    _log(f"  SHA256    {_sha256(blob)}")
    _log(f"  文件数    {len(files)} 个（{SRC_SUFFIXES}）")
    _log(f"  密钥      {'口令模式（' + 'SYM_MEMORY_VAULT_PASSPHRASE' + '）' if passphrase else '机器指纹模式，摘要 ' + loader.fingerprint_id()}")
    _log(f"  KDF/iters PBKDF2-HMAC-SHA256 / {iters}")
    _log(f"  清单      {manifest_path}")
    _log(f"  耗时      {elapsed_ms} ms")
    return EXIT_OK


# ---------------------------------------------------------------- 编译模式 --

def _tool_status(module_name: str) -> tuple[bool, str]:
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return False, f"未安装（python -m pip list | findstr /i {module_name} 为空）"
    return True, "已安装"


def _py_too_new_for(tool: str) -> str:
    """返回不支持原因；支持则返回空串。"""
    if sys.version_info[:2] > TOOL_MAX_PY:
        return (
            f"{tool} 上游支持到 CPython {TOOL_MAX_PY[0]}.{TOOL_MAX_PY[1]}，"
            f"本机是 {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        )
    return ""


def run_nuitka(pkg: pathlib.Path) -> int:
    ok, note = _tool_status("nuitka")
    if not ok:
        return _die(f"--mode nuitka 不可用：Nuitka {note}", EXIT_UNSUPPORTED)
    too_new = _py_too_new_for("Nuitka")
    if too_new:
        return _die(f"--mode nuitka 不可用：{too_new}；请降级到 --mode seal", EXIT_UNSUPPORTED)
    out = BUILD_ROOT / "nuitka"
    once = pkg / "__init__.py"
    if not once.is_file():
        return _die(f"找不到包入口：{once}", EXIT_USAGE)
    cmd = [
        venv_python(), "-m", "nuitka",
        "--module", str(once),
        f"--output-dir={out}",
        "--remove-output",
        "--no-pyi-file",
        "--quiet",
    ]
    _log(f"[nuitka] {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(ROOT), check=False)
    if proc.returncode != 0:
        return _die(f"nuitka 编译失败（退出码 {proc.returncode}）", EXIT_FAILED)
    _log(f"[OK] nuitka 产物目录：{out}（注意：.pyd 与 Python {sys.version_info.major}.{sys.version_info.minor} 死绑）")
    return EXIT_OK


def run_pyarmor(pkg: pathlib.Path) -> int:
    ok, note = _tool_status("pyarmor")
    if not ok:
        return _die(f"--mode pyarmor 不可用：PyArmor {note}", EXIT_UNSUPPORTED)
    too_new = _py_too_new_for("PyArmor")
    if too_new:
        return _die(f"--mode pyarmor 不可用：{too_new}；请降级到 --mode seal", EXIT_UNSUPPORTED)
    out = BUILD_ROOT / "pyarmor"
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    cmd = [venv_python(), "-m", "pyarmor", "gen", "--recursive", str(pkg), "-O", str(out)]
    _log(f"[pyarmor] {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(ROOT), check=False)
    if proc.returncode != 0:
        return _die(f"pyarmor 加密失败（退出码 {proc.returncode}）", EXIT_FAILED)
    _log(f"[OK] pyarmor 产物目录：{out}")
    return EXIT_OK


def run_auto(pkg: pathlib.Path, iters: int) -> int:
    """自动降级：nuitka → pyarmor → seal（每一步为什么降级都打出来）。"""
    for name, fn in (("nuitka", run_nuitka), ("pyarmor", run_pyarmor)):
        _log(f"\n=== 尝试 --mode {name} ===")
        code = fn(pkg)
        if code == EXIT_OK:
            _log(f"[auto] 最终模式：{name}")
            return EXIT_OK
        if code == EXIT_UNSUPPORTED:
            _log(f"[auto] {name} 不可用，降级")
            continue
        _log(f"[auto] {name} 执行失败（{code}），降级到 seal")
    _log("\n=== 降级到 --mode seal ===")
    code = run_seal(pkg, iters)
    if code == EXIT_OK:
        _log("[auto] 最终模式：seal")
    return code


# ---------------------------------------------------------------- 校验 --

_VERIFY_CODE = r"""
import json, sys, time
t0 = time.perf_counter()
import app.memory.api as api
core = api.ensure_core()
t1 = time.perf_counter()
info = api.core_info()
print(json.dumps({
    "ok": True,
    "origin": info.get("origin"),
    "file": info.get("file"),
    "temp_dir": info.get("temp_dir"),
    "version": info.get("version"),
    "forced_vault": info.get("forced_vault"),
    "import_ms": round((t1 - t0) * 1000, 1),
}, ensure_ascii=False))
"""


def run_verify() -> int:
    """从产物侧验一遍：容器本身 + 子进程导入（强制走容器，不许走源码）。"""
    loader = _load_loader_module()
    if not VAULT.is_file():
        return _die(f"找不到产物：{VAULT}（先跑 --mode seal）", EXIT_USAGE)

    started = time.perf_counter()
    _log("=== 1/2 容器自检 ===")
    try:
        info = loader.inspect_vault(VAULT)
    except Exception as exc:  # noqa: BLE001 - 指纹不对 / 文件被改 / 格式不认识
        return _die(f"容器自检失败：{type(exc).__name__}: {exc}", EXIT_FAILED)
    for key in ("path", "bytes", "sha256", "payload_bytes", "payload_sha256",
                "files", "mode", "version", "iters", "header_bytes", "ct_len"):
        _log(f"  {key:<16}{info[key]}")
    _log(f"  {'clear_bytes':<14}头 {info['header_bytes']} + 密文 {info['ct_len']} + tag 32 = {info['bytes']}")

    _log("\n=== 2/2 子进程导入自检（强制容器路径）===")
    env = dict(os.environ)
    env[loader.FORCE_ENV] = "1"
    env.pop("PYTHONPATH", None)
    t0 = time.perf_counter()
    proc = subprocess.run(
        [venv_python(), "-c", _VERIFY_CODE],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    if proc.returncode != 0:
        _log(proc.stdout.strip())
        _log(proc.stderr.strip()[-1800:])
        return _die(f"子进程导入失败（退出码 {proc.returncode}）", EXIT_FAILED)
    line = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else "{}"
    try:
        report = json.loads(line)
    except json.JSONDecodeError:
        return _die(f"子进程输出不是 JSON：{line[:200]}", EXIT_FAILED)
    if report.get("origin") != "vault":
        return _die(f"自检走错路径：origin={report.get('origin')}（应为 vault）", EXIT_FAILED)

    _log(f"  origin        {report['origin']}")
    _log(f"  version       {report['version']}")
    _log(f"  模块文件      {report['file']}")
    _log(f"  临时目录      {report['temp_dir']}")
    _log(f"  import 耗时   {report['import_ms']} ms（子进程整体 {elapsed_ms} ms）")
    _log(f"  文件数        {info['files']} 个（含 _vault_manifest.json）")
    ok_ms = round((time.perf_counter() - started) * 1000, 1)
    _log(f"\n[OK] 产物校验通过，总耗时 {ok_ms} ms")
    return EXIT_OK


# ---------------------------------------------------------------- 入口 --

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="闭源记忆内核打包 / 校验（Symvolve Memory vendor 包）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--mode", choices=("auto", "nuitka", "pyarmor", "seal"),
                    default="auto", help="打包模式（默认 auto：nuitka → pyarmor → seal）")
    ap.add_argument("--verify", action="store_true", help="从产物侧做一次导入自检")
    ap.add_argument("--fingerprint", action="store_true", help="只打印本机指纹摘要")
    ap.add_argument("--pkg", default=str(DEFAULT_PKG), help="源码目录（默认 vendor/symvolve_memory）")
    ap.add_argument("--iters", type=int, default=120_000, help="PBKDF2 迭代数（默认 120000）")
    args = ap.parse_args(argv)

    if args.fingerprint:
        loader = _load_loader_module()
        _log(f"本机指纹摘要  {loader.fingerprint_id()}")
        _log("（只是 sha256 前 16 位，原文不出本机；换机/改主机名/换网卡都会变）")
        return EXIT_OK

    pkg = pathlib.Path(args.pkg)
    if args.verify:
        return run_verify()
    if args.mode == "auto":
        return run_auto(pkg, args.iters)
    if args.mode == "seal":
        return run_seal(pkg, args.iters)
    if args.mode == "nuitka":
        return run_nuitka(pkg)
    if args.mode == "pyarmor":
        return run_pyarmor(pkg)
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
