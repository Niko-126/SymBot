# 15 · 人类语料管线（app/lifecorpus）

> 覆盖：`app/lifecorpus/*.py`（6 个文件 / 2243 行）｜基于 2026-11-01 的代码状态｜上游：docs/03（文件全表）、docs/10（生活作息）｜下游：docs/21（`scripts/life_corpus.py` / `scripts/test_lifecorpus.py`）

## 1. 这一层干什么

`app/lifecorpus` 是一套**离线静态检查工具**：回答"我手上这批人类语料，能不能拿去喂模型"。它把一份 JSONL 拆成四种记录（rhythm 作息切片 / activity 单条活动 / social_scene 社交场景 / snippet 对话片段），逐字段校验、扫 PII、算覆盖矩阵、列缺口清单，最后产出一份人能读的 Markdown 报告。

它的职责边界写在 `__init__.py` 的模块 docstring 里，边界很硬：

1. **只做"语料是否可用"的静态检查**，不负责采集、不做训练、不写数据库、不联网。
2. **只依赖标准库**，不 import 项目其它任何模块（`fetch.py` 只 import 同包的 `pii`）。所以它可以被单独拷出去复用到任意语料项目。
3. **运行时根本不调用它**。全仓 `grep lifecorpus` 在 `app/` 下只命中 `human_life.py` 的一句注释；`app/life/human_life.py` 和 `app/memory/style.py` 各自写了一套 `_read_jsonl` 直接读盘。也就是说：**它站在写侧（生产/校验），消费侧绕过它自己读文件**（详见 §4）。
4. 它唯一被"生产脚本"复用的部分是 `pii.py`——`scripts/corpus_chat.py`、`corpus_csds.py`、`corpus_partner.py`、`corpus_stranger.py`、`corpus_bili.py` 都 `from app.lifecorpus.pii import mask_text`，因为脱敏只该有一份实现。
5. 四种语料的枚举取值是**跨章节契约**：`ACTIVITY_TYPES` 的 13 个 label 就是 `app/life/human_life.py: ACTIVITY_MAP` 的键，缺一个，运行时那句"在干嘛"就回落成"在发呆"。

## 2. 文件清单

| 文件 | 行数 | 一句话职责 |
|---|---|---|
| `app/lifecorpus/__init__.py` | 47 | 包入口：re-export 19 个对外符号 + `__version__ = "0.2-P"` |
| `app/lifecorpus/schema.py` | 508 | 四种语料的字段白名单、枚举、数值边界与逐字段校验（返回错误列表） |
| `app/lifecorpus/pii.py` | 208 | 中国手机号 / 18 位身份证 / 邮箱的保守检测与遮掩 |
| `app/lifecorpus/validate.py` | 365 | JSONL 读取、按文件名推断类型、逐行校验、目录汇总与文本渲染 |
| `app/lifecorpus/coverage.py` | 484 | 覆盖矩阵（24h / 日型 / 活动 / 关系 / 场景 / 轮数）与缺口清单 |
| `app/lifecorpus/fetch.py` | 631 | 采集脚手架：只读 GET、守 robots、默认离线 |

依赖方向（`import` 关系，按源码实际）：

```text
scripts/life_corpus.py ─┐
scripts/test_lifecorpus.py ─┤
scripts/corpus_*.py ──────┴→ app.lifecorpus.{schema,pii,validate,coverage,fetch}   （pii 被 5 个 corpus_* 复用）

包内部：validate → pii, schema ；coverage → schema, validate ；fetch → pii
反方向：app/lifecorpus 不 import 任何 app.* 模块（对 app/life、app/memory 无反向依赖）
```

## 3. 模块逐一详解

### 3.1 `app/lifecorpus/schema.py`（508 行）

**职责**：定义四种记录的字段白名单 + 枚举 + 数值/长度边界，并给出"一次把一条记录的所有问题报全"的校验器。设计约定写在 docstring：校验函数**只返回 `FieldError` 列表，不抛异常**，空列表即合法；错误码保持稳定，CLI/测试/训练脚本可以直接断言。

**37 个模块级常量（逐个列全，源码 `grep ': Final'` 计数 = 37）**：

| # | 常量 | 值 / 规模 | 说明 |
|---|---|---|---|
| 1 | `DAY_TYPES` | dict 5 项 | `workday`/`restday`/`holiday`/`travel`/`special`（值是对应中文说明） |
| 2 | `ACTIVITY_TYPES` | dict 13 项 | `sleep`/`meal`/`work`/`study`/`commute`/`exercise`/`chores`/`leisure`/`social`/`screen`/`selfcare`/`errand`/`other` |
| 3 | `TIME_OF_DAY` | dict 7 项 | `early_morning`(05-08) / `morning`(08-11) / `noon`(11-13) / `afternoon`(13-17) / `evening`(17-21) / `night`(21-24) / `late_night`(00-05) |
| 4 | `LOCATIONS` | dict 12 项 | `home`/`office`/`school`/`cafe`/`restaurant`/`street`/`transit`/`gym`/`store`/`outdoor`/`online`/`other` |
| 5 | `RELATIONS` | dict 9 项 | `family`/`partner`/`friend`/`colleague`/`classmate`/`stranger`/`service`/`online_friend`/`neighbor` |
| 6 | `SETTINGS` | dict 11 项 | `home`/`workplace`/`school`/`restaurant`/`cafe`/`street`/`transit`/`party`/`online`/`store`/`other` |
| 7 | `INITIATORS` | dict 4 项 | `self`/`other`/`mutual`/`unknown` |
| 8 | `INTERRUPT_POLICIES` | dict 4 项 | `allow`/`defer`/`deny`/`ask_later` |
| 9 | `ROLES` | dict 3 项 | `self`（她）/`other`（对方）/`unknown` |
| 10 | `KIND_RHYTHM` | `"rhythm"` | 作息切片 |
| 11 | `KIND_ACTIVITY` | `"activity"` | 单条活动 |
| 12 | `KIND_SOCIAL_SCENE` | `"social_scene"` | 社交场景 |
| 13 | `KIND_SNIPPET` | `"snippet"` | 对话片段 |
| 14 | `KINDS` | tuple 4 项 | 上面四个的固定顺序，**遍历顺序影响报告排版** |
| 15 | `KIND_LABELS` | dict 4 项 | 四类的中文标签（"作息切片（一天的时间分配）"等） |
| 16 | `DAY_MINUTES` | `1440` | 24×60，平铺校验的锚 |
| 17 | `MIN_SEGMENTS` | `2` | rhythm 最少分段 |
| 18 | `MAX_SEGMENTS` | `48` | rhythm 最多分段 |
| 19 | `MIN_PARTICIPANTS` | `2` | social_scene 最少人数 |
| 20 | `MAX_PARTICIPANTS` | `30` | social_scene 最多人数 |
| 21 | `MAX_SCENE_TURNS` | `200` | social_scene `turns` 上限（下限是 0） |
| 22 | `MIN_DURATION_MIN` | `1` | activity 时长下限（分钟） |
| 23 | `MAX_DURATION_MIN` | `1440` | activity 时长上限（分钟） |
| 24 | `MIN_MESSAGES` | `2` | snippet 最少消息条数 |
| 25 | `MAX_MESSAGES` | `40` | snippet 最多消息条数 |
| 26 | `MAX_MESSAGE_LEN` | `500` | 单条消息文本上限（字符） |
| 27 | `MAX_NOTE_LEN` | `300` | `note` 上限 |
| 28 | `MAX_TOPIC_LEN` | `120` | social_scene `topic` 上限 |
| 29 | `MAX_CONTEXT_LEN` | `200` | snippet `context` 上限 |
| 30 | `MAX_OUTCOME_LEN` | `300` | social_scene `outcome` 上限 |
| 31 | `MIN_ENERGY` | `1` | activity `energy` 下限 |
| 32 | `MAX_ENERGY` | `5` | activity `energy` 上限 |
| 33 | `_COMMON_FIELDS` | frozenset 4 项 | `{id, kind, note, synthetic}` |
| 34 | `_SPEC_FIELDS` | dict 4 项 | 四类各自的字段白名单（= 公共字段 ∪ 该类专属字段） |
| 35 | `_ID_PATTERN` | `^[a-z][a-z0-9_\-]{1,63}$` | id 规则：小写字母开头，2–64 位 |
| 36 | `_HHMM_PATTERN` | `^([0-9]{1,2}):([0-5][0-9])$` | `HH:MM`，分钟位强制两位 |
| 37 | `_VALIDATORS` | dict 4 项 | kind → 校验函数（`_validate_rhythm` / `_validate_activity` / `_validate_social_scene` / `_validate_snippet`） |

**四种语料的字段全表**（`_SPEC_FIELDS` 原文 + 校验器里"是否必需"的实际判定）：

| kind | 字段 | 类型 | 必需 | 约束 |
|---|---|---|---|---|
| rhythm | `id` | str | 是 | 2–64 字符，匹配 `_ID_PATTERN` |
| rhythm | `kind` | str | 否 | 出现则必须 == `"rhythm"`，否则 `kind_mismatch` |
| rhythm | `note` | str | 否 | ≤ 300 |
| rhythm | `synthetic` | bool | 否 | 出现则必须 bool |
| rhythm | `day_type` | enum | 是 | `DAY_TYPES` 之一 |
| rhythm | `segments` | list | 是 | 2–48 段，每段 `{start,end,label}` 三键，多余键报 `unknown_field` |
| activity | `id` / `kind` / `note` / `synthetic` | 同上 | 同 rhythm | — |
| activity | `day_type` | enum | 是 | `DAY_TYPES` |
| activity | `activity_type` | enum | 是 | `ACTIVITY_TYPES` |
| activity | `time_of_day` | enum | 是 | `TIME_OF_DAY` |
| activity | `duration_min` | int | 是 | 1–1440 |
| activity | `location` | enum | 否 | `LOCATIONS` |
| activity | `alone` | bool | 否 | 出现则必须 bool |
| activity | `energy` | int | 否 | 1–5 |
| social_scene | `id` / `kind` / `note` / `synthetic` | 同上 | 同上 | — |
| social_scene | `day_type` | enum | **否** | 出现则必须在枚举内（这四类里唯独它和 snippet 的 day_type 非必需） |
| social_scene | `relation` | enum | 是 | `RELATIONS` |
| social_scene | `setting` | enum | 是 | `SETTINGS` |
| social_scene | `participants` | int | 是 | 2–30 |
| social_scene | `turns` | int | 是 | 0–200 |
| social_scene | `initiator` | enum | 是 | `INITIATORS` |
| social_scene | `interrupt_policy` | enum | 是 | `INTERRUPT_POLICIES` |
| social_scene | `topic` | str | 是 | ≤ 120 |
| social_scene | `outcome` | str | 否 | ≤ 300 |
| snippet | `id` / `kind` / `note` / `synthetic` | 同上 | 同上 | — |
| snippet | `day_type` | enum | 否 | 出现则必须在枚举内 |
| snippet | `relation` | enum | 是 | `RELATIONS` |
| snippet | `context` | str | 是 | ≤ 200 |
| snippet | `interrupt_policy` | enum | 是 | `INTERRUPT_POLICIES` |
| snippet | `messages` | list | 是 | 2–40 条，每条 `{role,text}` 两键；`text` 非空且 ≤ 500 |

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `parse_hhmm` | `parse_hhmm(value: str) -> int \| None` | `HH:MM` → 当天分钟数；`24:00`→1440；`23:59`→1439；非法返回 `None` |
| `format_hhmm` | `format_hhmm(minutes: int) -> str` | 分钟数 → `HH:MM`，先夹到 `[0, 1440]`，1440 显示 `24:00` |
| `snippet_turns` | `snippet_turns(messages: Any) -> int` | 数 `role == "self"` 的条数（一问一答算 1 轮） |
| `validate_record` | `validate_record(kind: str, record: Any) -> list[FieldError]` | 主入口：先判 kind，再判是否 Mapping，再 `reject_unknown` → 公共校验 → 该类校验 |
| `field_spec` | `field_spec(kind: str) -> frozenset[str]` | 返回某类允许字段；未知 kind 返回空 frozenset |
| `FieldError` | `@dataclass(frozen=True, slots=True)`，字段 `field/code/message` | `field` 为空字符串表示整条记录级错误 |

**关键实现（照着能重写一遍的流程）**：

1. `validate_record` 先查 `kind not in KINDS` → 直接返回单条 `bad_kind`，不往下走。
2. 再查 `isinstance(record, Mapping)`，不是就返回单条 `bad_type`。
3. 造 `_Checker(record, _SPEC_FIELDS[kind])`，第一步 `reject_unknown()`：遍历记录的所有键，不在白名单的报 `unknown_field`，错误信息里把允许字段全列出来。
4. `_validate_common`：`kind` 出现且不等于入参 kind 报 `kind_mismatch`；`id` 走 `text(maximum=64, minimum=2)`，再单独匹配 `_ID_PATTERN` 报 `bad_id`；`synthetic` 走 `boolean()`。
5. 分派到具体校验器。`_Checker` 的四种单字段检查器语义固定：字段缺 + `required=True` → `missing`；类型不对 → `bad_type`；字符串 `strip()` 后长度 < minimum → `too_short`、原长 > maximum → `too_long`；枚举不在集合 → `bad_enum`；整数（**显式排除 bool**，注释里写了"bool 是 int 的子类"）越界 → `out_of_range`。
6. `_validate_rhythm` 的平铺校验是这一层最硬的一段：`previous_end` 从 0 开始，逐段比对 `start == previous_end`，第 0 段必须从 `00:00`；任何一段 `start >= end` 报 `bad_range`；只要中间出过问题就把 `tiling_ok = False`，后面的"末段必须到 24:00"就不再报（避免一处错刷出一串错）。
7. `_validate_snippet` 在逐条消息校验完后做两条跨消息断言：`"self"` 不在 roles → `no_self_turn`；`"other"` 不在 roles → `no_partner`。

**数据结构 / 存储**：本模块不读不写任何文件与数据库。`_Checked`/`_Checker` 是纯内存对象（`_Checker` 非 dataclass，靠 `self.errors: list[FieldError]` 累积）。

**坑**（代码里真实存在的）：

- `_validate_common` 里 `checker.text("id", maximum=64, minimum=2)` 与 `_ID_PATTERN` 是两道独立判定：`"AB"` 只报 `bad_id`，`"a"` 两条都报（`too_short` + `bad_id`）。
- `social_scene` 的 `day_type` 是**非必需**，`activity` 的却是必需——同一个字段名在不同 kind 里必需性不一致，写新语料时最容易踩。
- `_validate_rhythm` 里 `previous_end = end` 是在 `start is None or end is None` 的 `continue` **之后**才执行，所以某段 `end` 解析失败时，`previous_end` 保持上一段的值，随后报出的 `rhythm_gap` 会指向一个并不相邻的时间点。
- `turns` 允许 0（`minimum=0`），而 `participants` 下限是 2；一个"两人零轮"的场景是合法的。

### 3.2 `app/lifecorpus/validate.py`（365 行）

**职责**：把目录里所有 `*.jsonl` 读进来，**按文件名推断类型**，逐行 JSON → schema → PII 三连校验，汇总成 `FileReport`/`DirReport`，并渲染成人读文本。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `infer_kind` | `infer_kind(filename: str) -> str \| None` | 文件名 → kind；识别不了返回 `None` |
| `iter_jsonl_files` | `iter_jsonl_files(root: Path) -> list[Path]` | 递归列 `*.jsonl`，按相对路径小写排序（输出稳定） |
| `read_jsonl` | `read_jsonl(path: Path) -> Iterator[tuple[int, dict \| None, str \| None]]` | 逐行产出 `(行号, 对象或 None, 错误说明或 None)`，空行跳过 |
| `validate_file` | `validate_file(path: str \| Path, *, kind: str \| None = None, scan_pii: bool = True) -> FileReport` | 校验单文件 |
| `validate_dir` | `validate_dir(dir_path: str \| Path, *, scan_pii: bool = True) -> DirReport` | 校验整目录 + 跨文件 id 去重 |
| `render_dir_report` | `render_dir_report(report: DirReport) -> str` | 多行中文文本 |
| `Issue` | frozen dataclass，字段 `path/line_no/kind/code/field/message/record_id=""` | `line_no == 0` 表示文件级问题 |
| `FileReport` | dataclass，字段 `path/kind/record_count/issues/pii/ids` | 属性 `error_count`/`pii_count`/`ok` |
| `DirReport` | dataclass，字段 `root/files/skipped` | 属性 `issues`/`pii`/`error_count`/`pii_count`/`record_count`/`ok`，方法 `kind_counts()`/`summary()` |

**类型从文件名推断的规则（`infer_kind` 逐条）**：

1. `Path(filename).name` 取 basename。
2. 若以 `.jsonl` 结尾，先切掉这个后缀。
3. 第一轮用 `_SPLIT_PATTERN_STRICT = re.compile(r"[.\-]+")` 切——只按 `.` 和 `-` 切，所以 `social_scene.sample.jsonl` 切出的 token 是 `social_scene / sample`，下划线**不被拆散**。
4. 第一轮找不到，第二轮用 `_SPLIT_PATTERN = re.compile(r"[._\-]+")`（多了 `_`），兼容 `life_activity.jsonl`。
5. 命中 `KINDS` 里任一 token 就返回；两轮都找不到返回 `None`。
6. 结论：**文件名里必须出现 `rhythm`/`activity`/`social_scene`/`snippet` 之一**，`social.scene.jsonl` 会被误判为认不出（因为第一轮切成 `social`+`scene`，第二轮同样），这是命名时必须注意的硬约束。

**逐行校验流程（`validate_file`，编号即代码顺序）**：

1. `display = file_path.name`（报告里只显示文件名，不显示全路径）。
2. `resolved_kind = kind or infer_kind(name)`；显式传了 `kind` 就不看文件名。
3. 文件不存在 → 一条 `file_missing`（`line_no=0`）后直接返回；不是普通文件 → `not_a_file`。
4. kind 推断不出来 → 一条 `unknown_kind`，消息里列出四个可用 token，返回。
5. 逐行 `read_jsonl`：JSON 解析失败 → `Issue(code="bad_json")`，消息带"第 N 列"；解析出来不是 JSON 对象（是 list/标量）→ 同样归到 `bad_json` 分支，消息是"每行必须是 JSON 对象"。
6. 记为一条记录（`record_count += 1`），取 `id` 字符串。
7. `validate_record(kind, record)` 的每个 `FieldError` 逐条转成 `Issue`（code/field/message/record_id 全带上）。
8. 文件内 `id` 重复 → `duplicate_id`，消息带首次出现的行号；文件内 `ids: dict[str,int]` 记录首见行号。
9. `scan_pii=True` 时 `scan_object(record)`：每条命中既进 `report.pii`，又转成一条 `Issue(code="pii_found")`，`field` 用命中路径（如 `messages[0].text`），消息里只放遮掩值。
10. 读完仍是 0 条记录、且没有任何 `bad_json` → 补一条 `empty_file`（注意：全是坏行的文件只报 `bad_json`，不再报空文件）。

**跨文件校验（`validate_dir` + `_check_global_duplicates`）**：

1. 目录不存在 → 造一个假 `FileReport` 带 `dir_missing`；路径不是目录 → `not_a_dir`。
2. `iter_jsonl_files` 递归取全部 jsonl，逐个 `validate_file`，把 `path` 改写成相对路径。
3. `report.skipped` = 根目录下**顶层**非 jsonl 文件的文件名（只扫一层，不递归）。
4. `_check_global_duplicates`：跨文件的 `id` 必须唯一，冲突时后出现的文件报 `duplicate_id_global`，消息里带"上一个文件:行号"。

**错误分级（如实说明：validate 本身没有 severity 字段）**：

代码里**不存在** `level`/`severity` 属性。所谓"分级"由三处结构体现，写文档时不要臆造第四处：

| 结构 | 位置 | 语义 |
|---|---|---|
| `FileReport.issues` vs `FileReport.pii` | 本模块 | 两类并列：schema/文件问题记 issue，PII 命中两份都记（pii 里存 `PiiFinding` 原件，issues 里存格式化后的 `pii_found`） |
| `report.ok` | 本模块 | `not issues and not pii` —— 一票否决，不存在"警告级"通过 |
| `--strict` | `scripts/life_corpus.py` | 不加时即使有错也退 0（先看全量报告）；加了才退 1 |

**全部错误码（源码逐个可查，共 24 个）**：

| 来源 | code |
|---|---|
| `_Checker` 通用 | `unknown_field`、`missing`、`bad_type`、`too_short`、`too_long`、`bad_enum`、`out_of_range` |
| `_validate_common` | `kind_mismatch`、`bad_id` |
| rhythm | `bad_time`、`bad_range`、`rhythm_gap` |
| snippet | `no_self_turn`、`no_partner` |
| `validate_record` | `bad_kind`、`bad_type`（记录非对象） |
| `validate_file` | `file_missing`、`not_a_file`、`unknown_kind`、`bad_json`、`duplicate_id`、`empty_file`、`pii_found` |
| `validate_dir` | `dir_missing`、`not_a_dir`、`duplicate_id_global` |

**渲染格式（`render_dir_report`）**：首行"语料目录：…"；有跳过文件则一行"跳过非 JSONL 文件：…（前 5 个）等 N 个"；"扫描文件：N 个"；每个文件一行定宽状态 `[OK  ]/[FAIL] 路径<34 kind=<12 记录 N 错误 N PII N`；有 issue 时打"错误明细（N 条）"逐条 `path:line [kind] field: message（code）`；有 PII 时打"PII 明细（N 条，仅显示遮掩值）"；最后一行"汇总：文件/记录/错误/PII → 通过|未通过"。

**坑**：

- `Issue.format()` 在 `line_no == 0` 时不打行号，直接打路径；文件级错误因此不会出现 `:0`。
- `read_jsonl` 用 `encoding="utf-8-sig"` 打开，BOM 被吃掉；但 `validate_file` 用 `path.exists()`/`is_file()` 前不 resolve，符号链接按目标判断。
- `DirReport.skipped` 只列顶层非 jsonl 文件；`_derived` 里真实存在 `chat_stats.json`、`partner_mix.json`、`csds_mix.json`、`stranger_mix.json`、`report.md` 这 5 个会被列进去（实测输出正是这 5 个）。

### 3.3 `app/lifecorpus/coverage.py`（484 行）

**职责**：把已通过校验的记录汇成"覆盖矩阵"，并算出一张可执行的缺口清单。docstring 里给了判定口径四条：24 小时以 `segments` 合并后是否正好 1440；活动类型合并 activity 记录与 rhythm 片段的 `label`；社交关系合并 social_scene 与 snippet 的 `relation`；对话轮数 = social_scene `turns` + snippet 中 `role=self` 条数。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `load_records` | `load_records(directory: str \| Path) -> dict[str, list[dict[str, Any]]]` | 按 kind 分组读全部可解析记录；坏行/认不出的文件名**直接跳过**（校验交给 validate） |
| `union_minutes` | `union_minutes(segments: Any) -> int` | 时间段求并集分钟数，重叠只算一次，超出当天裁掉，最后 `min(total, 1440)` |
| `build_coverage` | `build_coverage(records: Mapping[str, Sequence[Mapping[str, Any]]]) -> CoverageReport` | 生成矩阵 + 缺口 |
| `format_coverage` | `format_coverage(report: CoverageReport) -> str` | 多行中文报告，CLI 与 `report` 子命令共用 |
| `CoverageGap` | frozen dataclass，字段 `dimension/item/detail/level="warn"` | 属性 `critical`（`level == "critical"`），`format()` 输出 `[严重]/[提示] dimension/item：detail` |
| `CoverageReport` | dataclass，字段见下 | 属性 `total_records`/`turns_total`/`gap_count`/`critical_gap_count`/`ok`（`critical_gap_count == 0`） |

`CoverageReport` 的 16 个字段（写测试/面板时按名取）：`record_counts`、`synthetic_count`、`day_types`、`day_types_by_kind`、`activity_types`、`locations`、`relations`、`settings`、`interrupt_policies`、`scene_count`、`snippet_count`、`scene_turns_total`、`snippet_turns_total`、`turns_by_relation`、`rhythm_coverage`、`gaps`。

**覆盖矩阵的六个维度与目标值（`TARGETS` 全表）**：

| 键 | 值 | 含义 |
|---|---|---|
| `rhythm_records` | 4 | 作息记录条数目标（"按日型分别采样"） |
| `activity_types` | 10 | 活动类型种类目标 |
| `relations` | 6 | 社交关系种类目标 |
| `scenes` | 10 | social_scene 条数目标 |
| `snippets` | 8 | snippet 条数目标 |
| `turns` | 40 | 对话轮数合计目标 |
| `locations` | 3 | 活动地点种类目标 |

三个"核心集"常量：`CORE_ACTIVITY_TYPES`（8 项：sleep、meal、work、commute、leisure、selfcare、social、chores）、`CORE_RELATIONS`（6 项：family、partner、friend、colleague、stranger、service）、`CORE_DAY_TYPES`（2 项：workday、restday）。

**维度 1 · 24 小时（`union_minutes` + 逐条比对）**：

1. 只接受 `Sequence` 且排除 str/bytes；每个元素必须是 Mapping。
2. `start`/`end` 必须能 `parse_hhmm`，且 `end > start`，否则该段直接丢弃（**不报错**，错误已由 validate 负责）。
3. 区间夹到 `[0, 1440]`，排序后做标准并集扫描：`start > current_end` 就结算上一段，否则 `current_end = max(...)`。
4. 结果 `min(total, 1440)`。
5. 每条 rhythm 记录进 `rhythm_coverage[id]`；没有 `id` 的用 `<无 id 第 N 条>` 占位。

**维度 2 · 日型**：`day_types` 统计所有四种 kind 的 `day_type`；`day_types_by_kind` 再按 kind 细分，用来出"日型 × 记录类型"矩阵，并单独判"某日型有记录但没有 rhythm 样本"。

**维度 3 · 活动类型**：activity 记录的 `activity_type` + rhythm 每段的 `label` 合流到同一个计数器。

**维度 4 · 社交关系**：social_scene 与 snippet 的 `relation` 合流；`turns_by_relation` 按关系累计轮数（`stranger` 单列一条缺口）。

**维度 5 · 场景/片段/轮数**：`scene_count`、`snippet_count` 直接计数；`turns` 只接受 `isinstance(int) and not isinstance(bool) and > 0`。

**维度 6 · 地点与打断策略**：`locations` 只在 activity 记录里统计（rhythm 片段没有地点字段）；`interrupt_policies` 来自 social_scene + snippet。

**缺口清单的判定（`_compute_gaps`，逐条给阈值）**：

| dimension/item | 触发条件 | level |
|---|---|---|
| `24h/rhythm` | `rhythm_coverage` 为空 | critical |
| `24h/<id>` | 单条 rhythm 覆盖 < 1440 | critical（detail 里写"只覆盖 N/1440 分钟（HH:MM 之后断开），缺 M 分钟"） |
| `24h/rhythm_records` | 0 < 条数 < 4 | warn |
| `day_type/<t>` | 该日型一条记录都没有；workday/restday 为 critical，其余 warn | critical / warn |
| `day_type×rhythm/<t>` | 该日型有记录但 rhythm 为 0 | warn |
| `activity/<a>` | `CORE_ACTIVITY_TYPES` 里某项为 0 | warn |
| `activity/distinct` | 种类 < 10；**种类 < 8 时升 critical** | warn / critical |
| `relation/<r>` | `CORE_RELATIONS` 里某项为 0 | warn |
| `relation/distinct` | 种类 < 6；**< 3 时升 critical** | warn / critical |
| `relation/stranger_turns` | `turns_by_relation["stranger"]` 为 0 | warn |
| `scene/social_scene` | 0 条 → critical；< 10 → warn | critical / warn |
| `dialogue/snippet` | 0 条 → critical；< 8 → warn | critical / warn |
| `dialogue/turns` | 合计 0 → critical；< 40 → warn | critical / warn |
| `location/distinct` | 种类 < 3 | warn |
| `interrupt_policy/<p>` | 四种打断策略里某项 0 样本 | warn |

**缺口清单输出格式（`format_coverage` 的 7 段，顺序固定）**：

```text
覆盖报告（life corpus）
记录总量：<total>（rhythm N / activity N / social_scene N / snippet N）
合成数据：<synthetic>/<total> 条标记 synthetic=true
[1] 24 小时作息（rhythm）        <id><28> <minutes>/1440 分钟  完整|缺 N 分钟
[2] 日型 × 记录类型              表头 + 每个日型一行（四种 kind 定宽 + 合计 + 中文说明）
[3] 活动类型（activity 记录 + rhythm 片段）   13 行全枚举（含 0）
[4] 社交关系（social_scene + snippet）        9 行全枚举（含 0）
[5] 场景与对话轮数              场景数/片段数/轮数合计（场景 X + 片段 Y）/平均每场景轮数/关系 × 轮数（按轮数降序）
[6] 场景设定与打断策略          setting 只列实际出现的；interrupt_policy 列全 4 种；location 只列实际出现的
[7] 缺口清单（共 N 项，其中严重 M 项）  每行 "[严重]/[提示] dimension/item：detail"
```

**实测（2026-11-01 本机 `data/human_life/_derived`）**：984 条记录（rhythm 12 / activity 50 / social_scene 502 / snippet 420），12 条 rhythm 全部 `1440/1440 完整`，缺口 0 项、严重 0 项 —— 也就是说这份语料当前是"覆盖达标"状态，改语料后一旦掉出目标值，缺口清单会立刻长出来。

**坑**：

- `_bump` 只接受**非空字符串**键，所以 `day_type`/`location` 缺失的记录在计数器里完全不留痕，`distinct` 统计天然偏小。
- `[6]` 段的 setting/location 用"只列实际出现"的表，而 activity/interrupt_policy 用"列全枚举"的表——同一个报告里两种口径，看报告时别以为 0 行等于漏统计。
- `report.record_counts` 先按 `KINDS` 填 0 再累加，所以四种 kind 一定都在，不会缺键。
- `load_records` 与 `validate_dir` 是**两套独立的读盘**：`report` 子命令会把同一批文件读两遍（一次校验、一次覆盖），文件很大时会明显慢，但没有做共享缓存。

### 3.4 `app/lifecorpus/pii.py`（208 行）

**职责**：中国手机号、18 位身份证、常见邮箱的检测与遮掩。策略写在 docstring：**一律保守（宁可多报）**；同一段文本按 **邮箱 → 身份证 → 手机号** 顺序匹配，避免把邮箱里的数字错认成手机号；对外展示一律用 `masked`，不打印 `value`。

**三条正则（源码原文）**：

| 常量 | 正则 | 说明 |
|---|---|---|
| `_EMAIL_PATTERN` | `[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}` | 常见邮箱字符集，末段 TLD 至少 2 个字母 |
| `_ID_CARD_PATTERN` | `(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])` | 固定 18 位，末位可为 X/x，前后不邻接字母数字 |
| `_PHONE_PATTERN` | `(?<!\d)1[3-9]\d{9}(?!\d)` | `1[3-9]` 开头共 11 位，前后不邻接数字 |

三者合成一个**命名分组**大正则 `_SCAN`：`(?P<email>…)|(?P<id_card>…)|(?P<phone>…)`。分组顺序就是优先级，`match.lastgroup` 直接当 kind 用。

**对外符号（遮掩与检测，签名照抄）**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `mask_phone` | `mask_phone(value: str) -> str` | 留前 3 后 4：`138****8000`；长度 < 7 时整体打星 |
| `mask_id_card` | `mask_id_card(value: str) -> str` | 留前 6 后 4：`110101********1234`；长度 < 10 时整体打星 |
| `mask_email` | `mask_email(value: str) -> str` | 留首字母与域名：`a***@example.com`；无 `@` 整体打星 |
| `mask_value` | `mask_value(kind: str, value: str) -> str` | 按类型分派；未知 kind 整体打星 |
| `scan_text` | `scan_text(text: str, path: str = "") -> list[PiiFinding]` | 单段文本扫描，返回全部命中（按出现顺序） |
| `mask_text` | `mask_text(text: str, path: str = "") -> tuple[str, list[PiiFinding]]` | 返回 `(遮掩后文本, 命中列表)`；无命中原样返回 |
| `scan_object` | `scan_object(obj: Any, path: str = "") -> list[PiiFinding]` | 递归 JSON 结构，扫所有字符串 |
| `mask_object` | `mask_object(obj: Any, path: str = "") -> tuple[Any, list[PiiFinding]]` | 深拷贝并遮掩，**不改入参** |
| `scan_and_mask` | `scan_and_mask(obj: Any, path: str = "") -> tuple[list[PiiFinding], Any]` | 对外主入口，返回 `(findings, mask)` |
| `summarize` | `summarize(findings: Sequence[PiiFinding]) -> dict[str, int]` | 按类型汇总，键固定为 `phone`/`id_card`/`email` |
| `PiiFinding` | frozen dataclass，字段 `kind/value/masked/path/start/end` | `path` 形如 `messages[0].text`，偏移基于原字符串 |

常量：`PHONE = "phone"`、`ID_CARD = "id_card"`、`EMAIL = "email"`、`PII_KINDS`（三元组）、`PII_LABELS`（手机号/身份证号/邮箱）。

**路径拼接规则（`_iter_strings` 与 `mask_object.walk`）**：Mapping 子节点 `parent.key`（parent 空则用裸 key）；Sequence 子节点 `parent[index]`；str/bytes/bytearray 被排除在 Sequence 之外，所以不会把字符串当数组拆。

**保守策略的具体表现**：

1. 不校验身份证校验位、不看地区码、不判手机号号段真实性——**宁可多报**。
2. 手机号用 `(?<!\d)`/`(?!\d)` 双侧断言，11 位数字串中间不会被截一段报出来。
3. 身份证用 `(?<![0-9A-Za-z])`/`(?![0-9A-Za-z])`，18 位数字混在字母串里不报（比如哈希前缀）。
4. 邮箱先匹配，避免 `abc12345678901@x.com` 这种被手机号规则抢走中间 11 位。
5. `scan_text` 的偏移是原字符串偏移，`mask_text` 靠 `cursor` 顺序拼接，不做二次扫描。
6. 无外部词典、无 NLP、无网络——纯正则纯标准库，所以"人名/地名"这类 PII **它抓不到**，抓人名靠上游脚本自己的替换表（见 `scripts/corpus_chat.py`）。

**坑**：

- `mask_email` 的 `head` 在没有本地部分时取 `"*"`，输出仍是 `***@domain`，长度信息丢掉了。
- `mask_object` 对 `tuple` 递归返回 tuple、对 `list` 返回 list，但**其它类型（如 `set`）原样透传**，所以 `scan_object` 看不到 set 里的字符串（JSON 里不存在 set，实际无影响）。
- `summarize` 初始化 `{kind: 0 for kind in PII_KINDS}`，但累加时用 `.get(kind, 0)`，出现未知 kind 会新增键——目前调用方不会传未知 kind。

### 3.5 `app/lifecorpus/fetch.py`（631 行）

**职责**：采集脚手架。它存在的意义是"把边界写死在代码里"，让"随手写个爬虫"这件事在工程上不可能滑坡。docstring 第一段就是边界声明，**这四条不是注释，是代码结构**：

| 写死的边界 | 代码证据 |
|---|---|
| **只发 GET** | `fetch_page` 里 `urllib.request.Request(url, method="GET", headers={...})`；`_fetch_robots` 同样 `method="GET"`。函数签名里**没有** method 参数 |
| **守 robots** | `fetch_page` 第一步就 `robots_verdict(url, policy)`，`allowed is False` 立刻 return `error="robots 拒绝：…"`，请求根本不发 |
| **默认离线** | `plan_fetch` 全函数不 import 网络调用、不写文件；CLI `fetch` 子命令 `set_defaults(dry_run=True)`，不加 `--confirm` 永远只打印计划 |
| **不并发、必须 sleep** | `fetch_many` 是 `for index, url in enumerate(urls)` 顺序循环，`index > 0` 时 `sleep(delay)`，同 host 用 `delay * 1.5` |

**请求头（只有这 4 个）**：`User-Agent: symemory-corpus/1.0 (local research; contact: local)`、`Accept: text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5`、`Accept-Encoding: gzip`、`Accept-Language: zh-CN,zh;q=0.9`。源码里紧跟一行注释：`# 不带 Cookie / Referer / Authorization：不登录、不续会话`。

**明确"没有提供"的开关（这些在代码里查不到，不要以为有）**：

1. 没有 CLI 级"忽略 robots"开关：`scripts/life_corpus.py fetch` 的参数只有 `--urls/--out/--dry-run/--confirm/--delay/--max-bytes/--timeout`。`FetchPolicy.respect_robots` 是库层 dataclass 字段（默认 `True`），CLI 没有暴露。
2. 没有 POST/PUT/表单/登录/验证码通道：`fetch_page` 无 method 参数、无 body 参数。
3. 没有 Cookie / Authorization / Referer 注入口。
4. 没有并发开关（无线程池、无 asyncio）；`fetch_many` 也没有 `workers` 参数。
5. 没有"跳过 sleep"的正式开关：`sleeper` 形参只在库层用于测试注入（默认 `time.sleep`），CLI 不暴露。
6. 没有 UA 覆盖开关（`policy.user_agent` 是字段，CLI 无参数）。
7. 没有代理、没有自定义 SSL context、没有 `--insecure`（`_urlopen` 就是 `urllib.request.urlopen(request, timeout=policy.timeout_s)`，一个参数都不多）。
8. 没有重试/退避/限速自适应；失败就是失败，记进 `error`。
9. 没有断点续抓与去重：manifest 只做追加（`open("a")`）。

**robots 判定流程（`robots_verdict`，`allowed` 是三态）**：

1. `policy = policy or FetchPolicy()`。
2. 拆 URL；scheme 不是 http/https 或没有 netloc → `allowed=False`，reason「只支持 http/https，URL 无效或非网页协议」。
3. `policy.respect_robots is False` → `allowed=True`，reason「policy.respect_robots=False（未检查 robots）」。
4. `robots_url = f"{scheme}://{netloc}/robots.txt"`，写进返回值。
5. 查 `_ROBOTS_CACHE[netloc]`，没有就 `_fetch_robots` 抓一次并存缓存（**每个 host 只抓一次**，`clear_robots_cache()` 可清）。
6. `_fetch_robots` 里任何异常都不外抛，返回 `{"status": None, "text": None, "error": "类型: 消息"}`；单次最多读 `ROBOTS_MAX_BYTES = 512_000` 字节。
7. 取不到（`error` 非空）→ `allowed` 保持 `None`、`checked=False`、reason「robots 取不到（按允许处理但记录）」。**这是"宽松兜底但留痕"，不是静默放行**。
8. 取到了 → 用 `RobotFileParser().parse(text.splitlines())`（解析器抛异常就当空规则），再 `can_fetch(policy.user_agent, url)`：True → `allowed=True`「robots.txt 未禁止」；False → `allowed=False`「robots.txt Disallow 命中」。

`allowed` 三态语义（docstring 原文）：`True` robots 明确允许或关掉了检查；`False` Disallow 命中（含 `User-agent: *`），**调用方必须拒绝**；`None` robots.txt 取不到，按允许处理但记录原因。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `FetchPolicy` | frozen dataclass：`delay_seconds: float = 2.0`、`timeout_s: float = 10.0`、`user_agent: str = DEFAULT_USER_AGENT`、`max_bytes: int = 2_000_000`、`respect_robots: bool = True` | 一次采集会话的礼貌参数；`as_dict()` 供 manifest 快照 |
| `robots_verdict` | `robots_verdict(url: str, policy: FetchPolicy \| None = None) -> dict[str, Any]` | 三态判定，返回 `url/robots_url/allowed/reason/checked/status` |
| `clear_robots_cache` | `clear_robots_cache() -> None` | 清空 host 级缓存（测试用） |
| `fetch_page` | `fetch_page(url: str, policy: FetchPolicy \| None = None) -> dict[str, Any]` | 单次 GET → 纯文本；异常全进 `error`，绝不外抛 |
| `plan_fetch` | `plan_fetch(urls: list[str], policy: FetchPolicy \| None = None) -> list[dict[str, Any]]` | dry-run 计划，**不联网、不读文件、不写文件** |
| `format_plan` | `format_plan(plan: list[dict[str, Any]]) -> str` | 计划渲染，每行 `[OK  ]/[跳过] url host=… robots=未检查` |
| `fetch_many` | `fetch_many(urls: list[str], policy: FetchPolicy \| None = None, *, sleeper: Callable[[float], None] \| None = None) -> list[dict[str, Any]]` | 顺序抓取 + sleep，单条失败不影响后续 |
| `html_to_text` | `html_to_text(markup: str) -> str` | 去注释/script/style，块级标签换行，其余标签剥掉，`html.unescape`，逐行压空白 |
| `decode_body` | `decode_body(raw: bytes, content_type: str = "") -> tuple[str, str]` | 编码探测，返回 `(文本, 实际编码名)` |
| `filter_lines` | `filter_lines(text: str) -> tuple[list[str], dict[str, int]]` | 按行过滤，返回 `(保留行, 丢弃原因计数)` |
| `strip_ads` | `strip_ads(text: str) -> str` | 只留 `filter_lines` 的保留行 |
| `split_snippets` | `split_snippets(text: str, max_len: int = 120) -> list[str]` | 按行切短片段、去重、超长截断；`max_len <= 0` 抛 `ValueError` |
| `sha256_text` | `sha256_text(text: str) -> str` | 正文 sha256 hex，写进 manifest |
| `raw_filename` | `raw_filename(url: str, stamp: str \| None = None) -> str` | `<host>-<YYYYmmdd-HHMMSS>.txt`，host 非法字符压成 `_` |
| `manifest_entry` | `manifest_entry(result: dict[str, Any], policy: FetchPolicy, snippet_count: int) -> dict[str, Any]` | 拼一行 manifest：`url/status/bytes/encoding/sha256/fetched_at/snippet_count/robots/policy` |

**`fetch_page` 的实现顺序**：

1. `_empty_result(url)` 铺好 10 个键（`ok/url/status/bytes/encoding/text/error/fetched_at/robots/truncated`）。
2. scheme 校验失败 → `error` 后返回。
3. `robots_verdict` 判定；`allowed is False` → `error="robots 拒绝：…"` 后返回（此时 `status` 仍是 None）。
4. 发 GET，`_read_limited(response, policy.max_bytes)` 最多读 `max_bytes`。
5. `truncated = len(raw) >= policy.max_bytes`（读到上限即按"被截断"记）。
6. `_looks_gzip(content_encoding, raw)`：头里带 gzip 或前两字节是 `\x1f\x8b` 就 `_gunzip`；解不开（被截断的半截包）保留原字节，交给上层记 truncated。
7. 解压后仍超 `max_bytes` → 再截一次并把 `truncated` 置真。
8. `decode_body` 探编码；`content_type` 含 `html`/`xml`，或文本 `lstrip()` 以 `<` 开头 → `html_to_text`，否则 `_normalize_lines`。
9. `status is None or status < 400` → `ok=True`；否则 `ok=False`、`error=f"HTTP {status}"`。
10. `urllib.error.HTTPError`（403/404 属正常返回）、`ssl.SSLError/TimeoutError/OSError`、兜底 `Exception` 各自落 `error`，绝不上抛。

**片段过滤的 5 类丢弃原因（`_ad_reason` 顺序判定）**：`url`（命中 `_URLISH`：`https?://` 或 `www.`）、`contact`（命中 `_CONTACT_CTA`：(加|扫|关注|私信|联系)+微信/公众号/QQ/vx/VX/V信/群）、`ad`（含 `_AD_WORDS` 14 个词：优惠、促销、限时、抢购、立即购买、点击购买、领红包、扫码、下载APP、推广、代购、客服QQ、戳这里、广告）、`pii`（复用 `scan_text`）、`noise`（整行只有符号，`^[\W_]+$`）。

**坑**：

- 模块 docstring 说"不跟随验证码"，代码层面的实现方式是"根本没有表单/登录路径"，不是有判断逻辑。
- robots 取不到时按允许处理（`allowed=None`）——严格意义上这是**放行**，`fetch_page` 只在 `is False` 时拒绝；要用"取不到就拒"的策略，调用方必须自己判 `checked`。
- `raw_filename` 用 `netloc.rsplit("@",1)[-1].split(":")[0]` 去认证信息与端口，所以 `http://a.com:8080/x` 与 `http://a.com/y` 生成的 basename 相同，撞名只能靠 `life_corpus.py` 里那段 `while text_path.exists()` 加 `-N` 后缀补救。
- `_ROBOTS_CACHE` 是模块级全局，跨进程不复用；同进程内换 UA 也命中同一份缓存（缓存键只有 netloc）。

### 3.6 `app/lifecorpus/__init__.py`（47 行）

**职责**：包入口。`__version__ = "0.2-P"`；`__all__` 列出 19 个符号：`KINDS`、`CoverageGap`、`CoverageReport`、`DirReport`、`FieldError`、`FileReport`、`Issue`、`PiiFinding`、`build_coverage`、`format_coverage`、`infer_kind`、`load_records`、`mask_object`、`mask_text`、`scan_and_mask`、`scan_object`、`scan_text`、`validate_dir`、`validate_file`、`validate_record`、`__version__`（`__all__` 里合计 21 项，含版本号）。

注意**没有导出**的东西（要用来必须走子模块）：`fetch` 整个模块、`render_dir_report`、`field_spec`、`parse_hhmm`/`format_hhmm`/`snippet_turns`、`union_minutes`、`summarize`、`KIND_LABELS`、全部枚举常量。所以 `scripts/life_corpus.py` 是 `from app.lifecorpus.validate import DirReport, render_dir_report, validate_dir` 这样直接打子模块的。

## 4. 数据流

### 4.1 一次"生产 → 校验 → 消费"的完整链路

```text
外部素材                    生产脚本（scripts/）                落盘位置
──────────────────────────────────────────────────────────────────────────
B 站公开接口 / 公报 txt  → corpus_bili.py / life_corpus.py fetch → data/human_life/_raw/     （gitignored）
微信导出 Chat/*.json     → corpus_chat.py        ─┐
CrossWOZ zip             → corpus_stranger.py     │
CSDS json                → corpus_csds.py         ├→ data/human_life/_derived/*.jsonl
语料/ 伴侣素材            → corpus_partner.py      │   （文件名必须带 kind token）
公报 + vlog 事实          → corpus_derive.py       │
vlog 事实 5 条            → corpus_special.py      │
用户口述见闻              → corpus_stories.py     ─┘
                                                     │
                       app/lifecorpus 静态检查        │
        validate_dir / build_coverage  ←──────────────┘
                     │
                     ├→ 终端报告（render_dir_report / format_coverage）
                     └→ data/human_life/_derived/report.md 或 _reports/verify-*.md

运行时消费（绕过 app/lifecorpus，各自读盘）
        _derived/rhythm.*.jsonl ──→ app/life/human_life.py: resolve() ──→ app/life/clock.py:60
        _derived/{partner,stranger,csds}.snippet.jsonl ──→ app/memory/style.py ──→ app/mind/loop.py:426
```

### 4.2 三处数据落点（真实存在的目录）

| 路径 | 内容（实测清单） | 谁写 | 谁读 |
|---|---|---|---|
| `data/human_life/_raw/` | 公报原文 `www.stats.gov.cn-*.txt`（4 个）、B 站元数据 `bili-*.jsonl`（4 个）、`manifest.jsonl`、用户口述 `user-report-*.txt`（4 个）、`crosswoz/{train,val,test}.json.zip` | `corpus_bili.py`、`life_corpus.py fetch --confirm` | 全部 `corpus_*.py` |
| `data/human_life/_derived/` | 16 个 jsonl（rhythm 5 个 / activity 2 个 / social_scene 4 个 / snippet 4 个）+ 5 个非 jsonl（`chat_stats.json`、`csds_mix.json`、`partner_mix.json`、`stranger_mix.json`、`report.md`） | `corpus_*.py --write`、`life_corpus.py report` | `validate/coverage`、`human_life.py`、`style.py` |
| `data/human_life/_reports/` | `verify-YYYYmmdd-HHMMSS.md`（19 个）+ `examples-corpus.md`、`examples-day-life.md` | `scripts/verify_all.py` | 人 |
| `data/human_life/csds/` | `train.json`/`val.json`/`test.json` | 手工放入（数据集自带） | `corpus_csds.py` |

`data/` 整个目录在 `.gitignore` 里，`life_corpus.py` 的 `--out` 帮助文本也明写"data/ 已 gitignore"。

### 4.3 两个运行时消费者（为什么不直接用 app/lifecorpus）

**`app/life/human_life.py`（554 行）**：把 `_derived` 的作息语料读进运行时，**只读接管**。

- 开关：kv `human_life_enabled` 默认 `"0"`，关着时 `clock` 行为一个字都不变。
- 目录：`DATA_ROOT/data/human_life/_derived`，环境变量 `AI_LIFE_HUMAN_LIFE_DIR` 可覆盖（测试用）。
- 日型：`day_type_for(moment)` 按星期判；kv `human_life_force_day_type` 可强制 5 个日型之一。
- 优先级：`PREFERRED_IDS` 里 `workday → (user_ad_day_a, user_ad_day_b, user_ad_day_c)`、`special → (user_sick_day,)`，即"她自己的一天"盖过公报均值。
- 出口：`resolve(moment, day_type=None)` 被 `app/life/clock.py:60` 调用，返回 `activity/energy/sleeping` 三项盖掉时钟的默认值；`snapshot()`/`status()` 被 `app/api/routes.py:117/829/880` 拿去给面板看。
- 缓存：模块级 `_CACHE`，60 秒且文件 mtime/大小不变就复用。
- 读盘：自己写了 `_read_jsonl` / `_to_minute` / `_clean_segments`，**不 import app.lifecorpus**。

**`app/memory/style.py`（343 行）**：把语料压成"给 prompt 用的一点点东西"。

- 只读 4 个文件（`FILES`）：`partner.snippet.jsonl`、`partner.social_scene.jsonl`、`stranger.snippet.jsonl`、`csds.snippet.jsonl`；目录 `DATA_ROOT/data/human_life/_derived`，环境变量 `AI_LIFE_STYLE_DIR` 可覆盖。
- 三个出口：`partner_examples(limit=2)`（最多两条极短示范，相爱 85% : 吵架 15%，`LOVE_WEIGHT = 0.85`）、`stranger_rules()`（固定中文规则，≤ 400 字）、`service_note()`（客服腔降权一句）。
- 硬闸：单句 ≤ 40 字（`MAX_LINE_CHARS`）、一条样本 ≤ 80 字（`MAX_EXAMPLE_CHARS`）、`EXPLICIT_WORDS` 18 个短语命中即整条丢弃、60 秒缓存。
- 调用点：`app/mind/loop.py:426-435` —— 恋人私聊（`chat_type in ("private","")`）给 `partner_examples(2)`；群聊或"还不熟且不是恋人"给 `stranger_rules()+service_note()`；整段被 `try/except` 包住，读失败就当没有。

**结论（设计上是有意的）**：写侧用严格的 schema 把关，读侧用**零依赖、失败即降级**的极简解析保证"语料没了也不崩"。两套解析器并存是当前事实，不是遗漏；改 schema 时必须同时看 §5 的两张清单。

## 5. 改这一层要注意

1. **新增一个枚举值**（比如 `ACTIVITY_TYPES` 加 `pet`）：必须同步 ①`app/life/human_life.py: ACTIVITY_MAP`（否则运行时会回落 `other`）②`app/lifecorpus/coverage.py` 的 `CORE_ACTIVITY_TYPES`（要不要算核心集）③已落盘的 jsonl 是否需要回填。漏掉 ① 不报错，只会静默变成"在发呆"。
2. **新增一个字段**：要改三处——`_COMMON_FIELDS` 或 `_SPEC_FIELDS[kind]`（白名单，不改就会报 `unknown_field`）、对应 `_validate_*` 里的检查器调用、以及消费侧（`human_life.py` 或 `style.py`）是否要读。改完跑 `python scripts/test_lifecorpus.py`（72 条断言）与 `scripts/life_corpus.py validate data/human_life/_derived --strict`。
3. **新增一种语料 kind**：`KINDS` 是元组，顺序影响 `format_coverage` 的表头与 `record_counts` 的遍历顺序；还要加 `KIND_*` 常量、`KIND_LABELS`、`_SPEC_FIELDS`、`_VALIDATORS`、`__init__.py` 的 `KINDS` 导出，以及 `validate.infer_kind` 的 token 集合（它直接 `frozenset(KINDS)`，加了就自动生效）。
4. **收紧/放宽目标值**：只改 `coverage.TARGETS`；`critical` 的硬阈值（activity < 8 升 critical、relation < 3 升 critical）是写死在 `_compute_gaps` 里的数字，不在 TARGETS 里。
5. **改 PII 正则**：`pii.py` 的正则被 5 个 `corpus_*.py` 的 `mask_text` 复用，改宽了会让历史语料"突然有 PII"，改窄了会漏。同时 `scripts/check_secrets.py` 有一套**独立**的同类正则（手机号/身份证/邮箱），两边不一致是当前事实。
6. **改 fetch**：`life_corpus.py` 的 `FETCH_RULES` 文案与 `--confirm` 默认值是刻意设置的"防滑坡"结构，改默认联网会同时破坏 `scripts/test_lifecorpus.py` 里 12 条 fetch 断言（dry-run 零联网、robots 拒绝、异常不外抛、max_bytes 截断、manifest 字段）。
7. **不要指望它进运行时**：`app/lifecorpus` 与内核零耦合是设计约定（`__init__.py` docstring 第 12–13 行），加一条 `from app.core import db` 就会把"可单独拷出去复用"这条性质丢掉。

## 6. 交叉引用

- 文件级清单与行数：docs/03（`app/lifecorpus/` 一节 6 个文件逐个列）。
- 语料怎么被生活时钟消费：docs/10（`human_life.resolve` 与 `clock` 的接管关系）。
- 语料怎么进 prompt：docs/05（`loop.chat_turn` 的风格层注入）、docs/06（记忆与 prompt 预算）。
- 生产这些语料的脚本（参数、产出、是否联网）：docs/21。
- 命令行入口与退出码：docs/21 的 `scripts/life_corpus.py` 小节。
- 数据目录本身不入库：docs/03（`.gitignore` 约定）、docs/17（`data/life.db` 与 `data/` 的区别）。
