from __future__ import annotations

import argparse
import json
import pathlib
import sys

from app import __version__
from app.core import runtime


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    rt = runtime.init()
    print(f"衡忆 {__version__}  personae={rt.persona.name}  provider={'mock' if rt.cfg.provider.is_mock else rt.cfg.provider.model}")
    print(f"db={rt.cfg.db_file}")
    print(f"http://{rt.cfg.server.host}:{args.port}/")
    uvicorn.run("app.main:app", host=rt.cfg.server.host, port=args.port, log_level="info")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    rt = runtime.init()
    from app.memory import ingest as ingest_mod

    path = pathlib.Path(args.path).expanduser()
    if not path.exists():
        # 缺文件是用户输入问题，不该甩一屏 traceback（对抗训练 CLI 探针实测，见 adv 报告）﻿﻿‌‍​​‌‌‍‍‍‍‍‍﻿﻿‍﻿﻿‌‍﻿​‌‍﻿‌﻿﻿‌‌‌​﻿
        print(f"找不到文件：{path}", file=sys.stderr)
        return 1
    report = ingest_mod.ingest(path, rt.persona)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


def _cmd_init(_: argparse.Namespace) -> int:
    rt = runtime.init()
    print(json.dumps({"ok": True, "db": str(rt.cfg.db_file), "persona": rt.persona.name}, ensure_ascii=False))
    return 0


def _cmd_reference(args: argparse.Namespace) -> int:
    """参考语料：只学风格与相处方式，不写用户记忆、不动好感。"""
    from collections import Counter

    rt = runtime.init()
    from app.memory import reference

    target = pathlib.Path(args.path).expanduser()
    files = sorted(target.glob("part*.txt")) if target.is_dir() else [target]
    if not files:
        print("没有找到可导入的文件", file=sys.stderr)
        return 1

    # 第一遍：收集按月统计 + 全局人名
    from app.memory import scrub as scrub_mod

    merged: dict = {}
    global_names: Counter = Counter()
    for f in files:
        try:
            ev, _meta = reference._parse_file(f)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            continue
        high, _mid = scrub_mod.detect_names(ev, min_mid=1)
        for k, v in high.items():
            global_names[k] += int(v)
        for month, st in reference._month_stats(ev).items():  # noqa: SLF001
            agg = merged.setdefault(month, {"month": month, "n": 0, "her": 0, "sweet": 0, "strain": 0})
            for key in ("n", "her", "sweet", "strain"):
                agg[key] += st[key]
    global_arc = reference.classify_stats(merged)
    print(f"全局分期：{global_arc.counts()}", file=sys.stderr)
    print(f"全局识别到的人名：{len(global_names)} 个", file=sys.stderr)

    if args.force:
        from app.core import db as db0

        with db0.cursor() as cur:
            cur.execute("DELETE FROM ref_turns")
            cur.execute("DELETE FROM ref_quotes")
        print("已清空旧的参考语料，准备重导", file=sys.stderr)

    turns = quotes = 0
    scrub_hits: Counter = Counter()
    detected: Counter = Counter()
    suspected: Counter = Counter()
    periods: Counter = Counter()
    per_file: list[str] = []

    for i, f in enumerate(files, 1):
        try:
            r = reference.ingest_reference(
                f, rt.persona.name, names=list(global_names),
                force=args.force, min_mid=1,
                write_candidates=False, arc=global_arc,
            )
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{len(files)}] {f.name}: 失败 {e}", file=sys.stderr)
            continue
        if r.get("already_imported"):
            per_file.append(f"| {f.name} | 已导入过，跳过 | - | - |")
            continue
        turns += int(r.get("turns_written", 0))
        quotes += int(r.get("quotes_written", 0))
        for k, v in (r.get("scrub") or {}).get("by_kind", {}).items():
            scrub_hits[k] += int(v)
        for d in r.get("detected_names") or []:
            detected[d["token"]] += int(d["count"])
        for d in r.get("suspected_names") or []:
            suspected[d["token"]] += int(d["count"])
        periods.update(r.get("period_counts") or {})
        per_file.append(
            f"| {f.name} | {r.get('turns_written', 0)} | {r.get('quotes_written', 0)} | "
            f"{', '.join(f'{k}:{v}' for k, v in (r.get('period_counts') or {}).items())} |"
        )

    # 聚合后：至少被叫过 3 次才值得人工看
    suspected = Counter({k: v for k, v in suspected.items() if v >= 3 and k not in detected})
    # 附上"全局出现次数"，让人判断它是名字还是普通词组
    from app.core import db as db_mod

    totals: dict[str, int] = {}
    for tok in list(suspected)[:260]:
        row = db_mod.one("SELECT COUNT(*) AS c FROM ref_turns WHERE text LIKE ?", (f"%{tok}%",))
        if row:
            totals[tok] = int(row["c"])
    # 黑名单残留自检：这些必须为 0
    from app.memory.scrub import load_denylist

    deny_tokens = load_denylist(pathlib.Path("data/denylist.txt"))
    leaks = {}
    for tok in deny_tokens:
        row = db_mod.one("SELECT COUNT(*) c FROM ref_turns WHERE text LIKE ?", (f"%{tok}%",))
        leaks[tok] = int(row["c"]) if row else 0
    if any(leaks.values()):
        print(f"[警告] 黑名单仍有残留：{leaks}", file=sys.stderr)
    else:
        print(f"[校验] 黑名单 {len(deny_tokens)} 个词，残留 0", file=sys.stderr)

    reference._write_candidates(dict(suspected), dict(detected))  # noqa: SLF001
    _write_import_report(rt, turns, quotes, scrub_hits, detected, suspected, periods, per_file, totals)

    print(json.dumps({
        "ok": True,
        "files": len(files),
        "turns": turns,
        "quotes": quotes,
        "periods": dict(periods),
        "scrub": dict(scrub_hits),
        "auto_replaced_names": len(detected),
        "suspected_names": len(suspected),
        "denylist_leaks": leaks,
        "status": reference.status(),
    }, ensure_ascii=False, indent=2))
    return 0


def _write_import_report(rt, turns, quotes, scrub_hits, detected, suspected, periods, per_file, totals=None) -> None:
    from collections import Counter
    import datetime

    out = pathlib.Path("data")
    out.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 参考语料导入报告",
        "",
        f"生成时间：{datetime.datetime.now().isoformat(timespec='seconds')}",
        f"人物：{rt.persona.name}　文件数：{len(per_file)}",
        "",
        "## 总量",
        "",
        f"- 写入参考轮次：**{turns}**",
        f"- 收集原话：**{quotes}**",
        f"- 去敏替换：**{sum(scrub_hits.values())}** 处",
        "",
        "## 情绪分期",
        "",
        "| 阶段 | 卷数 |",
        "|---|---|",
    ]
    labels = {"honeymoon": "热恋期", "stable": "稳定期", "cooling": "降温期", "break": "破裂期", "unknown": "未定"}
    for k, v in periods.most_common():
        lines.append(f"| {labels.get(k, k)} | {v} |")
    lines += ["", "## 去敏统计", "", "| 类型 | 次数 |", "|---|---|"]
    for k, v in scrub_hits.most_common():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## 已自动替换的名字（不会再出现在语料里）", ""]
    lines += [f"- {k} ×{v}" for k, v in detected.most_common(120)]
    lines += ["", "## 未替换、需要你确认的名字", ""]
    totals = totals or {}
    lines += [
        f"- {k} ×{v}　（全局出现 ×{totals.get(k, '?')}）"
        for k, v in suspected.most_common(300)
    ]
    lines += [
        "",
        "## 怎么处理",
        "",
        "1. 把上面「需要确认」里确实该删的，每行一个复制到 `data/denylist.txt`。",
        "2. 重新导入（会先删掉同源旧数据，不会重复）：",
        "",
        "   ```powershell",
        r"   python -m app.cli reference 语料\jilu --force",
        "   ```",
        "",
        "3. 不需要删的不用管，它们不会被当成名字。",
        "",
        "## 分卷明细",
        "",
        "| 文件 | 轮次 | 原话 | 分期 |",
        "|---|---|---|---|",
    ] + per_file
    (out / "import-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _cmd_arc(args: argparse.Namespace) -> int:
    rt = runtime.init()
    from app.memory import reference

    target = pathlib.Path(args.path).expanduser()
    files = sorted(target.glob("part*.txt")) if target.is_dir() else [target]
    events = []
    for f in files:
        ev, _meta = reference._parse_file(f)  # noqa: SLF001 - 分析用
        events.extend(ev)
    arc = reference.classify_arc(events)
    print(f"{'月份':8} {'消息':>8} {'她占比':>7} {'甜/千':>7} {'负/千':>7} {'判定'}")
    for row in arc.table():
        label = reference.PERIOD_LABELS.get(row["period"], row["period"])
        print(f"{row['month']:8} {row['messages']:8d} {row['her_ratio']*100:6.0f}% "
              f"{row['sweet_rate']:7.1f} {row['strain_rate']:7.1f} {label}")
    print(json.dumps({"counts": arc.counts()}, ensure_ascii=False))
    return 0


def _cmd_anchors(args: argparse.Namespace) -> int:
    rt = runtime.init()
    from app.memory import anchors as anchors_mod

    stats = anchors_mod.build(min_per_scene=args.per_scene, sample_extra=args.extra)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print("\n=== 固定声纹（当前关系阶段可用）===")
    for a in anchors_mod.pick_fixed(0, 12):
        print("  ", a)
    print("\n=== 各场景样本（低阶段可见）===")
    for scene in ("日常", "关心", "撒娇", "生气", "自嘲", "亲密", "深夜"):
        items = anchors_mod.pick_dynamic(scene, 0, 4)
        if items:
            print(f"  [{scene}] " + " / ".join(items))
    return 0


def _cmd_cold(args: argparse.Namespace) -> int:
    import asyncio

    rt = runtime.init()
    from app.memory import cold

    result = asyncio.run(cold.compact_cold(rt, rt.persona, dry_run=bool(args.dry)))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(cold.stats(), ensure_ascii=False))
    return 0


def _cmd_compress(args: argparse.Namespace) -> int:
    import asyncio

    rt = runtime.init()
    from app.memory import compress
    from app.relation import engine

    result = asyncio.run(compress.compact(rt, rt.persona, engine.get_relation()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(compress.status(), ensure_ascii=False))
    return 0


def _cmd_letter(_: argparse.Namespace) -> int:
    rt = runtime.init()
    text = rt.persona.letter_template()
    if not text:
        print("这个人物包没有配置交接信模板", file=sys.stderr)
        return 1
    print(text)
    return 0


def _cmd_reset(_: argparse.Namespace) -> int:
    """清空全部状态（记忆、好感、对话）。人物设定不受影响。"""
    rt = runtime.init()
    base = str(rt.cfg.db_file)
    busy: list[str] = []
    for suffix in ("", "-wal", "-shm"):
        p = pathlib.Path(base + suffix)
        if not p.exists():
            continue
        try:
            p.unlink()
        except OSError as exc:
            # 主库删不掉 = 服务还在用（真失败）；-wal/-shm 是派生文件，
            # 删不掉不致命（SQLite 会自己重建），只记录不报错。
            note = f"{p.name}: {exc.__class__.__name__}"
            if suffix == "":
                busy.append(note)
            else:
                print(f"[warn] 派生文件未删除（不影响 reset）：{note}", file=sys.stderr)
    if busy:
        print(
            "数据库文件正被占用，先停掉服务再 reset：" + "；".join(busy),
            file=sys.stderr,
        )
        return 1
    from app.core import db as db_mod
    from app.relation import engine

    db_mod.init(rt.cfg.db_file)
    engine.init_traits(rt.persona)
    print(json.dumps({"ok": True, "reset": base}, ensure_ascii=False))
    return 0


def _cmd_plugin(args: argparse.Namespace) -> int:
    """R 级亲密插件的装/卸/启/停。

    设计目标（owner 要求：易拆卸、零上手门槛）：
      - status   看包在不在、开关开没开、走哪条链路
      - enable   把 config.local.yaml 的 intimacy.enabled 改成 true
      - disable  改成 false（插件还在，但主链回退到基础行为）
      - remove   把 app/intimacy/r18 整目录移到 data/plugins_backup/ 下（可 install 回来）
      - install  从最近一次 remove 的备份（或 --from 指定目录）复制回 app/intimacy/r18
    """
    import importlib
    import shutil
    from datetime import datetime

    from app.core.paths import DATA_ROOT, RESOURCE_ROOT

    pkg = pathlib.Path(RESOURCE_ROOT) / "app" / "intimacy" / "r18"
    # 备份落在数据目录下（data/ 已被 .gitignore 忽略），不要污染仓库根
    backup_root = pathlib.Path(DATA_ROOT) / "data" / "plugins_backup"

    def _status() -> dict:
        info: dict = {}
        try:
            from app.intimacy import r18

            info = dict(r18.info())
        except Exception as exc:  # noqa: BLE001
            info = {"available": False, "enabled": False,
                    "error": f"{type(exc).__name__}: {exc}"}
        try:
            from app.intimacy import gate as gate_mod

            info["backend"] = gate_mod.backend()
        except Exception as exc:  # noqa: BLE001
            info["backend"] = f"error: {type(exc).__name__}"
        info["path"] = str(pkg)
        info["dir_exists"] = pkg.exists()
        return info

    def _write_switch(on: bool) -> pathlib.Path:
        import yaml

        path = pathlib.Path(DATA_ROOT) / "config.local.yaml"
        data: dict = {}
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                data = {}
        sec = data.get("intimacy") if isinstance(data.get("intimacy"), dict) else {}
        sec["enabled"] = bool(on)
        data["intimacy"] = sec
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return path

    action = str(getattr(args, "action", "status"))

    if action == "status":
        print(json.dumps(_status(), ensure_ascii=False, indent=1))
        return 0

    if action in ("enable", "disable"):
        where = _write_switch(action == "enable")
        print(json.dumps({"ok": True, "wrote": str(where),
                          "intimacy.enabled": action == "enable",
                          "note": "内核进程内的配置有缓存，重启 serve 后生效",
                          "status": _status()}, ensure_ascii=False, indent=1))
        return 0

    if action == "remove":
        if not pkg.exists():
            print(json.dumps({"ok": False, "reason": "插件目录本来就不在", "path": str(pkg)},
                             ensure_ascii=False))
            return 1
        backup_root.mkdir(parents=True, exist_ok=True)
        dst = backup_root / f"r18-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        shutil.move(str(pkg), str(dst))
        importlib.invalidate_caches()
        print(json.dumps({"ok": True, "moved_to": str(dst), "status": _status()},
                         ensure_ascii=False, indent=1))
        return 0

    if action == "install":
        src = str(getattr(args, "src", "") or "")
        if src:
            src_path: pathlib.Path | None = pathlib.Path(src)
        else:
            cands = sorted(backup_root.glob("r18-*"))
            src_path = cands[-1] if cands else None
        if src_path is None or not src_path.exists():
            print(json.dumps({"ok": False,
                              "reason": "找不到可安装的源：先 remove 一次生成备份，或用 --from 指定目录"},
                             ensure_ascii=False))
            return 1
        if pkg.exists():
            print(json.dumps({"ok": False, "reason": "目标已存在，先 remove", "path": str(pkg)},
                             ensure_ascii=False))
            return 1
        shutil.copytree(src_path, pkg)
        importlib.invalidate_caches()
        print(json.dumps({"ok": True, "installed_from": str(src_path), "status": _status()},
                         ensure_ascii=False, indent=1))
        return 0

    print(json.dumps({"ok": False, "reason": f"未知动作 {action}"}, ensure_ascii=False))
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser("衡忆", description="可独立运行的拟人对话智能体内核（SymBot）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="启动内核与面板")
    p_serve.add_argument("--port", type=int, default=8787)
    p_serve.set_defaults(func=_cmd_serve)

    p_ing = sub.add_parser("ingest", help="导入聊天记录 TXT")
    p_ing.add_argument("path")
    p_ing.set_defaults(func=_cmd_ingest)

    p_init = sub.add_parser("init", help="初始化数据库与人物")
    p_init.set_defaults(func=_cmd_init)

    p_reset = sub.add_parser("reset", help="清空全部状态（记忆、好感、对话）")
    p_reset.set_defaults(func=_cmd_reset)

    p_anchors = sub.add_parser("anchors", help="从参考语料重建锚点池")
    p_anchors.add_argument("--per-scene", type=int, default=24)
    p_anchors.add_argument("--extra", type=int, default=40)
    p_anchors.set_defaults(func=_cmd_anchors)

    p_cold = sub.add_parser("cold", help="合并冷记忆（时间+主题）")
    p_cold.add_argument("--dry", action="store_true", help="只预览，不写入")
    p_cold.set_defaults(func=_cmd_cold)

    p_compress = sub.add_parser("compress", help="手动把旧对话折成线索")
    p_compress.set_defaults(func=_cmd_compress)

    p_letter = sub.add_parser("letter", help="打印记忆交接信模板")
    p_letter.set_defaults(func=_cmd_letter)

    p_ref = sub.add_parser("reference", help="导入参考语料（只学风格，不写用户记忆）")
    p_ref.add_argument("path", help="单个分卷或整个目录")
    p_ref.add_argument("--force", action="store_true", help="强制重导（先删同源旧数据）")
    p_ref.set_defaults(func=_cmd_reference)

    p_arc = sub.add_parser("arc", help="只看情绪分期，不写库")
    p_arc.add_argument("path")
    p_arc.set_defaults(func=_cmd_arc)

    p_plugin = sub.add_parser(
        "plugin", help="R 级亲密插件（app/intimacy/r18/）的装/卸/启/停")
    p_plugin.add_argument(
        "action", choices=("status", "enable", "disable", "install", "remove"),
        help="status 看状态；enable/disable 改开关；install/remove 装卸目录")
    p_plugin.add_argument("--from", dest="src", default="", help="install 时从哪个目录复制（默认取最近一次 remove 的备份）")
    p_plugin.set_defaults(func=_cmd_plugin)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
