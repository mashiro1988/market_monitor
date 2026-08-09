# -*- coding: utf-8 -*-
"""BlockBeats Pro API 采集器:北京时间转 UTC、HTML 去标签、分页、失败上抛。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import pytest

from scanners.sources.blockbeats_source import BlockBeatsSource


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


ITEM = {
    "id": 360591,
    "title": "pump.fun 挖角竞对 KOL",
    "content": "<p>BlockBeats 消息，<strong>据多位 KOL</strong> 爆料……</p>",
    "pic": "https://img/x.png",
    "link": "https://m.theblockbeats.info/flash/360591",
    "url": "",
    "create_time": "2026-08-09 13:30:17",
}


def test_beijing_time_converted_to_utc_naive(monkeypatch):
    src = BlockBeatsSource(api_key="k")
    monkeypatch.setattr(src, "_get_page", lambda page: [ITEM] if page == 1 else [])
    rec = src.fetch()[0]
    assert rec.published_at == datetime(2026, 8, 9, 5, 30, 17)   # 13:30:17 北京 = 05:30:17 UTC
    assert rec.published_at.tzinfo is None


def test_html_stripped_and_fields_mapped(monkeypatch):
    src = BlockBeatsSource(api_key="k")
    monkeypatch.setattr(src, "_get_page", lambda page: [ITEM] if page == 1 else [])
    rec = src.fetch()[0]
    assert rec.source == "blockbeats"
    assert rec.source_id == "360591"
    assert rec.market == "crypto"
    assert rec.language == "zh"
    assert "<p>" not in rec.content and "<strong>" not in rec.content
    assert "据多位 KOL 爆料" in rec.content
    assert rec.url == ITEM["link"]


def test_pagination_stops_on_empty_page(monkeypatch):
    src = BlockBeatsSource(api_key="k", max_pages=3)
    calls = []

    def fake_get_page(page):
        calls.append(page)
        return [dict(ITEM, id=100 + page)] if page == 1 else []

    monkeypatch.setattr(src, "_get_page", fake_get_page)
    recs = src.fetch()
    assert calls == [1, 2]          # 第 2 页空即止,不白跑第 3 页
    assert len(recs) == 1


def test_untitled_item_skipped(monkeypatch):
    src = BlockBeatsSource(api_key="k")
    monkeypatch.setattr(src, "_get_page",
                        lambda page: [dict(ITEM, title="  ")] if page == 1 else [])
    assert src.fetch() == []


def test_missing_key_raises():
    src = BlockBeatsSource(api_key="")
    with pytest.raises(RuntimeError, match="BLOCKBEATS_API_KEY"):
        src.fetch()


def test_api_error_status_raises(monkeypatch):
    """接口层错误必须上抛:老接口那种"成功+空数组"的软失败已经坑过一次。"""
    src = BlockBeatsSource(api_key="k")
    monkeypatch.setattr("scanners.sources.blockbeats_source.requests.get",
                        lambda *a, **kw: FakeResp(
                            {"status": 100, "message": "Missing API key", "data": None}))
    with pytest.raises(RuntimeError, match="Missing API key"):
        src.fetch()


def test_http_error_raises(monkeypatch):
    src = BlockBeatsSource(api_key="k")
    monkeypatch.setattr("scanners.sources.blockbeats_source.requests.get",
                        lambda *a, **kw: FakeResp({}, status=502))
    with pytest.raises(RuntimeError, match="502"):
        src.fetch()
