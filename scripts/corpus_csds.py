"""用 CSDS 公开客服数据补"真实服务对话"，并清掉覆盖报告里最后一个缺口。

缺口是 `interrupt_policy/ask_later：打断策略「先答应，约好稍后再说」没有样本`。
日常聊天语料里几乎不会出现"您稍等，我帮您查"这种话，只有客服场景天然有——所以用 CSDS 补。

数据：`data/human_life/csds/{train,val,test}.json`（list），每条
`{DialogueID, QRole, QA:[{intent, ...}], Dialogue:[{speaker:"Q"|"A", turn, utterance}], ...}`。
Q=用户（映射成 self）、A=客服（other）。原文是**按词切分带空格**的中文，落盘前要把 CJK 之间的
空格去掉，否则训练出来会一顿一顿。

策略：
- 窗口 4-6 条、Q/A 都有；先挑含 ask_later 关键词的窗口（目标 10 条，缺口只需 >0），
  其余用普通窗口按 intent 铺开补齐 60；
- interrupt_policy 由窗口文本里的关键词决定：出现「稍后/晚点/回头/等会/等一下/稍等/您稍等/晚些」
  就是 ask_later（先答应、约好再说），否则 allow（可以随时打断）；
- 隐私：个人信息一律掩码——手机、座机、邮箱、身份证、链接/二维码整窗丢弃，
  另外把「张先生」这类称呼与门牌地址换成占位符；**不落 DialogueID、不落原 id**；
- 异常不外抛：坏记录跳过并计数。

用法（仓库根目录）：
    python scripts/corpus_csds.py            # dry-run：打印能抽多少、策略分布，不写文件
    python scripts/corpus_csds.py --write    # 写 csds.snippet / csds.social_scene / csds_mix
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 允许直接 `python scripts/corpus_csds.py`
    sys.path.insert(0, str(ROOT))

from app.lifecorpus.pii import mask_text  # noqa: E402

DATA_DIR: Path = ROOT / "data" / "human_life" / "csds"
OUT_DIR: Path = ROOT / "data" / "human_life" / "_derived"
SNIPPET_PATH: Path = OUT_DIR / "csds.snippet.jsonl"
SCENE_PATH: Path = OUT_DIR / "csds.social_scene.jsonl"
MIX_PATH: Path = OUT_DIR / "csds_mix.json"

SPLITS: tuple[str, ...] = ("train", "val", "test")
TOTAL: int = 60                 # snippet 与 scene 各 60 条
ASK_LATER_TARGET: int = 10      # 至少 8 条；多挑 2 条做冗余
WINDOW_MIN: int = 4
WINDOW_MAX: int = 6
MAX_TEXT_CHARS: int = 160
PER_DIALOG_CAP: int = 2         # 同一段对话最多贡献几个窗口

# 命中就把打断策略记成"先答应，约好稍后再说"
ASK_LATER_WORDS: tuple[str, ...] = (
    "稍后", "晚点", "回头", "等会", "等一下", "稍等", "您稍等", "晚些",
)

# 链接/二维码：命中整窗丢弃
_LINK_HINT: re.Pattern[str] = re.compile(r"(https?://|www\.|\.com|\.cn|二维码|扫码|链接|网址)", re.IGNORECASE)

# CJK 之间的空格（分词残留）要连起来；CJK 与中文标点之间的空格也去掉
_CJK_SPACE: re.Pattern[str] = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")
_PUNCT_SPACE: re.Pattern[str] = re.compile(r"\s+(?=[，。？！、：；）】》])|(?<=[（【《])\s+")
_PUNCT_SPACE_LEADING: re.Pattern[str] = re.compile(r"(?<=[，。？！、：；）】》])\s+")

# 补充 PII 清洗（mask_text 已管手机/身份证/邮箱，这里再兜座机、称呼、门牌）
_EXTRA_PII: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "138****8000"),
    (re.compile(r"(?<!\d)0\d{2,3}[-\s]?\d{7,8}(?!\d)"), "010-****"),
    (re.compile(r"(微信|vx|VX|wx|QQ|qq)\s*[:：]?\s*[A-Za-z0-9_\-]{3,}"), "联系方式省略"),
    (re.compile(r"[\u4e00-\u9fff]{1,2}(先生|女士|小姐|老师|经理)"), "某位顾客"),
    (re.compile(r"[\u4e00-\u9fff]{2,8}(?:路|街|大道)[\u4e00-\u9fff0-9]{0,8}"), "[地址]"),
    (re.compile(r"\d+\s*(?:号|栋|单元|室|楼|层)"), "[门牌]"),
)

# intent -> 话题固定标签（顺序即优先级：先判更具体的）
TOPIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("改单", ("修改订单", "改单", "修改", "改地址")),
    ("退款", ("退款", "退货", "价保", "拒收", "取消订单")),
    ("退换", ("退换", "保修", "返修", "换货", "维修")),
    ("发票", ("发票",)),
    ("配送", ("配送", "出库", "物流", "发货", "运输", "快递", "到货", "签收")),
    ("订单", ("订单", "下单", "支付", "价格", "属性", "使用咨询", "取消", "查询", "账户", "活动", "咨询")),
)
TOPIC_FALLBACK: str = "其他"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _ensure_utf8_stdout() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        pass


def join_cjk(text: str) -> str:
    """去掉分词留下的空格：只在汉字之间连起来，英文/数字之间的空格保留。"""
    value = str(text or "").replace("\u3000", " ")
    value = _CJK_SPACE.sub("", value)
    value = _PUNCT_SPACE.sub("", value)
    value = _PUNCT_SPACE_LEADING.sub("", value)
    return " ".join(value.split())


def scrub(text: str) -> str:
    """落盘前最后一道：连词 -> PII 掩码 -> 补充清洗 -> 截断。"""
    value = join_cjk(text)
    if not value:
        return ""
    value, _ = mask_text(value)
    for pattern, replacement in _EXTRA_PII:
        value = pattern.sub(replacement, value)
    return " ".join(value.split())[:MAX_TEXT_CHARS]


def has_ask_later(text: str) -> bool:
    return any(word in text for word in ASK_LATER_WORDS)


def topic_of(intent: str) -> str:
    for label, keywords in TOPIC_RULES:
        if any(keyword in intent for keyword in keywords):
            return label
    return TOPIC_FALLBACK


# ---------------------------------------------------------------------------
# 读数据
# ---------------------------------------------------------------------------


@dataclass
class Dialog:
    """一段客服对话：[(role, text)] + 第一个 intent。"""

    split: str
    intent: str
    messages: list[tuple[str, str]]


def load_dialogs(data_dir: Path = DATA_DIR) -> tuple[list[Dialog], dict[str, int]]:
    """读三个 split；坏记录跳过并计数。返回 (对话列表, 统计)。"""
    stats = {"files": 0, "wrong_files": 0, "records": 0, "broken": 0, "skipped_short": 0}
    dialogs: list[Dialog] = []
    for split in SPLITS:
        path = data_dir / f"{split}.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            stats["wrong_files"] += 1
            continue
        if not isinstance(payload, list):
            stats["wrong_files"] += 1
            continue
        stats["files"] += 1
        for record in payload:
            if not isinstance(record, dict):
                stats["broken"] += 1
                continue
            stats["records"] += 1
            raw_dialogue = record.get("Dialogue")
            if not isinstance(raw_dialogue, list) or len(raw_dialogue) < WINDOW_MIN:
                stats["skipped_short"] += 1
                continue
            messages: list[tuple[str, str]] = []
            for turn in raw_dialogue:
                if not isinstance(turn, dict):
                    continue
                speaker = str(turn.get("speaker") or "").upper()
                text = join_cjk(turn.get("utterance"))
                if not text:
                    continue
                role = "self" if speaker == "Q" else "other"
                messages.append((role, text))
            if len(messages) < WINDOW_MIN:
                stats["skipped_short"] += 1
                continue
            intent = ""
            qa = record.get("QA")
            if isinstance(qa, list):
                for item in qa:
                    if isinstance(item, dict) and item.get("intent"):
                        intent = str(item["intent"])
                        break
            dialogs.append(Dialog(split=split, intent=intent, messages=messages))
    return dialogs, stats


# ---------------------------------------------------------------------------
# 抽窗口
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """一个可用窗口。"""

    intent: str
    policy: str                  # allow / ask_later
    messages: list[tuple[str, str]]


def build_candidates(dialogs: list[Dialog]) -> list[Candidate]:
    """逐段滑窗（步进 1 条，文本去重），保留 Q/A 都有、无链接的窗口。"""
    candidates: list[Candidate] = []
    seen: set[tuple[str, ...]] = set()
    for dialog in dialogs:
        used = 0
        for start in range(len(dialog.messages)):
            if used >= PER_DIALOG_CAP:
                break
            # 窗口长度按起点轮转 6/5/4，避免 60 条清一色 6 句
            order = [WINDOW_MAX, WINDOW_MAX - 1, WINDOW_MIN]
            order = order[start % len(order):] + order[: start % len(order)]
            for length in order:
                if start + length > len(dialog.messages):
                    continue
                window = dialog.messages[start : start + length]
                if len({role for role, _ in window}) < 2:
                    continue
                if any(_LINK_HINT.search(text) for _, text in window):
                    continue
                cleaned = [(role, scrub(text)) for role, text in window]
                if any(not text for _, text in cleaned):
                    continue
                key = tuple(text for _, text in cleaned)
                if key in seen:
                    continue
                seen.add(key)
                joined = "\n".join(text for _, text in cleaned)
                candidates.append(
                    Candidate(
                        intent=dialog.intent,
                        policy="ask_later" if has_ask_later(joined) else "allow",
                        messages=cleaned,
                    )
                )
                used += 1
                break
    return candidates


def select(candidates: list[Candidate], total: int = TOTAL) -> list[Candidate]:
    """先挑 ask_later（缺口就靠它），再按 intent 轮转补 allow，避免 60 条都来自一个业务。"""
    ask_later = [item for item in candidates if item.policy == "ask_later"]
    allow = [item for item in candidates if item.policy == "allow"]

    picked: list[Candidate] = ask_later[:ASK_LATER_TARGET]

    # 按 intent 轮转铺开，先每个 intent 一条，再回头补第二条
    buckets: dict[str, list[Candidate]] = {}
    for item in allow:
        buckets.setdefault(item.intent or "未知", []).append(item)
    cursor: Counter[str] = Counter()
    while len(picked) < total:
        progressed = False
        for intent in buckets:
            if len(picked) >= total:
                break
            items = buckets[intent]
            if cursor[intent] < len(items):
                picked.append(items[cursor[intent]])
                cursor[intent] += 1
                progressed = True
        if not progressed:
            break
    return picked[:total]


# ---------------------------------------------------------------------------
# 记录生成
# ---------------------------------------------------------------------------


def build_records(picked: list[Candidate]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    snippets: list[dict[str, Any]] = []
    scenes: list[dict[str, Any]] = []
    for item in picked:
        note = (
            "来源：CSDS 用户-客服对话（用户确认论文可用/公共版权）；"
            f"intent={item.intent or '未知'}"
        )
        messages = [{"role": role, "text": text} for role, text in item.messages]
        if len({m["role"] for m in messages}) < 2:
            continue
        q_turns = sum(1 for m in messages if m["role"] == "self")
        if q_turns < 1:
            continue
        snippets.append(
            {
                "id": f"csds_snip_{len(snippets) + 1:03d}",
                "kind": "snippet",
                "day_type": "workday",       # 数据集无日期
                "relation": "service",
                "context": "公开数据集对话（CSDS）",
                "interrupt_policy": item.policy,
                "messages": messages,
                "note": note,
            }
        )
        scenes.append(
            {
                "id": f"csds_scene_{len(scenes) + 1:03d}",
                "kind": "social_scene",
                "day_type": "workday",
                "relation": "service",
                "setting": "online",
                "participants": 2,
                "turns": min(200, q_turns),
                "initiator": "self",          # 客服对话都是用户先问
                "interrupt_policy": item.policy,
                "topic": topic_of(item.intent),
                "outcome": "未知",
                "note": note,
            }
        )
    return snippets, scenes


# ---------------------------------------------------------------------------
# 落盘与汇总
# ---------------------------------------------------------------------------


def write_outputs(
    snippets: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    picked: list[Candidate],
    stats: dict[str, int],
    candidate_count: int,
) -> tuple[Path, Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with SNIPPET_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        for row in snippets:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with SCENE_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        for row in scenes:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    mix = {
        "source": "CSDS (EMNLP 2021)",
        "license_note": "用户确认论文可用/公共版权",
        "url": "https://github.com/xiaolinAndy/CSDS",
        "snippet_total": len(snippets),
        "scene_total": len(scenes),
        "counts": {
            "relation": dict(Counter(row["relation"] for row in snippets)),
            "interrupt_policy": dict(Counter(row["interrupt_policy"] for row in snippets)),
            "intent": dict(Counter(item.intent or "未知" for item in picked)),
            "topic": dict(Counter(row["topic"] for row in scenes)),
        },
        "candidates": candidate_count,
        "parse": stats,
        "policy": "含「稍后/晚点/回头/等会/等一下/稍等/您稍等/晚些」的窗口记 ask_later，其余 allow；"
                  "先保 ask_later 配额，再按 intent 轮转补齐",
    }
    MIX_PATH.write_text(json.dumps(mix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return SNIPPET_PATH, SCENE_PATH, MIX_PATH


def render_summary(
    candidates: list[Candidate],
    picked: list[Candidate],
    snippets: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    stats: dict[str, int],
) -> str:
    policy_all = Counter(item.policy for item in candidates)
    policy_picked = Counter(row["interrupt_policy"] for row in snippets)
    topics = Counter(row["topic"] for row in scenes)
    lines = [
        "CSDS -> 客服对话语料（配比预览）",
        f"  文件 {stats['files']} 个（格式不对 {stats['wrong_files']}）；记录 {stats['records']}"
        f"（坏 {stats['broken']}，太短跳过 {stats['skipped_short']}）",
        f"  候选窗口 {len(candidates)} 个：allow {policy_all['allow']} / ask_later {policy_all['ask_later']}",
        f"  已挑 {len(picked)} 个 -> snippet {len(snippets)} / scene {len(scenes)}",
        f"  打断策略（落盘）：ask_later {policy_picked['ask_later']} / allow {policy_picked['allow']}"
        f"（目标 ask_later ≥8）",
        "  话题：" + "，".join(f"{key} {value}" for key, value in sorted(topics.items())),
        "  intent 前 6：" + "，".join(
            f"{key} {value}" for key, value in Counter(item.intent for item in picked).most_common(6)
        ),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corpus_csds",
        description="用 CSDS 公开客服数据补 service 场景并清 ask_later 缺口，产出写 data/human_life/_derived/",
    )
    parser.add_argument("--write", action="store_true", help="真正落盘")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="CSDS 数据目录（只读）")
    args = parser.parse_args(argv)
    _ensure_utf8_stdout()

    try:
        dialogs, stats = load_dialogs(Path(args.data_dir))
    except Exception as exc:  # noqa: BLE001 - 读数据失败也要给人话，不抛裸异常
        print(f"[失败] 读数据中断：{type(exc).__name__}: {exc}")
        return 1
    if not dialogs:
        print(f"没读到任何对话：{args.data_dir}")
        return 2

    candidates = build_candidates(dialogs)
    picked = select(candidates)
    snippets, scenes = build_records(picked)
    print(render_summary(candidates, picked, snippets, scenes, stats))

    if not args.write:
        print("")
        print(f"dry-run 结束：没有写文件。加 --write 才写 {OUT_DIR}（data/ 已 gitignore）。")
        return 0

    snippet_path, scene_path, mix_path = write_outputs(snippets, scenes, picked, stats, len(candidates))
    print("")
    print("已写入：")
    print(f"  {snippet_path}（{len(snippets)} 条）")
    print(f"  {scene_path}（{len(scenes)} 条）")
    print(f"  {mix_path}（source / license_note / 策略与 intent 分布）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
