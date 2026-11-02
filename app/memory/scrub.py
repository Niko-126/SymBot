"""去敏：把真实信息摘干净，只留"人怎么说话"。

原则：宁可多删，不可漏留。
一段语气学不学得到，是精度问题；一个真实身份漏出去，是安全问题。
﻿‌‍﻿﻿﻿​​‍‍‌‌‌‍﻿‌‍‍‍﻿﻿‍‍﻿﻿‍‍​‌​‍‌﻿﻿"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from app.memory.surnames import SURNAMES as _SURNAMES

# (名称, 正则, 替换)
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("wxid", re.compile(r"wxid_[A-Za-z0-9_]+"), "{账号}"),
    ("qq", re.compile(r"(?<!\d)QQ\s*[:：]?\s*\d{5,12}", re.IGNORECASE), "{QQ}"),
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "{手机}"),
    ("idcard", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "{证件}"),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}"), "{邮箱}"),
    ("url", re.compile(r"https?://\S+"), "{链接}"),
    ("longdigit", re.compile(r"(?<!\d)\d{6,}(?!\d)"), "{号码}"),
    ("ip", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "{地址}"),
    ("money", re.compile(r"(?<!\d)\d{4,}(?=\s*(?:块|元|万|千))"), "{金额}"),
    ("money2", re.compile(r"[￥¥]\s*[\d.,]+"), "{金额}"),
    ("wechat_id", re.compile(r"(?:微信号|微信号码|weixin|vx|VX)\s*[：:]?\s*[A-Za-z][A-Za-z0-9_-]{3,}"), "{微信号}"),
]

# 高频但无意义的占位符，保留以维持语感
_KEEP = {"[图片]", "[语音]", "[视频]", "[表情]", "[动画表情]", "[文件]", "[位置]", "[链接]", "[红包]", "[转账]"}


@dataclass
class ScrubReport:
    hits: dict[str, int] = field(default_factory=dict)
    names: list[str] = field(default_factory=list)

    def add(self, kind: str, n: int = 1) -> None:
        self.hits[kind] = self.hits.get(kind, 0) + n

    @property
    def total(self) -> int:
        return sum(self.hits.values())

    def as_dict(self) -> dict:
        return {"total": self.total, "by_kind": self.hits, "names": self.names}


def scrub(text: str, names: list[str] | None = None, report: ScrubReport | None = None) -> str:
    """把一段文本里的可识别信息替换成占位符。names 是已知的昵称/真名，会被替换成 {名字}。"""
    rep = report or ScrubReport()
    out = text
    all_names = [n for n in (names or []) if n and not n.startswith("re:")]
    regexes: list[re.Pattern[str]] = []
    for n in names or []:
        if isinstance(n, str) and n.startswith("re:"):
            try:
                regexes.append(re.compile(n[3:]))
            except re.error:
                continue

    for name in sorted(set(all_names), key=len, reverse=True):
        name = name.strip()
        if not name or name in _KEEP:
            continue
        if len(name) >= 2 and name in out:
            out = out.replace(name, "{名字}")
            rep.add("name", 1)
            if name not in rep.names:
                rep.names.append(name)

    for kind, pattern, repl in _PATTERNS:
        out, n = pattern.subn(repl, out)
        if n:
            rep.add(kind, n)

    # 正则条目：用于"上下文敏感"的名字（例如只当昵称时替换、当副词时保留）
    for rx in regexes:
        out, n = rx.subn("{名字}", out)
        if n:
            rep.add("name", n)

    # 连续占位符压掉，避免 "{号码}{号码}" 这种噪声进入语料
    out = re.sub(r"(\{[^}]{1,6}\})+(?=\1)", r"\1", out)
    return out.strip()


def scrub_events(events: list[dict], names: list[str] | None = None) -> tuple[list[dict], ScrubReport]:
    rep = ScrubReport()
    out: list[dict] = []
    for e in events:
        text = scrub(str(e.get("text", "")), names, rep)
        if not text:
            continue
        item = dict(e)
        item["text"] = text
        out.append(item)
    return out, rep


# ---------------------------------------------------------------- 人名识别

# 姓氏表统一在 app/memory/surnames.py（以前这里 157 条，extract 那边只有 79 条）

# 高置信：名片里的备注名、微信/QQ 名片
_RE_NAMECARD = re.compile(r"\[名片\][^\n]*?备注[：:]\s*([^\s　,，。;；|]+)")
# 中置信：只在明确的称呼动作后面取字
_RE_CALL_STRICT = re.compile(r"(?:mua+|亲亲|抱抱|叫|喊)\s*([\u4e00-\u9fff]{2,3})(?![\u4e00-\u9fff])")


_COMMON_WORDS = set(
    "今天 明天 昨天 现在 刚才 时候 什么 怎么 这样 那样 这个 那个 我们 你们 他们 她们 "
    "自己 知道 觉得 可以 应该 因为 所以 但是 如果 然后 真的 有点 一下 一会 一直 已经 "
    "喜欢 讨厌 开心 难过 生气 睡觉 吃饭 上班 下班 回家 出门 手机 电脑 游戏 视频 照片 "
    "声音 感觉 东西 事情 问题 地方 时间 朋友 家里 学校 公司 医院 老板 同事 老师 同学 "
    "宝宝 宝贝 老婆 老公 妈妈 爸爸 姐姐 哥哥 弟弟 妹妹 阿姨 叔叔 奶奶 爷爷 外婆 外公 "
    "可以 没事 好的 不行 不要 没有 是不是 对不对 有没有 想不想 好不好 对不起 谢谢你 "
    "爱你 想你 亲亲 抱抱 晚安 早安 哈哈 呜呜 怎么 干嘛 可爱 温柔 笨蛋 猪猪 讨厌 难受 "
    "累了 困了 饿了 睡了 好嘛 好呀 好的哦 知道啦 不知道 我爱你"
    .split()
)


# 姓氏打头但其实是普通词的，永久排除
_NAME_BLOCKLIST = {
    "岳父", "和好", "安慰", "平方", "安全感", "安静", "平时", "方向", "方面",
    "万一", "于是", "高兴", "周围", "安全", "干净", "方便", "明天", "礼貌",
    "程度", "颜色", "江湖", "黄昏", "童年", "白天", "白天鹅", "乌云", "马上",
}


def load_denylist(path: "pathlib.Path | None") -> list[str]:
    """手动黑名单：每行一个名字/昵称，强制替换。放在这里的话不用等自动识别。"""
    import pathlib as _p

    if path is None or not _p.Path(path).exists():
        return []
    raw = _p.Path(path).read_text(encoding="utf-8", errors="replace")
    return [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def _plausible(tok: str) -> bool:
    if not (2 <= len(tok) <= 3):
        return False
    if tok in _COMMON_WORDS or tok in _KEEP or tok in _NAME_BLOCKLIST:
        return False
    if any(ch in tok for ch in "的了是我你他她它们吗呢吧啊呀哦嘛"):
        return False
    if tok.endswith("宝"):
        return False
    return True


_PATTERN_NAMES = {}


def detect_names(events: list[dict], min_mid: int = 3) -> tuple[dict[str, int], dict[str, int]]:
    """返回 (要替换的, 疑似待确认的)。

    要替换的判定：姓氏打头（高置信），或在 ≥2 种不同的称呼方式里都出现过。
    真人名会被反复用不同方式叫（"mua凯杰"、"凯杰宝宝"），普通词组不会。
    剩下的只进报告，不自动替换——误伤一句语气，比漏掉一个真名更可逆。
    """
    high: Counter[str] = Counter()
    mid: Counter[str] = Counter()

    for e in events:
        text = str(e.get("text", ""))
        for m in _RE_NAMECARD.finditer(text):
            tok = m.group(1).strip()
            if _plausible(tok):
                high[tok] += 5
        if len(text) > 80:
            continue
        for m in _RE_CALL_STRICT.finditer(text):
            tok = m.group(1)
            if not _plausible(tok):
                continue
            if tok[0] in _SURNAMES:
                high[tok] += 1
            else:
                mid[tok] += 1

    # 只有"姓氏打头"和"名片备注"才自动替换。
    # 其余一律只报不改——误伤一句正常话，比多留一个待审词更难挽回。
    rest = {k: v for k, v in mid.items() if v >= min_mid}
    return dict(high), rest


def suggest_names(events: list[dict], limit: int = 30) -> list[dict]:
    high, mid = detect_names(events)
    out = [{"token": k, "count": v, "confidence": "high"} for k, v in Counter(high).most_common(limit)]
    out += [{"token": k, "count": v, "confidence": "mid"} for k, v in Counter(mid).most_common(limit)]
    return out[:limit]
