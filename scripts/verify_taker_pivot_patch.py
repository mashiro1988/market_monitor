# -*- coding: utf-8 -*-
"""BMAC 宽表 taker 补丁的部署验收（2026-08-07 净资金流入 spec §5.1）。

跑在**数据服务器**上，用 BMAC 自己的 python（本脚本只依赖 pandas + 标准库）：

    python scripts/verify_taker_pivot_patch.py \
        --data-root /root/data_center/data --offset 30m --year 2026 \
        --backup /root/backup/market_pivot_spot_2026.pkl.bak

四项检查，全过才算补丁部署成功：
  1. 结构     两个新矩阵存在，且行索引/列集合与 close 完全一致
  2. 抽样勾稽 随机抽币 × 抽时点，宽表值 vs 单币原始 pkl 逐值核对
  3. 回归勾稽 与补丁前的备份宽表比，旧字段一个值都不许变
  4. 备用源   data_api 目录的单币文件是否也带这两个字段（评估容错触发概率）

退出码 0 = 全过；1 = 有 FAIL。
"""
from __future__ import annotations

import argparse
import pickle
import random
import sys
from pathlib import Path

import pandas as pd

QUOTE_VOLUME_KEY = "quote_volume"
TAKER_BUY_KEY = "taker_buy_quote_asset_volume"
LEGACY_KEYS_SPOT = ("open", "close", "vwap1m")
LEGACY_KEYS_SWAP = ("open", "close", "vwap1m", "funding_rate")
TOLERANCE = 1e-6

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def load_pickle(path: Path):
    with open(path, "rb") as fh:
        return pickle.load(fh)


def check_structure(pivot: dict, market: str) -> bool:
    close = pivot.get("close")
    if close is None:
        record(f"结构/{market}", False, "pivot 里没有 close")
        return False
    missing = [k for k in (QUOTE_VOLUME_KEY, TAKER_BUY_KEY) if k not in pivot]
    if missing:
        record(f"结构/{market}", False, f"缺新键 {missing}（补丁未生效）")
        return False
    for key in (QUOTE_VOLUME_KEY, TAKER_BUY_KEY):
        frame = pivot[key]
        if not frame.index.equals(close.index):
            record(f"结构/{market}", False, f"{key} 行索引与 close 不一致")
            return False
        if list(frame.columns) != list(close.columns):
            record(f"结构/{market}", False, f"{key} 列集合与 close 不一致")
            return False
    record(f"结构/{market}", True,
           f"{len(close.index)} 行 × {len(close.columns)} 列，两个新矩阵齐备")
    return True


def check_sampling(pivot: dict, per_symbol_dir: Path, market: str,
                   n_symbols: int, n_times: int) -> bool:
    close = pivot["close"]
    candidates = [c for c in close.columns if (per_symbol_dir / f"{c}.pkl").exists()]
    if not candidates:
        record(f"抽样勾稽/{market}", False, f"{per_symbol_dir} 下找不到任何单币文件")
        return False
    picked = random.sample(candidates, min(n_symbols, len(candidates)))
    mismatches: list[str] = []
    checked = 0
    for symbol in picked:
        df = load_pickle(per_symbol_dir / f"{symbol}.pkl")
        df = df.set_index(pd.DatetimeIndex(df["candle_begin_time"]))
        common = close.index.intersection(df.index)
        if len(common) == 0:
            mismatches.append(f"{symbol}: 与宽表无共同时点")
            continue
        for ts in random.sample(list(common), min(n_times, len(common))):
            for key, column in ((QUOTE_VOLUME_KEY, "quote_volume"),
                                (TAKER_BUY_KEY, TAKER_BUY_KEY)):
                wide = pivot[key].at[ts, symbol]
                raw = df.at[ts, column]
                checked += 1
                if pd.isna(wide) and pd.isna(raw):
                    continue
                if pd.isna(wide) != pd.isna(raw) or abs(float(wide) - float(raw)) > TOLERANCE:
                    mismatches.append(f"{symbol}@{ts} {key}: 宽表 {wide} vs 原始 {raw}")
    if mismatches:
        record(f"抽样勾稽/{market}", False,
               f"{len(mismatches)} 处不一致，前 3 条：{mismatches[:3]}")
        return False
    record(f"抽样勾稽/{market}", True, f"{len(picked)} 个币 × 共 {checked} 个值全对上")
    return True


def check_regression(pivot: dict, backup_path: Path, market: str) -> bool:
    if not backup_path.exists():
        record(f"回归勾稽/{market}", False, f"备份不存在: {backup_path}")
        return False
    old = load_pickle(backup_path)
    legacy = LEGACY_KEYS_SWAP if market == "swap" else LEGACY_KEYS_SPOT
    for key in legacy:
        if key not in old:
            continue
        if key not in pivot:
            record(f"回归勾稽/{market}", False, f"补丁后丢了旧键 {key}")
            return False
        old_df, new_df = old[key], pivot[key]
        rows = old_df.index.intersection(new_df.index)
        cols = [c for c in old_df.columns if c in set(new_df.columns)]
        if len(rows) == 0 or not cols:
            record(f"回归勾稽/{market}", False, f"{key} 与备份无重叠区间，无法比对")
            return False
        a, b = old_df.loc[rows, cols], new_df.loc[rows, cols]
        diff = ((a - b).abs() > TOLERANCE) & a.notna() & b.notna()
        n_diff = int(diff.to_numpy().sum())
        if n_diff:
            record(f"回归勾稽/{market}", False, f"{key} 有 {n_diff} 个格子被改动")
            return False
    record(f"回归勾稽/{market}", True, f"旧字段 {legacy} 在重叠区间逐值未变")
    return True


def check_data_api(data_root: Path, offset: str, market: str, n: int) -> bool:
    api_dir = data_root / f"data_api_{market}_1h_resample" / offset
    if not api_dir.exists():
        record(f"备用源/{market}", True, f"{api_dir} 不存在（未启用备用源，无风险）")
        return True
    files = sorted(api_dir.glob("*USDT.pkl"))
    if not files:
        record(f"备用源/{market}", True, "备用源目录为空（无风险）")
        return True
    missing = []
    for path in random.sample(files, min(n, len(files))):
        columns = set(load_pickle(path).columns)
        if not {"quote_volume", TAKER_BUY_KEY} <= columns:
            missing.append(path.name)
    if missing:
        record(f"备用源/{market}", False,
               f"{len(missing)}/{min(n, len(files))} 个备用源文件缺字段（缺列容错会被触发）："
               f"{missing[:3]}")
        return False
    record(f"备用源/{market}", True, f"抽查 {min(n, len(files))} 个备用源文件字段齐备")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="BMAC taker 宽表补丁验收")
    parser.add_argument("--data-root", default="/root/data_center/data")
    parser.add_argument("--offset", default="30m")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--markets", default="spot,swap")
    parser.add_argument("--backup", default=None,
                        help="补丁前的 spot 宽表备份路径（swap 备份按同目录同名规则推断）")
    parser.add_argument("--symbols", type=int, default=10, help="抽样币数")
    parser.add_argument("--times", type=int, default=50, help="每个币抽的时点数")
    parser.add_argument("--seed", type=int, default=20260807)
    args = parser.parse_args()

    random.seed(args.seed)
    data_root = Path(args.data_root)

    for market in [m.strip() for m in args.markets.split(",") if m.strip()]:
        pivot_path = (data_root / "preprocess_1h_resample" / args.offset
                      / f"market_pivot_{market}_{args.year}.pkl")
        if not pivot_path.exists():
            record(f"结构/{market}", False, f"宽表不存在: {pivot_path}")
            continue
        pivot = load_pickle(pivot_path)

        if not check_structure(pivot, market):
            continue
        check_sampling(pivot, data_root / f"binance_{market}_1h_resample" / args.offset,
                       market, args.symbols, args.times)
        if args.backup:
            backup = Path(args.backup)
            if market == "swap":
                backup = backup.parent / backup.name.replace("_spot_", "_swap_")
            check_regression(pivot, backup, market)
        else:
            record(f"回归勾稽/{market}", False, "未传 --backup，无法证明旧数未变")
        check_data_api(data_root, args.offset, market, args.symbols)

    failed = [name for name, ok, _ in results if not ok]
    print("\n" + "=" * 60)
    if failed:
        print(f"验收未通过：{len(failed)}/{len(results)} 项 FAIL → {failed}")
        return 1
    print(f"验收通过：{len(results)}/{len(results)} 项全过，补丁可以留在服务器上。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
