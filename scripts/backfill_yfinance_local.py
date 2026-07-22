# -*- coding: utf-8 -*-
"""本机（住宅网络）拉取 yfinance 5m 缺口数据 → CSV。

用法（在仓库根目录）:
  D:\\anaconda\\python.exe scripts/backfill_yfinance_local.py ^
      --start 2026-07-21T20:00 --end auto --out data/yf_backfill.csv

坑位备忘：
- 必须显式 start/end（tz-aware UTC）。period="1d" 姿势对期货返回 0~1 根（2026-07-22 实证）。
- 逐品种串行 + ~1s 间隔；本机 16 次请求对 Yahoo 可忽略。
- 复用 YFinancePriceSource 的解析/收盘 bar 语义，不自造轮子。
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 仓库根

import yfinance as yf  # noqa: E402

from scanners.sources.yfinance_source import YFinancePriceSource  # noqa: E402
from scripts.price_dump import write_dump  # noqa: E402


def parse_utc(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="UTC ISO，如 2026-07-21T20:00")
    ap.add_argument("--end", default="auto", help="UTC ISO 或 auto=当前时刻")
    ap.add_argument("--out", default="data/yf_backfill.csv")
    args = ap.parse_args()

    start = parse_utc(args.start)
    end = datetime.now(timezone.utc) if args.end == "auto" else parse_utc(args.end)
    start_naive = start.replace(tzinfo=None)
    end_naive = end.replace(tzinfo=None)
    print(f"window UTC: {start_naive} -> {end_naive}")

    src = YFinancePriceSource()          # 只借 symbol 表与解析逻辑；本机无 curl_cffi 也可跑
    tickers = src._all_tickers()
    all_records = []
    failed: list[str] = []
    for i, (symbol, (asset_class, name)) in enumerate(tickers.items()):
        try:
            df = yf.download([symbol], start=start, end=end, interval="5m",
                             prepost=False, auto_adjust=True, progress=False, threads=False)
            if df.empty:
                print(f"  {symbol:12s} 0 bars (empty)")
                failed.append(symbol)
                continue
            close = src._close_series_for(df, symbol)
            recs = src._records_from_close_series(
                asset_class=asset_class, symbol=symbol, name=name,
                close_series=close, start_ts=start_naive, end_ts=end_naive)
            print(f"  {symbol:12s} {len(recs)} bars")
            all_records.extend(recs)
        except Exception as e:  # 单品种失败不中断
            print(f"  {symbol:12s} FAILED {type(e).__name__}: {e}")
            failed.append(symbol)
        if i < len(tickers) - 1:
            time.sleep(random.uniform(0.8, 1.5))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = write_dump(out, all_records)
    print(f"\nwrote {n} rows -> {out}")
    if failed:
        print(f"failed symbols ({len(failed)}): {', '.join(failed)}")
    return 1 if failed and not all_records else 0


if __name__ == "__main__":
    raise SystemExit(main())
