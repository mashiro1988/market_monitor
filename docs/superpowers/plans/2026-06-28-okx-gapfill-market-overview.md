# OKX 永续休市补点（市场概览 gap-fill）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 市场概览页的 `NQ=F`/`CL=F`/`GC=F` 在 yfinance 数据停更（休市）时段，用 OKX 已有永续 `QQQ/CL/XAU-USDT-SWAP` 经"比率锚定"补出与期货同量级的连续点，真实数据恢复时覆盖回真实值，前端用阴影带标出代理时段。

**Architecture:** 复用现有 `OkxPriceSource`（零新数据源）取 perp 已收盘价；新 `GapFiller` 编排判休市/锚点/比率/防呆/写库；锚点持久化在新 `gapfill_anchor` 表；真实优先靠 `_save_records` 的 source-aware UPDATE 覆盖；前端单线不变 + `ReferenceArea` 阴影带区分。

**Tech Stack:** Python 3 / SQLAlchemy / pytest（后端，用 `D:\anaconda\python.exe -m pytest`）；React + recharts / vitest（前端，用 `npx vitest run`）。

**Spec:** [docs/superpowers/specs/2026-06-28-okx-gapfill-market-overview-design.md](../specs/2026-06-28-okx-gapfill-market-overview-design.md)（v3，前端方案 B 已选）

**约定（贯穿全计划）**
- python 解释器固定 `D:\anaconda\python.exe`（PATH stub 会 exit 49）。
- 所有改动在分支 `feat/onchain-market-overview` 上，每个 Task 末尾提交。
- 不静默改用户校准配置：本计划只**新增** gap-fill 配置项，不改既有阈值。
- 哨兵来源字符串后端一律引用 `config.GAPFILL_SOURCE`，前端用 `types.ts` 镜像常量。

---

## 文件结构（先锁分解）

| 文件 | 职责 | 动作 |
|---|---|---|
| `config.py` | gap-fill 映射与阈值常量 | 修改 |
| `models/gapfill_anchor.py` | `GapfillAnchor` 锚点表 | 新建 |
| `models/__init__.py` | 注册新模型（否则不建表） | 修改 |
| `scanners/sources/okx_source.py` | `PerpBar` + `fetch_instrument_bars`（取任意 instId 已收盘价，不碰 `_make_record`） | 修改 |
| `scanners/gap_filler.py` | `GapFiller`：判休市/锚点/比率/防呆/写合成点 | 新建 |
| `scanners/price_scanner.py` | `_save_records` source-aware 覆盖；`scan()` 接入 GapFiller | 修改 |
| `schemas/market.py` | `MarketHistoryPoint.source` | 修改 |
| `services/market_service.py` | `get_history` 透出 source | 修改 |
| `frontend/src/api/types.ts` | `MarketHistoryPoint.source` + `OKX_GAPFILL_SOURCE` 常量 | 修改 |
| `frontend/src/components/Charts.tsx` | 可选 `shadedBands` → `ReferenceArea` | 修改 |
| `frontend/src/pages/MarketPage.tsx` | 从 source 推阴影带 + 卡片"代理价"角标 | 修改 |
| `tests/test_gap_filler.py` | GapFiller 单测 | 新建 |
| `tests/test_save_records_overwrite.py` | `_save_records` 覆盖单测 | 新建 |
| `tests/test_market_history.py` | get_history 透出 source（扩） | 修改 |
| `frontend/src/components/Charts.test.tsx` / `MarketPage` 测试 | 阴影带/角标（扩或新建） | 修改 |

---

## Task 1: 配置常量 + GapfillAnchor 模型 + 注册建表

**Files:**
- Modify: `config.py`（在价格数据源配置区附近）
- Create: `models/gapfill_anchor.py`
- Modify: `models/__init__.py`
- Test: `tests/test_gap_filler.py`

- [ ] **Step 1: 写失败测试（建表 + upsert 锚点）**

```python
# tests/test_gap_filler.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from database import Base
import models  # noqa: F401  注册模型

@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()

def test_gapfill_anchor_table_created_and_upsertable(session):
    from models.gapfill_anchor import GapfillAnchor
    assert "gapfill_anchor" in inspect(session.get_bind()).get_table_names()
    session.add(GapfillAnchor(symbol="NQ=F", real_ts=datetime(2026,6,26,21,0),
                              real_close=22000.0, perp_price=706.0))
    session.commit()
    row = session.get(GapfillAnchor, "NQ=F")
    assert row.real_close == 22000.0 and row.perp_price == 706.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_gap_filler.py::test_gapfill_anchor_table_created_and_upsertable -v`
Expected: FAIL（`ModuleNotFoundError: models.gapfill_anchor` 或 no such table）

- [ ] **Step 3: 建模型**

```python
# models/gapfill_anchor.py
"""休市补点锚点：每品种一行，记录最后一根真实 bar 与同刻 perp 价，用于比率锚定。"""
from sqlalchemy import Column, String, Float, DateTime
from datetime import datetime
from database import Base


class GapfillAnchor(Base):
    __tablename__ = "gapfill_anchor"

    symbol = Column(String(30), primary_key=True)   # "NQ=F"
    real_ts = Column(DateTime, nullable=False)        # 最后真实 bar 的 timestamp(UTC naive)
    real_close = Column(Float, nullable=False)
    perp_price = Column(Float, nullable=False)        # bar_end 对齐的 perp close
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

- [ ] **Step 4: 注册模型（关键——否则 create_all 不建表）**

`models/__init__.py` 在其它 import 后加一行：
```python
from models.gapfill_anchor import GapfillAnchor
```

- [ ] **Step 5: 加配置常量**

`config.py` 在 `PRICE_SOURCES` 定义之后加：
```python
# ============================================================
# 休市补点（gap-fill）：休市时段用 OKX 永续代理价补连续点
# 详见 docs/superpowers/specs/2026-06-28-okx-gapfill-market-overview-design.md
# ============================================================
ONCHAIN_GAPFILL = {
    "NQ=F": {"okx_inst": "QQQ-USDT-SWAP"},   # 纳指100：QQQ ETF 永续（同底层指数）
    "CL=F": {"okx_inst": "CL-USDT-SWAP"},    # WTI 原油
    "GC=F": {"okx_inst": "XAU-USDT-SWAP"},   # 现货黄金
}
GAPFILL_SOURCE = "okx_gapfill"   # 合成点 source 哨兵；后端一律引用本常量
GAPFILL_ENABLED = os.getenv("GAPFILL_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
GAPFILL_STALENESS_MINUTES = int(os.getenv("GAPFILL_STALENESS_MINUTES", "60"))   # 真实 bar 超此分钟数判休市
GAPFILL_PERP_FRESH_MINUTES = int(os.getenv("GAPFILL_PERP_FRESH_MINUTES", "12")) # perp 自身新鲜度
GAPFILL_STEP_PCT = float(os.getenv("GAPFILL_STEP_PCT", "0.05"))   # 单根 5m 跳变上限（抓坏价）
GAPFILL_SEAM_PCT = float(os.getenv("GAPFILL_SEAM_PCT", "0.15"))   # 补点段首点 seam 上限（抓坏锚点）
```

- [ ] **Step 6: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_gap_filler.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add config.py models/gapfill_anchor.py models/__init__.py tests/test_gap_filler.py
git commit -m "feat(gapfill): 锚点表 + 配置常量 + 模型注册"
```

---

## Task 2: OKX `fetch_instrument_bars`（取任意 instId 已收盘价）

**Files:**
- Modify: `scanners/sources/okx_source.py`
- Test: `tests/test_gap_filler.py`（追加）

- [ ] **Step 1: 写失败测试（mock `_fetch_candles`，验证解析为升序 PerpBar、不产 crypto 记录）**

```python
# tests/test_gap_filler.py 追加
def test_fetch_instrument_bars_parses_closed_bars_ascending(monkeypatch):
    from scanners.sources.okx_source import OkxPriceSource, PerpBar
    src = OkxPriceSource.__new__(OkxPriceSource)          # 绕过 __init__（不建真 exchange）
    src.proxy = ""
    monkeypatch.setattr(src, "_make_exchange", lambda: object())
    # OKX candle: [ts(start,ms), o,h,l,c, vol, volCcy, volCcyQuote, confirm]；newest-first
    canned = {
        "QQQ-USDT-SWAP": [
            ["1782700200000","705","707","704","706","10","0","0","1"],   # 较新
            ["1782699900000","704","706","703","705","9","0","0","1"],    # 较旧
        ],
        "XAU-USDT-SWAP": [["1782700200000","4085","4090","4080","4088","1","0","0","1"]],
    }
    monkeypatch.setattr(src, "_fetch_candles",
                        lambda exchange, inst_id, limit=12: canned.get(inst_id, []))
    out = src.fetch_instrument_bars(["QQQ-USDT-SWAP", "XAU-USDT-SWAP"])
    assert isinstance(out["QQQ-USDT-SWAP"][0], PerpBar)
    closes = [b.close for b in out["QQQ-USDT-SWAP"]]
    assert closes == [705.0, 706.0]                       # 升序（旧→新）
    assert out["XAU-USDT-SWAP"][-1].close == 4088.0
    # bar_end = start + 5min
    from datetime import datetime, timezone
    assert out["XAU-USDT-SWAP"][-1].bar_end == datetime.utcfromtimestamp(1782700200000/1000 + 300)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_gap_filler.py::test_fetch_instrument_bars_parses_closed_bars_ascending -v`
Expected: FAIL（`cannot import name 'PerpBar'` / `no attribute fetch_instrument_bars`）

- [ ] **Step 3: 实现 `PerpBar` + `fetch_instrument_bars`**

`scanners/sources/okx_source.py` 顶部加 `from typing import NamedTuple`，并在 import 后加：
```python
class PerpBar(NamedTuple):
    bar_end: datetime   # UTC naive，5m bar 收盘时刻
    close: float
```
在 `OkxPriceSource` 内加方法（复用 `_fetch_candles` + `_closed_candle_points`，**不调用 `_make_record`**）：
```python
def fetch_instrument_bars(self, inst_ids: list[str], limit: int = 12) -> dict[str, list[PerpBar]]:
    """取若干 instId 的已收盘 5m bar（升序）。供 GapFiller 用；返回原始 (bar_end, close)，
    不构造 crypto PriceRecord。一次建 exchange、循环复用。"""
    out: dict[str, list[PerpBar]] = {inst: [] for inst in inst_ids}
    try:
        exchange = self._make_exchange()
    except Exception as e:
        logger.error(f"[OKX] gapfill 初始化交易所失败: {type(e).__name__}: {e}")
        return out
    for inst_id in inst_ids:
        try:
            candles = self._fetch_candles(exchange, inst_id, limit=limit)
            pts = self._closed_candle_points(candles)   # (start_ms, bar_end, close, vol)，newest-first
            out[inst_id] = sorted(
                (PerpBar(bar_end=p[1], close=p[2]) for p in pts),
                key=lambda b: b.bar_end,
            )
        except Exception as e:
            logger.error(f"[OKX] gapfill 取 {inst_id} 失败: {type(e).__name__}: {e}")
            out[inst_id] = []
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_gap_filler.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scanners/sources/okx_source.py tests/test_gap_filler.py
git commit -m "feat(gapfill): OKX fetch_instrument_bars 取任意 instId 已收盘价"
```

---

## Task 3: GapFiller — 真实快照查询 + 锚点更新（live 路径，不补点）

**Files:**
- Create: `scanners/gap_filler.py`
- Test: `tests/test_gap_filler.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_gap_filler.py 追加
from datetime import datetime, timedelta
from models.price import PriceSnapshot
from models.gapfill_anchor import GapfillAnchor
from scanners.sources.okx_source import PerpBar

NOW = datetime(2026, 6, 26, 21, 0)   # 周五交易时段

class FakeOkx:
    def __init__(self, bars): self._bars = bars
    def fetch_instrument_bars(self, inst_ids, limit=12): return self._bars

def _real(session, symbol, ts, price, source="yfinance", asset_class="futures", name=None):
    session.add(PriceSnapshot(timestamp=ts, asset_class=asset_class, symbol=symbol,
                              name=name or symbol, price=price, source=source))

def test_live_updates_anchor_no_fill(session, monkeypatch):
    monkeypatch.setitem(__import__("config").ONCHAIN_GAPFILL, "NQ=F", {"okx_inst": "QQQ-USDT-SWAP"})
    _real(session, "NQ=F", NOW, 22000.0)        # 新鲜真实 bar
    session.commit()
    bars = {"QQQ-USDT-SWAP": [PerpBar(bar_end=NOW, close=706.0)]}   # bar_end 对齐
    from scanners.gap_filler import GapFiller
    written = GapFiller().run(session, FakeOkx(bars), NOW + timedelta(minutes=1))
    assert written == 0                          # live 不补
    a = session.get(GapfillAnchor, "NQ=F")
    assert a is not None and a.real_close == 22000.0 and a.perp_price == 706.0

def test_future_dated_real_row_ignored_for_latest(session):
    _real(session, "NQ=F", NOW, 22000.0)
    _real(session, "NQ=F", NOW + timedelta(minutes=10), 99999.0)    # 未来戳 fallback 行
    session.commit()
    from scanners.gap_filler import GapFiller
    real = GapFiller()._latest_real(session, "NQ=F", NOW + timedelta(minutes=1))
    assert real.price == 22000.0                 # 未来戳行不被选为 latest
```
> 注：`ONCHAIN_GAPFILL` 仅含 NQ=F 时其它两品种映射也会被遍历；测试用 `monkeypatch.setattr(config, "ONCHAIN_GAPFILL", {...})` 将其整体替换为单品种以隔离。把上面 `setitem` 改为 `monkeypatch.setattr(config, "ONCHAIN_GAPFILL", {"NQ=F": {"okx_inst": "QQQ-USDT-SWAP"}})`。

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_gap_filler.py -k "live or future_dated" -v`
Expected: FAIL（`No module named scanners.gap_filler`）

- [ ] **Step 3: 实现 GapFiller（live 路径 + 查询 + 锚点）**

```python
# scanners/gap_filler.py
"""休市补点编排：判休市、维护锚点、按比率合成 perp 代理价写库。

设计见 docs/superpowers/specs/2026-06-28-okx-gapfill-market-overview-design.md。
run() 显式接收 session（依赖注入，便于测试）；perp 取价复用 OkxPriceSource。"""
from datetime import datetime, timedelta
from loguru import logger
from sqlalchemy.orm import Session
import config
from models.price import PriceSnapshot
from models.gapfill_anchor import GapfillAnchor


class GapFiller:
    def __init__(self):
        self.mapping = config.ONCHAIN_GAPFILL
        self.source = config.GAPFILL_SOURCE
        self.staleness = timedelta(minutes=config.GAPFILL_STALENESS_MINUTES)
        self.perp_fresh = timedelta(minutes=config.GAPFILL_PERP_FRESH_MINUTES)
        self.step_pct = config.GAPFILL_STEP_PCT
        self.seam_pct = config.GAPFILL_SEAM_PCT

    def run(self, session: Session, okx_source, scan_time: datetime) -> int:
        if not config.GAPFILL_ENABLED or config.GAPFILL_STALENESS_MINUTES <= 0:
            return 0
        mapping = self.mapping
        if not mapping:
            return 0
        bars = okx_source.fetch_instrument_bars([m["okx_inst"] for m in mapping.values()])
        written = 0
        for symbol, m in mapping.items():
            try:
                written += self._handle(session, symbol, m["okx_inst"], bars.get(m["okx_inst"]) or [], scan_time)
            except Exception as e:
                logger.error(f"[GapFiller] {symbol} 失败: {type(e).__name__}: {e}")
        session.commit()
        return written

    def _latest_real(self, session: Session, symbol: str, scan_time: datetime):
        """最近真实快照：排除合成 source、排除未来戳，按时间降序取一。"""
        return (
            session.query(PriceSnapshot)
            .filter(
                PriceSnapshot.symbol == symbol,
                ~PriceSnapshot.source.like(f"{self.source}%"),
                PriceSnapshot.timestamp <= scan_time,
            )
            .order_by(PriceSnapshot.timestamp.desc())
            .first()
        )

    @staticmethod
    def _perp_at(bars, ts: datetime):
        """取 bar_end == ts 的 perp close；命中失败时在 ±5min 内取最近一根。"""
        exact = [b for b in bars if b.bar_end == ts]
        if exact:
            return exact[0].close
        near = [b for b in bars if abs((b.bar_end - ts).total_seconds()) <= 300]
        if near:
            return min(near, key=lambda b: abs((b.bar_end - ts).total_seconds())).close
        return None

    def _handle(self, session, symbol, inst_id, bars, scan_time) -> int:
        fresh = [b for b in bars if scan_time - b.bar_end <= self.perp_fresh]
        if not fresh:
            logger.warning(f"[GapFiller] {symbol} perp {inst_id} 无新鲜 bar，跳过")
            return 0
        latest = fresh[-1]
        real = self._latest_real(session, symbol, scan_time)
        if real is None:
            return 0
        if scan_time - real.timestamp <= self.staleness:
            self._maybe_update_anchor(session, symbol, real, bars)   # live：维护锚点，不补
            return 0
        return self._fill(session, symbol, latest, real)             # 休市：补点

    def _maybe_update_anchor(self, session, symbol, real, bars):
        anchor = session.get(GapfillAnchor, symbol)
        if anchor is not None and real.timestamp <= anchor.real_ts:
            return                                                   # 真实 bar 未推进
        perp = self._perp_at(bars, real.timestamp)
        if perp is None:
            if anchor is not None and (datetime.utcnow() - anchor.updated_at) > timedelta(minutes=30):
                logger.warning(f"[GapFiller] {symbol} 锚点超 30min 未对齐更新")
            return
        if anchor is None:
            session.add(GapfillAnchor(symbol=symbol, real_ts=real.timestamp,
                                      real_close=real.price, perp_price=perp))
        else:
            anchor.real_ts = real.timestamp
            anchor.real_close = real.price
            anchor.perp_price = perp

    def _fill(self, session, symbol, latest, real) -> int:
        # Task 4 实现
        return 0
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_gap_filler.py -k "live or future_dated" -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scanners/gap_filler.py tests/test_gap_filler.py
git commit -m "feat(gapfill): GapFiller live 路径——真实快照查询 + 锚点维护"
```

---

## Task 4: GapFiller — 休市补点（比率 + 步进/seam 防呆 + 写合成点）

**Files:**
- Modify: `scanners/gap_filler.py`（`_fill`）
- Test: `tests/test_gap_filler.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_gap_filler.py 追加
def _setup_single(monkeypatch):
    import config
    monkeypatch.setattr(config, "ONCHAIN_GAPFILL", {"NQ=F": {"okx_inst": "QQQ-USDT-SWAP"}})

def test_fill_writes_synthetic_at_futures_magnitude(session, monkeypatch):
    _setup_single(monkeypatch)
    fri_close = NOW
    _real(session, "NQ=F", fri_close, 22000.0)                  # 周五最后真实
    session.add(GapfillAnchor(symbol="NQ=F", real_ts=fri_close, real_close=22000.0, perp_price=706.0))
    session.commit()
    gap_time = fri_close + timedelta(hours=10)                  # 远超 60min → 休市
    bars = {"QQQ-USDT-SWAP": [PerpBar(bar_end=gap_time, close=710.0)]}
    from scanners.gap_filler import GapFiller
    written = GapFiller().run(session, FakeOkx(bars), gap_time + timedelta(minutes=1))
    assert written == 1
    syn = session.query(PriceSnapshot).filter_by(symbol="NQ=F", timestamp=gap_time).one()
    assert syn.source == "okx_gapfill"
    assert syn.asset_class == "futures"
    assert syn.price == pytest.approx(710.0 * 22000.0 / 706.0, rel=1e-6)   # ≈22124

def test_fill_skipped_without_anchor(session, monkeypatch):
    _setup_single(monkeypatch)
    _real(session, "NQ=F", NOW, 22000.0)
    session.commit()
    bars = {"QQQ-USDT-SWAP": [PerpBar(bar_end=NOW + timedelta(hours=10), close=710.0)]}
    from scanners.gap_filler import GapFiller
    assert GapFiller().run(session, FakeOkx(bars), NOW + timedelta(hours=10, minutes=1)) == 0

def test_step_guard_rejects_single_jump_but_allows_gradual(session, monkeypatch):
    _setup_single(monkeypatch)
    fri = NOW
    _real(session, "NQ=F", fri, 22000.0)
    session.add(GapfillAnchor(symbol="NQ=F", real_ts=fri, real_close=22000.0, perp_price=706.0))
    # 已有一根合成点在 t1（22124），下一根 t2 若 perp 暴跳 → 单步 >5% 应拒
    t1 = fri + timedelta(hours=10)
    session.add(PriceSnapshot(timestamp=t1, asset_class="futures", symbol="NQ=F",
                              name="NQ=F", price=22124.0, source="okx_gapfill"))
    session.commit()
    t2 = t1 + timedelta(minutes=5)
    bad = {"QQQ-USDT-SWAP": [PerpBar(bar_end=t2, close=800.0)]}   # 800/706*22000≈24924，单步 +12%
    from scanners.gap_filler import GapFiller
    assert GapFiller().run(session, FakeOkx(bad), t2 + timedelta(minutes=1)) == 0
    assert session.query(PriceSnapshot).filter_by(symbol="NQ=F", timestamp=t2).first() is None

def test_perp_stale_skipped(session, monkeypatch):
    _setup_single(monkeypatch)
    _real(session, "NQ=F", NOW, 22000.0)
    session.add(GapfillAnchor(symbol="NQ=F", real_ts=NOW, real_close=22000.0, perp_price=706.0))
    session.commit()
    scan = NOW + timedelta(hours=10)
    stale = {"QQQ-USDT-SWAP": [PerpBar(bar_end=scan - timedelta(hours=2), close=710.0)]}  # perp 自身 2h 旧
    from scanners.gap_filler import GapFiller
    assert GapFiller().run(session, FakeOkx(stale), scan) == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_gap_filler.py -k fill or step or perp_stale -v`
Expected: FAIL（`_fill` 返回 0，断言不满足）

- [ ] **Step 3: 实现 `_fill`**

替换 Task 3 里的占位 `_fill`：
```python
def _fill(self, session, symbol, latest, real) -> int:
    anchor = session.get(GapfillAnchor, symbol)
    if anchor is None or not anchor.perp_price:
        return 0
    # 同槽已有（含上一轮合成或回补真实）→ 不重复写、不覆盖
    if session.query(PriceSnapshot).filter_by(symbol=symbol, timestamp=latest.bar_end).first() is not None:
        return 0
    synthetic = latest.close * (anchor.real_close / anchor.perp_price)
    prev = (
        session.query(PriceSnapshot)
        .filter(PriceSnapshot.symbol == symbol, PriceSnapshot.timestamp < latest.bar_end)
        .order_by(PriceSnapshot.timestamp.desc())
        .first()
    )
    prev_price = prev.price if prev else None
    # 步进防呆：单根 5m 相对上一点跳变 > STEP_PCT → 坏价，跳过
    if prev_price and abs(synthetic / prev_price - 1) > self.step_pct:
        logger.warning(f"[GapFiller] {symbol} 合成单步跳变过大({synthetic:.2f} vs {prev_price:.2f})，跳过")
        return 0
    # 首点 seam 防呆：补点段第一根（上一点是真实）应≈最近真实收盘
    if (prev is None or not prev.source.startswith(self.source)) and real.price:
        if abs(synthetic / real.price - 1) > self.seam_pct:
            logger.warning(f"[GapFiller] {symbol} 补点首点 seam 过大，疑似坏锚点，跳过")
            return 0
    change_pct = ((synthetic - prev_price) / prev_price * 100) if prev_price else None
    session.add(PriceSnapshot(
        timestamp=latest.bar_end, asset_class=real.asset_class, symbol=symbol,
        name=real.name, price=synthetic, prev_price=prev_price,
        change_pct=change_pct, source=self.source,
    ))
    return 1
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_gap_filler.py -v`
Expected: PASS（全部）

- [ ] **Step 5: 提交**

```bash
git add scanners/gap_filler.py tests/test_gap_filler.py
git commit -m "feat(gapfill): 休市补点——比率合成 + 步进/seam 防呆"
```

---

## Task 5: `_save_records` source-aware 覆盖（真实优先，blocker 修复）

**Files:**
- Modify: `scanners/price_scanner.py`（`_save_records` 内 existing 投影与 dedup 分支）
- Test: `tests/test_save_records_overwrite.py`（新）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_save_records_overwrite.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
import models  # noqa: F401
from models.price import PriceSnapshot
from scanners.base import PriceRecord
from scanners.price_scanner import PriceScanner
import scanners.price_scanner as ps

T = datetime(2026, 6, 27, 12, 0)

@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    monkeypatch.setattr(ps, "get_session", lambda: s)      # _save_records 用注入会话
    try:
        yield s
    finally:
        # 注：_save_records 内部会 close 注入会话；这里容错
        try: s.close()
        except Exception: pass

def _scanner():
    return PriceScanner.__new__(PriceScanner)

def test_real_overwrites_existing_gapfill_row(session):
    session.add(PriceSnapshot(timestamp=T, asset_class="futures", symbol="NQ=F",
                              name="NQ=F", price=22124.0, source="okx_gapfill"))
    session.commit()
    rec = PriceRecord(asset_class="futures", symbol="NQ=F", name="NQ=F",
                      price=22050.0, source="yfinance", timestamp=T)
    _scanner()._save_records([rec], T)
    rows = session.query(PriceSnapshot).filter_by(symbol="NQ=F", timestamp=T).all()
    assert len(rows) == 1                       # 原地更新，非新增
    assert rows[0].source == "yfinance" and rows[0].price == 22050.0

def test_gapfill_incoming_does_not_overwrite_real(session):
    session.add(PriceSnapshot(timestamp=T, asset_class="futures", symbol="NQ=F",
                              name="NQ=F", price=22050.0, source="yfinance"))
    session.commit()
    rec = PriceRecord(asset_class="futures", symbol="NQ=F", name="NQ=F",
                      price=22124.0, source="okx_gapfill", timestamp=T)
    _scanner()._save_records([rec], T)
    row = session.query(PriceSnapshot).filter_by(symbol="NQ=F", timestamp=T).one()
    assert row.source == "yfinance" and row.price == 22050.0   # 真实不被合成覆盖

def test_next_real_bar_chains_prev_off_real_after_overwrite(session):
    session.add(PriceSnapshot(timestamp=T, asset_class="futures", symbol="NQ=F",
                              name="NQ=F", price=22124.0, source="okx_gapfill"))
    session.commit()
    recs = [
        PriceRecord(asset_class="futures", symbol="NQ=F", name="NQ=F", price=22050.0,
                    source="yfinance", timestamp=T),                          # 覆盖合成
        PriceRecord(asset_class="futures", symbol="NQ=F", name="NQ=F", price=22100.0,
                    source="yfinance", timestamp=T + timedelta(minutes=5)),   # 新真实
    ]
    _scanner()._save_records(recs, T)
    nxt = session.query(PriceSnapshot).filter_by(symbol="NQ=F", timestamp=T + timedelta(minutes=5)).one()
    assert nxt.prev_price == 22050.0            # 链算基于真实价，非被覆盖的合成 22124
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_save_records_overwrite.py -v`
Expected: FAIL（合成行未被覆盖 / IntegrityError / prev 错链）

- [ ] **Step 3: 改 `_save_records`**

定位 `scanners/price_scanner.py` 中 existing 投影与循环（约 159-203 行）。

(a) existing 投影增选 `source`：
```python
existing_rows = session.query(
    PriceSnapshot.timestamp,
    PriceSnapshot.price,
    PriceSnapshot.source,
).filter(
    PriceSnapshot.symbol == symbol,
    PriceSnapshot.timestamp >= min_ts,
    PriceSnapshot.timestamp <= max_ts,
).all()
existing_meta = {ts: (price, src) for ts, price, src in existing_rows}
existing_timestamps = set(existing_meta)
```

(b) 循环内 dedup 分支改为 source-aware（`GAPFILL_SOURCE` 来自 config）：
```python
import config  # 文件顶部已 import config
...
for r, snap_ts in symbol_records:
    if snap_ts in existing_timestamps:
        ex_price, ex_source = existing_meta[snap_ts]
        incoming_is_real = not r.source.startswith(config.GAPFILL_SOURCE)
        existing_is_gapfill = bool(ex_source) and ex_source.startswith(config.GAPFILL_SOURCE)
        if incoming_is_real and existing_is_gapfill:
            # 真实覆盖同槽合成：取 ORM 行原地更新（不可 add，否则撞唯一索引）
            row = session.query(PriceSnapshot).filter_by(symbol=symbol, timestamp=snap_ts).first()
            if row is not None:
                prev_price = r.prev_price
                change_pct = r.change_pct
                if prev_price is None and last_price is not None:
                    prev_price = last_price
                    if prev_price:
                        change_pct = ((r.price - prev_price) / abs(prev_price)) * 100
                row.asset_class = r.asset_class
                row.name = r.name
                row.price = r.price
                row.prev_price = prev_price
                row.change_pct = change_pct
                row.volume = r.volume
                row.source = r.source
                existing_meta[snap_ts] = (r.price, r.source)
                last_price = r.price          # 链推进到真实价
                inserted += 1
            continue
        # 既有真实 / 入库为合成 → 维持原跳过逻辑
        if ex_price is not None:
            last_price = ex_price
        continue
    # ... 以下新增插入逻辑保持原样 ...
```
> 说明：`inserted += 1` 用于让覆盖也计入"本轮有写入"。若调用方对 inserted 语义敏感，可单列 updated 计数；当前调用只 log 数量，复用 inserted 即可。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_save_records_overwrite.py -v`
Expected: PASS

- [ ] **Step 5: 回归既有价格测试**

Run: `D:\anaconda\python.exe -m pytest tests/test_price_backfill.py tests/test_market_history.py -v`
Expected: PASS（未回归）

- [ ] **Step 6: 提交**

```bash
git add scanners/price_scanner.py tests/test_save_records_overwrite.py
git commit -m "fix(gapfill): _save_records 真实行 UPDATE 覆盖同槽合成行（避免撞唯一索引丢真实数据）"
```

---

## Task 6: 接入 PriceScanner.scan()

**Files:**
- Modify: `scanners/price_scanner.py`（`__init__` 加 GapFiller；`scan()` 末尾调用）
- Test: `tests/test_gap_filler.py`（集成，追加）

- [ ] **Step 1: 写失败测试（集成：scan 末尾跑 GapFiller）**

```python
# tests/test_gap_filler.py 追加
def test_scan_invokes_gapfiller(monkeypatch, session):
    _setup_single(monkeypatch)
    import scanners.price_scanner as ps
    monkeypatch.setattr(ps, "get_session", lambda: session)
    scanner = ps.PriceScanner.__new__(ps.PriceScanner)
    # 桩掉真实源，只验证 GapFiller 被调用且能写
    from types import SimpleNamespace
    scanner.yfinance = SimpleNamespace(fetch=lambda: [])
    scanner.okx = FakeOkx({"QQQ-USDT-SWAP": [PerpBar(bar_end=NOW + timedelta(hours=10), close=710.0)]})
    scanner.coingecko = SimpleNamespace()
    scanner.cnbc_bonds = SimpleNamespace(fetch=lambda: [])
    monkeypatch.setattr(scanner, "_fetch_safe", lambda src: [])
    monkeypatch.setattr(scanner, "_save_records", lambda records, scan_time: 0)
    # 预置真实 bar + 锚点，使 GapFiller 进入补点
    _real(session, "NQ=F", NOW, 22000.0)
    session.add(GapfillAnchor(symbol="NQ=F", real_ts=NOW, real_close=22000.0, perp_price=706.0))
    session.commit()
    monkeypatch.setattr(ps, "datetime", __import__("datetime").datetime)  # 若 scan 用 now，可注入
    scanner.gap_filler = __import__("scanners.gap_filler", fromlist=["GapFiller"]).GapFiller()
    # 直接验证 run 写出合成点（scan() 内部会以 now 为 scan_time；此处用 GapFiller.run 验证接线契约）
    written = scanner.gap_filler.run(session, scanner.okx, NOW + timedelta(hours=10, minutes=1))
    assert written == 1
```
> 注：`scan()` 用 `datetime.now(timezone.utc)` 作 scan_time，难在单测里精确控制周末；故集成测试聚焦"`scan()` 末尾确实调用 `self.gap_filler.run(session, self.okx, scan_time)`"这一接线。可改为 monkeypatch `scanner.gap_filler.run` 记录调用参数来断言接线，避免依赖 now。实现时二选一，保持测试与实现一致。

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_gap_filler.py -k scan_invokes -v`
Expected: FAIL（`scanner.gap_filler` 不存在 / scan 未接线）

- [ ] **Step 3: 接线**

`scanners/price_scanner.py`：
- `__init__` 末尾加 `self.gap_filler = GapFiller()`（顶部 `from scanners.gap_filler import GapFiller`）。
- `scan()` 在 `self._save_records(all_records, scan_time)` 之后、`return` 之前加：
```python
# 休市补点：真实写库后，用 OKX 永续补休市空档
try:
    session = get_session()
    try:
        n = self.gap_filler.run(session, self.okx, scan_time)
        if n:
            logger.info(f"[PriceScanner] gap-fill 写出 {n} 条休市代理点")
    finally:
        session.close()
except Exception as e:
    logger.error(f"[PriceScanner] gap-fill 失败: {type(e).__name__}: {e}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_gap_filler.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scanners/price_scanner.py tests/test_gap_filler.py
git commit -m "feat(gapfill): PriceScanner.scan 接入 GapFiller"
```

---

## Task 7: API 透出 source（get_history）

**Files:**
- Modify: `schemas/market.py`（`MarketHistoryPoint`）
- Modify: `services/market_service.py`（`get_history` 查询 select + point 填充）
- Test: `tests/test_market_history.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_market_history.py 追加
def test_history_exposes_source(session):
    from datetime import timedelta
    now = utc_now_naive()
    start = now - timedelta(hours=4)
    _add(session, "NQ=F", start - timedelta(hours=1), 22000.0, asset_class="futures")  # 基准
    _add(session, "NQ=F", start + timedelta(minutes=5), 22010.0, asset_class="futures")
    session.add(PriceSnapshot(timestamp=start + timedelta(minutes=10), asset_class="futures",
                              symbol="NQ=F", name="NQ=F", price=22050.0, source="okx_gapfill"))
    session.commit()
    resp = market_service.get_history(session, symbols=["NQ=F"], hours=4)
    pts = resp.series[0].points
    assert pts[-1].source == "okx_gapfill"
    assert pts[0].source == "test"          # 真实点 source 透出（_add 用 source="test"）
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_market_history.py::test_history_exposes_source -v`
Expected: FAIL（`MarketHistoryPoint` 无 source / 未填）

- [ ] **Step 3: 实现**

`schemas/market.py` 给 `MarketHistoryPoint` 加字段：
```python
class MarketHistoryPoint(TimeFields):
    symbol: str
    name: str
    price: float
    normalized_pct: float | None = None
    source: str | None = None
```
`services/market_service.py` `get_history`：查询 select 增加 `PriceSnapshot.source`：
```python
session.query(
    PriceSnapshot.timestamp,
    PriceSnapshot.symbol,
    PriceSnapshot.name,
    PriceSnapshot.asset_class,
    PriceSnapshot.price,
    PriceSnapshot.source,
)
```
point 构造加 `source=row.source`：
```python
MarketHistoryPoint(
    symbol=row.symbol, name=row.name, price=row.price,
    normalized_pct=normalized[index] if index < len(normalized) else None,
    source=row.source,
    **timestamp_pair(row.timestamp),
)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_market_history.py -v`
Expected: PASS（含既有用例不回归）

- [ ] **Step 5: 提交**

```bash
git add schemas/market.py services/market_service.py tests/test_market_history.py
git commit -m "feat(gapfill): get_history 逐点透出 source"
```

---

## Task 8: 前端 — types + Charts `shadedBands`（ReferenceArea）

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/components/Charts.tsx`
- Test: `frontend/src/components/Charts.test.tsx`（扩或新建）

- [ ] **Step 1: 写失败测试（默认行为不变 + 传 shadedBands 渲染 ReferenceArea）**

> **jsdom 注意（reviewer 确认的真风险）**：jsdom 下 `ResponsiveContainer` 尺寸为 0，recharts 会跳过渲染 `<LineChart>` 子元素（line/area），`.recharts-reference-area` 查不到。仓库现有 `Charts.test.tsx` 只断言 `.chart-shell` 外壳、`WindowNetValueChart.test.tsx` 断言 SVG 外的文本，**无断言 recharts SVG 内部元素的先例**。故本测试**必须 mock `ResponsiveContainer` 注入固定尺寸**，让子元素真正渲染：

```tsx
// frontend/src/components/Charts.test.tsx（追加）
import React from "react";
import { render } from "@testing-library/react";
import { vi } from "vitest";

// 给 LineChart 注入显式 width/height，否则 jsdom 下 recharts 不渲染子元素
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: any) =>
      React.cloneElement(children, { width: 800, height: 400 }),
  };
});

import { MultiLineChart } from "./Charts";

const data = [
  { time: "06-27 04:00", "纳指 (NQ=F)": 0 },
  { time: "06-27 05:00", "纳指 (NQ=F)": 0.5 },
  { time: "06-27 06:00", "纳指 (NQ=F)": 0.8 },
];

it("renders without shadedBands (default unchanged)", () => {
  const { container } = render(
    <MultiLineChart data={data} keys={["纳指 (NQ=F)"]} />
  );
  expect(container.querySelectorAll(".recharts-reference-area").length).toBe(0);
});

it("renders a ReferenceArea when shadedBands passed", () => {
  const { container } = render(
    <MultiLineChart data={data} keys={["纳指 (NQ=F)"]}
      shadedBands={[{ x1: "06-27 05:00", x2: "06-27 06:00", label: "休市代理价" }]} />
  );
  expect(container.querySelectorAll(".recharts-reference-area").length).toBeGreaterThan(0);
});
```
> 实现前先看 `Charts.test.tsx`/`WindowNetValueChart.test.tsx` 既有套路；若它们已有 ResponsiveContainer 的 mock 约定则复用之，避免重复 mock 冲突。若 `.recharts-reference-area` 类名在当前 recharts 版本下不稳，退而给 `<ReferenceArea>` 加 `ifOverflow`/自定义 `label` 文本并断言该文本存在。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/components/Charts.test.tsx`
Expected: FAIL（shadedBands 未支持，第二个用例 0 个 reference-area）

- [ ] **Step 3: 实现**

`frontend/src/api/types.ts`：`MarketHistoryPoint` 加 `source?: string | null;`，并加：
```ts
// 与后端 config.GAPFILL_SOURCE 保持一致
export const OKX_GAPFILL_SOURCE = "okx_gapfill";
```
`frontend/src/components/Charts.tsx`：import 增 `ReferenceArea`；props 增可选：
```ts
shadedBands?: { x1: string; x2: string; label?: string }[];
```
在 `<LineChart>` 内（baseline/markers 附近）渲染：
```tsx
{(shadedBands ?? []).map((b, i) => (
  <ReferenceArea key={`band-${i}`} x1={b.x1} x2={b.x2}
    strokeOpacity={0} fill="rgba(148,163,184,0.14)"
    label={b.label ? { value: b.label, position: "insideTop", fill: "#94a3b8", fontSize: 11 } : undefined} />
))}
```
未传 `shadedBands` 时不渲染任何 ReferenceArea（默认行为不变）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/components/Charts.test.tsx`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/src/api/types.ts frontend/src/components/Charts.tsx frontend/src/components/Charts.test.tsx
git commit -m "feat(gapfill): Charts 支持 shadedBands(ReferenceArea) + 前端 source 类型"
```

---

## Task 9: 前端 — MarketPage 推阴影带 + 卡片角标

**Files:**
- Modify: `frontend/src/pages/MarketPage.tsx`（`buildHistoryChart` 推 bands；卡片角标）
- Test: `frontend/src/pages/MarketPage.test.tsx`（扩或新建）

- [ ] **Step 1: 写失败测试（band 端点为序列中存在的 time 串）**

```tsx
// 针对从 history 推阴影带的纯函数（实现时把该逻辑抽成可导出的纯函数 deriveShadedBands）
import { deriveShadedBands } from "./MarketPage";

it("derives band endpoints from gapfill-sourced points using existing time strings", () => {
  const history = {
    series: [{ symbol: "NQ=F", name: "纳指", asset_class: "futures", points: [
      { timestamp_bj: "2026-06-27 04:00:00", timestamp_utc: "...", symbol: "NQ=F", name: "纳指", price: 1, normalized_pct: 0, source: "yfinance" },
      { timestamp_bj: "2026-06-27 05:00:00", timestamp_utc: "...", symbol: "NQ=F", name: "纳指", price: 1, normalized_pct: 0.5, source: "okx_gapfill" },
      { timestamp_bj: "2026-06-27 06:00:00", timestamp_utc: "...", symbol: "NQ=F", name: "纳指", price: 1, normalized_pct: 0.8, source: "okx_gapfill" },
    ]}],
  } as any;
  const bands = deriveShadedBands(history);
  expect(bands[0].x1).toBe("06-27 05:00");   // 与 buildHistoryChart 的 time 串格式一致（slice(5,16)）
  expect(bands[0].x2).toBe("06-27 06:00");
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/pages/MarketPage.test.tsx`
Expected: FAIL（`deriveShadedBands` 未导出）

- [ ] **Step 3: 实现**

`MarketPage.tsx`：
- 抽出并导出纯函数 `deriveShadedBands(history)`：遍历所有 series 的 points，取 `source` 以 `OKX_GAPFILL_SOURCE` 开头的点，按与 `buildHistoryChart` **完全相同**的 `time` 串格式（`timestamp_bj.slice(5,16)`）收集，合成 `[{x1,x2,label:"休市代理价(OKX 永续)"}]`（连续区间取首末 time 串；多段可分段，起步可只取全局 min/max 一段）。
- 把 `shadedBands={deriveShadedBands(history.data)}` 传给概览的 `<MultiLineChart>`。
- 卡片渲染处：`item.source?.startsWith(OKX_GAPFILL_SOURCE)` 为真时显示"代理价"角标（title 注明来源 perp，可经 symbol 反查映射，或简单写"OKX 永续代理价"）。

- [ ] **Step 4: 跑测试确认通过 + 前端全量回归**

Run: `cd frontend && npx vitest run`
Expected: PASS（既有用例不回归）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/MarketPage.tsx frontend/src/pages/MarketPage.test.tsx
git commit -m "feat(gapfill): 概览图休市阴影带 + 卡片代理价角标"
```

---

## 收尾验证（全部 Task 完成后）

- [ ] 后端全量：`D:\anaconda\python.exe -m pytest -q`
- [ ] 前端全量：`cd frontend && npx vitest run`
- [ ] 手测（可选，周末时段最直观）：本地起服务，看市场概览图在 NQ=F/CL=F/GC=F 的休市段是否出现连续曲线 + 灰色"休市代理价"阴影带，卡片是否带角标。参考 `/run` 或既有启动方式。
- [ ] 确认部署服务器（腾讯云日本）对 OKX 直连可达（`fetch_instrument_bars` 依赖）；不可达则需代理。

## 风险与回滚
- 总开关 `GAPFILL_ENABLED=0` 一键停用补点；停用后系统行为与改造前一致（合成点不再产生，已产生的历史合成点仍带 `okx_gapfill` 标识、可后续清理）。
- 所有合成点 `source="okx_gapfill"` 可被一条 SQL 清除：`DELETE FROM price_snapshots WHERE source LIKE 'okx_gapfill%'`。
