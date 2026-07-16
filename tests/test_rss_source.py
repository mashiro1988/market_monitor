"""RSS 源：新英文快讯源注册 + Cloudflare 429 退避重试。"""
import sys
import os
from unittest.mock import MagicMock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from scanners.sources import rss_source
from scanners.sources.rss_source import RSSSource, create_rss_sources


def test_english_newswires_registered():
    keys = {s.source_key for s in create_rss_sources()}
    assert "investinglive" in keys
    assert "financialjuice" in keys


class _Resp:
    def __init__(self, status_code, content):
        self.status_code = status_code
        self.content = content


def test_rss_retries_once_on_429(monkeypatch):
    calls = {"n": 0}
    good = b"<rss><channel><item><title>Fed hikes</title><guid>9625942</guid></item></channel></rss>"

    def fake_get(url, **kwargs):
        calls["n"] += 1
        return _Resp(429, b"blocked") if calls["n"] == 1 else _Resp(200, good)

    monkeypatch.setattr(rss_source.requests, "get", fake_get)
    monkeypatch.setattr(rss_source.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(config, "proxies", lambda: {})

    records = RSSSource("financialjuice", "http://x/feed", "FinancialJuice", "en").fetch()
    assert any(r.title == "Fed hikes" for r in records)
    assert calls["n"] == 2


def test_rss_empty_guid_uses_title_and_time_fingerprint(monkeypatch):
    feed = b"""
    <rss><channel>
      <item><title>Fed hikes</title><guid></guid><pubDate>Mon, 06 Jul 2026 10:00:00 GMT</pubDate></item>
      <item><title>Fed hikes</title><guid></guid><pubDate>Mon, 06 Jul 2026 10:05:00 GMT</pubDate></item>
    </channel></rss>
    """

    monkeypatch.setattr(rss_source.requests, "get", lambda *a, **k: _Resp(200, feed))
    monkeypatch.setattr(config, "proxies", lambda: {})

    records = RSSSource("financialjuice", "http://x/feed", "FinancialJuice", "en").fetch()
    assert len(records) == 2
    assert len({record.source_id for record in records}) == 2
    assert any(r.title == "Fed hikes" for r in records)


def test_rss_logs_skipped_entry_count(monkeypatch):
    feed = b"""
    <rss><channel>
      <item><guid>missing-title</guid></item>
      <item><title>Fed hikes</title><guid>good</guid></item>
    </channel></rss>
    """
    fake_logger = MagicMock()
    monkeypatch.setattr(rss_source.requests, "get", lambda *a, **k: _Resp(200, feed))
    monkeypatch.setattr(rss_source, "logger", fake_logger)
    monkeypatch.setattr(config, "proxies", lambda: {})

    records = RSSSource("financialjuice", "http://x/feed", "FinancialJuice", "en").fetch()

    assert [record.title for record in records] == ["Fed hikes"]
    assert fake_logger.debug.called
    assert "跳过 1 条" in fake_logger.info.call_args.args[0]
