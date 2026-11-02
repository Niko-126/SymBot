"""参考语料：学"人是怎么相处的"，不碰"用户是谁"。

与 ingest.py 的分工：
- ingest.py  → 写 memories / relation，是**关于这个用户的事实**；
- reference.py → 只写 ref_turns / ref_quotes，是**关于人类行为的样本**。

两条数据流永不相交。参考语料里出现的名字、账号、地点一律去敏；
用户的记忆绝不会因为导入了别人的历史而多出什么。
﻿​​‍​‍‍‍​﻿​‍​​‍‍﻿﻿‍‌​‍‌﻿‍﻿﻿‍﻿﻿​‍‌﻿"""

from __future__ import annotations

import pathlib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.core import db
from app.memory.scrub import ScrubReport, detect_names, load_denylist, scrub

_MSG_HEAD = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2})\s+(?P<who>.+?)\s*$"
)
_RE_NICKNAME = re.compile(r"对方昵称\s*[：:]\s*(.+?)\s*(?:导出账号|　|$)")
_RE_EXPORT = re.compile(r"导出账号\s*[：:]\s*([^\s　]+)")

# 情绪分期：按月打标签，用于决定"这段能学什么"
SWEET_WORDS = ("想你", "喜欢", "宝宝", "乖", "亲", "抱", "晚安", "爱你", "么么", "辛苦", "可爱")
STRAIN_WORDS = ("累", "烦", "算了", "随便", "分手", "离开", "别联系", "冷静", "谈谈", "对不起",
                "抱歉", "哭", "难过", "删", "拉黑", "不爱", "生气", "吵架", "沉默", "不想说")

PERIOD_LABELS = {
    "honeymoon": "热恋期",
    "stable": "稳定期",
    "cooling": "降温期",
    "break": "破裂期",
    "unknown": "未定",
}

# 系统消息不进语料，也不进原话池
_SYSTEM_LINES = {
    "你撤回了一条消息",
    "对方撤回了一条消息",
    "以下是新消息",
    "以上是打招呼的内容",
    "我通过了你的朋友验证请求，现在我们可以开始聊天了",
    "你已添加了对方，现在可以开始聊天了",
}


@dataclass
class Arc:
    months: list[dict[str, Any]] = field(default_factory=list)

    def period_of(self, month: str) -> str:
        for m in self.months:
            if m["month"] == month:
                return str(m["period"])
        return "unknown"

    def table(self) -> list[dict[str, Any]]:
        return self.months

    def counts(self) -> dict[str, int]:
        c: Counter[str] = Counter()
        for m in self.months:
            c[str(m["period"])] += 1
        return dict(c)


def _parse_file(path: pathlib.Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()

    meta: dict[str, Any] = {"path": str(path), "nickname": "", "export_account": "", "names": []}
    for ln in lines[:10]:
        if not ln.startswith("#"):
            continue
        if not meta.get("nickname"):
            m = _RE_NICKNAME.search(ln)
            if m:
                meta["nickname"] = m.group(1).strip()
        if not meta.get("export_account"):
            m = _RE_EXPORT.search(ln)
            if m:
                meta["export_account"] = m.group(1).strip()
    if meta.get("nickname"):
        meta["names"].append(meta["nickname"])

    events: list[dict[str, Any]] = []
    who = ""
    ts = ""
    buf: list[str] = []

    def flush() -> None:
        if not buf or not who:
            return
        text = " ".join(x for x in buf if x).strip()
        if not text:
            return
        me = meta.get("export_account") or ""
        role = "me" if (me and who.strip() == me) else "her"
        events.append({"ts": ts, "role": role, "who": who, "text": text})

    for ln in lines:
        if ln.startswith("#"):
            continue
        m = _MSG_HEAD.match(ln.strip("\r"))
        if m:
            flush()
            buf = []
            who = m.group("who").strip()
            ts = f"{m.group('date')}T{m.group('time')}"
            continue
        if ln.strip():
            buf.append(ln.strip())
    flush()
    return events, meta


def _month_stats(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for e in events:
        month = str(e["ts"])[:7]
        st = stats.setdefault(month, {"month": month, "n": 0, "her": 0, "sweet": 0, "strain": 0})
        st["n"] += 1
        if e["role"] == "her":
            st["her"] += 1
            if any(k in e["text"] for k in SWEET_WORDS):
                st["sweet"] += 1
            if any(k in e["text"] for k in STRAIN_WORDS):
                st["strain"] += 1
    return stats


def classify_stats(stats: dict[str, dict[str, Any]]) -> Arc:
    """按月定阶段。

    判定不追求学术准确，追求"能用"：把明显甜的上半段和明显冷的下半段分开，
    中间留一段过渡。热恋期用于学语气，降温期用于学修复，破裂期单独隔离。
    """
    months = sorted(stats)
    if not months:
        return Arc([])

    rows: list[dict[str, Any]] = []
    for m in months:
        s = stats[m]
        n = max(1, s["n"])
        rows.append({
            "month": m,
            "messages": s["n"],
            "her_ratio": round(s["her"] / n, 2),
            "sweet_rate": round(s["sweet"] / n * 1000, 1),
            "strain_rate": round(s["strain"] / n * 1000, 1),
        })

    # 以甜度分位定热恋段；以体积萎缩 + 负面率上升定破裂段
    sweet_rates = sorted(r["sweet_rate"] for r in rows)
    high = sweet_rates[int(len(sweet_rates) * 0.7)] if sweet_rates else 0
    volumes = sorted(r["messages"] for r in rows)
    low_vol = volumes[int(len(volumes) * 0.3)] if volumes else 0

    for i, r in enumerate(rows):
        tail = i >= len(rows) - max(2, len(rows) // 6)
        if tail and (r["strain_rate"] >= 6.5 or r["messages"] < low_vol * 0.6):
            r["period"] = "break"
        elif r["sweet_rate"] >= high and r["sweet_rate"] > 0:
            r["period"] = "honeymoon"
        elif r["messages"] < low_vol and r["strain_rate"] >= 4.5:
            r["period"] = "cooling"
        else:
            r["period"] = "stable"
    return Arc(rows)


def classify_arc(events: list[dict[str, Any]]) -> Arc:
    return classify_stats(_month_stats(events))


def ingest_reference(
    path: pathlib.Path,
    her_name: str,
    names: list[str] | None = None,
    max_events: int = 0,
    force: bool = False,
    min_mid: int = 3,
    write_candidates: bool = True,
    arc: "Arc | None" = None,
) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{path}")

    import hashlib

    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    if not force and db.kv_get(f"ref_ingest:{digest}") == "done":
        return {"ok": True, "already_imported": True, "path": str(path), "turns_written": 0}
    if force:
        with db.cursor() as cur:
            cur.execute("DELETE FROM ref_turns WHERE source = ?", (path.name,))

    events, meta = _parse_file(path)
    if not events:
        return {"ok": False, "error": "没有解析出消息", "path": str(path)}
    if max_events:
        events = events[:max_events]

    if arc is None:
        arc = classify_arc(events)
    senders = {str(e["who"]).strip() for e in events} - {meta.get("export_account", "")}
    detected, suspected = detect_names(events, min_mid=min_mid)
    deny = (
        load_denylist(path.parent / "denylist.txt")
        + load_denylist(pathlib.Path("data/denylist.txt"))
    )
    all_names = list(dict.fromkeys(
        (names or [])
        + list(meta.get("names") or [])
        + [x for x in senders if x and "wxid" not in x]
        + list(detected.keys())
        + deny
    ))

    report = ScrubReport()
    keep: list[dict[str, Any]] = []
    for e in events:
        text = scrub(str(e["text"]), all_names, report)
        if not text or text in ("{名字}",):
            continue
        if text.startswith("[") and text.endswith("]"):
            continue  # 纯占位符消息没有语料价值
        if text.startswith(("[名片", "[转账", "[红包", "[文件", "[位置")):
            continue  # 名片/转账类消息本身携带真实信息，整条不进语料
        if text in _SYSTEM_LINES:
            continue
        e = dict(e)
        e["text"] = text
        e["period"] = arc.period_of(str(e["ts"])[:7])
        keep.append(e)

    # 参考语料只进 ref_turns，绝不进 memories / relation
    db.executemany(
        "INSERT INTO ref_turns (ts, role, text, period, source) VALUES (?, ?, ?, ?, ?)",
        [(e["ts"], e["role"], e["text"], e["period"], path.name) for e in keep],
    )

    # 原话池：只取热恋期与稳定期里她自己说的话
    quote_counter: Counter[str] = Counter()
    for e in keep:
        if e["role"] != "her" or e["period"] not in ("honeymoon", "stable"):
            continue
        t = e["text"].strip()
        if not (4 <= len(t) <= 40) or "{" in t:
            continue
        if t in _SYSTEM_LINES or len(set(t)) <= 2:
            continue
        if _is_explicit(t):
            continue
        quote_counter[t] += 1
    top = quote_counter.most_common(300)
    db.executemany(
        "INSERT OR IGNORE INTO ref_quotes (text, freq, period) VALUES (?, ?, ?)",
        [(t, f, "honeymoon_stable") for t, f in top],
    )
    db.kv_set(f"ref_ingest:{digest}", "done")

    if write_candidates:
        _write_candidates(suspected, detected)
    return {
        "ok": True,
        "path": str(path),
        "nickname_scrubbed": bool(meta.get("nickname")),
        "events_parsed": len(events),
        "turns_written": len(keep),
        "quotes_written": len(top),
        "scrub": report.as_dict(),
        "detected_names": [{"token": k, "count": v, "confidence": "high"} for k, v in sorted(detected.items(), key=lambda kv: -kv[1])[:15]],
        "suspected_names": [{"token": k, "count": v, "confidence": "mid"} for k, v in sorted(suspected.items(), key=lambda kv: -kv[1])[:15]],
        "denylist": deny,
        "arc": arc.table(),
        "period_counts": arc.counts(),
        "warning": _warn(arc, report),
        "digest": digest,
    }


def _warn(arc: Arc, report: ScrubReport) -> str:
    bits: list[str] = []
    counts = arc.counts()
    if counts.get("break"):
        bits.append(
            f"检测到 {counts['break']} 个破裂月份，已单独标记，不用于学习她的说话方式。"
        )
    if report.total:
        bits.append(f"去敏替换 {report.total} 处（{', '.join(f'{k}×{v}' for k, v in report.hits.items())}）。")
    names = report.names
    if names:
        bits.append("已抹掉的称呼：" + "、".join(names[:5]))
    return " ".join(bits)


def status() -> dict[str, Any]:
    turns = db.one("SELECT COUNT(*) c FROM ref_turns")
    quotes = db.one("SELECT COUNT(*) c FROM ref_quotes")
    periods = {r["period"]: r["c"] for r in db.rows("SELECT period, COUNT(*) c FROM ref_turns GROUP BY period")}
    return {
        "turns": int(turns["c"]) if turns else 0,
        "quotes": int(quotes["c"]) if quotes else 0,
        "periods": periods,
    }


def sample_quotes(limit: int = 12) -> list[str]:
    return [r["text"] for r in db.rows("SELECT text FROM ref_quotes ORDER BY freq DESC LIMIT ?", (limit,))]


# 露骨内容不进原话池（对白仍保留，供理解相处方式）。
# 若确实要做亲密向人格，再把 filter_explicit 打开成 False。
_EXPLICIT_WORDS = ("操", "射", "抽插", "骚", "做爱", "干我", "上我", "内射", "舔", "揉", "色情")


def _is_explicit(text: str) -> bool:
    return any(w in text for w in _EXPLICIT_WORDS)


def _write_candidates(suspected: dict[str, int], detected: dict[str, int]) -> None:
    """把"疑似人名"写到 data/name_candidates.txt，供人工确认后加入 denylist。

    不写进语料目录：候选名单本身也是敏感信息，留在可删除的 data/ 下。
    """
    import pathlib as _p

    out = _p.Path("data")
    out.mkdir(parents=True, exist_ok=True)
    f = out / "name_candidates.txt"
    lines = [
        "# 疑似真实人名/昵称。确认要删的，复制到 data/denylist.txt 或 语料/jilu/denylist.txt，",
        "# 每行一个，然后加 force 重新导入。不在名单里的，不会被当成名字替换。",
        "",
        "# 已自动替换（高置信）",
    ]
    lines += [f"{k}\t{v}" for k, v in sorted(detected.items(), key=lambda kv: -kv[1])]
    lines += ["", "# 待确认（未替换）"]
    lines += [f"{k}\t{v}" for k, v in sorted(suspected.items(), key=lambda kv: -kv[1])]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
