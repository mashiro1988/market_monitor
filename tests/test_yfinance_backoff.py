# -*- coding: utf-8 -*-
"""yfinance 指数退避（2026-07-27）：开市却拿不到数据 → 该品种跳过若干轮，成功即归零。

动机：Yahoo 限流时 yf.download 不抛异常、只返回空 DataFrame（0.2.x 把 YFRateLimitError
打到 stderr）。所以"开市品种返回空"就是限流的可观测信号。因为游标窗口每轮回看 24h，
跳过几轮零数据代价——下一次成功自动补回。
"""
from datetime import datetime, timedelta

import pandas as pd
import pytest

import scanners.sources.yfinance_source as yfs_module
from scanners.sources.yfinance_source import YFinancePriceSource

T0 = datetime(2026, 7, 27, 6, 0)


def _df(symbol: str) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp("2026-07-27 05:00", tz="UTC")])
    cols = pd.MultiIndex.from_product([["Close"], [symbol]])
    return pd.DataFrame([[100.0]], index=idx, columns=cols)


@pytest.fixture()
def src(monkeypatch):
    monkeypatch.setattr(yfs_module, "_sleep", lambda s: None)
    monkeypatch.setattr(yfs_module, "_jitter_ratio", lambda: 0.0)   # 去掉退避抖动，断言确定
    monkeypatch.setattr(yfs_module.market_sessions, "should_fetch", lambda sym, now: sym == "ES=F")
    return YFinancePriceSource()


def _run(src, monkeypatch, now: datetime, *, empty: bool) -> list[str]:
    """跑一轮 fetch_history，返回本轮实际请求过的 symbol 列表。"""
    calls: list[str] = []

    def fake_download(tickers, **kwargs):
        calls.append(tickers[0])
        return pd.DataFrame() if empty else _df(tickers[0])

    monkeypatch.setattr(yfs_module.yf, "download", fake_download)
    src.fetch_history(now - timedelta(hours=24), now)
    return calls


def test_first_failure_skips_next_cycle(src, monkeypatch):
    assert _run(src, monkeypatch, T0, empty=True) == ["ES=F"]          # 第 1 轮：失败
    # 退避 5 分钟：+5min 那轮仍在冷却窗内 → 不请求
    assert _run(src, monkeypatch, T0 + timedelta(minutes=4), empty=True) == []
    # 冷却到期后恢复请求
    assert _run(src, monkeypatch, T0 + timedelta(minutes=6), empty=True) == ["ES=F"]


def test_backoff_doubles_then_caps(src, monkeypatch):
    """连续失败 → 退避 5/10/20/40/60(封顶) 分钟。

    用只读的 _in_backoff 探冷却到期时刻——拿 fetch_history 去探会因为"探到了就发请求"
    而多记一次失败，把状态搅乱。"""
    now = T0
    delays = []
    for _ in range(6):
        assert _run(src, monkeypatch, now, empty=True) == ["ES=F"]
        d = 1
        while src._in_backoff("ES=F", now + timedelta(minutes=d)):
            d += 1
            assert d < 200, "退避未在合理时间内到期"
        delays.append(d)
        now = now + timedelta(minutes=d)
    assert delays == [5, 10, 20, 40, 60, 60]


def test_success_resets_streak(src, monkeypatch):
    _run(src, monkeypatch, T0, empty=True)                              # 失败 1 次 → 冷却到 T0+5
    _run(src, monkeypatch, T0 + timedelta(minutes=6), empty=True)       # 失败 2 次 → 冷却到 T0+16
    assert _run(src, monkeypatch, T0 + timedelta(minutes=17), empty=False) == ["ES=F"]  # 成功
    # 成功后 streak 归零：下一轮立刻可请求，且再失败时退避回到最短的 5 分钟
    assert _run(src, monkeypatch, T0 + timedelta(minutes=18), empty=True) == ["ES=F"]
    assert src._fail_streak["ES=F"] == 1
    assert not src._in_backoff("ES=F", T0 + timedelta(minutes=24))


def test_exception_also_counts_as_failure(src, monkeypatch):
    def boom(tickers, **kwargs):
        raise RuntimeError("429-ish")

    monkeypatch.setattr(yfs_module.yf, "download", boom)
    src.fetch_history(T0 - timedelta(hours=24), T0)
    # 异常同样触发退避
    assert _run(src, monkeypatch, T0 + timedelta(minutes=4), empty=False) == []


def test_backoff_is_per_symbol(monkeypatch):
    """一个品种退避不牵连其他品种。"""
    monkeypatch.setattr(yfs_module, "_sleep", lambda s: None)
    monkeypatch.setattr(yfs_module, "_jitter_ratio", lambda: 0.0)
    monkeypatch.setattr(yfs_module.market_sessions, "should_fetch",
                        lambda sym, now: sym in {"ES=F", "GC=F"})
    src = YFinancePriceSource()

    def only_es_fails(tickers, **kwargs):
        return pd.DataFrame() if tickers[0] == "ES=F" else _df(tickers[0])

    monkeypatch.setattr(yfs_module.yf, "download", only_es_fails)
    src.fetch_history(T0 - timedelta(hours=24), T0)

    calls: list[str] = []

    def spy(tickers, **kwargs):
        calls.append(tickers[0])
        return _df(tickers[0])

    monkeypatch.setattr(yfs_module.yf, "download", spy)
    src.fetch_history(T0 - timedelta(hours=24), T0 + timedelta(minutes=3))
    assert calls == ["GC=F"]        # ES=F 在退避中，GC=F 照常


def test_jitter_decorrelates_symbols(monkeypatch):
    """退避到期时刻带抖动，避免全部品种在同一轮齐步重试。"""
    monkeypatch.setattr(yfs_module, "_sleep", lambda s: None)
    monkeypatch.setattr(yfs_module.market_sessions, "should_fetch", lambda sym, now: True)
    src = YFinancePriceSource()
    ratios = {yfs_module._jitter_ratio() for _ in range(50)}
    assert len(ratios) > 1                      # 不是常数
    assert all(0.0 <= r <= 0.25 for r in ratios)
