# 人类作息与社交语料（life corpus）

> 这里放的是**格式样例**，数据全是编的，只为让你看清长什么样。
> 真语料放 `data/human_life/`，不进仓库。想知道她怎么用这些语料，看 [记忆怎么工作](../../docs/记忆怎么工作.md)。

---

## 一、文件怎么排

一行一条 JSON，UTF-8 编码，空行自动跳过。行号从 1 开始数，报错直接指到那一行。

文件名要带类型关键字，校验器从文件名里认 `rhythm` / `activity` / `social_scene` / `snippet`，
所以 `rhythm.sample.jsonl` 也能被认出来。

编造的写 `"synthetic": true`；真实数据写 `false` 或者干脆不写（不写就当 `false`）。
真数据入库前先过一遍隐私检测。

**字段写错不会悄悄丢数据，直接报错。** 多写一个没人认识的字段也报错——这是故意的，
怕你拼错了还以为存进去了。

每条记录都有的公共字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | 小写字母开头，只许小写字母、数字、下划线、连字符，2–64 位，同目录不能重 |
| `kind` | string | 否 | 写了就得和文件名推断的类型一致 |
| `synthetic` | bool | 否 | 是不是编的，不写当 `false` |
| `note` | string | 否 | 备注，最多 300 字 |

---

## 二、四种记录

### 1. rhythm：一天的作息

必填 `day_type`（日型）、`segments`（2–48 段，按时间从早到晚排）。

一天必须从 `00:00` 连着铺到 `24:00`。断开或者两段压一起，报 `rhythm_gap`。

每段三个字段：`start`（第一段必须 `00:00`）、`end`（最后一段必须 `24:00`，得晚于 `start`）、`label`。

```json
{"id":"rhythm_workday_01","kind":"rhythm","day_type":"workday","synthetic":true,
 "segments":[{"start":"00:00","end":"06:40","label":"sleep"},
             {"start":"06:40","end":"07:10","label":"selfcare"}]}
```

### 2. activity：一条活动

必填 `day_type`、`activity_type`、`time_of_day`、`duration_min`（1–1440 分钟）。

可选 `location`、`alone`（是不是一个人）、`energy`（1–5，1 最累、5 最精神）。

### 3. social_scene：一场社交

必填 `relation`（和对方的关系）、`setting`（场合）、`participants`（在场人数含本人，2–30）、
`turns`（说了几轮，0–200）、`initiator`（谁先开口）、`interrupt_policy`（被打断怎么办）、
`topic`（聊什么，最多 120 字）。

可选 `outcome`（聊成什么样，最多 300 字）、`day_type`。

### 4. snippet：一段对话

必填 `relation`、`context`（什么情境下说的，最多 200 字）、`interrupt_policy`、
`messages`（2–40 条，必须既有自己也有对方）；可选 `day_type`。

每条消息只认两个字段：`role` 和 `text`。`role` 只能是 `self` / `other` / `unknown`；
`text` 不能空、最多 500 字。轮数就是数 `role=self` 有几条。

---

## 三、枚举（照抄，别自己发明）

| 枚举 | 取值 |
|---|---|
| `day_type` | `workday` 工作日 / `restday` 休息日 / `holiday` 节假日 / `travel` 出差旅行 / `special` 特殊日 |
| `activity_type`、`label` | `sleep` / `meal` / `work` / `study` / `commute` / `exercise` / `chores` / `leisure` / `social` / `screen` / `selfcare` / `errand` / `other` |
| `time_of_day` | `early_morning` / `morning` / `noon` / `afternoon` / `evening` / `night` / `late_night` |
| `location` | `home` / `office` / `school` / `cafe` / `restaurant` / `street` / `transit` / `gym` / `store` / `outdoor` / `online` / `other` |
| `relation` | `family` / `partner` / `friend` / `colleague` / `classmate` / `stranger` / `service` / `online_friend` / `neighbor` |
| `setting` | `home` / `workplace` / `school` / `restaurant` / `cafe` / `street` / `transit` / `party` / `online` / `store` / `other` |
| `initiator` | `self` / `other` / `mutual` / `unknown` |
| `interrupt_policy` | `allow` 可随时打断 / `defer` 先记下稍后回 / `deny` 当下不接 / `ask_later` 先答应稍后再说 |

---

## 四、隐私检测

校验会保守地扫三样：手机号（1[3-9] 开头的 11 位）、身份证（18 位，末位可以是 X）、邮箱。
命中就算错误，严格模式直接退出码 1。

报告里只给遮掩过的值，不吐原文：

| 类型 | 原文 | 报告里 |
|---|---|---|
| 手机号 | `13800138000` | `138****8000` |
| 身份证 | `110101199003071234` | `110101********1234` |
| 邮箱 | `zhangsan@example.com` | `z***@example.com` |

想自己脱敏，调 `app.lifecorpus.pii.scan_and_mask(record)`，返回「问题列表 + 已遮掩的副本」，
你传进去的原对象不动。

---

## 五、怎么跑

在仓库根目录跑，用项目的虚拟环境：

```bash
# 校验整个目录，有错就退出码 1
python scripts/life_corpus.py validate samples/life_corpus --strict

# 看覆盖情况，还缺什么
python scripts/life_corpus.py coverage samples/life_corpus

# 出一份 Markdown 报告，默认写到 <目录>/report.md
python scripts/life_corpus.py report

# 自测，退出码 0 就是全过
python scripts/test_lifecorpus.py
```

不带路径时，三条命令默认看 `data/human_life`。

---

## 六、自己造一份，按这个顺序

1. 按类型建 `*.jsonl`，一行一条。最省事是从 `samples/` 拷一条改字段。
2. 跑 `validate --strict`，行号指到哪儿就改哪儿。隐私信息要么脱敏，要么换占位。
3. 跑 `coverage` 看缺口，先补「严重」那几项：24 小时断开、缺工作日或休息日、关系种类不够。
4. 覆盖够了，再把目录接进训练或检索流程。

---

## 下一步

- 她在对话里怎么用这些语料 → [记忆怎么工作](../../docs/记忆怎么工作.md)
- 想加功能 → [自己写模块](../../docs/自己写模块.md)
