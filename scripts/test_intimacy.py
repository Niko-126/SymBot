import sys, os, tempfile, pathlib

os.environ["AI_LIFE_DB"] = str(pathlib.Path(tempfile.mkdtemp()) / "smoke-i.db")
os.environ["AI_LIFE_MOCK"] = "1"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app)
from app.core import runtime
runtime.init()
from app.core import db
from app.intimacy import exemplars as iex
from app.intimacy import gate as igate
from app.intimacy import memory as imem
from app.intimacy import mode as im
from app.intimacy import prompt as iprompt
from app.intimacy.mode import Mode
from app.memory import observe
from app.mind import models
from app.mind import prompt as prompt_mod
from app.relation import engine, users
from app.relation import session as session_mod

# 测试必须在"她醒着"的固定状态跑，否则深夜第一条不回会让断言偶发失败
from app.life import clock as clock_mod
clock_mod.get_state = lambda persona: {
    "hour": 14, "sleeping": False, "energy": 85, "mood": 0,
    "activity": "在工位摸鱼", "social_battery": 70,
}

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS {name}")
    else:
        fail += 1
        print(f"  FAIL {name} {detail}")


print("[25] 亲密模式系统 · 意图 / 状态机")
check("explicit意图识别", im.detect_intent("我好想操你啊") == "explicit")
check("suggestive意图识别", im.detect_intent("欧金金") == "suggestive")
check("stops意图识别", im.detect_intent("不色了正常聊") == "stop")
check("普通消息none", im.detect_intent("你吃了吗") == "none")

im.set_mode(99, Mode.OFF)
m1, _ = im.transition(99, "suggestive", is_private=True, is_lover=True)
check("私聊恋人→暗示", m1 == Mode.SUGGESTIVE)
m2, _ = im.transition(99, "explicit", is_private=True, is_lover=True)
check("私聊恋人→直白", m2 == Mode.EXPLICIT)
im.set_mode(99, Mode.OFF)
m3, note = im.transition(99, "explicit", is_private=True, is_lover=False)
check("非恋人+性意图→调侃", note == "not_lover_deflect")
im.set_mode(99, Mode.OFF)
m4, _ = im.transition(99, "explicit", is_private=False, is_lover=True)
check("群聊恋人直白被压回", m4 == Mode.SUGGESTIVE)
im.set_mode(99, Mode.OFF)
m5, _ = im.transition(99, "explicit", is_private=False, is_lover=False)
check("群聊非恋人不开模式", m5 == Mode.OFF)

print("[26] 门控 / 记录 / 标记 / 强制思考")
_u = users.ensure_user("910001", "测试恋人")
uid = int(_u["id"])
users.update_relation(uid, affinity=100)
users.bind_lover(1, uid)

mode_a, _, lover_a, private_a = igate.prepare(uid, "我好想操你啊宝宝", "private")
check("私聊+恋人→直白", mode_a == Mode.EXPLICIT and lover_a and private_a, mode_a.value)
_sessions = imem.recent(uid, 3)
check("私密上下文已记录", bool(_sessions) and len(_sessions[0]["events"]) >= 1, str(_sessions)[:120])

mode_b, note_b, _, _ = igate.prepare(uid, "那就在群里说点特别的", "group")
check("群聊里直白只到暗示", mode_b == Mode.SUGGESTIVE, mode_b.value)

_u2 = users.ensure_user("910002", "测试路人")
uid2 = int(_u2["id"])
mode_c, note_c, _, _ = igate.prepare(uid2, "我好想操你", "private")
check("非恋人→不进入模式只调侃", mode_c == Mode.OFF and note_c == "not_lover_deflect", f"{mode_c.value}/{note_c}")

# 端到端：控制台模拟对话（不推回 QQ）也要走进亲密门控，并给事件打标记
im.set_mode(uid, Mode.OFF)
resp = c.post("/v1/console/send", json={
    "user_id": uid, "text": "欧金金", "as": "me", "chat_type": "private",
}).json()
check("控制台模拟对话回包带模式", resp.get("ok") is True and resp.get("intimate_mode") == "suggestive", str(resp)[:160])
row = db.one(
    "SELECT meta FROM events WHERE role = 'user' AND user_id = ? AND text = ?"
    " ORDER BY id DESC LIMIT 1", (uid, "欧金金"))
check("亲密事件带标记", row is not None and "intimate" in str(row["meta"] or ""), str(dict(row) if row else None))
_sys = str((runtime.get().last_prompt or {}).get("system") or "")
check("亲密提示注入到 prompt", "私密状态" in _sys or "暗示" in _sys, _sys[:80])

# 非恋人调侃拒绝：模式为 OFF 时也必须注入（之前是死分支）
im.set_mode(uid2, Mode.OFF)
c.post("/v1/console/send", json={
    "user_id": uid2, "text": "我好想操你", "as": "me", "chat_type": "private",
}).json()
_sys2 = str((runtime.get().last_prompt or {}).get("system") or "")
check("非恋人调侃拒绝注入", ("不是恋人" in _sys2 and "别把整段对话拒掉" in _sys2), _sys2[:120])

_pick_force = models.pick("chat_default", force_think=True)
_forced_name = models.forced_model()
check("强制深思走指定模型",
      _pick_force.key == ("forced" if _forced_name else "steady"),
      f"{_pick_force.key} / force={_forced_name}")
_bundle = prompt_mod.build(
    runtime.get().cfg, runtime.get().persona,
    relation=engine.get_relation(), life={"hour": 12}, memories=[], recent_events=[],
    user_text="嗯", deep_think=True,
)
check("强制深思写进 prompt", "多想一会儿" in _bundle.system, _bundle.system[:80])

print("[27] 范本库 / 私密记录查询 / 观察来源")
check("范本库已入库", iex.stats()["total"] >= 6, str(iex.stats()))
check("调侃范本含好女孩", any("好女孩" in x for x in iex.by_kind("deflect")), str(iex.by_kind("deflect"))[:80])
check("隐喻范本含棒棒糖", any("棒棒糖" in x for x in iex.by_kind("metaphor")), str(iex.by_kind("metaphor"))[:80])
check("暗示 prompt 用了变体范本", "学历" in iprompt.build_system_prompt(Mode.SUGGESTIVE, True, True, "小鲸"),
      "学历")
check("直白 prompt 限两句", "最多两句" in iprompt.build_system_prompt(Mode.EXPLICIT, True, True, "小鲸"), "两句")
check("群聊 prompt 禁直白词", "绝对不能说直白" in iprompt.build_system_prompt(Mode.SUGGESTIVE, False, True, "小鲸"), "禁")
check("群聊护栏换掉直白词",
      not iprompt.has_explicit_word(iprompt.sanitize_group_reply("我想要你的大鸡巴进来")),
      iprompt.sanitize_group_reply("我想要你的大鸡巴进来"))
check("群聊护栏保留暗示词",
      "大调查" in iprompt.sanitize_group_reply("我想要你的大鸡巴进来"),
      iprompt.sanitize_group_reply("我想要你的大鸡巴进来"))

_iv = c.get(f"/v1/console/intimate/{uid}").json()
check("控制台能看私密记录", int(_iv.get("user_id") or 0) == uid and isinstance(_iv.get("sessions"), list), str(_iv)[:120])
check("控制台能看范本", c.get("/v1/console/intimate/exemplars").json().get("stats", {}).get("total", 0) >= 6)

observe.add_note("说话方式", "他回消息喜欢先打个问号再看", chat_type="group")
_note = [n for n in observe.recent_notes(2, 50) if n["content"].startswith("他回消息")]
check("观察带来源场合", bool(_note) and _note[0].get("chat_type") == "group", str(_note)[:120])

_login = c.get("/v1/console/onebot/login").json()
check("控制台登录状态接口可用", _login.get("ok") is True and "online" in _login, str(_login)[:120])
runtime.get().cfg.onebot.api_base = "http://127.0.0.1:9"  # 别踩到真 NapCat
_sync = c.post("/v1/console/onebot/sync")
check("未启用时同步明确报错", _sync.status_code in (400, 502), str(_sync.status_code))

print("[28] P0 多人隔离 / 拉黑 / 遗忘")
_ua = users.ensure_user("920001", "甲")
_ub = users.ensure_user("920002", "乙")
uida, uidb = int(_ua["id"]), int(_ub["id"])
users.update_relation(uida, affinity=80.0, trust=70.0, attraction=60.0)
users.update_relation(uidb, affinity=5.0, trust=5.0, attraction=5.0)
_rel_a = engine.relation_for(uida)
_rel_b = engine.relation_for(uidb)
check("个人关系按人读", _rel_a["affinity"] == 80.0 and _rel_b["affinity"] == 5.0, f"{_rel_a}/{_rel_b}")

c.post("/v1/console/send", json={
    "user_id": uida, "text": "你吃了吗", "as": "me", "chat_type": "private",
})
_sys_a = str((runtime.get().last_prompt or {}).get("system") or "")
_label_a = runtime.get().persona.stage_for(80.0)["label"]
check("她的 prompt 用甲自己的阶段", _label_a in _sys_a, _sys_a[:80])

users.update_relation(uida, aversion=100.0)
_r = c.post("/v1/console/send", json={
    "user_id": uida, "text": "在吗", "as": "me", "chat_type": "private",
}).json()
check("厌恶拉满只拉黑甲", users.is_blocked(uida) is True and users.is_blocked(uidb) is False,
      f"{users.is_blocked(uida)}/{users.is_blocked(uidb)}")
check("没终结整个会话", session_mod.get(runtime.get().default_name).get("status") == "active",
      str(session_mod.get(runtime.get().default_name).get("status")))
check("乙的厌恶没被波及", float(users.relation_of(uidb)["aversion"]) == 0.0,
      str(users.relation_of(uidb)["aversion"]))
check("拉黑后只回冷话", any("不想跟你说话" in x for x in (_r.get("reply") or [])), str(_r)[:120])

_ev_before = int(db.one("SELECT COUNT(*) c FROM events WHERE user_id = ?", (uida,))["c"])
check("甲有聊天原文可清", _ev_before > 0, str(_ev_before))
_f = c.post(f"/v1/console/friends/{uida}/forget").json()
check("遗忘接口成功", _f.get("ok") is True and int(_f.get("user_id") or 0) == uida, str(_f)[:160])
check("遗忘后解除拉黑", users.is_blocked(uida) is False)
check("遗忘后关系归零",
      float(users.relation_of(uida)["affinity"]) == 0.0
      and float(users.relation_of(uida)["aversion"]) == 0.0,
      str(users.relation_of(uida)))
check("遗忘后事件清空",
      int(db.one("SELECT COUNT(*) c FROM events WHERE user_id = ?", (uida,))["c"]) == 0)
check("遗忘后记忆清空",
      int(db.one("SELECT COUNT(*) c FROM memories WHERE user_id = ?", (uida,))["c"]) == 0)
check("遗忘不影响乙", users.is_blocked(uidb) is False
      and float(users.relation_of(uidb)["affinity"]) == 5.0, str(users.relation_of(uidb)))

print("[29] 模式时效 / 句数硬约束")
_u3 = users.ensure_user("930001", "时效")
uid3 = int(_u3["id"])
im.set_mode(uid3, Mode.EXPLICIT)
im.touch(uid3)
from datetime import datetime, timedelta
from app.core import db as _db2
_fixed_now = datetime(2026, 10, 29, 12, 0, tzinfo=datetime.now().astimezone().tzinfo)
_db2.kv_set(f"intimate_mode_ts:{uid3}",
            (_fixed_now - timedelta(minutes=60)).isoformat(timespec="seconds"))
check("直白 60 分钟后降成暗示", im.expire(uid3, _fixed_now) == Mode.SUGGESTIVE,
      im.expire(uid3, _fixed_now).value)
_db2.kv_set(f"intimate_mode_ts:{uid3}",
            (_fixed_now - timedelta(hours=9)).isoformat(timespec="seconds"))
check("暗示 9 小时后回正常", im.expire(uid3, _fixed_now) == Mode.OFF,
      im.expire(uid3, _fixed_now).value)
im.set_mode(uid3, Mode.SUGGESTIVE)
_db2.kv_set(f"intimate_mode_ts:{uid3}",
            (_fixed_now - timedelta(days=1)).isoformat(timespec="seconds"))
check("跨天必降级", im.expire(uid3, _fixed_now) == Mode.OFF, im.expire(uid3, _fixed_now).value)

_sentence = "我想操你。我想做爱。我想插你。我想日你。"
_capped = iprompt.limit_intimate_sentences(_sentence, "explicit", True)
_hits = sum(1 for p in _capped.split("。") if p and any(
    w in p for w in ("操", "做爱", "插", "日")))
check("私聊直白最多两句", _hits <= 2, f"{_capped} / hits={_hits}")
_gcapped = iprompt.limit_intimate_sentences("是不是想查我的学历。想查我的避税。", "suggestive", False)
_check_g = sum(1 for p in _gcapped.split("。") if p and ("查" in p or "避税" in p))
check("群聊暗示最多一句", _check_g <= 1, f"{_gcapped} / hits={_check_g}")
check("非亲密模式不动原文",
      iprompt.limit_intimate_sentences("我想操你。我想做爱。", "off", True) == "我想操你。我想做爱。",
      "off")

print("[30] 偏好分析 / 主动外发 / 慢思考")
import asyncio

from app.intimacy import analyze as ianalyze
from app.life import proactive as proactive_mod
from app.mind import loop as loop_mod

_u4 = users.ensure_user("940001", "档案")
uid4 = int(_u4["id"])
users.update_relation(uid4, affinity=100)
_sid = imem.start_session(uid4, "explicit", True)
imem.record(uid4, "user", "我喜欢你抱着我，不喜欢太急", "explicit", True)
imem.record(uid4, "her", "嗯，我记住了", "explicit", True)
imem.end_session(uid4)
_profile = asyncio.run(ianalyze.analyze_user(runtime.get(), uid4))
check("偏好分析跑通并落库", _profile is not None
      and db.one("SELECT user_id FROM intimate_profile WHERE user_id = ?", (uid4,)) is not None,
      str(_profile))
check("分析后不再重复排队", ianalyze.due(uid4) is False)

ianalyze._save(uid4, {"likes": ["被抱着"], "dislikes": ["太急"],
                      "boundaries": ["别在群里说"], "summary": "喜欢慢一点"}, 1)
im.set_mode(uid4, Mode.SUGGESTIVE)
im.touch(uid4)
c.post("/v1/console/send", json={
    "user_id": uid4, "text": "抱抱", "as": "me", "chat_type": "private",
})
check("偏好档案注入 prompt", "你已经知道的他" in str((runtime.get().last_prompt or {}).get("system") or ""))

_u5 = users.ensure_user("950001", "主动")
uid5 = int(_u5["id"])
users.update_relation(uid5, affinity=100)
from datetime import datetime as _dt, timedelta as _td
_old = (_dt.now().astimezone() - _td(days=2)).isoformat(timespec="seconds")
db.execute(
    "INSERT INTO events (ts, role, kind, text, source, meta, user_id, chat_type, group_id)"
    " VALUES (?, 'user', 'text', '两天前说的话', 'qq_onebot', '{}', ?, 'private', '')",
    (_old, uid5),
)
_lines = proactive_mod.tick_user(
    runtime.get(), runtime.get().persona, uid5, "private",
    now=_dt.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0),
)
check("按用户主动能出话", bool(_lines), str(_lines))
_row = db.one("SELECT user_id, chat_type FROM events WHERE source = 'proactive' AND user_id = ?"
              " ORDER BY id DESC LIMIT 1", (uid5,))
check("主动事件归到这个用户", _row is not None and int(_row["user_id"]) == uid5
      and str(_row["chat_type"]) == "private", str(dict(_row) if _row else None))
check("群聊不主动外发", proactive_mod.tick_user(runtime.get(), runtime.get().persona, uid5, "group") == [])
users.block_user(uid5)
check("拉黑的人不主动", proactive_mod.tick_user(runtime.get(), runtime.get().persona, uid5, "private") == [])


async def _slow_turn():
    return await loop_mod.chat_turn(
        runtime.get(), "什么是豆包", user_id=uidb, chat_type="private",
        prefetched_hint="【你刚上网查了「豆包」】\n字节跳动的 AI 助手", slow=True,
    )


_slow = asyncio.run(_slow_turn())
check("慢思考带查到的信息",
      "刚上网查了" in str((runtime.get().last_prompt or {}).get("system") or ""))
check("慢思考有标记",
      _slow.get("meta", {}).get("slow") is True
      and _slow.get("meta", {}).get("lookup_prefetched") is True, str(_slow.get("meta", {}))[:120])

print(f"\n结果：{ok} 通过，{fail} 失败")
exit(0 if fail == 0 else 1)
