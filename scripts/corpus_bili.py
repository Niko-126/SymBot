"""采集 B 站公开 vlog 的标题/简介/热评，落成"人类日常"原始素材。

用途（docs/52 的 B 包 + docs/56）：补"一天的时间轴"——几点起、几点出门、通勤多久、
下班后做什么。只走 B 站**公开**接口（wbi 签名，见 app/web/bili.py），不登录、不带 cookie。

铁律：
- 只存标题 / 简介 / 评论正文（评论先过 PII 遮掩），不存昵称、UID、头像、作者名；
- 不下载视频、不抓整站、频率克制（默认每个视频间隔 1.5 秒）；
- 产出只写 `data/human_life/_raw/`（data/ 已 gitignore，不进仓库、不进包）；
- 任何异常都不中断整轮：某个视频失败就跳过，最后汇报成功/失败数。

用法（仓库根目录）：
    python scripts/corpus_bili.py --keywords "打工人一天,独居女生一天,通勤记录" --per 3
    python scripts/corpus_bili.py --dry-run          # 只搜、不取详情、不落盘
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.lifecorpus.pii import mask_text  # noqa: E402
from app.web import bili  # noqa: E402

DEFAULT_KEYWORDS = (
    "打工人一天",
    "独居女生一天",
    "上班vlog",
    "通勤记录",
    "周末vlog",
    "合租日常",
    "24小时记录",
    "下班后的生活",
)

DEFAULT_OUT = ROOT / "data" / "human_life" / "_raw"

# 看起来像"一天里的时间点/日常动作"的行，才留作片段。
_TIME_HINT = re.compile(
    r"(\d{1,2}\s*[:：点]|早上|清晨|上午|中午|午后|下午|傍晚|晚上|夜里|凌晨|"
    r"起床|出门|通勤|地铁|公交|打卡|上班|下班|午饭|午饭|外卖|加班|到家|洗澡|"
    r"做饭|买菜|遛狗|健身|跑步|午休|周末)"
)
# 纯营销/带货/求关注的行直接丢
_AD_HINT = re.compile(r"(点赞|投币|关注|三连|公众号|微信|私信|加群|链接|优惠|下单|带货)")


def _clean(text: str) -> str:
    return " ".join(str(text or "").replace("\u3000", " ").split())


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def collect(keywords: list[str], *, per_keyword: int = 2, want_comments: int = 10,
            delay: float = 1.5, dry_run: bool = False) -> tuple[list[dict], dict]:
    """搜一轮并取详情/热评。返回 (记录, 统计)。"""
    records: list[dict] = []
    stats = {"keywords": 0, "videos": 0, "comments": 0, "failed": 0, "seen": 0}
    seen: set[str] = set()
    for keyword in keywords:
        kw = _clean(keyword)
        if not kw:
            continue
        stats["keywords"] += 1
        try:
            hits = bili.search_videos(kw, max(1, int(per_keyword)))
        except Exception as exc:  # noqa: BLE001 - 搜不到就跳过这个关键词
            print(f"[跳过] 搜索失败：{kw}（{exc}）")
            stats["failed"] += 1
            continue
        for hit in hits:
            bvid = str(hit.get("bvid") or "")
            if not bvid or bvid in seen:
                continue
            seen.add(bvid)
            stats["seen"] += 1
            rec: dict = {
                "keyword": kw,
                "bvid": bvid,
                "url": hit.get("url") or f"https://www.bilibili.com/video/{bvid}",
                "title": _clean(hit.get("title")),
                "duration": str(hit.get("duration") or ""),
                "play": int(hit.get("play") or 0),
                "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "source": "bilibili_public",
            }
            if dry_run:
                records.append(rec)
                stats["videos"] += 1
                continue
            info = bili.video_info(bvid) or {}
            desc, _ = mask_text(_clean(info.get("desc") or ""))
            rec["desc"] = desc
            rec["aid"] = info.get("aid")
            stats["videos"] += 1
            aid = info.get("aid")
            if aid and want_comments > 0:
                try:
                    raw_comments = bili.comments(int(aid), max(1, int(want_comments)))
                except Exception:  # noqa: BLE001 - 风控/412 当没有
                    raw_comments = []
                kept = []
                for item in raw_comments[: int(want_comments)]:
                    body, _ = mask_text(_clean(item.get("text") or ""))
                    if len(body) >= 4:
                        kept.append({"text": body[:300], "likes": int(item.get("likes") or 0)})
                rec["comments"] = kept
                stats["comments"] += len(kept)
            rec["sha256"] = _sha(f"{rec['title']}\n{rec.get('desc', '')}")
            records.append(rec)
            time.sleep(max(0.2, float(delay)))
    return records, stats


def extract_snippets(records: list[dict]) -> list[dict]:
    """从标题/简介/评论里挑"带时间点或日常动作"的短句，供后面人工归类。"""
    out: list[dict] = []
    seen: set[str] = set()
    for rec in records:
        texts = [rec.get("title", ""), rec.get("desc", "")]
        texts += [c.get("text", "") for c in (rec.get("comments") or [])]
        for body in texts:
            for line in re.split(r"[\n。！？!?；;]", str(body or "")):
                line = _clean(line)
                if len(line) < 6 or len(line) > 120:
                    continue
                if not _TIME_HINT.search(line):
                    continue
                if _AD_HINT.search(line):
                    continue
                key = line[:40]
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "kind": "snippet_candidate",
                    "text": line[:120],
                    "source": rec.get("url", ""),
                    "bvid": rec.get("bvid", ""),
                    "keyword": rec.get("keyword", ""),
                    "fetched_at": rec.get("fetched_at", ""),
                })
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def collect_bvids(bvids: list[str], *, want_comments: int = 10,
                  delay: float = 1.0) -> tuple[list[dict], dict]:
    """按指定 bvid 采（用户从候选里挑的那几条），不经过关键词搜索。"""
    records: list[dict] = []
    stats = {"keywords": 0, "videos": 0, "comments": 0, "failed": 0, "seen": 0}
    for raw in bvids:
        bvid = str(raw or "").strip()
        if not bvid:
            continue
        stats["seen"] += 1
        info = bili.video_info(bvid) or {}
        if not info:
            stats["failed"] += 1
            print(f"[跳过] 取不到视频：{bvid}")
            continue
        desc, _ = mask_text(_clean(info.get("desc") or ""))
        rec: dict = {
            "keyword": "manual_pick",
            "bvid": bvid,
            "url": info.get("url") or f"https://www.bilibili.com/video/{bvid}",
            "title": _clean(info.get("title") or ""),
            "duration": "",
            "play": int(info.get("play") or 0),
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": "bilibili_public",
            "desc": desc,
            "aid": info.get("aid"),
        }
        stats["videos"] += 1
        aid = info.get("aid")
        if aid and want_comments > 0:
            try:
                raw_comments = bili.comments(int(aid), max(1, int(want_comments)))
            except Exception:  # noqa: BLE001
                raw_comments = []
            kept = []
            for item in raw_comments[: int(want_comments)]:
                body, _ = mask_text(_clean(item.get("text") or ""))
                if len(body) >= 4:
                    kept.append({"text": body[:300], "likes": int(item.get("likes") or 0)})
            rec["comments"] = kept
            stats["comments"] += len(kept)
        rec["sha256"] = _sha(f"{rec['title']}\n{rec.get('desc', '')}")
        records.append(rec)
        time.sleep(max(0.2, float(delay)))
    return records, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corpus_bili",
        description="采集 B 站公开 vlog 标题/简介/热评（不登录、不带 cookie、只写 data/human_life/_raw）",
    )
    parser.add_argument("--keywords", default=",".join(DEFAULT_KEYWORDS),
                        help="逗号分隔的搜索词")
    parser.add_argument("--bvids", default="",
                        help="逗号分隔的 bvid 列表（人工挑的视频，跳过关键词搜索）")
    parser.add_argument("--per", type=int, default=2, help="每个关键词取几个视频（默认 2）")
    parser.add_argument("--comments", type=int, default=10, help="每个视频取几条热评（默认 10，0=不取）")
    parser.add_argument("--delay", type=float, default=1.5, help="每个视频之间停顿秒数（默认 1.5）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录（默认 data/human_life/_raw）")
    parser.add_argument("--dry-run", action="store_true", help="只搜标题，不取详情/评论、不落盘")
    args = parser.parse_args(argv)

    keywords = [k.strip() for k in str(args.keywords).split(",") if k.strip()]
    bvids = [b.strip() for b in str(args.bvids or "").split(",") if b.strip()]
    if bvids:
        records, stats = collect_bvids(
            bvids, want_comments=args.comments, delay=args.delay,
        )
    else:
        records, stats = collect(
            keywords, per_keyword=args.per, want_comments=args.comments,
            delay=args.delay, dry_run=bool(args.dry_run),
        )
    snippets = [] if args.dry_run else extract_snippets(records)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.dry_run:
        print(f"[dry-run] 关键词 {stats['keywords']} 个，搜到 {len(records)} 个视频（未取详情、未落盘）")
        for rec in records[:12]:
            print(f"  - {rec['title'][:60]}  {rec['url']}")
        return 0

    out_dir = Path(args.out)
    raw_path = out_dir / f"bili-{stamp}.jsonl"
    snip_path = out_dir / f"bili-snippets-{stamp}.jsonl"
    write_jsonl(raw_path, records)
    write_jsonl(snip_path, snippets)
    print(f"关键词 {stats['keywords']} / 视频 {stats['videos']} / 评论 {stats['comments']}"
          f" / 失败 {stats['failed']}")
    print(f"原始记录：{raw_path}（{len(records)} 行）")
    print(f"候选片段：{snip_path}（{len(snippets)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
