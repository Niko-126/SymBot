from __future__ import annotations

import asyncio
import hashlib
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import Config
from app.persona.load import Persona


@dataclass
class LLMReply:
    text: str
    provider: str = "mock"
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: int = 0
    reasoning: str = ""
    reasoning_tokens: int = 0


class BaseLLM:
    provider = "base"

    async def chat(
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        model: str | None = None,
        thinking: bool | None = None,
    ) -> LLMReply:  # pragma: no cover
        raise NotImplementedError


class MockLLM(BaseLLM):
    """无 API Key 时的本地模拟。

    作用不是"假装能聊"，而是让面板、好感、记忆、导入全链路在离线状态下可验证。
    它只从人物包的原话池里取句子，所以演示出来的语气仍然是她的。
    """

    provider = "mock"

    def __init__(self, persona: Persona) -> None:
        self.persona = persona
        p = persona._speech.get("anchors") or {}  # noqa: SLF001 - 同包内访问
        self.pool: dict[str, list[str]] = {k: list(v) for k, v in p.items()}
        self.fallback = persona.all_quotes() or ["……嗯。"]
        self.last_thinking: bool | None = None

    def _scene(self, text: str) -> str:
        t = text.lower()
        if any(k in t for k in ("累", "困", "睡", "熬夜", "加班", "烦")):
            return "关心"
        if any(k in t for k in ("想你", "喜欢", "宝宝", "老婆", "抱", "亲", "爱你")):
            return "被撒娇"
        if any(k in t for k in ("滚", "讨厌", "生气", "烦死", "闭嘴")):
            return "不高兴"
        if any(k in t for k in ("你是谁", "ai", "机器人", "模型", "真的吗")):
            return "深层"
        if any(k in t for k in ("在吗", "在不在", "在么", "hello", "hi", "喂")):
            return "日常"
        return "她自己"

    async def chat(
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        model: str | None = None,
        thinking: bool | None = None,
    ) -> LLMReply:
        started = time.perf_counter()
        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_text = m.get("content", "")
                break

        scene = self._scene(user_text)
        pool = self.pool.get(scene) or self.fallback
        seed = int(hashlib.sha256((user_text + str(len(messages))).encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)

        lines = [rng.choice(pool)]
        # 偶尔追一句，模拟人连发两条﻿‌﻿‍‌‌﻿‌​‍﻿​​‍​​﻿​﻿﻿﻿​​‍‍​‌​‌﻿‌​‍﻿
        if rng.random() < 0.45:
            extra_pool = self.pool.get("日常") or self.fallback
            extra = rng.choice(extra_pool)
            if extra not in lines:
                lines.append(extra)

        await asyncio.sleep(round(rng.uniform(0.25, 0.85), 2))
        text = "\n".join(lines)
        return LLMReply(
            text=text,
            provider=self.provider,
            tokens_in=len(system) // 2 + sum(len(m.get("content", "")) // 2 for m in messages),
            tokens_out=len(text) // 2,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )


class OpenAICompatLLM(BaseLLM):
    """任何 OpenAI 兼容接口。DeepSeek / Qwen / GLM / Moonshot 都走这里。"""

    provider = "openai"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.cfg.provider.base_url.rstrip("/"),
                timeout=self.cfg.provider.timeout_s,
                headers={
                    "Authorization": f"Bearer {self.cfg.provider.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def chat(
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        model: str | None = None,
        thinking: bool | None = None,
    ) -> LLMReply:
        started = time.perf_counter()
        model_name = model or self.cfg.provider.model
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": self.cfg.provider.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.cfg.provider.max_tokens,
            "stream": False,
        }
        # DeepSeek V4 系列：显式开推理，返回 reasoning_content（她“想”的过程）
        # thinking=False 给结构化/辅助调用用：不推理，输出少、快 5–10 倍
        if "DeepSeek-V4" in model_name:
            payload["enable_thinking"] = True if thinking is None else bool(thinking)
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                r = await self.client.post("/chat/completions", json=payload)
                r.raise_for_status()
                data = r.json()
                message = (data.get("choices") or [{}])[0].get("message", {}) or {}
                text = message.get("content", "") or ""
                reasoning = (message.get("reasoning_content") or message.get("reasoning") or "").strip()
                usage = data.get("usage") or {}
                details = usage.get("completion_tokens_details") or {}
                return LLMReply(
                    text=text.strip(),
                    provider=self.provider,
                    tokens_in=int(usage.get("prompt_tokens", 0)),
                    tokens_out=int(usage.get("completion_tokens", 0)),
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    reasoning=reasoning,
                    reasoning_tokens=int(details.get("reasoning_tokens", 0) or 0),
                )
            except Exception as e:  # noqa: BLE001 - 网络层什么都可能抛
                last_err = e
                if attempt == 0:
                    await asyncio.sleep(0.8)
        msg = str(last_err) or type(last_err).__name__ if last_err else "未知错误"
        raise RuntimeError(f"模型调用失败：{msg}")

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def build(cfg: Config, persona: Persona) -> BaseLLM:
    if cfg.provider.is_mock:
        return MockLLM(persona)
    return OpenAICompatLLM(cfg)
