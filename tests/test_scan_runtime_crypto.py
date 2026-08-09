# -*- coding: utf-8 -*-
"""加密打标/挂接必须挂进 tick,且与宏观同款守卫(无 key/开关关静默跳过,异常自吞)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from services import scan_runtime


class FakeSession:
    def close(self):
        pass


def test_crypto_tag_skipped_when_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(config, "CRYPTO_NEWS_ENABLED", False)
    monkeypatch.setattr("services.crypto_tagging.tag_untagged_crypto",
                        lambda s, **kw: calls.append(1))
    scan_runtime._tag_crypto_news()
    assert calls == []


def test_crypto_tag_skipped_without_api_key(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(config, "CRYPTO_NEWS_ENABLED", True)
    monkeypatch.setattr("services.crypto_tagging.tag_untagged_crypto",
                        lambda s, **kw: calls.append(1))
    scan_runtime._tag_crypto_news()
    assert calls == []


def test_crypto_tag_runs_when_enabled(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(config, "CRYPTO_NEWS_ENABLED", True)
    monkeypatch.setattr(scan_runtime, "get_session", lambda: FakeSession())
    monkeypatch.setattr("services.crypto_tagging.tag_untagged_crypto",
                        lambda s, **kw: calls.append(1) or 1)
    scan_runtime._tag_crypto_news()
    assert calls == [1]


def test_crypto_tag_exception_swallowed(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(config, "CRYPTO_NEWS_ENABLED", True)
    monkeypatch.setattr(scan_runtime, "get_session", lambda: FakeSession())

    def boom(session, **kw):
        raise RuntimeError("接口挂了")

    monkeypatch.setattr("services.crypto_tagging.tag_untagged_crypto", boom)
    scan_runtime._tag_crypto_news()          # 不抛 = 本轮扫描不受影响


def test_link_runs_both_markets(monkeypatch):
    """挂接一轮跑两次:宏观 + 加密,各看各的池子。"""
    markets = []
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(config, "EVENT_LINK_ENABLED", True)
    monkeypatch.setattr(config, "CRYPTO_NEWS_ENABLED", True)
    monkeypatch.setattr(scan_runtime, "get_session", lambda: FakeSession())
    monkeypatch.setattr(
        "services.event_linking.link_unprocessed",
        lambda s, limit=200, market="macro", **kw: markets.append(market) or
        {"processed": 0, "linked": 0, "called": 0})
    scan_runtime._link_new_news()
    assert markets == ["macro", "crypto"]


def test_link_skips_crypto_when_crypto_disabled(monkeypatch):
    markets = []
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(config, "EVENT_LINK_ENABLED", True)
    monkeypatch.setattr(config, "CRYPTO_NEWS_ENABLED", False)
    monkeypatch.setattr(scan_runtime, "get_session", lambda: FakeSession())
    monkeypatch.setattr(
        "services.event_linking.link_unprocessed",
        lambda s, limit=200, market="macro", **kw: markets.append(market) or
        {"processed": 0, "linked": 0, "called": 0})
    scan_runtime._link_new_news()
    assert markets == ["macro"]
