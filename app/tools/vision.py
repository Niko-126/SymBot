"""识图工具：把用户发来的图交给一个多模态模型换成文字。

走 OpenAI 兼容的 `/chat/completions`（`image_url` 那种多模态 content），
所以只要你的中转/官方端点支持视觉模型，换 `tools.vision.model` 就行。

支持三种图片来源：
- `http(s)://…`      直接用
- `data:image/...`   直接用
- 本地路径 / `file://` 读文件转 base64（单图上限 4 MB）

失败一律返回 `{"ok": False, "reason": …}`：主链当"没看清"处理，不许抛。
﻿​​‍​​​‍‌﻿‌‍﻿﻿​​‌​​﻿﻿​‌‌​​​‌‌‌﻿‍​﻿"""

from __future__ import annotations

import base64
import pathlib
import urllib.parse
from typing import Any

_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}
_MAX_IMAGE_BYTES = 4_000_000
_DEFAULT_ASK = "客观描述这张图：谁、在哪、在做什么、图里有什么文字。不要评价，不要编没看见的细节。"


def _to_data_uri(src: str) -> str:
    """图片来源归一化：URL / data URI 原样返回，本地文件转成 data URI。"""
    s = (src or "").strip()
    if not s:
        raise ValueError("empty-image")
    if s.startswith("data:"):
        return s
    if s.startswith(("http://", "https://")):
        return s
    if s.startswith("file://"):
        parsed = urllib.parse.urlparse(s)
        s = urllib.parse.unquote(parsed.path)
        if s.startswith("/") and len(s) > 2 and s[2] == ":":  # Windows 的 /C:/…
            s = s[1:]
    path = pathlib.Path(s)
    if not path.is_file():
        raise ValueError("image-not-found")
    if path.stat().st_size > _MAX_IMAGE_BYTES:
        raise ValueError("image-too-large")
    mime = _MIME.get(path.suffix.lower(), "image/png")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def describe(images: list[str], *, question: str = "", cfg: Any = None) -> dict[str, Any]:
    """看 1..N 张图，返回一段文字描述。"""
    if cfg is None:
        from app.core import config as config_mod

        cfg = config_mod.get()

    box = getattr(getattr(cfg, "tools", None), "vision", None)
    max_images = max(1, int(getattr(box, "max_images", 2) or 2))
    picked = [str(x) for x in (images or []) if str(x).strip()][:max_images]
    if not picked:
        return {"ok": False, "name": "image_describe", "reason": "no-image"}

    api_key = str(getattr(box, "api_key", "") or "") or str(getattr(cfg.provider, "api_key", "") or "")
    if not api_key:
        return {"ok": False, "name": "image_describe", "reason": "no-key"}

    uris: list[str] = []
    for src in picked:
        try:
            uris.append(_to_data_uri(src))
        except ValueError as exc:
            return {"ok": False, "name": "image_describe", "reason": str(exc)[:60]}
    if not uris:
        return {"ok": False, "name": "image_describe", "reason": "no-image"}

    model = str(getattr(box, "model", "") or "") or str(cfg.provider.model or "")
    base = str(getattr(cfg.provider, "base_url", "") or "").rstrip("/")
    if not base or not model:
        return {"ok": False, "name": "image_describe", "reason": "no-endpoint"}

    ask = (question or "").strip()[:200] or _DEFAULT_ASK
    content: list[dict[str, Any]] = [{"type": "text", "text": ask}]
    content += [{"type": "image_url", "image_url": {"url": u}} for u in uris]

    payload = {
        "model": model,
        "max_tokens": max(64, int(getattr(box, "max_tokens", 400) or 400)),
        "temperature": 0.2,
        "messages": [{"role": "user", "content": content}],
    }
    timeout = float(getattr(box, "timeout_s", 20.0) or 20.0)

    import httpx

    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - 网络问题当"没看清"
        return {"ok": False, "name": "image_describe", "reason": f"{type(exc).__name__}: {exc}"[:120]}

    if not resp.is_success:
        return {"ok": False, "name": "image_describe", "reason": f"http-{resp.status_code}",
                "detail": (resp.text or "")[:160]}
    try:
        data = resp.json()
        text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    except (ValueError, AttributeError, IndexError, TypeError):
        return {"ok": False, "name": "image_describe", "reason": "bad-response"}
    if not text:
        return {"ok": False, "name": "image_describe", "reason": "empty-text"}
    return {"ok": True, "name": "image_describe", "model": model, "text": text[:600], "images": len(uris)}


__all__ = ["describe"]
