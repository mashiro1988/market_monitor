"""板块轮动的巨头剔除：BTC/ETH/WBTC/WBETH 不进任何板块聚合数字。

为什么要剔（2026-08-16 本机快照实测）：资金流是**求和**口径，BTC 一个币约 +60~70M
的 24h 现货净流入直接盖住整个板块 —— 白名单里 10 个含 BTC/ETH 的板块中有 9 个方向
反转（本该显示资金流出，页面显示大幅流入），且彼此数字几乎相同、毫无区分度。
涨跌是等权平均，受影响小得多，但同样一并剔除，保证页面上所有数字同一口径。
"""
from datetime import datetime

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
import scanners.sector_scanner as sector_scanner
from database import Base
from models.sector import CmcSymbolCategory
from services import sector_flows, sector_service

T0 = datetime(2026, 1, 1, 0, 0)
T1 = datetime(2026, 1, 1, 1, 0)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _flow_row(net: float, qv: float) -> dict[str, float]:
    row: dict[str, float] = {}
    for window in sector_flows.FLOW_WINDOWS:
        row[f"net_{window}"] = net
        row[f"qv_{window}"] = qv
    return row


def test_excluded_symbols_default_covers_btc_eth_and_their_wrappers():
    assert config.SECTOR_EXCLUDED_SYMBOLS == {"BTC", "ETH", "WBTC", "WBETH"}


def test_leaderboard_carries_exclusion_note_even_when_empty():
    """页面副标题的口径说明由后端下发 —— 以后改 config 名单，页面文案自动跟着变。"""
    session = _session()
    try:
        resp = sector_service.get_leaderboard(session)
        assert resp.rows == []                                  # 空库也要带上说明
        assert resp.exclusion_note == config.SECTOR_EXCLUSION_NOTE
        assert "BTC" in resp.exclusion_note and "ETH" in resp.exclusion_note
    finally:
        session.close()


def test_sector_aggregates_exclude_majors_from_returns_and_flows(monkeypatch):
    """成分币数、涨跌均值/中位、资金流求和，四项都不含巨头。"""
    session = _session()
    majors = {"BTC", "ETH", "WBTC", "WBETH"}
    members = {f"SYM{i}" for i in range(10)} | majors

    returns = {
        f"SYM{i}": {"ret_1h": 1.0, "ret_24h": 1.0, "ret_168h": 1.0, "ret_720h": 1.0}
        for i in range(10)
    }
    for major in majors:
        returns[major] = {
            "ret_1h": 100.0, "ret_24h": 100.0, "ret_168h": 100.0, "ret_720h": 100.0}

    # 普通币每个净流入 1 / 成交额 10；巨头每个 1000 / 10000（模拟真实的量级碾压）
    per_symbol = {sym: _flow_row(1.0, 10.0) for sym in members}
    for major in majors:
        per_symbol[major] = _flow_row(1000.0, 10000.0)

    monkeypatch.setattr(config, "all_whitelisted_cmc_categories", lambda: ["Good"])
    monkeypatch.setattr(config, "cmc_category_to_group", lambda name: "测试")
    monkeypatch.setattr(
        sector_scanner, "_load_market_data",
        lambda *, use_pivot_cache=False: sector_scanner.MarketData(
            snapshot_at=T1, spot_pivot={"close": None}, swap_pivot=None),
    )
    monkeypatch.setattr(sector_scanner, "_per_symbol_returns_from", lambda market: returns)
    monkeypatch.setattr(
        sector_scanner.cmc_client, "load_category_to_symbols",
        lambda session: {"Good": members},
    )
    monkeypatch.setattr(
        sector_flows, "resolve_per_symbol_flows",
        lambda pivot, market, *, as_of: (per_symbol, None),
    )

    try:
        result = sector_scanner.compute_all_sector_returns(session)
        assert len(result.aggregates) == 1
        agg = result.aggregates[0]

        assert agg.token_count == 10          # 14 个成分币里剔掉 4 个巨头
        assert agg.ret_24h == 1.0             # 巨头的 +100% 没有拉高均值
        assert agg.ret_24h_median == 1.0

        spot = agg.flows["spot"]
        assert spot.tokens == 10              # 资金流成分币数同样不含巨头
        assert spot.net["24h"] == 10.0        # 10 × 1，没有巨头那 4000
        assert spot.qv["24h"] == 100.0
    finally:
        session.close()


def test_sector_drops_below_min_tokens_after_excluding_majors(monkeypatch):
    """只靠巨头才够 10 个成分币的板块，剔除后按「信号太薄」跳过 —— 这是预期副作用。"""
    session = _session()
    members = {f"SYM{i}" for i in range(9)} | {"BTC", "ETH"}
    returns = {
        sym: {"ret_1h": 1.0, "ret_24h": 1.0, "ret_168h": 1.0, "ret_720h": 1.0}
        for sym in members
    }

    monkeypatch.setattr(config, "all_whitelisted_cmc_categories", lambda: ["Thin"])
    monkeypatch.setattr(config, "cmc_category_to_group", lambda name: "测试")
    monkeypatch.setattr(
        sector_scanner, "_load_market_data",
        lambda *, use_pivot_cache=False: sector_scanner.MarketData(
            snapshot_at=T1, spot_pivot=None, swap_pivot=None),
    )
    monkeypatch.setattr(sector_scanner, "_per_symbol_returns_from", lambda market: returns)
    monkeypatch.setattr(
        sector_scanner.cmc_client, "load_category_to_symbols",
        lambda session: {"Thin": members},
    )

    try:
        result = sector_scanner.compute_all_sector_returns(session)
        assert result.aggregates == []
        assert result.skipped_thin == ["Thin(9)"]
    finally:
        session.close()


def test_sector_tokens_flags_majors_and_sinks_them_to_bottom(monkeypatch):
    """明细表保留巨头行，但标 excluded=True 并沉到列表最底部。"""
    session = _session()
    for symbol in ("BTC", "ETH", "SYM1", "SYM2"):
        session.add(CmcSymbolCategory(symbol=symbol, category="Good"))
    session.commit()

    # 25 根 1h K 线才够算 ret_24h（不够就全是 None，排序退化成插入顺序，验不出沉底）
    index = pd.date_range(T0, periods=25, freq="h")
    values = [[100.0, 100.0, 100.0, 100.0]] * 24 + [[110.0, 101.0, 102.0, 103.0]]
    close = pd.DataFrame(
        values, index=index, columns=["BTCUSDT", "ETHUSDT", "SYM1USDT", "SYM2USDT"])
    snapshot_at = index[-1].to_pydatetime()

    monkeypatch.setattr(
        sector_service, "_load_market_data",
        lambda *, use_pivot_cache=False: sector_scanner.MarketData(
            snapshot_at=snapshot_at, spot_pivot={"close": close}, swap_pivot=None),
    )
    monkeypatch.setattr(
        sector_flows, "resolve_per_symbol_flows",
        lambda pivot, market, *, as_of: ({}, None),
    )

    try:
        resp = sector_service.get_sector_tokens(session, "Good")
        # 按 24h 涨跌本应是 BTC(10%) > SYM2(3%) > SYM1(2%) > ETH(1%)，
        # 但两个巨头被沉底，普通币之间仍按涨跌降序
        assert [t.symbol for t in resp.tokens] == ["SYM2", "SYM1", "BTC", "ETH"]
        assert [t.excluded for t in resp.tokens] == [False, False, True, True]
    finally:
        session.close()
