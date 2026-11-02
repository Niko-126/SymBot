# -*- coding: utf-8 -*-
"""AI-Life 全量体检（不登 QQ、断网、独立临时库）。

`scripts/smoke.py` 是每次改动必须过的回归基线（273 项，快、稳）；
这个脚本是"隔一段时间全面摸一遍"的体检：把 OpenAPI 里 **全部 105 条路径**逐条压一遍，
再把 12 个功能区（对话/记忆/导入/恋爱/生活/群聊/热梗/开发者/异常…）的语义过一遍。

三个外部依赖全部垫掉，所以可以离线跑：
- LLM：`AI_LIFE_MOCK=1`（provider 没 key 本来就是 mock）
- OneBot / NapCat：httpx.AsyncClient 换成本地假客户端（status ok + data）
- 联网查词 / B 站：urllib.request.urlopen 直接抛 URLError（验证离线兜底）

跑法：
    .\.venv\Scripts\python.exe scripts\fullcheck.py
退出码 0 = 全过；1 = 有失败项（明细在末尾）。
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[1]
TMP = pathlib.Path(tempfile.mkdtemp(prefix="ai-life-full-"))
os.environ["AI_LIFE_DB"] = str(TMP / "life.db")
os.environ["AI_LIFE_DATA"] = str(TMP)
os.environ["AI_LIFE_MOCK"] = "1"
os.environ["AI_LIFE_IDLE"] = "0"
# 开发者口令：公开仓库的 config.yaml 留空（等于开发者接口全关），测试用环境变量临时给一个。
os.environ["SYMEM_DEVELOPER_KEY"] = "test-dev-key"
os.environ.pop("AI_LIFE_API_KEY", None)
os.environ.pop("AI_LIFE_PERSONA", None)
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------- 断网垫片 --
import urllib.error
import urllib.request


def _offline(*a, **k):
    raise urllib.error.URLError("offline-selftest")


urllib.request.urlopen = _offline

import httpx  # noqa: E402


class _FakeResp:
    status_code = 200
    is_success = True
    text = '{"status":"ok"}'

    def json(self):
        return {
            "status": "ok",
            "retcode": 0,
            "data": {
                "user_id": 10001, "nickname": "自检小号", "online": True, "good": True,
                "message_id": 1, "group_id": 123456, "members": [], "friend_list": [],
            },
        }

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


httpx.AsyncClient = _FakeAsyncClient  # 只垫异步客户端；同步 Client 留给 TestClient 自己

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.core import config as config_mod  # noqa: E402
from app.core import db, memengine, runtime  # noqa: E402

# ---------------------------------------------------------------- 记账 --
RESULTS: list[tuple[str, str, bool, str]] = []
HIT: set[tuple[str, str]] = set()
SKIP: dict[str, str] = {}


def ck(section: str, name: str, cond, detail: str = "") -> None:
    RESULTS.append((section, name, bool(cond), str(detail)[:220]))


def call(method: str, path: str, *args, **kw):
    HIT.add((method.upper(), path.split("?")[0]))
    return getattr(c, method)(path, *args, **kw)


DEV = str(config_mod.get().developer.key)
SECT = ""


def sec(name: str) -> None:
    global SECT
    SECT = name
    print(f"\n[{name}]")


def jget(resp, *keys, default=None):
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return default
    cur = data
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


c = TestClient(app)


class _NullCtx:
    def __enter__(self):
        return c

    def __exit__(self, *a):
        return False


with _NullCtx():
    rt = runtime.get()

    # ============================================================ 1 静态 / 元信息
    sec("S1 静态与元信息")
    r = call("get", "/v1/health")
    ck("S1", "health 200", r.status_code == 200, r.text[:120])
    ck("S1", "health 带 provider/model", bool(jget(r, "provider")) and bool(jget(r, "active_model")))
    ck("S1", "openapi 200", call("get", "/v1/openapi.json").status_code == 200)
    ck("S1", "docs 200", call("get", "/v1/docs").status_code == 200)
    r = call("get", "/")
    ck("S1", "面板首页 200 且像面板", r.status_code == 200 and len(r.text) > 2000, str(len(r.text)))
    r = call("get", "/settings.html")
    ck("S1", "设置页 200", r.status_code == 200 and "settings" in r.text.lower(), str(len(r.text)))
    r = call("get", "/console.html")
    ck("S1", "控制台页 200", r.status_code == 200 and len(r.text) > 500, str(len(r.text)))
    r = call("get", "/v1/models")
    ck("S1", "models 200 且非空", r.status_code == 200 and bool(r.json()), str(r.text)[:120])
    r = call("get", "/v1/persona")
    ck("S1", "persona 200 有名字", r.status_code == 200 and bool(jget(r, "name") or jget(r, "persona", "name")), str(r.text)[:120])
    r = call("get", "/v1/session")
    ck("S1", "session 200", r.status_code == 200 and bool(jget(r, "her_name") or jget(r, "seq") is not None), str(r.text)[:120])
    r = call("get", "/v1/board")
    b = r.json()
    ck("S1", "board 键齐全",
       {"persona", "relation", "memories", "history", "life", "traits"} <= set(b),
       str(sorted(b))[:160])

    # ============================================================ 2 对话核心
    sec("S2 对话 / 上下文 / 名字 / 记性")
    # 体检不跟真实作息赛跑：先把她叫醒（S6 会单独测"睡着不回"）
    call("post", "/v1/dev/awake", params={"password": DEV, "on": 1})
    r = call("post", "/v1/chat", json={"text": "在吗"})
    d = r.json()
    ck("S2", "首轮 chat 200", r.status_code == 200, r.text[:160])
    ck("S2", "reply 是非空数组", isinstance(d.get("reply"), list) and bool(d["reply"]))
    ck("S2", "state 全量", {"affinity", "trust", "attraction", "next_stage", "call_user"} <= set(d.get("state") or {}))
    meta = d.get("meta") or {}
    ck("S2", "meta 记忆链路",
       {"memories_used", "recall", "pinned", "anchor_scene", "anchors_used", "context_tokens"} <= set(meta),
       str(sorted(meta))[:200])
    ck("S2", "recall 结构", {"status", "hot", "recalled", "confidence"} <= set(meta.get("recall") or {}))

    r = call("post", "/v1/chat", json={"text": "给你起个名字吧"})
    ck("S2", "问名字有回应", "叫什么" in " ".join(r.json().get("reply") or []), str(r.json().get("reply"))[:120])
    r = call("post", "/v1/chat", json={"text": "小满"})
    ck("S2", "进入待确认", "小满" in " ".join(r.json().get("reply") or []))
    r = call("post", "/v1/chat", json={"text": "确认"})
    ck("S2", "名字锁定", jget(r, "state", "session", "name_locked") is True, str(jget(r, "state", "session")))
    sess = call("get", "/v1/session").json()
    ck("S2", "session 反映新名字", "小满" in json.dumps(sess, ensure_ascii=False), str(sess)[:160])

    r = call("post", "/v1/chat", json={"text": "记住这个：我的生日是三月七号"})
    ck("S2", "钉住返回 id", int(jget(r, "meta", "pinned", default=0) or 0) > 0, str(jget(r, "meta", "pinned")))
    mids = call("get", "/v1/memories", params={"q": "生日", "limit": 10}).json().get("items") or []
    ck("S2", "钉住的记忆能在列表里看到且 pinned=true",
       any("生日" in (m.get("content") or "") and m.get("pinned") is True for m in mids),
       str([(m.get("content"), m.get("pinned")) for m in mids])[:200])
    r = call("get", "/v1/memory/recall", params={"q": "生日", "user_id": 0})
    ck("S2", "召回调试口 200 且状态合法",
       r.status_code == 200 and jget(r, "status") in ("hit", "uncertain", "miss", "none"), r.text[:120])

    r = call("post", "/v1/chat", json={"text": "今天被骂了，好累，不想说话"})
    ck("S2", "情绪轮 200 且有 delta", r.status_code == 200 and isinstance(jget(r, "meta", "delta"), dict), str(r.text)[:120])
    r = call("get", "/v1/inspect")
    ins = r.json()
    ck("S2", "inspect 有 system/messages/budget",
       r.status_code == 200 and bool(ins.get("system")) and bool(ins.get("messages"))
       and bool((ins.get("budget") or {}).get("sections")), str(sorted(ins))[:160])
    r = call("get", "/v1/dev/last", params={"password": DEV})
    ck("S2", "dev/last 200 且 has_prompt", r.status_code == 200 and jget(r, "has_prompt") is True, r.text[:120])
    r = call("post", "/v1/chat/completions", json={"messages": [{"role": "user", "content": "你在干嘛"}], "model": "deepseek-chat"})
    cc = r.json()
    ck("S2", "OpenAI 兼容结构", r.status_code == 200 and bool(cc.get("choices")) and bool(cc.get("usage")), r.text[:160])
    r = call("get", "/v1/stats")
    ck("S2", "用量统计 200", r.status_code == 200, r.text[:100])
    r = call("get", "/v1/relation/log")
    ck("S2", "关系流水 200", r.status_code == 200, r.text[:100])
    r = call("get", "/v1/dev/context", params={"password": DEV})
    _dev_ctx = r.json() if r.status_code == 200 else {}
    ck("S2", "dev/context 200", r.status_code == 200 and isinstance(_dev_ctx, dict)
       and ("budget" in _dev_ctx or "used" in _dev_ctx), r.text[:100])
    r = call("post", "/v1/dev/context", params={"password": DEV}, json={"mode": "budget"})
    ck("S2", "dev/context 可切模式", r.status_code == 200, r.text[:100])

    # ============================================================ 3 记忆 API
    sec("S3 记忆接口 / 引擎")
    r = call("get", "/v1/memories", params={"limit": 100})
    items = r.json().get("items") or []
    ck("S3", "记忆列表 200 有内容", r.status_code == 200 and len(items) > 0, str(len(items)))
    pid = next((m["id"] for m in items if m["kind"] != "quote"), 0)
    r = call("post", f"/v1/memories/{pid}/pin", json={"pinned": True})
    ck("S3", "钉住接口 200", r.status_code == 200 and jget(r, "pinned") is True, r.text[:100])
    r = call("post", f"/v1/memories/{pid}/pin", json={"pinned": False})
    ck("S3", "取消钉住 200", r.status_code == 200 and jget(r, "pinned") is False, r.text[:100])
    r = call("post", "/v1/memories/999999/pin", json={"pinned": True})
    ck("S3", "钉不存在 → 404", r.status_code == 404, str(r.status_code))
    victim = next((m["id"] for m in items if m["kind"] == "fact"), 0)
    r = call("delete", f"/v1/memories/{victim}")
    ck("S3", "软删记忆 200", r.status_code == 200, r.text[:100])
    r = call("delete", f"/v1/memories/{victim}")
    ck("S3", "再删 → 404", r.status_code == 404, str(r.status_code))
    r = call("get", "/v1/dev/memory/cold", params={"password": DEV})
    ck("S3", "冷记忆状态三键", r.status_code == 200 and {"cold_candidates", "groups", "merges"} <= set(r.json()), r.text[:120])
    r = call("post", "/v1/dev/memory/compact", params={"password": DEV, "dry": 1})
    ck("S3", "冷压缩 dry-run", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
    r = call("post", "/v1/dev/memory/restore/999999", params={"password": DEV})
    ck("S3", "还原不存在 → ok:false 不崩", r.status_code == 200 and r.json().get("ok") is False, r.text[:120])
    st = memengine.store().stats()
    view_rows = db.one("SELECT COUNT(*) c FROM memories")["c"]
    ck("S3", "视图行数 ≤ 引擎活跃条目", view_rows <= int(st["items_active"]), f"view={view_rows} active={st['items_active']}")
    ck("S3", "引擎 schema v2", int(st["schema_version"]) >= 2, str(st["schema_version"]))
    ck("S3", "旧表已归档", db.one("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'memories_legacy%'") is not None
       or db.one("SELECT type FROM sqlite_master WHERE name='memories'")["type"] == "view")

    # ============================================================ 4 导入 / 灵魂档案
    sec("S4 导入 / 参考语料 / 灵魂档案")
    letter = ROOT / "samples" / "记忆交接信_示例.txt"
    if letter.exists():
        r = call("post", "/v1/ingest", json={"path": str(letter)})
        rep = r.json()
        ck("S4", "导入交接信 200", r.status_code == 200, r.text[:160])
        ck("S4", "导入有格式识别与计数",
           rep.get("format_detected") is not None and rep.get("events_parsed", 0) >= 1,
           str({k: rep.get(k) for k in ("format_detected", "events_parsed", "memories_written")}))
    else:
        SKIP["ingest-letter"] = "samples/记忆交接信_示例.txt 不存在"
    chat_txt = TMP / "chat.txt"
    chat_txt.write_text(
        "[2026-09-01 23:40] 她: 你还没睡。\n"
        "[2026-09-01 23:41] 我: 我今天加班到十一点，好累\n"
        "[2026-09-02 01:10] 我: 我家猫叫年糕\n", encoding="utf-8")
    r = call("post", "/v1/ingest", json={"path": str(chat_txt)})
    ck("S4", "导入聊天 txt 200", r.status_code == 200 and r.json().get("events_parsed", 0) >= 3, r.text[:160])
    r = call("post", "/v1/ingest", json={"path": str(TMP / "不存在.txt")})
    ck("S4", "导入不存在 → 400", r.status_code == 400, str(r.status_code))
    ref = ROOT / "语料" / "参考" / "参考对话.txt"
    ref_dir = ROOT / "语料"
    ref_file = None
    for cand in ref_dir.rglob("*.txt"):
        ref_file = cand
        break
    if ref_file:
        r = call("post", "/v1/reference/ingest", json={"path": str(ref_file)})
        ck("S4", "参考语料导入 200", r.status_code == 200 and r.json().get("ok") is True, r.text[:140])
    else:
        SKIP["reference-ingest"] = "语料/ 下没有 txt"
    r = call("get", "/v1/reference/status")
    ck("S4", "参考语料状态三键", r.status_code == 200 and {"turns", "quotes", "periods"} <= set(r.json()), r.text[:120])
    r = call("get", "/v1/soul/letter-template")
    ck("S4", "记忆交接信模板 200", r.status_code == 200 and bool(jget(r, "text")), r.text[:120])
    r = call("post", "/v1/soul/export")
    ck("S4", "灵魂导出 200", r.status_code == 200, r.text[:140])
    export_path = jget(r, "path") or jget(r, "file")
    if export_path:
        ck("S4", "导出文件真的存在", pathlib.Path(str(export_path)).exists(), str(export_path))
        body = pathlib.Path(str(export_path)).read_text(encoding="utf-8")
        r2 = call("post", "/v1/soul/import", content=body.encode("utf-8"),
                  headers={"Content-Type": "application/json"})
        ck("S4", "灵魂回导 200", r2.status_code == 200, r2.text[:160])
    else:
        SKIP["soul-import"] = "导出没给路径"
    r = call("post", "/v1/soul/snapshots")
    ck("S4", "灵魂快照创建 200", r.status_code == 200, r.text[:140])
    snaps = call("get", "/v1/soul/snapshots").json()
    names = snaps.get("items") or snaps.get("snapshots") or []
    ck("S4", "快照列表非空", bool(names), str(snaps)[:160])
    if names:
        nm = names[0].get("name") if isinstance(names[0], dict) else str(names[0])
        r = call("post", "/v1/soul/snapshots/restore", json={"name": nm})
        ck("S4", "快照还原 200", r.status_code == 200, r.text[:160])

    # ============================================================ 5 关系 / 恋爱 / 告别
    sec("S5 关系 / 恋爱 / 告别")
    r = call("post", "/v1/relation", json={"affinity": 88, "trust": 70, "attraction": 75})
    ck("S5", "手写全局关系 200", r.status_code == 200, r.text[:120])
    board = call("get", "/v1/board").json()
    ck("S5", "board 反映关系", abs(float(board["relation"]["affinity"]) - 88) < 0.01, str(board["relation"]))
    r = call("post", "/v1/console/friends", json={"openid": "selftest-love", "nickname": "自检恋人"})
    love_uid = int(jget(r, "user_id", default=0) or 0)
    ck("S5", "建一个可恋爱的人", love_uid > 0, r.text[:120])
    r = call("post", "/v1/console/relation", json={"user_id": love_uid, "affinity": 92, "trust": 85, "attraction": 85})
    ck("S5", "按人设关系 200", r.status_code == 200, r.text[:120])
    r = call("post", "/v1/chat", json={"text": "我喜欢你，我们在一起吧", "user_id": love_uid})
    ck("S5", "表白成功绑定", r.status_code == 200 and jget(r, "meta", "love") == "bound",
       str(jget(r, "meta", "love")) + " | " + str(r.json().get("reply"))[:80])
    r = call("post", "/v1/chat", json={"text": "我爱你", "user_id": love_uid})
    ck("S5", "恋人再说我爱你 → lover_again", jget(r, "meta", "love") == "lover_again", str(jget(r, "meta", "love")))
    r = call("post", "/v1/chat", json={"text": "我们分手吧", "user_id": love_uid})
    ck("S5", "分手 → breakup", jget(r, "meta", "love") == "breakup", str(jget(r, "meta", "love")))
    r = call("get", "/v1/farewell")
    ck("S5", "告别信状态 200", r.status_code == 200, r.text[:120])
    r = call("post", "/v1/farewell/read")
    ck("S5", "读告别信 200", r.status_code == 200, r.text[:120])
    r = call("post", "/v1/session/new")
    ck("S5", "开新会话 200", r.status_code == 200, r.text[:120])

    # ============================================================ 6 生活 / 日记 / 日程
    sec("S6 生活 / 日记 / 日程 / 状态")
    r = call("get", "/v1/diary")
    ck("S6", "日记查询 200", r.status_code == 200, r.text[:120])
    r = call("post", "/v1/diary/run", json={})
    ck("S6", "日记生成 200", r.status_code == 200, r.text[:160])
    r = call("get", "/v1/diary")
    ck("S6", "生成后能读回", r.status_code == 200 and bool(r.json()) and r.text.strip() not in ("{}", "null"), r.text[:160])
    r = call("get", "/v1/schedule")
    ck("S6", "日程查询 200", r.status_code == 200, r.text[:120])
    r = call("post", "/v1/schedule", json={"proactive": {"enabled": True, "cooldown_min": 30}})
    ck("S6", "日程开关 200", r.status_code == 200, r.text[:120])
    r = call("post", "/v1/dev/state/set", params={"password": DEV},
             json={"affinity": 60, "trust": 40, "attraction": 30, "energy": 35, "mood": -10})
    ck("S6", "状态写入 200", r.status_code == 200, r.text[:120])
    board = call("get", "/v1/board").json()
    ck("S6", "board.life 有精力/心情", "energy" in json.dumps(board.get("life") or {}), str(board.get("life"))[:120])
    r = call("post", "/v1/dev/awake", params={"password": DEV, "on": 0})
    ck("S6", "睡着开关 200", r.status_code == 200, r.text[:100])
    call("post", "/v1/dev/awake", params={"password": DEV, "on": 1})

    # ============================================================ 7 群 / OneBot 模拟
    sec("S7 群聊 / OneBot 上报（不登 QQ）")
    ob = rt.cfg.onebot
    ob.enabled = True
    ob.self_id = "999999"
    ob.sticker_chance = 0.0
    ob.voice_chance = 0.0
    ob.group_at_only = True
    ob.auto_accept_friend_request = True
    r = call("post", "/onebot/event", json={
        "post_type": "message", "message_type": "private", "self_id": 999999,
        "user_id": 20001, "message": [{"type": "text", "data": {"text": "这是私聊"}}],
        "sender": {"nickname": "自检甲"},
    })
    ck("S7", "私聊上报有回复", r.status_code == 200 and jget(r, "replied") is True, r.text[:160])
    r = call("post", "/onebot/event", json={
        "post_type": "message", "message_type": "group", "self_id": 999999,
        "user_id": 20001, "group_id": 123456,
        "message": [{"type": "at", "data": {"qq": "999999"}},
                    {"type": "text", "data": {"text": " 群里 @ 你"}}],
        "sender": {"nickname": "自检甲"},
    })
    ck("S7", "群聊 @ 有回复", r.status_code == 200 and jget(r, "replied") is True, r.text[:160])
    r = call("post", "/onebot/event", json={
        "post_type": "message", "message_type": "group", "self_id": 999999,
        "user_id": 20002, "group_id": 123456,
        "message": [{"type": "text", "data": {"text": "小鲸鱼在不在"}}],
        "sender": {"nickname": "自检乙"},
    })
    ck("S7", "喊名字也能触发", r.status_code == 200 and jget(r, "replied") is True, r.text[:160])
    r = call("post", "/onebot/event", json={
        "post_type": "message", "message_type": "group", "self_id": 999999,
        "user_id": 20003, "group_id": 123456,
        "message": [{"type": "text", "data": {"text": "随便聊一句"}}],
        "sender": {"nickname": "自检丙"},
    })
    ck("S7", "不 @ 不说话（group_at_only）", r.status_code == 200 and jget(r, "replied") is not True, r.text[:160])
    ob.auto_accept_friend_request = False
    r = call("post", "/onebot/event", json={
        "post_type": "request", "request_type": "friend", "self_id": 999999,
        "user_id": 20004, "flag": "flag-1",
        "comment": "加个好友", "sender": {"nickname": "自检丁"},
    })
    ck("S7", "好友申请不崩", r.status_code == 200, r.text[:160])
    r = call("post", "/onebot/event", json={"post_type": "meta_event", "meta_event_type": "heartbeat"})
    ck("S7", "心跳事件不崩", r.status_code == 200, r.text[:120])
    r = call("post", "/onebot/event", json={"post_type": "不认识的类型"})
    ck("S7", "未知事件不崩", r.status_code == 200, r.text[:120])
    uids = call("get", "/v1/console/friends").json()
    items = uids.get("items") or uids.get("friends") or []
    ck("S7", "花名册有刚才的人", bool(items), str(uids)[:160])
    uid = int(items[0].get("user_id") or 0) if items and isinstance(items[0], dict) else 0
    uid = uid or 1
    r = call("get", f"/v1/console/chat/{uid}", params={"limit": 20})
    ck("S7", "控制台聊天记录 200", r.status_code == 200 and "messages" in r.json(), r.text[:120])
    r = call("get", "/v1/console/groups")
    ck("S7", "群列表 200", r.status_code == 200, r.text[:120])
    r = call("get", "/v1/console/groups/123456/members")
    ck("S7", "群成员断网降级（200/502）", r.status_code in (200, 502, 503), f"{r.status_code} {r.text[:100]}")
    r = call("post", "/v1/console/groups/123456/greet")
    ck("S7", "打招呼断网降级（200/502）", r.status_code in (200, 502, 503), f"{r.status_code} {r.text[:100]}")
    r = call("post", "/v1/console/send", json={"user_id": uid, "text": "控制台代发"})
    ck("S7", "控制台代发 200 且带 ok", r.status_code == 200 and "ok" in r.json(), r.text[:140])
    r = call("post", "/v1/console/send_sticker", json={"user_id": uid, "sticker_id": "nope"})
    ck("S7", "发表情 200/400 均可", r.status_code in (200, 400, 404), str(r.status_code))
    r = call("get", "/v1/console/stickers", params={"limit": 5})
    stickers = r.json()
    ck("S7", "表情库 200", r.status_code == 200, str(stickers)[:120])
    sid = None
    for s in (stickers.get("items") or []):
        sid = s.get("id") or s.get("name")
        break
    if sid:
        r = call("get", f"/v1/console/sticker/{sid}.webp")
        ck("S7", "表情图片可取", r.status_code == 200 and len(r.content) > 100, f"{r.status_code} {len(r.content)}")
    else:
        SKIP["sticker-file"] = "表情库为空"
    r = call("post", "/v1/group/nick", json={"user_id": uid, "nick": "自检昵称"})
    ck("S7", "起昵称 200", r.status_code == 200, r.text[:120])
    r = call("get", f"/v1/group/nick/{uid}")
    ck("S7", "昵称可读回", r.status_code == 200, r.text[:120])
    r = call("post", f"/v1/group/nick/{uid}/suggest")
    ck("S7", "昵称建议 200", r.status_code == 200, r.text[:120])
    r = call("post", "/v1/group/introduce", json={"group_id": 123456})
    ck("S7", "群自我介绍 200（断网降级）", r.status_code == 200, r.text[:140])
    r = call("get", "/v1/console/onebot/login")
    ck("S7", "查小号登录态 200", r.status_code == 200, r.text[:120])
    r = call("post", "/v1/console/onebot/sync")
    ck("S7", "同步好友降级（200/502）", r.status_code in (200, 502, 503), f"{r.status_code} {r.text[:100]}")
    r = call("post", "/v1/console/friends", json={"openid": "selftest-openid", "nickname": "自检好友"})
    ck("S7", "手工加好友 200", r.status_code == 200, r.text[:120])
    r = call("get", "/v1/console/friends/requests")
    ck("S7", "好友申请列表 200", r.status_code == 200, r.text[:120])
    reqs = r.json().get("items") or []
    if reqs:
        rid = reqs[0].get("id")
        r = call("post", f"/v1/console/friends/requests/{rid}", json={"approve": False})
        ck("S7", "拒绝好友申请 200", r.status_code == 200, r.text[:120])
        r = call("delete", f"/v1/console/friends/requests/{rid}")
        ck("S7", "删申请记录不崩", r.status_code in (200, 404, 409), f"{r.status_code} {r.text[:100]}")
    else:
        SKIP["friend-request"] = "申请被自动通过，列表为空"
    ob.auto_accept_friend_request = True
    r = call("get", "/v1/console/testing")
    ck("S7", "测试期开关 200", r.status_code == 200, r.text[:120])
    r = call("post", "/v1/console/testing", json={"enabled": True})
    ck("S7", "打开测试期 200", r.status_code == 200, r.text[:100])
    call("post", "/v1/console/testing", json={"enabled": False})
    r = call("get", "/v1/console/thinking")
    ck("S7", "深度思考开关 200", r.status_code == 200, r.text[:100])
    r = call("get", "/v1/console/mind")
    ck("S7", "内心页 200", r.status_code == 200 and {"impressions", "notes", "thoughts"} <= set(r.json()), r.text[:120])
    r = call("post", "/v1/console/mind/run", json={"kind": "thought"})
    ck("S7", "手动思考 200", r.status_code == 200, r.text[:140])
    r = call("get", "/v1/console/observations")
    ck("S7", "观察列表 200", r.status_code == 200, r.text[:120])
    r = call("get", f"/v1/console/impression/{uid}")
    ck("S7", "印象 200", r.status_code == 200, r.text[:120])
    r = call("get", f"/v1/console/memory/{uid}")
    ck("S7", "按人记忆 200", r.status_code == 200, r.text[:120])
    r = call("get", f"/v1/console/memory/{uid}/stats")
    ck("S7", "按人记忆统计 200", r.status_code == 200 and "by_kind" in r.json(), r.text[:120])
    r = call("get", f"/v1/console/intimate/{uid}")
    ck("S7", "亲密面板 200", r.status_code == 200, r.text[:120])
    r = call("post", f"/v1/console/intimate/{uid}/analyze")
    ck("S7", "亲密分析 200", r.status_code == 200, r.text[:140])
    r = call("get", "/v1/console/intimate/exemplars")
    ck("S7", "亲密范本 200", r.status_code == 200, r.text[:120])
    r = call("get", "/v1/console/status")
    ck("S7", "控制台状态 200", r.status_code == 200 and "memories" in r.json(), r.text[:160])
    r = call("post", "/v1/group/start", params={"interval": 1})
    ck("S7", "群活跃启动 200", r.status_code == 200, r.text[:120])
    r = call("get", "/v1/group/status")
    ck("S7", "群活跃状态 200", r.status_code == 200, r.text[:120])
    r = call("post", "/v1/group/speak")
    ck("S7", "主动说一句 200", r.status_code == 200, r.text[:140])
    r = call("post", "/v1/group/silence", json={"minutes": 5, "reason": "自检"})
    ck("S7", "静音 200", r.status_code == 200, r.text[:120])
    r = call("post", "/v1/group/unsilence")
    ck("S7", "解除静音 200", r.status_code == 200, r.text[:120])
    r = call("post", "/v1/group/stop")
    ck("S7", "群活跃停止 200", r.status_code == 200, r.text[:120])

    # ============================================================ 8 趋势 / 联网降级
    sec("S8 热梗 / 联网降级（断网）")
    r = call("get", "/v1/trend/stats")
    ck("S8", "热梗统计 200", r.status_code == 200, r.text[:120])
    r = call("get", "/v1/trend/glossary")
    ck("S8", "梗词典 200", r.status_code == 200, r.text[:120])
    r = call("get", "/v1/trend/hint")
    ck("S8", "梗提示 200", r.status_code == 200, r.text[:120])
    r = call("get", "/v1/trend/gags", params={"limit": 5})
    ck("S8", "梗列表 200", r.status_code == 200, r.text[:120])
    r = call("get", "/v1/trend/learned")
    ck("S8", "自学习词 200", r.status_code == 200, r.text[:120])
    r = call("get", "/v1/trend/lookup", params={"kw": "city不city"})
    lk = r.json()
    ck("S8", "断网查词走内置兜底", r.status_code == 200 and bool(lk.get("summary") or lk.get("results") or lk.get("hit")),
       str(lk)[:160])
    r = call("get", "/v1/trend/search", params={"q": "热梗", "limit": 3})
    ck("S8", "断网搜索优雅失败（200/502）", r.status_code in (200, 502), f"{r.status_code} {r.text[:100]}")
    r = call("post", "/v1/trend/harvest", json={"limit": 2})
    ck("S8", "断网抓梗不崩（200/502）", r.status_code in (200, 502), f"{r.status_code} {r.text[:100]}")
    r = call("post", "/v1/trend/send_video", json={"user_id": uid, "bvid": "BV1xx411c7mD"})
    ck("S8", "发视频断网优雅失败（200/404）", r.status_code in (200, 404), f"{r.status_code} {r.text[:100]}")
    r = call("post", "/v1/trend/gags/clear", params={"password": DEV})
    ck("S8", "清梗库 200", r.status_code == 200, r.text[:120])

    # ============================================================ 9 开发者面板
    sec("S9 开发者面板 / 档案 / 迁移")
    r = call("post", "/v1/dev/login", json={"password": "wrong"})
    ck("S9", "错密码被拒（ok=false）", r.status_code == 200 and jget(r, "ok") is False, r.text[:120])
    r = call("post", "/v1/dev/login", json={"password": DEV})
    ck("S9", "对密码通过", r.status_code == 200, r.text[:120])
    r = call("get", "/v1/dev/anchors", params={"password": DEV})
    ck("S9", "锚点列表 200（可为空）", r.status_code == 200 and "items" in r.json(), r.text[:120])
    r = call("get", "/v1/dev/logs/export", params={"password": DEV})
    ck("S9", "调试日志导出 200", r.status_code == 200 and jget(r, "ok") is True, r.text[:140])
    r = call("get", "/v1/dev/archives", params={"password": DEV})
    ck("S9", "档案列表 200", r.status_code == 200, r.text[:120])
    archs = r.json().get("items") or []
    if archs:
        nm = archs[0] if isinstance(archs[0], str) else (archs[0].get("name") or "")
        if nm:
            r = call("get", f"/v1/dev/archives/{nm}", params={"password": DEV})
            ck("S9", "读单个档案 200", r.status_code == 200, r.text[:120])
    r = call("get", "/v1/dev/transfer/export", params={"password": DEV})
    ck("S9", "灵魂档案导出 200", r.status_code == 200, r.text[:140])
    tpath = jget(r, "path")
    if tpath:
        payload = json.loads(pathlib.Path(tpath).read_text(encoding="utf-8"))
        ck("S9", "导出含引擎归档", isinstance(payload.get("memory_archive"), dict)
           and payload["memory_archive"].get("format") == "symvolve-memory-archive", str(list(payload))[:160])
        r2 = call("post", "/v1/dev/transfer/import", params={"password": DEV},
                  content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                  headers={"Content-Type": "application/json"})
        ck("S9", "灵魂档案回导 200", r2.status_code == 200 and jget(r2, "ok") is True, r2.text[:160])
        items_after = call("get", "/v1/memories", params={"limit": 200}).json().get("items") or []
        ck("S9", "回导后记忆还在", len(items_after) > 0, str(len(items_after)))
    traits = call("get", "/v1/board").json().get("traits") or []
    tdim = None
    if isinstance(traits, list) and traits and isinstance(traits[0], dict):
        tdim = traits[0].get("dim")
    elif isinstance(traits, dict):
        tdim = next(iter(traits), None)
    if tdim:
        row = db.one("SELECT value FROM traits WHERE dim = ?", (tdim,))
        r = call("post", "/v1/persona/traits", json={"dim": tdim, "value": float(row["value"])})
        ck("S9", "改人格刻度 200", r.status_code == 200, f"{tdim} {r.text[:100]}")
    else:
        SKIP["traits"] = "临时库没有 traits 维度"
    r = call("post", "/v1/persona/reload", params={"password": DEV})
    ck("S9", "重载人设 200", r.status_code == 200, r.text[:120])
    r = call("post", "/v1/dev/relation/reset", params={"password": DEV})
    ck("S9", "关系重置 200", r.status_code == 200, r.text[:100])

    # ============================================================ 10 异常 / 边界
    sec("S10 异常与边界")
    ck("S10", "未知路径 404", call("get", "/v1/不存在").status_code == 404)
    ck("S10", "缺字段 422", call("post", "/v1/chat", json={}).status_code == 422)
    ck("S10", "非法 JSON 400/422", call("post", "/v1/chat", content=b"{oops",
                                        headers={"Content-Type": "application/json"}).status_code in (400, 422))
    ck("S10", "管理员口令错 403", call("post", "/v1/dev/memory/clear", params={"password": "x"}).status_code == 403)
    ck("S10", "空文本 chat 可处理", call("post", "/v1/chat", json={"text": ""}).status_code in (200, 422))

    # ============================================================ 11 覆盖率兜底
    sec("S11 路由覆盖率兜底（未测的按 schema 生成参数）")
    oa = app.openapi()
    spec = oa["paths"]

    def fill(schema, depth=0):
        if not schema or depth > 4:
            return "x"
        if "$ref" in schema:
            schema = oa["components"]["schemas"][schema["$ref"].split("/")[-1]]
        t = schema.get("type")
        if t == "string":
            return "x"
        if t in ("integer", "number"):
            return 1
        if t == "boolean":
            return True
        if t == "array":
            return []
        if t == "object":
            return {k: fill(v, depth + 1) for k, v in (schema.get("properties") or {}).items()
                    if k in (schema.get("required") or [])}
        return "x"

    for path, ops in sorted(spec.items()):
        for method, op in ops.items():
            key = (method.upper(), path)
            if key in HIT:
                continue
            params = {}
            for p in op.get("parameters") or []:
                if not p.get("required"):
                    continue
                sch = p.get("schema") or {}
                name = p["name"]
                if name in ("user_id", "user", "uid", "mid", "memory_id", "note_id", "gag_id", "request_id", "merge_id", "group_id", "sticker_id", "name"):
                    params[name] = 1 if sch.get("type") in ("integer", "number") else "1"
                elif name == "password":
                    params[name] = DEV
                elif name == "q":
                    params[name] = "年糕"
                elif name == "kw":
                    params[name] = "city不city"
                else:
                    params[name] = fill(sch)
            body = (op.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema")
            kwargs = {"params": params} if params else {}
            if body:
                kwargs["json"] = fill(body)
            url = path
            for k, v in params.items():
                if "{" + k + "}" in url:
                    url = url.replace("{" + k + "}", str(v))
                    kwargs["params"] = {kk: vv for kk, vv in (kwargs.get("params") or {}).items() if kk != k}
                    if not kwargs["params"]:
                        kwargs.pop("params")
            try:
                rr = getattr(c, method)(url, **kwargs)
                status = rr.status_code
            except Exception as exc:  # noqa: BLE001
                status = f"EXC {type(exc).__name__}: {exc}"
            HIT.add((method.upper(), path.split("?")[0]))
            degraded = isinstance(status, int) and status in (502, 503) and any(
                k in path for k in ("onebot", "/group", "/trend", "console/send", "console/groups", "soul/import"))
            if isinstance(status, str) or (isinstance(status, int) and status >= 500 and not degraded):
                ck("S11", f"{method.upper()} {path} 未崩", False, str(status))
            elif degraded:
                SKIP[f"sweep-{method}-{path}"] = f"断网降级 {status}"

    # ============================================================ 12 收尾断言
    sec("S12 结构收尾")
    view_kind = db.one("SELECT type FROM sqlite_master WHERE name='memories'")["type"]
    ck("S12", "memories 仍是视图", view_kind == "view", str(view_kind))
    ck("S12", "引擎表齐活",
       all(db.one("SELECT name FROM sqlite_master WHERE name=?", (t,)) is not None
           for t in ("mem_event", "mem_item", "mem_cue", "mem_recall_log")))
    stats = memengine.store().stats()
    ck("S12", "引擎计数自洽",
       int(stats["items_active"]) > 0 and int(stats["cues"]) > 0, str(stats)[:160])
    ck("S12", "断网后没有外发（outbox 仅记录）",
       not (TMP / "data" / "onebot-outbox.jsonl").exists(), "outbox 是本地留档，不算外发")

# ---------------------------------------------------------------- 汇总 --
print("\n" + "=" * 62)
by_section: dict[str, list] = {}
for s, n, okk, det in RESULTS:
    by_section.setdefault(s, []).append((n, okk, det))
total = len(RESULTS)
passed = sum(1 for _, _, okk, _ in RESULTS if okk)
for s in sorted(by_section):
    rows = by_section[s]
    good = sum(1 for _, okk, _ in rows if okk)
    print(f"{s:14} {good:3}/{len(rows):3} 通过")
failures = [(s, n, det) for s, n, okk, det in RESULTS if not okk]
if failures:
    print("\n失败明细：")
    for s, n, det in failures:
        print(f"  [{s}] {n}  {det}")
import re as _re
def _norm(p):
    return _re.sub(r"/\d+", "/{n}", p)
hit_tpl = {_norm(p) for _, p in HIT}
spec_tpl = set(spec)
covered = len(hit_tpl & spec_tpl)
print(f"\n总计：{passed}/{total} 通过 | 路由模板覆盖 {covered}/{len(spec_tpl)}")
if SKIP:
    print("跳过：", SKIP)
print("临时库：", TMP)
sys.exit(0 if not failures else 1)
