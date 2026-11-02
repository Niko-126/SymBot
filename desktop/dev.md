# 开发笔记：起第二个实例

单实例锁是按 Electron 的 userData 目录算的，所以第二实例要换 `--user-data-dir`，
内核端口也要换，不然两个壳抢同一个 8787。

```powershell
# 窗口 A：默认 8787
pnpm -C desktop start

# 窗口 B：8788 端口 + 独立 userData（仓库根的 .tmp/ 已在 .gitignore 里）
$env:SYMBOT_PORT="8788"
pnpm -C desktop exec electron . --user-data-dir="..\.tmp\desktop-8788"
```

注意：

- 两套壳共用同一个 `data\`（SQLite / 日志 / 语音缓存）。调界面、看两套窗口没问题，
  别同时跑写库的自动化，容易撞 `life.db` 的锁。
- 第二个实例的日志还是写同一个 `data\desktop.log`，按时间戳区分。
- 只想验证语法：`node --check desktop\main.js`；`node --check desktop\preload.js`。
- 想干净重来：删 `desktop\node_modules` 再 `pnpm -C desktop install`（Electron 二进制会重新下）。
