from __future__ import annotations

import config
from services.alerts_service import get_webhook_status


def test_webhook_preview_drops_query_and_fragment(monkeypatch):
    monkeypatch.setattr(
        config,
        "WECHAT_WORK_WEBHOOK",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=super-secret#fragment",
    )

    status = get_webhook_status()

    assert status.configured is True
    assert status.preview == "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
    assert "super-secret" not in status.preview


def test_webhook_preview_is_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "WECHAT_WORK_WEBHOOK", "")

    status = get_webhook_status()

    assert status.configured is False
    assert status.preview is None
