from __future__ import annotations

import time
from typing import Any, NamedTuple

import requests

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"


class DeepSeekChatResult(NamedTuple):
    content: str
    reasoning_content: str
    duration_seconds: float


def call_deepseek_chat(
    payload: dict[str, Any],
    *,
    api_key: str,
    timeout: tuple[float, float],
    http_error_prefix: str = "DeepSeek 返回",
    error_preview_chars: int = 300,
    normalize_error_newlines: bool = True,
) -> DeepSeekChatResult:
    """Send one DeepSeek chat-completion request and extract its message fields."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    started = time.monotonic()
    response = requests.post(
        DEEPSEEK_API_URL,
        json=payload,
        headers=headers,
        timeout=timeout,
    )
    duration = time.monotonic() - started

    if response.status_code >= 400:
        preview = response.text[:error_preview_chars]
        if normalize_error_newlines:
            preview = preview.replace("\n", " ")
        raise RuntimeError(f"{http_error_prefix} {response.status_code}: {preview}")

    body = response.json()
    message = body["choices"][0].get("message", {})
    return DeepSeekChatResult(
        content=(message.get("content") or "").strip(),
        reasoning_content=(message.get("reasoning_content") or "").strip(),
        duration_seconds=duration,
    )
