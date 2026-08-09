# -*- coding: utf-8 -*-
"""币安公告采集器:毫秒时间戳转 UTC、多目录轮询、标题带目录名。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import pytest

from scanners.sources.binance_ann_source import BinanceAnnouncementSource

ARTICLE = {
    "id": 281779,
    "code": "307687ad279e42e6909ee1be8c472b50",
    "title": "Binance Futures Will Launch Multiple USD-Margined Perpetual Contracts",
    "type": 1,
    "releaseDate": 1785983412862,
}


def test_release_date_ms_to_utc_naive(monkeypatch):
    src = BinanceAnnouncementSource()
    monkeypatch.setattr(src, "_get_catalog", lambda cid: [ARTICLE] if cid == 48 else [])
    rec = src.fetch()[0]
    assert rec.published_at == datetime.utcfromtimestamp(1785983412862 / 1000)
    assert rec.published_at.tzinfo is None


def test_fields_mapped(monkeypatch):
    src = BinanceAnnouncementSource()
    monkeypatch.setattr(src, "_get_catalog", lambda cid: [ARTICLE] if cid == 48 else [])
    rec = src.fetch()[0]
    assert rec.source == "binance_ann"
    assert rec.source_id == "281779"
    assert rec.market == "crypto"
    assert rec.url.endswith(ARTICLE["code"])
    assert "新币上线" in rec.title            # 目录名前缀,人与模型都能一眼认出公告类型


def test_all_catalogs_polled(monkeypatch):
    src = BinanceAnnouncementSource()
    seen = []
    monkeypatch.setattr(src, "_get_catalog", lambda cid: seen.append(cid) or [])
    src.fetch()
    assert seen == [48, 161]


def test_untitled_article_skipped(monkeypatch):
    src = BinanceAnnouncementSource()
    monkeypatch.setattr(src, "_get_catalog",
                        lambda cid: [dict(ARTICLE, title="")] if cid == 48 else [])
    assert src.fetch() == []


def test_api_error_code_raises(monkeypatch):
    src = BinanceAnnouncementSource()

    class FakeResp:
        status_code = 200
        text = "boom"

        def json(self):
            return {"code": "500001", "message": "boom", "data": None}

    monkeypatch.setattr("scanners.sources.binance_ann_source.requests.get",
                        lambda *a, **kw: FakeResp())
    with pytest.raises(RuntimeError, match="boom"):
        src.fetch()
