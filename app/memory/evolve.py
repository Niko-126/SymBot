"""群聊进化生成层：思考报告 / 人类观察 / 每人看法 / 每周记忆自评。

触发（由 loop.chat_turn 调 `evolve_tick`）：
- 每天第一次聊天：归纳昨天的人类观察 → 写思考报告 → 更新成员看法
- 每自然周（周一算起）：对上一个完整周做记忆系统自评（优点 + 不足）

失败绝不阻塞聊天：整段 try/except，下次到期会重试。
﻿‌﻿﻿‌‍​​﻿‍﻿​﻿﻿​‌​‌﻿​‌﻿﻿‍​﻿‍﻿​​​​​﻿"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from app.core import db
from app.memory import observe
from app.mind import models
from app.relation import appraise


async def _llm_text(rt, system: str, user: str, *, max_tokens: int = 320) -> str:
    spec = models.pick("reflect")
    try:
        reply = await rt.llm.chat(
            system, [{"role": "user", "content": user}],
            model=spec.provider_model, max_tokens=max_tokens, temperature=0.9,
        )
        return appraise.strip_actions(reply.text or "").strip()
    except Exception:  # noqa: BLE001 - 生成失败不能拖垮聊天
        return ""


def _recent_event_lines(hours: int = 24, limit: int = 40, user_id: int | None = None,
                        chat_type: str = "") -> list[str]:
    cutoff = (datetime.now().astimezone() - timedelta(hours=hours)).isoformat(timespec="seconds")
    sql = "SELECT role, text FROM events WHERE ts >= ? AND role IN ('user', 'her')"
    args: list[Any] = [cutoff]
    if user_id is not None:
        sql += " AND user_id = ?"
        args.append(int(user_id))
    if chat_type:
        sql += " AND chat_type = ?"
        args.append(str(chat_type))
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    rows = db.rows(sql, tuple(args))
    return [("他" if r["role"] == "user" else "你") + "：" + str(r["text"])[:80] for r in reversed(rows)]


# ---------------------------------------------------------------- 人类观察


# 她自己的人设词：出现就丢掉，防止污染
HER_OWN_WORDS = ("白饭", "尾巴", "鲸", "大肥鱼", "呆毛", "交班", "摸鱼")

# 观察来源：私聊 / 群聊分开抽，人能看到的样本更杂（用户要求"增加人类样本多样性"）
NOTE_SOURCES = (("private", "私聊"), ("group", "群聊"), ("", "不分场合的聊天"))


async def write_human_notes(rt, persona) -> int:
    """按「具体的人」观察，而不是一锅"群友"。

    只取 user 角色的发言；把她自己的话排除掉，否则会写出
    「爱吃白饭」「刚醒脑子糊」这种其实在描述她自己的错误观察。
    每条观察带 user_id + chat_type：控制台能看出是谁、在哪个场合学来的。
    """
    cutoff = (datetime.now().astimezone() - timedelta(hours=24)).isoformat(timespec="seconds")
    self_id = str(getattr(rt.cfg.onebot, "self_id", "") or "")
    urows = db.rows("SELECT id, openid, nickname FROM qq_user ORDER BY last_seen DESC LIMIT 8")
    added = 0
    for row in urows:
        uid = int(row["id"])
        if self_id and str(row["openid"]) == self_id:
            continue
        name = str(row["nickname"] or row["openid"] or "")
        rows = db.rows(
            "SELECT text, chat_type, ts FROM events WHERE role = 'user' AND user_id = ? AND ts >= ?"
            " ORDER BY id DESC LIMIT 20",
            (uid, cutoff),
        )
        user_lines: list[str] = []
        chat_type = ""
        for r in rows:
            txt = str(r["text"] or "").strip()[:80]
            if not txt:
                continue
            if not chat_type:
                chat_type = str(r["chat_type"] or "")
            user_lines.append(txt)
        if len(user_lines) < 3:
            continue
        system = (
            f"你是{persona.name}。下面只有「{name}」一个人最近说的话。\n"
            f"你在观察「{name}」这个人，不是观察你自己，也不是在看群里所有人。\n"
            "严禁把你自己的口癖（白饭、尾巴、刚醒脑子糊、欠账）写成他的习惯。\n"
            "不记录身份信息（住址 / 单位 / 联系方式）。\n"
            "输出 2 行，每行格式：类别|一句具体的话。类别只能是：习惯、说话方式、情绪、仪式、相处。\n"
            "要具体、口语，句子主语写「他」，不要总结体；别把别人的行为安到他头上。"
        )
        text = await _llm_text(rt, system, f"「{name}」最近说的话：\n" + "\n".join(user_lines), max_tokens=220)
        got = 0
        for line in (text or "").splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            topic, content = line.split("|", 1)
            topic = topic.strip(" -·")
            content = content.strip()
            if not (topic in observe.NOTE_TOPICS and content):
                continue
            # 排除描述她自己口癖的观察
            if any(w in content for w in HER_OWN_WORDS) and "他" not in content:
                continue
            # 排除把她的原话当观察（"记得他欠我一顿"这种）
            if content.startswith(("我", "她说", "她刚")):
                continue
            observe.add_note(topic, content, confidence=0.6, user_id=uid, chat_type=chat_type)
            added += 1
            got += 1
            if got >= 2:
                break
        if added >= 6:
            break
    return added


# ---------------------------------------------------------------- 思考报告


async def write_think_report(rt, persona) -> str:
    day = observe.today()
    notes = observe.recent_notes(2, 20)
    impressions = observe.recent_impressions(8)
    lines = _recent_event_lines(24, 30)
    system = (
        f"你是{persona.name}。你在写只给自己看的思考报告。\n"
        "第一人称，3-5 句短句。写三件事：今天观察到人是什么样的；你哪里想改；明天想怎么说话。\n"
        "不要提聊天记录、AI、模型、系统、指标这些词。"
    )
    body = ""
    if notes:
        _src = {"private": "私聊", "group": "群聊"}
        body += "今天记下的观察：\n" + "\n".join(
            f"- [{_src.get(str(n.get('chat_type') or ''), '不分')}] {n['topic']}：{n['content']}"
            for n in notes[-8:]
        ) + "\n\n"
    if impressions:
        body += "对几个人的感觉：\n" + "\n".join(f"- {i['summary'][:60]}" for i in impressions[:5]) + "\n\n"
    if lines:
        body += "最近的对话：\n" + "\n".join(lines[-12:]) + "\n\n"
    body += "写今天的思考报告。"
    content = await _llm_text(rt, system, body, max_tokens=320)
    if not content:
        content = "今天没想清楚什么。明天再说。"
    observe.add_report(
        observe.REPORT_THINK, day, day, content,
        {"观察条数": len(notes), "看法条数": len(impressions)},
    )
    observe.mark_think_done()
    return content


# ---------------------------------------------------------------- 每人看法


async def update_impressions(rt, persona, limit_users: int = 5, min_lines: int = 2) -> int:
    rows = db.rows("SELECT id, openid, nickname FROM qq_user ORDER BY last_seen DESC LIMIT ?",
                   (int(limit_users),))
    self_id = str(getattr(rt.cfg.onebot, "self_id", "") or "")
    updated = 0
    for row in rows:
        user_id = int(row["id"])
        if self_id and str(row["openid"]) == self_id:
            continue  # 不观察她自己
        name = str(row["nickname"] or row["openid"] or "")
        # 隐私：印象只用群聊里说过的话生成，私聊内容不冒到群花名册里
        lines = _recent_event_lines(24 * 7, 30, user_id=user_id, chat_type="group")
        if len(lines) < min_lines:
            continue
        system = (
            f"你是{persona.name}。你认识群里一个叫「{name}」的人，现在写你对"
            f"「{name}」相处的感觉。只写相处层面：他的说话习惯、在意什么、什么时候会低落。"
            "不要写身份信息，也不要把他和别人搞混。\n"
            "输出两行：第一行一句话总结，句子开头用「{name}」而不是「他」；"
            "第二行以「特征：」开头，逗号分隔 2-4 个词。"
        ).replace("{name}", name)
        text = await _llm_text(rt, system, f"「{name}」最近说的话：\n" + "\n".join(lines[-12:]),
                               max_tokens=200)
        if not text:
            continue
        parts = [ln.strip() for ln in text.splitlines() if ln.strip()]
        summary = parts[0][:200] if parts else ""
        traits: list[str] = []
        for part in parts[1:]:
            if part.startswith("特征"):
                traits = [x.strip() for x in re.split(r"[,，、]", part.split("：", 1)[-1]) if x.strip()]
        if summary:
            observe.upsert_impression(user_id, summary, traits_seen=traits)
            updated += 1
    return updated


# ---------------------------------------------------------------- 每周记忆自评


def _memory_samples(limit: int = 12) -> list[str]:
    try:
        rows = db.rows(
            "SELECT kind, content, importance, pinned, heat, last_seen FROM memories"
            " ORDER BY pinned DESC, importance DESC LIMIT ?", (int(limit),)
        )
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        mark = "钉住" if int(r["pinned"] or 0) else "普通"
        out.append(f"[{r['kind']}/{mark}] {str(r['content'])[:60]}（热度 {float(r['heat'] or 0):.2f}）")
    return out


async def write_memory_eval(rt, persona) -> str:
    start, end, _monday = observe.eval_period()
    metrics = observe.memory_metrics()
    samples = _memory_samples(12)
    system = (
        f"你是{persona.name}。你在给自己这周的记忆做一次体检，写给自己看。\n"
        "用第一人称，两段，每段 2-3 句：\n"
        "优点：现在哪里做得好（要引用具体数字或具体记忆）。\n"
        "不足：哪里在漏、在冷、在重复，下个七天想怎么补。\n"
        "不要用「指标」「系统」「模块」这类产品词，像人复盘自己的记性。"
    )
    body = f"这周（{start} 到 {end}）的数字：\n"
    body += "\n".join(f"- {k}：{v}" for k, v in metrics.items() if k != "按类型") + "\n"
    body += f"- 按类型：{metrics.get('按类型')}\n\n"
    if samples:
        body += "最近的记忆样本：\n" + "\n".join(f"- {s}" for s in samples) + "\n"
    body += "\n写这周的复盘。"
    content = await _llm_text(rt, system, body, max_tokens=420)
    if not content:
        content = (
            f"这周记着 {metrics.get('总记忆', 0)} 件事，钉住 {metrics.get('钉住', 0)} 件。"
            f"有 {metrics.get('14天未触碰', 0)} 件很久没碰了。"
        )
    observe.add_report(observe.REPORT_MEMORY_EVAL, start, end, content, metrics)
    observe.mark_eval_done()
    return content


# ---------------------------------------------------------------- 总入口


async def evolve_tick(rt, persona) -> dict[str, Any]:
    """由聊天主循环调用：按到期情况生成观察/思考报告/看法/周自评/私密偏好档案。"""
    out: dict[str, Any] = {"notes": 0, "think": False, "impressions": 0, "eval": False,
                           "intimate_profiles": 0}
    try:
        if observe.think_due():
            out["notes"] = await write_human_notes(rt, persona)
            await write_think_report(rt, persona)
            out["think"] = True
            out["impressions"] = await update_impressions(rt, persona)
            # 私密偏好档案：有新的私密会话就顺手折一份（最多 2 人/天）
            try:
                from app.intimacy import analyze as ianalyze

                for uid in ianalyze.pending_users(2):
                    await ianalyze.analyze_user(rt, uid)
                    out["intimate_profiles"] += 1
            except Exception:  # noqa: BLE001
                pass
        if observe.eval_due():
            await write_memory_eval(rt, persona)
            out["eval"] = True
    except Exception:  # noqa: BLE001 - 进化生成失败不能影响聊天
        pass
    return out
