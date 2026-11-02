"""抓取 aigengtu「蓝色大肥鱼档案馆」全量表情包到本地。

输出（都在 data/ 下，不进 git）：
    data/stickers/index.json      索引：id/角色/台词/情绪/title/url/file/sha256/safe
    data/stickers/files/<id>.webp 图片
    data/stickers/summary.txt     统计

用法：
    .venv\\Scripts\\python.exe scripts\\fetch_stickers.py            # 全量 41 页
    .venv\\Scripts\\python.exe scripts\\fetch_stickers.py --pages 3  # 只抓前 3 页试跑
    .venv\\Scripts\\python.exe scripts\\fetch_stickers.py --no-images # 只建索引
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import pathlib
import re
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "stickers"
FILES = OUT / "files"
IMG = "https://img.aigengtu.com/0_preview/meme/{id}.webp"
ARCHIVE = "https://aigengtu.com/archive?p={p}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Referer": "https://aigengtu.com/archive",
}

# 不安全/不适合群聊的内容：命中即 safe=False（仍下载，留作以后人工复核）
UNSAFE_WORDS = (
    "性关系", "性行为", "做爱", "裸", "色情", "调教", "母狗", "傻逼", "去死",
    "杀", "乳", "臀", "射", "操你", "睡我",
)

# 情绪关键词 → 我们自己的场景标签
EMOTION_RULES = [
    ("不高兴", ("暴怒", "气鼓鼓", "生气", "愤怒", "不耐烦", "嫌弃", "无语", "吐槽", "声讨", "傲慢")),
    ("关心", ("委屈", "落泪", "大哭", "难过", "崩溃", "心疼")),
    ("被撒娇", ("羞涩", "脸红", "害羞", "撒娇", "可爱", "元气", "开心", "笑")),
    ("日常", ("疑惑", "思考", "摸鱼", "发呆", "吃瓜", "围观", "睡觉", "宕机", "恍惚")),
    ("她自己", ("得意", "嚣张", "凡尔赛", "夸赞", "点赞", "认可", "俏皮", "坏笑")),
]


def fetch(url: str, retries: int = 3, timeout: int = 30) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"fetch failed: {url}: {last}")


def strip_lines(html: str) -> list[str]:
    html = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)</(p|div|h[1-6]|li|tr|section|article|blockquote|td|th)>", "\n", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    import html as h

    text = h.unescape(html)
    out = []
    for ln in text.split("\n"):
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            out.append(ln)
    return out


ENTRY = re.compile(r"#(\d{3,4})\s+(.+?)(?:表情包)?\s*$")


def parse_page(html: str, found: dict[str, dict]) -> int:
    new = 0
    for line in strip_lines(html):
        m = ENTRY.search(line)
        if not m:
            continue
        mid = m.group(1)
        rest = re.sub(r"#\d{3,4}", "", m.group(2)).strip()
        if mid in found or len(rest) < 2:
            continue
        # 去掉重复拼接（同一行可能重复两遍标题）
        half = len(rest) // 2
        if half > 6 and rest[:half].strip() == rest[half:].strip():
            rest = rest[:half].strip()
        quote = ""
        qm = re.search(r"[『《](.+?)[』》]", rest)
        if qm:
            quote = qm.group(1).strip()
        char = re.split(r"[『《\s]", rest, maxsplit=1)[0].strip()
        if len(char) > 10 or "：" in char or "表情包" in char or "、" in char:
            char = "未知"
        emo = ""
        ems = re.findall(r"([^\s『』《》]{2,10})表情包", line)
        if ems:
            emo = ems[-1]
        scene = "日常"
        for tag, words in EMOTION_RULES:
            if emo and any(w in emo for w in words):
                scene = tag
                break
        bad = any(w in rest for w in UNSAFE_WORDS)
        found[mid] = {
            "id": mid,
            "char": char,
            "quote": quote,
            "emotion": emo,
            "scene": scene,
            "title": rest,
            "url": IMG.format(id=mid),
            "file": f"files/{mid}.webp",
            "safe": not bad,
            "sha256": "",
            "bytes": 0,
        }
        new += 1
    return new


def download(entry: dict, retries: int = 3) -> dict:
    path = OUT / entry["file"]
    if path.exists() and path.stat().st_size > 0:
        entry["bytes"] = path.stat().st_size
        entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return entry
    for attempt in range(retries):
        try:
            data = fetch(entry["url"], retries=2, timeout=30)
            if not data:
                raise RuntimeError("empty")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            entry["bytes"] = len(data)
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            return entry
        except Exception:  # noqa: BLE001
            if attempt == retries - 1:
                entry["error"] = "download failed"
                return entry
            time.sleep(1.0 * (attempt + 1))
    return entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=41, help="抓取前 N 页（默认 41 全量）")
    ap.add_argument("--no-images", action="store_true", help="只建索引，不下载图片")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    FILES.mkdir(parents=True, exist_ok=True)

    found: dict[str, dict] = {}
    for p in range(1, args.pages + 1):
        try:
            html = fetch(ARCHIVE.format(p=p)).decode("utf-8", errors="replace")
        except RuntimeError as exc:
            print(f"[page {p}] {exc}")
            continue
        added = parse_page(html, found)
        print(f"[page {p}/{args.pages}] +{added} 累计 {len(found)}", flush=True)

    entries = sorted(found.values(), key=lambda e: int(e["id"]))
    print(f"索引完成：{len(entries)} 条", flush=True)

    if not args.no_images:
        done = 0
        errors = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = [pool.submit(download, e) for e in entries]
            for fut in concurrent.futures.as_completed(futs):
                e = fut.result()
                done += 1
                if e.get("error"):
                    errors += 1
                if done % 100 == 0 or done == len(entries):
                    mb = sum(x["bytes"] for x in entries) / 1024 / 1024
                    print(f"[download] {done}/{len(entries)} · {mb:.1f} MB · 失败 {errors}", flush=True)

    (OUT / "index.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    safe = [e for e in entries if e["safe"]]
    scenes: dict[str, int] = {}
    chars: dict[str, int] = {}
    for e in entries:
        scenes[e["scene"]] = scenes.get(e["scene"], 0) + 1
        chars[e["char"]] = chars.get(e["char"], 0) + 1
    total_mb = sum(e["bytes"] for e in entries) / 1024 / 1024
    lines = [
        f"表情包索引 · {len(entries)} 条 · {total_mb:.1f} MB",
        f"安全子集：{len(safe)} 条（safe=true）",
        f"按场景：{json.dumps(scenes, ensure_ascii=False)}",
        f"按角色：{json.dumps(chars, ensure_ascii=False)}",
        "来源：https://aigengtu.com/archive（非营利整理站；图片版权归原作者）",
    ]
    (OUT / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
