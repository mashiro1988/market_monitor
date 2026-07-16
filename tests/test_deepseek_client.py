from __future__ import annotations

import pytest

from services import deepseek_client


class _FakeResponse:
    def __init__(self, status_code: int, body: dict, text: str = "") -> None:
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self) -> dict:
        return self._body


def test_call_deepseek_chat_sends_auth_and_extracts_message(monkeypatch):
    captured = {}

    def fake_post(url, *, json, headers, timeout):
        captured.update(url=url, payload=json, headers=headers, timeout=timeout)
        return _FakeResponse(
            200,
            {"choices": [{"message": {"content": " answer ", "reasoning_content": " why "}}]},
        )

    monkeypatch.setattr(deepseek_client.requests, "post", fake_post)
    result = deepseek_client.call_deepseek_chat(
        {"model": "test-model"},
        api_key="secret",
        timeout=(3, 30),
    )

    assert captured == {
        "url": deepseek_client.DEEPSEEK_API_URL,
        "payload": {"model": "test-model"},
        "headers": {"Authorization": "Bearer secret", "Content-Type": "application/json"},
        "timeout": (3, 30),
    }
    assert result.content == "answer"
    assert result.reasoning_content == "why"
    assert result.duration_seconds >= 0


def test_call_deepseek_chat_preserves_caller_error_format(monkeypatch):
    monkeypatch.setattr(
        deepseek_client.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(429, {}, "line one\nline two"),
    )

    with pytest.raises(RuntimeError, match=r"^DeepSeek 打标返回 429: line one\nline two$"):
        deepseek_client.call_deepseek_chat(
            {},
            api_key="secret",
            timeout=(3, 30),
            http_error_prefix="DeepSeek 打标返回",
            error_preview_chars=200,
            normalize_error_newlines=False,
        )
