# 跨资产净值曲线 + 标注页纳指对标 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development 或 superpowers:executing-plans 逐任务实现。步骤用 checkbox（`- [ ]`）跟踪。
>
> Spec：[cross-asset-nav-and-nasdaq-ref.md](cross-asset-nav-and-nasdaq-ref.md)

**Goal:** 让「跨资产走势」图按窗口起点锚定净值（隔夜跳空/熔断如实显示），并在「新闻标注」页对每个价格异动窗口加显同期纳指（NQ=F）涨跌，休市显「无」。

**Architecture:** A——`normalize_prices` 加可选 `base`，`get_history` 取每品种窗口起点前最后一笔收盘作基准。B——`PriceWindowSchema`/`AnnotationListItem` 加 `nasdaq_pct`，服务端按窗口端点最近的 NQ=F 快照算同期涨跌（不持久化）。前端镜像字段并渲染。

**Tech Stack:** Python / SQLAlchemy / FastAPI / pytest（内存 SQLite fixture）；React + TS + Vite。

> **本机注意：** pytest 用 `D:\anaconda\python.exe -m pytest ...`——PATH 上的 `python` 是 Windows Store 占位（exit 9009）。

---

## 文件结构

| 文件 | 改动 | 责任 |
|---|---|---|
| `chart_utils.py` | 改 `normalize_prices` | 价格归一（加可选基准） |
| `config.py` | 加常量 | `MARKET_HISTORY_BASELINE_LOOKBACK_DAYS` |
| `services/market_service.py` | 改 `get_history` + 加 `_window_baseline_prices` | 走势图按窗口起点锚定 |
| `services/annotation_service.py` | 加常量+2 helper + 接线 `load_price_windows`/`list_annotations` | 纳指对标计算 |
| `schemas/annotations.py` | `PriceWindowSchema`/`AnnotationListItem` 加 `nasdaq_pct` | 响应契约 |
| `frontend/src/api/types.ts` | 镜像 `nasdaq_pct` | 前端类型 |
| `frontend/src/pages/AnnotationsPage.tsx` | `fmtNasdaq` + 待标注行 + 已标注列 | UI 渲染 |
| `tests/test_price_history.py` | 扩展 | `normalize_prices(base=)` 单测 |
| `tests/test_market_history.py` | 新建 | `get_history` 基准 DB 测试 |
| `tests/test_annotation_windows.py` | 扩展 | 纳指对标 DB 测试 |
| `DATAFLOW.md`/`ARCHITECTURE.md`/`DECISIONS.md`/`PENDING.md` | 同步 | 地图维护契约 |

---

## Task 1：normalize_prices 加可选基准（改动 A 基础）

**Files:**
- Modify: `chart_utils.py:7-14`
- Test: `tests/test_price_history.py`（扩展）

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_price_history.py` 末尾）

```python
def test_normalize_with_explicit_base():
    # base 来自窗口起点（昨收 100），首点已跌到 92 → −8%，不被吃掉
    result = normalize_prices([92.0, 93.0], base=100.0)
    assert abs(result[0] - (-8.0)) < 0.001
    assert abs(result[1] - (-7.0)) < 0.001


def test_normalize_base_none_matches_legacy():
    assert normalize_prices([100.0, 110.0], base=None) == normalize_prices([100.0, 110.0])


def test_normalize_explicit_zero_base_falls_back_to_first_point():
    result = normalize_prices([100.0, 110.0], base=0)
    assert result[0] == 0.0
    assert abs(result[1] - 10.0) < 0.001
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_price_history.py -q`
Expected: FAIL（`normalize_prices() got an unexpected keyword argument 'base'`）

- [ ] **Step 3: 实现**（替换 `chart_utils.py:7-14` 整个函数）

```python
def normalize_prices(prices: list[float], base: float | None = None) -> list[float]:
    """将价格序列转为相对基准价的涨跌幅（%）。

    base=None 时以序列第一个点为基准（旧行为）；传入时以该基准价归一，用于
    「跨资产走势」按窗口起点锚定净值，保留隔夜跳空。base 为 0/None 时回退首点。
    """
    if not prices:
        return []
    if base is None or base == 0:
        base = prices[0]
    if base == 0:
        return [0.0] * len(prices)
    return [(p / base - 1) * 100 for p in prices]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_price_history.py -q`
Expected: PASS（全部，含旧用例）

- [ ] **Step 5: 提交**

```bash
git add chart_utils.py tests/test_price_history.py
git commit -m "feat(chart): normalize_prices 加可选 base 参数（向后兼容）"
```

---

## Task 2：get_history 窗口起点锚定净值（改动 A 主体）

**Files:**
- Modify: `config.py`（加常量，`SCAN_ROLLING_BACKFILL_INTERVALS` 那行 L80 之后）
- Modify: `services/market_service.py`（加 `_window_baseline_prices`；改 `get_history` L156-178 的归一）
- Modify: `DATAFLOW.md`（走势图归一语义）
- Test: `tests/test_market_history.py`（新建）

- [ ] **Step 1: 写失败测试**（新建 `tests/test_market_history.py`）

```python
"""get_history 窗口起点锚定净值：隔夜跳空不被首点基准吃掉。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models  # noqa: F401  注册模型到 Base.metadata
from models.price import PriceSnapshot
from services import market_service
from services.time_utils import utc_now_naive


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _add(s, symbol, ts, price, asset_class="asian_index"):
    s.add(PriceSnapshot(timestamp=ts, asset_class=asset_class, symbol=symbol,
                        name=symbol, price=price, source="test"))


def test_baseline_anchored_at_window_start_preserves_gap(session):
    now = utc_now_naive()
    start = now - timedelta(hours=4)
    _add(session, "^KS11", start - timedelta(hours=1), 100.0)        # 昨收=基准
    _add(session, "^KS11", start + timedelta(minutes=5), 92.0)       # 今开已跌 8%
    _add(session, "^KS11", start + timedelta(minutes=10), 93.0)
    session.commit()
    resp = market_service.get_history(session, symbols=["^KS11"], hours=4)
    pts = resp.series[0].points
    assert pts[0].normalized_pct == pytest.approx(-8.0, abs=0.05)    # 相对昨收，非 0
    assert pts[1].normalized_pct == pytest.approx(-7.0, abs=0.05)


def test_falls_back_to_first_point_without_pre_window_data(session):
    now = utc_now_naive()
    start = now - timedelta(hours=4)
    _add(session, "BTC/USDT", start + timedelta(minutes=5), 50000.0, asset_class="crypto")
    _add(session, "BTC/USDT", start + timedelta(minutes=10), 51000.0, asset_class="crypto")
    session.commit()
    resp = market_service.get_history(session, symbols=["BTC/USDT"], hours=4)
    pts = resp.series[0].points
    assert pts[0].normalized_pct == 0.0                              # 无前置数据 → 回退首点
    assert pts[1].normalized_pct == pytest.approx(2.0, abs=0.05)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_market_history.py -q`
Expected: FAIL（首个断言 −8 vs 实际 0，因当前以首点为基准）

- [ ] **Step 3: 加 config 常量**（`config.py`，`SCAN_ROLLING_BACKFILL_INTERVALS` 行之后插入）

```python
# 「跨资产走势」净值基准：取窗口起始时刻之前最后一笔收盘作基准，向前回看上限（天）。
MARKET_HISTORY_BASELINE_LOOKBACK_DAYS = int(os.getenv("MARKET_HISTORY_BASELINE_LOOKBACK_DAYS", "7"))
```

- [ ] **Step 4: 加 helper**（`services/market_service.py`，`get_history` 之前插入）

```python
def _window_baseline_prices(
    session: Session, symbols: list[str], start: datetime, lookback_days: int
) -> dict[str, float]:
    """每个 symbol 在窗口起点 start 当时的基准价 = timestamp ≤ start 的最后一笔收盘。
    用于「跨资产走势」按窗口起点锚定净值，保留隔夜跳空。无前置数据的 symbol 不入字典。"""
    if not symbols:
        return {}
    lookback_start = start - timedelta(days=lookback_days)
    rows = (
        session.query(PriceSnapshot.symbol, PriceSnapshot.timestamp, PriceSnapshot.price)
        .filter(
            PriceSnapshot.symbol.in_(symbols),
            PriceSnapshot.timestamp >= lookback_start,
            PriceSnapshot.timestamp <= start,
        )
        .order_by(PriceSnapshot.timestamp.asc())
        .all()
    )
    baseline: dict[str, float] = {}
    for row in rows:
        if row.price:
            baseline[row.symbol] = row.price  # asc 遍历，最后写入的是 ≤start 最近一笔
    return baseline
```

- [ ] **Step 5: 改 get_history 归一**（`services/market_service.py`，分组后、series 循环处）

在 `grouped` 构建完、`series` 循环之前插入：

```python
    baselines = _window_baseline_prices(
        session, list(grouped.keys()), start, config.MARKET_HISTORY_BASELINE_LOOKBACK_DAYS
    )
```

把循环内（现 L158-159）：

```python
        prices = [row.price for row in bucket["rows"]]
        normalized = normalize_prices(prices) if len(prices) >= 1 else []
```

改为：

```python
        prices = [row.price for row in bucket["rows"]]
        normalized = normalize_prices(prices, base=baselines.get(symbol)) if len(prices) >= 1 else []
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python -m pytest tests/test_market_history.py tests/test_price_history.py -q`
Expected: PASS

- [ ] **Step 7: 同步 DATAFLOW.md（维护契约：同次 commit）**

在 `DATAFLOW.md` 的「时间语义总结」或「改动后最容易引发问题的关键字段」附近，补一句走势图归一语义：
> `/api/market/history` 的 `normalized_pct` 自 2026-06-08 起按**窗口起点锚定**（每品种以 `timestamp ≤ start` 最后一笔收盘为基准，`config.MARKET_HISTORY_BASELINE_LOOKBACK_DAYS` 回看），保留隔夜跳空；无前置数据回退到窗口内首点。

- [ ] **Step 8: 提交**

```bash
git add config.py services/market_service.py tests/test_market_history.py DATAFLOW.md
git commit -m "feat(market): 跨资产走势按窗口起点锚定净值，保留隔夜跳空"
```

---

## Task 3：标注页纳指对标（后端）

**Files:**
- Modify: `schemas/annotations.py`（`PriceWindowSchema` L21-37、`AnnotationListItem` L142-155 各加字段）
- Modify: `services/annotation_service.py`（常量 + 2 helper + `load_price_windows` L260-293 接线 + `list_annotations` L441-466 接线）
- Modify: `DATAFLOW.md`（windows/list 端点加 `nasdaq_pct`）
- Test: `tests/test_annotation_windows.py`（扩展）

- [ ] **Step 1: 加 schema 字段**

`schemas/annotations.py`：`PriceWindowSchema` 内 `is_primary` 之后加：
```python
    nasdaq_pct: float | None = None    # 同期 NQ=F 涨跌；None=休市/本身
```
`AnnotationListItem` 内 `change_pct` 之后加：
```python
    nasdaq_pct: float | None = None
```

- [ ] **Step 2: 写失败测试**（追加到 `tests/test_annotation_windows.py`）

```python
def _add_nq(session, now, minutes_ago, price):
    session.add(PriceSnapshot(
        timestamp=now - timedelta(minutes=minutes_ago),
        asset_class="futures", symbol="NQ=F", name="纳指期货",
        price=price, source="test",
    ))


def test_window_carries_nasdaq_reference(session):
    now = utc_now_naive()
    _seed(session, now, [(20, 100.0), (15, 101.0)])          # TEST 窗口 [-20,-15]
    _add_nq(session, now, 20, 20000.0)
    _add_nq(session, now, 15, 20100.0)                       # (20100-20000)/20000 = +0.5%
    session.commit()
    wins = _call(session)
    assert len(wins) == 1
    assert wins[0].nasdaq_pct == pytest.approx(0.5, abs=0.01)


def test_window_nasdaq_none_when_market_closed(session):
    now = utc_now_naive()
    _seed(session, now, [(20, 100.0), (15, 101.0)])          # 无 NQ 快照
    wins = _call(session)
    assert wins[0].nasdaq_pct is None


def test_nasdaq_symbol_itself_has_none_reference(session):
    now = utc_now_naive()
    for m, p in [(20, 20000.0), (15, 20200.0)]:              # 标注 NQ 自身，+1% 触发
        _add_nq(session, now, m, p)
    session.commit()
    wins = load_price_windows(session, "NQ=F", hours=24, threshold_pct=0.5, window_minutes=5)
    assert len(wins) == 1
    assert wins[0].nasdaq_pct is None                        # 本身不对标


def test_list_annotations_carries_nasdaq_reference(session):
    from models.news import NewsPriceAnnotation
    now = utc_now_naive()
    ws, we = now - timedelta(minutes=20), now - timedelta(minutes=15)
    session.add(NewsPriceAnnotation(
        symbol="BTC/USDT", window_start=ws, window_end=we,
        context_start=ws, context_end=we,           # NOT NULL 无默认，必须给
        change_pct=1.0, no_clear_news=False, created_at=now, updated_at=now,
    ))
    _add_nq(session, now, 20, 20000.0)
    _add_nq(session, now, 15, 20100.0)
    session.commit()
    items = annotation_service.list_annotations(session, symbol=None, hours=24)
    assert len(items) == 1
    assert items[0].nasdaq_pct == pytest.approx(0.5, abs=0.01)
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python -m pytest tests/test_annotation_windows.py -q`
Expected: FAIL（`PriceWindowSchema`/`AnnotationListItem` 无 `nasdaq_pct` 属性 / 值为 None 不符）

- [ ] **Step 4: 加常量 + helpers**（`services/annotation_service.py`，`TARGET_PRICE_SYMBOLS` L33 之后）

```python
NASDAQ_REFERENCE_SYMBOL = "NQ=F"


def _nearest_snapshot_any(rows: list[PriceSnapshot], target: datetime, tolerance_minutes: int) -> PriceSnapshot | None:
    """rows 里与 target 时间差最小且 ≤ 容差者（不限前后，区别于 _nearest_snapshot）。"""
    best = None
    best_delta = None
    for row in rows:
        delta = abs((row.timestamp - target).total_seconds())
        if delta > tolerance_minutes * 60:
            continue
        if best_delta is None or delta < best_delta:
            best, best_delta = row, delta
    return best


def _load_reference_rows(session: Session, cutoff: datetime) -> list[PriceSnapshot]:
    return (
        session.query(PriceSnapshot)
        .filter(PriceSnapshot.symbol == NASDAQ_REFERENCE_SYMBOL, PriceSnapshot.timestamp >= cutoff)
        .order_by(PriceSnapshot.timestamp.asc())
        .all()
    )


def _reference_change_for_window(
    nq_rows: list[PriceSnapshot], window_start: datetime, window_end: datetime, tolerance_minutes: int
) -> float | None:
    """同期 NQ=F 涨跌：端点最近快照求 (end-start)/start。任一端无数据 → None。"""
    s = _nearest_snapshot_any(nq_rows, window_start, tolerance_minutes)
    e = _nearest_snapshot_any(nq_rows, window_end, tolerance_minutes)
    if s is None or e is None or not s.price:
        return None
    return (e.price - s.price) / abs(s.price) * 100
```

- [ ] **Step 5: 接线 load_price_windows**（`services/annotation_service.py`）

在 `tolerance_minutes = ...`（L205）之后加载 NQ 行（symbol 自身则不加载）：
```python
    nq_rows = [] if symbol == NASDAQ_REFERENCE_SYMBOL else _load_reference_rows(session, cutoff)
```
在 Step 3 构造 `PriceWindowSchema(...)` 的 kwargs 里（`is_primary=True,` 之后）加：
```python
            nasdaq_pct=None if symbol == NASDAQ_REFERENCE_SYMBOL
            else _reference_change_for_window(nq_rows, w_start, w_end, tolerance_minutes),
```

- [ ] **Step 6: 接线 list_annotations**（`services/annotation_service.py` L441-466）

`rows = query.order_by(...).limit(500).all()` 之后插入：
```python
    tolerance_minutes = max(config.SCAN_INTERVALS["price"] * 2, 1)
    if rows:
        earliest = min(r.window_start for r in rows)
        nq_rows = _load_reference_rows(session, earliest - timedelta(minutes=tolerance_minutes + 5))
    else:
        nq_rows = []
```
在 `AnnotationListItem(...)` 构造里 `change_pct=row.change_pct,` 之后加：
```python
            nasdaq_pct=None if row.symbol == NASDAQ_REFERENCE_SYMBOL
            else _reference_change_for_window(nq_rows, row.window_start, row.window_end, tolerance_minutes),
```

- [ ] **Step 7: 跑测试确认通过**

Run: `python -m pytest tests/test_annotation_windows.py -q`
Expected: PASS（含原有合并用例）

- [ ] **Step 8: 同步 DATAFLOW.md**

在 API 契约表里 `GET /api/annotations/windows` 与 `GET /api/annotations/list` 行后注明响应新增 `nasdaq_pct`（同期 NQ=F 涨跌，休市/本身为 null）。

- [ ] **Step 9: 提交**

```bash
git add schemas/annotations.py services/annotation_service.py tests/test_annotation_windows.py DATAFLOW.md
git commit -m "feat(annotations): 价格窗口加同期纳指(NQ=F)对标，休市为 null（后端）"
```

---

## Task 4：标注页纳指对标（前端）

**Files:**
- Modify: `frontend/src/api/types.ts`（`PriceWindow`、`AnnotationListItem` 接口）
- Modify: `frontend/src/pages/AnnotationsPage.tsx`

- [ ] **Step 1: 确认并镜像类型**

先在 `frontend/src/api/types.ts` 找到 `PriceWindow` 和 `AnnotationListItem` 接口（确认导出名），各加：
```typescript
  nasdaq_pct?: number | null;
```

- [ ] **Step 2: 加格式化 helper**（`AnnotationsPage.tsx`，`windowKey` 函数附近，组件外）

```typescript
function fmtNasdaq(symbol: string, pct: number | null | undefined): string {
  if (symbol === "NQ=F") return "纳指 本身";
  if (pct == null) return "纳指 无";
  return `纳指 ${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
}
```

- [ ] **Step 3: 待标注行加显**（`AnnotationsPage.tsx` 峰值 span L559-561 之后）

```tsx
                            <span className="window-item-pct" title="同期纳斯达克(NQ=F)涨跌，休市为无">
                              {fmtNasdaq(primary.symbol, primary.nasdaq_pct)}
                            </span>
```

- [ ] **Step 4: 已标注表加列**（`AnnotationsPage.tsx` 已标注 `columns` 数组里，`chg` 列之后）

```tsx
              {
                key: "nasdaq",
                header: "纳指对标",
                cell: (row) => fmtNasdaq(row.symbol, row.nasdaq_pct),
                className: "num"
              },
```

- [ ] **Step 5: typecheck + build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: 均通过（如本机已配置 npm）

- [ ] **Step 6: 提交**

```bash
git add frontend/src/api/types.ts frontend/src/pages/AnnotationsPage.tsx
git commit -m "feat(annotations): 标注页显示同期纳指对标（待标注行 + 已标注列）"
```

---

## Task 5：地图收尾 + 全量验证

**Files:**
- Modify: `ARCHITECTURE.md`、`DECISIONS.md`、`PENDING.md`

- [ ] **Step 1: ARCHITECTURE.md**
  - 顶部「最近一次基于代码扫描确认」日期 → **2026-06-08**。
  - 修 L77 调用链「Jin10 + Bloomberg RSS」→「Jin10 + CNBC RSS」。
  - 补一句：标注窗口已升级为「事件合并跨段窗口」（2026-06-06）+ 告警「陈旧 bar 守卫」（已落 main）。

- [ ] **Step 2: DECISIONS.md（追加 2 条，最新在前）**

```markdown
## 2026-06-08 跨资产走势按窗口起点锚定净值
- 背景：各品种以「窗口内首点」为基准，隔夜跳空（KOSPI 熔断）被首点吞掉，跨资产对比失真。
- 决策：`normalize_prices` 加可选 `base`；`get_history` 取每品种 `timestamp ≤ start` 最后一笔收盘为基准（7 天回看），无前置数据回退首点。
- 拒绝备选：昨收锚定「当日涨跌」——需各市场收盘时段逻辑 + 24h 加密昨收定义，复杂度高。
- 影响：`/api/market/history` 的 `normalized_pct` 语义改变；前端无需改。

## 2026-06-08 标注页同期纳指对标
- 背景：标注 BTC 异动时缺宏观参照，难区分宏观驱动 vs 个体异动。
- 决策：`PriceWindowSchema`/`AnnotationListItem` 加 `nasdaq_pct`，按窗口端点最近 NQ=F 快照算同期涨跌；休市/本身为 null（前端显「无」/「本身」）。
- 拒绝备选：持久化 + 喂入自动标注 prompt——可从 price_snapshots 随时重算，YAGNI。
- 影响：纯展示，不改库、不改告警。用 NQ=F 期货（非现货 ^IXIC）以覆盖更多时段。
```

- [ ] **Step 3: PENDING.md** 顶部确认日期 → 2026-06-08；「最近完成」加本次两项。

- [ ] **Step 4: 全量后端测试**

Run: `python -m pytest -q`
Expected: 全绿（原有 + 新增）

- [ ] **Step 5: 提交**

```bash
git add ARCHITECTURE.md DECISIONS.md PENDING.md
git commit -m "docs(maps): 同步跨资产净值 + 纳指对标，推进确认日期到 2026-06-08"
```

---

## 完成标准
- 走势图：选含早盘的区间时，KOSPI 熔断/隔夜跳空在曲线上如实显示（首点落在跳空后的净值，而非 0）。
- 标注页：每个待标注/已标注窗口显示同期纳指涨跌；周末/休市显「无」；标注 NQ 本身显「本身」。
- `python -m pytest -q` 全绿；`npm run typecheck && npm run build` 通过。
- 5 张地图与代码一致，确认日期 2026-06-08。
- 不在范围：纳指持久化/喂 LLM、走势图扩展到概览卡片、昨收锚定基准。
</content>
