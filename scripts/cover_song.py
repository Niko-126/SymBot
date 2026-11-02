# -*- coding: utf-8 -*-
"""点歌翻唱 CLI：搜歌 → 下载 → 人声分离 → 换声 → 混音 → mp3 + QQ 语音条。

跑法::

    .\\.venv\\Scripts\\python.exe scripts\\cover_song.py "雨爱"
    .\\.venv\\Scripts\\python.exe scripts\\cover_song.py BV118411B7XU
    .\\.venv\\Scripts\\python.exe scripts\\cover_song.py "https://www.bilibili.com/video/BV118411B7XU"
    .\\.venv\\Scripts\\python.exe scripts\\cover_song.py "雨爱" --search   # 只搜不唱，看看挑中哪条
    .\\.venv\\Scripts\\python.exe scripts\\cover_song.py "雨爱" --json     # 末尾多打一行 JSON（给上层解析）

退出码 0 = 拿到 mp3；1 = 失败（原因在最后一行）。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.voice import cover  # noqa: E402


def _size(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="点歌翻唱：B 站搜歌 → 分离 → 换声 → 混音")
    ap.add_argument("target", help="歌名 / BV 号 / B 站链接")
    ap.add_argument("--workdir", default="", help="中间产物目录（默认 data/covers/<时间戳-歌名>）")
    ap.add_argument("--voice", default="", help="换声音色（默认取配置 voice.voice，即 feiyu）")
    ap.add_argument("--gain", type=float, default=2.0, help="人声音量微调 dB（默认 +2）")
    ap.add_argument("--no-auto-level", action="store_true",
                    help="关掉自动电平对齐（RVC 掉电平时不补回去）")
    ap.add_argument("--search", action="store_true", help="只搜歌，不下载不翻唱")
    ap.add_argument("--json", action="store_true", help="末尾多打一行 JSON")
    args = ap.parse_args(argv)

    t0 = time.monotonic()

    print(f"[点歌] target = {args.target}")
    if args.search:
        hits = cover.search(args.target, limit=5)
        if not hits:
            print("[搜索] 一条都没搜到（接口风控或关键词太怪）")
            return 1
        best = cover.pick_best(args.target, hits)
        for i, it in enumerate(hits, 1):
            flag = "*" if best and it["bvid"] == best["bvid"] else " "
            print(f" {flag} {i}. {it['bvid']}  {it['duration'] or '?':>7}  {it['title'][:46]}  @{it['author']}")
        print(f"[搜索] 挑中：{best['bvid']}  {best['title']}" if best else "[搜索] 没挑出来")
        return 0

    last = 0.0

    def on_progress(text: str) -> None:
        nonlocal last
        now = time.monotonic()
        gap = f"（上一步 {now - last:.1f}s）" if last else ""
        last = now
        print(f"[{now - t0:6.1f}s] {text}{gap}", flush=True)

    job = cover.run_cover(
        args.target,
        workdir=pathlib.Path(args.workdir) if args.workdir else None,
        progress=on_progress,
        voice=args.voice,
        vocal_gain_db=args.gain,
        auto_level=not args.no_auto_level,
    )

    if not job.get("ok"):
        print(f"[失败] reason={job.get('reason')}  detail={job.get('detail') or ''}")
        if job.get("candidates"):
            print(f"[候选] 搜索返回 {len(job['candidates'])} 条，可改用 BV 号直跑")
        if args.json:
            print(json.dumps(job, ensure_ascii=False))
        return 1

    print()
    print("=" * 68)
    print(f"歌名   {job['title']}")
    print(f"来源   {job['bvid'] or '-'}  {job['url']}")
    print(f"整首   {job['mp3']}")
    print(f"       {_size(job['mp3_bytes'])} · {job['seconds']}s")
    print(f"语音条 {job['clip_amr'] or '（切失败，只有整首）'}")
    if job.get("clip_amr"):
        print(f"       {_size(job['clip_bytes'])} · {job['clip_seconds']}s · 从 {job['clip_start_s']}s 开始")
    print(f"中间件 {job['workdir']}")
    print(f"人声   +{job.get('vocal_gain_db', 0)} dB（其中自动补电平 {job.get('auto_gain_db', 0)} dB）")
    print(f"耗时   {job['cost_s']}s   " + "  ".join(f"{k}={v}" for k, v in (job.get("steps") or {}).items()))
    print("=" * 68)
    if args.json:
        print(json.dumps(job, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
