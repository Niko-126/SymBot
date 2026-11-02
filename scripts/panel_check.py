r"""面板自检：把「面板能不能用」变成可自动跑的检查。

用法：
    .\.venv\Scripts\python.exe scripts\panel_check.py

检查四件事（全部离线、mock、临时库）：

1. **页面能开**：`/`、`/settings.html`、`/console.html`、`/trend.html` 都 200；
2. **接口对得上**：面板 JS 里出现的每个 `/v1/...` 路径，后端 OpenAPI 里必须存在；
3. **元素找得到**：`$('id')` / `getElementById('id')` 引用的 id，要么页面里有，
   要么由 JS 动态创建（`xxx.id = 'id'`）；
4. **契约不漂移**：`/v1/settings` 返回的键必须覆盖面板读取的每一个键
   （工具/声音那一段最容易改后端忘改面板）。

退出码 0 = 全过；1 = 有失败项（明细在末尾）。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TMP = pathlib.Path(tempfile.mkdtemp(prefix="smd-panel-"))
os.environ["SYMEM_DATA"] = str(TMP)
os.environ["SYMEM_DB"] = str(TMP / "life.db")
os.environ["SYMEM_MOCK"] = "1"
os.environ["SYMEM_QQ_ENABLED"] = "0"
os.environ["SYMEM_ONEBOT_ENABLED"] = "0"
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PAGES = ("/", "/settings.html", "/console.html", "/trend.html")
# 这些路径需要参数/写操作，静态 GET 打不到，跳过"能不能 200"那一条（但仍要求存在）
GET_SKIP = (
    "/v1/console/memory/", "/v1/console/chat/", "/v1/console/intimate/",
    "/v1/console/impression/", "/v1/console/sticker/", "/v1/console/tts/file",
    "/v1/dev/", "/v1/session/new",
)

# 面板读取的键（改后端漏一个，这里就红）
SETTINGS_KEYS = {
    "tools": ("enabled", "group_enabled", "web_enabled", "vision_enabled",
              "vision_model", "vision_base_url", "vision_has_key", "mocked"),
    "voice": ("enabled", "send_in_chat", "base_url", "voice", "style",
              "cache_ttl_days", "cache_enabled"),
    # 设置页「连接与账号」读的两段（P0-4 配置外置化）
    "launcher": ("qq", "qq_set", "napcat_dir", "webui_port"),
    "family": ("roles", "father_roles", "announce_godfather"),
}
TTS_STATUS_KEYS = ("ok", "available", "reason", "cached_files", "max_per_min",
                   "used_this_minute", "workers", "cache_ttl_days")

OK = 0
BAD: list[str] = []


def ck(name: str, cond: bool, detail: str = "") -> None:
    global OK
    if cond:
        OK += 1
    else:
        BAD.append(f"{name}  {detail}"[:300])
        print(f"  FAIL  {name}  {detail}"[:300])


def _read(name: str) -> str:
    return (ROOT / "panel" / name).read_text(encoding="utf-8", errors="replace")


def main() -> int:
    client = TestClient(app)

    print("[1] 页面能开")
    for page in PAGES:
        r = client.get(page)
        ck(f"GET {page}", r.status_code == 200, f"status={r.status_code}")

    print("[2] 面板引用的接口都存在")
    spec = app.openapi()
    server = {re.sub(r"\{[^}]+\}", "{}", p) for p in spec["paths"]}
    for html in sorted((ROOT / "panel").glob("*.html")):
        text = html.read_text(encoding="utf-8", errors="replace")
        for raw in sorted(set(re.findall(r"[\"'](/(?:v1|onebot)[^\"'\s?#)]*)", text))):
            norm = re.sub(r"\$\{[^}]*\}", "{}", raw)
            norm = re.sub(r"\{[^}]+\}", "{}", norm)
            hit = norm in server or norm.rstrip("/") in server
            # 前缀型（/v1/console/memory/ + id）用前缀匹配兜一下
            if not hit:
                hit = any(s.startswith(norm.rstrip("/") + "/") for s in server)
            ck(f"{html.name} → {raw}", hit, "后端没有这个路径")

    print("[3] 面板引用的元素 id 都存在")
    for html in sorted((ROOT / "panel").glob("*.html")):
        text = html.read_text(encoding="utf-8", errors="replace")
        ids = set(re.findall(r'\bid="([^"]+)"', text))
        dynamic = set(re.findall(r"\.id\s*=\s*['\"]([\w\-]+)['\"]", text))
        refs: set[str] = set()
        for pat in (r"\$\(\s*['\"]([\w\-]+)['\"]\s*\)",
                    r"getElementById\(\s*['\"]([\w\-]+)['\"]\s*\)",
                    r"querySelector\(\s*['\"]#([\w\-]+)['\"]\s*\)"):
            refs |= set(re.findall(pat, text))
        missing = sorted(refs - ids - dynamic)
        ck(f"{html.name} 元素引用", not missing, f"缺 {missing[:8]}")

    print("[4] 契约不漂移")
    r = client.get("/v1/settings")
    ck("GET /v1/settings 200", r.status_code == 200, str(r.status_code))
    data = r.json() if r.status_code == 200 else {}
    for section, keys in SETTINGS_KEYS.items():
        got = data.get(section) or {}
        for k in keys:
            ck(f"settings.{section}.{k}", k in got, f"面板要读，后端没给（keys={sorted(got)[:8]}）")

    r = client.get("/v1/console/tts")
    ck("GET /v1/console/tts 200", r.status_code == 200, str(r.status_code))
    st = r.json() if r.status_code == 200 else {}
    for k in TTS_STATUS_KEYS:
        ck(f"tts.{k}", k in st, f"面板要读，后端没给（keys={sorted(st)[:8]}）")

    print("[5] 面板的保存动作能走通")
    body = {
        "tools_enabled": True, "group_tools_enabled": True,
        "web_enabled": True, "vision_enabled": True,
        "vision_model": "Qwen/Qwen2.5-VL-7B-Instruct", "vision_base_url": "",
        "tts_enabled": False, "tts_send_in_chat": False, "tts_cache_enabled": True,
        "tts_cache_ttl_days": 15, "tts_base_url": "http://127.0.0.1:9880",
        "tts_voice": "feiyu", "tts_style": "cute",
    }
    r = client.post("/v1/settings", json=body)
    ck("POST /v1/settings 200", r.status_code == 200, str(r.status_code))
    out = r.json() if r.status_code == 200 else {}
    ck("开关回显 group_enabled", (out.get("tools") or {}).get("group_enabled") is True, str(out)[:160])
    ck("视觉模型回显", (out.get("tools") or {}).get("vision_model") == body["vision_model"], str(out)[:160])
    ck("缓存天数回显", (out.get("voice") or {}).get("cache_ttl_days") == 15, str(out)[:160])

    print("[6] TTS 试听在关着的时候优雅拒绝")
    r = client.post("/v1/console/tts", json={"text": "面板自检"})
    ck("POST /v1/console/tts 不炸", r.status_code == 200, str(r.status_code))
    j = r.json() if r.status_code == 200 else {}
    ck("拒绝原因是 voice-disabled/mock", j.get("ok") is False and j.get("reason") in ("voice-disabled", "mock", "rate-limited"), str(j)[:160])

    print()
    print(f"结果：{OK} 通过，{len(BAD)} 失败")
    if BAD:
        for b in BAD:
            print("  -", b)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
