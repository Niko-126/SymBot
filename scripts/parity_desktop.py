"""对照测试：电脑版内核（Python）在同组输入下的表现。

输出 JSON，供与手机离线内核（JS）对比。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import os

os.environ["AI_LIFE_DB"] = str(pathlib.Path(tempfile.gettempdir()) / "ai-life-parity.db")
# 开发者口令：config.yaml 留空，这里给一个只用于本脚本的临时口令（config.py 读 SYMEM_DEVELOPER_KEY）
os.environ["SYMEM_DEVELOPER_KEY"] = "parity-dev-key"
for suffix in ("", "-wal", "-shm"):
    p = pathlib.Path(os.environ["AI_LIFE_DB"] + suffix)
    if p.exists():
        p.unlink()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
import app.life.behavior as behavior  # noqa: E402
from app.life.behavior import Reaction  # noqa: E402

behavior.decide = lambda life, n=0, persona=None, **kw: Reaction(mode="awake", delay_ms=0)

client = TestClient(app)
client.post("/v1/settings", json={
    "deep_think": False, "llm_appraisal": False,
    "show_delta": True, "style": "gentle", "mix_mode": False,
})
client.post(f"/v1/dev/state/set?password={os.environ['SYMEM_DEVELOPER_KEY']}", json={
    "affinity": 0, "trust": 0, "attraction": 0, "aversion": 0,
})

MESSAGES = [
    "今天好累",
    "我跟你说个事，其实我最近在考虑辞职",
    "你真好，抱抱",
    "你只是个程序吧",
    "你是人类吗",
]

out = {"replies": [], "meta": []}
for text in MESSAGES:
    r = client.post("/v1/chat", json={"text": text}).json()
    reply = r.get("reply") or []
    meta = r.get("meta") or {}
    out["replies"].append({
        "text": text,
        "segments": len(reply),
        "chars": sum(len(x) for x in reply),
        "joined": " / ".join(reply),
        "delta": meta.get("delta") or {},
    })
board = client.get("/v1/board").json()
out["final"] = {
    "affinity": board["relation"]["affinity"],
    "trust": board["relation"]["trust"],
    "attraction": board["relation"]["attraction"],
    "aversion": board["relation"]["aversion"],
    "stage": board["relation"]["stage"],
    "memories": board["memories"]["total"],
}
print(json.dumps(out, ensure_ascii=False, indent=2))
