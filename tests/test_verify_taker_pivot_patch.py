"""验收脚本自己的验收（2026-08-07 净资金流入）。

拿一个没验过的工具去验服务器补丁等于没验。这里造一个假的「数据服务器目录」
（目录结构 / 文件命名 / DataFrame 列名都照抄真实服务器），分别注入五种坏法，
断言 verify_taker_pivot_patch.py 每次都恰好抓到该抓的那一项、且不误伤其它项。
"""
import pickle
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_taker_pivot_patch.py"

N_ROWS, N_SYMS = 60, 8
SYMS = [f"SYM{i}USDT" for i in range(N_SYMS)]


def _dump(obj, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump(obj, fh)


def _build_market(market: str, break_mode: str, rng, index):
    frames = {
        key: pd.DataFrame(index=index, columns=SYMS, dtype=float)
        for key in ("open", "close", "vwap1m", "quote_volume",
                    "taker_buy_quote_asset_volume")
    }
    per_symbol = {}
    for sym in SYMS:
        close = rng.uniform(10, 200, N_ROWS)
        qv = rng.uniform(1e5, 1e7, N_ROWS)
        tb = qv * rng.uniform(0.3, 0.7, N_ROWS)
        per_symbol[sym] = pd.DataFrame({
            "candle_begin_time": index,
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": qv / close,
            "quote_volume": qv,
            "trade_num": rng.integers(100, 9999, N_ROWS),
            "taker_buy_base_asset_volume": tb / close,
            "taker_buy_quote_asset_volume": tb,
        })
        frames["open"][sym] = close * 0.99
        frames["close"][sym] = close
        frames["vwap1m"][sym] = close * 0.99
        frames["quote_volume"][sym] = qv
        frames["taker_buy_quote_asset_volume"][sym] = tb

    patched = dict(frames)
    if market == "swap":
        patched["funding_rate"] = pd.DataFrame(0.0, index=index, columns=SYMS)
    backup = {k: v.copy() for k, v in patched.items()
              if k not in ("quote_volume", "taker_buy_quote_asset_volume")}

    if break_mode == "missing_key":
        patched.pop("taker_buy_quote_asset_volume")
    elif break_mode == "sampling":
        patched["quote_volume"] = patched["quote_volume"] * 2
    elif break_mode == "regression":
        patched["close"] = patched["close"] + 1.0
    elif break_mode == "misaligned":
        patched["quote_volume"] = patched["quote_volume"].iloc[1:]

    return patched, backup, per_symbol


def _make_fixture(root: Path, break_mode: str):
    rng = np.random.default_rng(7)
    index = pd.date_range("2026-08-01", periods=N_ROWS, freq="h", tz="UTC")
    for market in ("spot", "swap"):
        patched, backup, per_symbol = _build_market(market, break_mode, rng, index)
        _dump(patched, root / "preprocess_1h_resample" / "30m"
              / f"market_pivot_{market}_2026.pkl")
        _dump(backup, root / "backup" / f"market_pivot_{market}_2026.pkl")
        for sym, df in per_symbol.items():
            _dump(df, root / f"binance_{market}_1h_resample" / "30m" / f"{sym}.pkl")
            api_df = (df.drop(columns=["taker_buy_quote_asset_volume"])
                      if break_mode == "data_api" else df)
            _dump(api_df, root / f"data_api_{market}_1h_resample" / "30m" / f"{sym}.pkl")


def _run(root: Path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--year", "2026",
         "--data-root", str(root),
         "--backup", str(root / "backup" / "market_pivot_spot_2026.pkl"),
         "--symbols", "4", "--times", "10"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout


def _checks(stdout: str) -> dict[str, bool]:
    """解析输出 → {检查名: 是否 PASS}。"""
    out = {}
    for line in stdout.splitlines():
        if line.startswith("[PASS] ") or line.startswith("[FAIL] "):
            name = line[7:].split(" — ")[0].strip()
            out[name] = line.startswith("[PASS]")
    return out


def test_clean_fixture_passes_every_check(tmp_path):
    _make_fixture(tmp_path, "none")
    code, stdout = _run(tmp_path)
    checks = _checks(stdout)
    assert code == 0, stdout
    assert checks and all(checks.values()), checks
    # 四类检查 × 两个市场都跑到了
    for market in ("spot", "swap"):
        for kind in ("结构", "抽样勾稽", "回归勾稽", "备用源"):
            assert f"{kind}/{market}" in checks, f"没跑 {kind}/{market}"


@pytest.mark.parametrize("break_mode,expect_failed", [
    ("missing_key", "结构"),
    ("misaligned", "结构"),
    ("sampling", "抽样勾稽"),
    ("regression", "回归勾稽"),
    ("data_api", "备用源"),
])
def test_each_break_mode_trips_exactly_its_own_check(tmp_path, break_mode, expect_failed):
    _make_fixture(tmp_path, break_mode)
    code, stdout = _run(tmp_path)
    checks = _checks(stdout)

    assert code == 1, f"{break_mode} 应判 FAIL\n{stdout}"
    failed = {name for name, ok in checks.items() if not ok}
    assert failed, stdout
    # 该抓的抓到了
    assert all(name.startswith(expect_failed) for name in failed), (
        f"{break_mode} 应只让「{expect_failed}」失败，实际失败: {failed}")
    # 且两个市场都抓到（坏法是对称注入的）
    assert len(failed) == 2, f"{break_mode} 应两个市场都失败，实际: {failed}"


def test_missing_backup_is_reported_not_silently_skipped(tmp_path):
    """没给备份就无法证明旧数未变 —— 必须判 FAIL，不能装作通过。"""
    _make_fixture(tmp_path, "none")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--year", "2026",
         "--data-root", str(tmp_path), "--symbols", "2", "--times", "5"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 1
    checks = _checks(proc.stdout)
    assert checks["回归勾稽/spot"] is False
    assert checks["回归勾稽/swap"] is False
