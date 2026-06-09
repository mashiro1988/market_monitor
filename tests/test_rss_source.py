"""RSS 源：新英文快讯源注册 + Cloudflare 429 退避重试。"""
import sys
import os
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
    assert calls["n"] == 2                        # 429 后退避重试了一次
    assert any(r.title == "Fed hikes" for r in records)
