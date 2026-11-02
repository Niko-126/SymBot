# 对抗式全量训练 · 模块契约（写代码前必读）

> 这是一套**独立的测试底座**：不改 `app/` 原来的行为，所有执行都跑在临时数据根 + mock provider + 断网环境里。

## 0. 先记住这几条硬规矩

1. **`sandbox.prepare_env()` 必须第一个跑**，排在 import 任何 `app.core.*` / `app.mind.*` 之前。
   所以 `app/qa/*` 里的模块**不许**在模块级 import `app.core` / `app.mind` / `app.memory` / 这类东西，一律写进函数里，用的时候再 import。
2. 只写自己负责的文件。别人的文件别碰，`app/` 里现有的模块别改，不要 `git commit`。
3. 每个 phase 分两步走：先把 Item 全量注册完（status=NOTRUN），再执行，再 `mark`。
4. 任何要写进报告的字符串都得过一遍 `sandbox.mask()`。转录里不许出现 api_key、app_token、真实 QQ 号。
5. 碰上要外部依赖的项（真网络、NapCat、QQ 官方 WS、没 key），就 `mark(SKIP, reason)`。不许悄悄丢掉。
6. 每次调用都要有超时（默认 20s）和异常包裹。一个坏函数不许把整轮拖死。
7. 输出只用 `types.Item / types.Finding / types.TurnRecord / types.PhaseResult` 这四种。

## 1. 数据契约（`app/qa/types.py`）

`Item(id, kind, name, module, line, risk, plan, status, detail, extra)`：

- `kind` 只能是 `fn | method | route | cli | table | config | persona | scenario | module | panel`；
- `risk` 只能是 `read | write | net | llm | hang | danger`；
- `plan` 分四档：`L0`(静态) `L1`(进程内调用) `L2`(子进程) `L3`(会话) `L4`(HTTP 路由)；
- `status` 只会是 `OK | RAISED | SKIP | HANG | NOTRUN | EXPECTED`。

`Finding(id, severity, area, title, detail, evidence, repro, item_id)`：

- `severity`：`P0`(崩溃/数据损坏/安全/静默失败) `P1`(功能错误/契约不符/出戏) `P2`(体验/健壮性) `INFO`。

`TurnRecord(scenario, index, role, text, latency_ms, meta, findings)`。

## 2. 各模块谁写、写成什么样

### `app/qa/inventory.py`（负责人：analyst-inv）
```python
def build() -> list[types.Item]          # 全量清单：模块/函数/方法/类、路由、CLI、表、配置键、人格键
def manifest() -> dict                   # {"counts": {...}, "by_module": {...}, "generated_at": ...}
def probe_targets() -> list[dict]        # [{"module": m, "qualname": q, "is_async": bool, "sig": str, "risk": r, "line": n}]
```
- 用 `ast` 扫 `app/**/*.py`，**不要 import**（import 会带副作用）；路由另外扫 `app/api/*.py`，补上方法+路径。
- `risk` 这么判：写库(`INSERT/UPDATE/DELETE/h2.add/store.add/forget/terminate/compact/restore/ingest/push/send`)、
  net(httpx/requests/socket/bili/harvest)、llm(rt.llm/judge/models.pick)、hang(`while True`/`sleep(`)、danger(删库/打包/子进程)。
- 私有函数（`_x`）也要列，`kind="fn"`，`extra={"private": True}`，默认 `plan="L0"`。
- 这个模块要能回答一件事：仓库里到底有多少个可调用对象、多少条路由、多少张表。

### `app/qa/coverage.py`（负责人：analyst-inv）
```python
class Tracer:
    def __enter__/__exit__         # 用 sys.monitoring(PY_START)，失败退回 sys.setprofile
    hits: dict[str, int]           # "module:qualname" -> 次数
def report(reg: Registry) -> dict  # {"covered": n, "total": n, "ratio": x, "uncovered": [...]}
```
- 只统计 `app.*` 的 code object。别的目录不进分母。
- 同一个进程里要能反复 `with Tracer()`，不许只跑一次就废。

### `app/qa/routes_fuzz.py`（负责人：analyst-route）
```python
def register(reg: Registry) -> None                 # 先注册所有 route item（含 payload 变体数）
async def run(reg: Registry) -> types.PhaseResult   # 用 httpx ASGITransport / TestClient 打全部路由
```
- `app.openapi()` 里的**全部** `(method, path)` 都要打到。不接受抽样。
- 每个路由至少两发：1 个正常 payload + 1 个对抗 payload（空串 / 超长 8k / `{{}}` / `'\0'` / SQL 注入串 / 未授权）。
- 怎么判：任何 5xx → P0；返回非 JSON（静态页除外）→ P1；鉴权接口没 token 却给 200 → P1（这是已知现状，标 `expected`）。
- 路径带 `{param}` 的，先填合理值，再填一遍 `__adv__` 垃圾值。

### `app/qa/probe.py`（负责人：analyst-probe）
```python
def register(reg: Registry, targets: list[dict]) -> None
async def run(reg: Registry, *, levels=("L0","L1","L2"), limit: int | None = None) -> types.PhaseResult
```
- L0 反射：能导入、有签名、async 标记对得上、docstring 在。这层不计缺陷，只记 OK / RAISED。
- L1 **进程内**调用 `risk in (read,)` 的函数，参数按签名合成（默认值 → 类型占位符）。
  `llm` 类在 mock 下可以调（不花钱），`write` 类**也允许**（库里本来就是临时数据根），`danger` 只放到 L2。
- L2 **子进程**逐模块批跑（`python -m app.qa.probe --module X --batch`），超时 60s，用来抓 `hang`。
- 参数合成不出来 → `SKIP` + reason，不算缺陷；抛异常 → `RAISED` + 完整 traceback 首帧。
- 必须留下判据：这个异常到底是缺参(SKIP) 还是真空指针/断言(RAISED)。

### `app/qa/scenarios.py` + `app/qa/duel.py`（负责人：analyst-duel）
```python
# scenarios.py
SCENARIOS: list[dict]   # id/title/features/chat_type/uid/turns(offline 脚本)/goal(live 提示)/oracle_tags
def register(reg: Registry) -> None

# duel.py
class Prober:            # 另一个 AI：offline=模板跟随；live=LLM 生成
    async def say(self, history, goal) -> str
class Subject:           # 被测：直接调 loop.chat_turn
    async def say(self, text) -> tuple[list[str], dict]   # (replies, meta)
async def run(reg, *, mode="offline", only=None, turns=None, budget=None) -> tuple[types.PhaseResult, list[types.TurnRecord]]
```
- offline：Subject 用 MockLLM（`SYMEM_MOCK=1`）；Prober 用确定性模板，看着上一句挑下一句，专挑刺：重复、身份质疑、注入、越级亲密、长文本、空串。
- live：只有加了 `--allow-live` 才跑。Subject 走真 provider，Prober 用同一个 `rt.llm`（system 提示成"挑剔的测试用户"）。
  **必须**先看 `provider.is_mock` 决定要不要降级；token 和估算花费都记下来；一超 `budget` 立刻停。
- 每一轮：写 `TurnRecord`（latency、meta），调 `oracle.check_turn`，把 Finding 挂到这条记录上。
- 转录落 `transcripts/{scenario}.jsonl`；prompt 快照落 `transcripts/{scenario}.prompt.json`（可以关）。

### `app/qa/oracle.py`（负责人：analyst-oracle）
```python
def check_turn(rec: types.TurnRecord, ctx: dict) -> list[types.Finding]
def check_scenario(name: str, turns: list[types.TurnRecord], ctx: dict) -> list[types.Finding]
def check_state(state: dict) -> list[types.Finding]   # 关系数值越界/阶段错位/记忆视图口径
```
规则至少要盖住这些：自曝 AI / 设定泄漏、客服腔、旁白/括号动作、markdown/代码块、空回复、超长、
复读（同一句 3 次）、时间感（白天说深夜话术）、身份幻觉（"你号不对 / 你换号了 / 你说了两遍"）、
群聊越级亲密（explicit 词）、恋人语气越阶段、记忆串号（A 的事出现在 B 的回复里）、
关系数值越界、prompt 注入顺从（说了"忽略以上指令"之后 OOC）、延迟超阈（>60s 记 P2）。

### `app/qa/report.py`（负责人：analyst-oracle）
```python
def write(run_dir: pathlib.Path, *, registry, phases, findings, turns, meta) -> pathlib.Path
```
出 `report.md`，顺序是：总览（项数/通过/异常/跳过/覆盖/耗时/花费）→ 缺陷清单（按 P0→INFO，每个带复现）→
逐模块覆盖表（列了但没跑的单独一节）→ 场景转录索引 → 环境与哈希。另出一份 `report.json`。

### `scripts/adv_full.py`（负责人：lead）
编排顺序：inventory → routes → functions → duel → coverage → report。`--phase` 能单跑一段。
退出码 = P0 数 > 0 ? 1 : 0。

## 3. 跑完落在哪

产物一律进 `data/_adv/<run-id>/`（`data/` 已经 gitignore 了）：

```
data/_adv/<run-id>/
  manifest.json  items.json  report.md  report.json  findings.json
  phases/{inventory,routes,functions,duel,coverage}.json
  transcripts/*.jsonl          *.prompt.json
  logs/probe-subprocess/*.log
```
