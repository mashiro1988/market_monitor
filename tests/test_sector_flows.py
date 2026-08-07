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


def test_gate_rejects_fully_corrupted_latest_bar_in_long_history():
    """整根最新 bar 串列，但历史很长 —— 全矩阵占比会把它稀释到看不见。

    2026-08-07 本地彩排实测：2000 行 × 482 列里坏掉整根最新 bar 只占 0.05%，
    低于 0.1% 的全矩阵阈值，闸门直接放行。而最新 bar 正是 1h 列直接读的那根、
    也是写入损坏最常出现的地方。故单独设一道「最新 bar」检查。
    """
    # 行数必须够长才能复现「稀释」：2000×100 里坏 100 格 = 0.05%，低于全矩阵 0.1% 的线，
    # 全矩阵那道检查会放行，只有最新 bar 那道能拦住（与线上 2000 行的真实形状一致）。
    n_rows, n_cols = 2000, 100
    columns = [f"C{i}USDT" for i in range(n_cols)]
    index = pd.date_range("2026-01-01", periods=n_rows, freq="h")
    close = pd.DataFrame(1.0, index=index, columns=columns)
    qv = pd.DataFrame(100.0, index=index, columns=columns)
    tb = pd.DataFrame(60.0, index=index, columns=columns)
    tb.iloc[-1, :] = 500.0  # 最新一整根 bar 串列：主动买入额是总成交额的 5 倍

    pivot = {"close": close, "quote_volume": qv, "taker_buy_quote_asset_volume": tb}
    reason = sector_flows.check_flow_gate(pivot)
    assert reason is not None, "整根最新 bar 串列必须被拦下"
    assert "最新 bar" in reason and "恒等式" in reason


def test_gate_tolerates_single_bad_cell_on_latest_bar():
    """最新 bar 上个别币抽风不该让整个市场的资金流作废（避免每小时误报）。"""
    n_rows, n_cols = 500, 100
    columns = [f"C{i}USDT" for i in range(n_cols)]
    index = pd.date_range("2026-01-01", periods=n_rows, freq="h")
    close = pd.DataFrame(1.0, index=index, columns=columns)
    qv = pd.DataFrame(100.0, index=index, columns=columns)
    tb = pd.DataFrame(60.0, index=index, columns=columns)
    tb.iloc[-1, 0] = 500.0  # 100 个币里坏 1 个 = 1%，在 5% 容忍内

    pivot = {"close": close, "quote_volume": qv, "taker_buy_quote_asset_volume": tb}
    assert sector_flows.check_flow_gate(pivot) is None


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


# ---------------- 板块聚合 ----------------

def test_aggregate_sums_members_and_ignores_outsiders():
    per_symbol = {
        "BTC": {"net_1h": 100.0, "qv_1h": 1000.0, "net_24h": 300.0, "qv_24h": 3000.0},
        "ETH": {"net_1h": -40.0, "qv_1h": 400.0, "net_24h": -60.0, "qv_24h": 600.0},
        "DOGE": {"net_1h": 999.0, "qv_1h": 999.0},   # 不在板块里
    }
    side = sector_flows.aggregate_side(per_symbol, {"BTC", "ETH"})
    assert side.tokens == 2
    assert side.net["1h"] == pytest.approx(60.0)
    assert side.qv["1h"] == pytest.approx(1400.0)
    assert side.net["24h"] == pytest.approx(240.0)
    assert side.qv["24h"] == pytest.approx(3600.0)
    # 无人有 168h 数据 → None（不是 0）
    assert side.net["168h"] is None
    assert side.qv["168h"] is None


def test_aggregate_returns_none_when_no_member_has_flow_data():
    assert sector_flows.aggregate_side({"BTC": {"net_1h": 1.0, "qv_1h": 2.0}}, {"SOL"}) is None


def test_aggregate_counts_only_members_with_data():
    per_symbol = {"BTC": {"net_1h": 10.0, "qv_1h": 100.0}}
    side = sector_flows.aggregate_side(per_symbol, {"BTC", "ETH", "SOL"})
    assert side.tokens == 1


# ---------------- DB 列名映射 ----------------

def test_to_columns_roundtrips_through_from_row():
    per_symbol = {"BTC": {"net_24h": 500.0, "qv_24h": 5000.0}}
    sides = {
        "spot": sector_flows.aggregate_side(per_symbol, {"BTC"}),
        "swap": None,
    }
    columns = sector_flows.to_columns(sides)
    assert columns["spot_net_24h"] == pytest.approx(500.0)
    assert columns["spot_qv_24h"] == pytest.approx(5000.0)
    assert columns["spot_flow_tokens"] == 1
    assert columns["spot_net_1h"] is None
    assert columns["swap_net_24h"] is None
    assert columns["swap_flow_tokens"] is None
    assert len(columns) == 18

    class Row:
        pass
    row = Row()
    for name, value in columns.items():
        setattr(row, name, value)

    flows = sector_flows.from_row(row)
    assert flows["spot"]["net_24h"] == pytest.approx(500.0)
    assert flows["spot"]["tokens"] == 1
    assert flows["swap"] is None


def test_from_row_returns_none_side_when_all_values_missing():
    class Row:
        pass
    row = Row()
    for name in sector_flows.to_columns({"spot": None, "swap": None}):
        setattr(row, name, None)
    flows = sector_flows.from_row(row)
    assert flows == {"spot": None, "swap": None}


# ---------------- scanner 接入 ----------------
from sqlalchemy import create_engine            # noqa: E402
from sqlalchemy.orm import sessionmaker          # noqa: E402

from database import Base                        # noqa: E402
from models.sector import SectorReturn           # noqa: E402
import scanners.sector_scanner as sector_scanner  # noqa: E402


def _memory_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _wire_scanner(monkeypatch, *, spot_pivot, swap_pivot, symbols):
    """把 scanner 的 pivot 加载与板块映射都换成内存假数据。"""
    monkeypatch.setattr(
        sector_scanner, "_load_market_data",
        lambda use_pivot_cache=False: sector_scanner.MarketData(
            snapshot_at=T1, spot_pivot=spot_pivot, swap_pivot=swap_pivot),
    )
    monkeypatch.setattr(config, "all_whitelisted_cmc_categories", lambda: ["AI"])
    monkeypatch.setattr(config, "cmc_category_to_group", lambda name: "测试")
    monkeypatch.setattr(sector_scanner, "MIN_TOKENS_PER_SECTOR", 1)
    monkeypatch.setattr(
        sector_scanner.cmc_client, "load_category_to_symbols",
        lambda session: {"AI": symbols},
    )
    # 勾稽失败分支会走真实推送（建 SessionLocal + 发企业微信），测试里一律挡掉
    monkeypatch.setattr(
        "services.sector_flow_monitoring.alert_flow_gate_failures",
        lambda failures, **kwargs: [],
    )


def test_scan_writes_flow_columns(monkeypatch):
    _wire_scanner(monkeypatch, spot_pivot=_good_pivot(), swap_pivot=None,
                  symbols={"BTC", "ETH"})
    session = _memory_session()
    try:
        stats = sector_scanner.SectorScanner(session=session).scan()
        assert stats["sectors_written"] == 1
        row = session.query(SectorReturn).one()
        # 现货：BTC(+800) + ETH(−200) = +600；qv = 2000 + 400 = 2400
        assert row.spot_net_1h == pytest.approx(600.0)
        assert row.spot_qv_1h == pytest.approx(2400.0)
        assert row.spot_flow_tokens == 2
        # 无永续 pivot → 整侧为空
        assert row.swap_net_1h is None
        assert row.swap_flow_tokens is None
        # 涨跌照常
        assert row.ret_1h is not None
    finally:
        session.close()


def test_scan_nulls_flows_but_keeps_returns_when_gate_fails(monkeypatch):
    """勾稽门失败：资金流全空、涨跌照常、失败原因进 result。"""
    broken = _good_pivot()
    del broken[sector_flows.TAKER_BUY_KEY]
    _wire_scanner(monkeypatch, spot_pivot=broken, swap_pivot=None, symbols={"BTC", "ETH"})
    session = _memory_session()
    try:
        result = sector_scanner.compute_all_sector_returns(session)
        assert "spot" in result.flow_gate_failures
        assert "缺字段" in result.flow_gate_failures["spot"]
        aggregate = result.aggregates[0]
        assert aggregate.ret_1h is not None
        assert aggregate.flows["spot"] is None
    finally:
        session.close()


def test_gate_failure_on_one_market_does_not_affect_the_other(monkeypatch):
    broken_spot = _good_pivot()
    del broken_spot[sector_flows.TAKER_BUY_KEY]
    _wire_scanner(monkeypatch, spot_pivot=broken_spot, swap_pivot=_good_pivot(),
                  symbols={"BTC", "ETH"})
    session = _memory_session()
    try:
        result = sector_scanner.compute_all_sector_returns(session)
        assert set(result.flow_gate_failures) == {"spot"}
        aggregate = result.aggregates[0]
        assert aggregate.flows["spot"] is None
        assert aggregate.flows["swap"].net["1h"] == pytest.approx(600.0)
    finally:
        session.close()


def test_legacy_pivot_without_flow_fields_degrades_quietly(monkeypatch):
    """服务器补丁没上/已回滚时，涨跌照常产出，资金流全空 —— 本项目可先于补丁上线。"""
    legacy = {"close": _frame(["BTCUSDT", "ETHUSDT"],
                              [(T0, [100.0, 10.0]), (T1, [110.0, 11.0])])}
    _wire_scanner(monkeypatch, spot_pivot=legacy, swap_pivot=None, symbols={"BTC", "ETH"})
    session = _memory_session()
    try:
        stats = sector_scanner.SectorScanner(session=session).scan()
        assert stats["sectors_written"] == 1
        row = session.query(SectorReturn).one()
        assert row.ret_1h is not None
        assert row.spot_net_24h is None
    finally:
        session.close()


# ---------------- 榜单读路径 ----------------
from services import sector_service  # noqa: E402


def test_leaderboard_serializes_flows_from_db_columns():
    session = _memory_session()
    try:
        session.add(SectorReturn(
            snapshot_at=T1, category="AI", group_name="测试", token_count=12,
            ret_24h=3.0, ret_24h_median=2.0,
            spot_net_24h=500.0, spot_qv_24h=5000.0, spot_flow_tokens=11,
        ))
        session.commit()

        response = sector_service.get_leaderboard(session)
        row = response.rows[0]
        assert row.flows is not None
        assert row.flows.spot.net_24h == pytest.approx(500.0)
        assert row.flows.spot.qv_24h == pytest.approx(5000.0)
        assert row.flows.spot.tokens == 11
        assert row.flows.spot.net_1h is None
        assert row.flows.swap is None
    finally:
        session.close()


def test_leaderboard_row_without_flow_data_has_both_sides_none():
    session = _memory_session()
    try:
        session.add(SectorReturn(
            snapshot_at=T1, category="AI", group_name="测试", token_count=12, ret_24h=3.0))
        session.commit()
        row = sector_service.get_leaderboard(session).rows[0]
        assert row.flows.spot is None
        assert row.flows.swap is None
    finally:
        session.close()


# ---------------- 成分币钻取 ----------------
from models.sector import CmcSymbolCategory  # noqa: E402


def test_token_row_carries_both_market_flows(monkeypatch):
    """一行币同时挂现货与永续两侧资金流，与它价格取自哪个市场无关。"""
    session = _memory_session()
    try:
        session.add(CmcSymbolCategory(symbol="BTC", category="AI"))
        session.commit()
        monkeypatch.setattr(config, "cmc_category_to_group", lambda name: "测试")
        # 注意：要打 sector_service 上的名字 —— 它是 from ... import 进来的绑定，
        # 打 sector_scanner 上的原名对已绑定的引用无效。
        monkeypatch.setattr(
            sector_service, "_load_market_data",
            lambda use_pivot_cache=False: sector_scanner.MarketData(
                snapshot_at=T1, spot_pivot=_good_pivot(), swap_pivot=_good_pivot()),
        )

        response = sector_service.get_sector_tokens(session, "AI")
        row = next(t for t in response.tokens if t.symbol == "BTC")
        assert row.market == "spot"           # 价格仍是现货优先
        assert row.flows.spot.net_1h == pytest.approx(800.0)
        assert row.flows.swap.net_1h == pytest.approx(800.0)
        assert row.flows.spot.tokens is None  # 币行不带覆盖币数
    finally:
        session.close()


def test_token_row_flow_side_is_none_when_market_absent(monkeypatch):
    session = _memory_session()
    try:
        session.add(CmcSymbolCategory(symbol="BTC", category="AI"))
        session.commit()
        monkeypatch.setattr(config, "cmc_category_to_group", lambda name: "测试")
        monkeypatch.setattr(
            sector_service, "_load_market_data",
            lambda use_pivot_cache=False: sector_scanner.MarketData(
                snapshot_at=T1, spot_pivot=_good_pivot(), swap_pivot=None),
        )
        row = sector_service.get_sector_tokens(session, "AI").tokens[0]
        assert row.flows.spot is not None
        assert row.flows.swap is None
    finally:
        session.close()


def test_token_flows_empty_when_gate_fails(monkeypatch):
    session = _memory_session()
    try:
        session.add(CmcSymbolCategory(symbol="BTC", category="AI"))
        session.commit()
        broken = _good_pivot()
        del broken[sector_flows.TAKER_BUY_KEY]
        monkeypatch.setattr(config, "cmc_category_to_group", lambda name: "测试")
        monkeypatch.setattr(
            sector_service, "_load_market_data",
            lambda use_pivot_cache=False: sector_scanner.MarketData(
                snapshot_at=T1, spot_pivot=broken, swap_pivot=None),
        )
        row = sector_service.get_sector_tokens(session, "AI").tokens[0]
        assert row.ret_1h is not None      # 涨跌照常
        assert row.flows.spot is None      # 资金流作废
    finally:
        session.close()
