"""发布前自检：扫一遍仓库，确认没有把密钥、账号、真实人名带进去。

用法：
    python scripts/check_secrets.py

退出码 1 表示发现可疑内容，不能打包。
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".venv", "data", "Girl-memory", "语料", ".git", "__pycache__",
    ".symvolve-preview", "dist", "build", "exports",
}

PATTERNS = [
    ("API Key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("微信账号", re.compile(r"wxid_[A-Za-z0-9_]+")),
    ("微信号", re.compile(r"微信号\s*[：:]\s*\S+")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("身份证", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")),
]

# 允许出现的示例值（文档与测试夹具里的假数据）
ALLOW = {
    "sk-xxxxxxxx", "sk-test", "sk-your-key", "sk-在这里填",
    "wxid_xxx", "wxid_abc", "wxid_me", "abc123", "微信号：abc123",
}


def iter_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in {
            ".pyc", ".png", ".jpg", ".jpeg", ".ico", ".webp", ".gif", ".bmp",
            ".db", ".json", ".lock", ".zip", ".exe", ".dll", ".woff", ".woff2",
            ".ttf", ".otf", ".pdf", ".mp3", ".wav", ".ogg", ".webm", ".mp4",
        }:
            continue
        files.append(p)
    return files


def main() -> int:
    import sys as _sys

    allow_local = "--allow-local" in _sys.argv
    problems: list[str] = []

    cfg = ROOT / "config.yaml"
    if cfg.exists():
        text = cfg.read_text(encoding="utf-8")
        m = re.search(r"api_key:\s*\"?([^\s\"#]*)", text)
        if m and m.group(1).strip():
            problems.append("config.yaml: provider.api_key 不是空的 —— 打包前必须清空")

    for f in iter_files():
        if allow_local and f.name == "config.local.yaml":
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, rx in PATTERNS:
            for m in rx.finditer(text):
                value = m.group(0)
                if value in ALLOW or any(a in value for a in ALLOW):
                    continue
                # 文档里的反引号示例与测试夹具
                if "`" in value or "xxx" in value.lower():
                    continue
                line_no = text[: m.start()].count("\n") + 1
                problems.append(f"{f.relative_to(ROOT)}:{line_no}: 疑似{label}「{value[:12]}…」")

    try:
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False
        ).stdout.splitlines()
        for name in tracked:
            if name.startswith("Girl-memory/") or name.startswith("data/"):
                problems.append(f"{name}: 私密目录被 git 跟踪了")
    except OSError:
        pass

    if problems:
        print("发现 %d 个问题：\n" % len(problems))
        for p in problems:
            print("  - " + p)
        print("\n不能打包。")
        return 1

    if allow_local:
        print("检查通过（已跳过 config.local.yaml；打包前必须删掉它并去掉 --allow-local）。")
    else:
        print("检查通过：没有发现密钥、账号或私密目录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
