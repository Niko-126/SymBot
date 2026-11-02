"""记忆交接的声明层。

职责：判断一个 TXT 到底是什么（对白 / 台词库 / 训练对 / 迁移信 / 普通文本），
以及——最关键的——确定"谁是我、谁是她"。

为什么需要它：语料里的「对方」是**相对**的。女主包里的「对方」是男主，
男主包里的「对方」是女主。没有声明的解析器只能猜，猜错就是把两个人的口吻
焊在一起，而且事后极难发现。
﻿‍​‌﻿​‌​‌‌‍​‌‌‌‍‍​﻿‌​‌‍​​​​﻿​​​‍‍﻿"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field

from app.persona.load import Persona

_DATE = r"(\d{4})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})"
_RE_DATE = re.compile(_DATE)
_RE_RANGE_CN = re.compile(
    rf"从\s*{_DATE}\s*[日号]?\s*(?:到|至|~|～|-|—)\s*{_DATE}"
)
_RE_RANGE_ANY = re.compile(
    rf"{_DATE}\s*[日号]?\s*(?:~|～|至|到|—|–)\s*{_DATE}"
)
_RE_TARGET = re.compile(r"(?:训练)?目标[方]?\s*[：:]\s*([^\s｜|，,。：:（）()【】]{1,8})")
_RE_SOURCE = re.compile(r"来源\s*[：:]\s*(.+)")
_RE_SECTION = re.compile(r"^\s*[—─━\-]{2,}\s*(?P<title>[^—─━\-]{0,24}?)\s*[—─━\-]{2,}\s*$")
_RE_WHO = re.compile(r"^(?P<who>[^：:\s]{1,8})\s*[：:]\s*(?P<text>.+)$")
_RE_SCENE = re.compile(r"^【[^】]{0,24}】\s*(?P<when>.*)$")
_RE_TS_WHO = re.compile(
    r"^\[?(?P<ts>\d{4}[-/.]\d{1,2}[-/.]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?)\]?\s*(?P<who>[^：:\s]{1,10})\s*[：:]\s*\S"
)
_RE_QUOTE = re.compile(r"^\s*(?P<freq>\d{1,6})\s{2,}(?P<text>\S.*)$")
_RE_LETTER_HER = re.compile(r"写\s*[「『\"'](?P<label>[^」』\"']{1,4})[」』\"']\s*的\s*是\s*你")
_RE_LETTER_ME = re.compile(r"写\s*[「『\"'](?P<label>[^」』\"']{1,4})[」』\"']\s*的\s*是\s*我")
_RE_HER_NAME = re.compile(r"你叫\s*[「『\"']?(?P<name>[^\s，,。」』\"']{1,8})")
_RE_ME_NAME = re.compile(r"我叫\s*[「『\"']?(?P<name>[^\s，,。」』\"']{1,8})")

_HER_WORDS = ("女主", "女方", "她", "女")
_ME_WORDS = ("男主", "男方", "他", "我", "对方")
_OTHER_WORD = "对方"


@dataclass
class Declared:
    fmt: str = "generic"
    her_labels: set[str] = field(default_factory=set)
    me_labels: set[str] = field(default_factory=set)
    target: str = ""
    her_name: str = ""
    me_name: str = ""
    time_start: str = ""
    time_end: str = ""
    remember: list[str] = field(default_factory=list)
    forget: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warning: list[str] = field(default_factory=list)

    def role_of(self, who: str) -> str:
        who = who.strip().strip("「」『』\"'")
        if who in self.her_labels:
            return "her"
        if who in self.me_labels:
            return "me"
        # 语料里偶见全角空格或别名，做一次子串兜底
        for label in self.her_labels:
            if label and label in who:
                return "her"
        for label in self.me_labels:
            if label and label in who:
                return "me"
        return "unknown"

    def as_dict(self) -> dict:
        return {
            "format": self.fmt,
            "target": self.target,
            "her_labels": sorted(self.her_labels),
            "me_labels": sorted(self.me_labels),
            "time": f"{self.time_start} ~ {self.time_end}" if self.time_start else "",
            "remember": len(self.remember),
            "forget": len(self.forget),
        }


def _date_str(m: re.Match) -> str:
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _is_letter(text: str) -> bool:
    head = text[:3000]
    if "ai-life/memory-letter" in head:
        return True
    return bool(re.search(r"[—─━]{2,}\s*给.{0,12}的信\s*[—─━]{2,}", head))


def detect(path: pathlib.Path, text: str) -> str:
    if _is_letter(text):
        return "memory_letter"
    if path.suffix.lower() in (".jsonl", ".json"):
        return "training_pairs"
    lines = [ln for ln in text.splitlines() if ln.strip()]
    head = lines[:300]
    if head and sum(bool(_RE_QUOTE.match(ln)) for ln in head) > len(head) * 0.6:
        return "style_library"
    if any(ln.lstrip().startswith("【") and _RE_SCENE.match(ln.strip()) for ln in head[:50]):
        # 【场景 …】或【精选 …】
        if any(_RE_WHO.match(ln.strip()) for ln in head[1:60]):
            return "scene_corpus"
    if any(_RE_TS_WHO.match(ln.strip()) for ln in head[:40]):
        return "generic_dialogue"
    if any(_RE_WHO.match(ln.strip()) for ln in head[:40]):
        return "generic_dialogue"
    return "plain"


def parse(path: pathlib.Path, text: str, persona: Persona) -> Declared:
    fmt = detect(path, text)
    d = Declared(fmt=fmt)

    # ---- 一层：注释行与自由标题（所有格式共用）----
    for ln in text.splitlines()[:400]:
        s = ln.strip()
        if s.startswith("#"):
            note = s.lstrip("#").strip()
            if note:
                d.notes.append(note)
        m = _RE_TARGET.search(ln)
        if m:
            d.target = m.group(1).strip()
        m = _RE_SOURCE.search(ln)
        if m and not d.time_start:
            rr = _RE_RANGE_CN.search(m.group(1)) or _RE_RANGE_ANY.search(m.group(1))
            if rr:
                d.time_start = _date_str(rr)
                d.time_end = f"{rr.group(4)}-{int(rr.group(5)):02d}-{int(rr.group(6)):02d}"

    # ---- 二层：迁移信头部（自然语言，优先级最高）----
    if fmt == "memory_letter":
        header = text.split("正文", 1)[0]
        for ln in header.splitlines():
            m = _RE_LETTER_HER.search(ln)
            if m:
                d.her_labels.add(m.group("label").strip())
            m = _RE_LETTER_ME.search(ln)
            if m:
                d.me_labels.add(m.group("label").strip())
            m = _RE_HER_NAME.search(ln)
            if m:
                d.her_name = m.group("name").strip()
            m = _RE_ME_NAME.search(ln)
            if m:
                d.me_name = m.group("name").strip()
            rr = _RE_RANGE_CN.search(ln) or _RE_RANGE_ANY.search(ln)
            if rr and not d.time_start:
                d.time_start = _date_str(rr)
                d.time_end = f"{rr.group(4)}-{int(rr.group(5)):02d}-{int(rr.group(6)):02d}"

    # ---- 三层：目标方推断（文件名 + 头注释）----
    if not d.target:
        stem = path.stem
        for word in ("女主", "男主", "女方", "男方"):
            if stem.startswith(word):
                d.target = word
                break

    _resolve_labels(d, persona)
    return d


def _resolve_labels(d: Declared, persona: Persona) -> None:
    name = persona.name
    target_is_her = False
    if d.target:
        target_is_her = any(w in d.target for w in _HER_WORDS) or d.target == name

    if d.her_labels and d.me_labels:
        # 迁移信里写明了，不再推断
        d.her_labels.add(name)
        d.her_labels.add("她")
        d.me_labels.add("我")
        return

    if d.her_labels:  # 只写了一半，另一半按默认补
        d.her_labels.add(name)
        d.me_labels |= {"我", "他", "男主", "男方", _OTHER_WORD}
        return

    if target_is_her:
        d.her_labels = {d.target, "女主", "女方", "她", name, "你"}
        d.me_labels = {"对方", "我", "他", "男主", "男方"}
    elif d.target:
        # 目标是男方：说明这份语料在教"他"说话，那「对方」才是她
        d.me_labels = {d.target, "男主", "男方", "他", "我", "你"}
        d.her_labels = {"对方", "女主", "女方", "她", name}
        d.warning.append(
            f"检测到目标方是「{d.target}」，已把「对方」当作她，把目标方当作用户。"
            "如果你是想用这份语料训练她，请换女主包。"
        )
    else:
        d.her_labels = {"女主", "女方", "她", name}
        d.me_labels = {"我", "他", "男主", "男方", "对方"}


def map_unknown(d: Declared, who: str, text: str) -> str:
    """未知标签的处理：不猜。宁可记成 me，也不要把陌生人的口吻当成她。"""
    return "me"
