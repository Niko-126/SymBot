"""把 `语料/` 里的伴侣素材做成 partner 语料（相爱 85% / 吵架 15%，吵架硬上限）。

用途（docs/52 的伴侣包）：数字人默认要"甜"，吵架只是调味——所以这里的比例是**写死的策略**，
不是统计出来的。以后拌嘴素材再多，也只从里面下采样 15%，不会把角色带成一点就炸。

两个来源（只读）：
- `语料/相爱语料_精选.txt`：80 段相爱期原文，角色是 `他：` / `她：`（她=self，他=other）；
- `语料/女主_场景语料_严格版.txt`：1432 个场景，角色是 `对方：` / `女主：`（女主=self），
  只从这里捞含冲突词的场景补 "吵架/和好"。

铁律：
- `语料/` 只读，不改不收，不把原文长文写进仓库；
- 产出只写 `data/human_life/_derived/`（data/ 已 gitignore）；
- 文本先按 `app/lifecorpus/pii.pii.mask_text` 掩码，再按《严格版说明》的边界整条剔除
  第三方/亲属/职业指称——那段话里只要出现"他/她/妈/朋友/同事"这类没有指代对象的词，
  这句就整条不用，模型就没有可编的材料；
- 显式性内容**整窗丢弃**（亲密语料另有系统，不混进日常社交语料）；
- 单段解析失败就跳过并计数，不向外抛异常。

用法（仓库根目录）：
    python scripts/corpus_partner.py            # dry-run：只打印解析与配比，不写文件
    python scripts/corpus_partner.py --write    # 写 partner.snippet / partner.social_scene / partner_mix
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 允许直接 `python scripts/corpus_partner.py`
    sys.path.insert(0, str(ROOT))

from app.lifecorpus.pii import mask_text  # noqa: E402

CORPUS_DIR: Path = ROOT / "语料"
LOVE_PATH: Path = CORPUS_DIR / "相爱语料_精选.txt"
FIGHT_PATH: Path = CORPUS_DIR / "女主_场景语料_严格版.txt"

OUT_DIR: Path = ROOT / "data" / "human_life" / "_derived"
SNIPPET_PATH: Path = OUT_DIR / "partner.snippet.jsonl"
SCENE_PATH: Path = OUT_DIR / "partner.social_scene.jsonl"
MIX_PATH: Path = OUT_DIR / "partner_mix.json"

# 配比：snippet 100 条（85/15），scene 80 条（68/12），两处都是 85/15
SNIPPET_TOTAL: int = 100
SNIPPET_FIGHT_MAX_RATIO: float = 0.15
SCENE_TOTAL: int = 80
SCENE_FIGHT_MAX_RATIO: float = 0.15

WINDOW_MIN: int = 4          # 片段最少几条消息（必须有来有回）
WINDOW_MAX: int = 6          # 片段最多几条消息
MAX_TEXT_CHARS: int = 160    # 单句最多保留多少字（掩码后截断）
MAX_TURNS: int = 200         # 与 schema.MAX_SCENE_TURNS 一致
PER_SCENE_WINDOW_CAP: int = 2  # 同一段最多贡献几个片段，避免全来自一两段

# 冲突词：用来从女主场景里挑"吵架/和好"样本
CONFLICT_WORDS: tuple[str, ...] = (
    "吵架", "生气", "别生气", "不理你", "道歉", "哄", "冷战", "烦", "委屈",
)
# 和好词：用来判 outcome 与 topic
MAKEUP_WORDS: tuple[str, ...] = ("和好", "原谅", "抱抱", "乖", "没事了", "别气", "对不起")

# 显式性内容：命中就整窗丢弃（本表是日常社交语料，不混亲密内容）
EXPLICIT_WORDS: tuple[str, ...] = (
    "做爱", "性欲", "裸", "胸部", "床事", "乳房", "内裤", "亲热", "开房",
    "上床", "湿了", "硬了", "叫床", "大又粗", "榨干", "嘴巴酸",
    "好涩", "爆炒", "香草泥",   # 审计抓到的谐音擦边（012 段）
)

# 《严格版说明》的边界：没有指代对象的第三方/亲属/职业指称，出现就整条剔除
STRICT_TOKENS: tuple[str, ...] = (
    "他", "她", "他们", "她们", "其他", "人家", "别人", "有人", "某人", "那个人", "男的", "女的",
    "爸妈", "爸爸", "妈妈", "爹", "娘", "哥", "姐", "弟", "妹", "叔", "姨", "伯", "姑", "舅",
    "爷", "奶", "外婆", "外公", "儿子", "女儿", "孩子",
    "同学", "同事", "朋友", "闺蜜", "兄弟", "姐妹", "室友", "老板", "领导", "老师", "医生",
    "护士", "司机", "客服", "店客", "快递", "房东", "客户", "师傅", "阿姨",
)

# 话题固定标签（按关键词打分，绝不截原文）
TOPIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("吵架", ("生气", "不理你", "吵架", "烦", "委屈", "凭什么", "冷战", "道歉", "滚")),
    ("和好", ("和好", "原谅", "抱抱", "没事了", "别气", "对不起", "乖")),
    ("撒娇", ("宝宝", "抱", "亲亲", "哼", "嘛", "人家", "不准", "就要", "乖", "黏")),
    ("想念", ("想你", "想见", "多久没", "什么时候回", "在干嘛", "等你", "想我")),
    ("约会", ("见面", "出来", "看电影", "几点到", "约", "接我", "去哪", "吃啥", "吃饭")),
)
TOPIC_FALLBACK: str = "日常"

# 联系方式残留（严格版已清真名，这里再兜一层"微信/电话 + 号码"）
_CONTACT_PATTERN: re.Pattern[str] = re.compile(
    r"(微信|weixin|vx|VX|wx|电话|手机号|手机|QQ|qq)\s*[:：]?\s*[A-Za-z0-9_\-]{3,}"
)

_SEG_LOVE: re.Pattern[str] = re.compile(r"^【精选\s*(\d{3})】\s*(\d{4}-\d{2}-\d{2})\s*(.*)$")
_SEG_SCENE: re.Pattern[str] = re.compile(r"^【场景\s*(\d{4})】\s*(\d{4}-\d{2}-\d{2})\s*(.*)$")


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


def _clean(text: str) -> str:
    return " ".join(str(text or "").replace("\u3000", " ").split())


def _day_type(value: str) -> str:
    """按段首日期判工作日/休息日；解析不了就当工作日（宁可保守）。"""
    try:
        return "restday" if date.fromisoformat(value).weekday() >= 5 else "workday"
    except ValueError:
        return "workday"


def scrub(text: str) -> str:
    """落盘前的最后一道：PII 掩码 -> 去掉联系方式残留 -> 压空白 -> 截断。"""
    value = _clean(text)
    if not value:
        return ""
    value, _ = mask_text(value)
    value = _CONTACT_PATTERN.sub("", value)
    value = _clean(value)
    return value[:MAX_TEXT_CHARS]


def _hit(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


@dataclass
class Segment:
    """一段素材：一个日期 + 一串 (role, text)。"""

    no: int
    date: str
    kind: str                 # "love" / "fight_candidate"
    messages: list[tuple[str, str]] = field(default_factory=list)  # (self/other, text)

    @property
    def self_count(self) -> int:
        return sum(1 for role, _ in self.messages if role == "self")

    @property
    def first_role(self) -> str:
        return self.messages[0][0] if self.messages else "other"


def parse_corpus(
    path: Path,
    header: re.Pattern[str],
    prefixes: tuple[tuple[str, str], ...],
    kind: str,
) -> tuple[list[Segment], int]:
    """按标题行切段；只认固定角色前缀，别的行一律跳过。返回 (段列表, 读坏段数)。"""
    segments: list[Segment] = []
    broken = 0
    current: Segment | None = None
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], 1

    for raw in raw_lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = header.match(line)
        if match:
            try:
                no = int(match.group(1))
            except (TypeError, ValueError):
                broken += 1
                current = None
                continue
            current = Segment(no=no, date=match.group(2), kind=kind)
            segments.append(current)
            continue
        if current is None:
            continue
        for prefix, role in prefixes:
            if line.startswith(prefix):
                text = _clean(line[len(prefix):])
                if text:
                    current.messages.append((role, text))
                break
    return [segment for segment in segments if segment.messages], broken


def load_love() -> tuple[list[Segment], int]:
    return parse_corpus(
        LOVE_PATH,
        _SEG_LOVE,
        (("她：", "self"), ("她:", "self"), ("他：", "other"), ("他:", "other")),
        "love",
    )


def load_fight_candidates() -> tuple[list[Segment], int]:
    """从女主场景里挑含冲突词的场景；不含冲突词的整段不要。"""
    segments, broken = parse_corpus(
        FIGHT_PATH,
        _SEG_SCENE,
        (("女主：", "self"), ("女主:", "self"), ("对方：", "other"), ("对方:", "other")),
        "fight_candidate",
    )
    kept = [segment for segment in segments if any(_hit(text, CONFLICT_WORDS) for _, text in segment.messages)]
    return kept, broken


# ---------------------------------------------------------------------------
# 过滤与窗口
# ---------------------------------------------------------------------------


def _is_strict_banned(text: str) -> bool:
    return _hit(text, STRICT_TOKENS)


def _runs(messages: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """把被严格版规则剔除的句子挖掉，剩下的连续片段才允许拼窗口。"""
    runs: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for role, text in messages:
        if _is_strict_banned(text):
            if current:
                runs.append(current)
                current = []
            continue
        current.append((role, text))
    if current:
        runs.append(current)
    return runs


def candidate_windows(segment: Segment, *, fight: bool = False) -> list[tuple[int, list[tuple[str, str]]]]:
    """返回该段可用的窗口：[(平衡分, 窗口)]，平衡分越小越"有来有回"。

    规则：窗口 4-6 句、两边都说话、不含显式性内容；fight=True 时还要求窗口里真的出现冲突词。
    窗口只会在"连续未被严格规则剔除"的片段内部生成，跨过被删的句子就会断。
    """
    windows: list[tuple[int, list[tuple[str, str]]]] = []
    for chunk in _runs(segment.messages):
        for start in range(len(chunk)):
            for length in range(WINDOW_MAX, WINDOW_MIN - 1, -1):
                if start + length > len(chunk):
                    continue
                window = chunk[start : start + length]
                if len({role for role, _ in window}) < 2:      # 必须两边都说话
                    continue
                if any(_hit(text, EXPLICIT_WORDS) for _, text in window):  # 显式内容整窗丢弃
                    continue
                if fight and not any(_hit(text, CONFLICT_WORDS) for _, text in window):
                    continue
                self_n = sum(1 for role, _ in window if role == "self")
                windows.append((abs(self_n - (length - self_n)), window))
                break  # 同一段起点只留最长的那个合格窗口
    return windows


def pick_windows(segments: list[Segment], want: int, *, fight: bool) -> list[tuple[Segment, list[tuple[str, str]]]]:
    """从候选段里挑窗口：先按"双方均衡"排序，同一段最多 PER_SCENE_WINDOW_CAP 个。"""
    scored: list[tuple[int, int, str, int, Segment, list[tuple[str, str]]]] = []
    for segment in segments:
        for balance, window in candidate_windows(segment, fight=fight):
            scored.append((balance, -len(window), segment.date, segment.no, segment, window))
    scored.sort(key=lambda item: (item[0], item[1], item[2], item[3]))

    picked: list[tuple[Segment, list[tuple[str, str]]]] = []
    used: Counter[int] = Counter()
    seen: set[tuple[str, ...]] = set()
    for _balance, _neg_len, _date, batch_no, segment, window in scored:
        if len(picked) >= want:
            break
        if used[batch_no] >= PER_SCENE_WINDOW_CAP:
            continue
        key = tuple(text for _, text in window)
        if key in seen:
            continue
        seen.add(key)
        used[batch_no] += 1
        picked.append((segment, window))
    return picked


# ---------------------------------------------------------------------------
# 话题与记录
# ---------------------------------------------------------------------------


def classify_topic(segment: Segment, *, fight: bool) -> str:
    """只吐固定标签，不吐原文。"""
    texts = [text for role, text in segment.messages if role == "self"] or [t for _, t in segment.messages]
    joined = "\n".join(texts)
    if fight:
        has_makeup = _hit(joined, MAKEUP_WORDS)
        return "和好" if has_makeup else "吵架"
    scores: Counter[str] = Counter()
    for label, words in TOPIC_RULES:
        if label in ("吵架", "和好"):
            continue
        hits = sum(1 for word in words if word in joined)
        if hits:
            scores[label] = hits
    if not scores:
        return TOPIC_FALLBACK
    return max(scores.items(), key=lambda item: (item[1], item[0]))[0]


def build_snippet(
    index: int,
    segment: Segment,
    window: list[tuple[str, str]],
    *,
    fight: bool,
) -> dict[str, Any] | None:
    messages: list[dict[str, str]] = []
    for role, text in window:
        cleaned = scrub(text)
        if not cleaned:
            return None
        messages.append({"role": role, "text": cleaned})
    if len({message["role"] for message in messages}) < 2:
        return None
    source = (
        f"女主场景语料 场景 {segment.no:04d}（冲突样本，占比受控）"
        if fight
        else f"来源：用户提供的相爱语料第 {segment.no:03d} 段"
    )
    return {
        "id": f"partner_snip_{index:03d}",
        "kind": "snippet",
        "day_type": _day_type(segment.date),
        "relation": "partner",
        "context": "线上私聊",
        "interrupt_policy": "allow",
        "messages": messages,
        "note": source,
    }


def _scene_has_makeup(segment: Segment) -> bool:
    """冲突场景里有没有"和好"信号：没有就给"未知"，不硬写成和好。"""
    joined = "\n".join(text for _, text in segment.messages)
    return _hit(joined, MAKEUP_WORDS)


def build_scene(
    index: int,
    segment: Segment,
    *,
    fight: bool,
) -> dict[str, Any]:
    note = (
        f"女主场景语料 场景 {segment.no:04d}（冲突样本，占比受控）"
        if fight
        else f"来源：用户提供的相爱语料第 {segment.no:03d} 段"
    )
    return {
        "id": f"partner_scene_{index:03d}",
        "kind": "social_scene",
        "day_type": _day_type(segment.date),
        "relation": "partner",
        "setting": "online",
        "participants": 2,
        "turns": min(MAX_TURNS, segment.self_count),
        "initiator": "self" if segment.first_role == "self" else "other",
        "interrupt_policy": "allow",
        "topic": classify_topic(segment, fight=fight),
        "outcome": ("和好" if _scene_has_makeup(segment) else "未知") if fight else "未知",
        "note": note,
    }


def build_mix(love_count: int, fight_count: int, *, kind: str) -> dict[str, Any]:
    total = love_count + fight_count
    return {
        "kind": kind,
        "love": round(love_count / total, 4) if total else 0.85,
        "fight": round(fight_count / total, 4) if total else 0.15,
        "love_count": love_count,
        "fight_count": fight_count,
        "total": total,
        "policy": "fight 占比硬上限 15%，超出下采样；后续语料吵架多也只取 15%",
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def plan() -> dict[str, Any]:
    """跑完全部解析与配比，返回待写数据（dry-run 也走这里）。"""
    love_segments, love_broken = load_love()
    fight_segments, fight_broken = load_fight_candidates()

    snippet_fight_target = int(round(SNIPPET_TOTAL * SNIPPET_FIGHT_MAX_RATIO))   # 15
    snippet_love_target = SNIPPET_TOTAL - snippet_fight_target                    # 85
    scene_fight_target = int(round(SCENE_TOTAL * SCENE_FIGHT_MAX_RATIO))          # 12
    scene_love_target = SCENE_TOTAL - scene_fight_target                          # 68

    love_windows = pick_windows(love_segments, snippet_love_target, fight=False)
    fight_windows = pick_windows(fight_segments, snippet_fight_target, fight=True)

    snippets: list[dict[str, Any]] = []
    for segment, window in love_windows:
        record = build_snippet(len(snippets) + 1, segment, window, fight=False)
        if record is not None:
            snippets.append(record)
    love_snippet_count = len(snippets)
    for segment, window in fight_windows:
        record = build_snippet(len(snippets) + 1, segment, window, fight=True)
        if record is not None:
            snippets.append(record)
    fight_snippet_count = len(snippets) - love_snippet_count

    # scene：相爱段按"她说话多"排序取前 N；冲突场景单独取
    ranked_love = sorted(love_segments, key=lambda item: (-item.self_count, -len(item.messages), item.no))
    scenes: list[dict[str, Any]] = []
    for segment in ranked_love[:scene_love_target]:
        scenes.append(build_scene(len(scenes) + 1, segment, fight=False))
    love_scene_count = len(scenes)
    ranked_fight = sorted(fight_segments, key=lambda item: (-item.self_count, -len(item.messages), item.no))
    for segment in ranked_fight[:scene_fight_target]:
        scenes.append(build_scene(len(scenes) + 1, segment, fight=True))
    fight_scene_count = len(scenes) - love_scene_count

    return {
        "snippets": snippets,
        "scenes": scenes,
        "mix": {
            "snippet": build_mix(love_snippet_count, fight_snippet_count, kind="snippet"),
            "social_scene": build_mix(love_scene_count, fight_scene_count, kind="social_scene"),
        },
        "parsed": {
            "love_segments": len(love_segments),
            "fight_candidates": len(fight_segments),
            "love_broken": love_broken,
            "fight_broken": fight_broken,
            "love_windows_available": sum(len(candidate_windows(seg)) for seg in love_segments),
            "fight_windows_available": sum(
                len(candidate_windows(seg, fight=True)) for seg in fight_segments
            ),
        },
    }


def render_summary(result: dict[str, Any]) -> str:
    parsed = result["parsed"]
    snippet_mix = result["mix"]["snippet"]
    scene_mix = result["mix"]["social_scene"]
    lines = [
        "伴侣语料配比（dry-run，不写文件）",
        f"  精选段 {parsed['love_segments']} 个（读坏 {parsed['love_broken']}）；"
        f"可用窗口 {parsed['love_windows_available']} 个",
        f"  冲突候选场景 {parsed['fight_candidates']} 个（读坏 {parsed['fight_broken']}）；"
        f"可用窗口 {parsed['fight_windows_available']} 个",
        "",
        "snippet：",
        f"  总数 {snippet_mix['total']} = love {snippet_mix['love_count']}"
        f" + fight {snippet_mix['fight_count']}（fight 占比 {snippet_mix['fight']:.1%}，上限 15%）",
        "social_scene：",
        f"  总数 {scene_mix['total']} = love {scene_mix['love_count']}"
        f" + fight {scene_mix['fight_count']}（fight 占比 {scene_mix['fight']:.1%}，上限 15%）",
        "",
        "话题分布（social_scene 侧）：",
    ]
    topics: Counter[str] = Counter(scene["topic"] for scene in result["scenes"])
    lines.append("  " + "，".join(f"{key} {value}" for key, value in sorted(topics.items())) if topics else "  -")
    lines.append("")
    lines.append("说明：fight 只从女主场景里含冲突词的段取，占满 15% 就不再往里加；")
    lines.append("      句子层面按《严格版说明》剔除第三方/亲属/职业指称，显式性内容整窗丢弃。")
    return "\n".join(lines)


def write_outputs(result: dict[str, Any]) -> tuple[Path, Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with SNIPPET_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        for row in result["snippets"]:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with SCENE_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        for row in result["scenes"]:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    mix = {
        "snippet": result["mix"]["snippet"],
        "social_scene": result["mix"]["social_scene"],
        "love": 0.85,
        "fight": 0.15,
        "love_count": result["mix"]["snippet"]["love_count"] + result["mix"]["social_scene"]["love_count"],
        "fight_count": result["mix"]["snippet"]["fight_count"] + result["mix"]["social_scene"]["fight_count"],
        "total": result["mix"]["snippet"]["total"] + result["mix"]["social_scene"]["total"],
        "policy": "fight 占比硬上限 15%，超出下采样；后续语料吵架多也只取 15%",
    }
    MIX_PATH.write_text(json.dumps(mix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return SNIPPET_PATH, SCENE_PATH, MIX_PATH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corpus_partner",
        description="把 语料/ 的伴侣素材做成 partner 语料（相爱 85% / 吵架 15%，硬上限）",
    )
    parser.add_argument("--write", action="store_true", help="真正落盘到 data/human_life/_derived/")
    args = parser.parse_args(argv)
    _ensure_utf8_stdout()

    if not LOVE_PATH.exists() or not FIGHT_PATH.exists():
        print(f"素材缺失：{LOVE_PATH} / {FIGHT_PATH}")
        return 2
    try:
        result = plan()
    except Exception as exc:  # noqa: BLE001 - 整个流程失败也要给人话，不抛裸异常
        print(f"[失败] 处理中断：{type(exc).__name__}: {exc}")
        return 1

    print(render_summary(result))
    if not args.write:
        print("")
        print(f"dry-run 结束：没有写文件。加 --write 才写 {OUT_DIR}（data/ 已 gitignore）。")
        return 0

    snippet_path, scene_path, mix_path = write_outputs(result)
    print("")
    print("已写入：")
    print(f"  {snippet_path}（{len(result['snippets'])} 条）")
    print(f"  {scene_path}（{len(result['scenes'])} 条）")
    print(f"  {mix_path}（85/15 策略）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
