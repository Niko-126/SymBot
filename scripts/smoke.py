"""冒烟测试：不依赖网络，验证 M0 全链路。

运行：python scripts/smoke.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = pathlib.Path(tempfile.mkdtemp(prefix="ai-life-smoke-"))
os.environ["AI_LIFE_DB"] = str(TMP / "smoke.db")
os.environ["AI_LIFE_DATA"] = str(TMP)
os.environ.pop("AI_LIFE_API_KEY", None)
os.environ["AI_LIFE_MOCK"] = "1"
os.environ["AI_LIFE_IDLE"] = "0"  # 夜间独处由 [45] 显式测，别让主链后台任务干扰计数
# 开发者口令：公开仓库的 config.yaml 留空（等于开发者接口全关），测试用环境变量临时给一个。
os.environ["SYMEM_DEVELOPER_KEY"] = "test-dev-key"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.core.runtime import get as get_runtime  # noqa: E402

ok = 0
fail = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


client = TestClient(app)

print("[1] 健康检查")
r = client.get("/v1/health")
check("health 200", r.status_code == 200, str(r.status_code))
h = r.json()
check("provider=mock", h.get("provider") == "mock", h.get("provider"))
print("       ", json.dumps(h, ensure_ascii=False))

print("[1.5] 命名流程（按需触发）")
client.post("/v1/chat", json={"text": "在吗"})
r = client.post("/v1/chat", json={"text": "给你起个名字吧"}).json()
check("问名字", "叫什么" in " ".join(r.get("reply") or []), str(r.get("reply")))
r = client.post("/v1/chat", json={"text": "小满"}).json()
check("进入待确认", "小满" in " ".join(r.get("reply") or []), str(r.get("reply")))
r = client.post("/v1/chat", json={"text": "确认"}).json()
check("名字锁定", r["state"]["session"]["name_locked"] is True, str(r["state"]["session"]))

print("[2] 首屏状态")
r = client.get("/v1/board")
check("board 200", r.status_code == 200)
b = r.json()
check("persona 有名字", bool(b["persona"]["name"]), str(b["persona"]))
check("relation 含三轴", {"affinity", "trust", "attraction"} <= set(b["relation"]))
check("stage_table 非空", len(b.get("stage_table") or []) >= 3)
print("        persona:", b["persona"]["name"], "| stage:", b["relation"]["stage_label"],
      "| affinity:", b["relation"]["affinity"])

print("[3] 对话")
r = client.post("/v1/chat", json={"text": "在吗"})
check("chat 200", r.status_code == 200, r.text[:200])
first = r.json()
check("reply 是数组", isinstance(first.get("reply"), list) and len(first["reply"]) > 0)
check("state 有阶段", bool(first.get("state", {}).get("stage_label")))
check(
    "state 是全量",
    {"affinity", "trust", "attraction", "next_stage", "call_user"} <= set(first["state"]),
    str(sorted(first["state"])),
)
print("        reply:", first.get("reply"))

r2 = client.post("/v1/chat", json={"text": "今天被骂了，好累，不想说话"})
check("第二轮 200", r2.status_code == 200)
second = r2.json()
check("好感有变化",
      any(abs(float(v)) > 1e-9 for v in second["state"]["delta"].values()
          if isinstance(v, (int, float))))
print("        delta:", second["state"]["delta"], "| affinity:", second["state"]["affinity"])

print("[4] 上下文检查口")
r = client.get("/v1/inspect")
check("inspect 200", r.status_code == 200, str(r.status_code))
ins = r.json()
check("system 非空", len(ins.get("system", "")) > 200)
check("预算有分区", bool(ins.get("budget", {}).get("sections")))
print("        budget:", json.dumps(ins["budget"], ensure_ascii=False))
print("        system 前 120 字:", ins["system"][:120].replace("\n", " / "))

print("[5] TXT 记忆导入")
sample = TMP / "chat.txt"
sample.write_text(
    """[2026-09-01 23:40] 她: 你还没睡。
[2026-09-01 23:41] 我: 睡不着，我叫林岸，我今年27岁
[2026-09-01 23:42] 她: ……嗯。我陪你一会儿。
[2026-09-02 01:10] 我: 我养了一只猫叫年糕，它今天把杯子打碎了
[2026-09-02 01:11] 她: 它没事吧。
[2026-09-02 01:12] 我: 我最近很怕一个人待着
[2026-09-03 02:30] 我: 我不喜欢别人叫我小名，别叫我岸岸
[2026-09-03 02:31] 她: 记住了。
[2026-09-03 02:40] 我: 宝宝，我真的很想你
""",
    encoding="utf-8",
)
r = client.post("/v1/ingest", json={"path": str(sample)})
check("ingest 200", r.status_code == 200, r.text[:300])
rep = r.json()
check("解析出事件", rep.get("events_parsed", 0) >= 8, str(rep.get("events_parsed")))
check("有 warning 字段", "warning" in rep, str(sorted(rep)))
check("写入记忆", rep.get("memories_written", 0) >= 3, str(rep.get("memories_written")))
check("收集原话", rep.get("quotes_collected", 0) >= 2, str(rep.get("quotes_collected")))
print("        format:", rep.get("format_detected"), "| events:", rep.get("events_parsed"),
      "| memories:", rep.get("memories_written"), "| quotes:", rep.get("quotes_collected"))
print("        cold_start:", json.dumps(rep.get("cold_start"), ensure_ascii=False))
print("        samples:", rep.get("samples"))

print("[6] 导入后的记忆与关系")
r = client.get("/v1/memories", params={"limit": 100})
check("memories 200", r.status_code == 200)
items = r.json()["items"]
check("有雷点记忆", any(i["kind"] == "boundary" for i in items), str([i["kind"] for i in items]))
check("有事实记忆", any(i["kind"] == "fact" for i in items))
b2 = client.get("/v1/board").json()
check("冷启动抬高了好感", b2["relation"]["affinity"] > b["relation"]["affinity"],
      f"{b2['relation']['affinity']} vs {b['relation']['affinity']}")
check("board 带历史", len(b2.get("history") or []) >= 2, str(len(b2.get("history") or [])))
print("        记忆条数:", b2["memories"]["total"], "| 分类:", b2["memories"]["by_kind"])
print("        affinity:", b2["relation"]["affinity"], "| stage:", b2["relation"]["stage_label"])

print("[7] 记忆是否真的被用上")
r = client.post("/v1/chat", json={"text": "年糕今天又闯祸了"})
used = r.json()["meta"]["memories_used"]
check("召回命中年糕", any("年糕" in m["content"] for m in used), str([m["content"] for m in used]))
print("        used:", [m["content"] for m in used])

print("[8] OpenAI 兼容接口")
r = client.post(
    "/v1/chat/completions",
    json={"messages": [{"role": "user", "content": "你在干嘛"}], "model": "deepseek-chat"},
)
check("compat 200", r.status_code == 200, r.text[:200])
c = r.json()
check("compat 结构正确", c["choices"][0]["message"]["role"] == "assistant" and c["usage"]["total_tokens"] > 0)
print("        content:", c["choices"][0]["message"]["content"][:60])
r = client.get("/v1/models")
check("models 200", r.status_code == 200)

print("[9] 遗忘")
mid = items[0]["id"]
r = client.delete(f"/v1/memories/{mid}")
check("delete 200", r.status_code == 200)


print("[10] 参考语料隔离（不碰用户记忆与好感）")
ref = TMP / "part99_fake.txt"
ref.write_text(
    "# 微信聊天记录 · 分卷（AI 可读）\n"
    "# 会话：wxid_abc　对方昵称：小蓝　导出账号：wxid_me\n"
    "# ---\n"
    "2024-01-01 12:00:00 小蓝\n你好呀\n\n"
    "2024-01-01 12:01:00 wxid_me\n你也好\n\n"
    "2024-01-01 12:02:00 小蓝\nmua小蓝 宝宝\n\n"
    "2024-01-01 12:03:00 小蓝\n今天有点累\n",
    encoding="utf-8",
)
before = client.get("/v1/board").json()
r = client.post("/v1/reference/ingest", json={"path": str(ref)})
check("reference 200", r.status_code == 200, r.text[:200])
rr = r.json()
check("写入参考轮次", rr.get("status", {}).get("turns", 0) >= 3, str(rr.get("status")))
after = client.get("/v1/board").json()
check("用户记忆未被污染", after["memories"]["total"] == before["memories"]["total"],
      f"{after['memories']['total']} vs {before['memories']['total']}")
check("好感未被冷启动", abs(after["relation"]["affinity"] - before["relation"]["affinity"]) < 0.01,
      f"{after['relation']['affinity']} vs {before['relation']['affinity']}")
st = client.get("/v1/reference/status").json()
check("参考原话已去敏", all("小蓝" not in q for q in st.get("quotes_sample", [])), str(st.get("quotes_sample"))[:120])
print("        参考语料:", st.get("turns"), "轮 /", st.get("quotes"), "原话 |", st.get("periods"))

print("[11] 开发者面板：看 prompt / 单步 / 导出日志")
_dev_key = str(get_runtime().cfg.developer.key)
r = client.get("/v1/dev/last", params={"password": "__wrong__"})
check("dev 密码不对被拒", r.status_code == 403, str(r.status_code))
r = client.get("/v1/dev/last", params={"password": _dev_key})
check("dev last 200", r.status_code == 200, r.text[:200])
last = r.json()
check("dev last 带完整 prompt", bool(last.get("has_prompt")) and bool((last.get("prompt") or {}).get("system")))
check("dev last 带单步 meta", bool((last.get("meta") or {}).get("model")), str(last.get("meta"))[:120])
r = client.get("/v1/dev/logs/export", params={"password": _dev_key})
check("dev 日志导出 200", r.status_code == 200, r.text[:200])
log = r.json()
log_path = pathlib.Path(log.get("path") or "")
check("dev 日志文件已落盘", log_path.exists() and int(log.get("events") or 0) > 0, str(log))
if log_path.exists():
    logged = json.loads(log_path.read_text(encoding="utf-8"))
    check("dev 日志含 prompt/meta/relation", bool(logged.get("prompt")) and bool(logged.get("meta")) and bool(logged.get("relation")))
    log_path.unlink()
print("        导出:", log.get("path"), "|", log.get("bytes"), "B")

print("[12] M5 设置面板接口")
r = client.get("/v1/persona")
check("persona 200", r.status_code == 200, r.text[:200])
pp = r.json()
check("persona 带 traits/quotes/stage_table",
      bool(pp.get("traits")) and bool(pp.get("quotes")) and len(pp.get("stage_table") or []) >= 3,
      str(list(pp)))
dim = pp["traits"][0]["dim"]
top = pp["traits"][0]["max"]
r = client.post("/v1/persona/traits", json={"dim": dim, "value": 9999})
check("traits 写回并夹到上限",
      r.status_code == 200 and r.json()["traits"][0]["value"] == top, str(r.json())[:160])
r = client.post("/v1/persona/traits", json={"dim": "__nope__", "value": 1})
check("traits 未知维度 404", r.status_code == 404, str(r.status_code))
r = client.get("/v1/schedule")
check("schedule 200", r.status_code == 200, r.text[:200])
sch = r.json()
check("schedule 含 now/table/proactive",
      bool(sch.get("now")) and bool(sch.get("table")) and "enabled" in (sch.get("proactive") or {}))
r = client.post("/v1/schedule", json={"proactive": {"enabled": True, "cooldown_min": 5, "daily_max": 2}})
check("schedule 写回生效",
      r.status_code == 200 and r.json()["schedule"]["proactive"]["cooldown_min"] == 5,
      str(r.json())[:160])
r = client.get("/v1/settings/models")
check("models 200", r.status_code == 200, r.text[:200])
mm = r.json()
check("models 含 provider/styles/tasks",
      "temperature" in (mm.get("provider") or {}) and bool(mm.get("styles")) and bool(mm.get("tasks")))
from app.core.runtime import get as _rt_now  # noqa: E402
_old_temp = _rt_now().cfg.provider.temperature
_old_max = _rt_now().cfg.provider.max_tokens
r = client.post("/v1/settings/models", json={"temperature": 0.7, "max_tokens": 300})
check("models 写回生效",
      r.status_code == 200 and abs(r.json()["provider"]["temperature"] - 0.7) < 1e-9,
      str(r.json())[:160])
# 测试只改运行时：还原，别把全局上限/温度带偏
_rt_now().cfg.provider.temperature = _old_temp
_rt_now().cfg.provider.max_tokens = _old_max
r = client.post("/v1/soul/snapshots")
check("快照新建 200",
      r.status_code == 200 and r.json().get("name", "").endswith(".life.json"), r.text[:200])
snap = r.json().get("name", "")
r = client.get("/v1/soul/snapshots")
check("快照列表含新快照", r.status_code == 200 and any(i["name"] == snap for i in r.json()["items"]))
r = client.post("/v1/soul/snapshots/restore", json={"name": snap})
check("快照恢复 200", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
r = client.post("/v1/soul/snapshots/restore", json={"name": "../escape"})
check("快照名越权 400", r.status_code == 400, str(r.status_code))
r = client.post("/v1/soul/export")
check("灵魂导出 200", r.status_code == 200 and pathlib.Path(r.json()["path"]).exists(), r.text[:200])
data = pathlib.Path(r.json()["path"]).read_text(encoding="utf-8")
r = client.post("/v1/soul/import", content=data.encode("utf-8"),
                headers={"Content-Type": "application/json"})
check("灵魂导入 200", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
r = client.post("/v1/soul/import", content=b"nope", headers={"Content-Type": "application/json"})
check("灵魂导入坏 JSON 400", r.status_code == 400, str(r.status_code))

print("[13] M4 她的一天（日记 → 次日引用）")
from datetime import datetime as _dt, timedelta as _td  # noqa: E402

from app.core import db as _db  # noqa: E402

_yesterday = (_dt.now().date() - _td(days=1)).isoformat()
_today = _dt.now().date().isoformat()
for _role, _text in (("user", "昨天加班到十一点"), ("her", "那你早点睡。")):
    _db.execute(
        "INSERT INTO events (ts, role, kind, text, source, meta) VALUES (?, ?, 'text', ?, 'panel', '')",
        (f"{_yesterday}T23:10:00+08:00", _role, _text),
    )
r = client.get("/v1/diary")
check("diary 200", r.status_code == 200, r.text[:200])
check("diary 结构完整", {"today", "last_written", "has_yesterday", "items"} <= set(r.json()))
r = client.post("/v1/diary/run", json={"day": _yesterday})
check("日记补写 200", r.status_code == 200 and (r.json().get("entry") or {}).get("day") == _yesterday,
      r.text[:200])
r = client.post("/v1/diary/run", json={"day": "2099-01-01"})
check("日记不许写未来 400", r.status_code == 400, str(r.status_code))
r = client.post("/v1/diary/run", json={"day": "not-a-date"})
check("日记坏日期 400", r.status_code == 400, str(r.status_code))
_db.kv_set("diary_ref_date", "")
r = client.post("/v1/chat", json={"text": "今天还是好累"})
check("次日引用命中", bool((r.json().get("meta") or {}).get("diary")), str((r.json().get("meta") or {}).get("diary")))
check("prompt 含昨天日记", "你昨天的日记" in client.get("/v1/inspect").json().get("system", ""))
client.post("/v1/chat", json={"text": "嗯"})
check("同一天不重复引用", "你昨天的日记" not in client.get("/v1/inspect").json().get("system", ""))
_db.kv_set("diary_last_date", "")
client.post("/v1/chat", json={"text": "晚安"})
check("跨天自动补写", _db.kv_get("diary_last_date", "") == _today and bool(_db.kv_get(f"diary:{_yesterday}", "")))

print("[14] 鲸鱼娘人设包（qq-group）")
from app.persona.load import load as _load_persona  # noqa: E402

_wp = ROOT / "persona" / "whale-chan.yaml"
check("鲸鱼娘人设存在", _wp.exists(), str(_wp))
_whale = _load_persona(_wp)
check(
    "鲸鱼娘人设字段完整",
    bool(_whale.name) and len(_whale.traits()) >= 6 and len(_whale.all_quotes()) >= 10
    and len(_whale.stages()) >= 3 and len(_whale.schedule()) >= 5,
    str({"name": _whale.name, "traits": len(_whale.traits()), "quotes": len(_whale.all_quotes())}),
)
check("鲸鱼娘锚点可按场景取", len(_whale.anchors(per_scene=2)) >= 4)

print("[15] QQ 群多用户表（建表 + 恋人绑定基础）")
from app.relation import users as _users  # noqa: E402

_tables = {r["name"] for r in _db.rows("SELECT name FROM sqlite_master WHERE type = 'table'")}
check("三张表已建立", {"qq_user", "user_relation", "lover"} <= _tables, str(sorted(_tables)))
_u1 = _users.ensure_user("smoke-openid-1", "阿岸")
check("用户可创建且幂等", _users.ensure_user("smoke-openid-1")["id"] == _u1["id"])
_rel = _users.relation_of(_u1["id"])
check("默认关系为观察者", _rel["affinity"] == 0 and _rel["stage"] == "observer", str(_rel))
_rel = _users.update_relation(_u1["id"], affinity=100)
check("好感 100 记录资格时间", _rel["affinity"] == 100 and bool(_rel["hit_100_at"]), str(_rel))
_u2 = _users.ensure_user("smoke-openid-2", "别人")
_users.update_relation(_u2["id"], affinity=100)
_lover = _users.bind_lover(1, _u1["id"])
check("先到者绑定唯一恋人", _lover["user_id"] == _u1["id"], str(_lover))
_blocked = False
try:
    _users.bind_lover(1, _u2["id"])
except ValueError:
    _blocked = True
check("同时只能一个恋人", _blocked)
_ended = _users.breakup(1, reason="smoke", cooldown_days=7)
check("分手释放恋人位", bool(_ended) and _users.current_lover(1) is None, str(_ended))
_cd = _users.cooldown_state(_u1["id"])
check("分手方冷却生效", _cd["active"] and 0 < _cd["days_left"] <= 7, str(_cd))
_ok, _reason, _days = _users.can_confess(_u1["id"])
check("冷却期内不能立刻复合", (not _ok) and _days > 0, f"{_ok} {_reason} {_days}")
_past = (_dt.now().astimezone() - _td(days=1)).isoformat(timespec="seconds")
_db.execute("UPDATE lover SET cooldown_until = ? WHERE user_id = ?", (_past, _u1["id"]))
check("冷却到期可再考虑", _users.can_confess(_u1["id"])[0])
_lover2 = _users.bind_lover(1, _u2["id"])
check("分手后别人可成为恋人", _lover2["user_id"] == _u2["id"], str(_lover2))

print("[16] 群聊进化：人类观察 / 每人看法 / 周期报告 / 记忆自评")
from app.memory import observe as _obs  # noqa: E402

check(
    "进化三表已建立 + memories.user_id",
    {"human_notes", "member_impression", "reflect_report"} <= _tables
    and "user_id" in {r["name"] for r in _db.rows("PRAGMA table_info(memories)")},
    str(sorted(_tables)),
)
_nid = _obs.add_note("说话方式", "群里的人喜欢先发一句短的，再补一句解释", confidence=0.7, user_id=_u1["id"])
check("人类观察可写入", _nid > 0 and any(x["id"] == _nid for x in _obs.recent_notes(7, 50)), str(_nid))
_imp = _obs.upsert_impression(_u1["id"], "他说话直接，爱开玩笑，夜里话最多", traits_seen=["直接", "幽默"], habits=["夜聊"])
check("每人看法可写入", _imp and _imp["summary"].startswith("他说话") and "直接" in _imp["traits_seen"], str(_imp))
_imp2 = _obs.upsert_impression(_u1["id"], "他最近压力大，会先岔开话题", traits_seen=["直接"], habits=["避开压力"])
check("看法可累积版本", _imp2["version"] == _imp["version"] + 1 and len(_imp2["habits"]) >= 2, str(_imp2))
_rid = _obs.add_report(_obs.REPORT_MEMORY_EVAL, "2026-10-01", "2026-10-07", "优点：钉住记忆没丢。不足：冷记忆没合并。",
                       {"总记忆": 3, "未触碰占比": 0.3})
check("周期报告可写入", _rid > 0 and (_obs.latest_report(_obs.REPORT_MEMORY_EVAL) or {}).get("id") == _rid)
_metrics = _obs.memory_metrics()
check("记忆指标可计算", {"总记忆", "按类型", "钉住占比", "未触碰占比"} <= set(_metrics), str(_metrics)[:160])
_last_start, _last_end, _this_monday = _obs.eval_period()
check(
    "自评周期按周一起算",
    _dt.fromisoformat(_last_start).weekday() == 0
    and (_dt.fromisoformat(_last_start) + _td(days=6)).date().isoformat() == _last_end,
    f"{_last_start} ~ {_last_end}",
)
check("旧周报告不满足本周", _obs.eval_due() is True)
_obs.add_report(_obs.REPORT_MEMORY_EVAL, _last_start, _last_end, "优点：稳。不足：冷。", {"总记忆": 1})
check("上周报告覆盖后不再到期", _obs.eval_due() is False)
_db.kv_set(_obs.KEY_THINK_DAY, "")
check("思考报告按天去重", _obs.think_due() is True)
_obs.mark_think_done()
check("思考报告当天不再重复", _obs.think_due() is False)

print("[17] 群聊进化生成层（每日思考报告 + 每周记忆自评）")
import asyncio  # noqa: E402

from app.core.runtime import get as _rt_get  # noqa: E402
from app.mind import loop as _loop_mod  # noqa: E402
from app.memory import evolve as _evolve  # noqa: E402

_rt = _rt_get()
_db.execute("DELETE FROM reflect_report WHERE kind = ?", (_obs.REPORT_MEMORY_EVAL,))
_db.execute("DELETE FROM reflect_report WHERE kind = ?", (_obs.REPORT_THINK,))
_db.kv_set(_obs.KEY_THINK_DAY, "")
_ev = asyncio.run(_evolve.evolve_tick(_rt, _rt.persona))
check("进化生成跑通", isinstance(_ev, dict) and _ev.get("think") is True, str(_ev))
check("思考报告已生成", bool((_obs.latest_report(_obs.REPORT_THINK) or {}).get("content")))
check("人类观察步骤已执行", isinstance(_ev.get("notes"), int))
_me = _obs.latest_report(_obs.REPORT_MEMORY_EVAL) or {}
check("周自评已生成且周期正确", _me.get("period_start") == _last_start and _me.get("period_end") == _last_end,
      str(_me.get("period_end")))
check("自评带指标", _me.get("metrics", {}).get("总记忆") is not None, str(_me.get("metrics"))[:120])
check("自评后本周不再重复", _obs.eval_due() is False)

print("[18] 衡忆按用户隔离 + 关系按用户结算")
from app.memory import store as _store  # noqa: E402
from app.memory import h2 as _sym  # noqa: E402
from app.relation import engine as _engine  # noqa: E402

_u3 = _users.ensure_user("smoke-openid-3", "第三人")
_u1_id = int(_u1["id"])
_u3_id = int(_u3["id"])
_mid = _sym.add("fact", "阿岸今天吃了超辣火锅", importance=0.8, user_id=_u1_id)
check("用户记忆可写入归属",
      _mid > 0 and int(_db.one("SELECT user_id FROM memories WHERE id = ?", (_mid,))["user_id"]) == _u1_id)
_r1 = _sym.retrieve("超辣火锅", user_id=_u1_id)
check("本人能召回自己的记忆", any(int(m["id"]) == _mid for m in (_r1.hot + _r1.recalled)))
_r3 = _sym.retrieve("超辣火锅", user_id=_u3_id)
check("别人的记忆不会被召回", all(int(m["id"]) != _mid for m in (_r3.hot + _r3.recalled)))
_evid = _store.add_event("user", "群里的测试消息", user_id=_u3_id)
check("事件可归属用户",
      int(_db.one("SELECT user_id FROM events WHERE id = ?", (_evid,))["user_id"]) == _u3_id)
_global_before = float(_engine.get_relation()["affinity"])
_engine.apply_appraisal({"intensity": 0.8, "tags": ["warm"], "valence": "positive"}, _rt.persona, user_id=_u1_id)
_after_user = _users.relation_of(_u1_id)
check("按用户结算不碰全局关系",
      abs(float(_engine.get_relation()["affinity"]) - _global_before) < 1e-9
      and _after_user["updated_at"] != "" and float(_after_user["affinity"]) > 0.0,
      f"user={_after_user['affinity']} global={_engine.get_relation()['affinity']}")
_r = client.post("/v1/chat", json={"text": "我是第三人", "user_id": _u3_id})
check("聊天接口带 user_id",
      _r.status_code == 200 and (_r.json().get("meta") or {}).get("user_id") == _u3_id,
      str(_r.json().get("meta", {}).get("user_id")))

print("[19] 恋爱机制接线（表白 / 唯一性 / 分手 / 冷却话术 / 主动提议）")
_db.execute("DELETE FROM lover")
_u4 = _users.ensure_user("smoke-love-1", "恋爱甲")
_users.update_relation(int(_u4["id"]), affinity=100)
_j = client.post("/v1/chat", json={"text": "我喜欢你，我们在一起吧", "user_id": int(_u4["id"])}).json()
check("表白成功绑定",
      (_j.get("meta") or {}).get("love") == "bound" and _users.lover_of(int(_u4["id"])) is not None,
      str(_j.get("meta")))
_u5 = _users.ensure_user("smoke-love-2", "恋爱乙")
_users.update_relation(int(_u5["id"]), affinity=100)
_j2 = client.post("/v1/chat", json={"text": "我也喜欢你，在一起吧", "user_id": int(_u5["id"])}).json()
check("已有恋人被拒", (_j2.get("meta") or {}).get("love") == "rejected_taken", str(_j2.get("meta")))
_j3 = client.post("/v1/chat", json={"text": "我们分手吧", "user_id": int(_u4["id"])}).json()
check("分手释放恋人位",
      (_j3.get("meta") or {}).get("love") == "breakup" and _users.current_lover(1) is None,
      str(_j3.get("meta")))
_j4 = client.post("/v1/chat", json={"text": "我还是喜欢你，想做你男朋友", "user_id": int(_u4["id"])}).json()
check("冷却期拒绝并说天数",
      (_j4.get("meta") or {}).get("love") == "rejected_cooldown"
      and int((_j4.get("meta") or {}).get("days_left", 0)) > 0
      and any("天" in str(x) for x in (_j4.get("reply") or [])),
      str(_j4.get("reply")))
_u6 = _users.ensure_user("smoke-love-3", "恋爱丙")
_users.update_relation(int(_u6["id"]), affinity=100)
# 深夜第一条可能不回（她现在按"这个人叫了几次"判断），叫两次再等她挂提议
client.post("/v1/chat", json={"text": "在吗", "user_id": int(_u6["id"])})
client.post("/v1/chat", json={"text": "今天天气不错", "user_id": int(_u6["id"])})
check("到 100% 挂主动提议", _db.kv_get(f"love_pending:{int(_u6['id'])}", "") != "")
_j5 = client.post("/v1/chat", json={"text": "好", "user_id": int(_u6["id"])}).json()
check("答应后绑定",
      (_j5.get("meta") or {}).get("love") == "bound_active"
      and _users.lover_of(int(_u6["id"])) is not None, str(_j5.get("meta")))
_j6 = client.post("/v1/chat", json={"text": "你吃饭了吗", "user_id": int(_u6["id"])}).json()
check("普通聊天不被拦",
      (_j6.get("meta") or {}).get("love") is None and bool(_j6.get("reply")), str(_j6.get("meta")))

print("[20] OneBot v11 小号接入（事件 → 对话 → 回发/兜底）")
_ob = _rt_get().cfg.onebot
_ob.enabled = True
_ob.api_base = "http://127.0.0.1:9"  # 故意不可达：验证 outbox 兜底
_ob.access_token = ""
_ob.self_id = "999999"
_ob.group_at_only = True
_ob.sticker_chance = 1.0  # 强制带表情包，验证图片段
# 先清掉本进程 DATA_ROOT 下的旧 outbox：不然上一轮残留也能让"兜底"断言通过
_OBOX_CLEAR = __import__("app.core.paths", fromlist=["DATA_ROOT"]).DATA_ROOT / "data" / "onebot_outbox.jsonl"
_OBOX_CLEAR.unlink(missing_ok=True)
_priv = client.post("/onebot/event", json={
    "post_type": "message", "message_type": "private", "self_id": 999999, "user_id": 424242,
    "message": [{"type": "text", "data": {"text": "你好，我是小号测试"}}],
    "sender": {"nickname": "测试甲"},
})
check("私聊事件被处理", _priv.status_code == 200 and _priv.json().get("replied") is True, _priv.text[:160])
_qq_user = _users.get_user_by_openid("424242")
check("OneBot 用户已建号", _qq_user is not None and str(_qq_user["openid"]) == "424242", str(_qq_user))
_outbox = ROOT / "data" / "onebot_outbox.jsonl"
_alt_outbox = __import__("app.core.paths", fromlist=["DATA_ROOT"]).DATA_ROOT / "data" / "onebot_outbox.jsonl"
_used = _alt_outbox if _alt_outbox.exists() else _outbox
check("发不出去写 outbox 兜底", _used.exists() and _used.stat().st_size > 0, str(_used))
_last_line = _used.read_text(encoding="utf-8").strip().splitlines()[-1] if _used.exists() else "{}"
try:
    _last_payload = json.loads(_last_line)
except json.JSONDecodeError:
    _last_payload = {}
_segs = (_last_payload.get("payload") or {}).get("message") or []
# 表情库是否可用决定这一轮该不该有图片段：公开版不带表情素材，空库时必须纯文本。
# 注意：`app/memory/stickers.py` 的查找顺序是 DATA_ROOT → RESOURCE_ROOT，
# 测试里 DATA_ROOT 是临时目录（永远为空），真正的库在仓库 data/stickers。
_paths = __import__("app.core.paths", fromlist=["DATA_ROOT", "RESOURCE_ROOT"])
_sticker_roots = [
    _paths.DATA_ROOT / "data" / "stickers",
    _paths.RESOURCE_ROOT / "data" / "stickers",
]
_has_stickers = any(
    r.exists() and any(p.is_file() for p in r.rglob("*")) for r in _sticker_roots
)
if _has_stickers:
    check("回复可带表情包图片段", any(s.get("type") == "image" for s in _segs), str(_segs)[:160])
else:
    check("表情库为空时不硬塞图片段", not any(s.get("type") == "image" for s in _segs), str(_segs)[:160])
_grp_noat = client.post("/onebot/event", json={
    "post_type": "message", "message_type": "group", "self_id": 999999, "user_id": 424242,
    "group_id": 123456, "message": [{"type": "text", "data": {"text": "大家好"}}],
    "sender": {"nickname": "测试甲"},
}).json()
# 新行为：没 @ 也没提名字时，看上下文是否在跟她说话；没有上下文就不回
check("群聊无 @ 无名字按语境判断",
      (_grp_noat.get("replied") is True and "sent" in _grp_noat)
      or _grp_noat.get("ignored") is True, str(_grp_noat))

print("[22] 群聊增强：静默 / 昵称 / 语境 / 主动配额")
from app.group import context as _gctx  # noqa: E402
from app.group import proactive as _gpro  # noqa: E402

check("静默指令能识别", all(_gpro.detect_stop(t) for t in ("别发了", "闭嘴", "停一下")))
check("正常话不触发静默", not _gpro.detect_stop("今天天气不错"))
_gpro.unsilence()
_gpro.silence(30, "smoke")
check("静默生效", _gpro.is_silenced()[0] is True)
_gpro.unsilence()
check("解除静默", _gpro.is_silenced()[0] is False)
_gctx.reset_nick(1)
_n = _gctx.suggest_nick(1)
check("能给她起昵称", bool(_n) and _gctx.nick_of(1) == _n, _n)
check("用户可指定昵称", _gctx.handle_nick_change("以后叫我小张", 1)["nick"] == "小张")
check("别叫我X 触发重新取名",
      (_gctx.handle_nick_change("别叫我话痨", 1) or {}).get("need_name") is True)
check("普通话不改昵称", _gctx.handle_nick_change("今天吃啥", 1) is None)
check("待机状态可读", "silenced" in _gctx.standby_state())

# 无回应就停：她连发 2 条没人接 → 该闭嘴
_db.execute("DELETE FROM events WHERE source = 'streak-test'")
for _r in ("user", "her", "her"):
    _db.execute(
        "INSERT INTO events (ts, role, kind, text, source, meta, user_id)"
        " VALUES (datetime('now','localtime'), ?, 'text', 'x', 'streak-test', '', 0)",
        (_r,),
    )
check("连发 2 条没人接就停", _gpro.should_be_quiet()[0] is True, str(_gpro.unanswered_streak()))
_db.execute("DELETE FROM events WHERE source = 'streak-test'")
check("用户回应后恢复", _gpro.should_be_quiet()[0] is False)
_grp_name = client.post("/onebot/event", json={
    "post_type": "message", "message_type": "group", "self_id": 999999, "user_id": 424242,
    "group_id": 123456, "message": [{"type": "text", "data": {"text": "小鲸鱼在吗"}}],
    "sender": {"nickname": "测试甲"},
}).json()
check("群里提到名字也会回", _grp_name.get("replied") is True, str(_grp_name))
_grp_at = client.post("/onebot/event", json={
    "post_type": "message", "message_type": "group", "self_id": 999999, "user_id": 424242,
    "group_id": 123456,
    "message": [{"type": "at", "data": {"qq": "999999"}}, {"type": "text", "data": {"text": " 在吗"}}],
    "sender": {"nickname": "测试甲"},
}).json()
check("群里 @ 她 → 回复", _grp_at.get("replied") is True and _grp_at.get("message_type") == "group", str(_grp_at))
_ob.enabled = False
_off = client.post("/onebot/event", json={"post_type": "message", "message_type": "private", "user_id": 1}).json()
check("关闭开关后忽略事件", _off.get("ignored") is True and _off.get("reason") == "onebot disabled", str(_off))

print("[21] 每日上限默认放开 + 按用户记账")
_pos_cap, _neg_cap, _caps_on = _engine.caps_limits()
check("默认不限（上限关闭）", _caps_on is False, f"on={_caps_on}")
_u7 = _users.ensure_user("smoke-cap-1", "额度甲")
_users.update_relation(int(_u7["id"]), affinity=0)
_engine.apply_appraisal({"intensity": 0.8, "tags": ["care"], "valence": "positive"}, _rt.persona, user_id=int(_u7["id"]))
_rel7 = _users.relation_of(int(_u7["id"]))
check("不设上限时全额入账", float(_rel7["affinity"]) > 0, str(_rel7["affinity"]))
check("额度按用户分开记账",
      _engine._cap_key(int(_u7["id"])) != _engine._cap_key(0)
      and _db.kv_get(_engine._cap_key(int(_u7["id"])), "") != "")

print("[22] 联网查词：识别 / 黑话表 / 可信度门槛")
from app.web import lookup as _lookup  # noqa: E402

check("能识别要查的句式",
      _lookup.needs_lookup("豆姐是什么梗") == "豆姐"
      and _lookup.needs_lookup("什么是豆包") == "豆包")
check("普通话不触发查询",
      _lookup.needs_lookup("今天天气不错") is None and _lookup.needs_lookup("你吃饭了吗") is None)
check("黑话表命中", "豆包" in _lookup.lookup("豆姐")["summary"], _lookup.lookup("豆姐")["source"])
_hi, _r1 = _lookup.assess_confidence(
    "海螺",
    "海螺AI是MiniMax旗下的AI助手；提供对话与语音合成",
    [
        {"title": "海螺AI - MiniMax 出品", "snippet": "AI 助手"},
        {"title": "海螺AI 官网", "snippet": "MiniMax 产品"},
        {"title": "海螺AI 是什么", "snippet": "海螺AI 介绍"},
    ],
    True,
)
check("可信的会学", _hi >= _lookup.LEARN_MIN_CONFIDENCE, f"{_hi:.2f}")
_lo, _r2 = _lookup.assess_confidence(
    "豆姐",
    "豆姐 Eva 的微博；网红圈红豆；主播直播带货",
    [
        {"title": "豆姐 Eva 的微博", "snippet": "网红"},
        {"title": "主播豆姐带货", "snippet": "直播"},
        {"title": "豆姐是谁", "snippet": "微博"},
    ],
    False,
)
check("娱乐八卦不学", _lo < _lookup.LEARN_MIN_CONFIDENCE, f"{_lo:.2f}")

print("[23] 离线兜底：模型失败时说人话（去吃饭）")
from app.mind.loop import _offline_reply as _off_reply  # noqa: E402

_r = _off_reply(_rt.persona, _rt)
check("兜底话术是拟人的", bool(_r) and ("吃" in _r or "饿" in _r), _r)
check("兜底不会报错文案",
      "错误" not in _r and "失败" not in _r and "500" not in _r, _r)
_orig_key = _rt.cfg.provider.api_key
_orig_llm = _rt.llm


class _BoomLLM:
    """模拟 API 限额/超时：一调用就抛。"""

    provider = "openai"

    async def chat(self, *a, **kw):
        raise RuntimeError("429 Too Many Requests")


_rt.cfg.provider.api_key = "sk-invalid"
_rt.llm = _BoomLLM()
try:
    _off = asyncio.run(_loop_mod.chat_turn(_rt, "在吗测试", source="smoke", user_id=0))
    check("模型失败走离线兜底",
          (_off.get("meta") or {}).get("mode") == "offline"
          and bool(_off.get("reply")), str(_off.get("meta"))[:100])
    check("离线回复是拟人话术",
          any(k in str(_off.get("reply")) for k in ("吃", "饿")), str(_off.get("reply")))
except Exception as exc:  # noqa: BLE001
    check("模型失败走离线兜底", False, f"抛异常了：{exc}")
finally:
    _rt.cfg.provider.api_key = _orig_key
    _rt.llm = _orig_llm

print("[24] 静默规则：30 分钟 + @ 可打破 + 深度思考默认开")
from app.mind import models as _models  # noqa: E402

check("静默时长 30 分钟", _gpro.STOP_MINUTES == 30, str(_gpro.STOP_MINUTES))
check("静默指令识别", _gpro.detect_stop("别说了") and _gpro.detect_stop("别回"))
_gpro.unsilence()
_gpro.silence(30, "smoke")
check("静默生效", _gpro.is_silenced()[0] is True)
_broke = _gpro.break_silence(by="@")
check("@ 能打破静默", _broke is True and _gpro.is_silenced()[0] is False)
_db.execute("DELETE FROM kv WHERE key = ?", ("deep_think",))
check("深度思考默认开启", _models.current_settings()["deep_think"] is True)
_models.save_settings(deep_think=False)
check("深度思考可关闭", _models.current_settings()["deep_think"] is False)
_models.save_settings(deep_think=True)
_gpro.unsilence()

print("[25] 私聊 / 群聊分流（事件带来源、记忆按场合隔离、语气分开）")
from app.memory import h2 as _sym  # noqa: E402

_ob.enabled = True
_ob.self_id = "999999"
_ob.sticker_chance = 0.0
_ob.voice_chance = 0.0
_priv_user = str(_qq_user["openid"])
_uid = int(_qq_user["id"])
_ev_private = client.post("/onebot/event", json={
    "post_type": "message", "message_type": "private", "self_id": 999999,
    "user_id": int(_priv_user),
    "message": [{"type": "text", "data": {"text": "这是私聊里说的话"}}],
    "sender": {"nickname": "测试甲"},
}).json()
check("私聊事件照常回", _ev_private.get("replied") is True, str(_ev_private)[:120])
_priv_row = _db.one(
    "SELECT chat_type, group_id FROM events WHERE role = 'user' AND text = ?"
    " ORDER BY id DESC LIMIT 1", ("这是私聊里说的话",))
check("私聊事件标了 private",
      _priv_row is not None and _priv_row["chat_type"] == "private", str(dict(_priv_row) if _priv_row else None))
check("私聊事件没有 group_id",
      _priv_row is not None and _priv_row["group_id"] == "", str(dict(_priv_row) if _priv_row else None))

_ev_group = client.post("/onebot/event", json={
    "post_type": "message", "message_type": "group", "self_id": 999999,
    "user_id": int(_priv_user), "group_id": 654321,
    "message": [{"type": "at", "data": {"qq": "999999"}}, {"type": "text", "data": {"text": " 这是群里说的话"}}],
    "sender": {"nickname": "测试甲"},
}).json()
check("群聊事件照常回", _ev_group.get("replied") is True, str(_ev_group)[:120])
_grp_row = _db.one(
    "SELECT chat_type, group_id FROM events WHERE role = 'user' AND text = ?"
    " ORDER BY id DESC LIMIT 1", ("这是群里说的话",))
check("群聊事件标了 group",
      _grp_row is not None and _grp_row["chat_type"] == "group", str(dict(_grp_row) if _grp_row else None))
check("群聊事件带 group_id",
      _grp_row is not None and str(_grp_row["group_id"]) == "654321",
      str(dict(_grp_row) if _grp_row else None))

_chat_priv = client.get(f"/v1/console/chat/{_uid}", params={"chat_type": "private"}).json()
check("控制台能只看私聊",
      all(m["chat_type"] in ("private", "") for m in _chat_priv.get("messages") or [])
      and any("私聊" in m["text"] for m in _chat_priv.get("messages") or []),
      str([m["chat_type"] for m in _chat_priv.get("messages") or []])[:120])
check("控制台私聊视图不含群聊原话",
      not any("群里说的话" in m["text"] for m in _chat_priv.get("messages") or []),
      str([m["text"][:10] for m in _chat_priv.get("messages") or []])[:160])
_chat_grp = client.get(f"/v1/console/chat/{_uid}", params={"chat_type": "group"}).json()
check("控制台能只看群聊",
      any("群里说的话" in m["text"] for m in _chat_grp.get("messages") or []),
      str([m["text"][:10] for m in _chat_grp.get("messages") or []])[:160])
check("聊天接口带场合统计",
      set(_chat_priv.get("counts") or {}) & {"private", "group", "unknown"} != set(),
      str(_chat_priv.get("counts")))

_sym.add("fact", "他在私聊里说过自己养了只猫", importance=0.9, user_id=_uid, chat_type="private")
_sym.add("fact", "群里有人说自己开货车", importance=0.9, user_id=_uid, chat_type="group")
_in_priv = _sym.retrieve("养猫 货车 开货车", hot_limit=200, user_id=_uid, chat_type="private")
_in_grp = _sym.retrieve("养猫 货车 开货车", hot_limit=200, user_id=_uid, chat_type="group")
_priv_texts = " ".join(m["content"] for m in _in_priv.hot + _in_priv.recalled)
_grp_texts = " ".join(m["content"] for m in _in_grp.hot + _in_grp.recalled)
check("私聊召回只带私聊记忆", "养了只猫" in _priv_texts and "开货车" not in _priv_texts, _priv_texts[:120])
check("群聊召回不带私聊记忆", "开货车" in _grp_texts and "养了只猫" not in _grp_texts, _grp_texts[:120])
_legacy = _sym.add("fact", "这是不分场合的旧记忆", importance=0.9, user_id=_uid)
check("不分场合的记忆两边都能召回",
      "旧记忆" in " ".join(
          m["content"] for m in _sym.retrieve("旧记忆", hot_limit=200, user_id=_uid, chat_type="private").hot)
      and "旧记忆" in " ".join(
          m["content"] for m in _sym.retrieve("旧记忆", hot_limit=200, user_id=_uid, chat_type="group").hot),
      str(_legacy))

check("私聊提示词告诉她是一对一", "私聊" in _loop_mod.chat_hint("private"), _loop_mod.chat_hint("private")[:40])
check("群聊提示词告诉她有人在看", "群聊" in _loop_mod.chat_hint("group"), _loop_mod.chat_hint("group")[:40])
check("面板等不分场合的调用不加场合提示", _loop_mod.chat_hint("") == "")

print("[26] 加好友：只登记本地，不假装能加 QQ 好友")
from app.core.config import OneBotCfg as _OneBotCfg  # noqa: E402

check("好友申请默认自动通过", _OneBotCfg().auto_accept_friend_request is True,
      str(_OneBotCfg().auto_accept_friend_request))
_ob.auto_accept_friend_request = False  # 先测"只记下来、等人点"的那条路
_added = client.post("/v1/console/friends", json={"openid": "555001", "nickname": "新人甲"}).json()
check("加好友返回登记语义", _added.get("mode") == "registered" and bool(_added.get("note")), str(_added)[:160])
_pending_before = client.get("/v1/console/friends/requests").json()
check("申请列表可读", isinstance(_pending_before.get("items"), list), str(_pending_before)[:120])
_req = client.post("/onebot/event", json={
    "post_type": "request", "request_type": "friend", "self_id": 999999,
    "user_id": 777001, "flag": "smoke-flag-1", "comment": "加一下",
}).json()
check("好友申请先记下来不自动通过",
      _req.get("mode") == "friend_request_pending" and _req.get("request_id"), str(_req)[:160])
_pending_after = client.get("/v1/console/friends/requests", params={"status": "pending"}).json()
check("待处理列表里有这条申请",
      any(str(i.get("openid")) == "777001" for i in _pending_after.get("items") or []),
      str(_pending_after.get("items"))[:160])
_approve = client.post(f"/v1/console/friends/requests/{_req['request_id']}", json={"approve": True})
check("NapCat 不通时通过会明确失败", _approve.status_code == 502, _approve.text[:160])
_req_row = client.get("/v1/console/friends/requests").json()
check("失败原因留在申请上",
      any(str(i.get("id")) == str(_req["request_id"]) and i.get("status") == "failed"
          for i in _req_row.get("items") or []), str(_req_row.get("items"))[:200])
_del = client.delete(f"/v1/console/friends/requests/{_req['request_id']}")
check("申请可删", _del.status_code == 200, _del.text[:120])
_dup = client.post("/onebot/event", json={
    "post_type": "request", "request_type": "friend", "self_id": 999999,
    "user_id": 777002, "flag": "smoke-flag-2",
}).json()
_dup2 = client.post("/onebot/event", json={
    "post_type": "request", "request_type": "friend", "self_id": 999999,
    "user_id": 777002, "flag": "smoke-flag-2",
}).json()
check("同一个 flag 不重复建行", _dup.get("request_id") == _dup2.get("request_id"), str([_dup, _dup2])[:160])
check("非好友类申请不处理",
      client.post("/onebot/event", json={
          "post_type": "request", "request_type": "group", "self_id": 999999,
          "user_id": 777003, "flag": "g-1", "group_id": 1,
      }).json().get("ignored") is True)
_ob.auto_accept_friend_request = True
_auto = client.post("/onebot/event", json={
    "post_type": "request", "request_type": "friend", "self_id": 999999,
    "user_id": 777004, "flag": "smoke-flag-3",
}).json()
check("开了自动通过会真去调 NapCat", _auto.get("mode") == "friend_request_failed", str(_auto)[:160])

# 起一个假的 NapCat：验证"自动通过"这条路真的把 set_friend_add_request 发出去了
import http.server as _httpserver  # noqa: E402
import socketserver as _socketserver  # noqa: E402
import threading as _threading  # noqa: E402

_calls: list[tuple[str, str]] = []


class _FakeNapCat(_httpserver.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - 标准库签名
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n).decode("utf-8") if n else ""
        _calls.append((self.path, body))
        data: Any = None
        if "get_login_info" in self.path:
            data = {"user_id": 999999, "nickname": "小鲸"}
        elif "get_status" in self.path:
            data = {"online": True, "good": True}
        elif "get_friend_list" in self.path:
            data = [
                {"user_id": 800101, "nickname": "甲"},
                {"user_id": 800102, "remark": "乙"},
            ]
        payload = {"status": "ok", "retcode": 0}
        if data is not None:
            payload["data"] = data
        blob = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def log_message(self, *args):  # noqa: D401 - 静音
        return


_srv = _socketserver.TCPServer(("127.0.0.1", 0), _FakeNapCat)
_threading.Thread(target=_srv.serve_forever, daemon=True).start()
_ob.api_base = f"http://127.0.0.1:{_srv.server_address[1]}"
_ob.auto_accept_friend_request = True
_auto_ok = client.post("/onebot/event", json={
    "post_type": "request", "request_type": "friend", "self_id": 999999,
    "user_id": 777005, "flag": "smoke-flag-4", "comment": "加我一下",
}).json()
check("自动通过成功 → accepted",
      _auto_ok.get("mode") == "friend_request_accepted", str(_auto_ok)[:160])
check("真的发出了 set_friend_add_request",
      any("set_friend_add_request" in path for path, _ in _calls), str(_calls)[:200])
_fr = next((json.loads(body) for path, body in _calls if "set_friend_add_request" in path), {})
check("申请参数带了 flag 且 approve=true",
      bool(_fr.get("flag")) and _fr.get("approve") is True, str(_fr)[:160])
check("通过后自动建了用户",
      _users.get_user_by_openid("777005") is not None, str(_users.get_user_by_openid("777005")))

# 控制台：小号登录状态 + 同步 QQ 真实好友（同一个假 NapCat）
_login = client.get("/v1/console/onebot/login").json()
check("控制台能查小号登录",
      _login.get("online") is True and str(_login.get("user_id")) == "999999", str(_login)[:160])
_friends_before = int(_db.one("SELECT COUNT(*) c FROM qq_user")["c"])
_sync = client.post("/v1/console/onebot/sync").json()
check("同步 QQ 好友", int(_sync.get("total") or 0) == 2 and int(_sync.get("added") or 0) == 2,
      str(_sync)[:160])
check("同步后好友真的入库",
      int(_db.one("SELECT COUNT(*) c FROM qq_user")["c"]) == _friends_before + 2,
      str(_friends_before))
_status_now = client.get("/v1/console/status").json()
check("状态里带 onebot 配置",
      isinstance(_status_now.get("onebot"), dict)
      and _status_now["onebot"].get("enabled") is True
      and bool(_status_now["onebot"].get("api_base")),
      str(_status_now.get("onebot"))[:160])
_srv.shutdown()
_srv.server_close()
_ob.api_base = "http://127.0.0.1:9"  # 收工：还原成不可达
_ob.auto_accept_friend_request = True  # 回到默认
_ob.enabled = False

print("[27] 控制台口令（配了才校验）")
_rt2 = _rt_get()
_old_token = _rt2.cfg.console.token
_rt2.cfg.console.token = "smoke-token"
_r401 = client.get("/v1/console/status")
check("没带口令被拒", _r401.status_code == 401, _r401.text[:120])
_r_ok = client.get("/v1/console/status", headers={"X-Console-Token": "smoke-token"})
check("带对口令放行", _r_ok.status_code == 200, _r_ok.text[:120])
check("状态里能看出控制台有没有口令", _r_ok.json().get("console_auth") is True, _r_ok.text[:120])
check("口令也能走 query", client.get("/v1/console/status", params={"token": "smoke-token"}).status_code == 200)
_rt2.cfg.console.token = _old_token
check("没配口令时不校验（回环地址）", client.get("/v1/console/status").status_code == 200)

print("[28] 厌恶校准（平常不涨，明确辱骂才大幅）")
from app.relation import appraise as _ap  # noqa: E402


def _av_delta(text: str) -> tuple[float, list[str]]:
    _users.update_relation(_u1_id, aversion=20.0)
    before = float(_users.relation_of(_u1_id)["aversion"])
    apr = _ap.appraise(text, 12)
    _engine.apply_appraisal(apr, _rt.persona, user_id=_u1_id)
    after = float(_users.relation_of(_u1_id)["aversion"])
    return after - before, list(apr.get("tags") or [])


_d0, _t0 = _av_delta("今天吃了两碗白饭，你呢")
check("普通聊天不涨厌恶", _d0 <= 0.0, f"{_d0} {_t0}")
_d1, _t1 = _av_delta("你烦不烦啊")
check("一般冲撞只小涨", 0 < _d1 <= 1.2 and "conflict" in _t1, f"{_d1} {_t1}")
_d2, _t2 = _av_delta("你是不是AI机器人")
check("试探只小涨", 0 < _d2 <= 0.8 and "test" in _t2, f"{_d2} {_t2}")
_d3, _t3 = _av_delta("滚开")
check("玩笑式滚算冲撞不算辱骂", "attack" not in _t3 and "conflict" in _t3, str(_t3))
_d4, _t4 = _av_delta("你就是个傻逼，滚开去死")
check("明确辱骂才大幅涨", 2.0 <= _d4 <= 6.0 and "attack" in _t4, f"{_d4} {_t4}")

_users.update_relation(_u1_id, aversion=20.0)
_before = float(_users.relation_of(_u1_id)["aversion"])
_engine.apply_appraisal(
    {"valence": "negative", "intensity": 0.8, "tags": ["test"],
     "llm": {"aversion": 4, "affinity": 0, "trust": 0, "attraction": 0}},
    _rt.persona, user_id=_u1_id,
)
_d5 = float(_users.relation_of(_u1_id)["aversion"]) - _before
check("模型也不能把试探顶高", 0 < _d5 <= 0.8, str(_d5))

print("[29] 控制台认人显示：群号列表 / 观察与看法带昵称")
_g = client.get("/v1/console/groups").json()
check("群号列表可读", isinstance(_g.get("items"), list), str(_g)[:120])
_obs2 = client.get("/v1/console/observations").json()
check("观察带昵称字段", all("nickname" in i for i in (_obs2.get("items") or [])), str(_obs2)[:120])
_mind2 = client.get("/v1/console/mind").json()
check("看法带昵称字段",
      all("nickname" in i for i in (_mind2.get("impressions") or [])), str(_mind2.get("impressions"))[:160])

print("[30] 新人识别：私聊自动进花名册 / 群聊新人正常 / 测试期应答")
_ob.enabled = True
_ob.api_base = "http://127.0.0.1:9"
_ob.self_id = "999999"
_ob.group_at_only = True
_ob.sticker_chance = 0.0
_ob.voice_chance = 0.0

# 私聊新人：只私聊过，没在群里说过话
for _i in range(2):
    client.post("/onebot/event", json={
        "post_type": "message", "message_type": "private", "self_id": 999999,
        "user_id": 515151,
        "message": [{"type": "text", "data": {"text": "你好，我是私聊新人" if _i == 0 else "在吗"}}],
        "sender": {"nickname": "私聊新人"},
    })
_pm = _users.get_user_by_openid("515151")
check("私聊新人自动建号", _pm is not None and _pm["nickname"] == "私聊新人", str(_pm))

# 群聊新人：第一次在群里 @ 她
_grp_new = client.post("/onebot/event", json={
    "post_type": "message", "message_type": "group", "self_id": 999999,
    "user_id": 616161, "group_id": 888999000,
    "message": [{"type": "at", "data": {"qq": "999999"}},
                {"type": "text", "data": {"text": " 我是刚进群的新人"}}],
    "sender": {"nickname": "群聊新人"},
}).json()
check("群聊新人被处理", _grp_new.get("ok") is True, str(_grp_new)[:160])
check("群聊新人自动建号", _users.get_user_by_openid("616161") is not None,
      str(_users.get_user_by_openid("616161")))

# 让上一个 prompt 是群聊：花名册应同时包含两个新人
for _i in range(2):
    client.post("/onebot/event", json={
        "post_type": "message", "message_type": "group", "self_id": 999999,
        "user_id": 616161, "group_id": 888999000,
        "message": [{"type": "at", "data": {"qq": "999999"}},
                    {"type": "text", "data": {"text": " 群里有人吗" if _i == 0 else " 在不在"}}],
        "sender": {"nickname": "群聊新人"},
    })
_sys30 = str((_rt.last_prompt or {}).get("system") or "")
check("私聊新人自动进花名册", "私聊新人" in _sys30, _sys30[:120])
check("群聊新人也在花名册", "群聊新人" in _sys30, _sys30[:120])
check("群聊消息带新人署名",
      any("群聊新人：" in str(m.get("content", "")) for m in ((_rt.last_prompt or {}).get("messages") or [])),
      str((_rt.last_prompt or {}).get("messages") or [])[:120])

# 测试期应答：只回三句模板，不走模型
_t_on = client.post("/v1/console/testing", json={"enabled": True}).json()
check("测试期开关可开", _t_on.get("enabled") is True, str(_t_on)[:120])
_t_reply = client.post("/onebot/event", json={
    "post_type": "message", "message_type": "private", "self_id": 999999,
    "user_id": 515151,
    "message": [{"type": "text", "data": {"text": "你在干嘛"}}],
    "sender": {"nickname": "私聊新人"},
}).json()
_t_her = _db.one(
    "SELECT text FROM events WHERE role = 'her' AND source = 'qq_onebot' AND user_id = ?"
    " ORDER BY id DESC LIMIT 1", (int(_pm["id"]),))
check("测试期只回模板",
      _t_her is not None and str(_t_her["text"]) in _loop_mod.TESTING_REPLIES,
      str(dict(_t_her) if _t_her else None))
_t_off = client.post("/v1/console/testing", json={"enabled": False}).json()
check("测试期开关可关", _t_off.get("enabled") is False, str(_t_off)[:120])

print("[31] 主动招呼还没认识的人")
_greet_calls: list[tuple[str, str]] = []


class _FakeNapCatGreet(_httpserver.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n).decode("utf-8") if n else ""
        _greet_calls.append((self.path, body))
        data: Any = None
        if "get_group_member_list" in self.path:
            data = [
                {"user_id": 818181, "nickname": "群里没说过话的"},
                {"user_id": 919191, "card": "新人乙"},
                {"user_id": 999999, "nickname": "她自己"},
            ]
        payload = {"status": "ok", "retcode": 0}
        if data is not None:
            payload["data"] = data
        blob = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def log_message(self, *args):  # noqa: D401
        return


_gsrv = _socketserver.TCPServer(("127.0.0.1", 0), _FakeNapCatGreet)
_threading.Thread(target=_gsrv.serve_forever, daemon=True).start()
_ob.enabled = True
_ob.api_base = f"http://127.0.0.1:{_gsrv.server_address[1]}"
_ob.self_id = "999999"
_ob.sticker_chance = 0.0
_ob.voice_chance = 0.0

_mem = client.get("/v1/console/groups/888999000/members").json()
check("群成员可读", int(_mem.get("total") or 0) == 3 and len(_mem.get("unknown") or []) == 2,
      str(_mem)[:160])
_g1 = client.post("/v1/console/groups/888999000/greet?limit=3").json()
check("主动招呼未认识的人", len(_g1.get("greeted") or []) == 2, str(_g1)[:200])
check("招呼时真的 @ 了对方",
      sum(1 for p, b in _greet_calls if "send_group_msg" in p
          and ("818181" in b or "919191" in b)) >= 2, str(_greet_calls)[:200])
check("招呼过的人已建号", _users.get_user_by_openid("818181") is not None
      and _users.get_user_by_openid("919191") is not None)
_g2 = client.post("/v1/console/groups/888999000/greet?limit=3").json()
check("同一个人只招呼一次", len(_g2.get("greeted") or []) == 0, str(_g2)[:160])

# 新人第一次在群里说话：回完话再主动 @ 问名字
_auto = client.post("/onebot/event", json={
    "post_type": "message", "message_type": "group", "self_id": 999999,
    "user_id": 717171, "group_id": 888999000,
    "message": [{"type": "at", "data": {"qq": "999999"}},
                {"type": "text", "data": {"text": " 我新来的"}}],
    "sender": {"nickname": "自动新人"},
}).json()
check("新人第一句会自动招呼", _auto.get("greeted") is True, str(_auto)[:160])
check("自动招呼也 @ 了新人",
      any("send_group_msg" in p and "717171" in b for p, b in _greet_calls), str(_greet_calls)[-3:])

# 测试期应答开着时不打扰
client.post("/v1/console/testing", json={"enabled": True})
_quiet = client.post("/onebot/event", json={
    "post_type": "message", "message_type": "group", "self_id": 999999,
    "user_id": 727272, "group_id": 888999000,
    "message": [{"type": "at", "data": {"qq": "999999"}},
                {"type": "text", "data": {"text": " 我是另一个新人"}}],
    "sender": {"nickname": "安静新人"},
}).json()
check("测试期不主动招呼", _quiet.get("greeted") is False, str(_quiet)[:160])
client.post("/v1/console/testing", json={"enabled": False})
_gsrv.shutdown()
_gsrv.server_close()
_ob.enabled = False
_ob.api_base = "http://127.0.0.1:9"

print("[32] 全局强制模型（覆盖所有任务与深思）")
from app.mind import models as _models  # noqa: E402

_old_force = _rt.cfg.provider.force_model
_rt.cfg.provider.force_model = "deepseek-ai/DeepSeek-V4.1-Flash"
_s32 = _models.pick("summary")
_c32 = _models.pick("chat_default", force_think=True)
check("强制模型覆盖所有任务",
      _s32.key == "forced" and _s32.provider_model == "deepseek-ai/DeepSeek-V4.1-Flash", str(_s32))
check("强制模型覆盖深思", _c32.key == "forced", _c32.key)
_rt.cfg.provider.force_model = _old_force
check("设置里能看到强制模型", "force_model" in _models.current_settings(),
      str(_models.current_settings()))

print("[33] 模型最大参数：health 与运行配置自洽（不写死某个模型的绝对上限）")
_h33 = client.get("/v1/health").json()
check("健康里暴露上下文与输出上限",
      isinstance(_h33.get("context_window"), int) and isinstance(_h33.get("max_tokens"), int),
      str(_h33)[:200])
_cfg33 = get_runtime().cfg
check("上下文按模型上限配置",
      int(_h33.get("context_window") or 0) == int(_cfg33.context.window)
      and int(_h33.get("reserve_output") or 0) == int(_cfg33.context.reserve_output)
      and int(_cfg33.context.reserve_output) < int(_cfg33.context.window), str(_h33))
check("输出按模型上限配置",
      int(_h33.get("max_tokens") or 0) == int(_cfg33.provider.max_tokens)
      and 0 < int(_cfg33.provider.max_tokens) <= int(_cfg33.context.window),
      str(_h33.get("max_tokens")))

print("[34] 恋爱门槛对齐 / 人设解禁 / 低精力也黏人")
from app.life import behavior as _behavior  # noqa: E402

check("恋爱门槛对齐 80", _users.THRESHOLD == 80.0 and _users.stage_for(80.0) == "lover",
      f"{_users.THRESHOLD}/{_users.stage_for(80.0)}")
_world = str((_rt.persona.raw or {}).get("world") or "")
check("人设不再全局禁恋", "她不谈恋爱" not in _world, _world[:120])
check("家人边界仍保留", "不会对生父和干爹越界" in _world, _world[-80:])
_r34 = _behavior.decide({"energy": 20, "hour": 14, "activity": "摸鱼", "sleeping": False}, 1)
check("低精力也黏人", ("黏" in _r34.prompt_hint or "撒娇" in _r34.prompt_hint), _r34.prompt_hint)

print("[35] 活人感：未完成话题 / 关系仪式 / 观察者主动")
from app.memory import threads as _threads  # noqa: E402
from app.relation import rituals as _rituals  # noqa: E402

_u35 = _users.ensure_user("smoke-thread-1", "话题甲")
_uid35 = int(_u35["id"])
_tid = _threads.capture(_uid35, "private", "这个明天再讲，我先去睡。")
check("她答应以后再说会被记下", _tid > 0, str(_tid))
_db.execute("UPDATE open_threads SET due_at = ? WHERE id = ?", ("2026-01-01T00:00:00+08:00", _tid))
_due = _threads.due_threads(_uid35, "private")
check("到点会浮出来一次", len(_due) == 1 and "明天" in str(_due[0]["text"]), str(_due)[:120])
check("浮出后不重复", _threads.due_threads(_uid35, "private") == [])
check("同一句不重复建", _threads.capture(_uid35, "private", "这个明天再讲，我先去睡。") == 0)

_rh = _rituals.hint(_uid35, 23, "private")
check("深夜有晚安提示", "晚安" in _rh, _rh[:80])
_rituals.confirm(_uid35, "晚安，明天见。")
check("说过晚安后当天不再提示", "晚安" not in _rituals.hint(_uid35, 23, "private"))
check("观察者也会低压主动", __import__("app.life.proactive", fromlist=["x"]).IDLE_MINUTES.get(0) == 360)

print("[36] 恋人主动撩 / 露骨触发扩词")
from app.intimacy import mode as _im  # noqa: E402
from app.intimacy import prompt as _ip  # noqa: E402

check("直白词扩展：想睡你", _im.detect_intent("今晚想睡你") == "explicit")
check("直白词扩展：骚话", _im.detect_intent("我想听你说点骚话") == "explicit")
check("直白词扩展：想摸你", _im.detect_intent("想摸你") == "explicit")
check("暗示词扩展：想黏你", _im.detect_intent("想黏你一会儿") == "suggestive")
_sug36 = _ip.build_system_prompt(_im.Mode.SUGGESTIVE, True, True, "小鲸")
check("恋人暗示会主动引导", "主动一点" in _sug36 and "喜欢什么" in _sug36, _sug36[:120])
_exp36 = _ip.build_system_prompt(_im.Mode.EXPLICIT, True, True, "小鲸")
check("直白可主动提", "主动提出" in _exp36 and "喜欢什么" in _exp36, _exp36[:120])

print("[37] 恋人重复表白不误拒")
_u37 = _users.get_user_by_openid("smoke-love-3")
if _u37 is not None and _users.lover_of(int(_u37["id"])) is not None:
    _r37 = client.post("/v1/chat", json={"text": "我爱你", "user_id": int(_u37["id"])}).json()
    check("恋人再说我爱你不会误拒",
          (_r37.get("meta") or {}).get("love") == "lover_again"
          and not any("只有一个人" in str(x) for x in (_r37.get("reply") or [])),
          str(_r37.get("meta"))[:160])
else:
    check("恋人再说我爱你不会误拒", False, "前置恋人状态缺失")

print("[38] 引导模式：暗示→明示→露骨→收尾")
from app.intimacy import guide as _guide  # noqa: E402

_guide.start(_uid35)
check("引导从暗示开始", _guide.stage(_uid35) == 0 and "棒棒糖" in _guide.hint(_uid35, True, "private"), "")
_guide.bump(_uid35, "我想睡你")
check("露骨请求推进到直白", _guide.stage(_uid35) == 2 and "露骨" in _guide.hint(_uid35, True, "private"), "")
_guide.bump(_uid35, "想要你")
_guide.bump(_uid35, "想操你")
check("三次露骨进入收尾", _guide.stage(_uid35) == 3 and "睡" in _guide.hint(_uid35, True, "private"), "")
_gpath = _guide.finish(_uid35)
check("收尾写下经验文件", __import__("pathlib").Path(_gpath).exists(), _gpath)
__import__("pathlib").Path(_gpath).unlink(missing_ok=True)

print("[39] 喊停软着陆")
from app.intimacy import mode as _im2  # noqa: E402

_im2.set_mode(99, _im2.Mode.EXPLICIT)
_m39, _note39 = _im2.transition(99, "stop", is_private=True, is_lover=True)
check("喊停返回软着陆标记", _note39 == "stop_soft" and _m39 == _im2.Mode.OFF, f"{_m39}/{_note39}")

print("[40] 热梗查询 / 私聊上下文隔离")
from app.web import lookup as _lookup  # noqa: E402

check("梗问法触发查询", _lookup.needs_lookup("你知道 city不city 这个梗吗？") == "city不city",
      str(_lookup.needs_lookup("你知道 city不city 这个梗吗？")))
check("热梗泛问触发查询", _lookup.needs_lookup("最近有什么热梗？") == "最近网络热梗")
check("内置梗释义命中", "洋气" in str(_lookup.lookup("city不city").get("summary") or ""),
      str(_lookup.lookup("city不city").get("summary"))[:80])
from app.memory import store as _store  # noqa: E402

_store.add_event("user", "群聊测试不该进私聊", user_id=_uid35, chat_type="group", group_id="999")
_store.add_event("user", "私聊测试只给自己", user_id=_uid35, chat_type="private")
_store.add_event("user", "别人的私聊也不该进来", user_id=_u1["id"], chat_type="private")
_rows40 = _store.events_between(0, 10**12, chat_type="private", user_id=_uid35)
check("私聊不混群聊", not any("群聊测试" in str(r.get("text")) for r in _rows40), str(len(_rows40)))
check("私聊不混别人", not any("别人的私聊" in str(r.get("text")) for r in _rows40), str(len(_rows40)))

print("[41] 衡忆 2.0：引擎接管 / 视图只读 / 软合并可还原")
import asyncio as _asyncio  # noqa: E402
import time as _time  # noqa: E402

from app.core import memengine as _me  # noqa: E402
from app.memory import cold as _cold, h2 as _h2  # noqa: E402

_kind41 = _db.one("SELECT type FROM sqlite_master WHERE name = 'memories'")
check("memories 已是只读视图", _kind41 is not None and _kind41["type"] == "view",
      str(dict(_kind41) if _kind41 else None))
_legacy41 = _db.one("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'memories_legacy%'")
_vrows41 = int(_db.one("SELECT COUNT(*) c FROM memories")["c"])
_mrows41 = int(_me.store().count_items())
check("视图与引擎条目数一致", _vrows41 <= _mrows41, f"view={_vrows41} engine={_mrows41}")
check("旧表若存在也只剩留档（或全新库没有旧表）",
      _legacy41 is None
      or int(_db.one(f"SELECT COUNT(*) c FROM {_legacy41['name']}")["c"]) >= _vrows41,
      str(dict(_legacy41) if _legacy41 else "fresh-db"))
_stats41 = _me.store().stats()
check("引擎 schema v2 已生效", int(_stats41.get("schema_version") or 0) >= 2, str(_stats41.get("schema_version")))
_cols41 = {r["name"] for r in _db.rows("PRAGMA table_info(memories)")}
check("视图列覆盖旧 SQL 用到的字段",
      {"heat", "pinned", "cues", "last_touch", "user_id", "chat_type", "first_seen", "source_ids"} <= _cols41,
      str(sorted(_cols41)))
try:
    _db.execute("INSERT INTO memories (kind, content, first_seen, last_seen)"
                " VALUES ('fact', '偷写视图', 'x', 'y')")
    _writable41 = True
except Exception:  # noqa: BLE001 - 视图本来就该拒绝写
    _writable41 = False
check("视图拒绝写入（写入只能走引擎）", not _writable41)

_pid41 = _h2.add("fact", "衡忆2.0：记忆核心已是引擎", importance=0.9, user_id=_uid35)
check("写入落在引擎条目表且视图可见",
      _db.one("SELECT id FROM mem_item WHERE id = ?", (_pid41,)) is not None
      and _db.one("SELECT id FROM memories WHERE id = ?", (_pid41,)) is not None, str(_pid41))
check("钉住即承诺", _h2.pin(_pid41, True)
      and abs(float(_db.one("SELECT heat FROM memories WHERE id = ?", (_pid41,))["heat"]) - 1.0) < 1e-9)
_logs_before41 = int(_me.store().stats().get("recall_logs") or 0)
_h2.retrieve("衡忆2.0 引擎", user_id=_uid35, chat_type="private")
_logs_after41 = int(_me.store().stats().get("recall_logs") or 0)
check("一次召回只落一条审计", _logs_after41 == _logs_before41 + 1, f"{_logs_before41}->{_logs_after41}")
_h2.add("fact", "共用档案：她的猫叫年糕", importance=0.95, user_id=0, chat_type="")
_shared41 = _h2.retrieve("年糕", hot_limit=200, user_id=_uid35, chat_type="private")
check("共用分区（user_id=0）对所有人生效",
      any("年糕" in m["content"] for m in _shared41.hot + _shared41.recalled))

from symvolve_memory import MemoryEvent as _MEv41, MemoryItem as _MIt41  # noqa: E402

_old41 = int(_time.time()) - 90 * 86400
_cold_ids41 = []
for _t41 in ("去年冬天一起看海", "风很大", "回来买了热奶茶"):
    _e41 = _me.store().append_event(_MEv41(user_id=str(_uid35), chat_type="private",
                                           raw_text=_t41, created_at=_old41))
    _cold_ids41.append(_me.store().insert_item(_MIt41(
        user_id=str(_uid35), chat_type="private", kind="fact", title=_t41[:8], text=_t41,
        importance=30, heat=0.05, clarity="trace", source_event_ids=[_e41],
        created_at=_old41, last_seen=_old41)))


class _MockProvider41:
    is_mock = True


class _MockCfg41:
    provider = _MockProvider41()


class _MockRT41:
    cfg = _MockCfg41()


_compact41 = _asyncio.run(_cold.compact_cold(_MockRT41(), None))
check("冷记忆按时间+主题合并", int(_compact41.get("merged") or 0) >= 1
      and int(_compact41.get("freed") or 0) >= 1, str(_compact41)[:160])
_visible41 = {r["id"] for r in _db.rows("SELECT id FROM memories")}
check("合并后源条目从视图消失（行还在档案里）",
      all(i not in _visible41 for i in _cold_ids41)
      and all(_db.one("SELECT merged_into FROM mem_item WHERE id = ?", (i,))["merged_into"]
              is not None for i in _cold_ids41), str(_cold_ids41))
_merge41 = _db.one("SELECT id FROM memory_merges ORDER BY id DESC LIMIT 1")
_restored41 = _cold.restore(int(_merge41["id"]))
_visible_after41 = {r["id"] for r in _db.rows("SELECT id FROM memories")}
check("restore 把原条目原样还原",
      bool(_restored41.get("ok")) and all(i in _visible_after41 for i in _cold_ids41),
      str(_restored41))
_cm41 = client.get(f"/v1/console/memory/{_uid35}/stats").json()
check("控制台记忆统计口径不变", {"user_id", "count", "by_kind"} <= set(_cm41), str(sorted(_cm41)))
_cd41 = client.post("/v1/dev/memory/compact", params={"password": _dev_key, "dry": 1}).json()
check("冷压缩 dry-run 端点可用", _cd41.get("ok") is True and "groups" in _cd41, str(_cd41)[:140])

print("[42] 回归：群活跃调度不炸事件循环 / 删两次给 404")
from app.group import active as _gact  # noqa: E402

_ob.enabled = False
_r42 = client.post("/v1/group/start", params={"interval": 600})
check("群活跃启动端点 200 且 ok", _r42.status_code == 200 and _r42.json().get("ok") is True, _r42.text[:140])
_r42b = client.post("/v1/group/start", params={"interval": 600})
check("重复启动仍 200（幂等不抛）", _r42b.status_code == 200, _r42b.text[:120])
check("群活跃停止端点 200", client.post("/v1/group/stop").status_code == 200)
# 同步上下文（没有事件循环）调 start：必须走线程兜底而不是抛 RuntimeError
check("线程兜底启动不抛", _gact.start(600) is True)
_gact.stop()

_pid42 = _h2.add("fact", "回归：同一条删两次", importance=0.5, user_id=_uid35)
check("第一次删成功", client.delete(f"/v1/memories/{_pid42}").status_code == 200)
check("第二次删 404", client.delete(f"/v1/memories/{_pid42}").status_code == 404)

print("[43] 连续性账本：改写她必须留痕（docs/51）")
from app.core import continuity as _cont  # noqa: E402

_c0 = _cont.count()
_r43 = client.get("/v1/console/continuity")
check("连续性接口 200 且结构对",
      _r43.status_code == 200 and {"count", "items", "major"} <= set(_r43.json()), _r43.text[:120])
_before43 = int(_r43.json().get("count") or 0)
check("改写前账本可读（行数自洽）", _c0 == _before43, f"{_c0} vs {_before43}")
client.post("/v1/dev/memory/clear", params={"password": _dev_key})
_c43 = client.get("/v1/console/continuity").json()
check("清记忆留一条痕",
      int(_c43.get("count") or 0) == _before43 + 1
      and any(i.get("kind") == "memory_forgotten" for i in _c43.get("items") or []),
      str(_c43.get("count")))
check("痕迹带人话标签（不是冷日志）",
      bool((_c43.get("items") or [{}])[0].get("label")),
      str((_c43.get("items") or [{}])[0])[:140])
_c43b = int(_c43.get("count") or 0)
client.post("/v1/dev/relation/reset", params={"password": _dev_key})
check("关系重置也留痕", _cont.count() == _c43b + 1, f"{_c43b} -> {_cont.count()}")
check("账本只增不改（历史不被覆盖）", _cont.count() > _c0)

print("[44] 陪伴层（衡忆 2.0 companion-emotion）")
from app.memory import companion as _comp  # noqa: E402

_uid44 = _uid35
_h2.add("fact", "加班到十一点，项目赶上线", importance=0.9, cues=["累", "加班"],
        user_id=_uid44, chat_type="private")
_h44 = _comp.hint_for("最近好累啊", user_id=_uid44, chat_type="private")
check("情绪够浓时会把旧事端上来", bool(_h44) and "加班" in _h44, _h44[:120])
check("同一件旧事不连着端两次（冷却）",
      _comp.hint_for("最近好累啊", user_id=_uid44, chat_type="private") == "")
check("中性话不硬提旧事",
      _comp.hint_for("今天几号", user_id=_uid44, chat_type="private") == "")
_t44 = _comp.hear("最近好累啊", user_id=_uid44, chat_type="private")
check("情绪读数正确", _t44["emotion"]["intensity"] > 0 and "累" in _t44["emotion"]["labels"],
      str(_t44["emotion"]))
check("决定在三态内（speak/ask/silent）", _t44["action"] in ("speak", "ask", "silent"),
      str(_t44["action"]))
check("冷却生效（不该再说）", _t44["should_initiate"] is False, str(_t44)[:160])
_r44 = client.get(f"/v1/console/companion/{_uid44}")
check("陪伴层状态接口", _r44.status_code == 200
      and {"policy", "last_turn", "thoughts_recent_24h"} <= set(_r44.json()), _r44.text[:140])
_d44 = client.post("/v1/dev/companion/ruminate",
                   params={"password": _dev_key, "user_id": _uid44, "dry": 1})
check("独处挑料 dry-run 可用", _d44.status_code == 200 and _d44.json().get("dry_run") is True,
      _d44.text[:140])
_meta44 = (client.post("/v1/chat", json={"text": "最近好累啊", "user_id": _uid44}).json()
           .get("meta") or {})
check("回合 meta 带陪伴层结论",
      isinstance(_meta44.get("companion"), dict) and bool(_meta44.get("companion")),
      str(sorted(_meta44))[:160])

print("[45] 夜间独处：她自己会想事（且不会变成\"用户说过的话\"）")
import asyncio as _asyncio  # noqa: E402

from app.life import thoughts as _thoughts  # noqa: E402

_uid45 = _uid35
_rt45 = get_runtime()
_h2.add("fact", "她说想去看一次海，后来一直没去", importance=0.9, cues=["海", "想去"],
        user_id=_uid45, chat_type="private")
_r45 = _asyncio.run(_thoughts.maybe_ruminate(_rt45, _rt45.persona, _uid45, force=True))
check("夜里能想出一句（force）", bool(_r45 and _r45.get("ok") and _r45.get("item_id")), str(_r45))
_item45 = _h2.get_memory(int(_r45["item_id"])) if _r45 and _r45.get("ok") else None
check("念头 kind=thought 且重要度封顶",
      bool(_item45) and _item45["kind"] == "thought"
      and float(_item45["importance"]) <= 0.2, str(_item45)[:120])
_rec45 = _h2.retrieve("海 想去", hot_limit=200, user_id=_uid45, chat_type="private")
check("念头不进热层（不会被当作用户说过的话）",
      not any(m["kind"] == "thought" for m in _rec45.hot + _rec45.recalled),
      str([m["kind"] for m in _rec45.hot][:6]))
_r45b = _asyncio.run(_thoughts.maybe_ruminate(_rt45, _rt45.persona, _uid45, force=True))
check("同一颗念头冷却（不会连着想）",
      bool(_r45b) and _r45b.get("ok") is False and "冷却" in str(_r45b.get("reason")),
      str(_r45b))
_ct45 = client.get(f"/v1/console/companion/{_uid45}/thoughts")
check("念头列表接口", _ct45.status_code == 200 and "items" in _ct45.json(), _ct45.text[:120])
_st45 = client.get(f"/v1/console/companion/{_uid45}").json()
check("状态里能看到账本计数",
      "ledger" in _st45 and _st45["ledger"].get("thought_writes", 0) >= 1,
      str(_st45.get("ledger")))

print("[46] 念头第二天浮现：提一次就冷却")
from datetime import datetime as _dt  # noqa: E402

import app.life.thoughts as _th46  # noqa: E402

_uid46 = _uid45
_recent46 = _th46.recent(_uid46, limit=5)
_mid46 = int(_recent46[0]["id"]) if _recent46 else 0
check("夜里确实留下了一条念头", _mid46 > 0, str(_recent46)[:120])
# 把念头创建时间挪到"昨天 22:00"：测试不依赖跑测试的钟点（不然上午 10 点后跑 age 会变负）
from datetime import timedelta as _td46  # noqa: E402
from app.core import db as _db46  # noqa: E402

_created46 = int((_dt.now().astimezone().replace(hour=22, minute=0, second=0,
                                                 microsecond=0) - _td46(days=1)).timestamp())
_db46.execute("UPDATE mem_item SET created_at = ? WHERE id = ?", (_created46, _mid46))
_day_ts = int(_dt.now().astimezone().replace(hour=10, minute=0, second=0,
                                             microsecond=0).timestamp())
_pick46 = _th46.pick_surface(_uid46, now_ts=_day_ts)
check("白天会挑出值得提的念头",
      bool(_pick46) and int(_pick46["id"]) == _mid46, str(_pick46)[:120])
_h46 = _th46.surface_hint(_pick46)
check("提示是她自己的口吻（不写「我昨晚想」）",
      bool(_h46) and "昨晚一个人时想过" in _h46 and "我昨晚想" not in _h46, _h46[:120])
check("提过就登记，不再提同一件",
      _th46.mark_surface(_mid46, _uid46)
      and _th46.pick_surface(_uid46, now_ts=_day_ts + 7 * 3600) is None)
check("夜里不翻旧账", _th46.pick_surface(_uid46, now_ts=_day_ts - 7 * 3600) is None)
_mid46b = _h2.add("thought", "想着周末要不要去海边走走", importance=0.18,
                  cues=["念头"], user_id=_uid46, chat_type="")
check("对方情绪很重时先接住他（不提自己的事）",
      _th46.pick_surface(_uid46, now_ts=_day_ts + 8 * 3600,
                         text="我真的好难受，撑不住了") is None)
_real_pick46 = _th46.pick_surface
_th46.pick_surface = lambda user_id=0, **kw: {
    "id": _mid46b, "content": "想着周末要不要去海边走走", "age_hours": 8.0,
}
_r46 = client.post("/v1/chat", json={"text": "在吗", "user_id": _uid46}).json()
_th46.pick_surface = _real_pick46
check("回合 meta 记下提了哪条念头",
      ((_r46.get("meta") or {}).get("thought")) == _mid46b,
      str((_r46.get("meta") or {}).get("thought")))
check("提过就落台账（同一条不会第二次）",
      any(x["id"] == _mid46b and x["surfaced"] for x in _th46.recent(_uid46, 5)),
      str(_th46.recent(_uid46, 5))[:160])

print("[47] 念头有据光谱：能联想、能记岔、能做梦，但不能凭空造")
_th47 = _th46
_ev47 = [{"kind": "chat", "id": 0, "text": "等这个项目上线，我想去看海。"}]
_d47 = lambda s: _th47._grounding_detail(s, _ev47)  # noqa: E731
check("连影子都没有的编造：判 none（退回复述）",
      _d47("2023 年我们在海边住了一周")["mode"] == "none",
      str(_d47("2023 年我们在海边住了一周")))
check("有一点点依据就允许联想（梦境级）",
      _d47("好像去年还是前年，我们去看海。")["mode"] == "dream",
      str(_d47("好像去年还是前年，我们去看海。")))
check("记得清的复述是清醒级",
      _d47("又想起项目上线后想去看海。")["mode"] == "clear",
      str(_d47("又想起项目上线后想去看海。")))
check("数字偏差不再一票否决，但要入审计",
      _d47("好像是 2023 年去看的海")["mode"] in ("dream", "fuzzy")
      and "2023" in _d47("好像是 2023 年去看的海")["new_digits"],
      str(_d47("好像是 2023 年去看的海")))
check("梦境提示会说「模糊的影子」",
      "模糊的影子" in _th47.surface_hint({"content": "好像去看过海", "mode": "dream"}),
      _th47.surface_hint({"content": "好像去看过海", "mode": "dream"})[:80])
_recent47 = {int(x["id"]): x for x in _th47.recent(_uid46, 10)}
check("夜里写下的念头带出处与级别（grounded）",
      bool(_recent47.get(_mid46, {}).get("grounded", {}).get("mode")),
      str(_recent47.get(_mid46, {}))[:140])
_day47 = _day_ts + 9 * 3600
check("来路不明的念头不会被端出去",
      _th47.pick_surface(_uid46, now_ts=_day47) is None,
      str(_th47.pick_surface(_uid46, now_ts=_day47)))

print("[48] 她主动开口：念头带出去 + 夜间静默/免打扰/配额（docs/55）")
from datetime import timedelta as _td48  # noqa: E402

from app.core import db as _db48  # noqa: E402
from app.life import proactive as _pro48  # noqa: E402
from app.relation import users as _users48  # noqa: E402

# 时刻全部相对"今天"生成：写死日期 = 明天开始必挂的定时炸弹
_now48 = _dt.now().astimezone()
_midnight48 = _now48.replace(hour=0, minute=0, second=0, microsecond=0)
_night48 = _midnight48 + _td48(hours=3)
_day48 = _midnight48 + _td48(hours=10)
_eve48 = _midnight48 + _td48(hours=19)
_eve2_48 = _midnight48 + _td48(hours=19, minutes=30)
_eve3_48 = _midnight48 + _td48(hours=20)
_eve4_48 = _midnight48 + _td48(hours=20, minutes=30)
_next48 = _midnight48 + _td48(days=1, hours=10)
_created48 = int(_midnight48.timestamp())   # 念头统一记成"今天 00:00 想的"

_u48 = _users48.ensure_user("980048", "主动开口")
_uid48 = int(_u48["id"])
_users48.update_relation(_uid48, affinity=100)
_rt48 = get_runtime()


def _say48(text: str, *, uid: int | None = None, role: str = "user",
           source: str = "qq_onebot", at=None) -> None:
    stamp = (at or (_midnight48 + _td48(hours=12))).isoformat(timespec="seconds")
    _db48.execute(
        "INSERT INTO events (ts, role, kind, text, source, meta, user_id, chat_type, group_id)"
        " VALUES (?, ?, 'text', ?, ?, '{}', ?, 'private', '')",
        (stamp, role, str(text), str(source), int(uid if uid is not None else _uid48)),
    )


def _thought48(text: str) -> int:
    """造一条"有据念头"，created_at 改到今天 00:00（年龄窗可预测，不依赖跑测试的钟点）。"""
    mid = _h2.add("thought", text, importance=0.18, cues=["念头"], user_id=_uid48,
                  chat_type="",
                  extra_meta={"grounded": {"score": 0.6, "mode": "clear", "shared": 5,
                                          "new_digits": [], "fallback": False,
                                          "evidence_ids": [], "evidence_kinds": ["chat"]}})
    try:
        _db48.execute("UPDATE mem_item SET created_at = ? WHERE id = ?",
                      (_created48, int(mid)))
    except Exception:  # noqa: BLE001 - 列名不同就按真实时间算
        pass
    return int(mid)


_say48("两天前聊过一次", at=(_midnight48 - _td48(days=2)))
_old_cap48 = _pro48.daily_max()
_db48.kv_set("proactive_daily_max", "0")   # 本节要连着发几条，先放开日上限
_pro48.clear_dnd(0)                        # [1.5] 那句"晚安"可能落过全局免打扰
_pro48.clear_dnd(_uid48)

check("夜间静默区间默认 23–08", _pro48.quiet_hours() == (23, 8), str(_pro48.quiet_hours()))
check("夜间/白天判断", _pro48.in_quiet_hours(_night48) and not _pro48.in_quiet_hours(_day48))
_db48.kv_set("dev_awake", "1")             # 排除"睡着"这条闸门，单测夜间静默
check("凌晨绝不主动外发（夜间静默单独也拦得住）",
      _pro48.tick_user(_rt48, _rt48.persona, _uid48, "private", now=_night48) == [])
_ok48, _why48 = _pro48.gate(_rt48, _rt48.persona, _uid48, now=_night48)
check("拦下时说人话（夜间静默）", (not _ok48) and "夜间静默" in str(_why48), str(_why48))
_db48.kv_set("dev_awake", "0")

_d48 = _pro48.detect_dnd("我在开会，别吵我", now=_day48)
check("说「在开会/别吵」就进免打扰", bool(_d48) and int(_d48[0]) > 0, str(_d48))
_pro48.apply_dnd(_uid48, _d48, now=_day48)
check("免打扰期间不主动",
      _pro48.tick_user(_rt48, _rt48.persona, _uid48, "private", now=_day48) == [])
_st48 = _pro48.dnd_status(_uid48, now=_day48)
check("免打扰能报原因和剩余时间",
      bool(_st48.get("active")) and int(_st48.get("left_min") or 0) > 0 and bool(_st48.get("reason")),
      str(_st48))
check("说「忙完了/在吗」就解除免打扰",
      _pro48.apply_dnd(_uid48, _pro48.detect_dnd("忙完了，在吗", now=_day48),
                       now=_day48).get("active") is False)
check("问句不误伤（你在忙吗 / 你睡了吗 / 你困了吗）",
      _pro48.detect_dnd("你在忙吗", now=_day48) is None
      and _pro48.detect_dnd("你睡了吗", now=_day48) is None
      and _pro48.detect_dnd("你困了吗", now=_day48) is None)
check("面板全局免打扰也会压住 QQ 主动",
      bool(_pro48.apply_dnd(0, (60, "面板手动免打扰"), now=_day48).get("active"))
      and _pro48.tick_user(_rt48, _rt48.persona, _uid48, "private", now=_day48) == [])
_pro48.apply_dnd(0, (0, ""), now=_day48)

_said48 = _pro48.tick_user(_rt48, _rt48.persona, _uid48, "private", now=_day48)
check("白天该主动时真的会开口", bool(_said48), str(_said48))

_mid48 = _thought48("又想起项目上线后想去看海边")
_say48("刚下班", at=(_eve48 - _td48(hours=1)))
_pick48 = _thoughts.pick_for_proactive(_uid48, now_ts=int(_eve48.timestamp()))
check("白天能挑出一条有据念头", bool(_pick48) and int(_pick48.get("id") or 0) == _mid48,
      str(_pick48))
_line48 = _thoughts.proactive_line(_pick48)
check("念头能翻成她主动发出的第一句", bool(_line48) and "海边" in _line48, str(_line48))
_said48b = _pro48.tick_user(_rt48, _rt48.persona, _uid48, "private", now=_eve48)
check("主动开口把念头带出去了",
      bool(_said48b) and "海边" in " ".join(_said48b), str(_said48b))
check("带出去的念头落了台账（聊天里不会再提一次）",
      any(x["id"] == _mid48 and x["surfaced"] for x in _thoughts.recent(_uid48, 5)),
      str(_thoughts.recent(_uid48, 5))[:160])
check("同一次沉默里不会连发两条",
      _pro48.tick_user(_rt48, _rt48.persona, _uid48, "private", now=_eve48) == [])

_db48.kv_set("proactive_thought_max", "0")
_say48("刚到家", at=(_eve48 - _td48(hours=2)))
_thought48("想起周末要去海边走走")
_said48c = _pro48.tick_user(_rt48, _rt48.persona, _uid48, "private", now=_eve2_48)
check("念头额度为 0 时不再带念头（直接寒暄）",
      bool(_said48c) and "海边" not in " ".join(_said48c), str(_said48c))
_db48.kv_set("proactive_thought_max", "2")

_db48.kv_set("proactive_daily_max", "1")
_say48("在干嘛呢", at=(_eve2_48 - _td48(minutes=10)))
_ok48b, _why48b = _pro48.gate(_rt48, _rt48.persona, _uid48, now=_eve3_48)
check("每天上限到了就闭嘴", (not _ok48b) and "次数" in str(_why48b), str(_why48b))

_db48.kv_set("proactive_daily_max", "0")
_say48("嗯", at=(_eve3_48 - _td48(minutes=10)))
_say48("还在吗", role="her", source="proactive", at=(_eve3_48 - _td48(minutes=5)))
_say48("在忙吗", role="her", source="proactive", at=(_eve3_48 - _td48(minutes=4)))
_ok48c, _why48c = _pro48.gate(_rt48, _rt48.persona, _uid48, now=_eve4_48)
check("连着两条没人接就不再发", (not _ok48c) and "连着发" in str(_why48c), str(_why48c))
_db48.kv_set("proactive_daily_max", str(_old_cap48))

_mid48c = _thought48("又想起他说过要一起去看海")
_say48("在吗", at=(_next48 - _td48(hours=1)))
_evt48_before = int(_db48.one("SELECT MAX(id) m FROM events")["m"] or 0)
_said48d = _asyncio.run(
    _pro48.tick_user_async(_rt48, _rt48.persona, _uid48, "private", now=_next48)
)
_text48 = " ".join(_said48d)
check("async 外发路径能用（模型→清洗→落库）",
      bool(_said48d) and 3 <= len(_text48) <= 80, str(_said48d)[:120])
check("发出去的话不露元信息",
      all(w not in _text48 for w in ("记忆", "记录", "系统", "念头")), _text48[:120])
_row48 = _db48.one(
    "SELECT id, source FROM events WHERE id > ? AND source = 'proactive_thought'"
    " ORDER BY id DESC LIMIT 1", (_evt48_before,))
check("主动带出的念头有独立来源标记（这次的新事件）",
      _row48 is not None and int(_row48["id"]) > _evt48_before,
      str(dict(_row48) if _row48 else None))
check("async 那次带的是念头（落了浮现台账）",
      any(x["id"] == _mid48c and x["surfaced"] for x in _thoughts.recent(_uid48, 6)),
      str(_thoughts.recent(_uid48, 6))[:160])

_sch48 = client.get("/v1/schedule").json().get("proactive") or {}
check("日程接口带三道闸门字段",
      {"quiet_start", "quiet_end", "thought_daily_max", "night_silent_now", "dnd"} <= set(_sch48),
      str(sorted(_sch48)))
_cb48 = client.get(f"/v1/console/companion/{_uid48}").json().get("proactive") or {}
check("陪伴层状态能看出她为什么没主动（闸门现状）",
      {"quiet_start", "quiet_end", "dnd", "today_thoughts", "unanswered"} <= set(_cb48),
      str(sorted(_cb48))[:160])
_q48 = client.post("/v1/schedule", json={
    "proactive": {"quiet_start": 22, "quiet_end": 7, "thought_daily_max": 3, "dnd_minutes": 30},
})
_q48p = (_q48.json().get("schedule") or {}).get("proactive") or {}
check("静默时段/念头额度/手动免打扰可改",
      _q48.status_code == 200 and _q48p.get("quiet_start") == 22
      and _q48p.get("thought_daily_max") == 3
      and bool((_q48p.get("dnd") or {}).get("active")), _q48.text[:160])
client.post("/v1/schedule", json={"proactive": {"quiet_start": 23, "quiet_end": 8,
                                               "thought_daily_max": 2, "clear_dnd": True}})

print("[49] prompt 分区显式化：【忽然想起】/【夜里想的】（docs/55）")
from app.mind import prompt as _pm49  # noqa: E402

_rel49 = {"affinity": 60.0, "trust": 40.0, "attraction": 30.0, "aversion": 0.0}
_life49 = {"energy": 70.0, "mood": 0.0, "activity": "发呆", "sleeping": False,
           "virtual_time_human": "下午 3 点 0 分", "hour": 15}
_b49 = _pm49.build(_rt48.cfg, _rt48.persona, relation=_rel49, life=_life49,
                   memories=[], recent_events=[], user_text="在吗",
                   state_hint="你现在有点困。",
                   companion_hint="你忽然想起去年冬天一起看过海",
                   thought_hint="你昨晚一个人时想过想去看海")
check("两个新分区各自成段",
      "【忽然想起】" in _b49.system and "【夜里想的】" in _b49.system)
check("两个新分区计入预算",
      _b49.sections.get("companion", 0) > 0 and _b49.sections.get("thought_surface", 0) > 0)
check("新提示只出现一次，不混进【此刻的状态】",
      _b49.system.count("忽然想起去年冬天") == 1
      and "忽然想起去年冬天" not in _b49.system.split("【此刻的状态】")[-1],
      _b49.system[-200:])
_b49e = _pm49.build(_rt48.cfg, _rt48.persona, relation=_rel49, life=_life49,
                    memories=[], recent_events=[], user_text="在吗")
check("不传就完全不出段（向后兼容）",
      "【忽然想起】" not in _b49e.system and "【夜里想的】" not in _b49e.system
      and _b49e.sections.get("companion") == 0
      and _b49e.sections.get("thought_surface") == 0)

print("[50] 白天碎片独处：白天也能闪一下，但不抢夜里的额度")
from app.core import db as _db50  # noqa: E402
from app.core import memengine as _me50  # noqa: E402

_th50 = _thoughts
_uid50 = int(_users48.ensure_user("980050", "白天碎片")["id"])
_h2.add("fact", "她说想去看一次海，后来一直没去", importance=0.9, cues=["海", "想去"],
        user_id=_uid50, chat_type="private")
_rt50 = get_runtime()

# 时刻全部相对"今天"造：注入 now，不依赖跑测试的钟点
_mid50 = _dt.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)


def _at50(hour: int, minute: int = 0) -> int:
    return int((_mid50 + _td48(hours=hour, minutes=minute)).timestamp())


_today50 = _dt.fromtimestamp(_at50(10, 0)).strftime("%Y-%m-%d")
_count_key50 = f"idle_fragment_count:{_uid50}:{_today50}"


def _reset50() -> None:
    """把白天/夜里的状态都清成"可以想"，每段断言自己造前提，互不串味。"""
    _db50.kv_set("idle_fragment_last", "0")
    _db50.kv_set("idle_fragment_enabled", "1")
    _db50.kv_set("idle_fragment_interval_hours", "4")
    _db50.kv_set("idle_fragment_daily_max", "2")
    _db50.kv_set(_count_key50, "0")
    _db50.kv_set("idle_thought_last", "0")


# smoke 头部把 AI_LIFE_IDLE 关了（别让主链后台任务干扰计数），测窗口时开回来
_old_idle50 = os.environ.get("AI_LIFE_IDLE")
os.environ["AI_LIFE_IDLE"] = "1"

_reset50()
_ok50a = _th50.should_try(_at50(10, 0))
_db50.kv_set("idle_fragment_last", str(_at50(10, 0)))
_ok50b = _th50.should_try(_at50(11, 0))
_ok50c = _th50.should_try(_at50(14, 30))
_db50.kv_set("idle_fragment_enabled", "0")
_ok50g = _th50.should_try(_at50(16, 0))
_db50.kv_set("idle_fragment_enabled", "1")
check("白天窗口：10 点该问、11 点间隔内不问、14:30 过了 4 小时又问；整段关掉就不问",
      _ok50a is True and _ok50b is False and _ok50c is True and _ok50g is False,
      f"{_ok50a}/{_ok50b}/{_ok50c}/{_ok50g}")

_reset50()
_ok50d = _th50.should_try(_at50(23, 30))
_ok50e = _th50.should_try(_at50(7, 30))
_ok50f = _th50.should_try(_at50(22, 15))
check("夜里窗口照旧（23:30 问；07:30、22:15 不问）",
      _ok50d is True and _ok50e is False and _ok50f is False,
      f"{_ok50d}/{_ok50e}/{_ok50f}")

_reset50()
_r50a = _asyncio.run(
    _th50.maybe_ruminate(_rt50, _rt50.persona, _uid50, force=True, now=_at50(10, 0))
)
check("白天能想出一条碎片（force + 注入时刻，不靠真实钟点）",
      bool(_r50a) and _r50a.get("ok") is True and _r50a.get("fragment") is True
      and int(_r50a.get("item_id") or 0) > 0, str(_r50a))
_item50 = (_me50.store().get_item(int(_r50a["item_id"]))
           if _r50a and _r50a.get("ok") else None)
_meta50 = dict(getattr(_item50, "meta", None) or {}) if _item50 is not None else {}
check("碎片 kind=thought 且带 fragment/day_part（10 点 → morning；中午/下午也分档）",
      _item50 is not None and str(_item50.kind) == "thought"
      and _meta50.get("fragment") is True and _meta50.get("day_part") == "morning"
      and _th50._day_part(12) == "noon" and _th50._day_part(16) == "afternoon",
      f"{_meta50} | {_th50._day_part(12)}/{_th50._day_part(16)}")

_db50.kv_set("idle_fragment_daily_max", "1")   # 今天已经写过 1 条，额度立刻收紧
_r50b = _asyncio.run(
    _th50.maybe_ruminate(_rt50, _rt50.persona, _uid50, force=True, now=_at50(15, 0))
)
_db50.kv_set("idle_fragment_daily_max", "2")
check("白天额度用完就不再想（改成 1 条后第二次被拦住）",
      bool(_r50b) and _r50b.get("ok") is False and "额度" in str(_r50b.get("reason")),
      str(_r50b))

_cm50 = _th50.companion.get(str(_uid50))
_night50 = int(_cm50.thoughts.recent_count(user_id=str(_uid50), now=_at50(23, 30)))
_day50 = _th50._fragment_count(str(_uid50), _at50(15, 0))
check("白天碎片不吃夜里的 3 条/24h（夜里计数仍是 0，白天记了 1 条）",
      _night50 == 0 and _day50 == 1, f"夜里 {_night50} / 白天 {_day50}")

_db50.kv_set("idle_thought_last", "0")
_r50c = _asyncio.run(
    _th50.maybe_ruminate(_rt50, _rt50.persona, _uid50, force=True, now=_at50(23, 30))
)
_night50b = int(_cm50.thoughts.recent_count(user_id=str(_uid50), now=_at50(23, 30)))
_day50b = _th50._fragment_count(str(_uid50), _at50(23, 30))
check("夜里照样能想自己的额度，且不回灌白天计数",
      bool(_r50c) and _r50c.get("ok") is True and not _r50c.get("fragment")
      and _night50b == 1 and _day50b == _day50, str(_r50c))

_uid50e = int(_users48.ensure_user("980051", "白天无料")["id"])
_db50.kv_set("idle_fragment_last", "0")
_r50d = _asyncio.run(
    _th50.maybe_ruminate(_rt50, _rt50.persona, _uid50e, force=True, now=_at50(12, 0))
)
check("没有可依据的记录就不写（force 下也返回 ok False）",
      bool(_r50d) and _r50d.get("ok") is False, str(_r50d))

if _old_idle50 is None:
    os.environ.pop("AI_LIFE_IDLE", None)
else:
    os.environ["AI_LIFE_IDLE"] = _old_idle50

print("[51] 人类日常数据接管（默认关）")
from datetime import datetime as _dt51  # noqa: E402

from app.core import db as _db51  # noqa: E402
from app.life import clock as _clock51  # noqa: E402
from app.life import human_life as _hl51  # noqa: E402

# 临时语料：一份工作日作息，00:00-23:00 平铺，23:00-24:00 故意留条缝测"落空"
_hl_dir51 = TMP / "human_life" / "_derived"
_hl_dir51.mkdir(parents=True, exist_ok=True)
(_hl_dir51 / "rhythm.workday.jsonl").write_text(
    json.dumps(
        {
            "id": "smoke_workday",
            "kind": "rhythm",
            "day_type": "workday",
            "synthetic": True,
            "segments": [
                {"start": "00:00", "end": "07:00", "label": "sleep"},
                {"start": "07:00", "end": "18:00", "label": "work"},
                {"start": "18:00", "end": "23:00", "label": "leisure"},
            ],
        },
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
(_hl_dir51 / "activity.jsonl").write_text(
    json.dumps({"id": "a1", "kind": "activity", "activity_type": "sleep"}, ensure_ascii=False) + "\n"
    + json.dumps({"id": "a2", "kind": "activity", "activity_type": "work"}, ensure_ascii=False) + "\n",
    encoding="utf-8",
)

# 环境变量在 load() 里现读，所以设完环境再 reset_cache 就行（不用重进模块）
_old_hl_dir51 = os.environ.get("AI_LIFE_HUMAN_LIFE_DIR")
os.environ["AI_LIFE_HUMAN_LIFE_DIR"] = str(_hl_dir51)
_hl51.reset_cache()
_hlst51 = _hl51.status()
_wed51 = _dt51(2026, 3, 4, 3, 0)      # 周三 → 工作日
_sat51 = _dt51(2026, 3, 7, 3, 0)      # 周六 → 休息日
check("临时语料读得到（ok / 1 份作息 / 2 条活动 / 0 坏行）；默认关；周末判休息日",
      _hlst51.get("ok") is True and _hlst51.get("rhythms") == 1
      and _hlst51.get("activities") == 2 and int(_hlst51.get("errors") or 0) == 0
      and str(_hlst51.get("dir")) == str(_hl_dir51)
      and _hl51.enabled() is False and _hlst51.get("enabled") is False
      and _hl51.day_type_for(_wed51) == "workday" and _hl51.day_type_for(_sat51) == "restday",
      str(_hlst51)[:220])

_r51a = _hl51.resolve(_dt51(2026, 3, 4, 3, 0))
_r51b = _hl51.resolve(_dt51(2026, 3, 4, 10, 30))
check("03:00 → 睡着了（sleeping、精力 10）；10:30 → 在上班",
      bool(_r51a) and _r51a.get("activity") == "睡着了" and _r51a.get("sleeping") is True
      and float(_r51a.get("energy")) == 10.0
      and bool(_r51b) and _r51b.get("activity") == "在上班"
      and _r51b.get("sleeping") is False and float(_r51b.get("energy")) == 55.0,
      f"{_r51a} | {_r51b}")

_r51c = _hl51.resolve(_dt51(2026, 3, 4, 23, 30))   # 落在那条缝里
_r51d = _hl51.resolve(_dt51(2026, 3, 7, 3, 0))     # 周六没休息日档
check("23:30 落缝 → None；休息日没档退回工作日",
      _r51c is None and bool(_r51d) and _r51d.get("day_type") == "workday"
      and _r51d.get("source") == "smoke_workday",
      f"{_r51c} | {_r51d}")

# clock 接管：把 now() 钉到 10:30，开/关各读一次（不动 get_state 签名）
_rt51 = get_runtime()
_orig_now51 = _clock51.now
_clock51.now = lambda: _dt51(2026, 3, 4, 10, 30)
try:
    _base51 = _clock51.get_state(_rt51.persona)   # 默认关：老逻辑
    _hl51.set_enabled(True)
    _on51 = _clock51.get_state(_rt51.persona)
    _hl51.set_enabled(False)
    _off51 = _clock51.get_state(_rt51.persona)
    _db51.kv_set("dev_awake", "1")               # 开发者让它醒着，语料不许盖
    _hl51.set_enabled(True)
    _awake51 = _clock51.get_state(_rt51.persona)
finally:
    _db51.kv_set("dev_awake", "0")
    _hl51.set_enabled(False)
    _clock51.now = _orig_now51

check("开开关后 clock 用人类语料（映射成中文、human_life 有值、不是原槽位文案）",
      _on51.get("activity") == "在上班" and bool(_on51.get("human_life"))
      and (_on51.get("human_life") or {}).get("label") == "work"
      and _on51.get("activity") != _base51.get("activity"),
      f"{_on51.get('activity')} | base={_base51.get('activity')} | {_on51.get('human_life')}")

check("关开关后回到 persona 槽位文案、human_life 为空",
      _off51.get("activity") == _base51.get("activity") and _off51.get("human_life") is None,
      f"{_off51.get('activity')} | base={_base51.get('activity')} | {_off51.get('human_life')}")

check("dev_awake 仍最优先：开着开关也保持「清醒着（开发者）」、不睡",
      _awake51.get("activity") == "清醒着（开发者）"
      and _awake51.get("sleeping") is False and _awake51.get("human_life") is None,
      f"{_awake51.get('activity')} / {_awake51.get('sleeping')}")

# 坏语料 / 目录不存在：status ok=False，clock 不抛、回退老逻辑
_bad51 = TMP / "human_life_bad" / "_derived"
_bad51.mkdir(parents=True, exist_ok=True)
(_bad51 / "rhythm.workday.jsonl").write_text("这不是 json\n{坏的\n", encoding="utf-8")
os.environ["AI_LIFE_HUMAN_LIFE_DIR"] = str(_bad51)
_hl51.reset_cache()
_badst51 = _hl51.status()
os.environ["AI_LIFE_HUMAN_LIFE_DIR"] = str(TMP / "human_life_none" / "_derived")
_hl51.reset_cache()
_nodirst51 = _hl51.status()
_clock51.now = lambda: _dt51(2026, 3, 4, 10, 30)
try:
    _hl51.set_enabled(True)
    _bad51state = _clock51.get_state(_rt51.persona)   # 坏语料下也得返回
finally:
    _hl51.set_enabled(False)
    _clock51.now = _orig_now51
check("坏 jsonl / 目录不存在 → status ok=False；clock 不抛且回退槽位文案",
      _badst51.get("ok") is False and int(_badst51.get("errors") or 0) >= 1
      and _nodirst51.get("ok") is False
      and _bad51state.get("activity") == _base51.get("activity")
      and _bad51state.get("human_life") is None,
      f"{_badst51.get('ok')}/{_badst51.get('errors')} | {_nodirst51.get('ok')} | "
      f"{_bad51state.get('activity')}")

# 接口层：GET 只增键；POST 单独给 human_life 也不 400，true 开、false 关回去
_sch51 = client.get("/v1/schedule").json()
_hl_get51 = _sch51.get("human_life") or {}
_r51e = client.post("/v1/schedule", json={"human_life": True})
_hl_on51 = (_r51e.json().get("schedule") or {}).get("human_life") or {}
_kv_on51 = _hl51.enabled()
_r51f = client.post("/v1/schedule", json={"human_life": False})
_hl_off51 = (_r51f.json().get("schedule") or {}).get("human_life") or {}
check("日程接口：GET 带 human_life；POST true 开、POST false 关（不传 proactive 也不 400）",
      "human_life" in _sch51 and "enabled" in _hl_get51
      and bool(_sch51.get("now")) and bool(_sch51.get("table"))
      and _r51e.status_code == 200 and _hl_on51.get("enabled") is True and _kv_on51 is True
      and _r51f.status_code == 200 and _hl_off51.get("enabled") is False
      and _hl51.enabled() is False,
      f"{_r51e.status_code}/{_hl_on51.get('enabled')}→{_r51f.status_code}/{_hl_off51.get('enabled')}")

# 收尾：环境变量和开关都还原，别污染（也没有后段了，但规矩要立住）
if _old_hl_dir51 is None:
    os.environ.pop("AI_LIFE_HUMAN_LIFE_DIR", None)
else:
    os.environ["AI_LIFE_HUMAN_LIFE_DIR"] = _old_hl_dir51
_hl51.reset_cache()
_hl51.set_enabled(False)

print("[52] 拟人作息展示接口")
# 复用 [51] 建的临时语料；把 now_local 钉到周三 10:30，别让跑测试的钟点决定答案
_old_dir52 = os.environ.get("AI_LIFE_HUMAN_LIFE_DIR")
os.environ["AI_LIFE_HUMAN_LIFE_DIR"] = str(_hl_dir51)
_hl51.reset_cache()
_orig_nowlocal52 = _hl51.now_local
_hl51.now_local = lambda: _dt51(2026, 3, 4, 10, 30)
try:
    _hl51.set_enabled(True)
    _r52a = client.get("/v1/human_life/now")
    _now52 = _r52a.json()
    _keys52 = {"enabled", "ok", "ts", "day_type", "activity", "energy", "sleeping",
               "label", "source", "segment", "seconds_left", "corpus"}
    check("/v1/human_life/now 200、12 个键齐、segment 是 {start,end}、ts 带 +08:00",
          _r52a.status_code == 200 and _keys52 <= set(_now52)
          and isinstance(_now52.get("segment"), dict)
          and {"start", "end"} <= set(_now52["segment"])
          and str(_now52.get("ts") or "").endswith("+08:00")
          and _now52.get("day_type") in ("workday", "restday"),
          str(_now52)[:220])

    check("10:30 语义：在上班 / 精力 55 / 没睡 / 距 18:00 还有 27000 秒 / 语料 1 档 2 条",
          _now52.get("enabled") is True and _now52.get("ok") is True
          and _now52.get("activity") == "在上班" and float(_now52.get("energy")) == 55.0
          and _now52.get("sleeping") is False
          and (_now52.get("segment") or {}).get("start") == "07:00"
          and (_now52.get("segment") or {}).get("end") == "18:00"
          and int(_now52.get("seconds_left")) == 27000
          and (_now52.get("corpus") or {}).get("rhythms") == 1
          and (_now52.get("corpus") or {}).get("activities") == 2,
          f"{_now52.get('activity')}/{_now52.get('seconds_left')}/{_now52.get('corpus')}")

    _r52c = client.get("/v1/board")
    _b52 = _r52c.json()
    check("board 带 human_life 卡（同一份结构），life 段原样保留",
          _r52c.status_code == 200 and isinstance(_b52.get("human_life"), dict)
          and _b52["human_life"].get("activity") == "在上班"
          and isinstance(_b52["human_life"].get("corpus"), dict)
          and isinstance(_b52.get("life"), dict) and bool(_b52["life"].get("activity")),
          str(_b52.get("human_life"))[:200])

    _hl51.set_enabled(False)
    _r52b = client.get("/v1/human_life/now")
    _off52 = _r52b.json()
    _hl51.set_enabled(True)   # 按新要求：测完再开回来
    check("关开关后 /now 仍 200：enabled=false、ok=false、activity/segment 为空、照报语料健康度",
          _r52b.status_code == 200 and _off52.get("enabled") is False
          and _off52.get("ok") is False and _off52.get("activity") is None
          and _off52.get("segment") is None and int(_off52.get("seconds_left") or 0) == 0
          and (_off52.get("corpus") or {}).get("rhythms") == 1
          and _hl51.enabled() is True,
          str(_off52)[:220])
finally:
    _hl51.now_local = _orig_nowlocal52
    if _old_dir52 is None:
        os.environ.pop("AI_LIFE_HUMAN_LIFE_DIR", None)
    else:
        os.environ["AI_LIFE_HUMAN_LIFE_DIR"] = _old_dir52
    _hl51.reset_cache()
    _hl51.set_enabled(True)   # 展示接口已上线：收尾保持开着（真库由 lead 管）

print("[53] 她的工作日 v2 + 日型覆盖")
# 临时语料：stat 主档排在文件前面，user_ad_day_a 排后面 → 只要选中 user_ 就证明是"优先级"生效
_v2dir53 = TMP / "human_life_v2" / "_derived"
_v2dir53.mkdir(parents=True, exist_ok=True)
(_v2dir53 / "rhythm.workday.jsonl").write_text(
    json.dumps({"id": "stat_workday_average", "kind": "rhythm", "day_type": "workday",
                "synthetic": True,
                "segments": [{"start": "00:00", "end": "07:00", "label": "sleep"},
                             {"start": "07:00", "end": "18:00", "label": "work"},
                             {"start": "18:00", "end": "24:00", "label": "leisure"}]},
               ensure_ascii=False) + "\n"
    + json.dumps({"id": "user_ad_day_a", "kind": "rhythm", "day_type": "workday",
                  "synthetic": True,
                  "segments": [{"start": "00:00", "end": "07:30", "label": "sleep"},
                               {"start": "07:30", "end": "09:00", "label": "selfcare"},
                               {"start": "09:00", "end": "12:00", "label": "work"},
                               {"start": "12:00", "end": "13:00", "label": "meal"},
                               {"start": "13:00", "end": "18:30", "label": "work"},
                               {"start": "18:30", "end": "24:00", "label": "leisure"}]},
                 ensure_ascii=False) + "\n",
    encoding="utf-8",
)
(_v2dir53 / "rhythm.special.jsonl").write_text(
    json.dumps({"id": "user_sick_day", "kind": "rhythm", "day_type": "special",
                "synthetic": True,
                "segments": [{"start": "00:00", "end": "11:00", "label": "sleep"},
                             {"start": "11:00", "end": "12:00", "label": "meal"},
                             {"start": "12:00", "end": "24:00", "label": "other"}]},
               ensure_ascii=False) + "\n",
    encoding="utf-8",
)
(_v2dir53 / "activity.jsonl").write_text(
    json.dumps({"id": "ad1", "kind": "activity", "activity_type": "work",
                "day_type": "workday"}, ensure_ascii=False) + "\n",
    encoding="utf-8",
)

_old_dir53 = os.environ.get("AI_LIFE_HUMAN_LIFE_DIR")
os.environ["AI_LIFE_HUMAN_LIFE_DIR"] = str(_v2dir53)
_hl51.reset_cache()
_hl51.set_force_day_type("")          # 先清覆盖，测"自动"
_wed53 = _dt51(2026, 3, 4, 10, 30)    # 周三
_sat53 = _dt51(2026, 3, 7, 10, 30)    # 周六
try:
    _r53a = _hl51.resolve(_wed53)
    _r53b = _hl51.resolve(_sat53)
    check("工作日 10:30 优先她自己的一天（source=user_ad_day_a，不是排在文件前面的 stat_*）；"
          "周六退工作日也照样优先",
          bool(_r53a) and _r53a.get("source") == "user_ad_day_a"
          and bool(_r53b) and _r53b.get("source") == "user_ad_day_a"
          and _r53b.get("day_type") == "workday",
          f"{_r53a} | {_r53b and {k: _r53b.get(k) for k in ('source', 'day_type')}}")

    _hl51.set_force_day_type("special")
    _hl51.set_enabled(True)
    _r53c = _hl51.resolve(_wed53)
    _snap53 = _hl51.snapshot(_wed53)
    check("force special：10:30 切成生病日（user_sick_day / 睡着 / 精力 10），"
          "snapshot 标 day_type_forced=true",
          bool(_r53c) and _r53c.get("source") == "user_sick_day"
          and _r53c.get("activity") == "睡着了" and float(_r53c.get("energy")) == 10.0
          and _r53c.get("sleeping") is True
          and _snap53.get("day_type") == "special" and _snap53.get("day_type_forced") is True,
          f"{_r53c} | {_snap53.get('day_type')}/{_snap53.get('day_type_forced')}")

    _bad53 = client.post("/v1/schedule", json={"human_life_day_type": "nope"})
    _after_bad53 = _hl51.force_day_type()
    _r53d = client.post("/v1/schedule", json={"human_life_day_type": ""})
    _after_clear53 = _hl51.force_day_type()
    _r53e = _hl51.resolve(_wed53)
    try:
        _hl51.set_force_day_type("nope")
        _module_raised53 = False
    except ValueError:
        _module_raised53 = True
    _hl51.set_force_day_type("")
    check("日型覆盖：非法 POST 400 且不改现状；空串复原自动（10:30 回到 user_ad_day_a）；"
          "模块层也拒绝非法值",
          _bad53.status_code == 400 and _after_bad53 == "special"
          and _r53d.status_code == 200 and _after_clear53 == ""
          and bool(_r53e) and _r53e.get("source") == "user_ad_day_a"
          and ((_r53d.json().get("schedule") or {}).get("human_life") or {})
          .get("force_day_type") == ""
          and _module_raised53 is True,
          f"{_bad53.status_code}/{_after_bad53}→{_r53d.status_code}/{_after_clear53} | {_r53e and _r53e.get('source')}")

    _sch53 = client.get("/v1/schedule").json()
    _hlstat53 = _sch53.get("human_life") or {}
    _now53 = client.get("/v1/human_life/now").json()
    check("GET /v1/schedule 的 human_life 带 force_day_type；/now 多 day_type_forced（自动时 false）",
          "force_day_type" in _hlstat53 and _hlstat53.get("force_day_type") == ""
          and "day_type_forced" in _now53 and _now53.get("day_type_forced") is False
          and _now53.get("enabled") is True and isinstance(_sch53.get("now"), dict),
          f"{_hlstat53.get('force_day_type')} | {_now53.get('day_type_forced')}")
finally:
    _hl51.set_force_day_type("")
    if _old_dir53 is None:
        os.environ.pop("AI_LIFE_HUMAN_LIFE_DIR", None)
    else:
        os.environ["AI_LIFE_HUMAN_LIFE_DIR"] = _old_dir53
    _hl51.reset_cache()
    _hl51.set_enabled(True)

_st53 = _hl51.status()
check("清掉环境变量后回到默认语料目录（DATA_ROOT 下），status 照常返回不抛",
      isinstance(_st53, dict) and _st53.get("dir") == str(_hl51.CORPUS_DIR)
      and isinstance(_st53.get("ok"), bool),
      str(_st53)[:200])

print("[54] 她的一天 v3：今天进 prompt + 自述轮换 + 活动文案")
import json as _json54  # noqa: E402
import tempfile as _tmp54  # noqa: E402

from app.mind import prompt as _pm54  # noqa: E402


def _rhythm54(rid: str, day: str) -> dict:
    segs = [
        {"start": "00:00", "end": "07:00", "label": "sleep"},
        {"start": "07:00", "end": "09:00", "label": "selfcare"},
        {"start": "09:00", "end": "12:00", "label": "work"},
        {"start": "12:00", "end": "13:00", "label": "meal"},
        {"start": "13:00", "end": "19:00", "label": "work"},
        {"start": "19:00", "end": "21:00", "label": "screen"},
        {"start": "21:00", "end": "24:00", "label": "sleep"},
    ]
    return {"id": rid, "kind": "rhythm", "day_type": day, "synthetic": True, "segments": segs}


_dir54 = pathlib.Path(_tmp54.mkdtemp(prefix="ai-life-hl54-"))
(_dir54 / "rhythm.workday.jsonl").write_text(
    "\n".join(_json54.dumps(_rhythm54(rid, "workday"), ensure_ascii=False)
              for rid in ("stat_x", "user_ad_day_a", "user_ad_day_b", "user_ad_day_c")) + "\n",
    encoding="utf-8",
)
(_dir54 / "activity.jsonl").write_text("", encoding="utf-8")
_old_dir54 = os.environ.get("AI_LIFE_HUMAN_LIFE_DIR")
os.environ["AI_LIFE_HUMAN_LIFE_DIR"] = str(_dir54)
_hl51.reset_cache()
_hl51.set_force_day_type("")
_old_enabled54 = _db48.kv_get("human_life_enabled", "0")
try:
    _db48.kv_set("human_life_enabled", "0")
    check("关着时不注入【你今天】", _hl51.today_line() == "")
    _db48.kv_set("human_life_enabled", "1")
    _day54 = _dt(2026, 3, 4, 10, 30).astimezone()
    _line54 = _hl51.today_line(_day54)
    check("开着时能给一句她今天在干嘛",
          bool(_line54) and "今天是" in _line54
          and any(word in _line54 for word in _hl51.ACTIVITY_PHRASES["work"]),
          _line54[:140])
    _hit54 = _hl51.resolve(_day54)
    check("活动有她自己的说法（不是枚举文案）",
          bool(_hit54) and bool(_hit54.get("phrase"))
          and _hit54["phrase"] in _hl51.ACTIVITY_PHRASES["work"], str(_hit54)[:120])
    _hit54b = _hl51.resolve(_day54 + _td48(days=1))
    check("工作日自述按日期轮换",
          bool(_hit54b) and _hit54["source"] != _hit54b["source"],
          f"{_hit54['source']} vs {_hit54b['source']}")
    _b54 = _pm54.build(_rt48.cfg, _rt48.persona, relation=_rel49, life=_life49,
                       memories=[], recent_events=[], user_text="在吗",
                       today_hint="今天是工作日，你现在的状态：在改稿。")
    check("prompt 有【你今天】段且单独计预算",
          "【你今天】" in _b54.system and int(_b54.sections.get("today") or 0) > 0,
          str(_b54.sections.get("today")))
    _b54b = _pm54.build(_rt48.cfg, _rt48.persona, relation=_rel49, life=_life49,
                        memories=[], recent_events=[], user_text="在吗")
    check("不传 today_hint 时完全不出段",
          "【你今天】" not in _b54b.system
          and int(_b54b.sections.get("today") or 0) == 0)
finally:
    _db48.kv_set("human_life_enabled", _old_enabled54)
    if _old_dir54 is None:
        os.environ.pop("AI_LIFE_HUMAN_LIFE_DIR", None)
    else:
        os.environ["AI_LIFE_HUMAN_LIFE_DIR"] = _old_dir54
    _hl51.reset_cache()

print("[55] 她先开口：用今天的生活当话题（没有旧事可说时）")
import app.life.proactive as _pro55  # noqa: E402

_old_enabled55 = _db48.kv_get("human_life_enabled", "0")
_old_dir55 = os.environ.get("AI_LIFE_HUMAN_LIFE_DIR")
_user55 = _users48.ensure_user("980055", "今天开口")
_uid55 = int(_user55["id"])
_users48.update_relation(_uid55, affinity=100)
_say48("两天前聊过一次", uid=_uid55,
       at=(_dt.now().astimezone() - _td48(days=2)))
os.environ["AI_LIFE_HUMAN_LIFE_DIR"] = str(_dir54)
_hl51.reset_cache()
_hl51.set_enabled(True)
_hl51.set_force_day_type("workday")
_now55 = _dt.now().astimezone().replace(hour=10, minute=30, second=0, microsecond=0)


async def _no_speak55(*_args, **_kwargs) -> str:
    return ""


try:
    _dec55 = _pro55.decide(_rt48, _rt48.persona, _uid55, now=_now55)
    check("没有旧事时用今天的生活当话题",
          _dec55.get("ok") is True and _dec55.get("kind") == "day"
          and bool((_dec55.get("item") or {}).get("phrase")),
          str(_dec55)[:160])
    _real_speak55 = _pro55._speak_day
    _pro55._speak_day = _no_speak55
    _prep55 = _asyncio.run(_pro55.prepare_tick(_rt48, _rt48.persona, _uid55, now=_now55))
    _pro55._speak_day = _real_speak55
    _lines55 = " ".join(_prep55.get("lines") or [])
    check("模型失败也退回模板，且带上她今天的说法",
          _prep55.get("ok") is True and _prep55.get("kind") == "day"
          and any(word in _lines55 for word in _hl51.ACTIVITY_PHRASES["work"]),
          _lines55[:120])
    _evt55 = _pro55.commit_tick(_prep55)
    _src55 = _db48.one(
        "SELECT source FROM events WHERE user_id = ? ORDER BY id DESC LIMIT 1", (_uid55,))
    check("今天的话题记成普通主动消息（不占念头台账）",
          bool(_evt55) and _src55 is not None and str(_src55["source"]) == "proactive"
          and _pro55._user_today_thoughts(_uid55, now=_now55) == 0,
          f"{_evt55} | {dict(_src55) if _src55 else None}")
    _pro55._mark_window(_uid55, 0)
    _db48.kv_set(_pro55._ukey("last_ts", _uid55), "")
    _db48.kv_set(_pro55._ukey("window", _uid55), "")
    _hl51.set_enabled(False)
    _dec55b = _pro55.decide(_rt48, _rt48.persona, _uid55, now=_now55)
    check("关掉语料开关就回到锚点寒暄",
          _dec55b.get("ok") is True and _dec55b.get("kind") == "anchor",
          str(_dec55b)[:120])
finally:
    _db48.kv_set("human_life_enabled", _old_enabled55)
    _hl51.set_force_day_type("")
    if _old_dir55 is None:
        os.environ.pop("AI_LIFE_HUMAN_LIFE_DIR", None)
    else:
        os.environ["AI_LIFE_HUMAN_LIFE_DIR"] = _old_dir55
    _hl51.reset_cache()

print("[56] 语料风格接线：恋人 85/15 语气 + 跟外人打交道规则")
import random as _random56  # noqa: E402
import re as _re56  # noqa: E402

from app.memory import style as _style56  # noqa: E402
from app.mind import loop as _loop56  # noqa: E402

_style_dir56 = pathlib.Path(_tmp54.mkdtemp(prefix="ai-life-style56-"))


def _snippet56(sid: str, relation: str, note: str, pairs: list) -> dict:
    return {
        "id": sid, "kind": "snippet", "day_type": "workday", "relation": relation,
        "context": "线上私聊", "interrupt_policy": "allow",
        "messages": [{"role": role, "text": text} for role, text in pairs],
        "note": note,
    }


_style_rows56 = [
    _snippet56("p_love1", "partner", "来源：用户提供的相爱语料第 001 段",
               [("self", "mua"), ("other", "亲亲")]),
    _snippet56("p_love2", "partner", "来源：用户提供的相爱语料第 002 段",
               [("self", "要抱抱"), ("other", "过来")]),
    _snippet56("p_love3", "partner", "来源：用户提供的相爱语料第 003 段",
               [("self", "今天也想你"), ("other", "我也是")]),
    _snippet56("p_love4", "partner", "来源：用户提供的相爱语料第 004 段",
               [("self", "记得吃饭"), ("other", "知道啦")]),
    _snippet56("p_love5", "partner", "来源：用户提供的相爱语料第 005 段",
               [("self", "晚安宝宝"), ("other", "晚安")]),
    _snippet56("p_fight1", "partner", "女主场景语料 场景 0001（冲突样本，占比受控）",
               [("self", "我现在不想说话"), ("other", "别生气")]),
    # 显式性内容：即使 note 标了相爱语料，也不许进 prompt（审计 S2 的第二道闸）
    _snippet56("p_explicit", "partner", "来源：用户提供的相爱语料第 006 段",
               [("self", "大宝贝又大又粗"), ("other", "爱你")]),
    # 谐音擦边（审计 S2 复核：012 段换出 好涩/爆炒）
    _snippet56("p_harm", "partner", "来源：用户提供的相爱语料第 007 段",
               [("self", "好涩"), ("other", "爆炒")]),
    # 两侧都满 40 字：检验总长封顶与引号闭合（审计 S4）
    _snippet56("p_long", "partner", "来源：用户提供的相爱语料第 008 段",
               [("self", "一" * 40), ("other", "二" * 40)]),
]
(_style_dir56 / "partner.snippet.jsonl").write_text(
    "\n".join(_json54.dumps(row, ensure_ascii=False) for row in _style_rows56) + "\n",
    encoding="utf-8",
)
(_style_dir56 / "stranger.snippet.jsonl").write_text(
    _json54.dumps(_snippet56("s_tr1", "stranger", "来源：CrossWOZ 出租 对话（Apache-2.0）",
                            [("self", "师傅到哪了"), ("other", "马上到")]), ensure_ascii=False) + "\n",
    encoding="utf-8",
)
_old_style_dir56 = os.environ.get("AI_LIFE_STYLE_DIR")
_old_awake56 = _db48.kv_get("dev_awake", "0")
os.environ["AI_LIFE_STYLE_DIR"] = str(_style_dir56)
_db48.kv_set("dev_awake", "1")   # 夜间跑 smoke 时别让她"睡着不回"，否则根本走不到 build
_style56.clear_cache()
try:
    _snap56 = _style56.snapshot()
    check("风格层能读到伴侣语料（原始 8 / 可用 6）",
          _snap56.get("ok") is True and int(_snap56.get("partner_love") or 0) == 8
          and int(_snap56.get("partner_fight") or 0) == 1
          and int(_snap56.get("partner_love_usable") or 0) == 6,
          str(_snap56)[:200])
    _long_row56 = [row for row in _style_rows56 if row["id"] == "p_long"][0]
    _long_line56 = _style56._format_line(_long_row56, fight=False)
    check("两侧都满 40 时总长封顶 80、引号闭合、省略号收尾",
          len(_long_line56) <= 80 and _long_line56.endswith("…")
          and _long_line56.count("「") == _long_line56.count("」"),
          f"{len(_long_line56)} | {_long_line56}")
    _ex56 = _style56.partner_examples(3, rng=_random56.Random(11))
    _love_lines56 = {
        _style56._format_line(row, fight=False)
        for row in _style_rows56
        if "相爱语料" in row["note"] and not _style56._has_explicit(row)
    }
    check("语气样本只给短句、不成段、没有分词空格、且甜样本只来自可用 love 池",
          len(_ex56) >= 1
          and all(len(x) <= 90 for x in _ex56)
          and not any(_re56.search(r"[\u4e00-\u9fff]\s+[\u4e00-\u9fff]", x) for x in _ex56)
          and all(x in _love_lines56 or "偶尔闹别扭" in x for x in _ex56),
          str(_ex56)[:160])
    _explicit_leak56 = any(
        word in x
        for _ in range(80)
        for x in _style56.partner_examples(3, rng=_random56.Random(_))
        for word in ("大又粗", "好涩", "爆炒", "香草泥")
    )
    check("显式/谐音样本整条不进 prompt（第二道闸）", _explicit_leak56 is False)
    _fights56 = sum(
        1 for _ in range(200)
        if any("偶尔闹别扭" in x for x in _style56.partner_examples(1, rng=_random56.Random(_)))
    )
    check("恋人样本按 85/15 抽（独立 15% 权重，200 次落在 5–30%）",
          10 <= _fights56 <= 60 and abs(_style56.LOVE_WEIGHT - 0.85) < 1e-9,
          f"fight={_fights56}/200 weight={_style56.LOVE_WEIGHT}")
    check("跟外人规则含降火/不硬刚/客服申诉；客服腔有降权说明",
          all(word in _style56.stranger_rules() for word in ("降火", "硬刚", "客服"))
          and "稍后" in _style56.service_note(),
          _style56.stranger_rules()[:80])
    _b56 = _pm54.build(_rt48.cfg, _rt48.persona, relation=_rel49, life=_life49,
                       memories=[], recent_events=[], user_text="在吗",
                       partner_hint=_ex56[0], stranger_hint=_style56.stranger_rules())
    check("prompt 有【你们以前的语气】/【跟外人打交道】两段且计预算",
          "【你们以前的语气】" in _b56.system and "【跟外人打交道】" in _b56.system
          and int(_b56.sections.get("partner") or 0) > 0
          and int(_b56.sections.get("stranger") or 0) > 0,
          str({k: _b56.sections.get(k) for k in ("partner", "stranger")}))
    _lover56 = _users48.ensure_user("980056", "恋人测试")
    _uid56 = int(_lover56["id"])
    _users48.update_relation(_uid56, affinity=100)
    _rt48.last_prompt = {}
    _asyncio.run(_loop56.chat_turn(_rt48, "恋人暗号四五六", user_id=_uid56,
                                   chat_type="private", is_lover=True))
    _lp56 = _rt48.last_prompt or {}
    _sys56 = str(_lp56.get("system") or "")
    _msgs56 = _lp56.get("messages") or []
    check("恋人私聊真正注入【你们以前的语气】（并确认是本次这一轮）",
          "【你们以前的语气】" in _sys56
          and bool(_msgs56) and "恋人暗号四五六" in str(_msgs56[-1].get("content") or ""),
          str(_msgs56[-1])[:80])
    check("恋人私聊不夹带外人规则（隐私不变量）", "【跟外人打交道】" not in _sys56)
    # 面板 /v1/chat：chat_type 空（保留摘要/群聊上下文），但语气层要生效、且不能同时塞外人规则
    _rt48.last_prompt = {}
    client.post("/v1/chat", json={"text": "面板暗号七八九", "user_id": 0})
    _lp_route56 = client.get("/v1/inspect").json() or {}
    _sys_route56 = str(_lp_route56.get("system") or "")
    _msgs_route56 = _lp_route56.get("messages") or []
    check("面板 /v1/chat 拿到恋人语气、且不带外人规则",
          "【你们以前的语气】" in _sys_route56
          and "【跟外人打交道】" not in _sys_route56
          and bool(_msgs_route56)
          and "面板暗号七八九" in str(_msgs_route56[-1].get("content") or ""),
          str(_msgs_route56[-1])[:80] if _msgs_route56 else "")
    _stranger56 = _users48.ensure_user("980057", "陌生人测试")
    _uid56b = int(_stranger56["id"])
    _rt48.last_prompt = {}
    _asyncio.run(_loop56.chat_turn(_rt48, "陌生人暗号一二三", user_id=_uid56b,
                                   chat_type="private"))
    _lp56b = _rt48.last_prompt or {}
    _sys56b = str(_lp56b.get("system") or "")
    _msgs56b = _lp56b.get("messages") or []
    check("跟不熟的人聊天真正注入【跟外人打交道】（本次这一轮）",
          "【跟外人打交道】" in _sys56b
          and bool(_msgs56b) and "陌生人暗号一二三" in str(_msgs56b[-1].get("content") or ""),
          str(_msgs56b[-1])[:80] if _msgs56b else "")
    _rt48.last_prompt = {}
    _asyncio.run(_loop56.chat_turn(_rt48, "群聊暗号零一二", user_id=_uid56,
                                   chat_type="group", is_lover=True))
    _lp_g56 = _rt48.last_prompt or {}
    _sys_g56 = str(_lp_g56.get("system") or "")
    _msgs_g56 = _lp_g56.get("messages") or []
    check("群聊不泄露恋人语气、但有外人规则（本次这一轮）",
          "【你们以前的语气】" not in _sys_g56 and "【跟外人打交道】" in _sys_g56
          and bool(_msgs_g56) and "群聊暗号零一二" in str(_msgs_g56[-1].get("content") or ""),
          str(_msgs_g56[-1])[:80] if _msgs_g56 else "")
    os.environ["AI_LIFE_STYLE_DIR"] = str(_style_dir56 / "missing")
    _style56.clear_cache()
    check("语料没了也不崩：返回空样本、规则照给",
          _style56.partner_examples(2) == [] and bool(_style56.stranger_rules()))
finally:
    _db48.kv_set("dev_awake", _old_awake56)
    if _old_style_dir56 is None:
        os.environ.pop("AI_LIFE_STYLE_DIR", None)
    else:
        os.environ["AI_LIFE_STYLE_DIR"] = _old_style_dir56
    _style56.clear_cache()

print()
print(f"结果：{ok} 通过，{fail} 失败")
sys.exit(0 if fail == 0 else 1)
