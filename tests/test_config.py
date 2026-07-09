"""Configuration helper tests."""

import config


def test_proxy_url_without_port_uses_checked_default_port():
    assert config._normalize_proxy_url("http://127.0.0.1") == "http://127.0.0.1:1080"


def test_proxy_url_with_port_is_kept():
    assert config._normalize_proxy_url("http://127.0.0.1:7897") == "http://127.0.0.1:7897"


def test_proxy_helpers_share_detected_proxy(monkeypatch):
    monkeypatch.setattr(config, "PROXY", "http://127.0.0.1:7897")
    assert config.proxy_url() == "http://127.0.0.1:7897"
    assert config.proxies() == {
        "http": "http://127.0.0.1:7897",
        "https": "http://127.0.0.1:7897",
    }
