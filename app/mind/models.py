"""模型库与路由。

三件事：
1. 给用户看的不是模型名，是风格分类（悬浮才显示名字）。
2. 混合模式按任务挑模型：短句聊天用快的，情绪场景用细腻的，写信用能写的。
3. 每次调用都记账：token、模型、任务、属于哪个会话。
﻿​​‍﻿‍﻿‍‍‍‌​​‍﻿​‍﻿​‌‍﻿​​‌﻿‍﻿​‌﻿​​﻿"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core import db


@dataclass
class ModelSpec:
    key: str
    provider_model: str
    name: str
    style: str            # 用户可见的分类
    note: str             # 悬浮提示
    price_in: float       # 元 / 百万 token（预估）
    price_out: float
    tags: tuple[str, ...] = field(default_factory=tuple)


# 风格分类 → 模型。价格是预估，按服务商当前价目自行改 config.yaml 覆盖。
CATALOG: list[ModelSpec] = [
    ModelSpec(
        key="gentle",
        provider_model="deepseek-ai/DeepSeek-V4-Flash",
        name="DeepSeek-V4-Flash",
        style="温柔细腻",
        note="句子长一点，会顺着情绪走，适合低落的时候",
        price_in=1.0,
        price_out=3.0,
        tags=("emotional", "summary"),
    ),
    ModelSpec(
        key="crisp",
        provider_model="Qwen/Qwen3-30B-A3B-Instruct-2507",
        name="Qwen3-30B",
        style="利落干脆",
        note="回得快，句子短，像在打字聊天",
        price_in=0.7,
        price_out=2.8,
        tags=("fast", "chat"),
    ),
    ModelSpec(
        key="steady",
        provider_model="deepseek-ai/DeepSeek-V4-Pro",
        name="DeepSeek-V4-Pro",
        style="知性沉着",
        note="想得深，话少但准，适合正经聊天",
        price_in=4.0,
        price_out=12.0,
        tags=("letter", "reflect"),
    ),
    ModelSpec(
        key="lively",
        provider_model="Qwen/Qwen3-14B",
        name="Qwen3-14B",
        style="活泼跳脱",
        note="接梗快，爱开玩笑，不太正经",
        price_in=0.5,
        price_out=2.0,
        tags=("playful",),
    ),
]

BY_KEY = {m.key: m for m in CATALOG}
DEFAULT_STYLE = "crisp"

# 全局强制模型：配了它，所有任务（聊天/摘要/评估/反思/信）都用同一个模型，
# 不再走风格/混合/深思路由。用 config.yaml / config.local.yaml 的 provider.force_model。
FORCED_KEY = "forced"


def forced_model() -> str:
    try:
        from app.core import config as config_mod

        return str(getattr(config_mod.get().provider, "force_model", "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def forced_spec(model: str) -> ModelSpec:
    price_in, price_out = 1.0, 3.0
    raw = db.kv_get("price:forced", "")
    if raw:
        try:
            a, b = raw.split(",")
            price_in, price_out = float(a), float(b)
        except ValueError:
            pass
    return ModelSpec(
        key=FORCED_KEY,
        provider_model=model,
        name=model,
        style="强制",
        note="全局强制模型：所有任务都用它，含推理",
        price_in=price_in,
        price_out=price_out,
        tags=("forced", "reasoning"),
    )

# 任务 → 风格键。混合模式下按这个挑。
TASK_ROUTE = {
    "chat_default": "crisp",
    "chat_emotional": "gentle",
    "chat_playful": "lively",
    "summary": "gentle",
    "letter": "steady",
    "reflect": "steady",
}


def price_of(model_key: str) -> tuple[float, float]:
    spec = BY_KEY.get(model_key)
    if not spec:
        return (1.0, 3.0)
    override = db.kv_get(f"price:{model_key}", "")
    if override:
        try:
            a, b = override.split(",")
            return (float(a), float(b))
        except ValueError:
            pass
    return (spec.price_in, spec.price_out)


def cost_of(model_key: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = price_of(model_key)
    return round(tokens_in / 1_000_000 * pin + tokens_out / 1_000_000 * pout, 6)


def styles() -> list[dict[str, Any]]:
    return [
        {"key": m.key, "style": m.style, "note": m.note, "name": m.name}
        for m in CATALOG
    ]


def current_settings() -> dict[str, Any]:
    mix = db.kv_get("mix_mode", "0") == "1"
    # 深度思考默认开启（qq-group 分支要求）：「会想」比「回得快」重要
    deep = db.kv_get("deep_think", "1") == "1"
    show_delta = db.kv_get("show_delta", "1") == "1"
    llm_appraisal = db.kv_get("llm_appraisal", "1") == "1"
    style = db.kv_get("style_pref", DEFAULT_STYLE)
    if style not in BY_KEY:
        style = DEFAULT_STYLE
    return {"mix_mode": mix, "deep_think": deep, "show_delta": show_delta,
            "llm_appraisal": llm_appraisal, "style": style,
            "force_model": forced_model()}


def save_settings(mix_mode: bool | None = None, style: str | None = None,
                  deep_think: bool | None = None, show_delta: bool | None = None,
                  llm_appraisal: bool | None = None) -> dict[str, Any]:
    if mix_mode is not None:
        db.kv_set("mix_mode", "1" if mix_mode else "0")
    if deep_think is not None:
        db.kv_set("deep_think", "1" if deep_think else "0")
    if show_delta is not None:
        db.kv_set("show_delta", "1" if show_delta else "0")
    if llm_appraisal is not None:
        db.kv_set("llm_appraisal", "1" if llm_appraisal else "0")
    if style is not None and style in BY_KEY:
        db.kv_set("style_pref", style)
    return current_settings()


def pick(task: str = "chat_default", aversion_phase: str = "calm",
         force_think: bool = False) -> ModelSpec:
    """挑模型。全局强制优先；否则深思优先，混合按任务，单模型用用户选的风格。

    force_think：亲密/慢思考时强制走深思模型（全局强制时由强制模型覆盖）。
    """
    forced = forced_model()
    if forced:
        return forced_spec(forced)
    st = current_settings()
    if (st.get("deep_think") or force_think) and task.startswith("chat"):
        return BY_KEY["steady"]  # DeepSeek-V4-Pro：慢但想得更全
    if st["mix_mode"]:
        key = TASK_ROUTE.get(task, "crisp")
        if task == "chat_default" and aversion_phase in ("irritated", "cold"):
            key = "gentle"
        return BY_KEY[key]
    return BY_KEY[st["style"]]


# ---------------------------------------------------------------- 记账

TASK_LABEL = {
    "chat_default": "对话",
    "chat_emotional": "情绪对话",
    "chat_playful": "玩笑",
    "summary": "摘要",
    "letter": "告别信",
    "reflect": "自述",
}


def record(model_key: str, task: str, tokens_in: int, tokens_out: int, session_seq: int = 1) -> float:
    cost = cost_of(model_key, tokens_in, tokens_out)
    db.execute(
        "INSERT INTO usage (ts, session_seq, model, task, tokens_in, tokens_out, cost)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            db.kv_get("__now__", "") or _now(),
            session_seq,
            model_key,
            task,
            int(tokens_in),
            int(tokens_out),
            cost,
        ),
    )
    return cost


def _now() -> str:
    from app.memory.store import now_iso

    return now_iso()


def usage_summary(session_seq: int | None = 1, bucket: str = "hour", limit: int = 24) -> dict[str, Any]:
    """按时间桶聚合。session_seq=None 表示全部（包含已离开的）。"""
    fmt = {"hour": "%Y-%m-%d %H", "day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}.get(bucket, "%Y-%m-%d %H")

    where = "WHERE 1=1"
    args: list[Any] = []
    if session_seq is not None:
        where += " AND session_seq = ?"
        args.append(int(session_seq))

    rows = db.rows(
        f"SELECT strftime('{fmt}', ts, 'localtime') AS k, SUM(tokens_in) ti, SUM(tokens_out) to_,"
        f" SUM(cost) c, COUNT(*) n FROM usage {where} GROUP BY k ORDER BY k DESC LIMIT ?",
        (*args, int(limit)),
    )
    buckets = [
        {
            "label": r["k"],
            "tokens_in": int(r["ti"] or 0),
            "tokens_out": int(r["to_"] or 0),
            "cost": round(float(r["c"] or 0), 6),
            "calls": int(r["n"] or 0),
        }
        for r in reversed(rows)
    ]

    totals_row = db.one(
        f"SELECT SUM(tokens_in) ti, SUM(tokens_out) to_, SUM(cost) c, COUNT(*) n FROM usage {where}",
        tuple(args),
    )
    by_model = [
        {
            "model": r["model"],
            "style": BY_KEY.get(r["model"], CATALOG[0]).style,
            "tokens_in": int(r["ti"] or 0),
            "tokens_out": int(r["to_"] or 0),
            "cost": round(float(r["c"] or 0), 6),
            "calls": int(r["n"] or 0),
        }
        for r in db.rows(
            f"SELECT model, SUM(tokens_in) ti, SUM(tokens_out) to_, SUM(cost) c, COUNT(*) n"
            f" FROM usage {where} GROUP BY model ORDER BY c DESC",
            tuple(args),
        )
    ]
    return {
        "bucket": bucket,
        "totals": {
            "tokens_in": int(totals_row["ti"] or 0) if totals_row else 0,
            "tokens_out": int(totals_row["to_"] or 0) if totals_row else 0,
            "cost": round(float(totals_row["c"] or 0), 6) if totals_row else 0.0,
            "calls": int(totals_row["n"] or 0) if totals_row else 0,
        },
        "buckets": buckets,
        "by_model": by_model,
    }


def history() -> list[dict[str, Any]]:
    rows = db.rows(
        "SELECT session_seq, SUM(tokens_in) ti, SUM(tokens_out) to_, SUM(cost) c,"
        " MIN(ts) started, MAX(ts) last FROM usage GROUP BY session_seq ORDER BY session_seq DESC"
    )
    return [
        {
            "seq": int(r["session_seq"]),
            "tokens_in": int(r["ti"] or 0),
            "tokens_out": int(r["to_"] or 0),
            "cost": round(float(r["c"] or 0), 6),
            "started": r["started"],
            "last": r["last"],
        }
        for r in rows
    ]


_EMOTIONAL_HINTS = ("累", "烦", "难过", "哭", "委屈", "害怕", "怕", "不想说", "算了", "骂")
_PLAYFUL_HINTS = ("哈哈", "笑死", "mua", "亲亲", "抱抱", "宝宝", "想你了")


def task_for(text: str, aversion_phase: str = "calm", scene: str = "日常") -> str:
    """按内容和状态选任务类型。"""
    if aversion_phase in ("irritated", "cold"):
        return "chat_emotional"
    if scene == "生气":
        return "chat_emotional"
    if any(k in text for k in _PLAYFUL_HINTS):
        return "chat_playful"
    if any(k in text for k in _EMOTIONAL_HINTS):
        return "chat_emotional"
    return "chat_default"
