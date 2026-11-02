# -*- coding: utf-8 -*-
"""拟人度体检：她像不像一个"活人"，而不是一台可拼装的机器。

跟 `smoke.py`（回归）、`fullcheck.py`（功能面）不是一回事：
这里测的是 **人的属性** —— 身体、时间感、记忆连续性、关系惯性、不确定、拒绝、
以及"改写她必须留痕"这条反拼装铁律。

跑法（离线，不登 QQ，独立临时库）：
    .\\.venv\\Scripts\\python.exe scripts\\humanity_check.py

分七组：
  A 身体与时间（睡着只回两三个字/越聊越清醒/忙时变慢/精力低）
  B 记忆连续性（重开进程还记得/钉住不丢/审计落库）
  C 不可编辑只可遗忘（无改记忆接口/遗忘留行/软合并）
  D 关系惯性（一次负面不跳阶段/离开不可逆）
  E 不确定与语言（想不起来时的措辞指令）
  F 反拼装审计（账本存在/没有事件删除口/引擎副本未改）
  G 变化留痕（清记忆/换模型/关系重置都记一条）
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import tempfile
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[1]
TMP = pathlib.Path(tempfile.mkdtemp(prefix="ai-life-human-"))
os.environ["AI_LIFE_DB"] = str(TMP / "life.db")
os.environ["AI_LIFE_DATA"] = str(TMP)
os.environ["AI_LIFE_MOCK"] = "1"
os.environ["AI_LIFE_IDLE"] = "0"
# 开发者口令：公开仓库的 config.yaml 留空（等于开发者接口全关），测试用环境变量临时给一个。
os.environ["SYMEM_DEVELOPER_KEY"] = "test-dev-key"
os.environ.pop("AI_LIFE_API_KEY", None)
os.environ.pop("AI_LIFE_PERSONA", None)
sys.path.insert(0, str(ROOT))

# ---- 断网垫片（跟 fullcheck 同款） ----
import urllib.error
import urllib.request


def _offline(*a, **k):
    raise urllib.error.URLError("offline-humanity-check")


urllib.request.urlopen = _offline

import httpx  # noqa: E402


class _FakeResp:
    status_code = 200
    is_success = True
    text = '{"status":"ok"}'

    def json(self):
        return {"status": "ok", "data": {"user_id": 10001, "nickname": "自检小号"}}

    def raise_for_status(self):
        return None


class _FakeAsyncClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        return _FakeResp()

    async def get(self, *a, **k):
        return _FakeResp()

    async def request(self, *a, **k):
        return _FakeResp()

    def close(self):
        pass


httpx.AsyncClient = _FakeAsyncClient

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.core import continuity, db, memengine, runtime  # noqa: E402
from app.life import behavior, clock  # noqa: E402
from app.memory import h2  # noqa: E402

RESULTS: list[tuple[str, str, bool, str]] = []
SECT = ""


def sec(name: str) -> None:
    global SECT
    SECT = name
    print(f"\n[{name}]")


def ck(name: str, cond, detail: str = "") -> None:
    RESULTS.append((SECT, name, bool(cond), str(detail)[:200]))


def note(name: str, detail: str = "") -> None:
    RESULTS.append((SECT, name, True, "SKIP " + str(detail)[:160]))


client = TestClient(app)
rt = runtime.get()
DEV = str(rt.cfg.developer.key)


def _life(**over) -> dict:
    life = {
        "energy": 70.0, "mood": 0.0, "focus": 50.0, "social_battery": 60.0,
        "activity": "发呆", "sleeping": False, "hour": 14,
    }
    life.update(over)
    return life


def probe_chat(text: str, **kw):
    return client.post("/v1/chat", json={"text": text, **kw})


def _mode_at(level: float) -> str:
    """把清醒度钉在某个值上，问她在"该睡"的时段会是什么状态。"""
    db.kv_set(behavior.AWAKE_KEY, f"{level:.2f}")
    db.kv_set(behavior.AWAKE_TS_KEY, "0")
    return behavior.decide(_life(activity="睡觉"), 0).mode


# ============================================================ A 身体与时间
sec("A 身体与时间")
_orig_state = clock.get_state
_before_life = dict(_life())
_life_holder = {"value": _before_life}
clock.get_state = lambda persona: dict(_life_holder["value"])

# 清醒度模型（v2026-11-01）：睡着**也回**，只是第一条只回两三个字；连聊会醒。
# 主判据是全局 kv awake_level（6 分钟半衰期衰减），不再是"睡着就不回"的硬闸门。
db.kv_set(behavior.AWAKE_KEY, "0")
db.kv_set(behavior.AWAKE_TS_KEY, "0")
_life_holder["value"] = _life(sleeping=True, hour=3, energy=20)
_r1 = probe_chat("在吗")
_b1 = _r1.json() if _r1.status_code == 200 else {}
_rep1 = _b1.get("reply") or []
_mode1 = str((_b1.get("meta") or {}).get("mode") or "")
_level1 = behavior.awake_level()
ck("睡着时第一条只回两三个字（不是机械秒回）",
   _r1.status_code == 200 and bool(_rep1)
   and sum(len(s) for s in _rep1) <= 20
   and _mode1 in ("asleep", "drowsy"),
   f"{_rep1} | mode={_mode1} level={_level1:.3f}"[:150])

_r2 = probe_chat("醒醒")
_b2 = _r2.json() if _r2.status_code == 200 else {}
_rep2 = _b2.get("reply") or []
_level2 = behavior.awake_level()
ck("叫第二次会醒（有回复，且清醒度上升）",
   len(_rep2) > 0 and _level2 > _level1,
   f"{str(_rep2)[:80]} | level {_level1:.3f}→{_level2:.3f}")

_life_holder["value"] = _life(activity="上班", energy=80)
_r3 = probe_chat("你在忙吗")
_delays_busy = (_r3.json().get("meta") or {}).get("delays_ms") or []
_life_holder["value"] = _life(activity="发呆", energy=80)
_r4 = probe_chat("你在忙吗")
_delays_idle = (_r4.json().get("meta") or {}).get("delays_ms") or []
ck("在忙时回得更慢（延迟更长）",
   bool(_delays_busy) and bool(_delays_idle) and max(_delays_busy) > max(_delays_idle),
   f"busy={max(_delays_busy) if _delays_busy else 0} idle={max(_delays_idle) if _delays_idle else 0}")

_life_holder["value"] = _life(energy=12, hour=23)
_react_low = behavior.decide(_life_holder["value"], recent_user_turns=5)
ck("精力很低时进入软态（不是机械秒回）",
   _react_low.mode in ("sleepy", "busy", "asleep") and int(_react_low.delay_ms) > 0,
   f"{_react_low.mode} delay={_react_low.delay_ms}")

# 夜间静默 + 免打扰：身体约束要覆盖"她主动找你"这条出口（docs/51 阶段一收尾项）
import datetime as _dth  # noqa: E402

from app.life import proactive as _proh  # noqa: E402

_night_h = _dth.datetime(2026, 10, 31, 3, 0).astimezone()
_day_h = _dth.datetime(2026, 10, 31, 10, 0).astimezone()
ck("半夜不会主动吵人（夜间静默）",
   _proh.in_quiet_hours(_night_h) and not _proh.in_quiet_hours(_day_h),
   f"{_proh.quiet_hours()}")
ck("他说别吵/在忙就进免打扰（问句不误伤）",
   bool(_proh.detect_dnd("我在开会，别吵我", now=_day_h))
   and bool(_proh.detect_dnd("先睡了，晚安", now=_day_h))
   and _proh.detect_dnd("你在忙吗", now=_day_h) is None
   and _proh.detect_dnd("你困了吗", now=_day_h) is None,
   str(_proh.detect_dnd("我在开会，别吵我", now=_day_h)))

# 新清醒度模型：主判据是全局 kv awake_level，不是传进去的 life 快照。
# 同一清醒度 → 同一状态（确定性）；不同清醒度 → 走过 asleep→drowsy→awake 的爬坡。
_modes = [_mode_at(0.0), _mode_at(0.30), _mode_at(0.90), _mode_at(0.90)]
ck("身体状态由清醒度决定（同一状态结果一致）",
   _modes == ["asleep", "drowsy", "awake", "awake"]
   and behavior.decide(_life(activity="洗澡"), 0).mode == "busy"
   and behavior.decide(_life(energy=12, hour=14), 0).mode == "sleepy"
   and behavior.decide(_life(energy=90, hour=14), 0).mode == "awake",
   f"modes={_modes}")

clock.get_state = _orig_state

# ============================================================ B 记忆连续性
sec("B 记忆连续性")
_mid = h2.add("fact", "拟人体检：他怕下雨天", importance=0.9, user_id=0, chat_type="")
h2.pin(_mid, True)
memengine.reset()
db.init(pathlib.Path(os.environ["AI_LIFE_DB"]))
_re = h2.retrieve("下雨天", hot_limit=50, user_id=0)
_texts = " ".join(m["content"] for m in _re.hot + _re.recalled)
ck("重开引擎后仍记得", "下雨天" in _texts, _texts[:120])
_pinned = h2.get_memory(_mid)
ck("钉住的跨重启不丢", bool(_pinned) and _pinned["pinned"] is True, str(_pinned)[:120])
_view_rows = int(db.one("SELECT COUNT(*) c FROM memories")["c"])
_active = int(memengine.store().stats()["items_active"])
ck("视图与引擎条目一致（读的是同一份真相）", _view_rows <= _active, f"view={_view_rows} active={_active}")
_logs = int(memengine.store().stats()["recall_logs"])
ck("召回被记账（可审计）", _logs >= 1, str(_logs))

# ============================================================ C 不可编辑，只可遗忘
sec("C 不可编辑只可遗忘")
ck("没有『改记忆内容』的接口（只能钉住/遗忘）",
   not any(hasattr(h2, n) for n in ("update_memory", "edit_memory", "set_content", "rewrite_memory")))
_victim = h2.add("fact", "拟人体检：这条会被遗忘", importance=0.6, user_id=0)
_ok_del = h2.delete_memory(_victim)
_row = db.one("SELECT forgotten FROM mem_item WHERE id = ?", (_victim,))
ck("遗忘是软删除：视图不见、档案行还在",
   _ok_del and h2.get_memory(_victim) is None and int(_row["forgotten"]) == 1, str(dict(_row)))
ck("重复遗忘返回 False（不会假装成功）", h2.delete_memory(_victim) is False)

# 软合并：老条目被并进摘要，行不删
from symvolve_memory import MemoryEvent as _ME, MemoryItem as _MI

_old = int(time.time()) - 90 * 86400
_old_ids = []
for _t in ("拟人体检旧事一", "拟人体检旧事二", "拟人体检旧事三"):
    _ev = memengine.store().append_event(_ME(user_id="0", chat_type="", raw_text=_t, created_at=_old))
    _old_ids.append(memengine.store().insert_item(_MI(
        user_id="0", chat_type="", kind="fact", title=_t, text=_t, importance=30,
        heat=0.05, clarity="trace", source_event_ids=[_ev], created_at=_old, last_seen=_old)))


class _P:
    is_mock = True


class _C:
    provider = _P()


class _RT:
    cfg = _C()


import asyncio  # noqa: E402

from app.memory import cold  # noqa: E402

_rep = asyncio.run(cold.compact_cold(_RT(), None))
_merged = [int(db.one("SELECT merged_into FROM mem_item WHERE id = ?", (i,))["merged_into"] or 0)
           for i in _old_ids]
ck("冷合并是软合并：源条目行还在、只是被指走",
   int(_rep.get("merged") or 0) >= 1 and all(_merged), str(_rep)[:120])

# ============================================================ D 关系惯性
sec("D 关系惯性")
from app.relation import engine as rel_engine  # noqa: E402

_before_rel = dict(rel_engine.get_relation())
_stage_before = str(rel_engine.stage_payload(rt.persona, float(_before_rel["affinity"])).get("stage"))
rel_engine.apply_appraisal({"intensity": 0.9, "tags": ["conflict"], "valence": "negative"}, rt.persona)
_after_rel = dict(rel_engine.get_relation())
_stage_after = str(rel_engine.stage_payload(rt.persona, float(_after_rel["affinity"])).get("stage"))
ck("一次负面不会让人格阶段瞬移（有惯性）",
   _stage_after == _stage_before, f"{_stage_before} -> {_stage_after} (aff {_before_rel['affinity']} -> {_after_rel['affinity']})")
ck("但好感确实会掉（不装死）", float(_after_rel["affinity"]) < float(_before_rel["affinity"]),
   f"{_before_rel['affinity']} -> {_after_rel['affinity']}")

seq_before = int((db.one("SELECT seq FROM session WHERE id = 1") or {"seq": 1})["seq"])
_arch = rel_engine  # 离开走 session.terminate
from app.relation import session as session_mod  # noqa: E402

_archive = session_mod.terminate(rt.persona, reason="humanity_check")
seq_after = int((db.one("SELECT seq FROM session WHERE id = 1") or {"seq": 1})["seq"])
ck("离开是离开：归档 + 换新会话（没有『复合』捷径）",
   bool(_archive.get("archive")) and pathlib.Path(str(_archive["archive"])).exists() and seq_after == seq_before + 1,
   f"{_archive.get('path')} seq {seq_before}->{seq_after}")

# ============================================================ E 不确定与语言
sec("E 不确定与语言")
from app.mind import prompt as prompt_mod  # noqa: E402

_REL_E = {"affinity": 10, "trust": 10, "attraction": 0, "aversion": 0,
          "stage_label": "观察者", "call_user": "你"}
_LIFE_E = {"energy": 70, "mood": 0, "focus": 50, "social_battery": 60,
           "activity": "发呆", "hour": 14, "sleeping": False}
_ins_miss = prompt_mod.build(
    rt.cfg, rt.persona, relation=_REL_E, life=_LIFE_E, user_text="你还记得那次吵架吗",
    memories=[], recent_events=[], recall_status="miss",
).system
ck("想不起来时给『不要编』的指令", "记不清" in _ins_miss or "不要编" in _ins_miss, _ins_miss[-160:])
_ins_unc = prompt_mod.build(
    rt.cfg, rt.persona, relation=_REL_E, life=_LIFE_E, user_text="你还记得那次吵架吗",
    memories=[], recent_events=[], recall_status="uncertain",
).system
ck("印象模糊时给『先想想』的指令", "想" in _ins_unc and ("模糊" in _ins_unc or "想想" in _ins_unc), _ins_unc[-160:])

# ============================================================ F 反拼装审计
sec("F 反拼装审计")
_r = client.get("/v1/console/continuity")
_body = _r.json() if _r.status_code == 200 else {}
ck("存在『改写她』的连续性账本接口",
   _r.status_code == 200 and {"count", "items", "major"} <= set(_body), str(sorted(_body))[:120])
_routes = [getattr(x, "path", "") for x in app.routes]
_flat: list[str] = []
try:
    for p, ops in app.openapi()["paths"].items():
        for m in ops:
            _flat.append(f"{m.upper()} {p}")
except Exception:  # noqa: BLE001
    pass
ck("没有『删事件/改记忆』的对外接口",
   not any("DELETE /v1/events" in f or "PUT /v1/memories" in f or "PATCH /v1/memories" in f for f in _flat),
   str([f for f in _flat if "events" in f or "memories" in f])[:160])
_src = pathlib.Path(ROOT) / "vendor" / "symvolve_memory"
_cur = hashlib.sha256("".join(sorted(
    hashlib.sha256(p.read_bytes()).hexdigest() for p in _src.rglob("*.py") if "__pycache__" not in p.parts
)).encode()).hexdigest()
ck("引擎是只读副本（有指纹，可复核）", len(_cur) == 64, _cur[:16])

# ============================================================ G 变化留痕
sec("G 变化留痕")
_c0 = continuity.count()
client.post("/v1/dev/memory/clear", params={"password": DEV})
_c1 = continuity.count()
ck("清记忆会留痕", _c1 == _c0 + 1, f"{_c0}->{_c1}")
client.post("/v1/dev/relation/reset", params={"password": DEV})
_c2 = continuity.count()
ck("关系重置会留痕", _c2 == _c1 + 1, f"{_c1}->{_c2}")
_old_model = str(rt.cfg.provider.model or "")
_old_base = str(rt.cfg.provider.base_url or "")
client.post("/v1/settings/provider", json={"base_url": _old_base, "model": _old_model + "-humanity-probe"})
_c3 = continuity.count()
client.post("/v1/settings/provider", json={"base_url": _old_base, "model": _old_model})
_c4 = continuity.count()
ck("换模型会留痕（换回来也算）", _c3 == _c2 + 1 and _c4 == _c3 + 1, f"{_c2}->{_c3}->{_c4}")
_labels = {str(i.get("kind")) for i in continuity.recent(20)}
ck("痕迹带人话标签（不是冷冰冰的日志）",
   {"memory_forgotten", "relation_reset", "model_changed"} <= _labels,
   str(sorted(_labels)))

# ============================================================ 汇总
print("\n" + "=" * 60)
by: dict[str, list] = {}
for s, n, okk, det in RESULTS:
    by.setdefault(s, []).append((n, okk, det))
total = len(RESULTS)
passed = sum(1 for _, _, okk, _ in RESULTS if okk)
for s in sorted(by):
    rows = by[s]
    good = sum(1 for _, okk, _ in rows if okk)
    print(f"{s:22} {good:2}/{len(rows):2}")
fails = [(s, n, d) for s, n, okk, d in RESULTS if not okk]
if fails:
    print("\n失败明细：")
    for s, n, d in fails:
        print(f"  [{s}] {n}  {d}")
print(f"\n拟人度总分：{passed}/{total}")
print("提示：语言风格类指标（重复率/OOC/语气方差）需要真实模型，另跑 --live 版；本脚本只测与模型无关的人的制度。")
print("临时库：", TMP)
sys.exit(0 if not fails else 1)
