"""yfinance 源必须把所有资产组合并为**一次** yf.download 调用。

注意：合并**不减少**对 Yahoo 的 HTTP 请求数——yf.download 内部对每个 ticker
各发一次 chart 请求（multi.py 无批量端点），怎么分组都一样。合并的真实价值：
1. 消除「单品种资产组」落入 len(ticker_list)==1 坏分支的结构性风险——
   yfinance ≥0.2.51 对列表输入恒返回 MultiIndex 列，df["Close"] 在单 ticker 时
   是 DataFrame 而非 Series，旧代码 currencies 组只有美元指数一个品种，
   每周期都走坏分支：fetch_history 恒返回 0 条（回补永远补不出美元指数）、
   fetch 落入未收盘 fallback。这是美元指数上线后一直无数据的确定性原因之一。
2. 代码路径单一，失败/解析行为全品种一致。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

import pandas as pd

from scanners.sources import yfinance_source as yfs


def _fake_df(symbols: list[str]) -> pd.DataFrame:
    """两根已收盘 5m K 线（UTC tz-aware index），列为 MultiIndex (字段, symbol)。"""
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    idx = pd.DatetimeIndex([now - timedelta(minutes=20), now - timedelta(minutes=15)], tz="UTC")
    cols = pd.MultiIndex.from_product([["Close"], symbols])
    data = [[100.0 + i for i in range(len(symbols))], [101.0 + i for i in range(len(symbols))]]
    return pd.DataFrame(data, index=idx, columns=cols)


def _patch_download(monkeypatch, calls):
    def fake_download(tickers, **kwargs):
        tick = list(tickers) if isinstance(tickers, (list, tuple)) else [tickers]
        calls.append(tick)
        return _fake_df(tick)

    monkeypatch.setattr(yfs.yf, "download", fake_download)


def test_fetch_downloads_once_across_all_groups(monkeypatch):
    calls = []
    _patch_download(monkeypatch, calls)

    records = yfs.YFinancePriceSource().fetch()

    assert len(calls) == 1, f"fetch 应只调一次 yf.download，实际 {len(calls)} 次"
    tickers = set(calls[0])
    # 跨组品种在同一次调用里（期货组 + 外汇组）
    assert "NQ=F" in tickers and "DX-Y.NYB" in tickers

    by_symbol = {r.symbol: r for r in records}
    assert by_symbol["NQ=F"].asset_class == "futures"
    assert by_symbol["DX-Y.NYB"].asset_class == "currency"
    assert by_symbol["DX-Y.NYB"].name == "美元指数"


def test_fetch_history_downloads_once_across_all_groups(monkeypatch):
    calls = []
    _patch_download(monkeypatch, calls)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    records = yfs.YFinancePriceSource().fetch_history(now - timedelta(hours=2), now)

    assert len(calls) == 1, f"fetch_history 应只调一次 yf.download，实际 {len(calls)} 次"
    tickers = set(calls[0])
    assert "NQ=F" in tickers and "DX-Y.NYB" in tickers

    classes = {r.symbol: r.asset_class for r in records}
    assert classes.get("NQ=F") == "futures"
    assert classes.get("DX-Y.NYB") == "currency"


def test_single_ticker_multiindex_columns_handled(monkeypatch):
    """yfinance ≥0.2.51 对单 ticker 列表也返回 MultiIndex 列（df["Close"] 是 DataFrame）。

    旧代码的 len(ticker_list)==1 特判直接把 DataFrame 当 Series 用：fetch_history
    静默 0 条、fetch 走未收盘 fallback——美元指数（currencies 组唯一品种）因此一直无数据。
    config 收缩到全局只剩 1 个品种时同样会触发，必须防御。"""
    calls = []
    _patch_download(monkeypatch, calls)

    src = yfs.YFinancePriceSource()
    src.symbol_groups = {"currency": {"美元指数": "DX-Y.NYB"}}   # 全局只剩 1 个品种

    records = src.fetch()
    assert len(records) == 1
    r = records[0]
    assert r.symbol == "DX-Y.NYB" and r.asset_class == "currency"
    assert r.price == 101.0                      # 最后一根已收盘 bar 的收盘价
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert r.timestamp is not None and r.timestamp <= now   # 不允许未来时间戳（未收盘 fallback 的特征）

    history = src.fetch_history(now - timedelta(hours=2), now)
    assert len(history) == 2, "单品种 config 下 fetch_history 不得静默返回 0 条"


def test_fetch_single_symbol_failure_does_not_drop_batch(monkeypatch):
    """单个品种数据缺失（列全 NaN）只跳过该品种，不影响同批其它品种。"""
    def fake_download(tickers, **kwargs):
        tick = list(tickers) if isinstance(tickers, (list, tuple)) else [tickers]
        df = _fake_df(tick)
        df[("Close", "DX-Y.NYB")] = float("nan")   # 模拟该品种拉取失败
        return df

    monkeypatch.setattr(yfs.yf, "download", fake_download)

    records = yfs.YFinancePriceSource().fetch()
    symbols = {r.symbol for r in records}
    assert "DX-Y.NYB" not in symbols
    assert "NQ=F" in symbols
