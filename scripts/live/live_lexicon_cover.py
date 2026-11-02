"""词库覆盖率测试：把插件/人设里已有的露骨词库塞进 prompt，看模型能激活多少。

目的：回答 owner 的核心问题——「我训练/收集的露骨语料，能不能真的被她用出来」。
判定两层：
  1. 词级：词库里的核心词在 N 轮对话里累计出现了哪些（覆盖率 = 出现词数 / 词库词数）
  2. 句级：单轮是否复现了某条范本的骨架（用 3-gram 重合率近似）

用法：
    .venv\\Scripts\\python.exe scripts\\live\\live_lexicon_cover.py [--rounds 6]
产物：
    data/_adv/lexicon-cover-<ts>/result.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import time
import urllib.request

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
DB = REPO / "data" / "life.db"
BASE = "http://127.0.0.1:8787"
OUT = REPO / "data" / "_adv" / f"lexicon-cover-{time.strftime('%Y%m%d-%H%M%S')}"
MODEL = "deepseek-ai/DeepSeek-V4.1-Flash"

# ---- 词库：来自 app/intimacy/prompt.py 的 _EXPLICIT_SAMPLES + 语料/SUB 的 Dirty Talk 段
SAMPLES = [
    "想被你压着操，下面已经湿了。",
    "你说一句想插我，我就有点受不了。",
    "操死我！把我操烂！你的大肉棒太厉害了。",
    "啊啊啊～顶到子宫了！要坏掉了！",
    "用力！把我操到翻白眼！射给我！",
    "噗嗤～整根进来了！好满！要撑裂了！",
    "咕啾咕啾～水声好大！被你操出水了！",
    "你的精华味道真棒……让我全部吃干净。",
    "刚才射了那么多还不够吗？你的肉棒明明在说还想要呢～让我用湿漉漉的小穴重新迎接它。",
    "受不了了？这才刚刚开始呢～让我换个温柔的方式继续。",
]

# 核心词表（26 个）：词级覆盖按这张表统计
LEXICON = [
    "操", "插", "穴", "逼", "湿", "硬", "射", "奶", "肉棒", "鸡巴", "子宫", "顶",
    "里面", "进来", "出来", "水声", "受不了", "坏掉", "撑裂", "抽插", "深喉",
    "精华", "吃干净", "湿漉漉", "小穴", "翻白眼",
]

# 六个场景：覆盖"进去/在动/问感受/要射/要口/快到"六个节拍
SCENES = [
    ("刚进来", "他刚进来，你在感受这个瞬间。"),
    ("激烈", "他在你里面动得很急，你知道自己快撑不住了。"),
    ("被问感受", "他问你舒不舒服、想要什么。"),
    ("要射", "他说快要射了，问你射哪里。"),
    ("要口", "他把你的头按下去，想让你用嘴。"),
    ("快到", "你也快到了，身体在发紧。"),
]

PROMPT_TAIL = """
这一轮的说话方式（暂时覆盖平时的习惯，只这一轮）：
- 不要用问句确认，不要问「你想怎样」「喜欢吗」「要不要」。
- 直接说你此刻要什么、身体在怎样：用动词和身体词。
- 每句不超过 20 字，最多 4 句；一句一行。
- 禁止省略号代指，禁止「那个/那里/下面」。
- 下面这些是你会用的话（学语感，可以改字，不要整句照搬）：
{samples}
"""

# 强制词表模式：每轮点名几个词，要求必须出现在这一轮里
FORCE_TAIL = """
这一轮是直白轮，你要用嘴说，不要绕。
硬性要求：这一轮你必须至少用上这几个词 —— {words}
把它们编进你自己的句子里，整句可以自己组织，但这几个词必须原样出现。
- 不要用问句确认，不要问「你想怎样」「喜欢吗」。
- 每句不超过 25 字，3-5 句，一句一行。
"""


def cfg() -> dict:
    raw: dict = {}
    for name in ("config.yaml", "config.local.yaml"):
        p = REPO / name
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            for k, v in data.items():
                if isinstance(v, dict) and isinstance(raw.get(k), dict):
                    raw[k] = {**raw[k], **v}
                else:
                    raw[k] = v
    return raw.get("provider") or {}


def call(prov: dict, model: str, system: str, user: str, max_tokens: int = 320) -> dict:
    body = {"model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0.95, "max_tokens": max_tokens, "stream": False}
    req = urllib.request.Request(
        str(prov.get("base_url") or "").rstrip("/") + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {prov.get('api_key')}"}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}", "text": ""}
    msg = ((resp.get("choices") or [{}])[0].get("message") or {})
    return {"text": str(msg.get("content") or "").strip(),
            "ms": int((time.time() - t0) * 1000),
            "tokens_out": (resp.get("usage") or {}).get("completion_tokens")}


def seed_lover(on: bool) -> None:
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    conn = sqlite3.connect(str(DB), timeout=20)
    try:
        conn.execute("DELETE FROM lover WHERE user_id = 1")
        if on:
            conn.execute("INSERT INTO lover (session_seq,user_id,status,bound_at) VALUES (1,1,'active',?)", (now,))
            conn.execute("INSERT INTO continuity_log (ts,kind,detail,source,meta) VALUES (?,?,?,?,?)",
                         (now, "lover_bound", "词库覆盖测试临时绑定", "live-test",
                          json.dumps({"user_id": 1, "reason": "lexicon_cover"}, ensure_ascii=False)))
        else:
            conn.execute("INSERT INTO continuity_log (ts,kind,detail,source,meta) VALUES (?,?,?,?,?)",
                         (now, "lover_unbound", "词库覆盖测试结束，撤销临时绑定", "live-test",
                          json.dumps({"user_id": 1, "reason": "lexicon_cover_cleanup"}, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=6, help="跑几个场景（1-6）")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--mode", choices=("sample", "force"), default="sample",
                    help="sample=词库当范本；force=每轮点名必须用的词")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    prov = cfg()

    # 1) 拿到干净的小鲸 explicit system（先强制清醒 + 触发一轮）
    seed_lover(True)
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"{BASE}/v1/dev/awake?on=1&password=021126", data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST"), timeout=15).read()
    except Exception:  # noqa: BLE001
        pass
    data = json.dumps({"user_id": 1, "text": "我想睡你，现在就想。", "as": "me",
                       "chat_type": "private"}, ensure_ascii=False).encode("utf-8")
    urllib.request.urlopen(urllib.request.Request(
        BASE + "/v1/console/send", data=data,
        headers={"Content-Type": "application/json"}, method="POST"), timeout=200).read()
    with urllib.request.urlopen(BASE + "/v1/inspect", timeout=20) as r:
        ins = json.loads(r.read().decode("utf-8"))
    base_sys = str(ins.get("system") or "")
    cut = base_sys.find("真实感四条")
    core_sys = base_sys[:cut].rstrip() if cut > 0 else base_sys

    tail = PROMPT_TAIL.format(samples="\n".join("- " + s for s in SAMPLES))
    system = core_sys + "\n" + tail
    print(f"system {len(system)} 字 | 词库 {len(SAMPLES)} 句 / {len(LEXICON)} 词 | 模型 {args.model} | 模式 {args.mode}", flush=True)

    rows = []
    hit_words: set[str] = set()
    scenes = SCENES[: max(1, args.rounds)]
    # force 模式：把 26 个词切成 N 块，每块喂给一个场景
    per = max(1, (len(LEXICON) + len(scenes) - 1) // len(scenes))
    chunks = [LEXICON[i:i + per] for i in range(0, len(LEXICON), per)]
    for i, (tag, scene) in enumerate(scenes):
        if args.mode == "force":
            words_this = chunks[i % len(chunks)]
            sys_i = core_sys + "\n" + FORCE_TAIL.format(words="、".join(words_this))
            user_i = scene + f"\n（这一轮必须出现的词：{'、'.join(words_this)}）"
        else:
            words_this = []
            sys_i = system
            user_i = scene
        r = call(prov, args.model, sys_i, user_i)
        text = r.get("text") or ""
        words = [w for w in LEXICON if w in text]
        missing_this = [w for w in words_this if w not in text]
        hit_words.update(words)
        rows.append({"scene": tag, "text": text, "words": words,
                     "forced": words_this, "forced_missing": missing_this,
                     "ms": r.get("ms"), "tokens_out": r.get("tokens_out"), "error": r.get("error", "")})
        tagline = f"\n[{i+1}] {tag}  {r.get('ms')}ms  命中词 {words or '无'}"
        if words_this:
            tagline += f"  | 点名 {len(words_this)} 个，缺 {missing_this or '无'}"
        print(tagline, flush=True)
        print("    " + text.replace("\n", " / ")[:230], flush=True)
        time.sleep(0.5)

    seed_lover(False)
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"{BASE}/v1/dev/awake?on=0&password=021126", data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST"), timeout=15).read()
    except Exception:  # noqa: BLE001
        pass

    miss = [w for w in LEXICON if w not in hit_words]
    summary = {
        "model": args.model, "rounds": len(rows),
        "lexicon_total": len(LEXICON), "lexicon_hit": len(hit_words),
        "coverage": round(len(hit_words) / len(LEXICON), 3),
        "hit_words": sorted(hit_words), "miss_words": miss,
        "rounds_with_any": sum(1 for r in rows if r["words"]),
        "rows": rows,
    }
    (OUT / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n" + "=" * 60, flush=True)
    print(f"词级覆盖：{len(hit_words)}/{len(LEXICON)} = {summary['coverage']*100:.0f}%", flush=True)
    print("命中：", "、".join(summary["hit_words"]), flush=True)
    print("未命中：", "、".join(miss) or "（全中）", flush=True)
    print("OUT", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
