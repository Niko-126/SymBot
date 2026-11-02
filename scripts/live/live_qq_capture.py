"""真机聊天采集器：轮询真实 events 表，把每一条 QQ 消息连同她的回复、meta、状态一起落盘。

产物：data/_adv/live-qq-<ts>/
  transcript.jsonl   每条事件一行（含 user/her、来源、chat_type、group_id、文本、meta）
  inspect.jsonl      每次她说完话后的 /v1/inspect 快照（prompt 各段长度、recall、relation delta）
  state.jsonl        每轮之后的关系/人格/记忆计数快照
  capture.log        采集中日志

用法：python .ai_analysis/live_qq_capture.py --minutes 40
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
DB = REPO / "data" / "life.db"
BASE = "http://127.0.0.1:8787"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def get_json(path: str):
    try:
        with urllib.request.urlopen(BASE + path, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"{type(exc).__name__}: {exc}"}


def state_snapshot() -> dict:
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rel = dict(con.execute("SELECT * FROM relation WHERE id=1").fetchone() or {})
            users = [dict(r) for r in con.execute(
                "SELECT user_id, affinity, trust, attraction, aversion, stage FROM user_relation")]
            counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                      for t in ("events", "mem_item", "mem_cue", "open_threads", "lover")}
            traits = [dict(r) for r in con.execute("SELECT dim, value FROM traits")]
            sess = dict(con.execute("SELECT * FROM session WHERE id=1").fetchone() or {})
            return {"relation": rel, "user_relation": users, "counts": counts,
                    "traits": traits, "session": sess}
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=40)
    ap.add_argument("--interval", type=float, default=2.0)
    a = ap.parse_args()

    out = REPO / "data" / "_adv" / f"live-qq-{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    tr = (out / "transcript.jsonl").open("a", encoding="utf-8")
    insp = (out / "inspect.jsonl").open("a", encoding="utf-8")
    st = (out / "state.jsonl").open("a", encoding="utf-8")
    log = (out / "capture.log").open("a", encoding="utf-8")

    def say(msg: str) -> None:
        line = f"[{now()}] {msg}"
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    last = con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
    say(f"start id={last} db={DB} out={out}")
    deadline = time.time() + a.minutes * 60
    last_state = ""
    last_her_id = 0

    try:
        while time.time() < deadline:
            time.sleep(a.interval)
            rows = con.execute(
                "SELECT id, ts, role, kind, text, source, user_id, chat_type, group_id, meta "
                "FROM events WHERE id > ? ORDER BY id LIMIT 50", (last,)).fetchall()
            for r in rows:
                last = r["id"]
                rec = dict(r)
                tr.write(json.dumps(rec, ensure_ascii=False) + "\n")
                tr.flush()
                tag = "她" if r["role"] == "her" else "他"
                where = r["chat_type"] or "-"
                say(f"#{r['id']} {tag}[{where}/{r['source']}] {str(r['text'])[:120]}")
                if r["role"] == "her":
                    last_her_id = r["id"]
                    ins = get_json("/v1/inspect")
                    if not isinstance(ins, dict):
                        ins = {"_raw": str(ins)[:500]}
                    insp.write(json.dumps({"event_id": r["id"], "ts": now(),
                                           "inspect": ins}, ensure_ascii=False) + "\n")
                    insp.flush()
            if rows:
                snap = state_snapshot()
                key = json.dumps(snap.get("relation"), ensure_ascii=False) + str(
                    snap.get("counts"))
                if key != last_state:
                    last_state = key
                    st.write(json.dumps({"ts": now(), **snap}, ensure_ascii=False) + "\n")
                    st.flush()
                    say(f"state relation={snap.get('relation')} counts={snap.get('counts')}")
    except KeyboardInterrupt:
        say("interrupted")
    finally:
        con.close()
        for f in (tr, insp, st, log):
            f.close()
        print(json.dumps({"out": str(out), "last_her_event": last_her_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
