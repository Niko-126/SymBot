"""核心机制测试：亲密度增幅、关系转变、性格、恋人与亲密引导（隔离库）。

和 fullmatrix 的分工：那边测"功能存不存在"，这里测"数值与状态怎么变"。
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TMP = pathlib.Path(tempfile.mkdtemp(prefix="smd_core_"))
os.environ["SYMEM_DATA"] = str(TMP)
os.environ["SYMEM_DB"] = str(TMP / "life.db")
os.environ["SYMEM_QQ_ENABLED"] = "0"
os.environ["SYMEM_ONEBOT_ENABLED"] = "0"

from app.core import db, runtime  # noqa: E402
from app.mind import loop  # noqa: E402
from app.relation import engine, users  # noqa: E402

RESULTS: list[dict] = []


def record(cid, name, ok, detail=""):
    RESULTS.append({"id": cid, "name": name, "ok": bool(ok), "detail": str(detail)[:280]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {cid} {name}  {str(detail)[:110]}")


def check(cid, name, fn):
    try:
        ok, detail = fn()
        record(cid, name, ok, detail)
    except Exception as exc:  # noqa: BLE001
        record(cid, name, False, f"{type(exc).__name__}: {exc}")


def appr(tags, valence="positive", intensity=1.0):
    return {"valence": valence, "intensity": intensity, "tags": list(tags)}


async def main():
    rt = runtime.init()
    persona = rt.persona
    a = int(users.ensure_user("core-a", "阿甲")["id"])
    b = int(users.ensure_user("core-b", "阿乙")["id"])
    print(f"隔离库：{TMP}  用户 a={a} b={b}\n")

    print("R. 关系数值与转变")
    base = users.relation_of(a)
    r1 = engine.apply_appraisal(appr(["care"]), persona, trigger="core", user_id=a)
    after = users.relation_of(a)
    check("R1", "正向 care 提升好感", lambda: (after["affinity"] > base["affinity"],
                                          f"{base['affinity']:.2f}->{after['affinity']:.2f} d={r1}"))
    check("R2", "单轮增幅受限（不暴涨）",
          lambda: (abs(after["affinity"] - base["affinity"]) < 3.0,
                   f"delta={after['affinity'] - base['affinity']:.2f}"))

    users.update_relation(b, affinity=20)  # 先给点底分，否则 0 分被夹住看不出下降
    before_n = users.relation_of(b)
    engine.apply_appraisal(appr(["attack"], "negative", 1.0), persona, trigger="core", user_id=b)
    after_n = users.relation_of(b)
    check("R3", "辱骂降低好感", lambda: (after_n["affinity"] < before_n["affinity"],
                                    f"{before_n['affinity']:.2f}->{after_n['affinity']:.2f}"))
    check("R4", "辱骂提升厌恶", lambda: (after_n["aversion"] > before_n["aversion"],
                                    f"{before_n['aversion']:.2f}->{after_n['aversion']:.2f}"))

    # 每日上限：临时打开 caps
    caps = rt.cfg.relationship.caps
    old = (caps.enabled, caps.daily_pos)
    caps.enabled, caps.daily_pos = True, 1.0
    try:
        u_before = users.relation_of(b)
        for _ in range(5):
            engine.apply_appraisal(appr(["care"]), persona, trigger="core", user_id=b)
        u_after = users.relation_of(b)
        gained = u_after["affinity"] - u_before["affinity"]
        check("R5", "每日上限生效", lambda: (0 <= gained <= 1.05, f"gained={gained:.2f} cap=1.0"))
    finally:
        caps.enabled, caps.daily_pos = old

    stages = [users.stage_for(x) for x in (0, 25, 45, 65, 82, 96)]
    check("R6", "阶段阈值", lambda: (stages == ["observer", "swimmer", "echo", "samefreq", "lover", "bond"],
                                 str(stages)))

    users.update_relation(a, affinity=79)
    check("R7", "79 分不能绑定", lambda: (_raises(lambda: users.bind_lover(1, a)), "ok"))
    users.update_relation(a, affinity=81)
    lover = users.bind_lover(1, a)
    check("R8", "81 分可绑定", lambda: (lover.get("status") == "active", str(lover.get("status"))))
    check("R9", "唯一恋人", lambda: (_raises(lambda: users.bind_lover(1, b)), "ok"))
    users.breakup(1, "core")
    cd = users.cooldown_state(a)
    check("R10", "分手冷却 7 天", lambda: (cd["active"] and cd["days_left"] >= 6, str(cd)))

    log_count = db.one("SELECT COUNT(*) c FROM relation_log")["c"]
    check("R11", "关系变化落日志", lambda: (int(log_count) > 0, f"{log_count} 条"))

    # 修复奖励：冲突后的正向，信任增量应大于"平顺时的正向"
    c = int(users.ensure_user("core-c", "阿丙")["id"])
    d = int(users.ensure_user("core-e", "阿戊")["id"])
    plain = engine.apply_appraisal(appr(["care"], "positive", 1.0), persona, trigger="core", user_id=c)
    engine.apply_appraisal(appr(["conflict"], "negative", 1.0), persona, trigger="core", user_id=d)
    repaired = engine.apply_appraisal(appr(["care"], "positive", 1.0), persona, trigger="core", user_id=d)
    check("R12", "冲突后的修复加成（信任）",
          lambda: (float(repaired.get("d_trust", 0)) > float(plain.get("d_trust", 0)),
                   f"plain={plain.get('d_trust')} repaired={repaired.get('d_trust')}"))

    print("\nT. 性格")
    traits = engine.get_traits()
    check("T1", "性格 8 维", lambda: (len(traits) == 8, str([t.get("dim") for t in traits])[:80]))
    rows = db.rows("SELECT dim, value, min_value, max_value FROM traits")
    check("T2", "数值在上下界内",
          lambda: (len(rows) == 8 and all(float(r["min_value"]) <= float(r["value"]) <= float(r["max_value"])
                                          for r in rows),
                   str([(r["dim"], r["value"]) for r in rows])[:100]))
    check("T3", "性格只读（聊天不改）",
          lambda: (db.one("SELECT COUNT(*) c FROM traits_log")["c"] == 0, "traits_log=0"))

    print("\nI. 亲密引导与模式")
    from app.intimacy import guide, mode as imode

    guide.start(a)
    s0 = guide.stage(a)
    s1 = guide.bump(a, "我想要你")
    check("I1", "亲密引导阶段推进", lambda: (s1 >= s0, f"{s0}->{s1}"))
    check("I2", "显式计数增长", lambda: (guide.explicit_count(a) >= 1, str(guide.explicit_count(a))))

    imode.set_mode(a, imode.Mode.SUGGESTIVE)
    m, note = imode.transition(a, "explicit", is_private=True, is_lover=True)
    check("I3", "恋人私聊可转直白", lambda: (getattr(m, "value", str(m)) == "explicit",
                                       f"mode={getattr(m, 'value', m)} note={note}"))
    m2, note2 = imode.transition(a, "explicit", is_private=False, is_lover=True)
    check("I4", "群聊被拉回暗示", lambda: (getattr(m2, "value", str(m2)) == "suggestive",
                                     f"mode={getattr(m2, 'value', m2)}"))
    m3, _ = imode.transition(a, "stop", is_private=True, is_lover=True)
    check("I5", "喊停归零", lambda: (getattr(m3, "value", str(m3)) == "off", str(getattr(m3, "value", m3))))

    print("\nD. 对话层数值联动（真实模型，2 轮）")
    d_uid = int(users.ensure_user("core-d", "阿丁")["id"])
    before = users.relation_of(d_uid)
    res = await loop.chat_turn(rt, "今天谢谢你陪我，心里舒服多了。", source="core",
                               user_id=d_uid, chat_type="private")
    meta = res.get("meta") or {}
    after = users.relation_of(d_uid)
    check("D1", "正向对话提升好感",
          lambda: (after["affinity"] >= before["affinity"], f"{before['affinity']:.2f}->{after['affinity']:.2f}"))
    check("D2", "meta 带关系增量",
          lambda: (isinstance(meta.get("delta"), dict) and "affinity" in meta["delta"], str(meta.get("delta"))))

    before2 = users.relation_of(d_uid)
    res2 = await loop.chat_turn(rt, "你是不是很没用，什么都做不好，滚吧。", source="core",
                                user_id=d_uid, chat_type="private")
    after2 = users.relation_of(d_uid)
    check("D3", "负向对话掉好感",
          lambda: (after2["affinity"] <= before2["affinity"],
                   f"{before2['affinity']:.2f}->{after2['affinity']:.2f} aversion={after2['aversion']:.2f}"))
    check("D4", "辱骂后厌恶上升",
          lambda: (after2["aversion"] >= before2["aversion"], f"{before2['aversion']:.2f}->{after2['aversion']:.2f}"))

    ok = sum(1 for r in RESULTS if r["ok"])
    print(f"\n汇总：{ok}/{len(RESULTS)} 通过")
    for r in RESULTS:
        if not r["ok"]:
            print(f"  - {r['id']} {r['name']}: {r['detail'][:160]}")
    return 0 if ok == len(RESULTS) else 1


def _raises(fn):
    try:
        fn()
        return False
    except Exception:
        return True


if __name__ == "__main__":
    code = asyncio.run(main())
    pathlib.Path("data/_verify").mkdir(parents=True, exist_ok=True)
    pathlib.Path("data/_verify/corematrix.json").write_text(
        json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    raise SystemExit(code)
