"""用户口述的"陌生人/平台"真实经历 → 结构化 social_scene（不装原文）。

原文在 ``data/human_life/_raw/user-report-stranger-stories.txt``（gitignored）；这里只把
"谁先开口、几个人、聊什么、结果"抽成 schema 记录，note 写一句转述来源，便于以后在 prompt /
训练里按场景取样。``synthetic=False``：这是真实见闻，不是我们编的一天。

用法（仓库根目录）：
    python scripts/corpus_stories.py            # dry-run：只打印
    python scripts/corpus_stories.py --write    # 写 _derived/user.stories.social_scene.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "data" / "human_life" / "_derived"
OUT_PATH = OUT_DIR / "user.stories.social_scene.jsonl"


def build_records() -> list[dict]:
    """两条真实见闻：平台投诉链 / 网约车安抚。只抽结构，不留原文。"""
    return [
        {
            "id": "user_story_platform_conflict",
            "kind": "social_scene",
            "day_type": "workday",
            "relation": "service",
            "setting": "online",
            "participants": 3,
            "turns": 6,
            "initiator": "other",
            "interrupt_policy": "allow",
            "topic": "投诉与申诉",
            "outcome": "未知",
            "note": (
                "用户目睹的真实事件：骑手与商家互骂→双方投诉扣款→找平台客服申诉被拒→"
                "转而投诉客服；结构化转述，原文见 _raw/user-report-stranger-stories.txt"
            ),
            "synthetic": False,
        },
        {
            "id": "user_story_taxi_soothe",
            "kind": "social_scene",
            "day_type": "restday",
            "relation": "stranger",
            "setting": "transit",
            "participants": 2,
            "turns": 6,
            "initiator": "self",
            "interrupt_policy": "allow",
            "topic": "安抚司机",
            "outcome": "安全到达",
            "note": (
                "用户亲身经历：网约车司机因平台抽成暴躁锤方向盘；乘客先卖惨再聊买车安抚，"
                "司机情绪平复并送到；结构化转述，原文见 _raw/user-report-stranger-stories.txt"
            ),
            "synthetic": False,
        },
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corpus_stories",
        description="用户口述的真实见闻 → social_scene（只抽结构，不装原文）",
    )
    parser.add_argument("--write", action="store_true", help="写入 _derived（默认只打印）")
    args = parser.parse_args(argv)

    records = build_records()
    if not args.write:
        print(f"dry-run：{len(records)} 条 social_scene（不写盘）")
        for record in records:
            print(f"  {record['id']}  {record['relation']}/{record['setting']}  {record['topic']}")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"已写入：{OUT_PATH}（{len(records)} 条）")
    print(f"时间：{datetime.now().astimezone().isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
