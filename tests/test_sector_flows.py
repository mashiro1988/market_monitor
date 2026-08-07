"""板块资金流：勾稽门 + 窗口求和 + 板块聚合。"""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

import config
from services import sector_flows


def _frame(columns, rows):
    """rows: [(ts, [values...]), ...] -> DataFrame(index=时间, columns=币)"""
    return pd.DataFrame(
        [values for _, values in rows],
        index=pd.DatetimeIndex([ts for ts, _ in rows]),
        columns=columns,
    )


def _pivot(columns, close_rows, qv_rows, taker_rows):
    """造一份合规的 pivot dict（close + 两个资金流矩阵）。"""
    return {
        "close": _frame(columns, close_rows),
        "quote_volume": _frame(columns, qv_rows),
        "taker_buy_quote_asset_volume": _frame(columns, taker_rows),
    }


T0 = datetime(2026, 8, 7, 9, 0)
T1 = datetime(2026, 8, 7, 10, 0)


def _good_pivot():
    return _pivot(
        ["BTCUSDT", "ETHUSDT"],
        close_rows=[(T0, [100.0, 10.0]), (T1, [110.0, 11.0])],
        qv_rows=[(T0, [1000.0, 200.0]), (T1, [2000.0, 400.0])],
        taker_rows=[(T0, [600.0, 80.0]), (T1, [1400.0, 100.0])],
    )


# ---------------- 勾稽门 ----------------

def test_gate_passes_on_wellformed_pivot():
    assert sector_flows.check_flow_gate(_good_pivot()) is None


def test_gate_rejects_missing_keys():
    pivot = _good_pivot()
    del pivot["taker_buy_quote_asset_volume"]
    reason = sector_flows.check_flow_gate(pivot)
    assert reason is not None
    assert "缺字段" in reason


def test_gate_rejects_index_mismatch():
    pivot = _good_pivot()
    pivot["quote_volume"] = _frame(["BTCUSDT", "ETHUSDT"], [(T1, [2000.0, 400.0])])
    reason = sector_flows.check_flow_gate(pivot)
    assert reason is not None
    assert "对齐" in reason


def test_gate_rejects_column_mismatch():
    pivot = _good_pivot()
    pivot["quote_volume"] = _frame(
        ["BTCUSDT", "SOLUSDT"],
        [(T0, [1000.0, 200.0]), (T1, [2000.0, 400.0])],
    )
    reason = sector_flows.check_flow_gate(pivot)
    assert reason is not None
    assert "对齐" in reason


def test_gate_rejects_identity_violation_over_threshold():
    """主动买入额 > 总成交额 = 数据串列，超过容忍占比就拦下。"""
    pivot = _good_pivot()
    pivot["taker_buy_quote_asset_volume"] = _frame(
        ["BTCUSDT", "ETHUSDT"],
        [(T0, [9999.0, 80.0]), (T1, [1400.0, 100.0])],  # 4 格里 1 格违规 = 25%
    )
    reason = sector_flows.check_flow_gate(pivot)
    assert reason is not None
    assert "恒等式" in reason


def test_gate_tolerates_float_noise_within_ratio():
    """浮点噪声导致的极小超出不该拦（相对容差 1e-6 内视为合规）。"""
    pivot = _good_pivot()
    pivot["taker_buy_quote_asset_volume"] = _frame(
        ["BTCUSDT", "ETHUSDT"],
        [(T0, [1000.0000001, 80.0]), (T1, [1400.0, 100.0])],
    )
    assert sector_flows.check_flow_gate(pivot) is None


def test_gate_rejects_nan_gap_over_threshold(monkeypatch):
    """收盘价有值但成交额大面积缺失 = 新字段没写好。"""
    monkeypatch.setattr(config, "FLOW_NAN_GAP_MAX", 0.05)
    pivot = _pivot(
        ["BTCUSDT", "ETHUSDT"],
        close_rows=[(T0, [100.0, 10.0]), (T1, [110.0, 11.0])],
        qv_rows=[(T0, [1000.0, 200.0]), (T1, [2000.0, np.nan])],  # 最新 bar 缺 50%
        taker_rows=[(T0, [600.0, 80.0]), (T1, [1400.0, np.nan])],
    )
    reason = sector_flows.check_flow_gate(pivot)
    assert reason is not None
    assert "缺失" in reason


def test_gate_allows_nan_gap_when_close_also_missing():
    """收盘价本身就缺的币不算资金流的账（缺口 = 0）。"""
    pivot = _pivot(
        ["BTCUSDT", "ETHUSDT"],
        close_rows=[(T0, [100.0, 10.0]), (T1, [110.0, np.nan])],
        qv_rows=[(T0, [1000.0, 200.0]), (T1, [2000.0, np.nan])],
        taker_rows=[(T0, [600.0, 80.0]), (T1, [1400.0, np.nan])],
    )
    assert sector_flows.check_flow_gate(pivot) is None


def test_gate_rejects_none_pivot():
    assert sector_flows.check_flow_gate(None) is not None


# ---------------- 币级窗口求和 ----------------

def test_per_symbol_flows_single_bar_window():
    """1h 窗口 = 最近 1 根 bar。BTC: 2×1400−2000 = +800，qv=2000。"""
    flows = sector_flows.per_symbol_flows(_good_pivot(), as_of=T1)
    assert flows["BTC"]["net_1h"] == pytest.approx(800.0)
    assert flows["BTC"]["qv_1h"] == pytest.approx(2000.0)
    # ETH 主动买入 100 / 总额 400 → 净 −200（卖压）
    assert flows["ETH"]["net_1h"] == pytest.approx(-200.0)


def test_per_symbol_flows_partial_window_sums_available_bars():
    """窗口 24 根但只有 2 根 bar → 按实际存在的 bar 求和，不补零不作废。"""
    flows = sector_flows.per_symbol_flows(_good_pivot(), as_of=T1)
    # BTC 两根：(2×600−1000) + (2×1400−2000) = 200 + 800 = 1000；qv = 3000
    assert flows["BTC"]["net_24h"] == pytest.approx(1000.0)
    assert flows["BTC"]["qv_24h"] == pytest.approx(3000.0)


def test_per_symbol_flows_respects_as_of_cutoff():
    """as_of 早于最新 bar 时，晚于 as_of 的 bar 不进窗口。"""
    flows = sector_flows.per_symbol_flows(_good_pivot(), as_of=T0)
    assert flows["BTC"]["net_1h"] == pytest.approx(200.0)
    assert flows["BTC"]["qv_24h"] == pytest.approx(1000.0)


def test_per_symbol_flows_skips_all_nan_symbol():
    """整窗口全 NaN 的币不产出该窗口字段（而不是产出 0）。"""
    pivot = _pivot(
        ["BTCUSDT", "DEADUSDT"],
        close_rows=[(T0, [100.0, np.nan]), (T1, [110.0, np.nan])],
        qv_rows=[(T0, [1000.0, np.nan]), (T1, [2000.0, np.nan])],
        taker_rows=[(T0, [600.0, np.nan]), (T1, [1400.0, np.nan])],
    )
    flows = sector_flows.per_symbol_flows(pivot, as_of=T1)
    assert "DEAD" not in flows
    assert "BTC" in flows


def test_per_symbol_flows_pairs_net_and_qv_from_same_bars():
    """某根 bar 只有一边有值时，两边都不算 —— 保证强度比率内部自洽。"""
    pivot = _pivot(
        ["BTCUSDT"],
        close_rows=[(T0, [100.0]), (T1, [110.0])],
        qv_rows=[(T0, [1000.0]), (T1, [2000.0])],
        taker_rows=[(T0, [np.nan]), (T1, [1400.0])],  # 第一根缺主动买入额
    )
    flows = sector_flows.per_symbol_flows(pivot, as_of=T1)
    assert flows["BTC"]["qv_24h"] == pytest.approx(2000.0)   # 只算第二根
    assert flows["BTC"]["net_24h"] == pytest.approx(800.0)


def test_per_symbol_flows_merges_duplicate_normalized_symbols():
    """同一市场内两个交易对归一到同一 symbol（如 BEAMX→BEAM）时合并求和。"""
    pivot = _pivot(
        ["BEAMUSDT", "BEAMXUSDT"],
        close_rows=[(T0, [1.0, 1.0]), (T1, [1.0, 1.0])],
        qv_rows=[(T0, [100.0, 100.0]), (T1, [100.0, 100.0])],
        taker_rows=[(T0, [80.0, 80.0]), (T1, [80.0, 80.0])],
    )
    flows = sector_flows.per_symbol_flows(pivot, as_of=T1)
    # 单个交易对 1h：2×80−100 = 60；两个合并 = 120
    assert flows["BEAM"]["net_1h"] == pytest.approx(120.0)
    assert flows["BEAM"]["qv_1h"] == pytest.approx(200.0)


def test_per_symbol_flows_returns_empty_for_none_pivot():
    assert sector_flows.per_symbol_flows(None, as_of=T1) == {}
