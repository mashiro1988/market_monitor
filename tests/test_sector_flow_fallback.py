"""宽表没有 taker 字段时，回退去读单币文件算资金流。

背景（2026-08-08 实证）：mmon.top 读的 BMAC 跑在第三方交易框架容器
（xbxtempleton/qronos-trading-framework）里，改不得、也留不住（镜像一更新补丁就没）。
但 taker 字段本来就在单币 1h resample 文件里 —— 宽表里没有只是因为 BMAC 的
make_market_pivot 没把它透视进去。所以直接读源文件，彻底不依赖任何 BMAC 改动。
"""
import pickle
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import config
from services import remote_fs, sector_flows


T0 = datetime(2026, 8, 7, 9, 0)
T1 = datetime(2026, 8, 7, 10, 0)


def _write_symbol_file(path: Path, rows, *, columns=None):
    """造一个 BMAC 单币 1h resample 文件（candle_begin_time 是列、tz-aware UTC）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame["candle_begin_time"] = pd.DatetimeIndex(frame["candle_begin_time"]).tz_localize("UTC")
    if columns is not None:
        frame = frame[columns]
    with open(path, "wb") as fh:
        pickle.dump(frame, fh)


def _make_tree(tmp_path: Path, market: str = "spot") -> Path:
    """铺一个最小的 BMAC 数据目录：两个币、两根 bar。"""
    root = tmp_path / "data"
    d = root / f"binance_{market}_1h_resample" / "30m"
    # BTC: 1h 净流入 = 2*1400-2000 = +800；24h（两根）= 200+800 = +1000
    _write_symbol_file(d / "BTCUSDT.pkl", [
        {"candle_begin_time": T0, "open": 99.0, "close": 100.0,
         "quote_volume": 1000.0, "taker_buy_quote_asset_volume": 600.0},
        {"candle_begin_time": T1, "open": 100.0, "close": 110.0,
         "quote_volume": 2000.0, "taker_buy_quote_asset_volume": 1400.0},
    ])
    # ETH: 1h 净流入 = 2*100-400 = -200
    _write_symbol_file(d / "ETHUSDT.pkl", [
        {"candle_begin_time": T0, "open": 9.9, "close": 10.0,
         "quote_volume": 200.0, "taker_buy_quote_asset_volume": 80.0},
        {"candle_begin_time": T1, "open": 10.0, "close": 11.0,
         "quote_volume": 400.0, "taker_buy_quote_asset_volume": 100.0},
    ])
    return root


def _use_local_tree(monkeypatch, root: Path):
    monkeypatch.setattr(remote_fs, "REMOTE_BACKEND", "local")
    monkeypatch.setattr(remote_fs, "REMOTE_DATA_ROOT", str(root).replace("\\", "/") + "/")
    monkeypatch.setattr(config, "REMOTE_OFFSET", "30m")


# ---------------- 读文件算资金流 ----------------

def test_reads_flows_from_per_symbol_files(tmp_path, monkeypatch):
    _use_local_tree(monkeypatch, _make_tree(tmp_path))
    flows = sector_flows.per_symbol_flows_from_files("spot", as_of=T1)

    assert set(flows) == {"BTC", "ETH"}
    assert flows["BTC"]["net_1h"] == pytest.approx(800.0)
    assert flows["BTC"]["qv_1h"] == pytest.approx(2000.0)
    assert flows["BTC"]["net_24h"] == pytest.approx(1000.0)
    assert flows["BTC"]["qv_24h"] == pytest.approx(3000.0)
    assert flows["ETH"]["net_1h"] == pytest.approx(-200.0)


def test_file_path_agrees_with_pivot_path(tmp_path, monkeypatch):
    """同一份数据，两条路径必须算出一模一样的结果 —— 否则口径就分叉了。"""
    _use_local_tree(monkeypatch, _make_tree(tmp_path))
    from_files = sector_flows.per_symbol_flows_from_files("spot", as_of=T1)

    index = pd.DatetimeIndex([T0, T1])
    pivot = {
        "close": pd.DataFrame([[100.0, 10.0], [110.0, 11.0]], index=index,
                              columns=["BTCUSDT", "ETHUSDT"]),
        "quote_volume": pd.DataFrame([[1000.0, 200.0], [2000.0, 400.0]], index=index,
                                     columns=["BTCUSDT", "ETHUSDT"]),
        "taker_buy_quote_asset_volume": pd.DataFrame([[600.0, 80.0], [1400.0, 100.0]], index=index,
                                                     columns=["BTCUSDT", "ETHUSDT"]),
    }
    from_pivot = sector_flows.per_symbol_flows(pivot, as_of=T1)

    assert set(from_files) == set(from_pivot)
    for sym in from_files:
        assert set(from_files[sym]) == set(from_pivot[sym]), sym
        for key, value in from_files[sym].items():
            assert value == pytest.approx(from_pivot[sym][key]), f"{sym}.{key}"


def test_respects_as_of_cutoff(tmp_path, monkeypatch):
    _use_local_tree(monkeypatch, _make_tree(tmp_path))
    flows = sector_flows.per_symbol_flows_from_files("spot", as_of=T0)
    assert flows["BTC"]["net_1h"] == pytest.approx(200.0)
    assert flows["BTC"]["qv_24h"] == pytest.approx(1000.0)


def test_skips_files_missing_taker_columns(tmp_path, monkeypatch):
    """个别币缺字段不该让整轮作废 —— 跳过它，其余照算。"""
    root = _make_tree(tmp_path)
    d = root / "binance_spot_1h_resample" / "30m"
    _write_symbol_file(d / "OLDUSDT.pkl", [
        {"candle_begin_time": T0, "open": 1.0, "close": 1.0},
        {"candle_begin_time": T1, "open": 1.0, "close": 1.0},
    ], columns=["candle_begin_time", "open", "close"])
    _use_local_tree(monkeypatch, root)

    flows = sector_flows.per_symbol_flows_from_files("spot", as_of=T1)
    assert "OLD" not in flows
    assert set(flows) == {"BTC", "ETH"}


def test_skips_unreadable_file_without_killing_the_batch(tmp_path, monkeypatch):
    root = _make_tree(tmp_path)
    (root / "binance_spot_1h_resample" / "30m" / "BADUSDT.pkl").write_bytes(b"not a pickle")
    _use_local_tree(monkeypatch, root)

    flows = sector_flows.per_symbol_flows_from_files("spot", as_of=T1)
    assert set(flows) == {"BTC", "ETH"}


def test_ignores_non_symbol_filenames(tmp_path, monkeypatch):
    """.ready 标记、乱码文件名都不该被当成币。"""
    root = _make_tree(tmp_path)
    d = root / "binance_spot_1h_resample" / "30m"
    (d / "binance_spot_1h_resample_1786163400.ready").write_text("1786163400")
    _use_local_tree(monkeypatch, root)

    flows = sector_flows.per_symbol_flows_from_files("spot", as_of=T1)
    assert set(flows) == {"BTC", "ETH"}


def test_missing_directory_returns_empty(tmp_path, monkeypatch):
    _use_local_tree(monkeypatch, tmp_path / "nowhere")
    assert sector_flows.per_symbol_flows_from_files("spot", as_of=T1) == {}


def test_sftp_backend_refuses_to_read_hundreds_of_files(tmp_path, monkeypatch):
    """SFTP 模式下几百次网络往返不可接受 —— 直接返回空，让调用方走「不可用」。"""
    _use_local_tree(monkeypatch, _make_tree(tmp_path))
    monkeypatch.setattr(remote_fs, "REMOTE_BACKEND", "sftp")
    assert sector_flows.per_symbol_flows_from_files("spot", as_of=T1) == {}


# ---------------- 接进扫描器：宽表不行就回退 ----------------
from sqlalchemy import create_engine          # noqa: E402
from sqlalchemy.orm import sessionmaker       # noqa: E402

from database import Base                     # noqa: E402
from models.sector import SectorReturn        # noqa: E402
import scanners.sector_scanner as sector_scanner  # noqa: E402


def _legacy_pivot():
    """补丁前的宽表：只有价格，没有 taker 字段 —— 正是线上那个容器里 BMAC 的产物。"""
    index = pd.DatetimeIndex([T0, T1])
    return {"close": pd.DataFrame([[100.0, 10.0], [110.0, 11.0]], index=index,
                                  columns=["BTCUSDT", "ETHUSDT"])}


def _memory_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _wire(monkeypatch, *, spot_pivot, swap_pivot):
    monkeypatch.setattr(
        sector_scanner, "_load_market_data",
        lambda use_pivot_cache=False: sector_scanner.MarketData(
            snapshot_at=T1, spot_pivot=spot_pivot, swap_pivot=swap_pivot))
    monkeypatch.setattr(config, "all_whitelisted_cmc_categories", lambda: ["AI"])
    monkeypatch.setattr(config, "cmc_category_to_group", lambda name: "测试")
    monkeypatch.setattr(sector_scanner, "MIN_TOKENS_PER_SECTOR", 1)
    # 同 test_sector_flows.py：样本币就叫 BTC/ETH，生产配置会把它们剔出板块聚合，
    # 不关掉的话板块被剔空，测不到回退路径本身。剔除行为由 test_sector_exclusions.py 专测。
    monkeypatch.setattr(config, "SECTOR_EXCLUDED_SYMBOLS", set())
    monkeypatch.setattr(sector_scanner.cmc_client, "load_category_to_symbols",
                        lambda session: {"AI": {"BTC", "ETH"}})
    monkeypatch.setattr("services.sector_flow_monitoring.alert_flow_gate_failures",
                        lambda failures, **kw: [])


def test_scan_falls_back_to_files_when_pivot_lacks_taker(tmp_path, monkeypatch):
    """线上实况复现：宽表没有 taker 字段，但单币文件有 → 资金流照样算出来，且不告警。"""
    _use_local_tree(monkeypatch, _make_tree(tmp_path))
    _wire(monkeypatch, spot_pivot=_legacy_pivot(), swap_pivot=None)
    session = _memory_session()
    try:
        result = sector_scanner.compute_all_sector_returns(session)
        assert result.flow_gate_failures == {}, "回退成功就不该报失败、更不该告警"
        side = result.aggregates[0].flows["spot"]
        assert side is not None
        # BTC(+800) + ETH(-200) = +600；qv = 2000 + 400 = 2400
        assert side.net["1h"] == pytest.approx(600.0)
        assert side.qv["1h"] == pytest.approx(2400.0)
        assert side.tokens == 2
    finally:
        session.close()


def test_scan_reports_failure_only_when_both_paths_dead(tmp_path, monkeypatch):
    """宽表没字段、单币文件也读不到 → 这才是真失败，写 None 并记原因。"""
    _use_local_tree(monkeypatch, tmp_path / "empty")
    _wire(monkeypatch, spot_pivot=_legacy_pivot(), swap_pivot=None)
    session = _memory_session()
    try:
        result = sector_scanner.compute_all_sector_returns(session)
        assert "spot" in result.flow_gate_failures
        assert "缺字段" in result.flow_gate_failures["spot"]
        assert result.aggregates[0].flows["spot"] is None
        assert result.aggregates[0].ret_1h is not None, "涨跌永远不受资金流影响"
    finally:
        session.close()


def test_scan_writes_fallback_flows_to_db(tmp_path, monkeypatch):
    _use_local_tree(monkeypatch, _make_tree(tmp_path))
    _wire(monkeypatch, spot_pivot=_legacy_pivot(), swap_pivot=None)
    session = _memory_session()
    try:
        stats = sector_scanner.SectorScanner(session=session).scan()
        assert stats["sectors_written"] == 1
        assert stats["flow_gate_failures"] == {}
        row = session.query(SectorReturn).one()
        assert row.spot_net_1h == pytest.approx(600.0)
        assert row.spot_flow_tokens == 2
    finally:
        session.close()
