# 人类作息与社交语料（life corpus）字段说明

本目录是语料管线的**合成示例**，用来演示格式；真实语料请放到 `data/human_life/`，不要提交到仓库。

- 文件按记录类型命名：`rhythm.jsonl`、`activity.jsonl`、`social_scene.jsonl`、`snippet.jsonl`。
  校验器会从文件名里找 `rhythm` / `activity` / `social_scene` / `snippet` 关键字
  （所以 `rhythm.sample.jsonl` 也能被正确识别）。
- 每行一个 JSON 对象，UTF-8 编码，空行会被跳过；行号从 1 开始，报错时直接给行号。
- 所有示例都带 `"synthetic": true`，表示非真人数据。真实数据必须显式写 `false` 或省略该字段，
  并在入库前跑 PII 检测。
- 未知字段会被当成错误（防止拼错字段名后静默丢数据）。

## 通用字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 小写字母开头，仅含小写字母/数字/下划线/连字符，2-64 位，目录内全局唯一 |
| `kind` | string | 否 | 若出现必须与文件名推断出的类型一致 |
| `synthetic` | bool | 否 | 是否为合成数据，默认视为 `false` |
| `note` | string | 否 | 备注，≤300 字符 |

## 1. rhythm：作息切片

一天的时间必须从 `00:00` 连续铺到 `24:00`，中间不能断开、不能重叠，否则报 `rhythm_gap`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `day_type` | enum | 是 | 见下方日型枚举 |
| `segments` | array | 是 | 2-48 个时间段，按时间升序 |

`segments[]` 每项：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `start` | string | `HH:MM`，第一段必须是 `00:00` |
| `end` | string | `HH:MM`，最后一段必须是 `24:00`，且必须晚于 `start` |
| `label` | enum | 活动类型，复用 activity 的枚举 |

示例（节选）：

```json
{"id":"rhythm_workday_01","kind":"rhythm","day_type":"workday","synthetic":true,
 "segments":[{"start":"00:00","end":"06:40","label":"sleep"},
             {"start":"06:40","end":"07:10","label":"selfcare"}]}
```

## 2. activity：单条活动

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `day_type` | enum | 是 | 日型 |
| `activity_type` | enum | 是 | 活动类型 |
| `time_of_day` | enum | 是 | 时段 |
| `duration_min` | int | 是 | 持续分钟数，范围 1-1440 |
| `location` | enum | 否 | 发生地点 |
| `alone` | bool | 否 | 是否独处 |
| `energy` | int | 否 | 精力 1-5，1 最疲惫、5 最充沛 |

## 3. social_scene：社交场景

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `relation` | enum | 是 | 与对方的关系 |
| `setting` | enum | 是 | 场景设定 |
| `participants` | int | 是 | 在场人数（含本人），2-30 |
| `turns` | int | 是 | 对话轮数，0-200 |
| `initiator` | enum | 是 | 谁先开口 |
| `interrupt_policy` | enum | 是 | 被打断时的处理策略 |
| `topic` | string | 是 | 话题，≤120 字符 |
| `outcome` | string | 否 | 结果，≤300 字符 |
| `day_type` | enum | 否 | 日型 |

## 4. snippet：对话片段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `relation` | enum | 是 | 与对方的关系 |
| `context` | string | 是 | 发生情境，≤200 字符 |
| `interrupt_policy` | enum | 是 | 被打断时的处理策略 |
| `messages` | array | 是 | 2-40 条消息，必须同时含 `self` 与 `other` 发言 |
| `day_type` | enum | 否 | 日型 |

`messages[]` 每项只有 `role` 与 `text` 两个字段：`role` 取 `self` / `other` / `unknown`，
`text` 非空且 ≤500 字符。对话轮数按 `role=self` 的条数统计。

## 枚举

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

## PII 规则

校验时会保守扫描手机号（11 位，`1[3-9]` 开头）、身份证（18 位，末位可为 X）、邮箱，
命中即算错误（严格模式直接退出码 1），报告里只展示遮掩值：

| 类型 | 原文示例 | 报告中的样子 |
| --- | --- | --- |
| 手机号 | `13800138000` | `138****8000` |
| 身份证 | `110101199003071234` | `110101********1234` |
| 邮箱 | `zhangsan@example.com` | `z***@example.com` |

脱敏接口：`app.lifecorpus.pii.scan_and_mask(record)` 返回 `(findings, 已遮掩的副本)`，
原对象不会被修改。

## 命令

在仓库根目录执行（解释器用项目 venv）：

```bash
# 校验目录，有错误时以退出码 1 结束
python scripts/life_corpus.py validate samples/life_corpus --strict

# 查看覆盖矩阵与缺口清单
python scripts/life_corpus.py coverage samples/life_corpus

# 生成 Markdown 报告（默认写到 <目录>/report.md，下面用默认目录 data/human_life）
python scripts/life_corpus.py report

# 自测（62 项断言，退出码 0 表示全过）
python scripts/test_lifecorpus.py
```

`validate` / `coverage` 的默认目录是 `data/human_life`，不传路径时就用它。
`--strict` 下 `validate` 遇到错误或 PII 返回 1，`coverage` 遇到严重缺口返回 1。

## 新增语料的顺序

1. 先按类型建 `*.jsonl`，一行一条，从 `samples/` 复制一条再改字段最省事。
2. 跑 `validate --strict`，把行号指出的问题改干净（PII 必须脱敏或替换为占位）。
3. 跑 `coverage` 看缺口，优先补 `critical` 项：24 小时断开、缺工作日/休息日、关系种类不足。
4. 覆盖达标后再把目录接进训练/检索流程。
