# -*- coding: utf-8 -*-
"""缺口自愈：检测（内部缺口、忽略头尾）→ 回补 → 按结果分类（补回/真实仍缺/休市静默）→ 推送账目。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.price import PriceSnapshot
from scanners.base import PriceRecord
from services import gap_repair

NOW = datetime(2026, 6, 11, 12, 0)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _add(s, symbol, ts, price=100.0, asset_class="futures"):
    s.add(PriceSnapshot(timestamp=ts, asset_class=asset_class, symbol=symbol,
                        name=symbol, price=price, source="t"))


def _series_with_hole(s, symbol, hole: set[datetime]):
    """NOW-3h ~ NOW-20min 的 5m 序列，挖掉 hole 里的时间点。"""
    t = NOW - timedelta(hours=3)
    while t <= NOW - timedelta(minutes=20):
        if t not in hole:
            _add(s, symbol, t)
        t += timedelta(minutes=5)


def test_find_gaps_detects_internal_holes_only(session):
    hole = {NOW - timedelta(minutes=90), NOW - timedelta(minutes=85)}   # 连续两根
    _series_with_hole(session, "NQ=F", hole)
    session.commit()
    gaps = gap_repair.find_gaps(session, ["NQ=F", "CL=F"], hours=24, now=NOW)
    assert list(gaps) == ["NQ=F"]                       # CL=F 无数据 → 无"内部"缺口
    (start, end), = gaps["NQ=F"]
    assert end - start == timedelta(minutes=15)         # 缺两根 → 间隔 15 分钟
    assert gap_repair._missing_bars((start, end)) == 2


class _FakeScanner:
    """回补假源：返回 source_bars，并把 insert_bars 真插库。"""
    def __init__(self, session, source_bars, insert_bars):
        self.session, self.source_bars, self.insert_bars = session, source_bars, insert_bars
        self.called_with = None

    def backfill_range(self, start, end):
        self.called_with = (start, end)
        for sym, ts in self.insert_bars:
            _add(self.session, sym, ts)
        self.session.commit()
        return [PriceRecord(asset_class="futures", symbol=sym, name=sym, price=100.0,
                            source="t", timestamp=ts) for sym, ts in self.source_bars]


class _FakeChannel:
    def __init__(self):
        self.sent = []

    def send(self, title, content):
        self.sent.append((title, content))
        return True


def test_repair_fills_and_reports(session, monkeypatch):
    monkeypatch.setattr(gap_repair, "repair_symbols", lambda: {"NQ=F": "futures"})
    hole = {NOW - timedelta(minutes=90), NOW - timedelta(minutes=85)}
    _series_with_hole(session, "NQ=F", hole)
    session.commit()

    bars = [("NQ=F", t) for t in hole]
    scanner = _FakeScanner(session, source_bars=bars, insert_bars=bars)
    channel = _FakeChannel()
    summary = gap_repair.run_gap_repair(session=session, hours=24, now=NOW, scanner=scanner, channel=channel)

    assert summary["bars_missing"] == 2 and summary["bars_repaired"] == 2
    assert summary["still_missing"] == []
    assert len(channel.sent) == 1
    assert "补回 **2** 根" in channel.sent[0][1]
    assert "已全部补全" in channel.sent[0][1]


def test_closed_market_gap_is_silent(session, monkeypatch):
    """源端对缺口时段没有任何 bar（休市）→ 不算仍缺、不推送。"""
    monkeypatch.setattr(gap_repair, "repair_symbols", lambda: {"NQ=F": "futures"})
    hole = {NOW - timedelta(minutes=90), NOW - timedelta(minutes=85)}
    _series_with_hole(session, "NQ=F", hole)
    session.commit()

    scanner = _FakeScanner(session, source_bars=[], insert_bars=[])   # 源端无数据
    channel = _FakeChannel()
    summary = gap_repair.run_gap_repair(session=session, hours=24, now=NOW, scanner=scanner, channel=channel)

    assert summary["bars_repaired"] == 0
    assert summary["still_missing"] == []
    assert summary["closed_ignored"] == 1
    assert channel.sent == []                            # 静默


def test_fetch_failure_reports_still_missing(session, monkeypatch):
    monkeypatch.setattr(gap_repair, "repair_symbols", lambda: {"NQ=F": "futures"})
    hole = {NOW - timedelta(minutes=90)}
    _series_with_hole(session, "NQ=F", hole)
    session.commit()

    class _BoomScanner:
        def backfill_range(self, start, end):
            raise RuntimeError("YFRateLimitError: Too Many Requests")

    channel = _FakeChannel()
    summary = gap_repair.run_gap_repair(session=session, hours=24, now=NOW, scanner=_BoomScanner(), channel=channel)

    assert summary["fetch_error"] is not None
    assert len(summary["still_missing"]) == 1
    assert "拉取失败" in summary["still_missing"][0]["reason"]
    assert len(channel.sent) == 1
    assert "仍缺" in channel.sent[0][1]


def test_no_gaps_is_fully_silent(session, monkeypatch):
    monkeypatch.setattr(gap_repair, "repair_symbols", lambda: {"NQ=F": "futures"})
    _series_with_hole(session, "NQ=F", set())
    session.commit()
    channel = _FakeChannel()
    summary = gap_repair.run_gap_repair(session=session, hours=24, now=NOW,
                                        scanner=_FakeScanner(session, [], []), channel=channel)
    assert summary["gaps_found"] == 0
    assert channel.sent == []
