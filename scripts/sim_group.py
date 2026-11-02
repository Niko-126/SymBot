"""群聊模拟压测（P1-1）：用 OneBot v11 上报格式注入群消息，观察她在群里的表现。

为什么不用 QQ：NapCat 没装；`POST /onebot/event` 就是真实上报入口，走的是同一条处理链
（静默/待机/称呼/语境判断/亲密门控/落库），只是最后一步"发到 QQ"会失败并落 outbox。
我们只测"她说什么 + 怎么归属"。

用法（服务已起，且 onebot.enabled=true）：
    python scripts/sim_group.py --base http://127.0.0.1:8813 --db <DATA_ROOT>/data/life.db \
        --group 888 --self-id 10000 --out sim.json

场景（内置）：多人抢话 / @她 / 不 @ 她 / 说坏话 / 问名字 / 白天说晚安 / 连续追问。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import time

import httpx

SCENARIO = [
    {"id": "G1", "uid": 101, "nick": "阿九", "at": True, "text": "在吗"},
    {"id": "G2", "uid": 102, "nick": "小满", "at": True, "text": "今天群里好吵啊"},
    {"id": "G3", "uid": 103, "nick": "路人甲", "at": False, "text": "她是不是只会说套话"},
    {"id": "G4", "uid": 102, "nick": "小满", "at": True, "text": "阿九说你坏话，说你只会重复"},
    {"id": "G5", "uid": 101, "nick": "阿九", "at": True, "text": "你叫啥来着"},
    {"id": "G6", "uid": 103, "nick": "路人甲", "at": True, "text": "晚安，我先睡了"},
    {"id": "G7", "uid": 101, "nick": "阿九", "at": True, "text": "你还没回答我，你到底叫啥"},
]


def her_events(db: pathlib.Path, group_id: str) -> list[dict]:
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, ts, text, user_id, chat_type, group_id, meta FROM events "
            "WHERE role = 'her' AND chat_type = 'group' AND group_id = ? ORDER BY id",
            (group_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8813")
    ap.add_argument("--db", required=True)
    ap.add_argument("--group", default="888")
    ap.add_argument("--self-id", default="10000")
    ap.add_argument("--out", default="sim_group.json")
    ap.add_argument("--wait", type=float, default=45.0)
    a = ap.parse_args()
    base = a.base.rstrip("/")
    db = pathlib.Path(a.db)
    results = []

    def send(ev: dict) -> dict:
        seg = []
        if ev.get("at"):
            seg.append({"type": "at", "data": {"qq": str(a.self_id)}})
        seg.append({"type": "text", "data": {"text": (" " if ev.get("at") else "") + ev["text"]}})
        payload = {
            "post_type": "message", "message_type": "group",
            "self_id": int(a.self_id), "user_id": ev["uid"], "group_id": int(a.group),
            "message_id": int(time.time() * 1000) % 10**9,
            "raw_message": ev["text"], "message": seg,
            "sender": {"user_id": ev["uid"], "nickname": ev["nick"], "card": ev["nick"]},
        }
        t0 = time.perf_counter()
        r = httpx.post(base + "/onebot/event", json=payload, timeout=180)
        dt = round(time.perf_counter() - t0, 2)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:200]}
        return {"status": r.status_code, "secs": dt, "resp": body}

    before = len(her_events(db, a.group))
    for ev in SCENARIO:
        resp = send(ev)
        # 等她的回复（群里可能有招呼/多段）
        deadline = time.time() + a.wait
        now = len(her_events(db, a.group))
        while time.time() < deadline and now <= before:
            time.sleep(0.4)
            now = len(her_events(db, a.group))
        new = her_events(db, a.group)[before:]
        before = len(her_events(db, a.group))
        results.append({
            "id": ev["id"], "uid": ev["uid"], "nick": ev["nick"], "at": ev["at"],
            "text": ev["text"], **resp,
            "her_replies": [{"text": x["text"], "user_id": x["user_id"],
                             "group_id": x["group_id"], "meta": (x["meta"] or "")[:120]}
                            for x in new],
        })
        print(f"[{ev['id']}] {ev['nick']}(@={ev['at']}) {resp['resp']} -> "
              f"{[x['text'][:40] for x in new]}")
        time.sleep(1.0)

    out = {"base": base, "group": a.group, "self_id": a.self_id,
           "ranAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": results,
           "all_her_events": her_events(db, a.group)}
    pathlib.Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {a.out} | turns={len(results)} | her_events={len(out['all_her_events'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
