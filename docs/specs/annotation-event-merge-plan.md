# 标注事件窗口跨段合并 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 标注页的价格"事件窗口"改为：同方向、相邻段静默间隔 ≤ `ANNOTATION_EVENT_MERGE_GAP_MINUTES`（默认 60）的触发合并成**一个跨段窗口**，携带净变动 + 峰值 + 振幅 + 段数。

**Architecture:** 在 `load_price_windows` 现有"触发检测"之上重写"分组+合成"层：Step 1 触发多带 `baseline.timestamp`；Step 2 按同号 + 静默间隔合并；Step 3 每事件合成一个 `PriceWindowSchema`。前端去掉 secondary、展示净+峰值。

**Tech Stack:** Python / SQLAlchemy / FastAPI / Pydantic（后端）；React + TypeScript（前端）；pytest（用内存 SQLite）。

**Spec:** `docs/specs/annotation-event-merge.md`（口径/边界以它为准）。
**本地跑测试用：** `D:\anaconda\python.exe -m pytest ...`

---

## File Structure

- **Modify** `config.py` — 新增 `ANNOTATION_EVENT_MERGE_GAP_MINUTES`。
- **Modify** `schemas/annotations.py` — `PriceWindowSchema` 加 4 字段（带默认值）。
- **Modify** `frontend/src/api/types.ts` — `PriceWindow` 镜像 4 字段。
- **Modify** `services/annotation_service.py` — `load_price_windows` 的 Step 1–3 重写。
- **Modify** `frontend/src/pages/AnnotationsPage.tsx` — 展示峰值、移除 secondary 死代码。
- **Create** `tests/test_annotation_windows.py` — 内存 DB fixture + 5 用例。

---

## Task 1：加配置 + schema 字段 + 前端类型（无行为改动，先打底）

**Files:** Modify `config.py`、`schemas/annotations.py`、`frontend/src/api/types.ts`

- [ ] **Step 1：config.py 加常量**（放在 `ALERT_PRICE_MAX_STALENESS_MINUTES` 之后）

```python
# 标注事件合并：相邻同方向异动段的静默间隔 ≤ 此分钟数则并为同一事件窗口。设 0/负 视为 1。
ANNOTATION_EVENT_MERGE_GAP_MINUTES = int(os.getenv("ANNOTATION_EVENT_MERGE_GAP_MINUTES", "60"))
```

- [ ] **Step 2：schemas/annotations.py — `PriceWindowSchema` 加字段**

在 `change_pct: float` 之后、`annotation_id` 之前插入：
```python
    peak_change_pct: float = 0.0
    low_price: float = 0.0
    high_price: float = 0.0
    segment_count: int = 1
```

- [ ] **Step 3：frontend/src/api/types.ts — `PriceWindow` 加字段**

在 `change_pct: number;` 之后插入：
```ts
  peak_change_pct: number;
  low_price: number;
  high_price: number;
  segment_count: number;
```

- [ ] **Step 4：快速验证不破坏导入**

Run: `D:\anaconda\python.exe -c "import schemas.annotations, config; print(config.ANNOTATION_EVENT_MERGE_GAP_MINUTES)"`
Expected: 打印 `60`，无异常。

- [ ] **Step 5：提交**

```bash
git add config.py schemas/annotations.py frontend/src/api/types.ts
git commit -m "feat(annotations): add event-merge config + schema/type fields"
```

---

## Task 2：后端核心——`load_price_windows` 跨段合并（TDD）

**Files:**
- Create: `tests/test_annotation_windows.py`
- Modify: `services/annotation_service.py`（Step 1–3，约 L220–275）

- [ ] **Step 1：写失败测试**（先建 fixture + 用例 1/3/4，再补 2/5）

```python
"""load_price_windows 跨段合并行为。用内存 SQLite，时间戳相对 now 倒推。"""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
import models  # noqa: F401  注册模型到 Base.metadata
from models.price import PriceSnapshot
from services import annotation_service
from services.annotation_service import load_price_windows, utc_now_naive


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _isolate_rules(monkeypatch):
    # load_price_windows 会调 load_alert_price_rules()，隔离掉以免读真实库
    monkeypatch.setattr(annotation_service, "load_alert_price_rules", lambda: [])


def _seed(session, now, bars):
    """bars: list[(minutes_ago, price)]，越大越旧。"""
    for minutes_ago, price in bars:
        session.add(PriceSnapshot(
            timestamp=now - timedelta(minutes=minutes_ago),
            asset_class="crypto", symbol="TEST", name="Test",
            price=price, source="test",
        ))
    session.commit()


def _call(session):
    # window_minutes=5（基线=前一根 5min bar）、threshold=0.5%、hours=24
    return load_price_windows(session, "TEST", hours=24, threshold_pct=0.5, window_minutes=5)


def test_two_segments_within_gap_merge_into_one(session):
    now = utc_now_naive()
    bars = (
        [(120, 100.0), (115, 101.0), (110, 102.0)]          # 段 A：触发 @-115,-110
        + [(m, 102.0) for m in (105, 100, 95, 90, 85, 80, 75)]  # 静默期，无触发
        + [(70, 103.0), (65, 104.0)]                          # 段 B：触发 @-70,-65（与 A 静默间隔 35min<60）
    )
    _seed(session, now, bars)
    wins = _call(session)
    assert len(wins) == 1
    w = wins[0]
    assert w.segment_count == 4
    assert w.change_pct == pytest.approx(4.0, abs=0.05)      # (104-100)/100
    assert w.high_price == pytest.approx(104.0)
    assert w.low_price == pytest.approx(100.0)
    # 峰值：同号、|peak|>=|net|、up 事件 high>=price_end
    assert w.peak_change_pct >= w.change_pct - 1e-6
    assert w.high_price >= w.price_end and w.low_price <= w.price_start


def test_two_segments_beyond_gap_split(session):
    now = utc_now_naive()
    bars = (
        [(160, 100.0), (155, 101.0), (150, 102.0)]           # 段 A：触发 @-155,-150
        + [(m, 102.0) for m in range(145, 45, -5)]            # 长静默期（>60min）
        + [(40, 103.0), (35, 104.0)]                          # 段 B：与 A 间隔 (45-(-... )) >60 → 拆开
    )
    _seed(session, now, bars)
    wins = _call(session)
    assert len(wins) == 2


def test_opposite_direction_does_not_merge(session):
    now = utc_now_naive()
    bars = [(120, 100.0), (115, 101.0)]                       # 段 A：+1% 触发 @-115
    bars += [(m, 101.0) for m in (110, 105)]                  # 静默
    bars += [(100, 100.0)]                                    # 段 B：-0.99% 触发 @-100（反向）
    _seed(session, now, bars)
    wins = _call(session)
    assert len(wins) == 2                                     # 方向不同不并


def test_single_segment(session):
    now = utc_now_naive()
    _seed(session, now, [(20, 100.0), (15, 101.0)])           # 单触发 @-15
    wins = _call(session)
    assert len(wins) == 1
    assert wins[0].segment_count == 1
    assert wins[0].peak_change_pct == pytest.approx(wins[0].change_pct, abs=1e-6)


def test_merge_gap_is_configurable(session, monkeypatch):
    monkeypatch.setattr(config, "ANNOTATION_EVENT_MERGE_GAP_MINUTES", 30)
    now = utc_now_naive()
    bars = (
        [(120, 100.0), (115, 101.0), (110, 102.0)]
        + [(m, 102.0) for m in (105, 100, 95, 90, 85, 80, 75)]
        + [(70, 103.0), (65, 104.0)]                          # 静默 35min > 30 → 拆成 2
    )
    _seed(session, now, bars)
    assert len(_call(session)) == 2
```

- [ ] **Step 2：跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_annotation_windows.py -v`
Expected: 多条 FAIL（现状把段 A 内部算 run、不跨 35min 间隔合并、且无 `segment_count` 等字段填值）。先确认是"行为不符"而非导入/fixture 报错；若 fixture 报错（如 `utc_now_naive` 导入路径不对）先修 fixture。

- [ ] **Step 3：实现——替换 `load_price_windows` 的 Step 1–3**

把 `services/annotation_service.py` 中从 `# Step 1：扫所有快照` 到函数 `return [t[2] for t in enriched][:200]` 的整段（约 L220–275）替换为：

```python
    # Step 1：扫快照，收集超阈值触发；保留原始 datetime 供合并用。
    triggers: list[dict] = []
    for current in rows:
        if current.timestamp < display_cutoff:
            continue
        baseline_time = current.timestamp - timedelta(minutes=window_minutes)
        baseline = _nearest_snapshot(rows, baseline_time, current.timestamp, tolerance_minutes)
        if baseline is None or not baseline.price:
            continue
        change_pct = ((current.price - baseline.price) / abs(baseline.price)) * 100
        if abs(change_pct) < threshold_pct:
            continue
        triggers.append({
            "start_dt": baseline.timestamp,
            "end_dt": current.timestamp,
            "price_start": baseline.price,
            "price_end": current.price,
            "sign": 1 if change_pct >= 0 else -1,
            "asset_class": current.asset_class,
            "name": current.name,
        })

    if not triggers:
        return []

    # Step 2：按 window_end 升序，把同方向、相邻段静默间隔 ≤ merge_gap 的触发聚成一个事件。
    triggers.sort(key=lambda t: t["end_dt"])
    merge_gap = timedelta(minutes=max(1, int(getattr(config, "ANNOTATION_EVENT_MERGE_GAP_MINUTES", 60))))
    events: list[list[dict]] = []
    for t in triggers:
        if (
            events
            and events[-1][-1]["sign"] == t["sign"]
            and (t["start_dt"] - events[-1][-1]["end_dt"]) <= merge_gap
        ):
            events[-1].append(t)
        else:
            events.append([t])

    # Step 3：每个事件合成一个跨段窗口（净 + 峰值 + 振幅 + 段数）。
    windows: list[tuple[datetime, PriceWindowSchema]] = []
    for ev in events:
        first, last = ev[0], ev[-1]
        w_start, w_end = first["start_dt"], last["end_dt"]
        p_start, p_end = first["price_start"], last["price_end"]
        if not p_start:
            continue
        net_pct = (p_end - p_start) / abs(p_start) * 100
        span_prices = [
            r.price for r in rows
            if r.price is not None and w_start <= r.timestamp <= w_end
        ]
        low_price = min(span_prices) if span_prices else min(p_start, p_end)
        high_price = max(span_prices) if span_prices else max(p_start, p_end)
        extreme = high_price if first["sign"] >= 0 else low_price
        peak_pct = (extreme - p_start) / abs(p_start) * 100
        windows.append((w_end, PriceWindowSchema(
            symbol=symbol,
            asset_class=first["asset_class"],
            name=first["name"],
            window_start=timestamp_pair(w_start),
            window_end=timestamp_pair(w_end),
            configured_window_minutes=window_minutes,
            actual_window_minutes=round((w_end - w_start).total_seconds() / 60, 1),
            price_start=p_start,
            price_end=p_end,
            change_pct=net_pct,
            peak_change_pct=peak_pct,
            low_price=low_price,
            high_price=high_price,
            segment_count=len(ev),
            annotation_id=annotation_index.get((w_start, w_end)),
            is_primary=True,
        )))

    # 最新事件在前；截断 200。
    windows.sort(key=lambda t: t[0], reverse=True)
    return [w for _, w in windows][:200]
```

> 注：`timestamp_pair`、`annotation_index`、`rows`、`display_cutoff`、`tolerance_minutes`、`_nearest_snapshot` 均为函数内已有变量/导入，无需新增 import。`config` 已在文件顶部 import。

- [ ] **Step 4：跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_annotation_windows.py -v`
Expected: 5 个用例全 PASS。若某用例的触发数与预期不符（fixture 边界），按实际微调种子价格/时间（TDD 正常迭代），但断言语义不变。

- [ ] **Step 5：提交**

```bash
git add services/annotation_service.py tests/test_annotation_windows.py
git commit -m "feat(annotations): merge same-direction triggers within gap into one spanning event window"
```

---

## Task 3：前端——展示峰值 + 移除 secondary 死代码

**Files:** Modify `frontend/src/pages/AnnotationsPage.tsx`

- [ ] **Step 1：窗口卡片加显峰值**

在 `L556-558` 的 `change_pct` span 之后、`</button>`(L559) 之前插入：
```tsx
                            <span className="window-item-pct" title="峰值（相对起点）">
                              峰 {primary.peak_change_pct >= 0 ? "+" : ""}{primary.peak_change_pct.toFixed(2)}%
                            </span>
```

- [ ] **Step 2：移除 secondary 渲染块**

删除 `L560-580` 整个 `{secondaries.length ? ( ... ) : null}` 块。并把外层 `.map(({ primary, secondaries }) => ...)` 的解构改为 `.map(({ primary }) => ...)`（去掉 `secondaries`）。

- [ ] **Step 3：清理失效 import**

`L3` 的图标 import 里**只移除 `CornerDownRight`**（仅 secondary 块在用，L568）。**保留 `Layers`（L485 在用）和 `Circle`（L552 在用）——别误删。**

- [ ] **Step 4：typecheck + build**

Run（在 `frontend/`）：`cmd /c npm.cmd run typecheck` 然后 `cmd /c npm.cmd run build`
Expected: 均通过，无 TS 错误（含无未用变量）。

- [ ] **Step 5：提交**

```bash
git add frontend/src/pages/AnnotationsPage.tsx
git commit -m "feat(annotations-ui): show peak move, drop secondary windows"
```

---

## Task 4：全量回归 + 收尾（feat 分支 → main）

- [ ] **Step 1：全量后端测试**

Run: `D:\anaconda\python.exe -m pytest -q`
Expected: 全绿（原有 + 新增 5 个）。

- [ ] **Step 2：把本特性整理到 feat 分支并推送**

> 实现期若一直在 `main` 工作树改动（未提交），可在最后统一收口：
```bash
git switch -c feat/annotation-event-merge   # 若尚未在分支上
git add docs/specs/annotation-event-merge.md docs/specs/annotation-event-merge-plan.md
git commit -m "docs(annotations): event-merge design + plan"
git switch main
git merge --no-ff feat/annotation-event-merge -m "Merge feat/annotation-event-merge"
git push origin main
```
（git 操作等用户确认后再执行。）

---

## 回滚 / 备注
- 纯前端可独立回退（恢复 secondary 块）；后端逻辑回退即还原 Step 1–3。
- `ANNOTATION_EVENT_MERGE_GAP_MINUTES=0`→视为 1min，几乎等于旧"仅连续"行为（应急可在 `.env` 调）。
- 不动旧标注（spec §3.6）。服务器部署：`git pull` + 重建前端（`npm run build`）+ `systemctl restart`。
