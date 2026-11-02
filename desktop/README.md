# 桌面壳（Electron）

> 这篇讲 `desktop/` 这层壳：由哪些文件组成、怎么构建、出问题怎么调、图标放哪。
> 只想用不想改的，看 [上手指南](../上手指南.md) 就行，这层不用碰。

## 它是什么

Electron 管窗口和托盘，Python 内核在后面跑，窗口加载本机面板。点叉只是收进托盘，托盘里点「退出」才真结束，而且只关它自己拉起来的那个内核。

## 目录结构

```text
desktop/
├─ main.js          主进程：单实例、拉起内核、窗口、托盘
├─ preload.js       给页面用的桥，外加顶栏按钮
├─ splash.html      启动过渡页
├─ package.json     依赖清单和打包配置
├─ assets/          图标
└─ README.md        本文件
```

## 怎么构建

先装 Node 20 以上和 pnpm，仓库根还要有一个装好依赖的 `.venv`。

```powershell
pnpm -C desktop install
pnpm -C desktop start     # 开发时直接跑
pnpm -C desktop build     # 出安装包，落在 desktop\dist\
```

产物是 NSIS 安装包，per-user 安装，默认装到 `%LOCALAPPDATA%\Programs\SymBot`，不要管理员权限。

## 怎么调试

| 现象 | 先看哪 |
|---|---|
| 端口被占 | 内核可能本来就在跑，壳会直接复用，这不是 bug |
| 内核没起来 | `data\desktop.log`，真报错都在里面 |
| 想换解释器 | 设环境变量 `SYMBOT_PYTHON`，指到 python.exe |
| 想换端口 | 设环境变量 `SYMBOT_PORT`，面板地址跟着变 |
| 打包卡在下载 | 挂国内镜像再 build，别硬等 |

## 图标在哪

`assets/icon.png` 管窗口和托盘，`assets/icon.ico` 管安装包和 exe，里面含多档尺寸。两张都从面板的 logo 转出来，换图就一起换，源图别低于 256 像素。

---

## 下一步

- 想装现成的 → 看 [上手指南](../上手指南.md)
- 想改面板 → 看 [开发者文档](../开发者文档.md)
- 想知道图标怎么用 → 看 [面板资源说明](../panel/assets/README.md)
