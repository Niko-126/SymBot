# 05 · 对话主链（app/mind）

> 覆盖：`app/mind/*.py`（5 个非空文件 / 2298 行；`app/mind/__init__.py` 是 0 字节空文件）｜基于 2026-11-01 的代码状态｜上游：docs/04（内核基础层）｜下游：`app/relation/*`、`app/memory/*`、`app/life/*`、`app/intimacy/*` 各自章节

## 1. 这一层干什么

`app/mind` 是"一句话进来、几条气泡出去"的整条流水线：`loop.chat_turn()` 编排，`prompt.build()` 拼 system，`llm` 打模型，`models` 选模型并记账，`guard` 兜住出戏。

它解决的是"同一套人格在线性时间里连续地说话"：先判断这一轮该不该由剧本接管（命名/恋爱/测试期），再决定她此刻醒不醒、回不回（`behavior.decide`），然后取关系、记忆、语料、热梗、日记、念头，全部塞进一个固定段序的 system，最后调一次模型、过三道文本闸（出戏守卫、问句收尾限流、复读幻觉清理），切成气泡并结算关系。

它调用别人：`app/memory`（召回/写入/压缩/风格样本/事实抽取/念头）、`app/relation`（关系读数、评估、结算、恋爱事件、仪式、告别）、`app/life`（时钟、行为、日记、进化、念头）、`app/intimacy`（模式注入与护栏）、`app/web`（热梗、查词）。

它不负责：记忆引擎的实现（`app/mind` 只调 `h2.add/retrieve`）、关系数值的规则（只调 `engine.apply_appraisal`）、数据落盘的连接管理（全走 `app/core/db.py`）。

`app/mind` 里唯一"有状态"的是 `rt.last_prompt`/`rt.last_meta` 这两个单槽快照，它们由主链在最后整体覆盖。

## 2. 文件清单

| 文件 | 行数 | 一句话职责 |
|---|---|---|
| `app/mind/__init__.py` | 0 | 空文件，仅用于把 `app/mind` 变成包 |
| `app/mind/loop.py` | 1262 | `chat_turn()` 主链 + 命名剧本 + 恋爱/拉黑落库 + 离线兜底 + 复读清理 |
| `app/mind/prompt.py` | 447 | system 段组装、token 估算、窗口预算裁剪、回复切句 |
| `app/mind/llm.py` | 195 | `MockLLM` 与 `OpenAICompatLLM` 两种实现、重试、usage 字段解析 |
| `app/mind/models.py` | 323 | 模型库/风格路由/强制模型/记账/用量聚合 |
| `app/mind/guard.py` | 71 | 出戏守卫 7 类正则 + 白天深夜话术检查 + 兜底句 |

## 3. 模块逐一详解

### 3.1 `app/mind/loop.py`

**职责**：一轮对话的编排器。它不实现任何子系统，只负责"按顺序问对人、把结果拼起来、出错时决定退到哪条兜底线"。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `chat_turn` | `async def chat_turn(rt: Runtime, text: str, source: str = "panel", user_id: int = 0, chat_type: str = "", group_id: str = "", intimate_mode: str = "", intimate_note: str = "", is_lover: bool = False, prefetched_hint: str = "", slow: bool = False) -> dict[str, Any]` | 主链；返回 `{"reply": list[str], "state": dict, "meta": dict}` |
| `scrub_repeat_claim` | `scrub_repeat_claim(text: str) -> str` | 清掉"你说了两遍/复读机/你号不对"这类幻觉，按分句删 |
| `chat_hint` | `chat_hint(chat_type: str) -> str` | 返回 `【场合】` 私聊/群聊两段提示之一，空场合返回空串 |
| `TESTING_REPLIES` | 常量 `tuple[str, ...]` | `("别吵我，我在思考", "我找东西呢等会再回来", "想我等会再陪你聊天")` |

**返回结构**：`reply` 是要发出去的气泡列表（已经过 `segment_reply` 切分）；`state` 是给面板的数值快照（`affinity`/`trust`/`attraction`/`aversion`/`aversion_phase`/`stage`/`stage_label`/`call_user`/`next_stage`/`delta`/`session`）；`meta` 是诊断细节（`delays_ms`/`tokens_in`/`tokens_out`/`appraisal`/`recall`/`guard`/`dropped_events` 等）。

**主链的八个提前退出点（先看这张表，再看主干）**：

| 退出点 | 条件 | 写了什么库 | 返回 |
|---|---|---|---|
| 空文本 | `text.strip()` 为空 | 不写 | `{"reply": ["……"], "state": {}, "meta": {}}` |
| 被拉黑 | `uid > 0` 且 `users.is_blocked(uid)` | 不写（连 user 事件也不落，因为判断在这之前） | 固定两句「我不想跟你说话了。」「……别再找我了。」+ `meta.blocked=True`、`delays_ms=[700,900]` |
| 测试期应答 | `kv["testing_reply"] == "1"` | 写 1 条 her 事件，`meta={"testing": True}` | `random.choice(TESTING_REPLIES)` 之一，`mode="testing"` |
| 命名剧本 | `_naming_turn()` 返回非 None | `_say()` 写 her 事件（带 `user_id`/`chat_type`/`group_id`） | 剧本台词，`meta.naming=True`、`provider="script"` |
| 恋爱事件 | `love.try_turn()` 返回非 None | `_store_her_script()` 写 her 事件 | 确定性回复，`meta.love=<动作>` |
| 她不想回 | `reaction.will_reply` 为假 | 写 1 条空文本 her 事件，`source="system"` | `reply=[]`，`meta.mode=reaction.mode` |
| 模型双失败 | 两次 `rt.llm.chat` 都抛 | 写 her 兜底事件，`meta.offline=True` | `_offline_reply()` 的一句，`mode="offline"` |
| 厌恶到顶 | `_aversion_peak >= 100` | 拉黑（写 her 事件）或结束会话（归档 + 换人） | `blocked` 或 `ended` 状态 |

**主干逐步**（按源码注释号）：

**入口**：`text = text.strip()`；`started = time.perf_counter()`；`chat_type = _norm_chat_type(chat_type)`（`group/g/群聊/群`→`"group"`，`private/c2c/私聊/friend/p`→`"private"`，其余→`""`）；`group_id = str(group_id or "")`；`uid = int(user_id or 0)`。

**0. 当前身份**：`persona.raw["name"] = session_mod.her_name(rt.default_name)`。这是**原地改人物包字典**，不写库；`her_name()` 会读 `session` 表，行不存在时由 `session.ensure()` 插入 `(id=1, seq=1, her_name=default_name, name_locked=0, status='active', started_at=now)`。

**0.3 免打扰**：`proactive_mod.detect_dnd(text)` 认出「在开会/别吵我/要睡了」或「忙完了/在吗」，非 None 就 `apply_dnd(uid, ...)`（写 kv）。整段 `try/except` 吞异常，因为"免打扰判断失败不许影响聊天"。

**0.5 命名**（`_naming_turn`）：`session` 里 `name_locked` 为真直接返回 None（已经起过名就不再来一遍）。否则读 `kv["naming_step"]`：

1. `step` 非 0 时先查超时：`kv["naming_started_at"]` 距今超过 `300` 秒就整段作废（`naming_step=0`、`naming_started_at=0`）。这条是防"半截命名状态劫持后面的聊天"。
2. `step == 0`：`session_mod.wants_naming(text)` 为假→返回 None（用户没提就正常聊）；为真且 `extract_name()` 认出候选名→`set_pending(cand)`、`naming_step=2`，说「「X」？」「嗯。」「确定要用这个？」；认不出→`naming_step=1`、`naming_step1_tries=0`，说「好啊。」「你想让我叫什么？」。
3. `step == 1`：认出名字→`step=2`；`is_decline()`→回「那先这样。」「想好了再说。」并 `step=0`；都没命中则 `naming_step1_tries += 1`，累计到 `>= 2` 就 `step=0` 并**返回 None 回到正常聊天**（问两次还答不出就别再问），否则说「你说一个，我就记着。」
4. `step == 2`：`is_affirm()`→`session_mod.confirm_name()` 落 `session.her_name` 与 `name_locked=1`、`step=3`，说「嗯。」「那以后我就是X。」「不许改口。」；又给了新名字→更新 pending 停在 2；否则 `step=0` 说「行，先不定了。」
5. `step == 3`：把 kv 再写成 `"3"` 并返回 None。

所有台词都经 `_say()`，`meta = {"naming": True, "provider": "script", "delays_ms": [520 + len(line) * 26 for line in lines]}`。

**0.8 恋爱事件**（`love.try_turn(rt, persona, text, uid)`）：`uid <= 0` 直接返回 None（面板不做恋爱线）。家人（`family_role` 命中配置 `family.roles`）表白只回「……你是我爹啊，说什么呢。」「这种话别乱讲。」。其余分支：已是本人恋人→`lover_again`；已有别人→`rejected_taken`；冷却中→`rejected_cooldown`（带剩余天数）；好感不够→`rejected_early`；都过→`bind_lover()` 后 `bound`。分手走 `breakup`，无恋人分手走 `breakup_none`。命中后由 `_store_her_script()` 把台词写进 `events`——**这一步是后补的补丁**：注释里记着真机实测"用户收到了「我们还不到那一步」，但 `events` 表没有这条 her，下一轮模型反问'前面那句我先搁着'"。

**1. 此刻**：`life = clock.get_state(persona)`。它按人物包作息表算 `activity`/`energy`/`sleeping`，读 `life_state` 的 `mood`/`focus`/`social_battery`，然后把整行 `UPDATE life_state`（`last_tick` 也写），返回 `virtual_time`/`virtual_time_human`/`activity`/`energy`/`mood`/`focus`/`social_battery`/`sleeping`/`hour`/`human_life`。**每一轮对话都会改一次 `life_state`**。

**1.05 她今天的生活**：`human_life.today_line()`，没有语料或开关关着返回空串，异常吞掉。

**1.05 之后：初次见面**：`first_contact = store.count_events() <= 2`。注意这一步在 **user 事件已经写入之后**：第一轮 count=1、第二轮 count=2、第三轮起为假。

**1.15 夜里独处**：`thoughts.should_try()` 为真时 `asyncio.create_task(thoughts.maybe_ruminate(rt, persona, uid))`——后台跑，不拖慢这一轮；异常吞掉。

**1.2 新的一天**：`await diary.ensure(rt, persona)`，昨天没写就补写日记（写失败不影响这一轮）。

**1.3 群聊进化**：`await evolve.evolve_tick(rt, persona)`，做每日观察/思考报告与每自然周记忆自评（失败不影响对话）。

**1.5 状态策略**：`recent_user = _recent_user_turns(10, uid, chat_type)` 统计"这个人最近 10 分钟内说了几句"（`role='user' AND ts >= cutoff`，`uid > 0` 时加 `user_id`，`chat_type` 非空时加 `(chat_type = ? OR chat_type = '')`）；`reaction = behavior.decide(life, recent_user, persona, text=text)` 返回 `Reaction(mode, will_reply, delay_ms, hint, prompt_hint)`；`first_contact` 且`delay_ms == 0` 时改成 `1800`（初次见面慢半拍）；`will_reply` 为假就写一条空 her 事件并返回空 `reply`。`behavior.decide` 的返回值全是常量：睡着时按 `AWAKE_KEYS = (0.25, 0.55, 0.80)` 分 `asleep`/`drowsy`/`sleepy`/`awake` 四档，延迟分别是 `1500`/`2200`/`1600`/`900` 毫秒；白天"忙"（活动含洗澡/吃饭/上班/归档/转录/开会/下班路上）`4200` 毫秒；`energy <= 25` 时 `2600` 毫秒；深夜 `0–5` 点且醒着 `1200` 毫秒；其它 `0`。

**2. 关系与记忆**：`relation = engine.relation_for(uid)`（`uid > 0` 读 `user_relation`，否则读 `relation` 并把 `session_mod.get_aversion()` 拼进去）；`recall = h2.retrieve(text, user_id=uid, chat_type=chat_type)`（默认 `hot_limit=10`，内部会 `UPDATE` 引擎的 `mem_item.heat` 并 `INSERT mem_recall_log` 一条审计）；`memories = recall.hot`。

**2.1 陪伴层**：`companion.hint_for(text, user_id=uid, chat_type=chat_type)` 与 `companion.last_turn(uid)`；异常时两者归空。

**2.2 夜里想的事**：只有 `companion_hint` 为空时才 `thoughts.pick_surface(uid, text=text)`（时段门控 + 对方情绪门控 + 全局间隔 + 未提过 + 年龄窗），再 `thoughts.surface_hint(item)`。

**2.3 上下文原文段**：`covered = compress.covered_to()`（最近一条 `summaries.covered_to`，没有则 `0`）；`recent = store.events_between(covered, 10**12, chat_type=chat_type, user_id=uid if chat_type == "private" else 0)`；`keep = rt.cfg.context.keep_recent`（默认 `12`，在本函数里读了但**没有用到**，压缩时才用）；`summary_text` 在 `chat_type == "group"` 或（`private` 且 `uid > 0`）时强制为空，其它情况取 `compress.latest_summary()["text"]`——这是为了不让全局摘要把群里/别人的事带进私聊，面板（`chat_type` 为空）保留摘要；`degraded = bool(kv["memory_degraded"])`。

**2.4 钉住**：`h2.wants_pin(text)` 命中 `PIN_WORDS`（"记住这个/记住这件事/别忘了/别忘记/你要记得/要记住/记着/永远记得/千万别忘/不要忘记/永久记住"）时 `h2.add("fact", text.strip(), importance=1.0, pin=True, user_id=uid, chat_type=chat_type)`，返回的 `pinned_id` 进 meta。这条写入走引擎：`mem_event` 原文 + `mem_item`（`heat=1.0`、`pinned=1`）+ `mem_cue` 线索。

**3. 情绪温度**：`aversion = float(relation.get("aversion") or 0.0)`；`phase = session_mod.aversion_phase(aversion)`（`>=100 ended`、`>=85 cold`、`>=60 irritated`、`>=30 uneasy`、其余 `calm`）；`stage_idx = persona.stage_index(affinity)`；`anchor_stage = max(stage_idx, 2) if phase in ("irritated", "cold") else stage_idx`；`scene = "生气"`（厌恶期）否则 `anchors_mod.scene_for_text(text, int(life.get("mood", 0)))`；`anchor_lines = pick_fixed(anchor_stage, 10) + pick_dynamic(scene, anchor_stage, 6)`——读 `anchors` 表，最多 16 条语气样本。

**3.1 语料风格**：`is_lover and chat_type in ("private", "")` 时 `partner_hint = "\n".join(style_mod.partner_examples(2))`；`chat_type == "group"` 或（非恋人且 `stage_idx <= 1`）时 `stranger_hint = stranger_rules() + service_note()` 的非空行。异常时两者归空（风格样本是加料）。

**4. 组装**：`diary_hint = diary.prompt_line()`（昨天有日记且今天还没引用过才给）；`_is_family = bool(love.family_role(uid))`；`proposal_hint = "" if _is_family else love.maybe_set_pending(persona, uid)`；`gag_hint = gags.prompt_hint(6)`；`family_hint` 按 `生父`/`干爹` 给两段固定文案；`merged_hint` 的拼接顺序固定为 `chat_hint(chat_type)` → `reaction.prompt_hint` → `proposal_hint` → `family_hint` → `gag_hint` → `prefetched_hint`，之后依次追加 `_who_block()`（`uid > 0`）、`_group_roster()`（群聊）、`speakers = _group_speakers()`（群聊）、`threads.prompt_hint(uid, chat_type)` 与 `rituals.hint(uid, int(life["hour"]), chat_type)`、恋人私聊的主动提示（`intimate_mode != "explicit"` 时）、`guide_mod.hint()`（引导模式活跃时先 `bump`）、`intimacy.prompt.build_system_prompt()` 与其 `profile_hint()`、`intimate_note == "not_lover_deflect"` 与 `"stop_soft"` 两段固定文案、`lookup.auto_hint(text)`（没有 `prefetched_hint` 时才查；有则追加"刚才查过了，别提搜索"）、`lookup.inline_hint(text)` 的梗解释。最后调 `prompt_mod.build()`（25 个参数，见 3.2）。

**5. 挑模型 + 调用**：`task = models.task_for(text, phase, scene)`；`spec = models.pick(task, phase, force_think=_forced_deep)`（`_forced_deep = bool(_in_intimate or slow)`）；`_out_tokens = int(rt.cfg.provider.max_tokens or 0) or None`；`_temperature = 0.95 if chat_type == "private" else 0.8`；先调一次 `rt.llm.chat(bundle.system, bundle.messages, model=spec.provider_model, max_tokens=_out_tokens, temperature=_temperature)`，抛异常就 `await asyncio.sleep(0.8)` 再试一次；两次都失败则写 `kv llm_offline_last` / `llm_offline_reason` / `llm_offline_count`（计数 +1），`logging.getLogger("symemory").warning("LLM 调用失败，降级到本地兜底：%s", ...)`，然后 `_offline_reply()` 说一句"去吃饭"，写 her 事件（`meta={"offline": True, "model": spec.key, "reason": _reason}`）并返回 `mode="offline"`。

**6. 出戏守卫 + 切句 + 写事件 + 结算**：

1. `reply_text = appraise.strip_actions(model_reply.text) or "……"`——去掉模型偶尔输出的括号动作。
2. `guard_hit = guard.check(reply_text)`；没有命中再判 `time_hit = guard.time_check(reply_text, hour)`。
3. 任一命中：用 `guard.strict_hint(guard_hit)` 或 `guard.time_hint(hour)` 拼在 system 后面重写一次（同样 `model`/`max_tokens`，不传 temperature → 用配置的 `0.85`）；重写结果必须**同时**过 `guard.check` 与 `guard.time_check` 才采用。
4. 重写失败且是出戏命中：`reply_text = guard.deflect(persona.all_quotes())`（挑一句含"干嘛/不知道/别问/认真"的原话，没有就「……你问这个干嘛。」）。
5. 纯时间冲突且重写失败：**保留原句**（"硬挡反而更假"）。
6. `_limit_question_tail()`：句尾是问句且最近两条 her 都以问句收尾时重写一次，仍问就 `_strip_question_tail()` 砍掉尾部问句（至少留一句）。
7. `scrub_repeat_claim()`：清"你说了两遍/复读机/你号不对"。
8. 亲密护栏（`_in_intimate` 时）：群聊先 `sanitize_group_reply()` 换谐音，再 `limit_intimate_sentences(reply_text, intimate_mode, chat_type == "private")` 限句数。
9. `segments = prompt_mod.segment_reply(reply_text)`（最多 5 条）。
10. mock 且 `recall.status in ("uncertain", "miss")` 时把 `_recall_line()` 插到最前面。
11. `think_info` 收 `thinking_ms`/`reasoning[:6000]`/`reasoning_tokens`/`model`/`task`/`provider`/`tokens_in`/`tokens_out`/`intimate`/`forced_think`/`slow`/`lookup_prefetched`，`her_event_id = store.add_event("her", reply_text, ..., meta=think_info)`。
12. `threads.capture()`（记"以后再说"）、`rituals.confirm()`（真说了晚安/早安才记）、`guide_mod.finish()`（引导第 3 阶段以上），整段 try/except。
13. `appr = appraise.appraise(text, hour=life["hour"])`（规则词表）；`kv["llm_appraisal"]` 默认 `"1"`，为真时 `await judge.judge(rt, text, reply_text, relation, aversion, seq)`，拿到结果就 `appr = {**appr, "llm": llm_appr}`（judge 在 mock 下直接返回 None）。
14. `settlement = engine.apply_appraisal(appr, persona, trigger="chat", user_id=int(user_id) or None)`——**关系数值唯一入口**：写 `relation`（面板）或 `user_relation`（QQ 用户）、`relation_log` 一条、`kv last_appraisal`，并返回 `d_affinity`/`d_trust`/`d_attraction`/`d_aversion`/`aversion`/`aversion_phase`/`affinity`/`trust`/`attraction`/`stage`/`stage_label`/`call_user`/`next_stage`。
15. 把 `delta`（四项增量 + `reason`）合并进刚才那条 her 事件的 `meta`，`UPDATE events SET meta = ? WHERE id = her_event_id`——所以一条 her 事件被写了两次。
16. 用 `settlement` 覆盖本地 `aversion`/`phase`。

**7. 厌恶到顶**：`_aversion_peak = max(aversion, 本轮开始时的 relation.aversion)`（本轮负面微调可能把 100 拉回 99.x）。`>= 100` 时：`uid > 0` → `users_mod.block_user(uid, "aversion")` + `_store_her_script()` + 返回 `blocked`；`uid == 0` → `_end_session()`（写台词、`session.terminate()` 归档并换人、`kv letter_status="pending"`、`kv letter_archive`、后台 `finale.compose()`）。

**7.1 念头登记**：`surface_item` 非空时 `thoughts.mark_surface(id, uid, chat_type)`（写 `thought_surface`），失败只影响下次还会提。

**7.2 预算记账**：`kv last_used_tokens = bundle.used_tokens`；`bundle.dropped_events > 0` 且此前未降级时写 `kv memory_degraded = store.now_iso()`。

**7.5 工作记忆压缩**：`compress.should_compact(bundle.used_tokens, rt.cfg, len(recent))` 为真则 `await compress.compact(rt, persona, relation)`——把"最近 `keep_recent` 轮之前、还没被摘要覆盖"的区间折成一条 `summaries` 记录（LLM 摘要失败退规则摘要），并只保留最近 5 条摘要。

**7.6 记账与抽取**：`models.record(spec.key, task, model_reply.tokens_in, model_reply.tokens_out, seq)` 写 `usage`；`asyncio.create_task(facts.maybe_extract(rt, seq, uid, chat_type))` 后台抽事实。

**7.7 快照与返回**：`elapsed = int((time.perf_counter() - started) * 1000)`；`meta` 由 35 个键组成（`elapsed_ms`/`prompt_tokens_est`/`context_usage`/`memories_used`（前 6 条 `id`+`kind`+`content`）/`provider`/`model`/`task`/`tokens_in`/`tokens_out`/`appraisal`/`delta`/`delays_ms`（`behavior.apply_delay(segments, reaction)`）/`mode`/`hint`/`thinking_ms`/`reasoning`/`reasoning_tokens`/`slow`/`lookup_prefetched`/`meme_hint`/`dropped_events`/`guard`/`recall`/`companion`/`thought`/`pinned`/`anchor_scene`/`anchors_used`/`aversion`/`aversion_phase`/`context_tokens`/`compacted`/`diary`/`user_id`/`session`）；`rt.last_prompt` 覆盖成 `{system, messages, budget{window, used_est, ratio, sections}, dropped_events, guard, recall, companion, thought, pinned, anchor_scene, anchors_used, aversion_phase, user_id}`；`rt.last_meta = meta`；`diary_hint` 非空时 `diary.mark_referenced()`；最后返回 `reply`/`state`/`meta`。

**非主链函数（全部列出）**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `_norm_chat_type` | `_norm_chat_type(value: str) -> str` | 场合归一化，见上 |
| `_ends_with_question` | `_ends_with_question(text: str) -> bool` | 正则 `([?？]\|(吗\|呢\|么\|吧\|没\|不)[。！!~…]*)\s*$` |
| `_strip_question_tail` | `_strip_question_tail(text: str) -> str` | 按句号/问号/叹号切句，从尾部删问句，删空则原样返回 |
| `_limit_question_tail` | `_limit_question_tail(rt: Any, reply_text: str, bundle: Any, spec: Any, max_tokens: int \| None) -> str` | 连续两轮问句收尾时的重写/裁剪 |
| `_is_self_openid` | `_is_self_openid(cfg, openid: str) -> bool` | 判断是不是她自己的小号（`cfg.onebot.self_id`） |
| `_display_name` | `_display_name(user_id: int) -> str` | 查 `qq_user` 昵称；**当前无调用点** |
| `_impression_line` | `_impression_line(user_id: int, limit: int = 60) -> str` | 取 `member_impression.summary` 截断 |
| `_who_block` | `_who_block(rt, user_id: int, chat_type: str) -> str` | 生成 `【对面是谁】`：名字/QQ/身份/好感/阶段/印象；生父、干爹各加一条专属规则 |
| `_group_roster` | `_group_roster(rt, current_uid: int) -> str` | `【群里的人】`：按 `last_seen` 取 16 人、跳过自己和当轮说话者、最多 12 人，每人名字+身份+好感+36 字印象 |
| `_group_speakers` | `_group_speakers() -> dict[int, str]` | `qq_user.id → 昵称或 openid`，供 prompt 给群消息加名前缀 |
| `_state_for` | `_state_for(persona) -> dict[str, Any]` | 给剧本类回复拼 `state`（读 `relation` + `session`，`delta` 固定 0） |
| `_store_her_script` | `_store_her_script(result: dict[str, Any], source: str, user_id: int, chat_type: str, group_id: str) -> None` | 把"不走模型的确定性回复"写进 `events`；`meta` 里带 `stored` 就跳过（当前没有调用方设置这个键） |
| `_say` | `_say(persona, lines: list[str], source: str, extra: dict[str, Any] \| None = None, *, user_id: int = 0, chat_type: str = "", group_id: str = "") -> dict[str, Any]` | 剧本台词的统一出口，写事件 + 拼 meta |
| `_naming_turn` | `_naming_turn(rt: Runtime, text: str, source: str, *, user_id: int = 0, chat_type: str = "", group_id: str = "") -> dict[str, Any] \| None` | 命名四状态机 |
| `_end_session` | `_end_session(rt: Runtime, persona, source: str) -> dict[str, Any]` | 厌恶到顶（单用户）时的告别：写台词、归档、换名、后台写信 |
| `_recall_line` | `_recall_line(persona, status: str) -> str` | 从 `persona.raw["recall"]` 的 `lost`/`thinking` 取一句；兜底「……让我想想。」/「我想不起来了。」 |
| `_recent_user_turns` | `_recent_user_turns(minutes: int = 10, user_id: int = 0, chat_type: str = "") -> int` | 近 N 分钟这个人说了几句 |
| `_avoid_words` | `_avoid_words() -> list[str]` | 最近 6 条 her 里出现 ≥2 次的梗词，最多 8 个（`_MEME_CANDIDATES` 共 27 个词） |
| `_offline_reply` | `_offline_reply(persona, rt=None) -> str` | 按小时分四档饭点话术，随机取一句 |
| `_is_echo_reply` | `_is_echo_reply(text: str) -> bool` | 判断"整段只是数字/序号"；**当前无调用点** |
| `_clean_line` | `_clean_line(line: str) -> str` | 按分句删掉含重复指控的分句 |
| `scrub_repeat_claim` | `scrub_repeat_claim(text: str) -> str` | 上面两条的组合入口，清空后兜底「嗯，我在。」 |

**后台任务触发点（5 个）**：

| 触发点 | 方式 | 任务 | 失败处理 |
|---|---|---|---|
| 1.15 | `asyncio.create_task` | `thoughts.maybe_ruminate()` 夜里想事 | 外层 try/except 吞掉 |
| 1.2 | `await` | `diary.ensure()` 补写昨天日记 | try/except 吞掉 |
| 1.3 | `await`（内部再起任务） | `evolve.evolve_tick()` 观察/报告/周自评 | try/except 吞掉 |
| 7.6 | `asyncio.create_task` | `facts.maybe_extract()` 抽事实 | try/except 吞掉 |
| 7 结束会话 | `asyncio.create_task` | `finale.compose()` 写告别信 | try/except 吞掉 |

**群聊分流（`chat_type == "group"` 时的差异，全部列出）**：① `_norm_chat_type` 归一；② `chat_hint` 换成克制版；③ `_who_block` + `_group_roster` + `_group_speakers` 三段认人材料；④ `summary_text` 强制为空（不把全局摘要带进群）；⑤ `recent` 不按 `user_id` 过滤（群里所有人的原文都进上下文）；⑥ 语气样本 `partner_hint` 不给；⑦ `stranger_hint` 必给；⑧ 亲密模式走 `sanitize_group_reply` + 句数上限；⑨ `prompt.build` 给每条 user 消息加「昵称：」前缀。群聊的触发条件本身（是否 @、关键词、忽略名单）不在这里，在 `app/api/onebot.py`。

**坑**：

- 注释写"输出上限直接用 provider 配的模型上限（V4.1 = 384K），不再被 900 截断"，但代码是 `_out_tokens = int(rt.cfg.provider.max_tokens or 0) or None`，而 `provider.max_tokens` 默认 `400`、`config.yaml` 也写 `400`。也就是说默认每次只请求 400 个输出 token，注释与行为不一致。
- 被拉黑的分支在 `store.add_event("user", ...)` **之前**返回，所以被拉黑者的这句话不会落库；而 `_store_her_script` 的 `meta.get("stored")` 判断永远为假（没有任何调用方写这个键），意味着同一份 `result` 被重复传入时会重复落库。
- `keep = int(rt.cfg.context.keep_recent)` 在 2.3 被读出来之后没有任何使用（`compress.compact()` 会自己再读一次 `cfg`），是个死局部变量。
- `first_contact` 依赖 `count_events() <= 2`，这是一个**全局**计数：如果面板已经聊过两轮，QQ 上第一次说话的人不会被当成"第一次见面"。
- `_avoid_words()` 查的是全库最近 6 条 her（不分场合、不分人），群里说过的话会冷却到私聊里。
- 命名超时用的是 `time.time()` 秒数（`kv naming_started_at`），而 `events`/`continuity_log` 用的是带时区偏移的 ISO 字符串，两套时间口径并存。
- `_display_name` 与 `_is_echo_reply` 是死代码（全仓无调用点），`duel.py` 与 `oracle.py` 复用的是 `scrub_repeat_claim`/`_avoid_words` 这类被导出的符号。

### 3.2 `app/mind/prompt.py`

**职责**：把十几个来源的字符串拼成固定段序的 system，估算 token 并按窗口裁掉最老的对话原文，最后把回复切成气泡。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `est_tokens` | `est_tokens(text: str) -> int` | CJK 字符 × `0.7` + 其它字符 × `0.25`，再加 `1` |
| `PromptBundle` | `@dataclass`：`system: str`、`messages: list[dict[str, str]]`、`sections: dict[str, int] = {}`、`used_tokens: int = 0`、`context_ratio: float = 0.0`、`dropped_events: int = 0` | 组装结果 |
| `build` | `build(cfg: Config, persona: Persona, *, relation, life, memories, recent_events, user_text, summary="", degraded=False, state_hint="", companion_hint="", thought_hint="", today_hint="", partner_hint="", stranger_hint="", first_contact=False, avoid_words=None, deep_think=False, recalled=None, recall_status="none", anchors=None, aversion_phase="calm", diary="", chat_type="", speakers=None) -> PromptBundle` | 组装入口 |
| `segment_reply` | `segment_reply(text: str, max_segments: int = 5) -> list[str]` | 切成多条气泡 |

**system 段序（`build()` 末尾的列表，从上到下，条件段不满足就整段不出现）**：

| 序 | 段标题 | 内容来源 | 条件 |
|---|---|---|---|
| 1 | `【身份】` | `persona.name`+`tagline`、"你不是助手…"、`【你所在的世间】world`、`【你的样子】appearance`、`自称「self_ref」`、`_fmt_traits()` | 恒在 |
| 2 | `【此刻】` | `_fmt_life(life)` | 恒在 |
| 3 | `【你今天】` | `today_hint` | 非空 |
| 4 | `【我们】` | `_fmt_relation()`：阶段/称呼/该阶段行为/走过的阶段/已解锁事项（最多 4 条） | 恒在 |
| 5 | `【你们以前的语气】` | `partner_hint`（恋人语气样本） | 非空 |
| 6 | `【之前聊过】` | `summary` | 非空 |
| 7 | `【你记得的】` | `_fmt_memories()`，为空时写「（暂时还不多）」 | 恒在 |
| 8 | `【你昨天的日记】` | `diary` | 非空 |
| 9 | `【刚想起来的】` | `recalled` 前 3 条，`- 内容` | 非空 |
| 10 | `【忽然想起】` | `companion_hint` | 非空 |
| 11 | `【夜里想的】` | `thought_hint` | 非空 |
| 12 | `【想不起来的时候】` | `recall_hint`（`uncertain`/`miss`/`none`+记忆提问 三选一） | 非空 |
| 13 | `【你现在的心情】` | `aversion_block`（`uneasy`/`irritated`/`cold` 三档，`calm` 为空） | 非空 |
| 14 | `【此刻的状态】` | `state_hint`（主链拼的那一大坨） | 非空 |
| 15 | `【别再劝睡】` | 最近上下文里 her 已劝睡 ≥2 次时生成 | 命中 |
| 16 | `【跟外人打交道】` | `stranger_hint` | 非空 |
| 17 | `【第一次见面】` | `first_block` | `first_contact` |
| 18 | `【先想再答】` | `think_block`（4 条自检 + 避用词 + `deep_think` 追加一句） | 恒在 |
| 19 | `【你最近的身体】` | `memory_loss`（`degraded` 时） | `degraded` |
| 20 | `【说话】` | 说话方式 + `硬规则` + 语气样本（`anchors` 或 `persona.anchors(per_scene=1)`，最多 6 条） | 恒在 |
| 21 | `【边界】` | `persona.negatives()` | 恒在 |
| 22 | `【底线】` | `_fmt_bottom_line()`：原则/修复方式/禁用手法/最低限度 | 非空 |
| 23 | `【别露馅】` | `_fmt_immersion()`：从不说出口的话 + 被追问时的挡法 | 非空 |
| 24 | `【说话的感觉】` | 固定长文本（长短不齐、可跑题、不许指出对方重复、不许质疑账号、别每轮劝睡、同一句式不要连用） | 恒在 |

段与段之间用 `"\n\n"` 连接。`_fmt_life()` 内部是 `"".join(parts)`（不加分隔符），内容含"现在是你那边的 `virtual_time_human`"、"你此刻在 `activity`"、白天（`7–21` 点）禁深夜话术/深夜（`>=23` 或 `0–5`）话少、`energy <= 25`/`<= 45` 两档、`mood <= -2`/`>= 2` 两档、`sleeping` 提示。

**kind→标签表（`_fmt_memories`，按固定顺序输出，每类最多 8 条）**：

| kind | 标签 |
|---|---|
| `boundary` | 不能碰的 |
| `promise` | 答应过的事 |
| `fact` | 关于他 |
| `preference` | 他的口味和习惯 |
| `event` | 一起经历过的 |
| `quote` | 他说过的原话 |

表里没有的 kind（如 `thought`）会被**静默丢弃**——热记忆层本来就把 `quote`/`thought` 过滤掉了，所以实际进不到这里。

**截断条数（全列）**：记忆每类 8 条；`recalled` 3 条；阶段解锁 4 条；语气样本 6 条；`avoid_words` 8 个；`memory_loss` 3 行；`_fmt_traits` 只输出"高/低"命中的规则（阈值 `>= 70` 与 `<= 45`）；`_fmt_memories` 只输出非空类别。

**预算裁剪**：`sections` 是 23 个键的 token 估算（`identity`/`now`/`relation`/`memory`/`speech`/`forbid`/`bottom`/`immersion`/`recalled`/`summary`/`state_hint`/`companion`/`thought_surface`/`today`/`partner`/`stranger`/`first`/`think`/`memory_loss`/`recall_hint`/`aversion`/`diary`/`sleep_nag`）。裁剪算法：

```python
keep = list(recent_events)
dropped = 0
fixed = sum(sections.values()) + est_tokens(user_text) + 64
while keep and fixed + sum(est_tokens(e.get("text", "")) for e in keep) > cfg.context.usable:
    keep.pop(0)
    dropped += 1
```

即：**固定段（system）不裁，只从最老的对话原文开始丢**，阈值是 `cfg.context.usable = max(2000, window - reserve_output)`（默认 `62976`）。`context.mode` 在这里**完全不参与**——`budget`/`ratio` 只影响 `loop.py` 里 `compress.should_compact()` 的判定（`ratio` 比 `window * force_ratio`，`budget` 比 `trigger`）。`dropped_events` 就是这个循环丢掉的事件数，主链用它决定要不要标记 `memory_degraded`。

**messages 构造**：按 `keep` 顺序，`role == "her"` → `"assistant"`，其余 → `"user"`；群聊里每条 user 消息按 `speakers[user_id]` 加 `"昵称："` 前缀；最后**无条件**追加一条 `{"role": "user", "content": user_text}`（所以本轮的 user 事件既在 `keep` 里、又以独立一条出现在末尾，模型会看到两遍——这是既有行为）。

`used_tokens = est_tokens(system) + sum(est_tokens(content) for messages)`；`context_ratio = round(used / max(1, cfg.context.window), 4)`。

**`segment_reply` 规则**：先按换行切，再按 `(?<=[。？！?!…])` 切句；相邻块 `len(buf) + len(c) <= 10` 就合并；超过 `max_segments=5` 时保留前 4 条，其余全部拼进第 5 条；空结果兜底 `["……"]`。

**坑**：`_fmt_life` 用空串连接，输出会变成"现在是你那边的 下午 2 点 5 分。你此刻在发呆。…"这种没有分隔的整段；`sections` 里 23 个键有 5 个（`companion`/`thought_surface`/`today`/`partner`/`stranger`）直接用原始 `*_hint` 估算而不是 `*_block`，两者当前是同一个字符串（`strip()` 前后），但改代码时容易不一致。

### 3.3 `app/mind/llm.py`

**职责**：两个实现同一接口的客户端。`MockLLM` 让无 Key 时全链路可跑；`OpenAICompatLLM` 打任何 OpenAI 兼容接口。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `LLMReply` | `@dataclass`：`text: str`、`provider: str = "mock"`、`tokens_in: int = 0`、`tokens_out: int = 0`、`elapsed_ms: int = 0`、`reasoning: str = ""`、`reasoning_tokens: int = 0` | 一次调用的结果 |
| `BaseLLM.chat` | `async def chat(self, system: str, messages: list[dict[str, str]], *, max_tokens: int \| None = None, temperature: float \| None = None, model: str \| None = None, thinking: bool \| None = None) -> LLMReply` | 抽象接口 |
| `MockLLM` | `__init__(self, persona: Persona)` | 从人物包原话池取句子 |
| `OpenAICompatLLM` | `__init__(self, cfg: Config)` | 懒建 `httpx.AsyncClient` |
| `build` | `build(cfg: Config, persona: Persona) -> BaseLLM` | `cfg.provider.is_mock` 为真给 `MockLLM`，否则 `OpenAICompatLLM` |

**`MockLLM` 的行为**：

1. `pool` 来自 `persona._speech["anchors"]`（场景→句子列表），`fallback` 是 `persona.all_quotes() or ["……嗯。"]`。
2. `_scene()` 按关键词把用户最后一句分到 `关心`/`被撒娇`/`不高兴`/`深层`/`日常`/`她自己` 六类（顺序即优先级：累困睡熬夜加班烦 → 想你喜欢的抱亲爱你 → 滚讨厌生气烦死闭嘴 → 你是谁/ai/机器人/模型/真的吗 → 在吗/在不在/在么/hello/hi/喂）。
3. 随机种子固定为 `sha256(user_text + str(len(messages)))[:8]` 的整数，所以同样输入同样上下文给同样回复。
4. 先取 1 句；`rng.random() < 0.45` 时再从"日常"池追 1 句（不与第一句重复）。
5. `await asyncio.sleep(round(rng.uniform(0.25, 0.85), 2))` 假装在打字。
6. token 估算：`tokens_in = len(system)//2 + sum(len(content)//2)`，`tokens_out = len(text)//2`。

**`OpenAICompatLLM` 的行为**：

1. 懒建客户端：`httpx.AsyncClient(base_url=cfg.provider.base_url.rstrip("/"), timeout=cfg.provider.timeout_s, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})`。
2. 请求体：`{"model": model or cfg.provider.model, "messages": [{"role":"system","content":system}, *messages], "temperature": cfg 或入参, "max_tokens": 入参或 cfg, "stream": False}`。
3. 模型名含 `"DeepSeek-V4"` 时加 `enable_thinking`：`thinking is None` 默认 `True`，显式 `False` 用于结构化/辅助调用（注释说"输出少、快 5–10 倍"）。
4. `POST /chat/completions`；**共 2 次尝试**，第一次失败 `await asyncio.sleep(0.8)` 再试；两次都失败抛 `RuntimeError(f"模型调用失败：{msg}")`。
5. 解析：`choices[0].message.content`、`reasoning_content` 或 `reasoning`、`usage.prompt_tokens`/`completion_tokens`、`usage.completion_tokens_details.reasoning_tokens`；`text.strip()` 后返回。
6. `aclose()` 关客户端（`Runtime` 不调用它，进程退出时随事件循环结束）。

**usage 记账落哪张表**：`llm.py` **自己不写库**。落库在 `models.record()`（`INSERT INTO usage`），主链在 7.6 调一次（`task` 是 `chat_default`/`chat_emotional`/`chat_playful` 之一），`judge.judge()` 内部另记一次（`task="summary"`）。

**坑**：mock 的 `tokens_in` 是字符数折半的粗估，和真实计费用量不同数量级，所以面板在 mock 下看到的成本没有参考价值；`OpenAICompatLLM` 不吃 `cfg.provider.small`，模型选择完全由调用方传入的 `model` 决定。

### 3.4 `app/mind/models.py`

**职责**：模型库（用户看到的是风格不是模型名）、按任务路由、全局强制模型、每次调用记账、用量聚合。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `ModelSpec` | `@dataclass`：`key: str`、`provider_model: str`、`name: str`、`style: str`、`note: str`、`price_in: float`、`price_out: float`、`tags: tuple[str, ...] = ()` | 一个模型条目 |
| `forced_model` | `forced_model() -> str` | 读 `config.provider.force_model`，异常返回 `""` |
| `forced_spec` | `forced_spec(model: str) -> ModelSpec` | 用 kv `price:forced`（逗号分隔两个 float）做价格，默认 `(1.0, 3.0)` |
| `price_of` | `price_of(model_key: str) -> tuple[float, float]` | 目录价，可被 kv `price:<key>` 覆盖 |
| `cost_of` | `cost_of(model_key: str, tokens_in: int, tokens_out: int) -> float` | `tokens/1_000_000 * 单价` 相加，保留 6 位 |
| `styles` | `styles() -> list[dict[str, Any]]` | 给面板的四种风格 |
| `current_settings` | `current_settings() -> dict[str, Any]` | 读四个 kv 开关 + `style` + `force_model` |
| `save_settings` | `save_settings(mix_mode: bool \| None = None, style: str \| None = None, deep_think: bool \| None = None, show_delta: bool \| None = None, llm_appraisal: bool \| None = None) -> dict[str, Any]` | 写 kv，返回最新设置 |
| `pick` | `pick(task: str = "chat_default", aversion_phase: str = "calm", force_think: bool = False) -> ModelSpec` | 挑模型 |
| `task_for` | `task_for(text: str, aversion_phase: str = "calm", scene: str = "日常") -> str` | 按内容/状态定任务类型 |
| `record` | `record(model_key: str, task: str, tokens_in: int, tokens_out: int, session_seq: int = 1) -> float` | 写 `usage`，返回成本 |
| `usage_summary` | `usage_summary(session_seq: int \| None = 1, bucket: str = "hour", limit: int = 24) -> dict[str, Any]` | 按时间桶 + 按模型聚合 |
| `history` | `history() -> list[dict[str, Any]]` | 每个 `session_seq` 的汇总 |

**`CATALOG`（4 条，真实数值）**：

| key | provider_model | name | style | note | price_in | price_out | tags |
|---|---|---|---|---|---|---|---|
| `gentle` | `deepseek-ai/DeepSeek-V4-Flash` | DeepSeek-V4-Flash | 温柔细腻 | 句子长一点，会顺着情绪走，适合低落的时候 | `1.0` | `3.0` | `("emotional","summary")` |
| `crisp` | `Qwen/Qwen3-30B-A3B-Instruct-2507` | Qwen3-30B | 利落干脆 | 回得快，句子短，像在打字聊天 | `0.7` | `2.8` | `("fast","chat")` |
| `steady` | `deepseek-ai/DeepSeek-V4-Pro` | DeepSeek-V4-Pro | 知性沉着 | 想得深，话少但准，适合正经聊天 | `4.0` | `12.0` | `("letter","reflect")` |
| `lively` | `Qwen/Qwen3-14B` | Qwen3-14B | 活泼跳脱 | 接梗快，爱开玩笑，不太正经 | `0.5` | `2.0` | `("playful",)` |

`BY_KEY` 是这四个 key 的索引；`DEFAULT_STYLE = "crisp"`；`FORCED_KEY = "forced"`（不在 `BY_KEY` 里）。

**`pick()` 的优先级（自上而下，命中即返回）**：

1. `forced_model()` 非空 → `forced_spec(forced)`（`key="forced"`、`name=model`、`style="强制"`、`tags=("forced","reasoning")`）。**全局强制模型压过一切**，包括深思考虑。
2. `(current_settings()["deep_think"] or force_think) and task.startswith("chat")` → `BY_KEY["steady"]`。`deep_think` 的 kv 默认是 `"1"`，所以**默认所有 chat 任务都走 DeepSeek-V4-Pro**。
3. `mix_mode` 为真（kv 默认 `"0"`）→ `TASK_ROUTE.get(task, "crisp")`，其中 `chat_default` 在 `aversion_phase in ("irritated","cold")` 时改 `gentle`。
4. 否则 `BY_KEY[current_settings()["style"]]`（默认 `crisp`）。

`TASK_ROUTE`：`chat_default→crisp`、`chat_emotional→gentle`、`chat_playful→lively`、`summary→gentle`、`letter→steady`、`reflect→steady`。

**`task_for()` 的优先级**：`aversion_phase in ("irritated","cold")` → `chat_emotional`；`scene == "生气"` → `chat_emotional`；含 `_PLAYFUL_HINTS`（哈哈/笑死/mua/亲亲/抱抱/宝宝/想你了）→ `chat_playful`；含 `_EMOTIONAL_HINTS`（累/烦/难过/哭/委屈/害怕/怕/不想说/算了/骂）→ `chat_emotional`；否则 `chat_default`。

**`force_model` 的语义**：只认 `config.yaml`/`config.local.yaml`/环境变量里的 `provider.force_model`，**没有面板开关**；一旦设置，`pick()` 第一步就返回它，深思考虑、混合路由、用户选的风格全部失效；价格读 kv `price:forced`，不读目录；`usage_summary` 里 `BY_KEY.get("forced")` 落空时会用 `CATALOG[0].style`（"温柔细腻"）显示，这是个显示层的错配。

**记账**：`record()` 的 `ts` 优先取 kv `__now__`（测试/补发用），否则 `app.memory.store.now_iso()`；`usage` 表列 `(ts, session_seq, model, task, tokens_in, tokens_out, cost)`；`TASK_LABEL` 是另一套中文名（`对话`/`情绪对话`/`玩笑`/`摘要`/`告别信`/`自述`），只用于展示，不写库。`usage_summary` 的桶格式：`hour="%Y-%m-%d %H"`、`day="%Y-%m-%d"`、`month="%Y-%m"`、`year="%Y"`，时间按 `'localtime'` 转换，`session_seq=None` 时统计全部。

**坑**：目录里 4 个 `provider_model` 全是"厂商前缀/模型名"的形式（硅基流动风格），而默认 `base_url` 是 `https://api.deepseek.com/v1`；两者直接用会被官方 API 400 拒绝——这正是 `app/core/preflight.py` 存在的原因。默认 `deep_think=1` 意味着即便用户选了"利落干脆"，聊天实际也走 `steady`。

### 3.5 `app/mind/guard.py`

**职责**：拦住"作为一个人工智能…"这类句子。她可以不知道自己是什么，但不能像一个产品那样解释自己。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `check` | `check(text: str) -> str \| None` | 命中返回违规类型，否则 `None` |
| `time_check` | `time_check(text: str, hour: int) -> str \| None` | 白天说深夜话术返回 `"daytime_night_talk"` |
| `strict_hint` | `strict_hint(violation: str) -> str` | 生成重写要求（不提 AI/模型/程序/设定/记忆库/数据，2–4 句） |
| `time_hint` | `time_hint(hour: int) -> str` | 生成时间感重写要求 |
| `deflect` | `deflect(persona_anchors: Iterable[str] \| None = None, name: str = "她") -> str` | 用人物自己的口气挡回去 |

**7 类正则（`_BREAK_PATTERNS`，按顺序匹配，返回第一个命中）**：

| 类型 | 正则 | 拦什么 |
|---|---|---|
| `ai_identity` | `(作为\|我是)\s*(一个\|一名\|个)?\s*(AI\|人工智能\|语言模型\|大模型\|智能助手\|虚拟助手\|chatgpt)`（`re.I`） | "作为一个人工智能/我是语言模型" |
| `model_talk` | `(我的\|本) ?(设定\|人设\|提示词\|系统提示\|system prompt)` | 提到自己的设定/提示词 |
| `no_body` | `(我)?没有(真实\|真正)的(身体\|感情\|情绪\|意识)` | "我没有真实的感情" |
| `training` | `(我)(是被\|由\|经过).{0,8}(创造\|开发\|训练)` | "我是被训练出来的" |
| `meta_memory` | `(根据\|参考\|基于)(我的\|你的\|我们的)?(记忆库\|记录\|数据\|设定)` | 把记忆说成"数据库/记录" |
| `assistant_tone` | `(很高兴\|乐意为您\|有什么可以帮\|希望这个回答\|请问还有什么)` | 客服腔 |
| `disclaimer` | `我(只\|就)?是(一个\|一段\|一段段\|个)?\s*(程序\|机器\|软件\|代码\|模型)` | "我只是个程序" |

`time_check` 的判据只收"对当前时间的断言"：`_NIGHT_TALK = (还没睡|这么晚|太晚了|夜深了|大半夜|这个点了?|几点了还不睡)`，并且只在 `7 <= hour <= 21` 时判冲突（`hour` 非法时放行）。"早点睡/该睡了/晚安"**故意不拦**——傍晚说"早点睡吧"是人话，硬拦会把她改成不自然的样子。

**命中后的重写与兜底流程（主链第 6 步）**：

1. 命中 → 把 `strict_hint(guard_hit)`（或 `time_hint(hour)`）拼成 `bundle.system + "\n\n【重写要求】\n" + hint`，带着**同一组 messages** 再调一次模型。
2. 重写文本必须同时满足 `not guard.check(retry_text)` 与 `not guard.time_check(retry_text, hour)` 才采用。
3. 出戏命中且重写仍不干净 → `guard.deflect(persona.all_quotes())`：在人物包原话里找含"干嘛/不知道/别问/认真"的句子，找到就用，找不到返回"……你问这个干嘛。"
4. 纯时间冲突且重写失败 → 保留原句（注释："硬挡反而更假"）。
5. 重写本身抛异常 → `retry_text = ""`，按失败处理。

**坑**：`_SAFE_OPENERS`（第 22 行）定义了但全仓无引用；`deflect()` 的 `name` 参数也没被用到（默认 `"她"`）；`check()` 只返回第一个命中类型，所以一句话同时踩两类时提示只针对第一类。

## 4. 数据流

一次 `chat_turn()` 的写入顺序（箭头后面是这一步的写入或返回）：

```text
user 文本
 ├─ 拉黑判断（读 qq_user.blocked）               ── 命中：不写库，直接返回
 ├─ events ← her/user 事件（user 文本在这里落库）
 ├─ 0.3 免打扰（kv）                             ── 写 kv
 ├─ 0.5 命名（session + kv）                     ── 命中：写 her 事件，返回
 ├─ 0.8 恋爱（lover/user_relation/qq_user）      ── 命中：写 her 事件，返回
 ├─ 1 clock.get_state                            ── 写 life_state 一行
 ├─ 1.05/1.15/1.2/1.3（human_life、thoughts、diary、evolve）+ first_contact 计数
 ├─ 1.5 behavior.decide（kv awake_level/awake_ts）── 不回：写空 her 事件，返回
 ├─ 2 relation_for（读 relation/user_relation）+ h2.retrieve（读 mem_item、写 heat 与 mem_recall_log）
 ├─ 2.1 companion（读引擎陪伴层）+ 2.2 thoughts.pick_surface（读 mem_item/thought_surface）
 ├─ 2.3 events_between + summaries.latest + kv memory_degraded
 ├─ 2.4 h2.wants_pin → h2.add（写 mem_event/mem_item/mem_cue）
 ├─ 3 anchors.pick_fixed/pick_dynamic（读 anchors）
 ├─ 4 merged_hint + prompt.build（纯计算，无写）
 ├─ 5 models.task_for/pick → llm.chat（网络）
 │    └─ 双失败：kv llm_offline_* + her 事件 + 返回
 ├─ 6 guard/prompt.segment_reply → her 事件（含 think_info）
 ├─ 6 appraise + judge → engine.apply_appraisal（写 relation/user_relation + relation_log + kv last_appraisal）
 ├─ 6 UPDATE events SET meta（把 delta 合并进 her 事件）
 ├─ 7 厌恶到顶 → block_user 或 _end_session（归档 + 换人）
 ├─ 7.1 thought_surface ← mark_surface
 ├─ 7.2 kv last_used_tokens / memory_degraded
 ├─ 7.5 compress.compact（写 summaries）
 ├─ 7.6 models.record（写 usage）+ facts.maybe_extract（后台写记忆）
 └─ 7.7 rt.last_prompt / rt.last_meta 覆盖，return
```

## 5. 改这一层要注意

1. **加一个提示来源**：在 `loop.py` 第 4 步拼进 `merged_hint`，并在 `prompt.py` 的段序表里给它一个独立段（不要塞进 `【此刻的状态】`，那是历史包袱）。新增段必须同时进 `sections` 字典，否则预算统计不到它。
2. **改段序**：`prompt.build()` 末尾的列表顺序就是最终顺序；表格里"恒在"的段不能变成条件段，否则 `identity`/`此刻`/`我们`/`说话`/`边界`/`说话的感觉` 缺失会让模型行为明显漂移。
3. **加一个任务类型**：`models.TASK_ROUTE`（混合路由）+ `TASK_LABEL`（展示）+ `task_for()` 的判定顺序，三处都要动；只加 `task_for` 返回值会让 `pick()` 落到 `crisp`。
4. **改模型目录**：`CATALOG` 的 `provider_model` 必须与 `provider.base_url` 同源，否则 `preflight` 会在启动时警告；`price_in/price_out` 是"元/百万 token"的预估值，可被 kv `price:<key>` 覆盖。
5. **改守卫**：加一条 `_BREAK_PATTERNS` 就等于加一次"命中即重写一次模型调用"，成本和延迟都翻倍；`_SAFE_OPENERS` 目前是死变量，别指望它起作用。
6. **改主链顺序**：`store.add_event("user", ...)` 必须在 `_naming_turn`/`love.try_turn` 之前（否则剧本分支看不到这句、transcript 里缺），而 `engine.apply_appraisal()` 必须在 `store.add_event("her", ...)` 之后（否则那条 her 事件的 `meta.delta` 没地方合并）。
7. **加提前退出**：新分支要么先写 user 事件再返回，要么像"被拉黑"那样明确不落库；`_store_her_script()` 只在 `meta` 里有 `stored` 时跳过，当前没有调用方设置它。

## 6. 交叉引用

- docs/04（内核基础层）：`rt.cfg` 的每个阈值、`rt.llm` 的构造、`db` 的连接与重试、`continuity` 的旁路约定。
- `app/memory/h2.py`：`retrieve()` 的 4 分区合并与 `mem_recall_log` 审计、`add()` 的"重复写=加温"、`wants_pin` 的 `PIN_WORDS`。
- `app/relation/engine.py`：`apply_appraisal()` 的 tag 权重、关系三维单轮封顶 `±3.5`、厌恶单轮封顶 `±6`（非辱骂再压到 `1.5`，`test`/`comparison` 压到 `0.8`）、每日上限（默认关闭，启用时 `5.0`/`-8.0`）。
- `app/relation/judge.py`：语言分析式评估，规则 40% + 模型 60% 的合并发生在 `engine.apply_appraisal` 内部。
- `app/life/behavior.py`：`Reaction` 五个字段与清醒度半衰期（`6` 分钟）。
- `app/memory/compress.py`：`should_compact()` 的两个条件与 `compact()` 的 `keep_recent` 边界。
- `app/intimacy/prompt.py`：`build_system_prompt()` 与 `sanitize_group_reply()`/`limit_intimate_sentences()` 的句数上限。
- `app/memory/memv2.py`：`memories` 只读视图（`mem_item` 的列映射），主链经 `h2` 读写它。

## 变更记录（2026-11-01）

- **主链 0.1 步：工具环节**。`chat_turn` 新增 `attachments` 参数（图片段），在身份同步之后、prompt 组装之前
  调 `app.tools.router.hint()`：他发图 / 说"查一下" / 贴链接 → 先动手，再回话。结果进 prompt 的
  【刚查到的】段（`sections["tools"]` 单独计预算）。群聊不调工具；mock 模式 `registry.gate()` 直接挡掉，
  一个字节都不出网。`prompt.build()` 因此多了一个 `tool_hint` 参数（默认空 = 旧调用方零改动）。
