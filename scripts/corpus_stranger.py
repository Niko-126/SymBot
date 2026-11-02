"""用 CrossWOZ 公开数据补"陌生人 / 服务人员"场景（Apache-2.0）。

为什么要有这个：`data/human_life/_derived` 的覆盖报告里一直挂着一条
`relation/stranger_turns：陌生人场景没有对话轮数样本`。日常语料里不敢自己编陌生人说话，
这里就用公开数据集补——用户是 self，服务人员/司机是 other。

数据来源：thu-coai/CrossWOZ（Apache-2.0），`data/crosswoz/{train,val,test}.json.zip`。
一段对话 = `{id: {messages:[{content, role:"usr"|"sys", dialog_act:[...]}], goal:[[编号,领域,槽位,值,...]], ...}}`。
`dialog_act` 每条形如 `["Inform", "餐馆", "人均消费", "50-100元"]`，第 2 项是领域；
`General` 类型（greet / thank / bye …）没有领域，我们只取非 General 的那些来判窗口属于哪个领域。

判定规则：
- 窗口内出现过 `出租` -> relation=**stranger**（对方是司机，不认识的人）；
- 餐馆 / 酒店 / 景点 / 地铁 -> relation=**service**（店员、客服、站务）；
- 一个窗口只收领域同质的片段，避免"餐馆对话里夹一句打车"被标错关系。

铁律：
- 只读 `data/human_life/_raw/crosswoz/`（data/ 已 gitignore），产出只写 `data/human_life/_derived/`；
- 不落数据集原始 id、不落长文，note 只写领域与许可证；
- 文本过 `app/lifecorpus/pii.mask_text`，再额外清 11 位手机号 / 座机 / 邮箱 / 身份证 / 微信 QQ 号；
- 含链接、二维码的窗口整窗丢弃；
- 异常不外抛：单个 zip 坏了跳过并计数。

用法（仓库根目录）：
    python scripts/corpus_stranger.py            # dry-run：打印能抽多少窗口、配比，不写文件
    python scripts/corpus_stranger.py --write    # 写 stranger.snippet / stranger.social_scene / stranger_mix
    python scripts/corpus_stranger.py --download # 缺 train/val 时先从 GitHub 拉（默认不联网）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 允许直接 `python scripts/corpus_stranger.py`
    sys.path.insert(0, str(ROOT))

from app.lifecorpus.pii import mask_text  # noqa: E402

RAW_DIR: Path = ROOT / "data" / "human_life" / "_raw" / "crosswoz"
OUT_DIR: Path = ROOT / "data" / "human_life" / "_derived"
SNIPPET_PATH: Path = OUT_DIR / "stranger.snippet.jsonl"
SCENE_PATH: Path = OUT_DIR / "stranger.social_scene.jsonl"
MIX_PATH: Path = OUT_DIR / "stranger_mix.json"

SPLITS: tuple[str, ...] = ("train", "val", "test")
DOWNLOAD_URL: str = "https://raw.githubusercontent.com/thu-coai/CrossWOZ/master/data/crosswoz/{split}.json.zip"

TOTAL_PER_FILE: int = 60       # snippet 与 scene 各 60 条
STRANGER_TARGET: int = 20      # 出租（司机）至少 20 条，其余是 service
WINDOW_MIN: int = 4            # 片段最少几条消息
WINDOW_MAX: int = 6            # 片段最多几条消息
MAX_TEXT_CHARS: int = 160      # 单句最多保留多少字

TAXI_DOMAIN: str = "出租"       # 唯一映射到 stranger 的领域
# 领域 -> 记录关系 / 场景设定 / 话题标签
DOMAIN_RULES: dict[str, dict[str, str]] = {
    "出租": {"relation": "stranger", "setting": "transit", "topic": "打车"},
    "地铁": {"relation": "service", "setting": "transit", "topic": "坐地铁"},
    "餐馆": {"relation": "service", "setting": "restaurant", "topic": "订餐馆"},
    "酒店": {"relation": "service", "setting": "other", "topic": "订酒店"},
    "景点": {"relation": "service", "setting": "other", "topic": "问景点"},
}
# General 类 dialog_act 的第二个位置是动作名，不是领域
_GENERAL_ACTS: frozenset[str] = frozenset({"General", "greet", "thank", "welcome", "bye", "reqmore"})

_LINK_HINT: re.Pattern[str] = re.compile(
    r"(https?://|www\.|\.com|\.cn|二维码|扫码|链接|网址)", re.IGNORECASE
)
# PII 补充清洗（mask_text 已管手机号/身份证/邮箱，这里再兜座机与微信号）
_EXTRA_PII: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "138****8000"),
    (re.compile(r"(?<!\d)0\d{2,3}[-\s]?\d{7,8}(?!\d)"), "010-****"),
    (re.compile(r"(微信|vx|VX|wx|QQ|qq)\s*[:：]?\s*[A-Za-z0-9_\-]{3,}"), "联系方式省略"),
    # 6 位以上纯数字串：营业时间/编号里混着电话碎片，没法逐条判，整段打码最稳
    (re.compile(r"(?<!\d)\d{6,}(?!\d)"), "******"),
)


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


def scrub(text: str) -> str:
    """落盘前最后一道：PII 掩码 -> 补充清洗 -> 截断。"""
    value = _clean(text)
    if not value:
        return ""
    value, _ = mask_text(value)
    for pattern, replacement in _EXTRA_PII:
        value = pattern.sub(replacement, value)
    return _clean(value)[:MAX_TEXT_CHARS]


def _domains_of(message: dict[str, Any]) -> list[str]:
    """取一条消息里的领域；General 动作（greet/thank…）没有领域，跳过。"""
    domains: list[str] = []
    acts = message.get("dialog_act")
    if not isinstance(acts, list):
        return domains
    for act in acts:
        if not isinstance(act, list) or len(act) < 2:
            continue
        head = str(act[0])
        target = str(act[1])
        if head in _GENERAL_ACTS or target in _GENERAL_ACTS:
            continue
        if target in DOMAIN_RULES:
            domains.append(target)
    return domains


# ---------------------------------------------------------------------------
# 读数据
# ---------------------------------------------------------------------------


@dataclass
class Dialog:
    """一段对话：按顺序的 (role, text, 领域集合)。"""

    split: str
    messages: list[tuple[str, str]] = field(default_factory=list)          # ("self"/"other", text)
    domains: list[frozenset[str]] = field(default_factory=list)            # 与 messages 等长


def download_split(split: str) -> Path | None:
    """按需下载一个 split；失败返回 None（不抛异常）。"""
    target = RAW_DIR / f"{split}.json.zip"
    url = DOWNLOAD_URL.format(split=split)
    print(f"  下载 {split}：{url}")
    try:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as handle:
            handle.write(response.read())
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        print(f"  [跳过] {split} 下载失败：{exc}")
        if target.exists() and target.stat().st_size == 0:
            target.unlink()
        return None
    print(f"  {split} 已保存 {target.stat().st_size} 字节")
    return target


def load_dialogs(*, want_download: bool = False) -> tuple[list[Dialog], dict[str, int]]:
    """读取全部 split 的对话。返回 (对话列表, 统计：坏 zip / 跳过的对话数)。"""
    stats: dict[str, int] = {"zips": 0, "broken_zips": 0, "dialogs": 0, "skipped_short": 0,
                             "skipped_no_alternate": 0, "missing_splits": 0}
    dialogs: list[Dialog] = []
    for split in SPLITS:
        path = RAW_DIR / f"{split}.json.zip"
        if (not path.exists()) or path.stat().st_size == 0:
            if want_download:
                path = download_split(split)
            if path is None or not path.exists() or path.stat().st_size == 0:
                stats["missing_splits"] += 1
                continue
        try:
            with zipfile.ZipFile(path) as archive:
                name = archive.namelist()[0]
                payload = json.loads(archive.read(name).decode("utf-8"))
        except (zipfile.BadZipFile, OSError, ValueError, KeyError, IndexError):
            stats["broken_zips"] += 1
            continue
        stats["zips"] += 1
        if not isinstance(payload, dict):
            stats["broken_zips"] += 1
            continue
        for _key, raw in payload.items():
            if not isinstance(raw, dict):
                continue
            raw_messages = raw.get("messages")
            if not isinstance(raw_messages, list):
                continue
            stats["dialogs"] += 1
            if len(raw_messages) < WINDOW_MIN:
                stats["skipped_short"] += 1
                continue
            dialog = Dialog(split=split)
            previous_role = ""
            alternating = True
            for message in raw_messages:
                if not isinstance(message, dict):
                    alternating = False
                    break
                role = "self" if str(message.get("role")) == "usr" else "other"
                text = _clean(message.get("content"))
                if not text:
                    continue
                if role == previous_role:
                    alternating = False   # 连续同角色、没有交错，整段不要
                    break
                previous_role = role
                dialog.messages.append((role, text))
                dialog.domains.append(frozenset(_domains_of(message)))
            if not alternating or len(dialog.messages) < WINDOW_MIN:
                stats["skipped_no_alternate"] += 1
                continue
            dialogs.append(dialog)
    return dialogs, stats


# ---------------------------------------------------------------------------
# 抽窗口
# ---------------------------------------------------------------------------


def window_domain(dialog: Dialog, start: int, length: int) -> str | None:
    """窗口的领域：非 General 的领域必须**全部一致**才认，否则返回 None（多领域混着的不要）。"""
    found: list[str] = []
    for index in range(start, start + length):
        found.extend(dialog.domains[index])
    if not found:
        return None
    unique = set(found)
    if len(unique) != 1:
        return None
    domain = unique.pop()
    return domain if len(found) >= 2 else None   # 至少要 2 处领域表态，避免纯寒暄窗口


def candidate_windows(dialogs: list[Dialog]) -> dict[str, list[tuple[Dialog, list[tuple[str, str]]]]]:
    """按领域收集候选窗口。"""
    buckets: dict[str, list[tuple[Dialog, list[tuple[str, str]]]]] = {domain: [] for domain in DOMAIN_RULES}
    seen: set[tuple[str, ...]] = set()
    for dialog in dialogs:
        for start in range(len(dialog.messages)):
            for length in range(WINDOW_MAX, WINDOW_MIN - 1, -1):
                if start + length > len(dialog.messages):
                    continue
                window = dialog.messages[start : start + length]
                if len({role for role, _ in window}) < 2:      # usr / sys 都得有
                    continue
                if any(_LINK_HINT.search(text) for _, text in window):  # 链接/二维码窗口整窗丢弃
                    continue
                domain = window_domain(dialog, start, length)
                if domain is None:
                    continue
                cleaned = [scrub(text) for _, text in window]
                if any(not text for text in cleaned):
                    continue
                key = tuple(cleaned)
                if key in seen:
                    continue
                seen.add(key)
                buckets[domain].append((dialog, window))
                break   # 同一起点只留最长的合格窗口
    return buckets


def pick(
    buckets: dict[str, list[tuple[Dialog, list[tuple[str, str]]]]],
) -> list[tuple[str, Dialog, list[tuple[str, str]]]]:
    """挑 60 个窗口：出租先凑 20，其余四个服务领域轮转补齐 40。"""
    picked: list[tuple[str, Dialog, list[tuple[str, str]]]] = []
    taxi = buckets.get(TAXI_DOMAIN, [])
    picked.extend((TAXI_DOMAIN, dialog, window) for dialog, window in taxi[:STRANGER_TARGET])

    service_domains = [domain for domain in DOMAIN_RULES if domain != TAXI_DOMAIN]  # 地铁/餐馆/酒店/景点
    need = TOTAL_PER_FILE - len(picked)
    cursor: Counter[str] = Counter()
    while len(picked) < TOTAL_PER_FILE and need > 0:
        progressed = False
        for domain in service_domains:
            if len(picked) >= TOTAL_PER_FILE:
                break
            items = buckets.get(domain, [])
            if cursor[domain] < len(items):
                dialog, window = items[cursor[domain]]
                cursor[domain] += 1
                picked.append((domain, dialog, window))
                progressed = True
        if not progressed:
            break
    return picked


def build_records(
    picked: list[tuple[str, Dialog, list[tuple[str, str]]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    snippets: list[dict[str, Any]] = []
    scenes: list[dict[str, Any]] = []
    for domain, _dialog, window in picked:
        rule = DOMAIN_RULES[domain]
        note = f"来源：CrossWOZ {domain} 对话（Apache-2.0）"
        messages: list[dict[str, str]] = []
        for role, text in window:
            cleaned = scrub(text)
            if not cleaned:
                messages = []
                break
            messages.append({"role": role, "text": cleaned})
        if len(messages) < WINDOW_MIN or len({m["role"] for m in messages}) < 2:
            continue
        usr_turns = sum(1 for m in messages if m["role"] == "self")
        if usr_turns < 1:
            continue
        snippets.append(
            {
                "id": f"stranger_snip_{len(snippets) + 1:03d}",
                "kind": "snippet",
                "day_type": "workday",       # 数据集没有日期，统一按工作日
                "relation": rule["relation"],
                "context": "公开数据集对话（CrossWOZ）",
                "interrupt_policy": "allow",
                "messages": messages,
                "note": note,
            }
        )
        scenes.append(
            {
                "id": f"stranger_scene_{len(scenes) + 1:03d}",
                "kind": "social_scene",
                "day_type": "workday",
                "relation": rule["relation"],
                "setting": rule["setting"],
                "participants": 2,
                "turns": min(200, usr_turns),
                "initiator": "self",          # 数据集都是用户先开口
                "interrupt_policy": "allow",
                "topic": rule["topic"],
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
    buckets: dict[str, list[tuple[Dialog, list[tuple[str, str]]]]],
    stats: dict[str, int],
) -> tuple[Path, Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with SNIPPET_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        for row in snippets:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with SCENE_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        for row in scenes:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    relation_counts: Counter[str] = Counter(row["relation"] for row in snippets)
    setting_counts: Counter[str] = Counter(row["setting"] for row in scenes)
    domain_counts: Counter[str] = Counter(row["note"].split()[1] for row in snippets)
    mix = {
        "license": "Apache-2.0",
        "source": "thu-coai/CrossWOZ",
        "url": "https://github.com/thu-coai/CrossWOZ",
        "relation_counts": dict(sorted(relation_counts.items())),
        "domain_counts": dict(sorted(domain_counts.items())),
        "setting_counts": dict(sorted(setting_counts.items())),
        "snippet_total": len(snippets),
        "scene_total": len(scenes),
        "total": len(snippets),
        "candidates": {domain: len(items) for domain, items in sorted(buckets.items())},
        "parse": stats,
        "policy": "出租(司机)->stranger；餐馆/酒店/景点/地铁->service；窗口领域必须同质，链接/二维码窗口整窗丢弃",
    }
    MIX_PATH.write_text(json.dumps(mix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return SNIPPET_PATH, SCENE_PATH, MIX_PATH


def render_summary(
    buckets: dict[str, list[tuple[Dialog, list[tuple[str, str]]]]],
    stats: dict[str, int],
    picked: list[tuple[str, Dialog, list[tuple[str, str]]]],
    snippets: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
) -> str:
    relation_counts = Counter(row["relation"] for row in snippets)
    domain_counts = Counter(row["note"].split()[1] for row in snippets)
    lines = [
        "CrossWOZ -> 陌生人/服务场景（dry-run，不写文件）",
        f"  zip {stats['zips']} 个（坏 {stats['broken_zips']}，缺 {stats['missing_splits']}）；"
        f"对话 {stats['dialogs']} 段（太短跳过 {stats['skipped_short']}，无交错跳过 {stats['skipped_no_alternate']}）",
        "  候选窗口：" + "，".join(f"{domain} {len(items)}" for domain, items in sorted(buckets.items())),
        f"  已挑 {len(picked)} 个窗口 -> snippet {len(snippets)} / scene {len(scenes)}",
        "  关系：" + "，".join(f"{key} {value}" for key, value in sorted(relation_counts.items())),
        "  领域：" + "，".join(f"{key} {value}" for key, value in sorted(domain_counts.items())),
        "  设定：" + "，".join(
            f"{key} {value}" for key, value in sorted(Counter(row["setting"] for row in scenes).items())
        ),
        f"  口径：出租->stranger 目标 {STRANGER_TARGET} 条，其余由餐馆/酒店/景点/地铁轮转补齐",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corpus_stranger",
        description="用 CrossWOZ（Apache-2.0）补 stranger/service 场景，产出写 data/human_life/_derived/",
    )
    parser.add_argument("--write", action="store_true", help="真正落盘")
    parser.add_argument("--download", action="store_true", help="缺 train/val 时从 GitHub 下载（默认不联网）")
    args = parser.parse_args(argv)
    _ensure_utf8_stdout()

    try:
        dialogs, stats = load_dialogs(want_download=bool(args.download))
    except Exception as exc:  # noqa: BLE001 - 读数据失败也要给人话，不抛裸异常
        print(f"[失败] 读数据中断：{type(exc).__name__}: {exc}")
        return 1
    if not dialogs:
        print(f"没读到任何对话：{RAW_DIR}（可加 --download 先拉数据）")
        return 2

    buckets = candidate_windows(dialogs)
    picked = pick(buckets)
    snippets, scenes = build_records(picked)
    print(render_summary(buckets, stats, picked, snippets, scenes))

    if not args.write:
        print("")
        print(f"dry-run 结束：没有写文件。加 --write 才写 {OUT_DIR}（data/ 已 gitignore）。")
        return 0

    snippet_path, scene_path, mix_path = write_outputs(snippets, scenes, buckets, stats)
    print("")
    print("已写入：")
    print(f"  {snippet_path}（{len(snippets)} 条）")
    print(f"  {scene_path}（{len(scenes)} 条）")
    print(f"  {mix_path}（license / 关系分布 / 候选量）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
