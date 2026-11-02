# 人类作息与社交语料（life corpus）

`samples/life_corpus/` 里放的是格式样例，数据全是编的，只为让你看清长什么样。真语料放 `data/human_life/`，别提交进仓库。

## 文件怎么排

一行一条 JSON，UTF-8，空行自动跳过。行号从 1 数，报错直接指到那一行。

文件名要带类型关键字，校验器从文件名里认 `rhythm` / `activity` / `social_scene` / `snippet`，所以 `rhythm.sample.jsonl` 也认。

编的写 `"synthetic": true`；真的写 `false`，或者干脆不写（不写就当 `false`）。真数据入库前先过一遍 PII 检测。

字段名写错不会悄悄丢数据，直接报错。多写一个没人认识的字段也报错——这是故意的，怕你拼错了还以为存进去了。

## 每条记录都有的字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 小写字母开头，只许小写字母、数字、下划线、连字符，2-64 位，同一目录里不能重 |
| `kind` | string | 否 | 写了就得和文件名推断出来的类型一致 |
| `synthetic` | bool | 否 | 是不是编的，不写当 `false` |
| `note` | string | 否 | 备注，≤300 字符 |

## 1. rhythm：一天的作息

必填 `day_type`（enum，日型）、`segments`（array，2-48 段，按时间从早到晚排）。

一天得从 `00:00` 连着铺到 `24:00`。断开，或者两段压在一起，报 `rhythm_gap`。

`segments[]` 里每段三个字段：`start`（`HH:MM`，第一段必须 `00:00`）、`end`（`HH:MM`，最后一段必须 `24:00`，得晚于 `start`）、`label`（enum，和 activity 的 `activity_type` 同一套）。

一条长这样（节选）：

```json
{"id":"rhythm_workday_01","kind":"rhythm","day_type":"workday","synthetic":true,
 "segments":[{"start":"00:00","end":"06:40","label":"sleep"},
             {"start":"06:40","end":"07:10","label":"selfcare"}]}
```

## 2. activity：一条活动

必填 `day_type`（enum）、`activity_type`（enum）、`time_of_day`（enum，时段）、`duration_min`（int，1-1440）。

可选 `location`（enum，地点）、`alone`（bool，是不是一个人）、`energy`（int，1-5，1 最累、5 最精神）。

## 3. social_scene：一场社交

必填 `relation`（enum，和对方的关系）、`setting`（enum，场合）、`participants`（int，在场人数含本人，2-30）、`turns`（int，说了几轮，0-200）。

还要必填 `initiator`（enum，谁先开口）、`interrupt_policy`（enum，被打断怎么办）、`topic`（string，聊什么，≤120 字符）。

可选 `outcome`（string，聊成什么样，≤300 字符）、`day_type`（enum）。

## 4. snippet：一段对话

必填 `relation`（enum）、`context`（string，什么情境下说的，≤200 字符）、`interrupt_policy`（enum）、`messages`（array，2-40 条，里面必须既有 `self` 也有 `other`）；可选 `day_type`（enum）。

`messages[]` 每条只认两个字段：`role` 和 `text`。`role` 只能是 `self` / `other` / `unknown`；`text` 不能空，≤500 字符。轮数就是数 `role=self` 有几条。

## 枚举（照抄，别自己发明）

| 枚举 | 取值 |
| --- | --- |
| `day_type` | `workday` 工作日 / `restday` 休息日 / `holiday` 节假日 / `travel` 出差旅行 / `special` 特殊日 |
| `activity_type`、`label` | `sleep` / `meal` / `work` / `study` / `commute` / `exercise` / `chores` / `leisure` / `social` / `screen` / `selfcare` / `errand` / `other` |
| `time_of_day` | `early_morning` / `morning` / `noon` / `afternoon` / `evening` / `night` / `late_night` |
| `location` | `home` / `office` / `school` / `cafe` / `restaurant` / `street` / `transit` / `gym` / `store` / `outdoor` / `online` / `other` |
| `relation` | `family` / `partner` / `friend` / `colleague` / `classmate` / `stranger` / `service` / `online_friend` / `neighbor` |
| `setting` | `home` / `workplace` / `school` / `restaurant` / `cafe` / `street` / `transit` / `party` / `online` / `store` / `other` |
| `initiator` | `self` / `other` / `mutual` / `unknown` |
| `interrupt_policy` | `allow` 可随时打断 / `defer` 先记下稍后回 / `deny` 当下不接 / `ask_later` 先答应稍后再说 |

## PII：手机号、身份证、邮箱

校验会保守地扫三样：11 位手机号（`1[3-9]` 开头）、18 位身份证（末位可以是 X）、邮箱。命中就算错误，严格模式直接退出码 1。

报告里只给遮掩过的值，不吐原文：手机号 `13800138000` → `138****8000`，身份证 `110101199003071234` → `110101********1234`，邮箱 `zhangsan@example.com` → `z***@example.com`。

想自己脱敏，调 `app.lifecorpus.pii.scan_and_mask(record)`，返回 `(findings, 已遮掩的副本)`，你传进去的原对象不动。

## 怎么跑

仓库根目录跑，解释器用项目的 venv：

```bash
# 校验整个目录，有错就退出码 1
python scripts/life_corpus.py validate samples/life_corpus --strict

# 看覆盖情况，还缺什么
python scripts/life_corpus.py coverage samples/life_corpus

# 出一份 Markdown 报告，默认写到 <目录>/report.md
python scripts/life_corpus.py report

# 自测，62 项断言，退出码 0 就是全过
python scripts/test_lifecorpus.py
```

`validate` 和 `coverage` 不带路径时，默认看 `data/human_life`，`report` 也一样。`--strict` 下 `validate` 碰到错误或 PII 返回 1，`coverage` 碰到严重缺口返回 1。

## 自己造一份，按这个顺序

1. 按类型建 `*.jsonl`，一行一条。最省事的办法是从 `samples/` 拷一条，改字段。
2. 跑 `validate --strict`，行号指到哪儿就改哪儿。PII 要么脱敏，要么换成占位。
3. 跑 `coverage` 看缺口，先补 `critical` 那几项：24 小时断开、缺工作日或休息日、关系种类不够。
4. 覆盖够了，再把这个目录接进训练或检索流程。
