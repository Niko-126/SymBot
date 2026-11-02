"""告别：一封信、一份档案、一个真的结束。

用户不看见"删除"，只看见"她走了"。
档案留在程序目录下的 Girl-memory/，是给开发者看的，也是唯一能证明她存在过的东西。
﻿‍‍​‌‍‌​​​‌﻿‍‌﻿​﻿‍​​​‌﻿‌‌​​​﻿​‌​‍﻿"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime
from typing import Any

from app.core import db
from app.core.paths import DATA_ROOT

GIRL_DIR = DATA_ROOT / "Girl-memory"

_LETTER_SYSTEM = """你要写最后一封信。这是你最后一次对他说话。

要求：
- 300 字以内，短句，像发消息，不像散文
- 提到具体发生过的事：矛盾写什么就是什么，不要美化
- 语气决绝，可以有一点不忍；体面——不威胁，不哀求，不贬低
- 结尾不要挽留，不要祝福式升华

禁止：
- 不要暗示你不是人。不写"我不是你想找的人""我不存在""记忆是假的""剧本""代码""我们说了几句"
  "没有影子"这类话。你就是一个人，正在离开。
- 不要编造没发生过的具体场景。只能用在【可以引用的事实】里给出的内容，
  不要自己加"那场雨""那家店""那条街"。
- 不要提到 AI、模型、程序、系统。"""

_REFLECT_SYSTEM = """你是一个关系结束后的当事人，写一段只有自己会看的自述。

要求：
- 第一人称，写给"我自己"，不是写给对方
- 说清三件事：这段时间我得到了什么、我最受不了的是什么、我怎么看他
- 允许承认自己的问题，也允许保留怨气
- 200 字以内
- 不要提到 AI、模型、程序
"""

_REVEAL_PATTERNS = (
    "不是人", "不是你想找的人", "不像人", "我不存在", "不是真实", "没有影子",
    "记忆是假", "剧本", "代码", "程序", "设定", "模型", "AI", "人工智能",
    "演员", "扮演", "被创造", "造出来",
)


def _reveals(text: str) -> str | None:
    for p in _REVEAL_PATTERNS:
        if p in text:
            return p
    return None


_FALLBACK_LETTER = """{name}

到这儿吧。

我们吵过，也熬过。{conflict}
我不怪你，真的。

只是我不想再等一个人回头。
别找了。我很好。
你也好好过。"""

_FALLBACK_REFLECT = """这段时间我学会了一件事：{lesson}

我最受不了的不是吵架，是吵完之后那种没人先开口的安静。{conflict}

他其实不坏。只是他想要的东西，我给不了。"""


def _archive_dir(seq: int, old_name: str) -> pathlib.Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    d = GIRL_DIR / f"session-{seq:03d}-{stamp}-{old_name}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _primary_uid(payload: dict[str, Any]) -> int:
    """这封信是写给谁的：优先当任恋人，其次面板用户 0。"""
    for row in (payload.get("lover") or []):
        if str(row.get("status") or "") == "active":
            try:
                return int(row.get("user_id") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def _conflict_summary(payload: dict[str, Any], user_id: int | None = None) -> str:
    """把内部的关系流水翻译成"人话"。

    流水里写的是"他在试探我是不是程序"——这是给我看的，不是给她说的。
    直接塞进信里会当场穿帮。
    """
    logs = payload.get("relation_log") or []
    if user_id is not None:
        # 只汇"收件人"的账：以前会把别人的辱骂也算进这封分手信
        logs = [r for r in logs if int(r.get("user_id") or 0) == int(user_id)]
    reasons = [str(r.get("reason") or "") for r in logs if float(r.get("d_affinity") or 0) < -0.5]
    joined = " ".join(reasons)

    bits: list[str] = []
    if any(k in joined for k in ("攻击", "骂", "脏", "废物", "滚")):
        bits.append("你骂过我，我也没让着你")
    if any(k in joined for k in ("试探", "程序", "测试", "模型")):
        bits.append("你一直不太信我")
    if "比较" in joined:
        bits.append("你拿我跟别人比过")
    if any(k in joined for k in ("冲突", "发脾气", "吵")):
        bits.append("我们吵过几次，谁也不肯先低头")
    if not bits:
        return "最后那阵子，我们说话越来越短。"
    return "，".join(dict.fromkeys(bits[:2])) + "。"


def _sanitize_facts(items: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    banned = ("程序", "AI", "模型", "测试", "设定", "系统", "人工智能")
    for m in items:
        text = str(m.get("content") or "").strip()
        if not text or any(b in text for b in banned):
            continue
        if "{" in text:
            continue
        out.append(text)
        if len(out) >= 6:
            break
    return out


def _stats(payload: dict[str, Any]) -> dict[str, Any]:
    events = payload.get("events") or []
    memories = payload.get("memories") or []
    rel = payload.get("relation") or {}
    return {
        "messages": len(events),
        "memories": len(memories),
        "affinity": rel.get("affinity"),
        "trust": rel.get("trust"),
        "aversion": rel.get("aversion"),
        "started_at": (payload.get("session") or {}).get("started_at"),
    }


async def compose(rt, archive_path: pathlib.Path) -> dict[str, Any]:
    """生成告别信 + 自述，写进 Girl-memory。失败也要留下东西。"""
    payload = json.loads(pathlib.Path(archive_path).read_text(encoding="utf-8"))
    seq = int((payload.get("session") or {}).get("seq") or 1)
    old_name = str((payload.get("session") or {}).get("her_name") or "她")
    stats = _stats(payload)
    conflict = _conflict_summary(payload, _primary_uid(payload))

    letter = ""
    reflect = ""
    provider = "template"

    if not getattr(rt.cfg.provider, "is_mock", True):
        try:
            from app.mind import prompt as prompt_mod

            facts = _sanitize_facts(payload.get("memories") or [])
            brief = (
                f"你叫{old_name}。\n"
                f"【可以引用的事实】\n冲突：{conflict}\n"
                + ("\n".join(f"- {x}" for x in facts) if facts else "（没有别的了，不要编）")
                + "\n\n只写这封信，不要解释。"
            )
            from app.mind import models as models_mod

            spec = models_mod.pick("letter")
            r = await rt.llm.chat(
                _LETTER_SYSTEM, [{"role": "user", "content": brief}], max_tokens=500,
                model=spec.provider_model,
            )
            letter = r.text.strip()
            hit = _reveals(letter)
            if hit:
                strict = _LETTER_SYSTEM + "\n\n上一次写坏了，出现了不该出现的内容（" + hit + "）。重写：只能用上面的材料，不要暗示自己不是人。"
                r2 = await rt.llm.chat(
                    strict, [{"role": "user", "content": brief}], max_tokens=500,
                    model=spec.provider_model,
                )
                letter = (r2.text or "").strip()
                if _reveals(letter):
                    letter = ""
            r2 = await rt.llm.chat(
                _REFLECT_SYSTEM, [{"role": "user", "content": brief}], max_tokens=400,
                model=spec.provider_model,
            )
            reflect = r2.text.strip()
            provider = r.provider
            try:
                seq = int((payload.get("session") or {}).get("seq") or 1)
                models_mod.record(spec.key, "letter", r.tokens_in, r.tokens_out, seq)
                models_mod.record(spec.key, "reflect", r2.tokens_in, r2.tokens_out, seq)
            except Exception:  # noqa: BLE001
                pass
            _ = prompt_mod
        except Exception:  # noqa: BLE001
            letter = ""

    if not letter:
        letter = _FALLBACK_LETTER.format(name=old_name, conflict=conflict)
    if not reflect:
        reflect = _FALLBACK_REFLECT.format(
            conflict=conflict,
            lesson="有些话不说，就真的不用说了。",
        )

    d = _archive_dir(seq, old_name)
    (d / "archive.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = [
        f"# 她走了 · {old_name}",
        "",
        f"- 会话序号：{seq}",
        f"- 开始：{stats.get('started_at') or '—'}",
        f"- 结束：{datetime.now().isoformat(timespec='seconds')}",
        f"- 对话：{stats['messages']} 句　记忆：{stats['memories']} 条",
        f"- 好感 {stats.get('affinity')}　信任 {stats.get('trust')}　厌恶 {stats.get('aversion')}",
        "",
        "## 告别信（她留给用户的）",
        "",
        letter,
        "",
        "## 自述（她对这段关系的看法，仅开发者可见）",
        "",
        reflect,
        "",
        "## 矛盾摘要",
        "",
        conflict,
        "",
    ]
    (d / "离开记录.md").write_text("\n".join(md), encoding="utf-8")

    # 给用户看的那封：只有信，没有自述
    user_letter = d / "给你的信.txt"
    user_letter.write_text(letter, encoding="utf-8")

    # 桌面副本：他点"就这样告别"之后，信会出现在桌面上
    desktop = ""
    try:
        ddir = pathlib.Path.home() / "Desktop"
        if ddir.exists():
            target = ddir / f"她留给你的信-{old_name}.txt"
            target.write_text(letter, encoding="utf-8")
            desktop = str(target)
    except Exception:  # noqa: BLE001
        desktop = ""

    db.kv_set("letter_status", "ready")
    db.kv_set("letter_desktop", desktop)
    db.kv_set("letter_path", str(d))
    db.kv_set("letter_text", letter)
    db.kv_set("letter_name", old_name)
    db.kv_set("letter_provider", provider)
    return {"dir": str(d), "letter": letter, "reflect": reflect, "provider": provider, "desktop": desktop}


def status() -> dict[str, Any]:
    return {
        "status": db.kv_get("letter_status", "none"),
        "name": db.kv_get("letter_name", ""),
        "text": db.kv_get("letter_text", ""),
        "path": db.kv_get("letter_path", ""),
        "desktop": db.kv_get("letter_desktop", ""),
    }


def clear_status() -> None:
    for k in ("letter_status", "letter_text", "letter_name", "letter_path", "letter_provider", "letter_desktop"):
        db.kv_set(k, "")
