# 测试样本：别拿去线上跑

这里全是自己写的小样本，不含任何真实站点内容，测试一律不联网。

> `gbk-page.html` 本来就不是 UTF-8，编辑器打不开是正常的。别手滑重存成 UTF-8，
> 那这张样本就废了——它专门用来测编码兜底。

| 文件 | 干嘛用的 |
|---|---|
| `tiny.html` | UTF-8 小页面，带脚本、样式、注释、广告行，测「网页转纯文本」 |
| `gbk-page.html` | **GBK 字节**的页面（meta 里声明 gbk，但 HTTP 头不给编码），测编码兜底 |
| `robots-allow.txt` | `robots.txt` 允许样例（另有 `/private/` 禁抓） |
| `robots-disallow.txt` | `robots.txt` 里禁抓 `/daily-life/`，测拒绝路径 |
| `urls-demo.txt` | 演练用的地址列表，只解析不联网 |

---

## 下一步

- 语料格式说明 → [上一层 README](../README.md)
- 联网工具怎么工作 → [开发者文档](../../../开发者文档.md)
