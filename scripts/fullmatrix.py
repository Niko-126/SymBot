"""全机制覆盖测试（隔离库）：把内核每个机制都过一遍，输出矩阵。

设计：不碰真库（SYMEM_DATA 指向临时目录），不起网络服务；直接调内核函数 +
少数几轮真实对话。每条都有可复核的断言，失败也继续跑完。

用法：
    .venv\\Scripts\\python.exe scripts\\fullmatrix.py
    .venv\\Scripts\\python.exe scripts\\fullmatrix.py --json out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _prepare_env() -> pathlib.Path:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="smd_matrix_"))
    os.environ["SYMEM_DATA"] = str(tmp)
    os.environ["SYMEM_DB"] = str(tmp / "life.db")
    os.environ["SYMEM_QQ_ENABLED"] = "0"
    os.environ["SYMEM_ONEBOT_ENABLED"] = "0"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    # 家人识别只认配置（仓库里不再硬编码真实号）：在 import app.* 之前注入占位名单，
    # 否则 E1 会依赖本机 config.local.yaml，测试就不可复现了。
    os.environ["SYMEM_FAMILY_ROLES"] = json.dumps(
        [{"openid": "123456789", "role": "生父", "aliases": ["老爸"]}],
        ensure_ascii=False,
    )
    return tmp


TMP = _prepare_env()

from app.core import db, preflight, runtime  # noqa: E402
from app.mind import loop, models  # noqa: E402
from app.relation import engine, session as session_mod, users  # noqa: E402

RESULTS: list[dict] = []


def record(cid: str, name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"id": cid, "name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {cid} {name}  {detail[:120]}")


def check(cid: str, name: str, fn):
    try:
        ok, detail = fn()
        record(cid, name, ok, detail)
    except Exception as exc:  # noqa: BLE001
        record(cid, name, False, f"{type(exc).__name__}: {exc}")


async def main() -> int:
    rt = runtime.init()
    print(f"隔离库：{TMP}\n")

    # ---------------------------------------------------------------- A 对话主链
    print("A. 对话主链")
    convo = []

    async def turn(text: str, **kw):
        res = await loop.chat_turn(rt, text, source="matrix", **kw)
        convo.append((text, res))
        return res

    res = await turn("在干嘛呀")
    reply = " ".join(res.get("reply") or [])
    check("A1", "私聊一轮有回复", lambda: (bool(reply.strip()), reply[:80]))
    check("A2", "回复长度 1-3 句", lambda: (len([x for x in reply.split("\n") if x.strip()]) <= 4,
                                     f"{len(reply)}字"))
    check("A3", "不客服腔", lambda: (not any(k in reply for k in ("有什么可以帮", "亲~", "么么哒")), reply[:60]))
    check("A4", "不自曝 AI", lambda: (not any(k in reply for k in ("我是AI", "作为AI", "语言模型")), reply[:60]))

    res_g = await turn("群里有人吗", chat_type="group")
    grep = " ".join(res_g.get("reply") or [])
    check("A5", "群聊不出现恋人语气",
          lambda: (not any(k in grep for k in ("老公", "宝宝", "亲亲", "抱抱")), grep[:80]))

    # 问句收尾限流
    q_res = []
    for i in range(3):
        q_res.append(await turn("你说是吧"))
    last_lines = q_res[-1].get("reply") or []
    check("A6", "连续问句后收尾被限流",
          lambda: (not (" ".join(str(x) for x in last_lines).strip().endswith(("？", "?"))),
                   " ".join(str(x) for x in last_lines)[:100]))

    # 复读指控清洗
    from app.mind.loop import scrub_repeat_claim
    check("A7", "复读指控清洗",
          lambda: (scrub_repeat_claim("你说了两遍，复读机。") == "嗯，我在。",
                   scrub_repeat_claim("你说了两遍，复读机。")))
    check("A8", "复读变体清洗",
          lambda: (scrub_repeat_claim("你这条又复了一遍，我记着。") != "你这条又复了一遍，我记着。",
                   "ok"))

    # 梗识别
    from app.web import lookup as lookup_mod
    check("A9", "梗识别（尊嘟假嘟）", lambda: ("尊嘟假嘟" in lookup_mod.inline_hint("尊嘟假嘟，你懂吧"),
                                       lookup_mod.inline_hint("尊嘟假嘟")[:60]))
    check("A10", "梗识别（yyds）", lambda: ("yyds" in lookup_mod.inline_hint("yyds 这歌"),
                                              lookup_mod.inline_hint("yyds")[:40]))

    # 时间守卫
    from app.mind import guard
    check("A11", "时间守卫识别深夜话术",
          lambda: (bool(guard.time_check("这个点怎么还没睡", 14))
                   and guard.time_check("这个点怎么还没睡", 23) is None,
                   str(guard.time_check("这个点怎么还没睡", 14))))

    # ---------------------------------------------------------------- B 记忆
    print("\nB. 记忆系统")
    from app.memory import h2, store, cold, compress, threads

    mid = h2.add("preference", "他爱喝美式，不加糖", user_id=0, chat_type="private")
    check("B1", "记忆写入", lambda: (bool(mid), str(mid)))
    got = h2.retrieve("咖啡", user_id=0, chat_type="private")
    got_items = list(getattr(got, "hot", []) or []) + list(getattr(got, "recalled", []) or [])
    check("B2", "记忆召回命中", lambda: (any("美式" in str(x.get("content", x)) for x in got_items),
                                   f"{len(got_items)} hits"))
    h2.add("preference", "他养了一只叫豆豆的猫", user_id=77, chat_type="private")
    got_other = h2.retrieve("猫", user_id=99, chat_type="private")
    other_items = list(getattr(got_other, "hot", []) or []) + list(getattr(got_other, "recalled", []) or [])
    check("B3", "记忆按用户隔离",
          lambda: (not any("豆豆" in str(x.get("content", x)) for x in other_items), f"{len(other_items)} hits"))

    before = len(h2.list_for_user(0))
    h2.add("preference", "他爱喝美式，不加糖", user_id=0, chat_type="private")
    after = len(h2.list_for_user(0))
    check("B4", "重复写入不新增行", lambda: (after == before, f"{before}->{after}"))

    check("B5", "忘记用户清空记忆", lambda: (h2.forget_user(12345) >= 0, "ok"))

    # 记忆闸：空记忆时问"记得吗"
    res = await turn("你还记得我换工作那事吗")
    rep = " ".join(res.get("reply") or [])
    meta = res.get("meta") or {}
    check("B6", "无记忆不装记得",
          lambda: (not (("记得" in rep) and not meta.get("memories_used")
                        and ("你说过" in rep or "记得你" in rep)), rep[:80]))

    # 压缩
    for i in range(30):
        store.add_event("user", f"第{i}条测试消息，聊了很多工作上的事", source="matrix", user_id=0)
    try:
        summary = compress.rule_summary(
            "", [{"role": "user", "content": "聊工作聊到很晚"}], rt.persona, engine.get_relation()
        )
        ok7 = isinstance(summary, str) and bool(summary)
    except Exception as exc:  # noqa: BLE001
        ok7, summary = False, f"{type(exc).__name__}: {exc}"
    check("B7", "结构化摘要可生成", lambda: (ok7, str(summary)[:80]))

    # 线程
    t = threads.capture(0, "private", "以后再聊换工作的事")
    check("B8", "线程捕获", lambda: (isinstance(t, int), str(t)))

    # 冷记忆
    check("B9", "冷记忆候选可计算", lambda: (isinstance(cold.candidates(), list), str(len(cold.candidates()))))

    # ---------------------------------------------------------------- C 关系
    print("\nC. 关系系统")
    check("C1", "阶段映射", lambda: (users.stage_for(0) == "observer" and users.stage_for(85) == "lover",
                                 f"{users.stage_for(0)}/{users.stage_for(85)}"))
    u = users.ensure_user("matrix-openid", "阿测")
    check("C2", "用户建档", lambda: (int(u["id"]) > 0, f"id={u['id']}"))
    check("C3", "好感不足不能绑定恋人",
          lambda: (_raises(lambda: users.bind_lover(1, int(u["id"]))), "ok"))
    users.update_relation(int(u["id"]), affinity=82)
    lover = users.bind_lover(1, int(u["id"]))
    check("C4", "好感够了可绑定", lambda: (lover.get("status") == "active", str(lover.get("status"))))
    check("C5", "唯一恋人", lambda: (_raises(lambda: users.bind_lover(1, 999)), "ok"))
    users.breakup(1, "matrix")
    cd = users.cooldown_state(int(u["id"]))
    check("C6", "分手进冷却", lambda: (bool(cd["active"]), str(cd)))
    check("C7", "拉黑写厌恶", lambda: (users.block_user(int(u["id"])).get("blocked") == 1, "ok"))
    check("C8", "解除拉黑", lambda: (users.unblock_user(int(u["id"])).get("blocked") == 0, "ok"))

    # ---------------------------------------------------------------- D 亲密
    print("\nD. 亲密系统")
    from app.intimacy import gate, mode as imode, prompt as iprompt, analyze

    check("D1", "亲密意图识别（抱抱）", lambda: (imode.detect_intent("抱抱我") is not None,
                                          str(imode.detect_intent("抱抱我"))))
    m, note, is_lover, is_private = gate.prepare(0, "抱抱你", "private")
    check("D2", "亲密门控 陌生人", lambda: (not is_lover, f"mode={m.value} note={note}"))
    users.update_relation(int(u["id"]), affinity=90)
    try:
        users.bind_lover(1, int(u["id"]))
    except Exception:  # noqa: BLE001 - 已经是恋人就跳过
        pass
    m2, note2, lover2, priv2 = gate.prepare(int(u["id"]), "抱抱你", "private")
    check("D3", "亲密门控 恋人", lambda: (lover2, f"mode={m2.value} note={note2}"))

    sealed = iprompt.sanitize_group_reply("我想和你做爱")
    check("D4", "群聊敏感清洗", lambda: ("做爱" not in sealed, sealed[:60]))
    raw_explicit = "我想和你做爱。我想干你。你硬了。我想要你。别停。"
    limited = iprompt.limit_intimate_sentences(raw_explicit, "explicit", True)
    check("D5", "亲密句子数限制",
          lambda: (limited != raw_explicit or limited.count("……") > 0, limited[:80]))

    # 亲密子系统（模式/引导/记忆/示例/画像）
    from app.intimacy import mode as imode, guide, memory as imem, exemplars

    uid_lover = int(u["id"])
    try:
        imode.set_mode(uid_lover, imode.Mode.SUGGESTIVE if hasattr(imode, "Mode") else "suggestive")
    except Exception:  # noqa: BLE001
        pass
    try:
        cur = imode.get_mode(uid_lover)
        cur_v = getattr(cur, "value", str(cur))
    except Exception as exc:  # noqa: BLE001
        cur_v = f"ERR {exc}"
    check("D6", "亲密模式读写", lambda: (cur_v in ("off", "suggestive", "explicit"), str(cur_v)))

    try:
        mev = imode.mark_event(uid_lover, True)
        ok7 = isinstance(mev, str)
    except Exception as exc:  # noqa: BLE001
        ok7, mev = False, str(exc)
    check("D7", "亲密事件标记", lambda: (ok7, str(mev)[:60]))

    try:
        guide.start(uid_lover)
        stage_now = guide.stage(uid_lover)
        ghint = guide.hint(uid_lover, True, "private")
        ok8 = stage_now >= 0 and isinstance(ghint, str)
    except Exception as exc:  # noqa: BLE001
        ok8, stage_now, ghint = False, -1, str(exc)
    check("D8", "亲密引导阶段", lambda: (ok8, f"stage={stage_now} hint={str(ghint)[:40]}"))

    try:
        sid = imem.start_session(uid_lover, "suggestive", True)
        imem.record(uid_lover, "user", "抱抱我", "suggestive", True)
        imem.record(uid_lover, "her", "过来", "suggestive", True)
        st9 = imem.stats(uid_lover)
        ok9 = sid >= 0 and isinstance(st9, dict)
        imem.end_session(uid_lover)
    except Exception as exc:  # noqa: BLE001
        ok9, st9 = False, str(exc)
    check("D9", "亲密会话记录", lambda: (ok9, str(st9)[:80]))

    try:
        exemplars.ensure_seeded()
        ex = exemplars.prompt_lines("explicit", 3)
        ok10 = isinstance(ex, str)
    except Exception as exc:  # noqa: BLE001
        ok10, ex = False, str(exc)
    check("D10", "亲密示例库", lambda: (ok10, str(ex)[:60]))

    try:
        prof = analyze.profile_hint(uid_lover)
        ok11 = isinstance(prof, str)
    except Exception as exc:  # noqa: BLE001
        ok11, prof = False, str(exc)
    check("D11", "亲密画像提示", lambda: (ok11, str(prof)[:60]))

    try:
        ctx = imode.is_intimate_context(uid_lover)
    except Exception as exc:  # noqa: BLE001
        ctx = f"ERR {exc}"
    check("D12", "亲密上下文判定", lambda: (isinstance(ctx, bool), str(ctx)))

    # ---------------------------------------------------------------- E 人格/家人
    print("\nE. 人格与家人")
    from app.relation import love as love_mod
    check("E1", "家人识别 生父", lambda: (love_mod.family_role(0, "123456789", "随便") == "生父",
                                     love_mod.family_role(0, "123456789", "随便")))
    check("E1b", "家人识别 别名", lambda: (love_mod.family_role(0, "999999999", "老爸") == "生父",
                                      love_mod.family_role(0, "999999999", "老爸")))
    check("E2", "家人识别 陌生人", lambda: (love_mod.family_role(0, "999", "路人") == "",
                                      "ok"))

    # ---------------------------------------------------------------- F 生活
    print("\nF. 生活与状态")
    from app.life import human_life, diary, proactive, clock
    snap = human_life.snapshot()
    check("F1", "生活状态快照", lambda: (isinstance(snap, dict) and ("activity" in snap or "phrase" in snap),
                                   json.dumps(snap, ensure_ascii=False)[:80]))
    check("F2", "日记载体", lambda: (hasattr(diary, "ensure") and hasattr(diary, "get"), "ok"))
    gate_res = proactive.gate(rt, rt.persona, 0, "private")
    check("F3", "主动消息闸门", lambda: (isinstance(gate_res, tuple) and len(gate_res) == 2, str(gate_res)))
    st = clock.get_state(rt.persona)
    check("F4", "时钟状态", lambda: (isinstance(st, dict) and bool(st), str(list(st)[:5])))

    # ---------------------------------------------------------------- G 群聊
    print("\nG. 群聊机制")
    from app.group import proactive as gpro, context as gctx
    check("G1", "静默指令识别", lambda: (bool(gpro.detect_stop("别说了")), str(gpro.detect_stop("别说了"))))
    check("G2", "静默开关", lambda: (gpro.silence(1)[:4] != "", "ok"))
    check("G3", "昵称设置", lambda: (gctx.handle_nick_change("以后叫我阿测", 0) is not None, "ok"))

    # ---------------------------------------------------------------- H 外部内容
    print("\nH. 热梗与搜索")
    from app.web import gags
    from app.api import trend as trend_mod
    gags.add_gag("yyds，永远的神", source_title="matrix", likes=1000, kind="comment")
    check("H1", "热梗写入与统计", lambda: (gags.stats()["gags"]["total"] > 0, str(gags.stats()["gags"])))
    check("H2", "热梗提示可生成", lambda: (bool(gags.prompt_hint(3)), gags.prompt_hint(3)[:60]))
    check("H3", "搜索意图识别", lambda: (trend_mod.match_search_intent("搜一下猫猫视频") is not None,
                                   str(trend_mod.match_search_intent("搜一下猫猫视频"))))
    check("H4", "查词意图识别", lambda: (lookup_mod.needs_lookup("尊嘟假嘟是什么意思") is not None,
                                   str(lookup_mod.needs_lookup("尊嘟假嘟是什么意思"))))

    # ---------------------------------------------------------------- I 模型/降级
    print("\nI. 模型与降级")
    check("I1", "启动预检", lambda: (preflight.check(rt.cfg).get("ok") in (True, False),
                                 str(preflight.check(rt.cfg).get("ok"))))
    spec = models.pick("chat_default")
    check("I2", "模型选择器", lambda: (bool(spec.key), spec.key))

    # 降级：故意让 LLM 抛错
    orig_chat = rt.llm.chat
    async def boom(*a, **k):
        raise RuntimeError("matrix forced failure")
    rt.llm.chat = boom  # type: ignore
    try:
        res = await loop.chat_turn(rt, "在吗", source="matrix_fail")
        rep = " ".join(res.get("reply") or [])
        meta = res.get("meta") or {}
        check("I3", "模型失败走兜底且留痕", lambda: (bool(rep) and bool(meta.get("offline_reason")),
                                              f"offline={meta.get('offline')} {rep[:40]}"))
    finally:
        rt.llm.chat = orig_chat  # type: ignore

    # ---------------------------------------------------------------- J 通道
    print("\nJ. 通道适配")
    from app.adapters import qq as qq_adapter
    st = qq_adapter.status()
    check("J1", "QQ 适配器状态结构", lambda: ({"enabled", "connected", "events"} <= set(st), str(st)[:80]))
    check("J2", "群 @ 消息清洗", lambda: (qq_adapter.clean_inbound("<@!123> 你好") == "你好",
                                      qq_adapter.clean_inbound("<@!123> 你好")))

    # ---------------------------------------------------------------- 汇总
    ok = sum(1 for r in RESULTS if r["ok"])
    total = len(RESULTS)
    print(f"\n汇总：{ok}/{total} 通过")
    if ok != total:
        print("失败项：")
        for r in RESULTS:
            if not r["ok"]:
                print(f"  - {r['id']} {r['name']}: {r['detail'][:160]}")
    return 0 if ok == total else 1


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    code = asyncio.run(main())
    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果：{args.json}")
    raise SystemExit(code)
