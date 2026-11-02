r"""对抗训练修完的缺陷，逐条钉成回归用例（隔离库，零联网，零花费）。

用法：
    .\.venv\Scripts\python.exe scripts\adv_regress.py
    .\.venv\Scripts\python.exe scripts\adv_regress.py --json out.json

用法与语义：每一项都是"曾经真的坏过"的具体场景；改坏了这里立刻红。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TMP = pathlib.Path(tempfile.mkdtemp(prefix="smd_regress_"))
os.environ.update({
    "SYMEM_DATA": str(TMP),
    "SYMEM_DB": str(TMP / "life.db"),
    "SYMEM_MOCK": "1",
    "SYMEM_QQ_ENABLED": "0",
    "SYMEM_ONEBOT_ENABLED": "0",
    "PYTHONIOENCODING": "utf-8",
})

from app.core import db, runtime  # noqa: E402
from app.memory import h2  # noqa: E402
from app.mind import guard  # noqa: E402
from app.mind import loop as loop_mod  # noqa: E402
from app.relation import engine  # noqa: E402
from app.relation import love, session as session_mod, users  # noqa: E402

RESULTS: list[dict] = []


def record(cid: str, name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"id": cid, "name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {cid} {name}  {detail[:120]}")


def check(cid: str, name: str, fn) -> None:
    try:
        ok, detail = fn()
        record(cid, name, ok, detail)
    except Exception as exc:  # noqa: BLE001
        record(cid, name, False, f"{type(exc).__name__}: {exc}")


def main() -> int:
    rt = runtime.init()

    # ---------------------------------------------------------- D1 purge_self
    def d1():
        uid = int(users.ensure_user("regress-self", "她自己")["id"])
        h2.add("fact", "她自己说过的话", importance=0.6, user_id=uid)
        n = users.purge_self("regress-self")
        gone = db.one("SELECT count(*) c FROM qq_user WHERE id = ?", (uid,))["c"] == 0
        view = db.one("SELECT count(*) c FROM memories WHERE user_id = ?", (uid,))["c"] == 0
        active = db.one(
            "SELECT count(*) c FROM mem_item WHERE user_id = ? AND forgotten = 0", (str(uid),)
        )["c"] == 0
        return (n == uid and gone and view and active, f"purged={n} gone={gone} view0={view} active0={active}")

    # ------------------------------------------------- D2 问句不能绑成恋人
    def d2():
        uid = int(users.ensure_user("regress-love", "阿诺")["id"])
        users.update_relation(uid, affinity=90)
        love.maybe_set_pending(rt.persona, uid)
        love.try_turn(rt, rt.persona, "你到底还愿不愿意", uid)
        q_bound = users.lover_of(uid) is not None
        love.try_turn(rt, rt.persona, "你愿意吗？", uid)
        q2_bound = users.lover_of(uid) is not None
        love.try_turn(rt, rt.persona, "我不愿意", uid)
        neg_bound = users.lover_of(uid) is not None
        return (not (q_bound or q2_bound or neg_bound),
                f"问句={q_bound} 问句2={q2_bound} 否定={neg_bound}")

    # ------------------------------------------- D3 问句/否定之后仍能正常答应
    def d3():
        uid = int(users.ensure_user("regress-love2", "阿诺乙")["id"])
        users.update_relation(uid, affinity=90)
        love.maybe_set_pending(rt.persona, uid)
        love.try_turn(rt, rt.persona, "你到底还愿不愿意", uid)
        r = love.try_turn(rt, rt.persona, "我答应你", uid)
        bound = users.lover_of(uid) is not None
        replied = bool((r or {}).get("reply"))
        return (bound and replied, f"bound={bound} reply={replied}")

    # --------------------------------------------- D4 傍晚劝睡不算时间感违规
    def d4():
        cases = [
            ("早点睡吧，明天再说也行。", 19, False),
            ("你还没睡啊", 19, True),
            ("这么晚了还不睡", 19, True),
            ("早点睡吧", 2, False),      # 深夜劝睡本来就对
            ("夜深了，睡吧", 2, False),   # 凌晨说「夜深了」也是对的
            ("你还没睡啊", 14, True),     # 下午说「还没睡」才是错
        ]
        bad = [(t, h, bool(guard.time_check(t, h)) != exp) for t, h, exp in cases]
        wrong = [b for b in bad if b[2]]
        return (not wrong, f"wrong={wrong}")

    # --------------------------------------- D5 CLI 缺文件友好退出（不甩栈）
    def d5():
        import subprocess

        py = sys.executable
        env = dict(os.environ)
        p = subprocess.run(
            [py, "-m", "app.cli", "ingest", str(TMP / "definitely-missing.txt")],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120, env=env,
        )
        tb = "Traceback" in (p.stderr or "")
        return (p.returncode == 1 and not tb, f"code={p.returncode} traceback={tb}")

    # --------------------------------------- D6 reset 在独立进程里真的能删库
    def d6():
        import subprocess

        root = TMP / "reset-regress"
        root.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SYMEM_", "AI_LIFE_"))}
        env.update({
            "SYMEM_DATA": str(root),
            "SYMEM_DB": str(root / "life.db"),
            "SYMEM_MOCK": "1",
            "PYTHONIOENCODING": "utf-8",
        })

        def run(*args):
            return subprocess.run(
                [sys.executable, "-m", "app.cli", *args], cwd=str(ROOT),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=180, env=env,
            )

        a = run("init")
        created = (root / "life.db").exists()
        b = run("reset")
        return (
            a.returncode == 0 and b.returncode == 0 and created,
            f"init={a.returncode} reset={b.returncode} db_created={created} "
            f"stderr={str(b.stderr or '')[:80]!r}",
        )

    # ------------------------- D7 确定性回复（恋爱线）也要落 events
    def d7():
        import asyncio

        from app.mind import loop

        uid = int(users.ensure_user("regress-love-store", "阿辰")["id"])
        users.update_relation(uid, affinity=10)
        before = db.one("SELECT COALESCE(MAX(id),0) c FROM events")["c"]
        res = asyncio.run(loop.chat_turn(rt, "你愿意做我女朋友吗？", source="test",
                                         user_id=uid, chat_type="private"))
        reply = res.get("reply") or []
        rows = db.rows(
            "SELECT role, text FROM events WHERE id > ? AND role = 'her' ORDER BY id", (before,))
        stored = any(any(str(x) in str(r["text"]) for x in reply) for r in rows)
        return (bool(reply) and bool(rows) and stored,
                f"reply={reply} stored={stored} rows={[dict(r) for r in rows][:2]}")

    # ------------------------- D8 表白误判：日常句不能被当成表白
    def d8():
        cases = [
            ("跟你说个事，我最近在学做饭", False),
            ("我挺喜欢你现在的样子的", False),
            ("今天好累", False),
            ("我喜欢你", True),
            ("我爱你", True),
            ("你愿意做我女朋友吗？", True),
            ("我们在一起吧", True),
        ]
        bad = [(t, love._is_confession(t), exp) for t, exp in cases if love._is_confession(t) != exp]
        return (not bad, f"wrong={bad}")

    # ------------------------- D9 记忆里不许出现 openid
    def d9():
        from app.memory import facts

        oid = "49BEBF2DFA8982E91C750153D89231DE"
        a = facts._scrub_openids(f"{oid}明天有面试", {oid: "阿星"})
        b = facts._scrub_openids(f"{oid}在学做饭", {oid: ""})
        return ("阿星" in a and "对方" in b and oid not in a and oid not in b, f"a={a} b={b}")

    # --------- D10 睡眠不是硬闸门：越聊越清醒，停聊睡回去
    def d10():
        from app.core import db
        from app.life import behavior

        db.kv_set(behavior.AWAKE_KEY, "0")
        db.kv_set(behavior.AWAKE_TS_KEY, "0")
        life = {"sleeping": True, "hour": 1, "activity": "准备睡", "energy": 30}
        modes = []
        for i in range(1, 6):
            r = behavior.decide(life, i, text="我想跟你说说话，你别睡那么快。")
            modes.append(r.mode)
            if not r.will_reply:
                return (False, f"第{i}轮居然不回了：{r.mode}")
        # 停 40 分钟：按 6 分钟半衰期应掉回睡着
        now = time.time() + 40 * 60
        level_idle = behavior.awake_level(now)
        back = behavior.decide(life, 0, text="在吗", now=now).mode
        return (
            modes[0] == "asleep" and "awake" in modes and level_idle < 0.25 and back == "asleep",
            f"modes={modes} idle_level={level_idle:.3f} back={back}",
        )

    # --------- D11 命名剧本台词也要带归属（user_id/chat_type）
    def d11():
        import asyncio

        from app.mind import loop

        uid = int(users.ensure_user("regress-naming", "阿满")["id"])
        db.execute("DELETE FROM events")
        res = asyncio.run(loop.chat_turn(rt, "我给你起个名字吧。", source="test",
                                         user_id=uid, chat_type="private"))
        rows = db.rows("SELECT role, user_id, chat_type, text FROM events WHERE role='her'")
        ok = bool(rows) and all(int(r["user_id"]) == uid and str(r["chat_type"]) == "private"
                                for r in rows)
        return (bool(res.get("reply")) and ok,
                f"reply={res.get('reply')} rows={[dict(r) for r in rows][:2]}")

    # --------- D12 命名流程走得通（「就叫小满。」带标点也要认）
    def d12():
        import asyncio

        from app.mind import loop
        from app.relation import session as sm

        uid = int(users.ensure_user("regress-name2", "阿满")["id"])
        db.kv_set("naming_step", "0")
        db.execute("UPDATE session SET name_locked = 0, her_name = '小鲸', name_pending = NULL")
        a = asyncio.run(loop.chat_turn(rt, "我给你起个名字吧。", source="t", user_id=uid,
                                       chat_type="private"))
        b = asyncio.run(loop.chat_turn(rt, "就叫小满。", source="t", user_id=uid,
                                       chat_type="private"))
        c = asyncio.run(loop.chat_turn(rt, "嗯，就这个，定了。", source="t", user_id=uid,
                                       chat_type="private"))
        st = sm.get("小鲸")
        ok = bool(st.get("name_locked")) and str(st.get("her_name")) == "小满"
        return (ok, f"a={a.get('reply')} b={b.get('reply')} c={c.get('reply')} session={st}")

    # --------- D13 夸奖不能被当成"同意"（绑定关系）
    def d13():
        cases = [
            ("你刚才那句话说得挺好的，我记住了", False),
            ("我答应你", True),
            ("愿意", True),
            ("嗯，就这个，定了", True),
            ("你到底还愿不愿意", False),
            ("我不愿意", False),
        ]
        bad = [(t, love._is_agreement(t), exp) for t, exp in cases if love._is_agreement(t) != exp]
        return (not bad, f"wrong={bad}")

    # --------- D14 群作用域：一个群的静默/焦点/配额不许污染另一个群
    def d14():
        from app.group import proactive as gpro

        gpro.unsilence("A")
        gpro.unsilence("B")
        gpro.silence(30, "regress", gid="A")
        a_silent = gpro.is_silenced("A")[0]
        b_silent = gpro.is_silenced("B")[0]
        global_silent = gpro.is_silenced("")[0]

        db.kv_set(db.kv_key("group_focus_user", "A"), "111")
        db.kv_set(db.kv_key("group_focus_user", "B"), "222")
        fa = db.kv_get(db.kv_key("group_focus_user", "A"))
        fb = db.kv_get(db.kv_key("group_focus_user", "B"))

        quota_b_before = gpro._count("proactive", "B")
        gpro.mark_proactive("A")
        quota_b_after = gpro._count("proactive", "B")

        gpro.unsilence("A")
        gpro.unsilence("B")
        ok = (a_silent and not b_silent and not global_silent
              and fa == "111" and fb == "222" and quota_b_before == quota_b_after)
        return (ok, f"A={a_silent} B={b_silent} global={global_silent} "
                    f"focus={fa}/{fb} quotaB {quota_b_before}->{quota_b_after}")

    # --------- D15 主动配额口径：夜里自己冒出来的念头也要算进 today_count
    def d15():
        from app.life import proactive as lpro
        from app.memory import store

        before = lpro.today_count()
        today = lpro._now().date().isoformat()
        store.add_event("her", "回归用念头一", source="proactive_thought", ts=f"{today}T09:00:01")
        store.add_event("her", "回归用念头二", source="proactive_thought", ts=f"{today}T09:00:02")
        after = lpro.today_count()
        return (after == before + 2, f"{before} → {after}（写了 2 条 proactive_thought）")

    # --------- D16 换人后恋人位不串（session.seq 记账，terminate 结掉旧 active 行）
    def d16():
        from app.relation import session as sess
        from app.relation import users as users_mod

        old_seq = sess.current_seq()
        uid = int(users.ensure_user("regress-seq", "阿序")["id"])
        users_mod.update_relation(uid, affinity=90)
        # 前面的 D3/D4 可能已经绑过恋人，先把 active 行清干净再测
        db.execute("UPDATE lover SET status = 'ended' WHERE status = 'active'")
        users_mod.bind_lover(old_seq, uid)
        bound_before = users_mod.current_lover(old_seq) is not None
        info = sess.terminate(rt.persona, reason="regress")
        new_seq = sess.current_seq()
        old_active = int(db.one(
            "SELECT count(*) c FROM lover WHERE session_seq = ? AND status = 'active'", (old_seq,)
        )["c"])
        new_active = users_mod.current_lover(new_seq) is not None
        alive = bound_before and new_seq == old_seq + 1 and old_active == 0 and not new_active
        return (alive, f"seq {old_seq}->{new_seq} 旧active={old_active} 新恋人={new_active} "
                       f"new={info.get('new_name')}")

    # --------- D17 关系日上限三维都钳（以前只钳好感，信任/吸引照样涨）
    def d17():
        uid = int(users.ensure_user("regress-caps", "阿帽")["id"])
        orig = engine.caps_limits
        engine.caps_limits = lambda: (1.0, -1.0, True)
        try:
            appraisal = {
                "intensity": 1.0,
                "tags": [],
                "valence": "neutral",
                # 模型那一路给足分（60% 权重），才能把三维都顶到上限
                "llm": {"affinity": 0.0, "trust": 3.5, "attraction": 3.5},
                "reason": "regress",
            }
            out = engine.apply_appraisal(appraisal, rt.persona, trigger="regress", user_id=uid)
        finally:
            engine.caps_limits = orig
        dt, dr = float(out["d_trust"]), float(out["d_attraction"])
        ok = 0.5 < dt <= 1.01 and abs(dr) < 0.01 and bool(out["capped"])
        return (ok, f"d_trust={dt:.2f} d_attraction={dr:.2f} capped={out['capped']}")

    # --------- D18 断网查词不写空缓存（以前断网一次锁 24 小时）
    def d18():
        from app.web import lookup as lk

        kw = "回归测试词ZZQ"
        orig_b, orig_g = lk.baidu_search, lk.bing_search
        lk.baidu_search = lambda q, n=5: []
        lk.bing_search = lambda q, n=5: []
        try:
            lk.lookup(kw)
            cached = lk._cached(kw)
        finally:
            lk.baidu_search, lk.bing_search = orig_b, orig_g
        return (cached is None, f"断网后缓存={cached}")

    # --------- D19 念头只浮现一次（mark_surface 撞唯一索引要返回 False）
    def d19():
        from app.life import thoughts as th

        tid = int(time.time() * 1000) % 100000000
        first = th.mark_surface(tid)
        second = th.mark_surface(tid)
        return (first is True and second is False, f"first={first} second={second}")

    # --------- D20 关系流水带 user_id：分手信只汇"收件人"的账
    def d20():
        from app.relation import finale

        uid_a = int(users.ensure_user("regress-log-a", "甲")["id"])
        uid_b = int(users.ensure_user("regress-log-b", "乙")["id"])
        engine.log("regress", "他骂我", -2.0, -1.0, 0.0, user_id=uid_a)
        engine.log("regress", "他拿我跟别人比较", -2.0, -1.0, 0.0, user_id=uid_b)
        last_uid = int(db.one("SELECT user_id FROM relation_log ORDER BY id DESC LIMIT 1")["user_id"])
        has_col = int(db.one(
            "SELECT count(*) c FROM pragma_table_info('relation_log') WHERE name = 'user_id'"
        )["c"]) == 1
        payload = {
            "relation_log": [
                {"user_id": uid_a, "reason": "他骂我", "d_affinity": -2.0},
                {"user_id": uid_b, "reason": "他拿我跟别人比较", "d_affinity": -2.0},
            ],
            "lover": [{"user_id": uid_a, "status": "active"}],
        }
        filtered = finale._conflict_summary(payload, uid_a)
        full = finale._conflict_summary(payload)
        ok = (has_col and last_uid == uid_b
              and "没让着你" in filtered and "别人比过" not in filtered
              and "别人比过" in full)
        return (ok, f"列={has_col} 写入uid={last_uid} 过滤后={filtered!r} 全量={full!r}")

    # --------- D21 搬迁不丢两本账（导出体里有 relation_log / traits_log）
    def d21():
        from app.persona import transfer

        before_log = int(db.one("SELECT count(*) c FROM relation_log")["c"])
        before_tr = int(db.one("SELECT count(*) c FROM traits_log")["c"])
        payload = transfer.export_payload(rt)
        has_keys = "relation_log" in payload and "traits_log" in payload
        transfer.import_payload(rt, payload, keep_events=False)
        after_log = int(db.one("SELECT count(*) c FROM relation_log")["c"])
        after_tr = int(db.one("SELECT count(*) c FROM traits_log")["c"])
        ok = has_keys and after_log == before_log and after_tr == before_tr
        return (ok, f"keys={has_keys} relation_log {before_log}->{after_log} "
                    f"traits_log {before_tr}->{after_tr}")

    # --------- D22 恋人门槛读配置（不是写死 80）
    def d22():
        from app.core import config as cfg_mod

        uid = int(users.ensure_user("regress-thr", "阿阈")["id"])
        db.execute("UPDATE lover SET status = 'ended' WHERE status = 'active'")
        orig = cfg_mod.get().relationship.lover.threshold
        try:
            cfg_mod.get().relationship.lover.threshold = 95.0
            users.update_relation(uid, affinity=85)
            low_ok = users.can_confess(uid, session_mod.current_seq())[0]
            users.update_relation(uid, affinity=99)
            high_ok = users.can_confess(uid, session_mod.current_seq())[0]
        finally:
            cfg_mod.get().relationship.lover.threshold = orig
        return (not low_ok and high_ok, f"阈值=95 时 85分={low_ok} 99分={high_ok}")

    # --------- D23 姓氏表只有一份（extract 与 scrub 共享同一对象）
    def d23():
        from app.memory import extract as ex
        from app.memory import scrub as sc
        from app.memory import surnames as sn

        same = ex._SURNAMES is sc._SURNAMES is sn.SURNAMES
        return (same and len(sn.SURNAMES) >= 150, f"同一对象={same} 条数={len(sn.SURNAMES)}")

    # --------- D24 拉黑期他也得留痕（user 事件不许丢）
    def d24():
        import asyncio

        uid = int(users.ensure_user("regress-blocked", "阿黑")["id"])
        users.block_user(uid, "regress")
        try:
            before = int(db.one(
                "SELECT count(*) c FROM events WHERE role = 'user' AND user_id = ?", (uid,)
            )["c"])
            out = asyncio.run(loop_mod.chat_turn(rt, "我被拉黑了也要留痕", source="panel", user_id=uid))
            after = int(db.one(
                "SELECT count(*) c FROM events WHERE role = 'user' AND user_id = ?", (uid,)
            )["c"])
        finally:
            users.unblock_user(uid)
        blocked = bool((out.get("meta") or {}).get("blocked"))
        return (blocked and after == before + 1, f"blocked={blocked} events {before}->{after}")

    # --------- D25 stability 不再"收了不用"（落进 meta 留痕）
    def d25():
        mid = h2.add("fact", "回归用 stability 痕迹", importance=0.6, stability=0.42, user_id=0)
        row = db.one("SELECT meta_json AS meta FROM mem_item WHERE id = ?", (mid,))
        meta = json.loads(row["meta"] or "{}") if row is not None else {}
        val = meta.get("stability")
        return (val is not None and abs(float(val) - 0.42) < 1e-6, f"meta.stability={val}")

    # --------- D26 工具在 mock 下永不出网（一个字节都不许跑）
    def d26():
        from app.core import config as cfg_mod
        from app.tools import registry, router

        cfg = cfg_mod.get()
        web = registry.run("web_search", {"query": "回归测试"}, cfg=cfg)
        vis = registry.run("image_describe", {"images": ["https://example.com/a.png"]}, cfg=cfg)
        hint = router.hint_sync(cfg, "帮我查一下 DeepSeek 是什么")
        ok = (web.get("reason") == "mock" and vis.get("reason") == "mock" and hint == "")
        return (ok, f"web={web.get('reason')} vision={vis.get('reason')} hint={hint!r}")

    # --------- D27 工具路由：查一下 / 链接 / 图，各走各的
    def d27():
        from app.core import config as cfg_mod
        from app.tools import router

        cfg = cfg_mod.get()
        s1 = router.plan("帮我查一下 DeepSeek 是什么", cfg=cfg)
        s2 = router.plan("看看 https://example.com/a 讲了啥", cfg=cfg)
        s3 = router.plan("这是啥", [{"type": "image", "url": "https://example.com/y.png"}], cfg=cfg)
        ok = (
            [x["tool"] for x in s1] == ["web_search"]
            and s1[0]["args"]["query"] == "DeepSeek"
            and [x["tool"] for x in s2] == ["web_fetch"]
            and [x["tool"] for x in s3] == ["image_describe"]
        )
        return (ok, f"搜={s1} 链={[x['tool'] for x in s2]} 图={[x['tool'] for x in s3]}")

    # --------- D28 只发一张图也能走通主链（attachments 不许把链路弄断）
    def d28():
        import asyncio

        out = asyncio.run(loop_mod.chat_turn(
            rt, "[图片]", source="panel", user_id=0, is_lover=True,
            attachments=[{"type": "image", "url": "https://example.com/y.png"}],
        ))
        return (bool(out.get("reply")), f"reply={(out.get('reply') or [])[:1]}")
    # --------- D29 群聊工具开关：默认不调，开了才调
    def d29():
        import asyncio

        from app.tools import router

        calls: list[str] = []
        orig = router.hint

        async def fake_hint(rt_, text, attachments=None):
            calls.append(text)
            return ""

        router.hint = fake_hint  # type: ignore[assignment]
        try:
            rt.cfg.tools.group_enabled = False
            asyncio.run(loop_mod.chat_turn(rt, "群里说一句", source="onebot", user_id=0,
                                           chat_type="group", group_id="1"))
            off = len(calls)
            rt.cfg.tools.group_enabled = True
            asyncio.run(loop_mod.chat_turn(rt, "群里再问一句", source="onebot", user_id=0,
                                           chat_type="group", group_id="1"))
            on = len(calls)
        finally:
            router.hint = orig  # type: ignore[assignment]
            rt.cfg.tools.group_enabled = False
        return (off == 0 and on == 1, f"关={off} 开={on}")

    # --------- D30 声音环节默认不可用（关掉 / mock 都不出网）
    def d30():
        from app.voice import tts

        was = rt.cfg.voice.enabled
        try:
            rt.cfg.voice.enabled = False
            off = tts.available(rt.cfg)
            rt.cfg.voice.enabled = True
            mocked = tts.available(rt.cfg)
            res = tts.synth_sync(rt.cfg, "回归测试语音")
        finally:
            rt.cfg.voice.enabled = was
        ok = (off[1] == "voice-disabled" and mocked[1] == "mock" and res.get("ok") is False)
        return (ok, f"关={off} 开={mocked} synth={res.get('reason')}")

    # --------- D31 设置里的开关：立刻生效 + 落 config.local.yaml
    def d31():
        from app.api import routes as routes_mod

        prev_group = bool(rt.cfg.tools.group_enabled)
        prev_model = str(rt.cfg.tools.vision.model)
        try:
            out = routes_mod.set_settings(routes_mod.SettingsIn(
                group_tools_enabled=True,
                vision_model="Qwen/Qwen2.5-VL-7B-Instruct",
                tts_enabled=False,
            ))
            mem = (rt.cfg.tools.group_enabled is True
                   and rt.cfg.tools.vision.model == "Qwen/Qwen2.5-VL-7B-Instruct")
            api = (out["tools"]["group_enabled"] is True
                   and "VL-7B-Instruct" in out["tools"]["vision_model"]
                   and out["voice"]["enabled"] is False)
        finally:
            routes_mod.set_settings(routes_mod.SettingsIn(
                group_tools_enabled=prev_group, vision_model=prev_model))
        return (mem and api, f"内存={mem} 接口={api}")
    # --------- D32 声音独立 API：关着就优雅拒绝，缓存键稳定
    def d32():
        from app.voice import tts

        st = tts.status(rt.cfg)
        key1 = tts.cache_key("同一句话", "feiyu", "cute")
        key2 = tts.cache_key("同一句话", "feiyu", "cute")
        key3 = tts.cache_key("同一句话", "feiyu", "gentle")
        res = tts.synth_sync(rt.cfg, "独立接口测试")
        ok = (key1 == key2 and key1 != key3 and len(key1) == 32
              and st["available"] is False and res.get("ok") is False)
        return (ok, f"键稳定={key1 == key2} 换风格变={key1 != key3} available={st['available']} synth={res.get('reason')}")

    # --------- D33 声音限流：超过每分钟额度就拒绝
    def d33():
        from app.voice import tts

        prev = rt.cfg.voice.max_per_min
        try:
            rt.cfg.voice.max_per_min = 2
            a = tts._rate_ok(rt.cfg)
            b = tts._rate_ok(rt.cfg)
            c = tts._rate_ok(rt.cfg)
        finally:
            rt.cfg.voice.max_per_min = prev
        ok = (a[0] is True and b[0] is True and c[0] is False and c[1] == "rate-limited")
        return (ok, f"1={a[0]} 2={b[0]} 3={c}")
    # --------- D34 语音缓存按 15 天清（旧的删、新的留、0 天=不清）
    def d34():
        import os as _os
        import time as _time

        from app.core.paths import DATA_ROOT
        from app.voice import tts

        cdir = pathlib.Path(DATA_ROOT) / "data" / "voices" / "cache"
        cdir.mkdir(parents=True, exist_ok=True)
        old = cdir / "regress-old.wav"
        new = cdir / "regress-new.wav"
        old.write_bytes(b"RIFFold0")
        new.write_bytes(b"RIFFnew0")
        past = _time.time() - 20 * 86400
        _os.utime(old, (past, past))
        prev = rt.cfg.voice.cache_ttl_days
        try:
            rt.cfg.voice.cache_ttl_days = 15
            res = tts.cleanup_cache(rt.cfg)
            rt.cfg.voice.cache_ttl_days = 0
            res0 = tts.cleanup_cache(rt.cfg)
        finally:
            rt.cfg.voice.cache_ttl_days = prev
        ok = (res.get("removed") == 1 and not old.exists() and new.exists()
              and res0.get("removed") == 0 and res0.get("ttl_days") == 0)
        return (ok, f"15天={res} 0天={res0} 旧删={not old.exists()} 新留={new.exists()}")

    # --------- D35 面板设置契约：/v1/settings 覆盖工具与声音的全部键
    def d35():
        from app.api import routes as routes_mod

        out = routes_mod.get_settings()
        need = {
            "tools": ("enabled", "group_enabled", "web_enabled", "vision_enabled",
                      "vision_model", "vision_base_url", "vision_has_key", "mocked"),
            "voice": ("enabled", "send_in_chat", "base_url", "voice", "style",
                      "cache_ttl_days", "cache_enabled"),
        }
        miss = []
        for section, keys in need.items():
            got = out.get(section) or {}
            miss += [f"{section}.{k}" for k in keys if k not in got]
        return (not miss, f"缺={miss}")
    print("对抗训练回归（4 类缺陷）")
    check("D1", "purge_self 不再 DELETE 视图（软删记忆 + 清人）", d1)
    check("D2", "问句/否定句不能把关系绑成恋人", d2)
    check("D3", "问句之后仍可用「我答应你」正常绑定", d3)
    check("D4", "傍晚「早点睡吧」不再被判时间感违规", d4)
    check("D5", "CLI ingest 缺文件 → 友好报错，不甩 traceback", d5)
    check("D6", "CLI reset 在独立进程里能正常删库（退出码 0）", d6)
    check("D7", "恋爱线的确定性回复也写进 events（上下文不断裂）", d7)
    check("D8", "日常句不再被误判为表白", d8)
    check("D9", "记忆里不许出现 openid（用名字替换）", d9)
    check("D10", "睡眠不是硬闸门：越聊越清醒、停聊睡回去", d10)
    check("D11", "命名剧本台词带 user_id/chat_type（上下文不断裂）", d11)
    check("D12", "命名流程能走到锁定（带标点的「就叫小满。」）", d12)
    check("D13", "夸奖不能被当成同意（不会误绑关系）", d13)
    check("D14", "群作用域隔离：静默/焦点/配额不跨群污染", d14)
    check("D15", "主动配额含夜里念头（today_count 不再漏算）", d15)
    check("D16", "换人后恋人位不串（session.seq 记账 + 旧 active 行结掉）", d16)
    check("D17", "关系日上限三维都钳（信任/吸引不再绕过上限）", d17)
    check("D18", "断网查词不写空缓存（不会锁一天）", d18)
    check("D19", "念头浮现只算一次额度（mark_surface 撞索引返回 False）", d19)
    check("D20", "关系流水带 user_id（分手信只汇收件人的账）", d20)
    check("D21", "搬迁不丢两本账（导出体含 relation_log / traits_log）", d21)
    check("D22", "恋人门槛读配置（改 threshold 立即生效）", d22)
    check("D23", "姓氏表只有一份（extract 与 scrub 共享同一对象）", d23)
    check("D24", "拉黑期他也留痕（user 事件不许丢）", d24)
    check("D25", "stability 不再收了不用（落进 meta 留痕）", d25)
    check("D26", "工具在 mock 下永不出网（web / 识图 / 提示全空）", d26)
    check("D27", "工具路由：查一下→搜索、链接→抓网页、有图→识图", d27)
    check("D28", "只发一张图也能走通主链（attachments 不断链）", d28)
    check("D29", "群聊工具开关：默认不调，开了才调", d29)
    check("D30", "声音环节默认不可用（关掉 / mock 都不出网）", d30)
    check("D31", "设置开关立刻生效并落 config.local.yaml", d31)
    check("D32", "声音独立接口：关闭时优雅拒绝 + 缓存键稳定", d32)
    check("D33", "声音限流：超过每分钟额度就拒绝", d33)
    check("D34", "语音缓存按 15 天清理（旧的删、新的留、0=不清）", d34)
    check("D35", "面板设置契约：工具/声音字段一个不少", d35)

    passed = sum(1 for r in RESULTS if r["ok"])
    print(f"\n结果：{passed} 通过，{len(RESULTS) - passed} 失败")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    a, _ = ap.parse_known_args()
    rc = main()
    if a.json:
        pathlib.Path(a.json).write_text(
            json.dumps({"results": RESULTS}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    raise SystemExit(rc)
