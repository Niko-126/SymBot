# 31 · 上架 GitHub 收尾清单

> 发布前逐条打勾。没打完**不要 push**。owner 说"发布"才执行最后两步。

---

> 前端 UI 测试阶段：页面可能仍有 BUG / 视觉问题，欢迎 Issue 共创。

## 0. 状态（2026-11-02 收尾完成，待 owner 拍板发布）

发布版已生成在 `<发布目录>`（本地 commit `5ca4e11`，**未 push / 未打 tag / 未发 release**）。

| 项 | 结果 |
|---|---|
| 仓库卫生 | 5.08 MB / 277 文件；无 `data/`、`.venv/`、`vendor/`、`life.db`、`config.local.yaml`、`.wm.key` |
| 隐私 | 真实 QQ/群号/api_key 在入库文件里 **0 命中**（`HANDOFF.md`、`TASKS.md`、`scripts/live/` 已排除） |
| 第三方 | 无 NapCat/CosyVoice/RVC/表情包/权重；`NOTICE.md` + `LICENSE` 在根目录 |
| 安装 | `environment: test` 默认：独立库 `data/test-life.db`、mock、OneBot/语音/热梗关闭；clone 即跑 |
| 功能自检 | adv **35/35** · smoke **387/0** · fullcheck **148/148 + 路由 127/127**（无表情库时 1 项 SKIP）· panel_check **143/0** |
| 水印 | 130 文件已注入，`verify --scan app` → 130/130（密钥 `scripts/.wm.key` 不入库） |
| 闭源记忆 | `app/memory/_vault.bin` 154,103 B（38 成员），删掉 vendor 源码仍可 remember/recall |
| 桌面 | `desktop/dist/SymBot Setup 0.1.0.exe` 79,137,272 B，SHA256 `A44FD9A7…BE4693`，per-user |

**剩下只有一件**：owner 说"发布"→ `git remote add origin … && git push -u origin main` + 打 tag `v0.1.0`。

---

## A. 仓库卫生（红线）

- [ ] 删除仓库根目录游荡的 `life.db`（未跟踪，含真实聊天）
- [ ] `.gitignore` 补：`life.db`、`*.db`、`*.db-wal`、`*.db-shm`、`data/`、`.third_party/`、
      `config.local.yaml`、`persona-active.yaml`、`*.key`、`exports/`、`dist/`、`build/`
- [ ] `git status` 里**没有**：`.venv/`、`node_modules/`、`data/`、`.third_party/`、任何 `.pyd`（除非有意发布闭源运行时）
- [ ] 仓库体积 < 50 MB（`git count-objects -vH` 看 size-pack）

## B. 隐私清理（红线）

```powershell
# 文档里只写占位号。发布前把你本机的真实号填进 $qqs 再跑（别把真号写回仓库）。
# 应该零命中；config.local.yaml 除外（它本身不进仓库）。
$qqs = @('123456789', '987654321')
Get-ChildItem -Recurse -File -Include *.py,*.yaml,*.md,*.html,*.json |
  Where-Object { $_.FullName -notmatch '\\(\.git|\.venv|\.third_party|data)\\' } |
  Select-String -SimpleMatch -Pattern $qqs |
  Select-Object Path, LineNumber, Line
# key / token 泄漏（应零命中；config.local.yaml 除外）
Get-ChildItem -Recurse -File -Include *.py,*.md,*.yaml |
  Select-String -Pattern 'sk-|Bearer [A-Za-z0-9]{20,}'
```

- [ ] 真实 QQ 号 / 群号 → 全替换成 `123456789` / `987654321`
- [ ] api_key / voice api_key → 只留在 `config.local.yaml`（不进库）
- [ ] `docs/` 里出现过的真实对话、昵称、生日 → 删或打码（示例值用 `2000.01.01`）
- [ ] `HANDOFF.md` 里也含真实 QQ/路径 → **发布前删掉这份文件**（它只是交接用）
- [ ] 家人名单 / 小号 / NapCat 路径只写 `config.local.yaml`：代码与示例里只有占位

## C. 第三方合规

- [ ] 仓库无 NapCat/CosyVoice/RVC/Demucs 权重/表情包（见 `NOTICE.md` 第二节）
- [ ] `NOTICE.md` 与 `LICENSE` 同时在仓库根目录
- [ ] `docs/30-依赖与安装评估.md` 与实际 `requirements.txt` 一致
- [ ] `requirements.txt` 里没有 git URL / 本地路径依赖

## D. 安装可用性

- [ ] 干净机器（或全新 venv）按 `docs/01-quickstart.md` 走一遍，15 分钟内能看到面板
- [ ] `config.example.yaml` 存在且**不含任何真实值**
- [ ] 没有 API key 时启动 → 进入 mock 模式且**不报错**（面板能开、能聊）
- [ ] `install.ps1`（若有）在无管理员权限下也能跑通（NapCat 那步除外）

## E. 功能自检（发版前必须全绿）

```powershell
$env:SYMEM_MOCK="1"
python scripts/adv_regress.py    # 35/35
python scripts/smoke.py          # 387/0
python scripts/fullcheck.py      # 149/149 + 路由 113/113
python scripts/panel_check.py    # 0 失败（通过数随功能增长，别拿旧数字当回归）
```

- [ ] 四套件全部 0 失败（通过数随功能增长，以实际输出为准；别把旧数字当回归）
- [ ] 真机（真实 key、真实 NapCat）跑通：私聊回话、关键词语音、点歌、主动开口、关系上限开关
- [ ] 面板「服务控制」能重启内核并恢复

## F. 文档

- [ ] `README.md` 首屏有：一句话是什么、截图、30 秒上手、许可声明（个人免费/禁商用/记忆闭源）
- [ ] `README.md` 写明"代码仓库不含第三方模型与素材；Standard/Full 分发包附带部分资源并按 `NOTICE.md` 声明权属"
- [ ] `docs/README.md` 索引补齐新增文档（30/31/32…）
- [ ] `HANDOFF.md`、`.tmp/`、`TASKS.md`（内部任务板）→ 发布前移除或移到私有仓库

## G. 水印

- [ ] `python scripts/apply_watermark.py --write` 已注入
- [ ] `python scripts/verify_watermark.py --scan .` 命中数与注入数一致
- [ ] 抽查 3 个文件：删掉水印后 verify 能报"缺失"

## H. 发布（等 owner 指令，不要自己动）

- [ ] `git add -A` 后**人工过一遍** `git diff --cached --stat`
- [ ] 按 `HANDOFF.md` 第 7 节切分提交
- [ ] `gh repo create` / `git remote add origin` + `git push -u origin main`
- [ ] Release：打 tag `v0.1.0`，附 `SHA256SUMS.txt`、安装说明、许可声明
- [ ] 关闭 Issues 里暴露隐私的旧内容

---

## 附：发布前 5 分钟速查

```powershell
git status --short | Measure-Object            # 数量对不对
git ls-files | Select-String "life.db|\.venv|data/|\.third_party"   # 应为空
python scripts/verify_watermark.py --scan app  # 水印在
python scripts/fullcheck.py                    # 149/149
```
