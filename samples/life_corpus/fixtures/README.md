# 采集脚手架测试用 fixture

全部为本仓库自制的小样本，**不含任何真实站点内容**，测试一律不联网。

| 文件 | 用途 |
| --- | --- |
| `tiny.html` | UTF-8 小页面：含 `script` / `style` / 注释 / 广告行，用来测 HTML→纯文本 |
| `gbk-page.html` | **GBK 字节**页面（`meta charset=gbk`，HTTP 头不给 charset），用来测编码兜底 |
| `robots-allow.txt` | `robots.txt` 允许样例（另有 `/private/` 禁抓） |
| `robots-disallow.txt` | `robots.txt` Disallow `/daily-life/`，用来测拒绝路径 |
| `urls-demo.txt` | dry-run 演示用的 URL 列表（只解析不联网） |

说明：`gbk-page.html` 不是 UTF-8，编辑器打不开属正常；重新生成方式见
`scripts/test_lifecorpus.py` 的 `check_fetch` 注释（只读字节，不重写）。
