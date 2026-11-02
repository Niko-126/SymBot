# 外置模块（modules/）

> 从 GitHub 下载 → 解压到 `modules/<id>/` → 面板点「重新加载」或者重启内核。**一行 Python 代码都不用改。**

内核不认识你的模块。它只做一件事：看到 `modules/<id>/manifest.json`，就按你声明的加载。
放错了也不会崩，面板「模块」页会把哪儿不对直接写给你看。

## 1. 三步接入

1. **下载**：拿到模块压缩包（GitHub 上 Code → Download ZIP，或者 `git clone`）。
2. **解压**：整个目录放到仓库的 `modules/<id>/` 里。`<id>` 必须跟 `manifest.json` 里的 `id` 一模一样。
   比如 id 是 `hello`，就放 `modules/hello/`。多套一层文件夹是最常见的放错。
3. **生效**：面板 →「模块」→ 点「重新加载」。没面板就重启内核（`python -m app.cli serve`）。

放完长这样：

```
modules/
├── README.md          ← 本文件（内核第一次启动会自动生成）
├── hello/             ← 示例模块，可以照着抄
│   ├── manifest.json
│   └── hello.py
└── 你的模块/
    ├── manifest.json
    ├── main.py
    └── 其它文件（模板 / 数据 / 子脚本都放这一层）
```

## 2. manifest.json 里写什么

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `id` | ✅ | string | 模块 id，kebab-case（小写字母、数字、连字符），**必须等于目录名** |
| `name` | ✅ | string | 显示名，面板上给人看的 |
| `version` | ✅ | string | 版本号，建议 `1.0.0` |
| `entry` | ✅ | string | 入口 `.py` 的相对路径（相对模块目录，不许用 `..` 跳出去） |
| `kind` | ✅ | string | `tool` / `route` / `hook` / `mixed` |
| `requires` | ✅ | string[] | pip 包名列表。**只校验、不自动装**，没依赖就写 `[]` |

想加 `author`、`description`、`homepage` 这类字段随便加。内核不管，但别人看得懂。

## 3. 入口文件：只要一个 register(api)

```python
def hello_ping() -> dict:
    return {"ok": True, "pong": 1700000000.0}


def register(api):
    api.register_tool("hello_ping", hello_ping, desc="示例工具：回一个 pong")
```

`api` 上就三个方法：

| 方法 | 作用 |
|---|---|
| `api.register_tool(name, fn, desc="")` | 注册工具；`fn(**args)` 返回 dict，名字全局唯一（先到先得） |
| `api.register_router(router)` | 挂一个 `fastapi.APIRouter`，reload 时自动挂到内核上 |
| `api.register_hook(event, fn)` | 注册钩子；`fn(payload: dict)`，事件名自定，`registry().emit(event, payload)` 触发 |

另外还有两样现成的：`api.dir`（自己这个目录的 `pathlib.Path`）和 `api.log`（logger）。
缺依赖就先写进 `requires`，自己 `pip install`。包没装的话模块不加载，面板会告诉你是哪个。

## 4. 装之前先想两秒

- 模块代码跑在**内核进程里**，权限跟内核一样：能读文件、能发网络请求、能写数据库。
- 只装你看得懂、信得过的模块。来路不明的压缩包，等于把整台机器交出去。
- 模块只能通过 `register(api)` 挂东西，改不了内核。停用之后，它的工具 / 路由 / 钩子立刻下架。
- 内核自己只生成两个东西：本文件和 `modules/` 目录。卸载 = 停用 + 删目录。

## 5. 出问题先看这三处

- 面板「模块」页：每个模块都带 `error`（人话）、`loaded`、`tools`、`routes`。
- 命令行：`python -m app.core.modules --list` 打完整清单（JSON）。
- 想弄明白热插拔怎么设计的：`docs/自己写模块.md`。

---

本文件在内核第一次启动、或跑 `python -m app.core.modules --init` 时自动生成。
