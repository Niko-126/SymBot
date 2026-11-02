from __future__ import annotations

import pathlib
from typing import Any

import yaml


class Persona:
    """人物包。只读，不改写源文件。"""

    def __init__(self, raw: dict[str, Any], path: pathlib.Path | None = None) -> None:
        self.raw = raw
        self.path = path

    # ---- 基础 ----﻿‍‌‌‍﻿‍‍​‍​‍​‍‍‍​﻿‍​‍‌​‍‌‌‌​‌﻿​​﻿﻿
    @property
    def name(self) -> str:
        return self.raw.get("name", "她")

    @property
    def key(self) -> str:
        return self.raw.get("key", "default")

    @property
    def species(self) -> str:
        return self.raw.get("species", "")

    @property
    def age_label(self) -> str:
        return self.raw.get("age_label", "")

    @property
    def tagline(self) -> str:
        return self.raw.get("tagline", "")

    @property
    def appearance(self) -> str:
        return (self.raw.get("appearance") or "").strip()

    @property
    def world(self) -> str:
        return (self.raw.get("world") or "").strip()

    @property
    def self_ref(self) -> str:
        return self.raw.get("self_ref", "我")

    # ---- 人格 ----
    def traits(self) -> dict[str, dict[str, float]]:
        return self.raw.get("traits") or {}

    def trait_value(self, dim: str) -> float:
        t = self.traits().get(dim) or {}
        return float(t.get("baseline", 50))

    # ---- 说话 ----
    @property
    def _speech(self) -> dict[str, Any]:
        return self.raw.get("speech") or {}

    def style(self) -> list[str]:
        return list(self._speech.get("style") or [])

    def rules(self) -> list[str]:
        return list(self._speech.get("rules") or [])

    def negatives(self) -> list[str]:
        return list(self._speech.get("negatives") or [])

    def anchors(self, scenes: list[str] | None = None, per_scene: int = 2) -> list[str]:
        anchors = self._speech.get("anchors") or {}
        scenes = scenes or list(anchors.keys())
        out: list[str] = []
        for scene in scenes:
            out.extend(list(anchors.get(scene) or [])[:per_scene])
        return out

    def all_quotes(self) -> list[str]:
        anchors = self._speech.get("anchors") or {}
        out: list[str] = []
        for items in anchors.values():
            out.extend(items or [])
        return out

    # ---- 阶段 ----
    def stages(self) -> list[dict[str, Any]]:
        return sorted(self.raw.get("stages") or [], key=lambda s: float(s.get("at", 0)))

    def stage_for(self, affinity: float) -> dict[str, Any]:
        current = self.stages()[0] if self.stages() else {}
        for s in self.stages():
            if affinity >= float(s.get("at", 0)):
                current = s
        return current

    def stage_index(self, affinity: float) -> int:
        for i, s in enumerate(self.stages()):
            if affinity >= float(s.get("at", 0)):
                current = i
        return current if self.stages() else 0

    def next_stage(self, affinity: float) -> dict[str, Any] | None:
        for s in self.stages():
            if affinity < float(s.get("at", 0)):
                return s
        return None

    def call_user(self, affinity: float) -> str:
        return str(self.stage_for(affinity).get("call_user", "你"))

    # ---- 作息 ----
    def schedule(self) -> list[dict[str, Any]]:
        return self.raw.get("schedule") or []
    def activity_at(self, hour: int, minute: int = 0) -> dict[str, Any]:
        cur: dict[str, Any] = {}
        hhmm = hour * 60 + minute
        for item in self.schedule():
            try:
                h, m = str(item.get("start", "00:00")).split(":")
            except ValueError:
                continue
            if hhmm >= int(h) * 60 + int(m):
                cur = item
        return cur or {"activity": "发呆", "energy": 50}

    # ---- 记忆交接 ----
    @property
    def memory_letter(self) -> dict[str, Any]:
        return self.raw.get("memory_letter") or {}

    def letter_template(self) -> str:
        rel = str(self.memory_letter.get("template", "") or "")
        if not rel:
            return ""
        from app.core.config import ROOT

        p = pathlib.Path(rel)
        if not p.is_absolute():
            p = ROOT / p
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8")


def load(path: pathlib.Path) -> Persona:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Persona(raw, path)
