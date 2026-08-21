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
    # investinglive 2026-08-06 下线(未评分重灾+信息价值低,enabled=False 保留配置可重开)
    assert "investinglive" not in keys
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


ATOM_FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>\xe5\x90\xb4\xe8\xaf\xb4</title>
  <entry>
    <title>\xe6\x9f\x90\xe5\xb7\xa8\xe9\xb2\xb8\xe5\xa2\x9e\xe6\x8c\x81 BTC</title>
    <id>tag:wu,2026:1</id>
    <link href="https://www.wublock123.com/p/1"/>
    <updated>2026-08-20T08:34:21.000Z</updated>
    <summary>\xe9\x93\xbe\xe4\xb8\x8a\xe7\x9b\x91\xe6\xb5\x8b\xe6\x98\xbe\xe7\xa4\xba\xe5\xb7\xa8\xe9\xb2\xb8\xe4\xb9\xb0\xe5\x85\xa5</summary>
  </entry>
</feed>"""


def test_rss_market_defaults_to_macro(monkeypatch):
    """不传 market 的现有宏观调用方,行为一个字节不变。"""
    feed = b"<rss><channel><item><title>Fed hikes</title><guid>1</guid></item></channel></rss>"
    monkeypatch.setattr(rss_source.requests, "get", lambda *a, **k: _Resp(200, feed))
    monkeypatch.setattr(config, "proxies", lambda: {})
    records = RSSSource("financialjuice", "http://x/feed", "FinancialJuice", "en").fetch()
    assert records[0].market == "macro"


def test_rss_crypto_market_and_atom_parse(monkeypatch):
    """吴说是 Atom 格式:market 透传 + Atom 的题/摘要/时间解析一次验掉。"""
    monkeypatch.setattr(rss_source.requests, "get", lambda *a, **k: _Resp(200, ATOM_FEED))
    monkeypatch.setattr(config, "proxies", lambda: {})
    records = RSSSource("wublock", "http://x/feed", "吴说区块链", "zh",
                        market="crypto").fetch()
    assert len(records) == 1
    record = records[0]
    assert record.market == "crypto"
    assert record.title == "某巨鲸增持 BTC"
    assert record.published_at is not None
    assert "链上监测" in (record.content or "")
