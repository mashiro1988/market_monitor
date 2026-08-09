# -*- coding: utf-8 -*-
"""加密新闻不进宏观 scorer(口径不同会打歪),且 market 必须落库。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
