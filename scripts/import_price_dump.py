# -*- coding: utf-8 -*-
"""服务器端导入回补 CSV → price_snapshots（幂等）。

用法（在 /opt/market_monitor，服务可继续运行，WAL+busy_timeout 容忍并发）:
  .venv/bin/python scripts/import_price_dump.py --dump /tmp/yf_backfill.csv \
      --start 2026-07-21T20:00 --end 2026-07-22T12:00 --dry-run
  确认数字后去掉 --dry-run 真跑。

写路径 100% 复用 PriceScanner._save_records：幂等跳重、prev/change 链条、
真实覆盖 gapfill 合成点，与扫描器同构。前置纪律：先 VACUUM INTO 备份（见实施计划 Task 3）。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.price_dump import read_dump  # noqa: E402


def _allowed_symbols() -> set[str]:
    from scanners.sources.yfinance_source import YFinancePriceSource
    return set(YFinancePriceSource()._all_tickers())


def run_import(dump_path: Path | str, *, allowed_symbols: set[str],
               start: datetime, end: datetime, dry_run: bool) -> dict:
    from scanners.price_scanner import PriceScanner, get_session

    rows, stats = read_dump(dump_path, allowed_symbols=allowed_symbols, start=start, end=end)
    out = {"validation": stats, "rows": len(rows)}

    if dry_run:
        session = get_session()
        try:
            from models.price import PriceSnapshot
            would = 0
            by_symbol: dict[str, list] = {}
            for r in rows:
                by_symbol.setdefault(r.symbol, []).append(r)
            for symbol, rs in by_symbol.items():
                existing = {
                    ts for (ts,) in session.query(PriceSnapshot.timestamp).filter(
                        PriceSnapshot.symbol == symbol,
                        PriceSnapshot.timestamp >= rs[0].timestamp,
                        PriceSnapshot.timestamp <= rs[-1].timestamp,
                    )
                }
                would += sum(1 for r in rs if r.timestamp not in existing)
            out["would_insert"] = would
        finally:
            session.close()
        return out

    scanner = PriceScanner()             # __init__ 无网络副作用（okx/cnbc/yf 均惰性）
    inserted = scanner._save_records(rows, scan_time=end)
    out["inserted"] = len(inserted)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)

    result = run_import(args.dump, allowed_symbols=_allowed_symbols(),
                        start=start, end=end, dry_run=args.dry_run)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
