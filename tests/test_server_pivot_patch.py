"""服务器端 BMAC 宽表补丁的行为测试（2026-08-07 净资金流入 spec §4）。

这段代码要跑在数据服务器上，而那台服务器的宽表同时喂着用户的量化交易框架 ——
改坏了不只是本项目的资金流没了，交易框架也会断粮。所以补丁上服务器前，
先在本地把它的行为钉死。

scripts/server_src/preprocess.py 是服务器文件的留档镜像，依赖 BMAC 内部模块
（core.utils.log_kit 等），本地 import 不了。这里用 ast 把 PIVOT_COLUMNS 与
make_market_pivot 单独抠出来 exec —— 测的就是将要贴到服务器上的那段源码本身。
"""
import ast
from pathlib import Path

import pandas as pd
import pytest

MIRROR = Path(__file__).resolve().parents[1] / "scripts" / "server_src" / "preprocess.py"

QUOTE_VOLUME_KEY = "quote_volume"
TAKER_BUY_KEY = "taker_buy_quote_asset_volume"


@pytest.fixture(scope="module")
def make_market_pivot():
    """从镜像源码里抠出 PIVOT_COLUMNS + make_market_pivot 并 exec。"""
    tree = ast.parse(MIRROR.read_text(encoding="utf-8"))
    wanted = [
        node for node in tree.body
        if (isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == "PIVOT_COLUMNS" for t in node.targets))
        or (isinstance(node, ast.FunctionDef) and node.name == "make_market_pivot")
    ]
    assert len(wanted) == 2, f"镜像里没找到 PIVOT_COLUMNS 和 make_market_pivot：{wanted}"
    namespace: dict = {"pd": pd}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(MIRROR), "exec"), namespace)
    return namespace["make_market_pivot"], namespace["PIVOT_COLUMNS"]


def _symbol_frame(symbol: str, *, n=3, with_taker=True):
    index = pd.date_range("2026-08-01", periods=n, freq="h", tz="UTC")
    data = {
        "candle_begin_time": index,
        "symbol": symbol,
        "open": [10.0] * n,
        "close": [11.0] * n,
        "avg_price_1m": [10.5] * n,
        "funding_fee": [0.0001] * n,
        "quote_volume": [1000.0 * (i + 1) for i in range(n)],
    }
    if with_taker:
        data[TAKER_BUY_KEY] = [600.0 * (i + 1) for i in range(n)]
    return pd.DataFrame(data)


def test_pivot_columns_include_the_two_flow_fields(make_market_pivot):
    _, columns = make_market_pivot
    assert "quote_volume" in columns
    assert TAKER_BUY_KEY in columns
    # 旧列一个都不许少（交易框架在吃这些）
    for legacy in ("candle_begin_time", "symbol", "open", "close",
                   "avg_price_1m", "funding_fee"):
        assert legacy in columns


def test_spot_pivot_gains_flow_matrices_without_losing_legacy_keys(make_market_pivot):
    fn, _ = make_market_pivot
    result = fn({"AUSDT": _symbol_frame("AUSDT"), "BUSDT": _symbol_frame("BUSDT")}, "spot")

    assert set(result) == {"open", "close", "vwap1m", QUOTE_VOLUME_KEY, TAKER_BUY_KEY}
    assert result[QUOTE_VOLUME_KEY].shape == result["close"].shape
    assert list(result[QUOTE_VOLUME_KEY].columns) == list(result["close"].columns)
    assert result[QUOTE_VOLUME_KEY].index.equals(result["close"].index)
    assert result[QUOTE_VOLUME_KEY].iloc[0]["AUSDT"] == pytest.approx(1000.0)
    assert result[TAKER_BUY_KEY].iloc[2]["BUSDT"] == pytest.approx(1800.0)


def test_swap_pivot_keeps_funding_rate_alongside_flow_matrices(make_market_pivot):
    fn, _ = make_market_pivot
    result = fn({"AUSDT": _symbol_frame("AUSDT")}, "swap")

    assert set(result) == {"open", "close", "vwap1m", "funding_rate",
                           QUOTE_VOLUME_KEY, TAKER_BUY_KEY}
    assert result["funding_rate"].iloc[0]["AUSDT"] == pytest.approx(0.0001)


def test_symbol_missing_taker_column_yields_nan_instead_of_crashing(make_market_pivot):
    """data_api 备用源个别文件缺 taker 字段时，绝不能让整轮预处理崩掉。

    预处理停产 = 交易框架断粮，代价远大于少一个币的资金流。
    """
    fn, _ = make_market_pivot
    result = fn({
        "GOODUSDT": _symbol_frame("GOODUSDT"),
        "OLDUSDT": _symbol_frame("OLDUSDT", with_taker=False),
    }, "spot")

    assert result[TAKER_BUY_KEY]["OLDUSDT"].isna().all(), "缺字段的币应为 NaN"
    assert result[TAKER_BUY_KEY]["GOODUSDT"].notna().all(), "正常币不该被连累"
    # 价格字段完全不受影响 —— 交易框架照常有数
    assert result["close"]["OLDUSDT"].notna().all()


def test_identity_holds_on_generated_matrices(make_market_pivot):
    """产出的两个矩阵必须满足 0 <= 主动买入额 <= 总成交额（本项目勾稽门就查这个）。"""
    fn, _ = make_market_pivot
    result = fn({"AUSDT": _symbol_frame("AUSDT"), "BUSDT": _symbol_frame("BUSDT")}, "spot")
    qv, tb = result[QUOTE_VOLUME_KEY], result[TAKER_BUY_KEY]
    assert (tb >= 0).all().all()
    assert (tb <= qv).all().all()
