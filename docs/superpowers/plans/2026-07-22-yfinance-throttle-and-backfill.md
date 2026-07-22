# yfinance 限流治本 + 缺口回补 + 卡片源状态标注 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 回补 07-21 22:05 UTC 起的 yfinance 缺口；把服务器对 Yahoo 的请求降到不触发封禁（会话过滤+串行抖动）；数据源异常在市场概览卡片上显性标注。

**Architecture:** 三条工作流按序落地——① 两个一次性脚本（本机拉取 CSV → 服务器备份后幂等导入，复用 `PriceScanner._save_records`）；② 新模块 `scanners/market_sessions.py` 用交易所本地时区定义会话（zoneinfo，夏令时自动），`yfinance_source` 改逐品种串行+抖动+软预算；③ `/api/market/latest` 每项新增 `freshness`/`stale_minutes` 字段（后端判定），前端按字段渲染四态徽标。

**Tech Stack:** Python 3 (zoneinfo + tzdata)、SQLAlchemy/SQLite、pytest、FastAPI/pydantic、React+TS（types.ts 由 `scripts/generate_openapi_types.py` 自动生成，**严禁手改**）、vitest。

**Ground rules（每个 Task 都适用）：**
- 本地 python 一律 `D:\anaconda\python.exe`（PATH 里的 python 是坏 stub，exit 49）。
- 后端测试：`D:\anaconda\python.exe -m pytest tests/<file> -v`；全量回归 `D:\anaconda\python.exe -m pytest`。
- 前端命令都在 `frontend/` 目录下跑；`npm run typecheck` 会先重新生成 types.ts 再 tsc。
- **每次 commit 前**按 AGENTS.md 同步本地地图（ARCHITECTURE/DATAFLOW/DECISIONS/PENDING.md，gitignored 不入库）。
- 工作流一先行（今天恢复数据），二、三在同一分支序列上随后实施，最后一次性部署。
- 生产纪律：线上库写操作仅限 Task 3 的导入步骤，且必须先备份（VACUUM INTO + integrity_check，`Connection.backup()` 已被 tests/test_deploy_script.py 封杀）、先 dry-run 报数、用户确认后再真跑。

---

## 工作流一：缺口回补

### Task 1: CSV dump 共享模块 + 本机拉取脚本

**Files:**
- Create: `scripts/price_dump.py`（CSV 读写 + 行校验，两端共用）
- Create: `scripts/backfill_yfinance_local.py`（本机跑，拉数据写 CSV）
- Test: `tests/test_price_dump.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_price_dump.py
# -*- coding: utf-8 -*-
"""price_dump CSV 往返与行校验（回补脚本共享模块）。"""
from datetime import datetime
from pathlib import Path

import pytest

from scanners.base import PriceRecord
from scripts.price_dump import read_dump, write_dump, ValidationStats

REC = PriceRecord(
    asset_class="futures", symbol="NQ=F", name="纳指期货",
    price=20000.5, volume=123.0, source="yfinance",
    timestamp=datetime(2026, 7, 21, 22, 10),
)


def test_roundtrip(tmp_path: Path):
    p = tmp_path / "dump.csv"
    write_dump(p, [REC])
    rows, stats = read_dump(
        p,
        allowed_symbols={"NQ=F"},
        start=datetime(2026, 7, 21, 20, 0),
        end=datetime(2026, 7, 22, 12, 0),
    )
    assert stats == ValidationStats(total=1, kept=1, bad_symbol=0, bad_price=0, out_of_range=0)
    r = rows[0]
    assert (r.symbol, r.asset_class, r.name) == ("NQ=F", "futures", "纳指期货")
    assert r.price == 20000.5 and r.volume == 123.0
    assert r.timestamp == datetime(2026, 7, 21, 22, 10)
    assert r.source == "yfinance"
    assert r.prev_price is None and r.change_pct is None  # 链条由 _save_records 落库时衔接


def test_validation_drops_bad_rows(tmp_path: Path):
    p = tmp_path / "dump.csv"
    bad_symbol = PriceRecord(asset_class="futures", symbol="EVIL=F", name="x",
                             price=1.0, source="yfinance", timestamp=datetime(2026, 7, 21, 23, 0))
    bad_price = PriceRecord(asset_class="futures", symbol="NQ=F", name="纳指期货",
                            price=0.0, source="yfinance", timestamp=datetime(2026, 7, 21, 23, 0))
    out_of_range = PriceRecord(asset_class="futures", symbol="NQ=F", name="纳指期货",
                               price=1.0, source="yfinance", timestamp=datetime(2026, 7, 30, 0, 0))
    write_dump(p, [REC, bad_symbol, bad_price, out_of_range])
    rows, stats = read_dump(p, allowed_symbols={"NQ=F"},
                            start=datetime(2026, 7, 21, 20, 0), end=datetime(2026, 7, 22, 12, 0))
    assert [r.symbol for r in rows] == ["NQ=F"]
    assert stats == ValidationStats(total=4, kept=1, bad_symbol=1, bad_price=1, out_of_range=1)


def test_volume_none_roundtrip(tmp_path: Path):
    p = tmp_path / "dump.csv"
    rec = PriceRecord(asset_class="stock_index", symbol="^GSPC", name="标普500",
                      price=6000.0, volume=None, source="yfinance",
                      timestamp=datetime(2026, 7, 21, 22, 10))
    write_dump(p, [rec])
    rows, _ = read_dump(p, allowed_symbols={"^GSPC"},
                        start=datetime(2026, 7, 21, 0, 0), end=datetime(2026, 7, 22, 0, 0))
    assert rows[0].volume is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_price_dump.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'scripts.price_dump'`
（若报 `scripts` 不是包：`scripts/` 下无 `__init__.py`，本 Task Step 3 一并创建空的 `scripts/__init__.py`）

- [ ] **Step 3: 实现 `scripts/price_dump.py`**

```python
# -*- coding: utf-8 -*-
"""回补 CSV 的读写与校验：本机拉取端(write) 与服务器导入端(read) 共用一个口径。

列: symbol, timestamp_utc(ISO, naive UTC, bar_end), close, volume, asset_class, name
prev_price/change_pct 不进 CSV——落库时由 PriceScanner._save_records 按库内邻档自动衔接。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from scanners.base import PriceRecord

FIELDS = ["symbol", "timestamp_utc", "close", "volume", "asset_class", "name"]


@dataclass(frozen=True)
class ValidationStats:
    total: int = 0
    kept: int = 0
    bad_symbol: int = 0
    bad_price: int = 0
    out_of_range: int = 0


def write_dump(path: Path | str, records: list[PriceRecord]) -> int:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in records:
            w.writerow({
                "symbol": r.symbol,
                "timestamp_utc": r.timestamp.isoformat(timespec="seconds"),
                "close": repr(r.price),
                "volume": "" if r.volume is None else repr(r.volume),
                "asset_class": r.asset_class,
                "name": r.name,
            })
    return len(records)


def read_dump(path: Path | str, *, allowed_symbols: set[str],
              start: datetime, end: datetime) -> tuple[list[PriceRecord], ValidationStats]:
    """读 CSV → PriceRecord（source 固定 yfinance）；行校验：白名单/价格>0/时间窗内。"""
    rows: list[PriceRecord] = []
    total = bad_symbol = bad_price = out_of_range = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            total += 1
            if row["symbol"] not in allowed_symbols:
                bad_symbol += 1
                continue
            try:
                price = float(row["close"])
            except ValueError:
                price = float("nan")
            if not price > 0:          # NaN 也走这个分支
                bad_price += 1
                continue
            ts = datetime.fromisoformat(row["timestamp_utc"])
            if not (start <= ts <= end):
                out_of_range += 1
                continue
            volume = float(row["volume"]) if row["volume"] else None
            rows.append(PriceRecord(
                asset_class=row["asset_class"], symbol=row["symbol"], name=row["name"],
                price=price, volume=volume, source="yfinance", timestamp=ts,
            ))
    rows.sort(key=lambda r: (r.symbol, r.timestamp))
    return rows, ValidationStats(total=total, kept=len(rows), bad_symbol=bad_symbol,
                                 bad_price=bad_price, out_of_range=out_of_range)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_price_dump.py -v`
Expected: 3 passed

- [ ] **Step 5: 实现本机拉取脚本 `scripts/backfill_yfinance_local.py`（无单测，纯编排已测组件）**

```python
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
```

- [ ] **Step 6: 冒烟验证脚本可跑（拉一个短窗口）**

Run: `D:\anaconda\python.exe scripts/backfill_yfinance_local.py --start 2026-07-22T01:00 --end 2026-07-22T02:00 --out ../scratch_smoke.csv`
（写到仓库外，避免脏工作区；跑完手动删除）
Expected: 各品种行数打印（休市品种 0 bars 正常），`wrote N rows`

- [ ] **Step 7: Commit**

```bash
git add scripts/__init__.py scripts/price_dump.py scripts/backfill_yfinance_local.py tests/test_price_dump.py
git commit -m "feat(backfill): local yfinance gap puller + shared CSV dump module"
```

### Task 2: 服务器导入脚本（幂等，复用 _save_records）

**Files:**
- Create: `scripts/import_price_dump.py`
- Test: `tests/test_import_price_dump.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_import_price_dump.py
# -*- coding: utf-8 -*-
"""导入脚本核心：幂等落库、dry-run 不写、统计口径。"""
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.price import PriceSnapshot
from scanners.base import PriceRecord
from scripts.price_dump import write_dump
from scripts.import_price_dump import run_import

START = datetime(2026, 7, 21, 20, 0)
END = datetime(2026, 7, 22, 12, 0)


@pytest.fixture()
def session_factory():
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)


def _dump(tmp_path: Path) -> Path:
    p = tmp_path / "dump.csv"
    recs = [
        PriceRecord(asset_class="futures", symbol="NQ=F", name="纳指期货",
                    price=20000.0, source="yfinance", timestamp=datetime(2026, 7, 21, 22, 10)),
        PriceRecord(asset_class="futures", symbol="NQ=F", name="纳指期货",
                    price=20010.0, source="yfinance", timestamp=datetime(2026, 7, 21, 22, 15)),
    ]
    write_dump(p, recs)
    return p


def test_import_inserts_and_chains(session_factory, tmp_path, monkeypatch):
    import scanners.price_scanner as ps_module
    monkeypatch.setattr(ps_module, "get_session", session_factory)

    stats = run_import(_dump(tmp_path), allowed_symbols={"NQ=F"},
                       start=START, end=END, dry_run=False)
    assert stats["inserted"] == 2

    s = session_factory()
    rows = s.query(PriceSnapshot).order_by(PriceSnapshot.timestamp).all()
    assert len(rows) == 2
    assert rows[1].prev_price == 20000.0            # 链条衔接
    assert rows[1].change_pct == pytest.approx(0.05, abs=1e-6)
    assert all(r.source == "yfinance" for r in rows)
    s.close()


def test_import_is_idempotent(session_factory, tmp_path, monkeypatch):
    import scanners.price_scanner as ps_module
    monkeypatch.setattr(ps_module, "get_session", session_factory)

    p = _dump(tmp_path)
    first = run_import(p, allowed_symbols={"NQ=F"}, start=START, end=END, dry_run=False)
    second = run_import(p, allowed_symbols={"NQ=F"}, start=START, end=END, dry_run=False)
    assert first["inserted"] == 2 and second["inserted"] == 0

    s = session_factory()
    assert s.query(PriceSnapshot).count() == 2
    s.close()


def test_dry_run_writes_nothing(session_factory, tmp_path, monkeypatch):
    import scanners.price_scanner as ps_module
    monkeypatch.setattr(ps_module, "get_session", session_factory)

    stats = run_import(_dump(tmp_path), allowed_symbols={"NQ=F"},
                       start=START, end=END, dry_run=True)
    assert stats["would_insert"] == 2

    s = session_factory()
    assert s.query(PriceSnapshot).count() == 0
    s.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_import_price_dump.py -v`
Expected: FAIL，`No module named 'scripts.import_price_dump'`

- [ ] **Step 3: 实现 `scripts/import_price_dump.py`**

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_import_price_dump.py tests/test_price_dump.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/import_price_dump.py tests/test_import_price_dump.py
git commit -m "feat(backfill): idempotent server-side dump importer reusing _save_records"
```

### Task 3: 实操——拉取、备份、导入、验收（OPS，人工闸门）

**Files:** 无代码改动；产物 `data/yf_backfill.csv`（本地，不提交）

- [ ] **Step 1: 本机全量拉取缺口**

Run: `D:\anaconda\python.exe scripts/backfill_yfinance_local.py --start 2026-07-21T20:00 --end auto --out data/yf_backfill.csv`
Expected: 期货 7 品种各数百根；现指类按各自开市时段有数；`wrote N rows`（N 预计 2000~4000）

- [ ] **Step 2: 上传 CSV 到服务器**

Run: `scp -o BatchMode=yes data/yf_backfill.csv mmon:/tmp/yf_backfill.csv`

- [ ] **Step 3: 线上库备份（VACUUM INTO + 校验；沿用 deploy.sh 同款姿势）**

```bash
ssh -o BatchMode=yes mmon 'cd /opt/market_monitor && .venv/bin/python - market_monitor.db backups/pre_backfill_$(date -u +%Y%m%dT%H%M%SZ).db <<PY
import sqlite3, sys, os
src, dst = sys.argv[1], sys.argv[2]
con = sqlite3.connect(src)
con.execute("VACUUM INTO ?", (dst,))
con.close()
chk = sqlite3.connect(dst).execute("PRAGMA integrity_check").fetchone()[0]
assert chk == "ok", chk
print("backup ok:", dst, f"{os.path.getsize(dst)/1e6:.0f} MB")
PY'
```

Expected: `backup ok: backups/pre_backfill_<ts>.db ~315 MB`
然后拉回本地一份：`scp -o BatchMode=yes "mmon:/opt/market_monitor/backups/pre_backfill_*.db" D:\market_monitor\data\`

- [ ] **Step 4: dry-run 报数（⚠️ 到此暂停，把数字报给用户确认）**

Run: `ssh -o BatchMode=yes mmon "cd /opt/market_monitor && .venv/bin/python scripts/import_price_dump.py --dump /tmp/yf_backfill.csv --start 2026-07-21T20:00 --end <本次拉取的end,UTC ISO> --dry-run"`
Expected: `validation` 各计数 + `would_insert ≈ CSV 行数 −（重叠已有行数）`。**用户确认后才进 Step 5。**

- [ ] **Step 5: 真跑导入**

同 Step 4 去掉 `--dry-run`。Expected: `inserted == would_insert`（±新到的几根）

- [ ] **Step 6: 验收查询**

```bash
ssh -o BatchMode=yes mmon "cd /opt/market_monitor && .venv/bin/python - <<PY
import sqlite3
db = sqlite3.connect('file:market_monitor.db?mode=ro', uri=True)
for row in db.execute('''SELECT symbol, MAX(timestamp), COUNT(*) FROM price_snapshots
    WHERE source='yfinance' AND timestamp >= '2026-07-21 20:00'
    GROUP BY symbol ORDER BY symbol'''):
    print(row)
PY"
```

Expected: 期货 7 品种 MAX(timestamp) 接近导入 end；条数与 CSV 各品种行数吻合（本地 `python -c` 数 CSV 对照）。抽 NQ=F/GC=F/^GSPC 各 1 根 K 线与 CSV 对值。

- [ ] **Step 7: 清理 + 记录**

`ssh mmon "rm -f /tmp/yf_backfill.csv"`；PENDING.md 记录回补窗口与 inserted 数（本地地图，不提交）。
提醒：解封或治本上线后，同一对脚本再跑一次近端窗口收尾。

---

## 工作流二：治本改造

### Task 4: 交易时段表 `scanners/market_sessions.py`

**Files:**
- Create: `scanners/market_sessions.py`
- Modify: `requirements.txt`（加 `tzdata>=2024.1`，Windows 下 zoneinfo 必需；Linux 无害）
- Test: `tests/test_market_sessions.py`

- [ ] **Step 1: 写失败测试（夏令时边界是重点）**

```python
# tests/test_market_sessions.py
# -*- coding: utf-8 -*-
"""交易时段表：夏令时切换、周末、维护段、午休、+10min 尾巴、未知品种 fail-open。

所有断言时刻都是 naive UTC（与库内口径一致）。
2026 美国夏令时：03-08 开始（EDT, UTC-4），11-01 结束（EST, UTC-5）。
"""
from datetime import datetime

from scanners.market_sessions import active_symbols, is_open, should_fetch


# ---------- 美股现指：夏令时切换让同一 UTC 时刻翻转 ----------

def test_gspc_dst_spring_forward():
    # 03-06（周五, EST）: 14:35 UTC = 09:35 EST → 开市
    assert is_open("^GSPC", datetime(2026, 3, 6, 14, 35))
    # 03-09（周一, EDT）: 14:35 UTC = 10:35 EDT → 开市；13:25 UTC = 09:25 EDT → 未开
    assert is_open("^GSPC", datetime(2026, 3, 9, 14, 35))
    assert not is_open("^GSPC", datetime(2026, 3, 9, 13, 25))
    # 但 03-06 的 13:35 UTC = 08:35 EST → 未开（同一 UTC 钟点冬夏答案不同）
    assert not is_open("^GSPC", datetime(2026, 3, 6, 13, 35))


def test_gspc_dst_fall_back():
    # 10-30（周五, EDT）: 13:35 UTC = 09:35 EDT → 开市
    assert is_open("^GSPC", datetime(2026, 10, 30, 13, 35))
    # 11-02（周一, EST）: 13:35 UTC = 08:35 EST → 未开；14:35 UTC → 开市
    assert not is_open("^GSPC", datetime(2026, 11, 2, 13, 35))
    assert is_open("^GSPC", datetime(2026, 11, 2, 14, 35))


# ---------- CME 期货：周界 + 每日维护段（芝加哥时区） ----------

def test_cme_daily_maintenance_break():
    # 2026-07-22 是周三。16:30 CT = 21:30 UTC（CDT, UTC-5）→ 维护段，闭市
    assert not is_open("ES=F", datetime(2026, 7, 22, 21, 30))
    # 17:30 CT = 22:30 UTC → 重开
    assert is_open("ES=F", datetime(2026, 7, 22, 22, 30))
    # 维护段开始后 10 分钟内 should_fetch 仍为 True（收尾抓最后一根 bar）
    assert should_fetch("ES=F", datetime(2026, 7, 22, 21, 5))
    assert not should_fetch("ES=F", datetime(2026, 7, 22, 21, 30))


def test_cme_weekend():
    # 周六全天闭市（2026-07-25 周六 12:00 UTC）
    assert not is_open("NQ=F", datetime(2026, 7, 25, 12, 0))
    assert not should_fetch("NQ=F", datetime(2026, 7, 25, 12, 0))
    # 周日 17:05 CT = 22:05 UTC 重开
    assert is_open("NQ=F", datetime(2026, 7, 26, 22, 5))
    # 周五 15:55 CT = 20:55 UTC 仍开；16:05 CT 闭市
    assert is_open("CL=F", datetime(2026, 7, 24, 20, 55))
    assert not is_open("CL=F", datetime(2026, 7, 24, 21, 5))


# ---------- 亚洲现指：午休、尾巴；无夏令时 ----------

def test_n225_lunch_and_tail():
    # 2026-07-22 周三。11:45 JST = 02:45 UTC → 午休
    assert not is_open("^N225", datetime(2026, 7, 22, 2, 45))
    # 午休开始后 10min 内 should_fetch 抓收尾
    assert should_fetch("^N225", datetime(2026, 7, 22, 2, 35))
    # 12:35 JST = 03:35 UTC → 下午场
    assert is_open("^N225", datetime(2026, 7, 22, 3, 35))
    # 收盘 15:30 JST；15:35 仍 fetch，15:45 停止
    assert should_fetch("^N225", datetime(2026, 7, 22, 6, 35))
    assert not should_fetch("^N225", datetime(2026, 7, 22, 6, 45))


def test_cn_indices_sessions():
    # 上证 2026-07-22 周三 10:00 CST = 02:00 UTC → 开市
    assert is_open("000001.SS", datetime(2026, 7, 22, 2, 0))
    # 12:00 CST = 04:00 UTC → 午休
    assert not is_open("399001.SZ", datetime(2026, 7, 22, 4, 0))
    # 14:30 CST = 06:30 UTC → 下午场
    assert is_open("399006.SZ", datetime(2026, 7, 22, 6, 30))


def test_kospi_continuous():
    # KOSPI 无午休：12:00 KST = 03:00 UTC → 开市
    assert is_open("^KS11", datetime(2026, 7, 22, 3, 0))


# ---------- 美元指数 / 债券 / 加密 ----------

def test_dxy_daily_break_ny():
    # ICE 每日 17:00-18:00 ET 跳过。2026-07-22: 17:30 ET = 21:30 UTC → 闭
    assert not is_open("DX-Y.NYB", datetime(2026, 7, 22, 21, 30))
    assert is_open("DX-Y.NYB", datetime(2026, 7, 22, 22, 30))


def test_bonds_and_crypto():
    # 美债近 24h（周三 12:00 UTC 开）；日债东京时段；加密永远开
    assert is_open("US_10Y", datetime(2026, 7, 22, 12, 0))
    assert is_open("JP_10Y", datetime(2026, 7, 22, 2, 0))    # 11:00 JST
    assert not is_open("JP_10Y", datetime(2026, 7, 22, 12, 0))  # 21:00 JST
    assert is_open("BTC/USDT", datetime(2026, 7, 25, 3, 0))     # 周六也开
    assert is_open("QQQ-USDT-SWAP", datetime(2026, 7, 25, 3, 0))


# ---------- 未知品种 fail-open + 集合过滤 ----------

def test_unknown_symbol_fails_open():
    assert is_open("NEW=F", datetime(2026, 7, 25, 12, 0))   # 宁多拉勿漏拉


def test_active_symbols_filters():
    # 周六 12:00 UTC：期货/现指全闭，加密开
    now = datetime(2026, 7, 25, 12, 0)
    got = active_symbols(["ES=F", "^GSPC", "^N225", "BTC/USDT"], now)
    assert got == {"BTC/USDT"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_market_sessions.py -v`
Expected: FAIL，`No module named 'scanners.market_sessions'`

- [ ] **Step 3: 实现 `scanners/market_sessions.py`**

```python
# -*- coding: utf-8 -*-
"""交易时段表：按交易所本地时区定义会话，zoneinfo 换算，夏令时自动正确。

is_open(symbol, now_utc)      —— 严格"此刻开市吗"（卡片 freshness 判定用）
should_fetch(symbol, now_utc) —— is_open(now) or is_open(now-10min)：
                                  收盘/午休/维护开始后 10 分钟内仍拉一轮，抓最后一根已收盘 bar
active_symbols(symbols, now)  —— 过滤出应拉取的品种集合

节假日不建模（设计取舍，见 spec §4.1）：假日请求返回空数据，浪费可忽略。
债券会话为近似口径，仅影响卡片标注边缘时刻，不影响采集（cnbc 不限流）。
未知品种 fail-open（按开市处理并 warning 一次）：宁可多拉，不可静默漏拉。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from loguru import logger

FETCH_TAIL = timedelta(minutes=10)
_WARNED_UNKNOWN: set[str] = set()


@dataclass(frozen=True)
class DailySessions:
    """常规日内市场：周一~五若干个 (开, 收) 区间，交易所当地时间。"""
    tz: str
    spans: tuple[tuple[time, time], ...]

    def is_open(self, local: datetime) -> bool:
        if local.weekday() > 4:
            return False
        t = local.time()
        return any(start <= t < end for start, end in self.spans)


@dataclass(frozen=True)
class WeeklyNearRoundTheClock:
    """近 24h 市场：周 open_wd open_t 开 → 周 close_wd close_t 收，每日 break 段跳过。
    weekday: Monday=0 … Sunday=6。跨周界（如周日开）用周分钟索引环绕比较。"""
    tz: str
    open_wd: int
    open_t: time
    close_wd: int
    close_t: time
    break_start: time
    break_end: time

    def is_open(self, local: datetime) -> bool:
        idx = local.weekday() * 1440 + local.hour * 60 + local.minute
        open_idx = self.open_wd * 1440 + self.open_t.hour * 60 + self.open_t.minute
        close_idx = self.close_wd * 1440 + self.close_t.hour * 60 + self.close_t.minute
        if open_idx <= close_idx:
            in_span = open_idx <= idx < close_idx
        else:                     # 跨周界（周日开→周五收）
            in_span = idx >= open_idx or idx < close_idx
        if not in_span:
            return False
        return not (self.break_start <= local.time() < self.break_end)


@dataclass(frozen=True)
class AlwaysOpen:
    tz: str = "UTC"

    def is_open(self, local: datetime) -> bool:  # noqa: ARG002
        return True


_US_CASH = DailySessions(tz="America/New_York", spans=((time(9, 30), time(16, 0)),))
_CME = WeeklyNearRoundTheClock(tz="America/Chicago",
                               open_wd=6, open_t=time(17, 0), close_wd=4, close_t=time(16, 0),
                               break_start=time(16, 0), break_end=time(17, 0))
_ICE_NY = WeeklyNearRoundTheClock(tz="America/New_York",
                                  open_wd=6, open_t=time(18, 0), close_wd=4, close_t=time(17, 0),
                                  break_start=time(17, 0), break_end=time(18, 0))
_TOKYO_CASH = DailySessions(tz="Asia/Tokyo",
                            spans=((time(9, 0), time(11, 30)), (time(12, 30), time(15, 30))))
_SEOUL_CASH = DailySessions(tz="Asia/Seoul", spans=((time(9, 0), time(15, 30)),))
_CN_CASH = DailySessions(tz="Asia/Shanghai",
                         spans=((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0))))
_JGB = DailySessions(tz="Asia/Tokyo",
                     spans=((time(9, 0), time(11, 30)), (time(12, 30), time(15, 0))))
_ALWAYS = AlwaysOpen()

SYMBOL_RULES: dict[str, object] = {
    # 美股现指
    "^DJI": _US_CASH, "^IXIC": _US_CASH, "^GSPC": _US_CASH,
    # CME 期货 + 商品（NIY=F 是 CME 日经期货，跟 Globex 时段）
    "ES=F": _CME, "NQ=F": _CME, "YM=F": _CME, "NIY=F": _CME,
    "GC=F": _CME, "SI=F": _CME, "CL=F": _CME,
    # 美元指数（ICE，NY 锚定）
    "DX-Y.NYB": _ICE_NY,
    # 亚洲现指
    "^N225": _TOKYO_CASH, "^KS11": _SEOUL_CASH,
    "000001.SS": _CN_CASH, "399001.SZ": _CN_CASH, "399006.SZ": _CN_CASH,
    # 债券（近似口径，仅用于卡片标注）
    "US_10Y": _ICE_NY, "US_2Y": _ICE_NY, "US_SPREAD": _ICE_NY,
    "JP_10Y": _JGB, "JP_2Y": _JGB, "JP_SPREAD": _JGB,
}
# 加密与代理永续 24×7：按前缀/后缀匹配（BTC/USDT、*-USDT-SWAP）
_ALWAYS_SUFFIXES = ("/USDT", "-USDT-SWAP")


def _rule_for(symbol: str):
    rule = SYMBOL_RULES.get(symbol)
    if rule is not None:
        return rule
    if symbol.endswith(_ALWAYS_SUFFIXES):
        return _ALWAYS
    if symbol not in _WARNED_UNKNOWN:
        _WARNED_UNKNOWN.add(symbol)
        logger.warning(f"[MarketSessions] 未知品种 {symbol}，fail-open 按开市处理")
    return _ALWAYS


def is_open(symbol: str, now_utc: datetime) -> bool:
    """now_utc: naive UTC（库内口径）。"""
    rule = _rule_for(symbol)
    local = now_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(rule.tz))
    return rule.is_open(local.replace(tzinfo=None))


def should_fetch(symbol: str, now_utc: datetime) -> bool:
    return is_open(symbol, now_utc) or is_open(symbol, now_utc - FETCH_TAIL)


def active_symbols(symbols, now_utc: datetime) -> set[str]:
    return {s for s in symbols if should_fetch(s, now_utc)}
```

- [ ] **Step 4: requirements.txt 加 tzdata**

在 `# 工具库` 段落追加一行：

```
tzdata>=2024.1,<2027   # zoneinfo 时区数据（Windows 本地开发必需；Linux 冗余无害）
```

- [ ] **Step 5: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_market_sessions.py -v`
Expected: 全部 passed（若 Windows 报 `ZoneInfoNotFoundError`：`D:\anaconda\python.exe -m pip install tzdata` 后重跑）

- [ ] **Step 6: Commit**

```bash
git add scanners/market_sessions.py tests/test_market_sessions.py requirements.txt
git commit -m "feat(sessions): exchange-local trading session table (DST-safe via zoneinfo)"
```

### Task 5: yfinance 源改串行 + 抖动 + 软预算 + 会话过滤

**Files:**
- Modify: `scanners/sources/yfinance_source.py`（`fetch_history` 重写 + 新增 `active_tickers`）
- Modify: `config.py`（新增 4 个常量，加在 SYNC_MIN_LOOKBACK_HOURS 附近）
- Test: `tests/test_yfinance_serial_fetch.py`（新文件；旧 `test_yfinance_single_batch.py` 断言"单次批量下载"将过时，本 Task 一并改造）

- [ ] **Step 1: config.py 加常量（SYNC_MIN_LOOKBACK_HOURS 块之后）**

```python
# ── yfinance 请求整形（2026-07-22 治本：告别 16 并发突发；参数可环境变量覆盖）──
YF_REQUEST_TIMEOUT_SEC = int(os.getenv("YF_REQUEST_TIMEOUT_SEC", "10"))    # 单请求超时
YF_STAGE_BUDGET_SEC = int(os.getenv("YF_STAGE_BUDGET_SEC", "180"))         # 阶段软预算，保 5min 周期
YF_JITTER_MIN_SEC = float(os.getenv("YF_JITTER_MIN_SEC", "0.3"))           # 品种间随机抖动下限
YF_JITTER_MAX_SEC = float(os.getenv("YF_JITTER_MAX_SEC", "0.8"))           # 品种间随机抖动上限
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_yfinance_serial_fetch.py
# -*- coding: utf-8 -*-
"""yfinance 串行拉取：会话过滤、逐品种、抖动、软预算截断、单品失败隔离。"""
from datetime import datetime

import pandas as pd
import pytest

import scanners.sources.yfinance_source as yfs_module
from scanners.sources.yfinance_source import YFinancePriceSource

START = datetime(2026, 7, 22, 0, 0)
END = datetime(2026, 7, 22, 6, 0)   # 周三 06:00 UTC：亚洲收尾+期货在场


def _fake_df(symbol: str, price: float = 100.0) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp("2026-07-22 05:00", tz="UTC")])
    cols = pd.MultiIndex.from_product([["Close"], [symbol]])
    return pd.DataFrame([[price]], index=idx, columns=cols)


@pytest.fixture()
def src(monkeypatch):
    monkeypatch.setattr(yfs_module, "_sleep", lambda s: None)   # 测试不真睡
    return YFinancePriceSource()


def test_only_active_symbols_requested(src, monkeypatch):
    calls: list[str] = []

    def fake_download(tickers, **kwargs):
        calls.append(tickers[0])
        return _fake_df(tickers[0])

    monkeypatch.setattr(yfs_module.yf, "download", fake_download)
    monkeypatch.setattr(yfs_module.market_sessions, "should_fetch",
                        lambda sym, now: sym in {"ES=F", "GC=F"})
    records = src.fetch_history(START, END)
    assert sorted(calls) == ["ES=F", "GC=F"]                    # 串行逐品种，只拉活跃
    assert {r.symbol for r in records} == {"ES=F", "GC=F"}


def test_all_closed_no_http(src, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("closed round must not hit network")

    monkeypatch.setattr(yfs_module.yf, "download", boom)
    monkeypatch.setattr(yfs_module.market_sessions, "should_fetch", lambda s, n: False)
    assert src.fetch_history(START, END) == []


def test_one_symbol_failure_isolated(src, monkeypatch):
    def fake_download(tickers, **kwargs):
        if tickers[0] == "ES=F":
            raise RuntimeError("boom")
        return _fake_df(tickers[0])

    monkeypatch.setattr(yfs_module.yf, "download", fake_download)
    monkeypatch.setattr(yfs_module.market_sessions, "should_fetch",
                        lambda sym, now: sym in {"ES=F", "GC=F"})
    records = src.fetch_history(START, END)
    assert {r.symbol for r in records} == {"GC=F"}


def test_stage_budget_cuts_remaining(src, monkeypatch):
    fake_now = [0.0]

    def fake_monotonic():
        return fake_now[0]

    def fake_download(tickers, **kwargs):
        fake_now[0] += 200.0                                    # 每次下载耗 200s
        return _fake_df(tickers[0])

    monkeypatch.setattr(yfs_module, "_monotonic", fake_monotonic)
    monkeypatch.setattr(yfs_module.yf, "download", fake_download)
    monkeypatch.setattr(yfs_module.market_sessions, "should_fetch",
                        lambda sym, now: sym in {"ES=F", "GC=F", "CL=F"})
    records = src.fetch_history(START, END)
    # 第 1 个下载后已超 180s 预算 → 只完成 1 个
    assert len({r.symbol for r in records}) == 1


def test_download_kwargs(src, monkeypatch):
    seen: dict = {}

    def fake_download(tickers, **kwargs):
        seen.update(kwargs)
        return _fake_df(tickers[0])

    monkeypatch.setattr(yfs_module.yf, "download", fake_download)
    monkeypatch.setattr(yfs_module.market_sessions, "should_fetch",
                        lambda sym, now: sym == "ES=F")
    src.fetch_history(START, END)
    assert seen["interval"] == "5m" and seen["threads"] is False
    assert seen["timeout"] == 10 and seen["progress"] is False
```

- [ ] **Step 3: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_yfinance_serial_fetch.py -v`
Expected: FAIL（`_sleep`/`market_sessions` 属性不存在、fetch_history 仍是单次批量）

- [ ] **Step 4: 重写 `yfinance_source.py` 的 fetch_history**

模块头部新增导入与可注入原语：

```python
import random
import time as _time

from scanners import market_sessions

_sleep = _time.sleep          # 测试可注入
_monotonic = _time.monotonic  # 测试可注入
```

类内新增：

```python
    def active_tickers(self, now_utc: datetime) -> dict[str, tuple[str, str]]:
        """本轮应拉取的 symbol -> (asset_class, name)；供 fetch_history 与 PriceScanner 共用。"""
        return {s: meta for s, meta in self._all_tickers().items()
                if market_sessions.should_fetch(s, now_utc)}
```

`fetch_history` 整体替换为（保留原 tz 归一逻辑）：

```python
    def fetch_history(self, start_ts: datetime, end_ts: datetime) -> list[PriceRecord]:
        """逐品种串行拉取窗口内 5m 收盘价：会话过滤 + 抖动 + 单请求超时 + 阶段软预算。

        2026-07-22 治本改造：原 16 ticker 单次并发批量（threads=True）是 Yahoo 封 IP 的
        直接诱因；改串行后每轮只拉开市品种，全休市轮零请求。"""
        if start_ts.tzinfo is not None:
            start_ts = start_ts.astimezone(timezone.utc).replace(tzinfo=None)
        if end_ts.tzinfo is not None:
            end_ts = end_ts.astimezone(timezone.utc).replace(tzinfo=None)
        if start_ts >= end_ts:
            return []

        tickers = self.active_tickers(end_ts)
        if not tickers:
            return []

        records: list[PriceRecord] = []
        deadline = _monotonic() + config.YF_STAGE_BUDGET_SEC
        skipped: list[str] = []
        items = list(tickers.items())
        for i, (symbol, (asset_class, name)) in enumerate(items):
            if _monotonic() >= deadline:
                skipped = [s for s, _ in items[i:]]
                break
            try:
                df = yf.download(
                    [symbol],
                    start=start_ts.replace(tzinfo=timezone.utc),
                    end=end_ts.replace(tzinfo=timezone.utc),
                    interval=self.INTERVAL,
                    prepost=False,
                    auto_adjust=True,
                    progress=False,
                    threads=False,
                    session=self._session,
                    timeout=config.YF_REQUEST_TIMEOUT_SEC,
                )
                if df.empty:
                    continue
                close_series = self._close_series_for(df, symbol)
                records.extend(self._records_from_close_series(
                    asset_class=asset_class, symbol=symbol, name=name,
                    close_series=close_series, start_ts=start_ts, end_ts=end_ts))
            except Exception as e:
                logger.error(f"yfinance {symbol} 拉取失败: {type(e).__name__}: {e}")
            if i < len(items) - 1:
                _sleep(random.uniform(config.YF_JITTER_MIN_SEC, config.YF_JITTER_MAX_SEC))

        if skipped:
            logger.warning(f"yfinance 阶段超软预算({config.YF_STAGE_BUDGET_SEC}s)，"
                           f"本轮放弃 {len(skipped)} 品种: {', '.join(skipped)}（下一轮游标窗口自愈）")
        return records
```

- [ ] **Step 5: 改造过时的旧测试**

打开 `tests/test_yfinance_single_batch.py`：其"合并为一次 yf.download"断言与串行方案冲突。
保留其中仍成立的解析类测试（`_close_series_for` MultiIndex 兼容），删除/改写"单次批量调用"断言为
"逐品种调用"（可直接并入 `test_yfinance_serial_fetch.py` 后删除旧文件）。
`_all_tickers` 的 docstring 里"合并为一次 yf.download"一句同步改为"串行逐品种"。

- [ ] **Step 6: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_yfinance_serial_fetch.py tests/test_yfinance_session.py -v`
Expected: 全部 passed

- [ ] **Step 7: Commit**

```bash
git add scanners/sources/yfinance_source.py config.py tests/test_yfinance_serial_fetch.py tests/test_yfinance_single_batch.py
git commit -m "feat(yfinance): session-filtered serial fetch with jitter, timeout, stage budget"
```

### Task 6: PriceScanner 集成（窗口按活跃品种 + closed 轮次语义）

**Files:**
- Modify: `scanners/price_scanner.py:66-79`（yf_latest 限定活跃品种；全休市跳过）
- Modify: `services/scan_runtime.py:261-270`（`stage=="closed"` 不进 0 行告警）
- Test: `tests/test_cursor_sync.py`（追加 2 个用例）

- [ ] **Step 1: 写失败测试（追加到 test_cursor_sync.py 末尾）**

```python
# ---------- 会话过滤集成（2026-07-22 治本） ----------

def test_scan_window_uses_active_tickers_only(make_session, monkeypatch):
    """游标窗口只看活跃品种：休市品种的老游标不该把窗口拖长。"""
    Session = make_session
    s = Session()
    s.add_all([
        PriceSnapshot(timestamp=NOW - timedelta(hours=60), asset_class="asian_index",
                      symbol="^N225", name="日经225", price=40000.0, source="yfinance"),
        PriceSnapshot(timestamp=NOW - timedelta(minutes=10), asset_class="futures",
                      symbol="ES=F", name="S&P500期货", price=6000.0, source="yfinance"),
    ])
    s.commit(); s.close()

    monkeypatch.setattr(ps_module, "get_session", Session)
    scanner = PriceScanner()
    monkeypatch.setattr(scanner.yfinance, "active_tickers",
                        lambda now: {"ES=F": ("futures", "S&P500期货")})
    captured: dict = {}

    def fake_fetch(start_ts, end_ts):
        captured["start"] = start_ts
        return []

    monkeypatch.setattr(scanner.yfinance, "fetch_history", fake_fetch)
    monkeypatch.setattr(scanner.okx, "fetch_history", lambda *a: [])
    monkeypatch.setattr(scanner.cnbc_bonds, "fetch", lambda: [])
    scanner.scan()
    # 只看 ES=F（10min 前）→ 窗口应是 24h 地板，而不是被 ^N225 拖到 60h+
    assert captured["start"] >= captured_now_minus_25h(captured["start"])


def captured_now_minus_25h(start):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return now - timedelta(hours=25)


def test_scan_all_closed_records_closed_status(make_session, monkeypatch):
    Session = make_session
    monkeypatch.setattr(ps_module, "get_session", Session)
    scanner = PriceScanner()
    monkeypatch.setattr(scanner.yfinance, "active_tickers", lambda now: {})

    def boom(*a, **k):
        raise AssertionError("closed round must not fetch yfinance")

    monkeypatch.setattr(scanner.yfinance, "fetch_history", boom)
    monkeypatch.setattr(scanner.okx, "fetch_history", lambda *a: [])
    monkeypatch.setattr(scanner.cnbc_bonds, "fetch", lambda: [])
    scanner.scan()
    yf_status = [st for st in scanner.source_statuses if st.source == "yfinance"]
    assert len(yf_status) == 1 and yf_status[0].stage == "closed" and yf_status[0].ok
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_cursor_sync.py -v -k "active_tickers or all_closed"`
Expected: FAIL（scan 仍对全部 ticker 计算窗口、无 closed 状态）

- [ ] **Step 3: 改 `price_scanner.py` scan() 的 yfinance 段**

把原第 66-79 行的 yf 部分改为：

```python
        session = get_session()
        try:
            yf_active = self.yfinance.active_tickers(scan_time)
            yf_latest = _latest_by_symbol(session, list(yf_active)) if yf_active else {}
            okx_symbols = [f"{base}/USDT" for base in config.PRICE_SOURCES.get("crypto", {})]
            okx_symbols.extend(config.PRICE_SOURCES.get("perp_proxy", {}).values())
            okx_latest = _latest_by_symbol(session, okx_symbols)
        finally:
            session.close()
        okx_start = sync_window_start(okx_latest, scan_time,
                                      cap_hours=float(config.PRICE_BACKFILL_MAX_HOURS))

        # 1. yfinance: 只拉开市品种；全休市轮次记 closed 状态、零请求
        if yf_active:
            yf_start = sync_window_start(yf_latest, scan_time, cap_hours=self.yfinance.CAP_HOURS)
            inserted.extend(self._save_records(
                self._fetch_history_safe(self.yfinance, yf_start, scan_time), scan_time))
        else:
            logger.info("[PriceScanner] yfinance 全品种休市，本轮跳过")
            self.source_statuses.append(SourceFetchStatus(
                source=self.yfinance.name, ok=True, record_count=0, empty=True, stage="closed"))
```

头部导入补 `SourceFetchStatus`：`from scanners.base import BaseSource, PriceRecord, SourceFetchStatus, SourceHealthMixin`

- [ ] **Step 4: 改 `scan_runtime.py` 的 `_log_source_statuses`**

```python
def _log_source_statuses(source_statuses: dict[str, list[dict]]) -> None:
    for group, statuses in source_statuses.items():
        failed = [s for s in statuses if not s["ok"]]
        # stage=closed 是"全品种休市主动跳过"，不是 0 行异常，不进告警噪音
        empty = [s for s in statuses if s["ok"] and s["empty"] and s.get("stage") != "closed"]
        if failed:
            names = ", ".join(f"{s['source']} ({s['error']})" for s in failed)
            logger.warning("[ScanSource] {} failed: {}", group, names)
        if empty:
            names = ", ".join(s["source"] for s in empty)
            logger.info("[ScanSource] {} returned 0 rows: {}", group, names)
```

- [ ] **Step 5: 跑测试确认通过 + 全量回归**

Run: `D:\anaconda\python.exe -m pytest tests/test_cursor_sync.py -v` → 全 passed
Run: `D:\anaconda\python.exe -m pytest` → 全量 passed（gap_filler/save_records 等既有行为不受影响）

- [ ] **Step 6: 同步本地地图（ARCHITECTURE/DATAFLOW：yfinance 采集路径新增会话过滤节点）后 Commit**

```bash
git add scanners/price_scanner.py services/scan_runtime.py tests/test_cursor_sync.py
git commit -m "feat(scan): cursor window over active tickers; closed-round status semantics"
```

---

## 工作流三：卡片源状态标注

### Task 7: 后端 freshness 判定 + schema 字段

**Files:**
- Modify: `config.py`（FRESHNESS 两阈值，加在 YF_* 常量后）
- Modify: `schemas/market.py:8-18`（MarketLatestItem 两个新字段）
- Modify: `services/market_service.py`（get_latest_prices 计算 freshness）
- Test: `tests/test_market_freshness.py`

- [ ] **Step 1: config.py 加阈值**

```python
# ── 市场概览卡片 freshness 标注阈值（分钟）──
FRESHNESS_STALE_MINUTES = int(os.getenv("FRESHNESS_STALE_MINUTES", "15"))   # 开市中滞后→黄标
FRESHNESS_DOWN_MINUTES = int(os.getenv("FRESHNESS_DOWN_MINUTES", "60"))    # 开市中滞后→红标"源中断"
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_market_freshness.py
# -*- coding: utf-8 -*-
"""卡片 freshness 四态：live/stale/source_down/closed + 扫描报错直判。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.price import PriceSnapshot
import services.market_service as ms

NOW = datetime(2026, 7, 22, 6, 0, 0)


@pytest.fixture()
def session(monkeypatch):
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    monkeypatch.setattr(ms, "utc_now_naive", lambda: NOW)
    monkeypatch.setattr(ms, "_failed_price_scanner_names", lambda: set())
    yield s
    s.close()


def _snap(s, symbol, minutes_ago, source="yfinance", asset_class="futures"):
    s.add(PriceSnapshot(timestamp=NOW - timedelta(minutes=minutes_ago),
                        asset_class=asset_class, symbol=symbol, name=symbol,
                        price=100.0, source=source))
    s.commit()


def _item(resp, symbol):
    return next(i for i in resp.items if i.symbol == symbol)


def test_live_stale_down_by_lag(session, monkeypatch):
    monkeypatch.setattr(ms.market_sessions, "is_open", lambda sym, now: True)
    _snap(session, "ES=F", 5)      # ≤15 → live
    _snap(session, "NQ=F", 30)     # (15,60] → stale
    _snap(session, "GC=F", 90)     # >60 → source_down
    resp = ms.get_latest_prices(session)
    assert _item(resp, "ES=F").freshness == "live"
    nq = _item(resp, "NQ=F")
    assert nq.freshness == "stale" and nq.stale_minutes == 30
    gc = _item(resp, "GC=F")
    assert gc.freshness == "source_down" and gc.stale_minutes == 90


def test_closed_market_is_calm(session, monkeypatch):
    monkeypatch.setattr(ms.market_sessions, "is_open", lambda sym, now: False)
    _snap(session, "^GSPC", 600, asset_class="stock_index")
    resp = ms.get_latest_prices(session)
    item = _item(resp, "^GSPC")
    assert item.freshness == "closed" and item.stale_minutes is None


def test_scanner_error_forces_down_even_if_fresh(session, monkeypatch):
    monkeypatch.setattr(ms.market_sessions, "is_open", lambda sym, now: True)
    monkeypatch.setattr(ms, "_failed_price_scanner_names", lambda: {"yfinance"})
    _snap(session, "ES=F", 5)
    resp = ms.get_latest_prices(session)
    assert _item(resp, "ES=F").freshness == "source_down"


def test_okx_snapshot_maps_to_okx_scanner_name(session, monkeypatch):
    monkeypatch.setattr(ms.market_sessions, "is_open", lambda sym, now: True)
    monkeypatch.setattr(ms, "_failed_price_scanner_names", lambda: {"okx"})
    _snap(session, "BTC/USDT", 3, source="okx_swap_5m", asset_class="crypto")
    resp = ms.get_latest_prices(session)
    assert _item(resp, "BTC/USDT").freshness == "source_down"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_market_freshness.py -v`
Expected: FAIL（无 freshness 字段 / 无 _failed_price_scanner_names / 无 market_sessions 引用）

- [ ] **Step 4: schemas/market.py 加字段**

`MarketLatestItem` 末尾追加：

```python
    freshness: str = "live"              # live | stale | source_down | closed
    stale_minutes: int | None = None     # stale/source_down 时的滞后分钟数
```

- [ ] **Step 5: market_service.py 实现判定**

头部导入追加：

```python
from scanners import market_sessions
```

模块内新增两个函数（放在 get_latest_prices 上方）：

```python
def _failed_price_scanner_names() -> set[str]:
    """最近一轮扫描中报错（ok=False）的价格 scanner 名。单 worker 内存态，无扫描历史时为空。"""
    try:
        from services.scan_runtime import run_scan_once
        statuses = getattr(run_scan_once, "last_source_statuses", {}) or {}
        return {s["source"] for s in statuses.get("price", []) if not s.get("ok", True)}
    except Exception:
        return set()


_SNAPSHOT_SOURCE_TO_SCANNER = (
    ("yfinance", "yfinance"),
    ("okx", "okx"),                      # okx_swap_5m / okx_spot_5m / okx_gapfill*
    ("cnbc_bond_quote", "cnbc_bond_quote"),
)


def _freshness_for(symbol: str, snapshot_source: str, ts, now) -> tuple[str, int | None]:
    if ts is None:
        return "source_down", None
    if not market_sessions.is_open(symbol, now):
        return "closed", None
    lag_min = max(0, int((now - ts).total_seconds() // 60))
    scanner = next((sc for prefix, sc in _SNAPSHOT_SOURCE_TO_SCANNER
                    if snapshot_source.startswith(prefix)), None)
    if scanner and scanner in _failed_price_scanner_names():
        return "source_down", lag_min
    if lag_min <= config.FRESHNESS_STALE_MINUTES:
        return "live", None
    if lag_min <= config.FRESHNESS_DOWN_MINUTES:
        return "stale", lag_min
    return "source_down", lag_min
```

get_latest_prices 里 `items.append(MarketLatestItem(...))` 前插入并传参：

```python
        now = utc_now_naive()
        freshness, stale_minutes = _freshness_for(symbol, latest.source, latest.timestamp, now)
```

（`now` 取一次放循环外亦可；MarketLatestItem 调用追加 `freshness=freshness, stale_minutes=stale_minutes,`）

注意：`_freshness_for` 内查 `_failed_price_scanner_names()` 每 item 一次可接受（内存 set），
若在意可在 get_latest_prices 开头取一次传入。

- [ ] **Step 6: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_market_freshness.py -v`
Expected: 5 passed；再跑 `D:\anaconda\python.exe -m pytest tests/ -k market` 无回归

- [ ] **Step 7: Commit**

```bash
git add config.py schemas/market.py services/market_service.py tests/test_market_freshness.py
git commit -m "feat(api): per-card freshness field (live/stale/source_down/closed)"
```

### Task 8: 前端四态徽标

**Files:**
- Modify: `frontend/src/pages/MarketPage.tsx`（AssetCard 渲染徽标；`badge-proxy` 先例同款内联样式）
- Regenerate: `frontend/src/api/types.ts`（跑生成脚本，**不手改**）
- Test: `frontend/src/pages/MarketPage.test.tsx`（追加用例，沿用现有 mock 风格）

- [ ] **Step 1: 重新生成 types.ts**

在 `frontend/` 下 Run: `npm run typecheck`
Expected: types.ts 的 `MarketLatestItem` 出现 `freshness: string; stale_minutes: number | null;`；tsc 此时对 MarketPage 无报错（新字段尚未使用）

- [ ] **Step 2: 写失败组件测试（追加到 MarketPage.test.tsx，沿用文件内既有 render/mock 模式）**

```tsx
// —— freshness 徽标（沿用本文件既有的 api mock + render helper 写法）——
it("renders stale badge with minutes", async () => {
  // 在既有 mock 的 /market/latest 响应里，把某个 item 改为:
  // { ...item, freshness: "stale", stale_minutes: 23 }
  // 渲染后断言:
  expect(await screen.findByText("滞后 23 分钟")).toBeInTheDocument();
});

it("renders source_down badge", async () => {
  // freshness: "source_down", stale_minutes: 95
  expect(await screen.findByText("源中断")).toBeInTheDocument();
});

it("renders closed badge quietly and nothing when live", async () => {
  // freshness: "closed" → 「休市」徽标；freshness: "live" → 无任何徽标文案
  expect(await screen.findByText("休市")).toBeInTheDocument();
  expect(screen.queryByText("源中断")).not.toBeInTheDocument();
});
```

（具体 mock 组装照抄该测试文件现有用例的结构；断言文案以下方 Step 4 的 label 为准。）

- [ ] **Step 3: 跑测试确认失败**

在 `frontend/` 下 Run: `npm run test`
Expected: 新用例 FAIL（徽标未实现）

- [ ] **Step 4: 实现徽标渲染**

MarketPage.tsx 顶部加常量（classNames 定义附近）：

```tsx
const FRESHNESS_BADGES: Record<
  string,
  { label: (m: number | null) => string; title: string; color: string; bg: string; border: string } | undefined
> = {
  stale: {
    label: (m) => `滞后 ${m ?? "?"} 分钟`,
    title: "开市中但最新K线滞后，数据源可能被限流",
    color: "#d97706", bg: "rgba(217,119,6,0.14)", border: "1px solid rgba(217,119,6,0.35)"
  },
  source_down: {
    label: () => "源中断",
    title: "数据源持续无返回或报错，当前显示为最后可得旧价",
    color: "#dc2626", bg: "rgba(220,38,38,0.14)", border: "1px solid rgba(220,38,38,0.35)"
  },
  closed: {
    label: () => "休市",
    title: "该市场当前不在交易时段",
    color: "#94a3b8", bg: "rgba(148,163,184,0.12)", border: "1px solid rgba(148,163,184,0.25)"
  }
};

function FreshnessBadge({ item }: { item: MarketLatestItem }) {
  const spec = FRESHNESS_BADGES[item.freshness];
  if (!spec) return null;   // live 或未知值 → 无徽标
  return (
    <span
      className={`badge-freshness badge-${item.freshness}`}
      title={spec.title}
      style={{
        alignSelf: "flex-start", fontSize: 13, lineHeight: 1.4, padding: "1px 6px",
        borderRadius: 4, color: spec.color, background: spec.bg, border: spec.border
      }}
    >
      {spec.label(item.stale_minutes)}
    </span>
  );
}
```

卡片里 `<strong>{formatPrice(item)}</strong>` 之后、既有 gapfill 徽标之前插入：

```tsx
      <FreshnessBadge item={item} />
```

- [ ] **Step 5: 跑测试 + 类型检查确认通过**

在 `frontend/` 下 Run: `npm run test` → 全 passed；`npm run typecheck` → 无错误

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/MarketPage.tsx frontend/src/pages/MarketPage.test.tsx frontend/src/api/types.ts
git commit -m "feat(ui): market card freshness badges (stale/source_down/closed)"
```

### Task 9: 收尾——全量回归、地图同步、部署、线上观察

**Files:** 无新代码；本地地图 + PENDING.md（不入库）

- [ ] **Step 1: 全量回归**

Run: `D:\anaconda\python.exe -m pytest` → 全 passed
Run（frontend/）: `npm run test && npm run typecheck` → 全 passed

- [ ] **Step 2: 同步本地地图**

ARCHITECTURE.md（新模块 market_sessions、yfinance 串行路径、freshness 字段）、
DATAFLOW.md（采集流加会话过滤节点、/market/latest 加 freshness）、
DECISIONS.md（节假日不建模、fail-open、软预算 180s、阈值 15/60 的取舍）、
PENDING.md（回补已完成记录 + "解封后二次收尾回补"待办）。HTML 两份同步。

- [ ] **Step 3: 部署（用户已批准的标准流程）**

Run: `ssh -o BatchMode=yes mmon "cd /opt/market_monitor && ./deploy.sh"`
（deploy.sh 自带 VACUUM INTO 备份 → git pull → pip → npm build → restart）
Expected: 备份 ok、restart 完成

- [ ] **Step 4: 线上观察 2~3 轮（10~15 分钟）**

```bash
ssh -o BatchMode=yes mmon "journalctl -u market-monitor --since '-16 min' --no-pager | grep -E 'PriceScanner|ScanSource' | tail -n 30"
```

Expected: 只出现开市品种的拉取日志；休市轮 `yfinance 全品种休市，本轮跳过`；无新异常。
浏览器抽查 mmon.top 市场概览：休市品种灰标、被限流品种红标"源中断"（解封前正好是活体验收）。

- [ ] **Step 5: 解封探测与二次收尾（挂 PENDING 待办，非阻塞）**

探测（服务器）：`curl_cffi` Chrome 会话请求 chart 接口看状态码；解封后观察 24h 无 429 复发，
再跑一次 Task 3 的近端窗口回补收尾，卡片全绿后本事项关闭。

---

## Self-Review 记录

- **Spec 覆盖**：§3 回补→Task 1-3；§4 治本→Task 4-6；§5 标注→Task 7-8；§6 测试→各 Task 内嵌；§7 顺序/部署→Task 3+9；§9 验收→Task 3 Step 6、Task 9 Step 4-5。无缺口。
- **占位符扫描**：无 TBD/TODO；Task 8 Step 2 的测试骨架按既有 mock 风格补全属预期内适配（文件现有结构未在本计划内逐行复制，执行时照抄同文件用例）。
- **类型一致性**：`active_tickers` 命名在 Task 5（定义）与 Task 6（调用）一致；`should_fetch/is_open` 在 Task 4（定义）与 Task 5/7（调用）一致；config 常量名四处引用一致；`SourceFetchStatus.stage=="closed"` 在 Task 6 两处一致。
