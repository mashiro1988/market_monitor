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


def _perturb_backup_close(root: Path, market: str, row_positions: list[int], n_cols: int):
    """把备份里指定行的 close 挪动 0.05%，模拟"备份写入时这几根还没收盘"。"""
    path = root / "backup" / f"market_pivot_{market}_2026.pkl"
    with open(path, "rb") as fh:
        backup = pickle.load(fh)
    close = backup["close"]
    for pos in row_positions:
        ts = close.index[pos]
        for sym in list(close.columns)[:n_cols]:
            close.at[ts, sym] = close.at[ts, sym] * 1.0005
    _dump(backup, path)


def test_last_bar_only_diff_is_exempted_as_unsettled_candle(tmp_path):
    """差异全落在重叠区最后一根 bar → 判 PASS 并说明是收盘补全。

    2026-08-08 线上实证：全量重建后，备份里那根卡在收盘边界上的 K 线被补全，
    close 变了（现货 1 格、永续 113 格），open 一格没变。这不是补丁改了历史数据，
    校验器不该为此判 FAIL、逼人每次手工排查。
    """
    _make_fixture(tmp_path, "none")
    for market, n_cols in (("spot", 1), ("swap", 3)):
        _perturb_backup_close(tmp_path, market, [-1], n_cols)

    code, stdout = _run(tmp_path)
    checks = _checks(stdout)

    assert code == 0, stdout
    assert all(checks.values()), checks
    assert "豁免" in stdout, f"豁免了就要说清楚为什么，否则等于偷偷放水：\n{stdout}"
    assert "未收盘" in stdout, stdout


def test_diff_beyond_last_bar_still_fails(tmp_path):
    """差异只要多出一根 bar 就不再豁免 —— 豁免口径必须是最窄的那一档。"""
    _make_fixture(tmp_path, "none")
    for market in ("spot", "swap"):
        _perturb_backup_close(tmp_path, market, [-1, -2], 2)

    code, stdout = _run(tmp_path)
    checks = _checks(stdout)

    assert code == 1, f"倒数第二根也变了就不是收盘补全，必须判 FAIL\n{stdout}"
    failed = {name for name, ok in checks.items() if not ok}
    assert failed == {"回归勾稽/spot", "回归勾稽/swap"}, failed
    assert "需人工排查" in stdout, stdout


def test_cross_offset_backup_fails_with_actionable_hint(tmp_path):
    """拿别的 offset 的备份来比：必须判 FAIL，且要点破成因。

    2026-08-08 线上实证：用 10m 的新宽表比 30m 的备份，两者 bar 边界不同
    （:10 vs :30）、时间戳交集恒为零。校验器拒绝比对是对的，但当时只说
    「无重叠区间」，让人误以为数据坏了 —— 提示里必须带上 offset 这条线索。
    """
    _make_fixture(tmp_path, "none")
    # 把备份的时间戳整体移到每小时 :30，模拟"另一个 offset 的备份"
    for market in ("spot", "swap"):
        path = tmp_path / "backup" / f"market_pivot_{market}_2026.pkl"
        with open(path, "rb") as fh:
            backup = pickle.load(fh)
        shifted = {k: v.set_axis(v.index + pd.Timedelta(minutes=30), axis=0)
                   for k, v in backup.items()}
        _dump(shifted, path)

    code, stdout = _run(tmp_path)
    checks = _checks(stdout)

    assert code == 1, stdout
    failed = {name for name, ok in checks.items() if not ok}
    assert failed == {"回归勾稽/spot", "回归勾稽/swap"}, f"只该回归勾稽失败，实际: {failed}"
    # 结构与抽样不该被连累 —— 数据本身是好的
    assert checks["结构/spot"] and checks["抽样勾稽/spot"], stdout
    # 提示必须点破成因，而不是只说"无重叠区间"
    assert "offset" in stdout, f"提示里没提 offset，用户无从下手：\n{stdout}"
    assert ":30" in stdout and ":00" in stdout, f"提示里没给出两边的分钟数：\n{stdout}"


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
