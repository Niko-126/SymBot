# 这些文件是测试用的，别拿去线上跑

全是自己写的小样本，不含任何真实站点内容，测试一律不联网。`gbk-page.html` 本来就不是 UTF-8，编辑器打不开是正常的，别手滑重存成 UTF-8。

| 文件 | 干嘛用的 |
| --- | --- |
| `tiny.html` | UTF-8 小页面，带 `script` / `style` / 注释 / 广告行，测 HTML→纯文本 |
| `gbk-page.html` | **GBK 字节**页面（`meta charset=gbk`，HTTP 头不给 charset），测编码兜底 |
| `robots-allow.txt` | `robots.txt` 允许样例（另有 `/private/` 禁抓） |
| `robots-disallow.txt` | `robots.txt` 里 Disallow `/daily-life/`，测拒绝路径 |
| `urls-demo.txt` | dry-run 用的 URL 列表，只解析不联网 |
