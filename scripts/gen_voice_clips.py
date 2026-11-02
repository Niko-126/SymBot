#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""关键词触发的语音素材批量生成器。

作用：把「触发词 → 一句台词」合成成 WAV，落到 `data/voices/<触发词>.wav`。
`app/memory/voices.py` 的规则是**文件名即触发词**，所以文件名必须是触发词本身，
不能是 tts 缓存那串 hash（缓存 15 天会被 cleanup_cache 清掉）。

为什么走内核而不是直连 9880：
- 9880 那套是 `/v1/speech` + 轮询 job 的异步协议，自己实现一遍纯属重复劳动；
- 内核 `/v1/console/tts` 已经封装好了（还带缓存、限流、24k 单声道落盘），免鉴权；
- 但它返回的 path 在 `data/voices/tts/` 或 `data/voices/cache/` 下，**不能直接用**，
  所以这里统一复制成 `data/voices/<触发词>.wav`。

用法：
    .venv\\Scripts\\python.exe scripts\\gen_voice_clips.py --list
    .venv\\Scripts\\python.exe scripts\\gen_voice_clips.py
    .venv\\Scripts\\python.exe scripts\\gen_voice_clips.py --only 晚安,早安 --force
    .venv\\Scripts\\python.exe scripts\\gen_voice_clips.py --gap 12

纪律：
- **串行**合成。服务端每合成一条要重载模型（约 35 秒），并发只会互相拖死；
- 内核默认限流 6 次/分钟，所以这里按 `--gap` 控制请求间隔（默认 10.5 秒 ≥ 60/6）；
- 已存在的同名 wav 默认跳过，`--force` 才覆盖。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------- 台词表（改这里就行）
#
# 键 = 触发词 = 输出文件名（不带后缀）；值 = 要合成的台词。
# 台词写短一点（≤20 字）、口语、像"她"会说的话，别写成客服播报。
# 顺序即合成顺序。
LINES: dict[str, str] = {
    "晚安": "晚安啦，快闭上眼睛～",
    "早安": "早安呀，今天也要元气满满哦",
    "在的": "在哒，找我什么事呀",
    "想你": "我也想你，想得不得了",
    "抱抱": "抱抱，整个人都给你抱",
    "笨蛋": "笨蛋，就你最笨啦",
    "我回来啦": "我回来啦，有没有想我",
    "不许熬夜": "不许熬夜，快去睡觉觉",
}

DEFAULT_BASE = "http://127.0.0.1:8787"
DEFAULT_VOICE = "feiyu"
DEFAULT_STYLE = "cute"
# 6 次/分钟 → 每条之间至少 10 秒；留 0.5 秒余量防边界抖动
DEFAULT_GAP = 10.5


def _out_dir() -> pathlib.Path:
    """`data/voices/` 的绝对路径。优先复用 app.core.paths（认 SYMEM_DATA 环境变量）。"""
    try:
        from app.core.paths import DATA_ROOT  # noqa: PLC0415 - 延迟导入，脚本独立可跑

        return pathlib.Path(DATA_ROOT) / "data" / "voices"
    except Exception:  # noqa: BLE001 - 不在仓库根/没装依赖时退回脚本位置推算
        return pathlib.Path(__file__).resolve().parents[1] / "data" / "voices"


def _post_tts(base: str, text: str, voice: str, style: str,
              timeout: float) -> dict:
    """调内核 `/v1/console/tts`，返回解析后的 JSON（失败也返回 dict，带 ok=False）。"""
    body = json.dumps({"text": text, "voice": voice, "style": style},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base.rstrip('/')}/v1/console/tts",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except OSError:
            detail = ""
        return {"ok": False, "reason": f"http-{exc.code}", "detail": detail,
                "elapsed": time.monotonic() - started}
    except Exception as exc:  # noqa: BLE001 - 内核没起/超时都算这条失败
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:160],
                "elapsed": time.monotonic() - started}
    payload["elapsed"] = time.monotonic() - started
    return payload


def _human(n: float) -> str:
    return f"{n:6.1f}s"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(
        description="按触发词批量合成语音素材到 data/voices/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--only", default="",
                    help="只做指定几条，逗号分隔，如 晚安,早安")
    ap.add_argument("--force", action="store_true", help="已存在同名 wav 也重新合成并覆盖")
    ap.add_argument("--list", action="store_true", help="只打印计划，不合成")
    ap.add_argument("--base-url", default=DEFAULT_BASE, help=f"内核地址，默认 {DEFAULT_BASE}")
    ap.add_argument("--voice", default=DEFAULT_VOICE, help=f"音色，默认 {DEFAULT_VOICE}")
    ap.add_argument("--style", default=DEFAULT_STYLE, help=f"风格，默认 {DEFAULT_STYLE}")
    ap.add_argument("--gap", type=float, default=DEFAULT_GAP,
                    help=f"两条之间的最小间隔秒数，默认 {DEFAULT_GAP}（防撞 6 次/分钟限流）")
    ap.add_argument("--timeout", type=float, default=180.0,
                    help="单条合成的 HTTP 超时秒数，默认 180")
    args = ap.parse_args(argv)

    out = _out_dir()
    plan: list[tuple[str, str, pathlib.Path]] = []
    for trigger, line in LINES.items():
        plan.append((trigger, line, out / f"{trigger}.wav"))

    wanted = [t.strip() for t in args.only.split(",") if t.strip()]
    if wanted:
        unknown = [t for t in wanted if t not in LINES]
        if unknown:
            print(f"[!] 台词表里没有这些触发词：{', '.join(unknown)}", file=sys.stderr)
            print(f"    可选：{', '.join(LINES)}", file=sys.stderr)
            return 2
        plan = [row for row in plan if row[0] in wanted]

    if not plan:
        print("[!] 没有要做的条目", file=sys.stderr)
        return 2

    print(f"输出目录：{out}")
    print(f"内核地址：{args.base_url}  音色：{args.voice}  风格：{args.style}")
    print(f"限流间隔：{args.gap:.1f}s/条   共 {len(plan)} 条")
    print("-" * 74)

    if args.list:
        for i, (trigger, line, target) in enumerate(plan, 1):
            state = "跳过(已存在)" if target.is_file() and not args.force else "待合成"
            print(f"{i:>2}. {trigger:<6} 「{line}」  -> {target.name:<14} [{state}]")
        print("-" * 74)
        print(f"计划 {len(plan)} 条；加 --force 才覆盖已存在的文件。")
        return 0

    out.mkdir(parents=True, exist_ok=True)

    ok_count = 0
    skip_count = 0
    fail: list[tuple[str, str]] = []
    last_request_at = 0.0

    for i, (trigger, line, target) in enumerate(plan, 1):
        head = f"{i:>2}/{len(plan)} {trigger:<6}"
        if target.is_file() and not args.force:
            size = target.stat().st_size
            print(f"{head} 跳过（已存在 {size} B）：{target.name}")
            skip_count += 1
            continue

        if last_request_at:
            wait = args.gap - (time.monotonic() - last_request_at)
            if wait > 0:
                time.sleep(wait)
        last_request_at = time.monotonic()

        res = _post_tts(args.base_url, line, args.voice, args.style, args.timeout)
        elapsed = float(res.get("elapsed") or 0.0)

        if not res.get("ok"):
            reason = res.get("reason") or "unknown"
            detail = res.get("detail") or ""
            print(f"{head} 失败 {_human(elapsed)} {reason} {detail}".rstrip())
            fail.append((trigger, reason))
            continue

        src = pathlib.Path(str(res.get("path") or ""))
        if not src.is_file():
            print(f"{head} 失败 {_human(elapsed)} 返回路径不存在：{src}")
            fail.append((trigger, "missing-src"))
            continue

        try:
            shutil.copyfile(src, target)
        except OSError as exc:
            print(f"{head} 失败 {_human(elapsed)} 复制出错：{exc}")
            fail.append((trigger, "copy-failed"))
            continue

        size = target.stat().st_size
        cached = "命中缓存" if res.get("cached") else ""
        print(f"{head} 完成 {_human(elapsed)} {size:>8} B 「{line}」 {cached}".rstrip())
        ok_count += 1

    print("-" * 74)
    print(f"汇总：成功 {ok_count} 条，跳过 {skip_count} 条，失败 {len(fail)} 条")
    if fail:
        for trigger, reason in fail:
            print(f"  ✗ {trigger}: {reason}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
