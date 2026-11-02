"""语料出口：把 `events` 表里的真实对话导成 `{context, reply}` 训练对。

为什么要这一步：内核把每一轮都写进 `events`（角色 / 文本 / 来源 / chat_type / user_id / meta），
但**没有任何出口**把它们变成能拿去微调、能拿去评测的语料。采了也导不出来，等于白采。

口径：
- 一行一个样本，`context` 是她回话之前的那几轮（默认 3 轮，`--turns` 可改），`reply` 是她这一句；
- **只读数据库**（`mode=ro`），不改任何状态，运行中的内核不受影响；
- 默认脱敏手机号 / 身份证 / 邮箱，命中数写进 `stats.json`（`--no-mask` 可关，关之前先看清内容）；
- 群聊按 `group_id` 切会话、私聊按 `user_id` 切，两者不会串上下文。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\export_corpus.py --stats      # 只看能导多少
    .\\.venv\\Scripts\\python.exe scripts\\export_corpus.py              # 导到 exports/corpus-<ts>/
    .\\.venv\\Scripts\\python.exe scripts\\export_corpus.py --turns 5 --limit 2000
    .\\.venv\\Scripts\\python.exe scripts\\export_corpus.py --out D:\\corpus --no-mask

产物：
    exports/corpus-<ts>/train.jsonl   一行一个 {context, reply, meta}
    exports/corpus-<ts>/stats.json    事件数 / 会话数 / 样本数 / 脱敏命中数

红线：`exports/` 已在 `.gitignore`，导出的东西永不入库（里面有真实对话）。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

_SKIP_ROLES = {"system"}
_CTX_SEP = "\n"


def _rows(db_path: pathlib.Path) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT id, ts, role, kind, text, source, meta, user_id, chat_type, group_id"
            " FROM events ORDER BY id"
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def _meta(raw: str) -> dict:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def _sess(row: dict) -> tuple:
    """会话键：私聊按人，群聊按群。老数据 chat_type 为空时用 group_id 兜底。"""
    ct = str(row.get("chat_type") or "").strip()
    gid = str(row.get("group_id") or "").strip()
    if not ct:
        ct = "group" if gid else "private"
    return (ct, int(row.get("user_id") or 0), gid)


def build_pairs(rows: list[dict], turns: int = 3) -> tuple[list[dict], dict]:
    """按会话切上下文，返回 (训练对, 统计)。纯函数，不碰磁盘。"""
    sessions: dict[tuple, list[dict]] = {}
    for r in rows:
        if str(r.get("role") or "") in _SKIP_ROLES or not str(r.get("text") or "").strip():
            continue
        sessions.setdefault(_sess(r), []).append(r)

    pairs: list[dict] = []
    for key, seq in sessions.items():
        for i, r in enumerate(seq):
            if str(r.get("role")) != "her":
                continue
            body = str(r.get("text") or "").strip()
            if not body:
                continue
            ctx_rows = [x for x in seq[max(0, i - turns):i]
                        if x is not r and str(x.get("text") or "").strip()]
            if not ctx_rows:
                continue  # 没有前文的第一句不进训练对
            m = _meta(r.get("meta") or "")
            ctx = _CTX_SEP.join(
                ("我：" if str(x.get("role")) == "user" else "她：") + str(x.get("text")).strip()
                for x in ctx_rows
            )
            pairs.append({
                "context": ctx,
                "reply": body,
                "meta": {
                    "ts": r.get("ts"),
                    "chat_type": key[0],
                    "user_id": key[1],
                    "group_id": key[2],
                    "source": r.get("source"),
                    "mode": str(m.get("mode") or ""),
                    "turns": len(ctx_rows),
                },
            })

    stat = {
        "events": len(rows),
        "sessions": len(sessions),
        "pairs": len(pairs),
        "turns": turns,
    }
    return pairs, stat


def _mask(text: str) -> tuple[str, int]:
    """手机号 / 身份证 / 邮箱脱敏。脱敏失败原样返回（导出不该因为脱敏挂掉）。"""
    try:
        from app.lifecorpus import pii

        out, findings = pii.mask_text(text)
        return out, len(findings)
    except Exception:  # noqa: BLE001
        return text, 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser("export_corpus")
    ap.add_argument("--db", default=str(REPO / "data" / "life.db"), help="库路径（只读打开）")
    ap.add_argument("--out", default="", help="输出目录（默认 exports/corpus-<ts>）")
    ap.add_argument("--turns", type=int, default=3, help="上下文取她回话前几轮（默认 3）")
    ap.add_argument("--no-mask", action="store_true", help="关掉 PII 脱敏（先看清内容再用）")
    ap.add_argument("--limit", type=int, default=0, help="只留最近 N 条（0 = 全部）")
    ap.add_argument("--stats", action="store_true", help="只统计不落盘")
    args = ap.parse_args(argv)

    db_path = pathlib.Path(args.db)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}")
        return 2
    rows = _rows(db_path)
    pairs, stat = build_pairs(rows, turns=max(1, args.turns))
    if args.limit and len(pairs) > args.limit:
        pairs = pairs[-args.limit:]  # 只留最近的
        stat["limited_to"] = args.limit
    stat["db"] = str(db_path)

    if args.stats:
        print(json.dumps(stat, ensure_ascii=False, indent=1))
        return 0

    out_dir = pathlib.Path(args.out) if args.out else (
        REPO / "exports" / f"corpus-{time.strftime('%Y%m%d-%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)

    masked_hits = 0
    lines = []
    for p in pairs:
        if not args.no_mask:
            p["context"], n1 = _mask(p["context"])
            p["reply"], n2 = _mask(p["reply"])
            masked_hits += n1 + n2
        lines.append(json.dumps(p, ensure_ascii=False))

    (out_dir / "train.jsonl").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    stat["masked_hits"] = masked_hits
    stat["out"] = str(out_dir)
    (out_dir / "stats.json").write_text(
        json.dumps(stat, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(stat, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
