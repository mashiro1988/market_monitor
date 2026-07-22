# -*- coding: utf-8 -*-
"""yfinance 串行拉取（2026-07-22 治本）：会话过滤、逐品种、软预算截断、失败隔离。

继承自已废除的 test_yfinance_single_batch.py 的防回归点：
- 单 ticker 列表输入返回 MultiIndex 列（df["Close"] 是 DataFrame）必须被正确解析
  （串行后每次调用都是单品种列表，这成为唯一形态）；
- 单品种坏数据（全 NaN）只跳过该品种，不影响其余品种。
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import scanners.sources.yfinance_source as yfs_module
from scanners.sources.yfinance_source import YFinancePriceSource

START = datetime(2026, 7, 22, 0, 0)
END = datetime(2026, 7, 22, 6, 0)   # 周三 06:00 UTC：亚洲收尾+期货在场


def _fake_df(symbol: str, prices: tuple[float, ...] = (100.0, 101.0)) -> pd.DataFrame:
    """已收盘 5m K 线（UTC tz-aware index），列为 MultiIndex (字段, symbol)——
    yfinance ≥0.2.51 对列表输入的恒定形态。"""
    base = pd.Timestamp("2026-07-22 04:00", tz="UTC")
    idx = pd.DatetimeIndex([base + pd.Timedelta(minutes=5 * i) for i in range(len(prices))])
    cols = pd.MultiIndex.from_product([["Close"], [symbol]])
    return pd.DataFrame([[p] for p in prices], index=idx, columns=cols)


@pytest.fixture()
def src(monkeypatch):
    monkeypatch.setattr(yfs_module, "_sleep", lambda s: None)   # 测试不真睡
    return YFinancePriceSource()


def test_only_active_symbols_requested_serially(src, monkeypatch):
    calls: list[str] = []

    def fake_download(tickers, **kwargs):
        assert isinstance(tickers, list) and len(tickers) == 1   # 恒为单品种列表
        calls.append(tickers[0])
        return _fake_df(tickers[0])

    monkeypatch.setattr(yfs_module.yf, "download", fake_download)
    monkeypatch.setattr(yfs_module.market_sessions, "should_fetch",
                        lambda sym, now: sym in {"ES=F", "GC=F"})
    records = src.fetch_history(START, END)
    assert sorted(calls) == ["ES=F", "GC=F"]
    assert {r.symbol for r in records} == {"ES=F", "GC=F"}
    # 单 ticker MultiIndex 列被正确解析出价格（继承旧回归点）
    es = [r for r in records if r.symbol == "ES=F"]
    assert es[-1].price == 101.0 and es[-1].asset_class == "futures"


def test_all_closed_no_http(src, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("closed round must not hit network")

    monkeypatch.setattr(yfs_module.yf, "download", boom)
    monkeypatch.setattr(yfs_module.market_sessions, "should_fetch", lambda s, n: False)
    assert src.fetch_history(START, END) == []


def test_one_symbol_exception_isolated(src, monkeypatch):
    def fake_download(tickers, **kwargs):
        if tickers[0] == "ES=F":
            raise RuntimeError("boom")
        return _fake_df(tickers[0])

    monkeypatch.setattr(yfs_module.yf, "download", fake_download)
    monkeypatch.setattr(yfs_module.market_sessions, "should_fetch",
                        lambda sym, now: sym in {"ES=F", "GC=F"})
    records = src.fetch_history(START, END)
    assert {r.symbol for r in records} == {"GC=F"}


def test_nan_close_symbol_skipped_not_batch(src, monkeypatch):
    """单品种全 NaN 只跳过该品种（继承旧回归点，串行形态下按品种隔离）。"""
    def fake_download(tickers, **kwargs):
        df = _fake_df(tickers[0])
        if tickers[0] == "DX-Y.NYB":
            df[("Close", "DX-Y.NYB")] = float("nan")
        return df

    monkeypatch.setattr(yfs_module.yf, "download", fake_download)
    monkeypatch.setattr(yfs_module.market_sessions, "should_fetch",
                        lambda sym, now: sym in {"DX-Y.NYB", "NQ=F"})
    records = src.fetch_history(START, END)
    symbols = {r.symbol for r in records}
    assert "DX-Y.NYB" not in symbols and "NQ=F" in symbols


def test_stage_budget_cuts_remaining(src, monkeypatch):
    fake_now = [0.0]

    def fake_monotonic():
        return fake_now[0]

    def fake_download(tickers, **kwargs):
        fake_now[0] += 200.0                                    # 每次下载耗 200s
        return _fake_df(tickers[0])

    monkeypatch.setattr(yfs_module, "_monotonic", fake_monotonic)
    monkeypatch.setattr(yfs_module.yf, "download", fake_download)
    monkeypatch.setattr(yfs_module.market_sessions, "should_fetch",
                        lambda sym, now: sym in {"ES=F", "GC=F", "CL=F"})
    records = src.fetch_history(START, END)
    # 第 1 个下载后已超 180s 预算 → 只完成 1 个品种
    assert len({r.symbol for r in records}) == 1


def test_download_kwargs(src, monkeypatch):
    seen: dict = {}

    def fake_download(tickers, **kwargs):
        seen.update(kwargs)
        return _fake_df(tickers[0])

    monkeypatch.setattr(yfs_module.yf, "download", fake_download)
    monkeypatch.setattr(yfs_module.market_sessions, "should_fetch",
                        lambda sym, now: sym == "ES=F")
    src.fetch_history(START, END)
    assert seen["interval"] == "5m" and seen["threads"] is False
    assert seen["timeout"] == 10 and seen["progress"] is False
    assert seen["start"] == START.replace(tzinfo=timezone.utc)
    assert seen["end"] == END.replace(tzinfo=timezone.utc)
