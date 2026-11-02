"""从官方统计公报 + B 站 vlog 片段派生 rhythm（作息）与 activity（活动）语料。

用法（仓库根目录，venv 里的 python）：
    python scripts/corpus_derive.py            # 生成 + 自检 + 打印核对表
    python scripts/corpus_derive.py --dry-run  # 只打印核对表，不写任何文件

产出（data/ 已 gitignore）：
    data/human_life/_derived/rhythm.workday.jsonl   2 条（stat_workday_average / stat_workday_urban_woman）
    data/human_life/_derived/rhythm.restday.jsonl   1 条（stat_restday）
    data/human_life/_derived/activity.jsonl         N 条（24–36）

设计约定：
- 只读 ``data/human_life/_raw/`` 里的公报与 vlog 片段，绝不改原始素材。
- 所有派生记录 ``synthetic=True``：它们是"由统计均值推出来的骨架"，不是真实个体的一天。
- 数字优先从公报正文正则抽取；抽不到时用 :data:`FALLBACK_DAILY` 兜底，并在核对表里标 ⚠。
- 互联网使用是**并行活动**（可以边吃饭边刷手机），不单独占段、不加进 1440 分钟平铺。
- 异常一律不外抛：main() 统一兜住并打印原因，返回非 0 退出码。
- 不打印任何聊天原文/昵称，只打印聚合数字与公报表述。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Final

ROOT: Final[Path] = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 允许直接 `python scripts/corpus_derive.py`
    sys.path.insert(0, str(ROOT))

RAW_DIR: Final[Path] = ROOT / "data" / "human_life" / "_raw"
OUT_DIR: Final[Path] = ROOT / "data" / "human_life" / "_derived"

#: 公报第二号：分活动大类的日均时长（按一周 7 天计算）
STAT_MAIN: Final[Path] = RAW_DIR / "www.stats.gov.cn-20261031-053539.txt"
#: 公报第三号：分城乡/性别/年龄组的参与者时长与参与率
STAT_SUB: Final[Path] = RAW_DIR / "www.stats.gov.cn-20261031-053539-1.txt"
#: B 站 16 个 vlog 元数据（标题/简介/热评）
BILI_META: Final[Path] = RAW_DIR / "bili-20261031-053116.jsonl"
#: B 站 20 条带时间词/动作的候选片段（只用来挑动作，不取原文进语料）
BILI_SNIPPETS: Final[Path] = RAW_DIR / "bili-snippets-20261031-053116.jsonl"
#: 用户真实聊天 7×24 分布（由 corpus_chat.py 产出，只读聚合量）
CHAT_STATS: Final[Path] = OUT_DIR / "chat_stats.json"

DAY_MINUTES: Final[int] = 24 * 60

# ---------------------------------------------------------------------------
# 公报第二号：13 个活动大类
# ---------------------------------------------------------------------------
# (公报里的名字, 归一后的键, 兜底日均分钟)
# 兜底值来自任务书摘录，只用于"正则没抽到"时保证脚本还能跑；正常路径都用抽取值。
STAT_CATEGORIES: Final[tuple[tuple[str, str, int], ...]] = (
    ("睡觉休息", "sleep", 586),
    ("个人卫生护理", "selfcare", 56),
    ("用餐或其他饮食", "meal", 103),
    ("交通出行", "commute", 50),
    ("劳动就业", "work", 208),
    ("学习培训", "study", 107),
    ("家务劳动", "chores", 77),
    ("陪伴照料家人", "care", 30),
    ("购买商品或服务", "shopping", 15),
    ("看病就医", "clinic", 2),
    ("运动健身", "exercise", 35),
    ("文化休闲娱乐", "leisure", 153),
    ("社会交往", "social", 18),
)

#: 互联网使用（并行活动，只进 note 与交叉核对，不进平铺）
INTERNET_NAME: Final[str] = "互联网使用"
INTERNET_FALLBACK: Final[int] = 337  # 5 小时 37 分钟

# ---------------------------------------------------------------------------
# 公报第三号：6 个活动领域 + 互联网
# ---------------------------------------------------------------------------
STAT_DOMAINS: Final[tuple[tuple[str, str], ...]] = (
    ("个人生理必需活动领域", "physiological"),
    ("有酬劳动领域", "paid"),
    ("无酬劳动领域", "unpaid"),
    ("个人自由支配活动领域", "free"),
    ("学习培训领域", "study"),
    ("交通活动领域", "transport"),
    ("互联网使用", "internet"),
)

# ---------------------------------------------------------------------------
# 正则
# ---------------------------------------------------------------------------
_DURATION_RE: Final = re.compile(r"(?:均为|为)\s*(?:(\d+)\s*小时)?\s*(\d+)\s*分钟")
_PARTICIPANT_RE: Final = re.compile(r"参与者每日平均时间为\s*(?:(\d+)\s*小时)?\s*(\d+)\s*分钟")
_RATE_RE: Final = re.compile(r"活动参与率为\s*([0-9.]+)\s*%")
_RATE_NONE_RE: Final = re.compile(r"活动参与率均为\s*([0-9.]+)\s*%")
_HOURS_ONLY_RE: Final = re.compile(r"^\s*(\d+)\s*小时\s*$")
_MINUTES_RE: Final = re.compile(r"(?:(\d+)\s*小时)?\s*(?:(\d+)\s*分钟)?")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    """读 UTF-8 文本；读不到就打印原因并返回 None（不抛异常）。"""
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        print(f"[跳过] 找不到文件：{path}")
    except OSError as exc:
        print(f"[跳过] 读文件失败：{path}（{exc}）")
    except UnicodeDecodeError as exc:
        print(f"[跳过] 文件不是 UTF-8：{path}（{exc}）")
    return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """逐行读 JSONL，坏行直接跳过；文件读不到返回空列表。"""
    rows: list[dict[str, Any]] = []
    text = _read_text(path)
    if text is None:
        return rows
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _hhmm(minutes: int) -> str:
    """分钟数转 HH:MM（1440 显示 24:00），与 schema.format_hhmm 同口径。"""
    bounded = max(0, min(int(minutes), DAY_MINUTES))
    return f"{bounded // 60:02d}:{bounded % 60:02d}"


def _cn(minutes: int) -> str:
    """分钟数转"X 小时 Y 分钟"的中文写法，用于打印核对表。"""
    return f"{minutes // 60} 小时 {minutes % 60} 分钟"


def _to_minutes(hours: str | None, minutes: str | None) -> int:
    """把正则抓到的 (小时, 分钟) 两段转成总分钟。"""
    return int(hours or 0) * 60 + int(minutes or 0)


def _parse_cn_duration(fragment: str) -> int | None:
    """解析"1 小时"、"6 小时 44 分钟"、"56 分钟"这类片段；解析不了返回 None。"""
    text = fragment.strip().replace(" ", "")
    hours_only = _HOURS_ONLY_RE.match(text)
    if hours_only:
        return int(hours_only.group(1)) * 60
    match = _MINUTES_RE.search(text)
    if match is None or (match.group(1) is None and match.group(2) is None):
        return None
    return _to_minutes(match.group(1), match.group(2))


def _parse_percent(fragment: str) -> float | None:
    """解析"47.7%"这类百分比片段。"""
    match = re.search(r"([0-9.]+)\s*%", fragment)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _capture_after(line: str, label: str) -> str | None:
    """抓 label 之后、下一个中文标点之前的片段（用于"男性 6 小时 44 分钟，"这种结构）。"""
    match = re.search(rf"{label}\s*([^，；。]+)", line)
    return match.group(1) if match is None else match.group(1)


def _allocate(total: int, weights: Sequence[int]) -> list[int]:
    """最大余数法：把 total 分钟按 weights 比例分给整数分钟，保证加起来精确等于 total。"""
    weight_sum = sum(weights) or 1
    raw = [total * weight / weight_sum for weight in weights]
    floors = [int(value) for value in raw]
    remainder = total - sum(floors)
    order = sorted(range(len(weights)), key=lambda index: (-(raw[index] - floors[index]), index))
    for index in order[:remainder]:
        floors[index] += 1
    return floors


# ---------------------------------------------------------------------------
# 抽取：公报第二号（13 大类日均）
# ---------------------------------------------------------------------------


def extract_categories(text: str) -> dict[str, dict[str, Any]]:
    """从公报第二号正文抽取 13 大类的日均/参与者/参与率；抽不到用兜底值并标记。"""
    lines = text.splitlines()
    # 附表从"附表1"开始，正文的"其中"叙述都在它之前，避免匹配到附表里的分列数字
    appendix_index = next(
        (index for index, line in enumerate(lines) if "附表1" in line),
        len(lines),
    )
    body = lines[:appendix_index]

    result: dict[str, dict[str, Any]] = {}
    for name, key, fallback in STAT_CATEGORIES:
        hit = next(
            (line for line in body if f"{name}活动" in line and "居民每日平均" in line),
            "",
        )
        duration = _DURATION_RE.search(hit)
        if duration is not None:
            daily = _to_minutes(duration.group(1), duration.group(2))
            raw_daily = f"{duration.group(1) + '小时' if duration.group(1) else ''}{duration.group(2)}分钟"
            extracted = True
        else:
            daily, raw_daily, extracted = fallback, "（未抽到）", False

        if "均为" in hit:
            participant, raw_participant = daily, raw_daily
        else:
            found = _PARTICIPANT_RE.search(hit)
            if found is not None:
                participant = _to_minutes(found.group(1), found.group(2))
                raw_participant = f"{found.group(1) + '小时' if found.group(1) else ''}{found.group(2)}分钟"
            else:
                participant, raw_participant = daily, "（同日均）"

        rate_match = _RATE_RE.search(hit)
        rate = float(rate_match.group(1)) if rate_match is not None else 0.0
        raw_rate = f"{rate_match.group(1)}%" if rate_match is not None else "（未抽到）"

        result[key] = {
            "name": name,
            "daily": daily,
            "participant": participant,
            "rate": rate,
            "extracted": extracted,
            "raw_daily": raw_daily,
            "raw_participant": raw_participant,
            "raw_rate": raw_rate,
            "line": hit.strip(),
        }

    # 互联网使用（并行活动）
    hit = next((line for line in body if INTERNET_NAME in line and "居民每日平均" in line), "")
    found = _DURATION_RE.search(hit)
    if found is not None:
        result["internet"] = {
            "name": INTERNET_NAME,
            "daily": _to_minutes(found.group(1), found.group(2)),
            "extracted": True,
            "raw_daily": f"{found.group(1) + '小时' if found.group(1) else ''}{found.group(2)}分钟",
            "line": hit.strip(),
        }
    else:
        result["internet"] = {
            "name": INTERNET_NAME,
            "daily": INTERNET_FALLBACK,
            "extracted": False,
            "raw_daily": "（未抽到）",
            "line": hit.strip(),
        }
    return result


def print_category_table(categories: dict[str, dict[str, Any]]) -> None:
    """打印"提取到 vs 公报原文"核对表。"""
    print("[1] 提取核对表 —— 公报第二号（按一周 7 天计算的居民每日平均时间）")
    print("    大类                日均   参与者  参与率   原文片段                     抽取")
    total = 0
    for _name, key, fallback in STAT_CATEGORIES:
        item = categories[key]
        total += item["daily"]
        flag = "✓" if item["extracted"] and item["daily"] == fallback else ("⚠兜底" if not item["extracted"] else "⚠差异")
        print(
            f"    {item['name']:<16} {item['daily']:>4} {item['participant']:>6} {item['rate']:>6.1f}"
            f"   {item['raw_daily']:<20} {flag}"
        )
    print(f"    13 大类日均合计：{total} 分钟（期望 1440）{'✓ 自洽' if total == DAY_MINUTES else '✗ 不自洽'}")
    print(
        f"    参考（并行，不计入平铺）：{categories['internet']['name']} 日均 {categories['internet']['daily']} 分钟"
        f"（{_cn(categories['internet']['daily'])}，原文 {categories['internet']['raw_daily']}）"
    )
    print()


# ---------------------------------------------------------------------------
# 抽取：公报第三号（分性别/城乡/年龄组的参与者时长与参与率）
# ---------------------------------------------------------------------------


def extract_domains(text: str) -> dict[str, dict[str, Any]]:
    """从公报第三号正文抽取 7 个领域的参与者时长与参与率（合计/男/女/城镇/农村/18–59 岁）。"""
    lines = text.splitlines()
    appendix_index = next(
        (index for index, line in enumerate(lines) if "附表1" in line),
        len(lines),
    )
    body = lines[:appendix_index]

    result: dict[str, dict[str, Any]] = {}
    for name, key in STAT_DOMAINS:
        duration_line = next(
            (line for line in body if name in line and "的参与者每日平均时间为" in line),
            "",
        )
        rate_line = next(
            (line for line in body if name in line and "活动参与率" in line and "%" in line),
            "",
        )

        overall_raw = _DURATION_RE.search(duration_line)
        overall = _to_minutes(overall_raw.group(1), overall_raw.group(2)) if overall_raw else None

        detail: dict[str, int | None] = {}
        for label, field in (
            ("男性", "male"),
            ("女性", "female"),
            ("城镇居民", "urban"),
            ("农村居民", "rural"),
            ("18—59周岁居民", "age1859"),
        ):
            fragment = _capture_after(duration_line, label)
            detail[field] = _parse_cn_duration(fragment) if fragment else None

        rate_overall = None
        rate_match = _RATE_RE.search(rate_line)
        if rate_match is not None:
            rate_overall = float(rate_match.group(1))
        else:
            none_match = _RATE_NONE_RE.search(rate_line)
            if none_match is not None:
                rate_overall = float(none_match.group(1))

        rate_detail: dict[str, float | None] = {}
        if rate_match is not None:  # 有"其中"分项
            for label, field in (
                ("男性", "male"),
                ("女性", "female"),
                ("城镇居民", "urban"),
                ("农村居民", "rural"),
                ("18—59周岁居民", "age1859"),
            ):
                fragment = _capture_after(rate_line, label)
                rate_detail[field] = _parse_percent(fragment) if fragment else None
        else:  # 生理必需这类"参与率均为 100.0%"的写法
            for field in ("male", "female", "urban", "rural", "age1859"):
                rate_detail[field] = rate_overall

        result[key] = {
            "name": name,
            "overall": overall,
            "rate_overall": rate_overall,
            "detail": detail,
            "rate_detail": rate_detail,
            "line": duration_line.strip(),
        }
    return result


def print_domain_table(domains: dict[str, dict[str, Any]], residual: int | None) -> None:
    """打印第三号抽取结果 + 女性日均换算（参与者时长 × 参与率）。"""
    print("[2] 提取核对表 —— 公报第三号（女性分项 → 日均 = 参与者时长 × 参与率）")
    print("    领域                        参与者 参与率    日均   原文")
    for name, key in STAT_DOMAINS:
        if key == "internet":
            continue
        item = domains.get(key, {})
        female = item.get("detail", {}).get("female")
        rate = item.get("rate_detail", {}).get("female")
        derived = f"{round(female * rate / 100):>4}" if female is not None and rate is not None else "   -"
        shown_rate = f"{rate:.1f}%" if rate is not None else "  -"
        shown_female = f"{female:>5}" if female is not None else "    -"
        print(f"    {name:<24} {shown_female} {shown_rate:>7} {derived}   {item.get('line', '')[:44]}")
    internet = domains.get("internet", {})
    female_internet = internet.get("detail", {}).get("female")
    rate_internet = internet.get("rate_detail", {}).get("female")
    if female_internet is not None and rate_internet is not None:
        daily_internet = round(female_internet * rate_internet / 100)
        print(f"    互联网使用（并行）            {female_internet:>5} {rate_internet:>6.1f}% {daily_internet:>4}   {internet.get('line', '')[:44]}")
    if residual is not None:
        print(f"    女性 6 个领域日均合计 + 四舍五入残差 = {DAY_MINUTES} 分钟（残差 {residual:+d} 分钟并入生理必需/睡眠）")
    print()


# ---------------------------------------------------------------------------
# 目标时长：工作日 / 城市女性 / 休息日
# ---------------------------------------------------------------------------


def workday_targets(categories: dict[str, dict[str, Any]]) -> dict[str, int]:
    """全国口径的工作日目标：13 大类归并成 11 个 rhythm 标签。"""
    return {
        "sleep": categories["sleep"]["daily"],
        "selfcare": categories["selfcare"]["daily"],
        "meal": categories["meal"]["daily"],
        "commute": categories["commute"]["daily"],
        "work": categories["work"]["daily"],
        "study": categories["study"]["daily"],
        "chores": categories["chores"]["daily"] + categories["care"]["daily"],
        "errand": categories["shopping"]["daily"] + categories["clinic"]["daily"],
        "exercise": categories["exercise"]["daily"],
        "leisure": categories["leisure"]["daily"],
        "social": categories["social"]["daily"],
    }


def derive_female_day(domains: dict[str, dict[str, Any]]) -> tuple[dict[str, int], int | None]:
    """把女性分项换算成 6 个领域的日均分钟；返回 (领域→分钟, 残差)。"""
    values: dict[str, int] = {}
    for _name, key in STAT_DOMAINS:
        if key == "internet":
            continue
        item = domains.get(key, {})
        female = item.get("detail", {}).get("female")
        rate = item.get("rate_detail", {}).get("female")
        if female is None or rate is None:
            return {}, None
        values[key] = round(female * rate / 100)
    residual = DAY_MINUTES - sum(values.values())
    # 公报四舍五入会带来 ±1 分钟残差，统一塞进生理必需（落到睡眠），保证平铺到 1440
    values["physiological"] += residual
    return values, residual


def woman_targets(
    base: dict[str, int],
    female_domain: dict[str, int],
    categories: dict[str, dict[str, Any]],
) -> dict[str, int]:
    """城市女性版目标：领域总量用女性分项，领域内部拆法沿用全国大类比例。"""
    clinic = categories["clinic"]["daily"]
    sleep, selfcare, meal = _allocate(
        female_domain["physiological"] - clinic,
        [base["sleep"], base["selfcare"], base["meal"]],
    )
    chores, care, shopping = _allocate(
        female_domain["unpaid"],
        [categories["chores"]["daily"], categories["care"]["daily"], categories["shopping"]["daily"]],
    )
    exercise, leisure, social = _allocate(
        female_domain["free"],
        [base["exercise"], base["leisure"], base["social"]],
    )
    return {
        "sleep": sleep,
        "selfcare": selfcare,
        "meal": meal,
        "commute": female_domain["transport"],
        "work": female_domain["paid"],
        "study": female_domain["study"],
        "chores": chores + care,
        "errand": shopping + clinic,
        "exercise": exercise,
        "leisure": leisure,
        "social": social,
    }


def restday_targets(base: dict[str, int]) -> dict[str, int]:
    """休息日目标：在公报日均基础上按"晚起 + 不上班 + 休闲上调"推定，总量强制 1440。"""
    fixed = {
        "sleep": base["sleep"] + 60,
        "work": 0,
        "study": max(base["study"] - 17, 0),
        "chores": base["chores"] + 25,
        "exercise": base["exercise"] + 15,
        "commute": max(base["commute"] - 20, 0),
        "selfcare": base["selfcare"] + 4,
        "meal": base["meal"] + 7,
        "errand": base["errand"] - 2,
        "social": base["social"] + 12,
    }
    fixed["leisure"] = DAY_MINUTES - sum(fixed.values())
    return fixed


# ---------------------------------------------------------------------------
# 平铺：把有序的 (label, 分钟) 块拼成 00:00→24:00 的 segments
# ---------------------------------------------------------------------------
# 块顺序就是"一天怎么过"的推定顺序；每个块的分钟数已经按上面的目标配平，
# 自检会逐标签核对，差一分钟都会报出来，绝不悄悄写盘。
# 每个块的顺序、位置都是推定，不是真实个体的一天。

WORKDAY_AVG_BLOCKS: Final[tuple[tuple[str, int], ...]] = (
    ("sleep", 400),      # 00:00–06:40 夜里睡觉
    ("selfcare", 35),    # 06:40–07:15 起床洗漱
    ("meal", 20),        # 07:15–07:35 早餐
    ("commute", 30),     # 07:35–08:05 去程
    ("work", 120),       # 08:05–10:05 上午上班
    ("selfcare", 10),    # 10:05–10:15 喝水、洗手间
    ("work", 88),        # 10:15–11:43 上午续
    ("meal", 45),        # 11:43–12:28 午餐
    ("sleep", 55),       # 12:28–13:23 午休（计入睡觉总量）
    ("chores", 40),      # 13:23–14:03 打扫
    ("study", 60),       # 14:03–15:03 学习/培训
    ("errand", 17),      # 15:03–15:20 买东西 + 看病合并
    ("exercise", 35),    # 15:20–15:55 运动
    ("leisure", 30),     # 15:55–16:25 休闲
    ("commute", 20),     # 16:25–16:45 傍晚出门路上
    ("chores", 37),      # 16:45–17:22 做饭
    ("meal", 38),        # 17:22–18:00 晚餐
    ("social", 18),      # 18:00–18:18 和人聊两句
    ("chores", 30),      # 18:18–18:48 陪家人（陪伴照料家人）
    ("study", 47),       # 18:48–19:35 学习续
    ("leisure", 93),     # 19:35–21:08 追剧/看书
    ("screen", 30),      # 21:08–21:38 睡前刷手机（并行上网在此落段，时长算进休闲）
    ("selfcare", 11),    # 21:38–21:49 洗漱
    ("sleep", 131),      # 21:49–24:00 夜里睡觉续
)

WORKDAY_WOMAN_BLOCKS: Final[tuple[tuple[str, int], ...]] = (
    ("sleep", 410),      # 00:00–06:50
    ("selfcare", 35),    # 06:50–07:25
    ("meal", 25),        # 07:25–07:50 早餐
    ("commute", 35),     # 07:50–08:25
    ("work", 90),        # 08:25–09:55
    ("selfcare", 10),    # 09:55–10:05
    ("work", 79),        # 10:05–11:24
    ("meal", 45),        # 11:24–12:09 午餐
    ("sleep", 60),       # 12:09–13:09 午睡
    ("chores", 55),      # 13:09–14:04 打扫
    ("study", 55),       # 14:04–14:59
    ("errand", 24),      # 14:59–15:23 购物 + 看病
    ("exercise", 33),    # 15:23–15:56
    ("commute", 12),     # 15:56–16:08
    ("chores", 55),      # 16:08–17:03 做饭
    ("leisure", 45),     # 17:03–17:48
    ("meal", 34),        # 17:48–18:22 晚餐
    ("social", 17),      # 18:22–18:39
    ("chores", 43),      # 18:39–19:22 陪家人
    ("study", 46),       # 19:22–20:08
    ("leisure", 71),     # 20:08–21:19
    ("screen", 30),      # 21:19–21:49 睡前刷手机
    ("selfcare", 11),    # 21:49–22:00 洗漱
    ("sleep", 120),      # 22:00–24:00
)

RESTDAY_BLOCKS: Final[tuple[tuple[str, int], ...]] = (
    ("sleep", 586),      # 00:00–09:46 晚起
    ("selfcare", 40),    # 09:46–10:26
    ("meal", 35),        # 10:26–11:01 早午餐
    ("chores", 55),      # 11:01–11:56 打扫
    ("screen", 30),      # 11:56–12:26 躺床刷手机
    ("meal", 40),        # 12:26–13:06 午餐
    ("sleep", 60),       # 13:06–14:06 午睡
    ("leisure", 60),     # 14:06–15:06
    ("exercise", 50),    # 15:06–15:56
    ("commute", 30),     # 15:56–16:26 出门
    ("social", 30),      # 16:26–16:56
    ("errand", 15),      # 16:56–17:11
    ("screen", 30),      # 17:11–17:41
    ("meal", 35),        # 17:41–18:16 晚餐
    ("chores", 47),      # 18:16–19:03 做饭/收拾
    ("chores", 30),      # 19:03–19:33 陪家人
    ("study", 90),       # 19:33–21:03
    ("leisure", 60),     # 21:03–22:03
    ("leisure", 97),     # 22:03–23:40
    ("selfcare", 20),    # 23:40–24:00 洗漱
)


def tile(blocks: Iterable[tuple[str, int]]) -> list[dict[str, str]]:
    """把有序块拼成 segments；调用方保证总分钟 = 1440。"""
    segments: list[dict[str, str]] = []
    cursor = 0
    for label, minutes in blocks:
        start, end = cursor, cursor + minutes
        segments.append({"start": _hhmm(start), "end": _hhmm(end), "label": label})
        cursor = end
    return segments


def blocks_total(blocks: Iterable[tuple[str, int]]) -> dict[str, int]:
    """按标签汇总分钟数（screen 计入 leisure，因为刷手机落在"文化休闲娱乐"里）。"""
    totals: dict[str, int] = {}
    for label, minutes in blocks:
        key = "leisure" if label == "screen" else label
        totals[key] = totals.get(key, 0) + minutes
    return totals


# ---------------------------------------------------------------------------
# 记录构造
# ---------------------------------------------------------------------------

WORKDAY_AVG_NOTE: Final[str] = (
    "由第三次全国时间利用调查公报（第二/三号）日均时长推导，顺序为推定，非真实个体的一天。"
    "按一周 7 天加权，劳动就业日均仅 3 小时 28 分钟，故上班段不满 8 小时；"
    "互联网使用 5 小时 37 分钟为并行活动，不计入平铺；购物 15 分钟与看病 2 分钟并入跑腿；午睡计入睡觉总量。"
)
WORKDAY_WOMAN_NOTE: Final[str] = (
    "id 里的 urban_woman 是标签：时长取公报第三号女性分项（参与者时长 × 参与率）换算的日均，"
    "城镇/18–59 岁分项未与性别交叉公布，只作参考；大类内部拆法沿用全国比例，顺序为推定，非真实个体的一天。"
    "女性互联网使用 5 小时 27 分钟为并行活动不计入；购物与看病并入跑腿。"
)
RESTDAY_NOTE: Final[str] = (
    "推定：公报只给一周 7 天加权值，没有休息日分项，按「睡觉 +60 分钟、劳动就业归零、家务/休闲/运动上调、"
    "交通下调」推得，顺序为推定，等真实休息日语料替换。夜间睡眠 586 分钟 + 午睡 60 分钟；"
    "互联网为并行活动，刷手机 60 分钟只落在休闲里。"
)


def make_rhythm(
    record_id: str,
    day_type: str,
    blocks: Sequence[tuple[str, int]],
    note: str,
) -> dict[str, Any]:
    """构造一条 rhythm 记录（synthetic 恒为 true）。"""
    return {
        "id": record_id,
        "kind": "rhythm",
        "day_type": day_type,
        "synthetic": True,
        "segments": tile(blocks),
        "note": note,
    }


def act(
    record_id: str,
    day_type: str,
    activity_type: str,
    time_of_day: str,
    duration_min: int,
    location: str,
    alone: bool,
    energy: int,
    note: str,
) -> dict[str, Any]:
    """构造一条 activity 记录。"""
    return {
        "id": record_id,
        "kind": "activity",
        "day_type": day_type,
        "activity_type": activity_type,
        "time_of_day": time_of_day,
        "duration_min": duration_min,
        "location": location,
        "alone": alone,
        "energy": energy,
        "note": note,
        "synthetic": True,
    }


def build_activities() -> list[dict[str, Any]]:
    """34 条 activity：工作日常规 16 + 休息日推定 12 + B 站 vlog 片段 6。"""
    rows: list[dict[str, Any]] = [
        # ---- 工作日（顺序与平铺位置一致） ----
        act("derive_workday_sleep_night", "workday", "sleep", "late_night", 400, "home", False, 1,
            "公报第二号：睡觉休息日均 9 小时 46 分钟（含午睡），夜间段 400 分钟"),
        act("derive_workday_wake", "workday", "selfcare", "early_morning", 35, "home", False, 2,
            "公报第二号：个人卫生护理 56 分钟，早间 35 分钟"),
        act("derive_workday_breakfast", "workday", "meal", "early_morning", 20, "home", False, 2,
            "公报第二号：用餐 1 小时 43 分钟，早餐 20 分钟"),
        act("derive_workday_commute_morning", "workday", "commute", "early_morning", 30, "transit", True, 2,
            "公报第二号：交通出行 50 分钟（参与者 1 小时 2 分钟），早高峰 30 分钟"),
        act("derive_workday_work_morning", "workday", "work", "morning", 120, "office", False, 3,
            "公报第二号：劳动就业日均 3 小时 28 分钟（参与者 6 小时 23 分钟），上午 120 分钟"),
        act("derive_workday_nap", "workday", "sleep", "noon", 55, "office", True, 1,
            "公报注释：午睡计入睡觉休息；55 分钟从 9 小时 46 分钟里拆出"),
        act("derive_workday_lunch", "workday", "meal", "noon", 45, "office", False, 3,
            "公报第二号：用餐 1 小时 43 分钟，午餐 45 分钟"),
        act("derive_workday_work_afternoon", "workday", "work", "afternoon", 88, "office", False, 3,
            "公报第二号：劳动就业日均 3 小时 28 分钟，下午 88 分钟"),
        act("derive_workday_chores", "workday", "chores", "afternoon", 40, "home", True, 2,
            "公报第二号：家务劳动 1 小时 17 分钟，打扫 40 分钟"),
        act("derive_workday_study", "workday", "study", "afternoon", 60, "home", True, 3,
            "公报第二号：学习培训 1 小时 47 分钟，下午 60 分钟"),
        act("derive_workday_errand", "workday", "errand", "afternoon", 17, "store", True, 2,
            "公报第二号：购买商品或服务 15 分钟 + 看病就医 2 分钟，合并为跑腿"),
        act("derive_workday_exercise", "workday", "exercise", "afternoon", 35, "outdoor", True, 4,
            "公报第二号：运动健身 35 分钟（参与者 1 小时 10 分钟）"),
        act("derive_workday_dinner", "workday", "meal", "evening", 38, "home", False, 3,
            "公报第二号：用餐 1 小时 43 分钟，晚餐 38 分钟"),
        act("derive_workday_social", "workday", "social", "evening", 18, "home", False, 3,
            "公报第二号：社会交往 18 分钟（参与者 1 小时 9 分钟）"),
        act("derive_workday_leisure", "workday", "leisure", "evening", 123, "home", True, 4,
            "公报第二号：文化休闲娱乐 2 小时 33 分钟，晚间 123 分钟"),
        act("derive_workday_screen", "workday", "screen", "night", 30, "home", True, 2,
            "公报第二号：文化休闲娱乐含视听娱乐；互联网 5 小时 37 分钟为并行活动，只把睡前 30 分钟落段"),
        # ---- 休息日（推定） ----
        act("derive_restday_sleep_night", "restday", "sleep", "late_night", 586, "home", False, 1,
            "推定：休息日睡觉 646 分钟（夜间 586 + 午睡 60），比工作日上调 1 小时，等真实语料替换"),
        act("derive_restday_brunch", "restday", "meal", "morning", 35, "home", False, 2,
            "推定：休息日用餐 110 分钟，早午餐 35 分钟"),
        act("derive_restday_selfcare", "restday", "selfcare", "morning", 40, "home", True, 2,
            "推定：休息日个人卫生 60 分钟，上午 40 分钟"),
        act("derive_restday_chores_morning", "restday", "chores", "morning", 55, "home", True, 2,
            "推定：休息日家务上调到 132 分钟（含陪伴家人 30），上午 55 分钟"),
        act("derive_restday_screen_noon", "restday", "screen", "noon", 30, "home", True, 2,
            "推定：躺床刷手机 30 分钟；互联网使用为并行活动，不单独占满时段"),
        act("derive_restday_nap", "restday", "sleep", "afternoon", 60, "home", True, 1,
            "推定：休息日午睡 60 分钟，计入睡觉 646 分钟"),
        act("derive_restday_leisure", "restday", "leisure", "afternoon", 60, "home", True, 4,
            "推定：休息日文化休闲上调到 277 分钟（含刷手机 60），下午 60 分钟"),
        act("derive_restday_exercise", "restday", "exercise", "afternoon", 50, "outdoor", True, 4,
            "推定：休息日运动健身 50 分钟（工作日 35 分钟上调）"),
        act("derive_restday_commute", "restday", "commute", "afternoon", 30, "transit", True, 2,
            "推定：休息日交通 30 分钟（工作日 50 分钟下调）"),
        act("derive_restday_social", "restday", "social", "afternoon", 30, "restaurant", False, 3,
            "推定：休息日社会交往 30 分钟（工作日 18 分钟上调）"),
        act("derive_restday_errand", "restday", "errand", "afternoon", 15, "store", True, 2,
            "推定：休息日购物跑腿 15 分钟"),
        act("derive_restday_study", "restday", "study", "evening", 90, "home", True, 3,
            "推定：休息日学习 90 分钟（工作日 107 分钟下调）"),
        # ---- B 站 vlog 片段（note 写 bvid，不搬标题原文） ----
        act("derive_bili_pilates", "workday", "exercise", "evening", 60, "gym", True, 4,
            "B 站 vlog：BV1LNJi65ESD 片段（下班后普拉提），时长按常见课时 60 分钟估"),
        act("derive_bili_night_snack", "workday", "meal", "night", 30, "street", False, 2,
            "B 站 vlog：BV1LNJi65ESD 片段（夜宵小酌），30 分钟估"),
        act("derive_bili_bake_cake", "restday", "chores", "afternoon", 50, "home", True, 3,
            "B 站 vlog：BV11rHV6MEbJ 片段（自己动手做小蛋糕），50 分钟估"),
        act("derive_bili_hot_tea", "restday", "meal", "night", 20, "home", True, 2,
            "B 站 vlog：BV11rHV6MEbJ 片段（睡前煮热奶茶），20 分钟估"),
        act("derive_bili_muji_shopping", "restday", "errand", "afternoon", 60, "store", False, 2,
            "B 站 vlog：BV11rHV6MEbJ 片段（逛家居店），60 分钟估"),
        act("derive_bili_weekend_meet", "restday", "social", "afternoon", 180, "restaurant", False, 3,
            "B 站 vlog：BV1RYxv6oEsZ 片段（周末见姐姐、高强度出门），180 分钟估"),
    ]
    return rows


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------


def check_rhythm(record: dict[str, Any], targets: dict[str, int]) -> list[str]:
    """核对单条 rhythm：平铺连续、总分钟 1440、段数合法、标签枚举内、逐标签对上目标。"""
    problems: list[str] = []
    segments = record.get("segments") or []
    record_id = record.get("id", "?")

    if not 2 <= len(segments) <= 48:
        problems.append(f"{record_id}: 段数 {len(segments)} 超出 [2, 48]")

    total = 0
    expected_start = 0
    for index, segment in enumerate(segments):
        start = segment.get("start")
        end = segment.get("end")
        label = segment.get("label")
        try:
            start_min = int(start[:2]) * 60 + int(start[3:5])
            end_min = int(end[:2]) * 60 + int(end[3:5])
        except (TypeError, ValueError, IndexError):
            problems.append(f"{record_id}: 第 {index + 1} 段时间格式异常 {start!r}->{end!r}")
            continue
        if end_min <= start_min:
            problems.append(f"{record_id}: 第 {index + 1} 段 {start}->{end} 结束不晚于开始")
        if start_min != expected_start:
            problems.append(f"{record_id}: 第 {index + 1} 段应从 {_hhmm(expected_start)} 开始，实际 {start}")
        expected_start = end_min
        total += end_min - start_min
        if label not in _activity_enum():
            problems.append(f"{record_id}: 第 {index + 1} 段 label={label!r} 不在活动类型枚举内")

    if total != DAY_MINUTES:
        problems.append(f"{record_id}: 平铺合计 {total} 分钟，应为 {DAY_MINUTES}")
    if segments and segments[-1].get("end") != "24:00":
        problems.append(f"{record_id}: 最后一段应到 24:00，实际 {segments[-1].get('end')!r}")

    actual = blocks_total([(s.get("label"), (int(s["end"][:2]) * 60 + int(s["end"][3:5])) - (int(s["start"][:2]) * 60 + int(s["start"][3:5]))) for s in segments])
    for label, expected in targets.items():
        got = actual.get(label, 0)
        if got != expected:
            problems.append(f"{record_id}: 标签 {label} 实际 {got} 分钟，目标 {expected} 分钟")
    for label, got in actual.items():
        if label not in targets:
            problems.append(f"{record_id}: 多出标签 {label}（{got} 分钟）")
    return problems


def _activity_enum() -> frozenset[str]:
    """活动类型枚举：优先用项目 schema（唯一权威），导入失败时退回内置集合。"""
    try:
        from app.lifecorpus.schema import ACTIVITY_TYPES  # noqa: PLC0415

        return frozenset(ACTIVITY_TYPES)
    except Exception:  # noqa: BLE001 - 枚举拿不到也不能让整个脚本挂掉
        return frozenset(
            {
                "sleep", "meal", "work", "study", "commute", "exercise", "chores",
                "leisure", "social", "screen", "selfcare", "errand", "other",
            }
        )


def validate_with_schema(records: Sequence[dict[str, Any]], kind: str) -> list[str]:
    """用项目自带的 schema 复核（拿不到 schema 就跳过，由 CLI 兜底）。"""
    try:
        from app.lifecorpus.schema import validate_record  # noqa: PLC0415

        return [f"{record.get('id')}: {error}" for record in records for error in validate_record(kind, record)]
    except Exception as exc:  # noqa: BLE001
        print(f"    （schema 自检跳过：{exc}）")
        return []


# ---------------------------------------------------------------------------
# 写盘与打印
# ---------------------------------------------------------------------------


def write_jsonl(path: Path, records: Sequence[dict[str, Any]]) -> bool:
    """写成 JSONL（每行一个紧凑 JSON）；失败打印原因并返回 False。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"[失败] 写 {path} 出错：{exc}")
        return False
    print(f"    写入 {path.relative_to(ROOT)}：{len(records)} 条")
    return True


def print_self_check(
    records: Sequence[dict[str, Any]],
    targets_by_id: dict[str, dict[str, int]],
) -> list[str]:
    """打印每条 rhythm 的分钟总和与逐标签核对。"""
    print("[3] 自检 —— rhythm 必须完整平铺 00:00→24:00 且合计 1440 分钟")
    problems: list[str] = []
    for record in records:
        record_id = str(record.get("id"))
        segments = record.get("segments") or []
        total = sum(
            (int(s["end"][:2]) * 60 + int(s["end"][3:5])) - (int(s["start"][:2]) * 60 + int(s["start"][3:5]))
            for s in segments
        )
        print(f"    {record_id:<26} {len(segments):>2} 段  合计 {total:>4}/{DAY_MINUTES} 分钟  {'✓' if total == DAY_MINUTES else '✗'}")
        record_problems = check_rhythm(record, targets_by_id.get(record_id, {}))
        problems.extend(record_problems)
        if record_problems:
            for item in record_problems:
                print(f"      ✗ {item}")
    if not records:
        problems.append("没有任何 rhythm 记录")
    print()
    return problems


def print_activity_check(activities: Sequence[dict[str, Any]]) -> None:
    """打印 activity 条数、日型分布、活动类型覆盖与地点覆盖。"""
    print("[4] 自检 —— activity 覆盖")
    by_day: dict[str, int] = {}
    by_type: dict[str, int] = {}
    by_location: dict[str, int] = {}
    for record in activities:
        by_day[record["day_type"]] = by_day.get(record["day_type"], 0) + 1
        by_type[record["activity_type"]] = by_type.get(record["activity_type"], 0) + 1
        by_location[record["location"]] = by_location.get(record["location"], 0) + 1
    print(f"    条数 {len(activities)}（目标 24–36），日型分布 {by_day}")
    print(f"    活动类型 {len(by_type)} 种：{', '.join(sorted(by_type))}")
    print(f"    地点 {len(by_location)} 种：{', '.join(sorted(by_location))}")
    missing = [name for name in ("sleep", "meal", "work", "commute", "exercise", "chores", "leisure", "social", "screen", "selfcare", "errand") if not by_type.get(name)]
    print(f"    必覆盖类型缺失：{missing if missing else '无 ✓'}")
    print()


def print_chat_cross_check() -> None:
    """与用户真实聊天分布做方向性交叉核对（只打印聚合量，不碰原文）。"""
    print("[5] 交叉核对 —— 用户真实聊天 7×24 分布（只读聚合量）")
    text = _read_text(CHAT_STATS)
    if text is None:
        print("    （chat_stats.json 不存在，跳过；休息日推定仍按任务书规则执行）")
        print()
        return
    try:
        stats = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"    （chat_stats.json 解析失败：{exc}，跳过）")
        print()
        return
    deep_night = stats.get("deep_night") or {}
    day_type = stats.get("day_type") or {}
    top_cell = stats.get("top_cell") or {}
    ratio = deep_night.get("ratio")
    restday_ratio = day_type.get("restday_ratio")
    print(
        f"    深夜 0–5 点消息占比 {ratio:.2%}；休息日消息占比 {restday_ratio:.2%}；最热格子 周{top_cell.get('weekday')} {top_cell.get('hour')} 点。"
        if isinstance(ratio, (int, float)) and isinstance(restday_ratio, (int, float))
        else "    （关键字段缺失，跳过）"
    )
    print("    → 休息日作息整体后移、睡眠拉长的推定方向与真实分布一致（仅方向性核对，不参与计算）")
    print()


def print_bili_check() -> None:
    """核对 bvid 是否真的在采集结果里（只比对 id，不打印标题/评论原文）。"""
    print("[6] 自检 —— B 站 bvid 核对（只比对 id，不打印标题/评论）")
    available = {row.get("bvid") for row in _read_jsonl(BILI_META)}
    available |= {row.get("bvid") for row in _read_jsonl(BILI_SNIPPETS)}
    available.discard(None)
    used = ["BV1LNJi65ESD", "BV11rHV6MEbJ", "BV1RYxv6oEsZ"]
    for bvid in used:
        print(f"    {bvid}  {'✓ 在原始采集结果里' if bvid in available else '✗ 未找到'}")
    print(f"    原始采集共 {len(available)} 个 bvid，本次引用 {len(used)} 个（其余留作后续扩样）")
    print()


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run(dry_run: bool = False) -> int:
    """主流程：抽取 → 派生 → 自检 → 写盘。返回进程退出码。"""
    print("== 语料派生：官方统计 + vlog → rhythm / activity ==")
    print(f"项目根目录：{ROOT}")
    print(f"输出目录：{OUT_DIR.relative_to(ROOT)}（data/ 已 gitignore）")
    print()

    main_text = _read_text(STAT_MAIN) or ""
    sub_text = _read_text(STAT_SUB) or ""
    if not main_text:
        print("[失败] 公报第二号正文没读到，无法派生；请确认 _raw/ 素材存在。")
        return 2

    categories = extract_categories(main_text)
    print_category_table(categories)

    domains = extract_domains(sub_text) if sub_text else {}
    female_domain, residual = derive_female_day(domains)
    print_domain_table(domains, residual)

    if not female_domain:
        print("[提示] 第三号分项没抽全，城市女性版退回全国口径（note 会标注）。")
        female_domain = {"physiological": 0, "paid": 0, "unpaid": 0, "free": 0, "study": 0, "transport": 0}

    base = workday_targets(categories)
    targets_avg = dict(base)
    targets_woman = woman_targets(base, female_domain, categories) if sum(female_domain.values()) == DAY_MINUTES else dict(base)
    targets_rest = restday_targets(base)

    print("[2b] 三段目标时长（rhythm 平铺的标尺，单位：分钟）")
    print(f"    标签         工作日(全国)  工作日(女性)  休息日(推定)")
    for label in ("sleep", "selfcare", "meal", "commute", "work", "study", "chores", "errand", "exercise", "leisure", "social"):
        print(f"    {label:<12} {targets_avg.get(label, 0):>10} {targets_woman.get(label, 0):>12} {targets_rest.get(label, 0):>12}")
    print(f"    合计         {sum(targets_avg.values()):>10} {sum(targets_woman.values()):>12} {sum(targets_rest.values()):>12}")
    print()

    workday_records = [
        make_rhythm("stat_workday_average", "workday", WORKDAY_AVG_BLOCKS, WORKDAY_AVG_NOTE),
        make_rhythm("stat_workday_urban_woman", "workday", WORKDAY_WOMAN_BLOCKS, WORKDAY_WOMAN_NOTE),
    ]
    restday_records = [make_rhythm("stat_restday", "restday", RESTDAY_BLOCKS, RESTDAY_NOTE)]
    activities = build_activities()

    targets_by_id = {
        "stat_workday_average": targets_avg,
        "stat_workday_urban_woman": targets_woman,
        "stat_restday": targets_rest,
    }

    problems = print_self_check(workday_records + restday_records, targets_by_id)
    problems.extend(validate_with_schema(workday_records + restday_records, "rhythm"))
    print_activity_check(activities)
    problems.extend(validate_with_schema(activities, "activity"))
    print_chat_cross_check()
    print_bili_check()

    if problems:
        print(f"[失败] 自检发现 {len(problems)} 个问题，未写盘：")
        for item in problems[:20]:
            print(f"  - {item}")
        return 1

    if dry_run:
        print("[dry-run] 自检全部通过，未写任何文件。")
        print()
    else:
        print("[写盘]")
        ok = write_jsonl(OUT_DIR / "rhythm.workday.jsonl", workday_records)
        ok = write_jsonl(OUT_DIR / "rhythm.restday.jsonl", restday_records) and ok
        ok = write_jsonl(OUT_DIR / "activity.jsonl", activities) and ok
        print()
        if not ok:
            return 1

    print("下一步（人工确认后跑）：")
    print("  python scripts/life_corpus.py validate data/human_life/_derived --strict")
    print("  python scripts/life_corpus.py coverage data/human_life/_derived --strict")
    print("  python scripts/life_corpus.py report   data/human_life/_derived")
    print()
    print("口径提醒：所有派生记录 synthetic=true；互联网使用是并行活动，未计入 1440 分钟平铺。")
    return 0


def _ensure_utf8_stdout() -> None:
    """重定向到管道/文件时强制 UTF-8，避免 Windows 控制台默认 cp936 把中文和符号写成乱码。"""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):  # 极少数环境不允许重配，忽略即可
        pass


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口；异常一律兜住，不向外抛。"""
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="corpus_derive",
        description="从官方时间利用公报 + B 站 vlog 片段派生 rhythm / activity 语料（标准库，离线）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印提取核对表与自检，不写文件")
    args = parser.parse_args(argv)
    try:
        return run(dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - 约定：异常不外抛
        print(f"[异常] 派生中断：{type(exc).__name__}: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
