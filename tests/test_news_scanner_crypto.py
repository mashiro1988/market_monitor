# -*- coding: utf-8 -*-
"""加密新闻不进宏观 scorer(口径不同会打歪),且 market 必须落库。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
import scanners.news_scanner as ns
from database import Base
from models.news import NewsItem
from scanners.base import NewsRecord
from scanners.news_scanner import NewsScanner


def test_only_macro_records_go_to_scorer():
    scanner = NewsScanner.__new__(NewsScanner)      # 不跑 __init__,免起真源
    macro = NewsRecord(source="jin10", source_id="1", title="宏观", market="macro")
    crypto = NewsRecord(source="blockbeats", source_id="2", title="币圈", market="crypto")
    assert [r.title for r in scanner._macro_only([macro, crypto])] == ["宏观"]


def test_save_records_persists_market(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ns, "get_session", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)

    scanner = NewsScanner.__new__(NewsScanner)
    scanner._save_records(
        [NewsRecord(source="blockbeats", source_id="9", title="币圈", market="crypto",
                    published_at=datetime(2026, 8, 9, 5, 30)),
         NewsRecord(source="jin10", source_id="8", title="宏观",
                    published_at=datetime(2026, 8, 9, 5, 31))],
        datetime(2026, 8, 9, 6, 0))

    rows = {r.title: r.market for r in session.query(NewsItem).all()}
    assert rows == {"币圈": "crypto", "宏观": "macro"}


def test_scanner_registers_crypto_rss_and_skips_disabled_blockbeats(monkeypatch):
    """扫描器注册段:rss 型加密源自动挂上;BlockBeats 停用后有 key 也不注册。"""
    monkeypatch.setattr(config, "NEWS_SOURCES", {"jin10": {"enabled": False}}, raising=False)
    monkeypatch.setattr(config, "CRYPTO_NEWS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "BLOCKBEATS_API_KEY", "still-have-key", raising=False)
    monkeypatch.setattr(config, "CRYPTO_NEWS_SOURCES", {
        "blockbeats": {"enabled": False},
        "panews": {"enabled": True, "type": "rss", "url": "http://x/rss",
                   "name": "PANews", "language": "zh"},
    }, raising=False)

    scanner = NewsScanner()
    keys = {getattr(s, "source_key", None) for s in scanner.sources}
    assert "panews" in keys
    assert all(type(s).__name__ != "BlockBeatsSource" for s in scanner.sources)
