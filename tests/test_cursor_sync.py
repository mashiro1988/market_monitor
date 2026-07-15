# -*- coding: utf-8 -*-
"""游标同步（2026-07-14 重构）：窗口公式、幂等写入返回、scan 单路径。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.price import PriceSnapshot
from scanners.price_scanner import sync_window_start, _latest_by_symbol

NOW = datetime(2026, 7, 14, 12, 0, 0)


@pytest.fixture()
def make_session():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)


# ---------- 窗口公式三态 + 种子 ----------

def test_normal_floor_is_24h():
    latest = {"A": NOW - timedelta(minutes=5), "B": NOW - timedelta(minutes=10)}
    assert sync_window_start(latest, NOW, cap_hours=168) == NOW - timedelta(hours=24)


def test_downtime_stretches_to_cursor_minus_30min():
    latest = {"A": NOW - timedelta(hours=70), "B": NOW - timedelta(hours=69)}
    assert sync_window_start(latest, NOW, cap_hours=168) == NOW - timedelta(hours=70, minutes=30)


def test_cap_bounds_the_window():
    latest = {"A": NOW - timedelta(days=10)}
    assert sync_window_start(latest, NOW, cap_hours=72) == NOW - timedelta(hours=72)


def test_empty_symbol_seeds_full_cap():
    latest = {"A": NOW - timedelta(minutes=5), "B": None}
    assert sync_window_start(latest, NOW, cap_hours=72) == NOW - timedelta(hours=72)


# ---------- 幂等写入返回 ----------

from scanners.base import PriceRecord
import scanners.price_scanner as ps_module
from scanners.price_scanner import PriceScanner


def _rec(ts, symbol="NQ=F", price=100.0, source="yfinance"):
    return PriceRecord(asset_class="futures", symbol=symbol, name="纳指期货",
                       price=price, source=source, timestamp=ts)


def test_save_records_returns_only_inserted(make_session, monkeypatch):
    monkeypatch.setattr(ps_module, "get_session", make_session)
    scanner = PriceScanner()
    t1, t2 = NOW - timedelta(minutes=10), NOW - timedelta(minutes=5)
    first = scanner._save_records([_rec(t1)], NOW)
    assert [r.timestamp for r in first] == [t1]
    second = scanner._save_records([_rec(t1), _rec(t2)], NOW)   # t1 已存在
    assert [r.timestamp for r in second] == [t2]
    third = scanner._save_records([_rec(t1), _rec(t2)], NOW)    # 全部已存在 → 幂等
    assert third == []


# ---------- scan 单路径 ----------

import config


class FakeHistorySource:
    """可编程的 fetch_history 源：记录被调用的窗口，按轮次返回预设记录。"""
    def __init__(self, name, rounds):
        self.name = name
        self.rounds = list(rounds)      # 每轮返回的 list[PriceRecord]
        self.calls = []                 # [(start, end)]

    def fetch_history(self, start_ts, end_ts):
        self.calls.append((start_ts, end_ts))
        return self.rounds.pop(0) if self.rounds else []


class FakeQuoteSource:
    name = "cnbc_bond_quote"
    def fetch(self):
        return []


class NoopGapFiller:
    def run(self, session, okx_source, scan_time):
        return 0


def _make_scanner(make_session, monkeypatch, yf_rounds, okx_rounds):
    monkeypatch.setattr(ps_module, "get_session", make_session)
    scanner = PriceScanner()
    yf = FakeHistorySource("yfinance", yf_rounds)
    yf._all_tickers = lambda: {"NQ=F": ("futures", "纳指期货")}
    yf.CAP_HOURS = 168
    scanner.yfinance = yf
    scanner.okx = FakeHistorySource("okx", okx_rounds)
    scanner.cnbc_bonds = FakeQuoteSource()
    scanner.gap_filler = NoopGapFiller()
    monkeypatch.setattr(config, "PRICE_SOURCES",
                        {**config.PRICE_SOURCES, "crypto": {"BTC": "BTCUSDT"}})
    return scanner


def test_scan_empty_db_seeds_cap_window(make_session, monkeypatch):
    scanner = _make_scanner(make_session, monkeypatch, [[]], [[]])
    scanner.scan()
    (yf_start, yf_end), = scanner.yfinance.calls
    (okx_start, okx_end), = scanner.okx.calls
    assert (yf_end - yf_start) == timedelta(hours=168)      # 库空 → 种子拉满 CAP
    assert (okx_end - okx_start) == timedelta(hours=int(config.PRICE_BACKFILL_MAX_HOURS))


def test_scan_normal_uses_24h_floor_and_returns_inserted(make_session, monkeypatch):
    t_old = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(minutes=10)
    t_new = t_old + timedelta(minutes=5)
    scanner = _make_scanner(
        make_session, monkeypatch,
        yf_rounds=[[_rec(t_old)], [_rec(t_old), _rec(t_new)]],
        okx_rounds=[[], []],
    )
    first = scanner.scan()
    assert [r.timestamp for r in first] == [t_old]
    second = scanner.scan()                                  # t_old 已在库 → 只插 t_new
    assert [r.timestamp for r in second] == [t_new]
    yf_start2, yf_end2 = scanner.yfinance.calls[1]
    assert (yf_end2 - yf_start2) == timedelta(hours=24)      # 游标新鲜 → 24h 地板


def test_scan_heals_mid_window_hole(make_session, monkeypatch):
    base = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(hours=1)
    t1, t2, t3 = base, base + timedelta(minutes=5), base + timedelta(minutes=10)
    scanner = _make_scanner(
        make_session, monkeypatch,
        yf_rounds=[[_rec(t1), _rec(t3)], [_rec(t1), _rec(t2), _rec(t3)]],   # 第一轮源端缺 t2
        okx_rounds=[[], []],
    )
    scanner.scan()
    healed = scanner.scan()                                  # 第二轮源补全 → 洞被填
    assert [r.timestamp for r in healed] == [t2]


# ---------- 打标挂尾（原每小时 job 收编） ----------

from services import scan_runtime


def test_tag_new_news_skips_without_key(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "", raising=False)
    called = []
    monkeypatch.setattr("services.news_tagging.tag_untagged",
                        lambda session, limit: called.append(limit) or 0)
    scan_runtime._tag_new_news()
    assert called == []                          # 无 key 静默跳过


def test_tag_new_news_invokes_tagger_with_limit(monkeypatch, make_session):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr("services.scan_runtime.get_session", make_session, raising=False)
    called = []
    monkeypatch.setattr("services.news_tagging.tag_untagged",
                        lambda session, limit: called.append(limit) or 3)
    scan_runtime._tag_new_news()
    assert called == [200]


def test_tag_new_news_error_does_not_raise(monkeypatch, make_session):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr("services.scan_runtime.get_session", make_session, raising=False)
    def boom(session, limit):
        raise RuntimeError("api down")
    monkeypatch.setattr("services.news_tagging.tag_untagged", boom)
    scan_runtime._tag_new_news()                 # 不应抛出


# ---------- 游标查询 ----------

def test_latest_by_symbol_reads_max_ts_and_none_for_missing(make_session):
    s = make_session()
    for m in (10, 5):
        s.add(PriceSnapshot(timestamp=NOW - timedelta(minutes=m), asset_class="crypto",
                            symbol="BTC/USDT", name="BTC", price=100.0, source="okx_swap_5m"))
    s.commit()
    latest = _latest_by_symbol(s, ["BTC/USDT", "ETH/USDT"])
    assert latest["BTC/USDT"] == NOW - timedelta(minutes=5)
    assert latest["ETH/USDT"] is None
    s.close()
