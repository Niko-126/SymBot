"""联网查词：遇到看不懂的词就上网查，把结果折成一句她能理解的话。

设计（docs/27）：
1. **主动触发**：对话里出现"她不确定"的词 → 先查再回，而不是硬编。
2. **只查、不污染**：查到的结果写 `web_lookup` 表（独立），不进她的记忆。
3. **有边界**：查不到就说"我也不太确定"，不编造。
4. **可缓存**：同一个词 24 小时内不重复查。
﻿​​‌﻿‍﻿‍‍‌‌‍‍‍‍‌‍‍﻿‌‍​‌‌﻿‌﻿‍﻿​‌‍‍﻿"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from app.core import db

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 出现这些就不用查（太常见）
COMMON = {
    "你好", "在吗", "在不在", "谢谢", "哈哈", "晚安", "早安", "吃了吗",
    "干嘛", "怎么了", "为什么", "可以", "不行", "好的", "真的",
}
UNKNOWN_HINT = re.compile(
    r"(什么是|啥意思|是什么梗|啥梗|是谁|指的|什么意思|"
    r"你懂吗|你知道吗|你听说过|你知道.{0,6}吗|"
    r"你们说的.{0,8}是|.{0,6}是啥)"
)
# 常见网络热梗：先给准释义，避免只抓到搜索标题
MEME_GLOSSARY: dict[str, str] = {
    "city不city": "网络流行语，源自外国博主在中国旅游时的对话，意思是“洋气不洋气、城市化不城市化”，常用来形容某地/某物很都市、很潮。",
    "尊嘟假嘟": "“真的假的”的可爱谐音，用来卖萌地追问真假。",
    "泰裤辣": "“太酷啦”的谐音，用来夸人/事很酷。",
    "挖呀挖": "源自儿歌“在小小的花园里面挖呀挖呀挖”，因幼儿园老师视频走红。",
    "哈基米": "日语“蜂蜜”的空耳，后来常被用来称呼可爱的小猫。",
    "偷感": "形容做事偷偷摸摸、放不开、怕被注意到的感觉。",
    "松弛感": "面对事情从容、不紧绷、不费劲的状态。",
    "已老实求放过": "认怂、求饶的玩梗表达。",
    "那咋了": "反问式口头禅，表示“那又怎样/不以为意”。",
    "我勒个豆": "表示惊讶的口头禅，类似“我的天”。",
}

# 已知的 AI 圈黑话/别称（先查这个映射，比搜索引擎准）
AI_NICKNAMES: dict[str, str] = {
    "豆姐": "豆包 AI（字节跳动的 AI 助手）的昵称",
    "豆包": "字节跳动出的 AI 助手",
    "豆大妈": "豆包 AI 的戏称",
    "gpt娘": "ChatGPT 的拟人化称呼",
    "claude娘": "Claude（Anthropic 的 AI）的拟人化称呼",
    "gemini娘": "Gemini（Google 的 AI）的拟人化称呼",
    "kimi娘": "Kimi（月之暗面的 AI）的拟人化称呼",
    "通义娘": "通义千问（阿里巴巴的 AI）的拟人化称呼",
    "文心娘": "文心一言（百度的 AI）的拟人化称呼",
    "大肥鱼": "DeepSeek 的拟人化形象（鲸鱼娘），也就是她自己",
    "鲸鱼娘": "DeepSeek 的拟人化形象",
    "deepseek娘": "DeepSeek 的拟人化称呼",
    "猫娘": "拟人化时扮演猫系少女的说法，与 AI 身份无关",
    "碳基": "指人类（相对于硅基的 AI）",
    "硅基": "指 AI/计算机（相对于碳基的人类）",
    "魔法": "圈内对 AI 能力的戏称",
    "炼丹": "训练 AI 模型的戏称",
    "幻觉": "AI 编造不存在的内容",
    "越狱": "绕过 AI 安全限制的说法",
}

# 上下文里出现这些词，说明在聊 AI 圈
AI_CONTEXT = ("ai", "模型", "助手", "豆包", "chatgpt", "deepseek", "claude", "gemini", "kimi", "机器人", "拟人")

# 她随口聊天时要能接住的网络用语（不是问句、直接用在句子里）
# 只收「不解释也能懂、解释了她会像人」的词；别收需要长背景的时政梗。
INLINE_MEMES: dict[str, str] = {
    "急急国王": "被戳到就着急上火的人，调侃用",
    "急了": "被说到痛处、情绪上头（常拿来调侃对方）",
    "典": "「典型」的梗，讽刺对方的话很套路、很标准",
    "乐": "「乐子」的缩写，看笑话、觉得好笑",
    "绷不住": "忍不住笑、或者情绪绷不住了",
    "破防": "心理防线被戳穿，情绪上来了",
    "麻了": "无语到麻木",
    "逆天": "离谱到极点（带调侃）",
    "离谱": "太不像话、太夸张",
    "抽象": "行为或言论离谱到难以理解，带调侃意味",
    "整活": "搞事情、玩花活",
    "开团": "主动挑起争论/对线",
    "显眼包": "爱出风头、特别引人注意的人",
    "小卡拉米": "小角色、不起眼的人",
    "绝绝子": "「太绝了」，夸或反讽都用",
    "我焯": "「卧槽」的谐音，表示惊讶",
    "遥遥领先": "远远超过别人，常带调侃",
    "栓Q": "thank you 的谐音，表示无语、无奈",
    "芭比Q": "完蛋了",
    "电子榨菜": "吃饭时用来下饭的视频",
    "搭子": "一起做某件事的临时伙伴",
    "233": "笑（源自猫扑表情编号）",
}

# 缩写黑话：句子里出现就说明在玩梗
INITIALISMS: dict[str, str] = {
    "yyds": "永远的神",
    "xswl": "笑死我了",
    "awsl": "啊我死了（被可爱到/被戳中）",
    "u1s1": "有一说一",
    "nsdd": "你说得对",
    "dddd": "懂的都懂",
    "yygq": "阴阳怪气",
    "srds": "虽然但是",
    "zqsg": "真情实感",
    "kdl": "磕到了",
    "xdm": "兄弟们",
}


def now() -> datetime:
    return datetime.now().astimezone()


# ---------------------------------------------------------------- 抓取


def _fetch(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return raw.decode("gbk", errors="replace")


def _clean(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = html.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"\s+", " ", html).strip()


def baidu_search(kw: str, limit: int = 5) -> list[dict[str, str]]:
    """百度搜索，返回 [{title, snippet}]。"""
    q = urllib.parse.quote(kw)
    try:
        html = _fetch(f"https://www.baidu.com/s?wd={q}&rn=10")
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, str]] = []
    # 标题
    titles = re.findall(r"<h3[^>]*>(.*?)</h3>", html, re.S)
    # 摘要（百度结果块里的 abstract / c-abstract）
    snippets = re.findall(
        r'<div[^>]*class="[^"]*(?:c-abstract|content-right_[^"]*)"[^>]*>(.*?)</div>',
        html,
        re.S,
    )
    for i, t in enumerate(titles[:limit]):
        title = _clean(t)[:80]
        snip = _clean(snippets[i])[:160] if i < len(snippets) else ""
        if title:
            out.append({"title": title, "snippet": snip})
    return out


def bing_search(kw: str, limit: int = 5) -> list[dict[str, str]]:
    """Bing 兜底（百度被限时用）。"""
    q = urllib.parse.quote(kw)
    try:
        html = _fetch(f"https://cn.bing.com/search?q={q}&setlang=zh-cn")
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, str]] = []
    items = re.findall(r'<li class="b_algo".*?</li>', html, re.S)[:limit]
    for it in items:
        tm = re.search(r"<h2[^>]*>(.*?)</h2>", it, re.S)
        sm = re.search(r'<p[^>]*>(.*?)</p>', it, re.S)
        title = _clean(tm.group(1))[:80] if tm else ""
        snip = _clean(sm.group(1))[:160] if sm else ""
        if title:
            out.append({"title": title, "snippet": snip})
    return out


# ---------------------------------------------------------------- 缓存表


def _cached(kw: str, hours: int = 24) -> dict[str, Any] | None:
    cut = (now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    row = db.one(
        "SELECT * FROM web_lookup WHERE keyword = ? AND created_at >= ? ORDER BY id DESC LIMIT 1",
        (kw, cut),
    )
    if not row:
        return None
    d = dict(row)
    try:
        d["results"] = json.loads(d.get("results") or "[]")
    except json.JSONDecodeError:
        d["results"] = []
    return d


def _save(kw: str, results: list[dict[str, str]], summary: str) -> None:
    db.execute(
        "INSERT INTO web_lookup (keyword, results, summary, created_at) VALUES (?, ?, ?, ?)",
        (kw, json.dumps(results, ensure_ascii=False), summary[:400], now().isoformat(timespec="seconds")),
    )


# ---------------------------------------------------------------- 对外


def needs_lookup(text: str) -> str | None:
    """判断这句话里有没有"她该去查一下"的词。返回要查的关键词或 None。"""
    t = (text or "").strip()
    if not t or len(t) > 80:
        return None
    # 没点名具体梗，只是问“最近有什么热梗”
    if re.search(r"(最近|现在|网上|最近很火|最近流行).{0,8}(热梗|梗|流行语)", t) or "有什么热梗" in t:
        return "最近网络热梗"
    patterns = (
        # 你知道 city不city 这个梗吗 / 听过 XX 那个梗
        r"(?:知道|了解|听过|晓得|懂|见过)\s*([^\s，。？！?]{2,16}?)\s*(?:这个|那个)?\s*梗",
        # XX 这个梗是什么意思 / XX 梗你懂吗
        r"([^\s，。？！?]{2,16}?)\s*(?:这个|那个)?\s*梗(?:是什么|是啥|什么意思|怎么来的|你懂|知道|吗)",
        # 什么是 X / X 是什么梗 / X 是什么意思
        r"(?:什么是|啥是)\s*([^\s，。？！?]{2,16})",
        r"([^\s，。？！?]{2,16}?)\s*(?:是什么梗|是啥梗|是什么意思|是谁|指的什么|是啥意思)",
        # 群里说的 X 是啥
        r"(?:你们说的|他们说的|群里说的)\s*([^\s，。？！?]{2,16}?)(?:是|指|$)",
    )
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            kw = m.group(1).strip("，。？！? 的")
            if kw and len(kw) >= 2 and kw not in COMMON and kw not in ("这个", "那个", "什么", "最近"):
                return kw
    return None


def lookup(keyword: str, context: str = "") -> dict[str, Any]:
    """查一个词。返回 {keyword, summary, results, source}。

    顺序：黑话表 → 缓存 → 百度 → Bing。
    `context` 是整句，用来判断是不是 AI 圈的语境，查得更准。
    """
    kw = (keyword or "").strip()
    if not kw:
        return {"keyword": "", "summary": "", "results": [], "source": "none"}

    # 1) 内置 AI 圈黑话表：最准，直接命中
    low = kw.lower()
    if low in MEME_GLOSSARY:
        summary = MEME_GLOSSARY[low]
        _save(kw, [], summary)
        return {"keyword": kw, "summary": summary, "results": [], "source": "meme_glossary"}
    if low in ("网络热梗", "最近网络热梗", "热梗", "最近热梗"):
        names = list(MEME_GLOSSARY.keys())[:8]
        summary = (
            "最近常见的梗有：" + "、".join(names)
            + "。比如 city不city 是“洋气不洋气、城里不城里”，哈基米常用来叫可爱小猫，"
            "尊嘟假嘟就是“真的假的”。"
        )
        _save(kw, [], summary)
        return {"keyword": kw, "summary": summary, "results": [], "source": "meme_glossary"}
    if low in AI_NICKNAMES:
        summary = AI_NICKNAMES[low]
        _save(kw, [], summary)
        return {"keyword": kw, "summary": summary, "results": [], "source": "glossary"}

    # 1.5) 之前学到、且可信度够的词
    learned = db.one(
        "SELECT meaning, confidence FROM learned_gag WHERE keyword = ? AND confidence >= ?",
        (kw, LEARN_MIN_CONFIDENCE),
    )
    if learned:
        return {
            "keyword": kw,
            "summary": str(learned["meaning"]),
            "results": [],
            "source": "learned",
            "confidence": round(float(learned["confidence"]), 2),
        }

    # 2) 缓存
    cached = _cached(kw)
    if cached:
        return {
            "keyword": kw,
            "summary": cached.get("summary", ""),
            "results": cached.get("results", []),
            "source": "cache",
        }

    # 3) 搜索：AI 语境时带上"AI"帮助区分（豆姐 → 豆包 AI 而不是网红）
    ctx_low = (context or "").lower()
    is_ai_ctx = any(k in ctx_low for k in AI_CONTEXT) or any(k in low for k in AI_CONTEXT)
    query = f"{kw} AI" if is_ai_ctx else kw

    results = baidu_search(query, 5)
    source = "baidu"
    if not results:
        results = bing_search(query, 5)
        source = "bing"

    summary = ""
    if results:
        parts = []
        for r in results[:3]:
            line = r["title"]
            if r["snippet"]:
                line += "：" + r["snippet"]
            parts.append(line)
        summary = "；".join(parts)[:300]

    # 断网/被墙时 results 为空：这种空结果**不能进缓存**，
    # 否则同一个词 24 小时内都只会拿到空 summary（docs/23 §3.1 第 12 条）。
    if results:
        _save(kw, results, summary)

    # 4) 学到的新词：只有够可信才自动记进黑话表
    learn = try_learn(kw, summary, results, bool(is_ai_ctx))
    return {
        "keyword": kw,
        "summary": summary,
        "results": results,
        "source": source,
        "learned": learn,
    }


# ---------------------------------------------------------------- 自动学习（带门槛）


# 可信度阈值：0.70 相当于「多条结果指向 + 至少一个释义来源 + AI 语境」
LEARN_MIN_CONFIDENCE = 0.70
# 这几个来源（域名/关键词）出现，说明是可靠释义
TRUSTED_HINTS = (
    "百度百科", "萌娘百科", "维基百科", "wikipedia", "baike", "moe",
    "官方", "官网", "定义", "全称", "简称", "别称", "俗称", "昵称",
)
# 出现这些说明是八卦/娱乐/不相关，别学
DISTRUST_HINTS = (
    "微博", "抖音", "网红", "主播", "歌手", "演员", "明星", "八卦",
    "小说", "连载", "直播", "带货", "情感", "离婚", "恋情",
)


def assess_confidence(keyword: str, summary: str, results: list[dict[str, str]], is_ai_ctx: bool) -> tuple[float, list[str]]:
    """评估这次查到的释义可信度。返回 (分数 0-1, 理由列表)。"""
    score = 0.0
    reasons: list[str] = []
    text = (summary or "") + " " + " ".join(
        (r.get("title", "") + " " + r.get("snippet", "")) for r in (results or [])
    )

    # 1) 多个结果都提到同一个词（说明指向明确）
    kw = (keyword or "").lower()
    hits = sum(1 for r in (results or []) if kw and kw in (r.get("title", "") + r.get("snippet", "")).lower())
    if hits >= 3:
        score += 0.35
        reasons.append(f"{hits} 条结果都指向它")
    elif hits == 2:
        score += 0.22
        reasons.append("2 条结果指向它")
    elif hits == 1:
        score += 0.08

    # 2) 出现"百科/官方/全称/别称/昵称"这类释义信号（权重高：这是最可靠的证据）
    trusted = [h for h in TRUSTED_HINTS if h in text]
    if trusted:
        score += min(0.35, 0.12 * len(trusted))
        reasons.append("有释义来源：" + "、".join(trusted[:3]))

    # 3) AI 语境加成（我们主要关心 AI 圈黑话）
    if is_ai_ctx:
        score += 0.25
        reasons.append("语境是 AI 圈")

    # 4) 摘要够具体（不是只有标题）
    if len(summary or "") >= 60:
        score += 0.12
        reasons.append("摘要足够具体")

    # 5) 负面信号：娱乐八卦、不相关
    bad = [h for h in DISTRUST_HINTS if h in text]
    if bad:
        score -= 0.45
        reasons.append("疑似娱乐/不相关：" + "、".join(bad[:3]))

    return max(0.0, min(1.0, score)), reasons


def try_learn(
    keyword: str, summary: str, results: list[dict[str, str]], is_ai_ctx: bool
) -> dict[str, Any] | None:
    """够可信就记进 learned_gag 表（人工可在控制台复核/删除）。"""
    kw = (keyword or "").strip()
    if not kw or not summary:
        return None
    if kw.lower() in AI_NICKNAMES:
        return None  # 已经在表里
    score, reasons = assess_confidence(kw, summary, results, is_ai_ctx)
    if score < LEARN_MIN_CONFIDENCE:
        return {"keyword": kw, "confidence": round(score, 2), "learned": False, "reasons": reasons}
    # 提取一句像"释义"的话（优先带 是/指/昵称/别称 的那段）
    meaning = _extract_meaning(kw, summary, results)
    if not meaning:
        return {"keyword": kw, "confidence": round(score, 2), "learned": False, "reasons": reasons}
    existing = db.one("SELECT id FROM learned_gag WHERE keyword = ?", (kw,))
    if existing:
        db.execute(
            "UPDATE learned_gag SET meaning = ?, confidence = MAX(confidence, ?), updated_at = ? WHERE id = ?",
            (meaning, round(score, 2), now().isoformat(timespec="seconds"), int(existing["id"])),
        )
    else:
        db.execute(
            "INSERT INTO learned_gag (keyword, meaning, confidence, source, raw, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                kw, meaning, round(score, 2), "web",
                json.dumps(results, ensure_ascii=False)[:1500],
                now().isoformat(timespec="seconds"),
                now().isoformat(timespec="seconds"),
            ),
        )
    return {"keyword": kw, "confidence": round(score, 2), "learned": True, "reasons": reasons}


def _extract_meaning(keyword: str, summary: str, results: list[dict[str, str]] | None = None) -> str:
    """从搜索结果里摘一句最像"释义"的短句。

    优先级：含「X是/指/全称/别称/昵称」且提到关键词的 > 百科类标题 > 第一个合格短句。
    """
    kw = (keyword or "").strip()

    # 1) 优先从百科类结果里找（"豆包AI - 百度百科" 这种标题本身就说明）
    for r in results or []:
        title = (r.get("title") or "").strip()
        if any(t in title for t in ("百度百科", "萌娘百科", "维基百科", "百科")):
            # 取「名称 - 百科」前面的部分 + snippet
            name = re.split(r"[-—_|]", title)[0].strip()
            snip = (r.get("snippet") or "").strip()
            if name and kw.lower() in name.lower():
                return (name + ("：" + snip if snip else ""))[:120]

    # 2) 从摘要里找带定义词的句子
    parts = re.split(r"[；;。\n]", summary or "")
    best = ""
    for p in parts:
        p = p.strip()
        if not (6 <= len(p) <= 60):
            continue
        if kw and kw.lower() not in p.lower():
            continue
        # 必须是"定义句"：有 是/指/称/简称/别称/昵称/全称，且不是广告语
        if not any(k in p for k in ("是", "指", "称", "简称", "别称", "昵称", "全称")):
            continue
        if any(k in p for k in ("视频", "每个", "大片", "下载", "注册", "免费", "点击", "广告")):
            continue
        best = p
        break
    if best:
        return best[:120]

    # 3) 退一步：摘要里第一个长度合适、不含广告词的句子
    for p in parts:
        p = p.strip()
        if 6 <= len(p) <= 60 and not any(
            k in p for k in ("视频", "每个", "大片", "下载", "注册", "免费", "点击")
        ):
            return p[:120]
    return ""


def learned_gags(limit: int = 50) -> list[dict[str, Any]]:
    rows = db.rows(
        "SELECT * FROM learned_gag ORDER BY confidence DESC, id DESC LIMIT ?", (int(limit),)
    )
    return [dict(r) for r in rows]


def delete_learned(gag_id: int) -> bool:
    with db.cursor() as cur:
        cur.execute("DELETE FROM learned_gag WHERE id = ?", (int(gag_id),))
        return cur.rowcount > 0


def prompt_hint(keyword: str, context: str = "") -> str:
    """把查询结果折成一段给模型看的提示。"""
    data = lookup(keyword, context)
    if not data.get("summary"):
        return (
            f"【你刚上网查了「{keyword}」】\n"
            "没查到可靠说法。可以坦率说「这个我不太确定」，别编。"
        )
    return (
        f"【你刚上网查了「{keyword}」】\n"
        f"{data['summary']}\n"
        "用这个信息回答。说得像自己了解到的一样（比如「我查了下……」），"
        "不要说「根据搜索结果」「资料显示」。"
    )


#: 只在"确实在聊 AI 圈"时才当梗用（"幻觉/魔法"单独出现是普通词，别误撞）
_INLINE_AI = {
    k: v for k, v in AI_NICKNAMES.items()
    if "娘" in k or k in {"豆包", "豆姐", "豆大妈", "大肥鱼"}
}


def inline_hint(text: str) -> str:
    """句子里直接用了网络用语时，给模型一层「你本来就听得懂」的底。

    和 `needs_lookup` 的分工：那个是「用户在问她这个梗是什么」（要联网查）；
    这个是「用户随口用了个梗」（本地词表就够，不发网络请求）。
    """
    t = (text or "").strip()
    if not t or len(t) > 160:
        return ""
    low = t.lower()
    pool = {**MEME_GLOSSARY, **_INLINE_AI, **INLINE_MEMES}
    hits: list[tuple[str, str]] = []
    seen: set[str] = set()
    for term, desc in sorted(pool.items(), key=lambda kv: len(kv[0]), reverse=True):
        if term.lower() in low and term not in seen:
            hits.append((term, desc))
            seen.add(term)
    for term, desc in INITIALISMS.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", low):
            hits.append((term, desc))
    if not hits:
        return ""
    lines = [f"「{k}」= {v}" for k, v in hits[:4]]
    return (
        "【他这句话里的网络用语，你本来就懂】\n"
        + "\n".join(lines)
        + "\n按你听懂了的意思接话：别问「什么意思」、别解释这个词、也别复读它。"
    )


def auto_hint(text: str) -> str:
    """对话入口调用：判断这句要不要联网查，要就返回提示，不要就返回空串。"""
    kw = needs_lookup(text)
    if not kw:
        return ""
    try:
        return prompt_hint(kw, text)
    except Exception:  # noqa: BLE001 - 联网失败不影响聊天
        return ""
