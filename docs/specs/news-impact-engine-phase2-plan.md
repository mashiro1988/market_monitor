# Phase 2 — 窗口改单 15min 开收净 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把多尺度(15m+60m)+净门槛的标注窗口检测，简化为**单个 15min、开收净触发、5min 断档**的窗口检测。

**Architecture:** 复用现有 `_scale_events` 的"触发→同向合并"骨架，只做三处收敛：① 配置塌成单档；② 去掉 `net_min` 二次门槛、把"最小净幅度"并进**唯一的 threshold**；③ 合并的"断档"判据从 span-based(旧 60min) 改成**扫描点相邻(end_dt 间隔 ≤ 5min)**，并删掉 `load_price_windows` 里的跨档合并。窗口仍是 **compute-on-read**（不落库），标注绑定的 A 策略属于 Phase 3、本期不做。

**Tech Stack:** Python 3 / SQLAlchemy / pytest；本地 python 必须用 `D:\anaconda\python.exe`（见 memory: local-env）。

---

## 关键设计澄清（实现前必读）

1. **threshold 现在= "该 15min 窗口必须达到的净幅度"，继承旧 `net_min` 的严格度、不是旧 trigger 的低值。**
   旧逻辑里 `net_min`(BTC 1.0% / NQ 0.6%) > `threshold`(0.5% / 0.3%)：trigger 只是"进入候选"，net_min 才是"够不够大算事件"。单档塌缩后只剩一个旋钮，**必须取旧 net_min 那一档的值**，否则 0.5% 级别的小腿会全冒出来。spec §0 写"删 net_min（触发阈值已是净门槛）"——这里的落地就是**把 threshold 抬到旧 net_min 水平**。最终值由回放校准（Task 6）。

2. **横跳不一定不出窗口——只有"每 15min 净幅度 < threshold"的横跳才不出。**
   用户假设"net≈0 横跳过不了净阈值"只在小振幅横跳成立。若某条腿在 15min 内净幅度 ≥ threshold，它就是一个真窗口；上下交替的大腿会按"变向收口"产出**交替的多个窗口**。这是单档模型的固有行为，靠 threshold 校准压制，不再用 net_min 合并杀。

3. **5min 断档 ⇒ 单个缺失快照就会把窗口劈开。**
   `merge_gap=5min` 意味着扫描点只要跳一格(缺一个快照)就断档。开市时段限频丢快照会造成**虚假劈窗**，由每小时 gap-repair 补洞后、下次 compute-on-read 自动愈合（与已定的 settle 作业集、A 策略一致）。本期接受，不额外处理。

4. **丢失慢趋势能力是有意的。** 删 60m 档后，每 15min 净 < threshold 的阴跌（如 6/10 夜 -0.22%/15min）**不再出窗口**。用户已拍板（spec「删 60m 档」）。Task 1 用一个测试把这个行为显式钉住，避免被误当 bug"修回去"。

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `config.py` | 窗口档位 + 断档阈值常量 | `ANNOTATION_WINDOW_SCALES` 塌成单档、删 `net_min_pct`；新增 `ANNOTATION_EVENT_MERGE_GAP_MINUTES = 5` |
| `services/annotation_service.py` | 窗口检测 | `_scale_events` 删 net_min、改扫描点断档；`load_price_windows` 删跨档合并、单档直出；`_scales_for` 删 net_min_pct |
| `tests/test_annotation_window_scales.py` | 单档窗口行为 | 整文件重写（多尺度→单档语义） |
| `tests/test_annotation_windows.py` | 跨段合并行为 | 按 5min 扫描点断档调整断言 |
| `docs/specs/news-impact-engine-plan.md` | 路线图 | Phase 2 状态标「已实现」 |

---

## Task 1: 单档 15min 开收净窗口检测

把配置、`_scale_events`、`load_price_windows` 一起收敛到单档语义。三处改动互相依赖、必须一起落地才有可运行状态，故归一个 task，commit 在全绿后。

**Files:**
- Modify: `config.py:112-126`（`ANNOTATION_WINDOW_SCALES` 与注释）、`config.py` 增 `ANNOTATION_EVENT_MERGE_GAP_MINUTES`
- Modify: `services/annotation_service.py:382-434`（`_scale_events`）、`:437-521`（`load_price_windows`）、`:366-379`（`_scales_for`）
- Test: `tests/test_annotation_window_scales.py`（整文件重写）

- [ ] **Step 1: 重写窗口行为测试（RED）**

把 `tests/test_annotation_window_scales.py` 整文件替换为下面内容。fixture 改单档、无 `net_min_pct`；覆盖：方向性单窗、子阈值阴跌无窗、子阈值横跳无窗、5min 断档劈窗、连续合并、变向收口、显式参数路径。

```python
# -*- coding: utf-8 -*-
"""单 15min 开收净窗口（news-impact-engine Phase 2）：

触发 = (窗口末收盘 − 窗口初开盘)/初开盘 ≥ threshold（含第一根 bar）；
收口 = 同向且扫描点相邻(≤5min)则合并，变向或断档(>5min)则上一窗走完。
无 60m 档、无跨档合并、无独立 net_min（threshold 即最小净幅度）。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.price import PriceSnapshot
from services.annotation_service import load_price_windows
from services.time_utils import utc_now_naive


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(config, "ANNOTATION_REFERENCE_ASSETS", [])
    monkeypatch.setattr(config, "ANNOTATION_WINDOW_SCALES", {
        "TEST": [{"window_minutes": 15, "threshold_pct": 1.0, "pre_minutes": 30}],
    })
    monkeypatch.setattr(config, "ANNOTATION_EVENT_MERGE_GAP_MINUTES", 5)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _series(session, prices, step_min=5):
    """从 now 往回构造 5m 序列：prices[0] 最早。"""
    now = utc_now_naive().replace(second=0, microsecond=0)
    start = now - timedelta(minutes=step_min * (len(prices) - 1))
    for i, p in enumerate(prices):
        session.add(PriceSnapshot(
            timestamp=start + timedelta(minutes=step_min * i),
            asset_class="futures", symbol="TEST", name="TEST", price=p, source="t",
        ))
    session.commit()


def test_directional_move_one_window(session):
    """单向急跌 -1.5%（15min 净 ≥ 1.0%）→ 1 个窗口，方向为负。"""
    prices = [10000.0] * 6 + [9950.0, 9900.0, 9870.0, 9850.0] + [9850.0] * 6
    _series(session, prices)
    wins = load_price_windows(session, "TEST", hours=24)
    assert len(wins) == 1
    assert wins[0].change_pct == pytest.approx(-1.5, abs=0.1)
    assert wins[0].configured_window_minutes == 15


def test_subthreshold_drift_no_window(session):
    """慢阴跌：每 15min 净仅 ~0.22%（< 1.0% 阈值）→ 不出窗口（删 60m 档的有意取舍）。"""
    n = 16
    prices = [10000.0] * 4 + [10000.0 * (1 - 0.0125 * i / n) for i in range(1, n + 1)] + [9875.0] * 4
    _series(session, prices)
    assert load_price_windows(session, "TEST", hours=24) == []


def test_subthreshold_chop_no_window(session):
    """小振幅横跳：每条腿 ±0.6%（< 1.0% 阈值）→ 不触发、不出窗口。"""
    base = 10000.0
    prices = [base]
    for _ in range(6):
        prices += [base * 1.006, base * 1.006, base, base]
    _series(session, prices)
    assert load_price_windows(session, "TEST", hours=24) == []


def test_gap_over_5min_splits_into_two(session):
    """两段同向急跌，中间隔一段不触发的平台(>5min 扫描点断档) → 2 个窗口。"""
    # 第 1 段：-1.2% 急跌；平台 5 根(25min)不动；第 2 段：再 -1.2%。
    prices = (
        [10000.0] * 3
        + [9940.0, 9880.0]                       # 段1：15min 内 -1.2%
        + [9880.0, 9880.0, 9880.0, 9880.0, 9880.0]   # 平台：扫描点不触发 → 断档
        + [9820.0, 9760.0]                       # 段2：再 -1.2%
        + [9760.0] * 3
    )
    _series(session, prices)
    wins = load_price_windows(session, "TEST", hours=24)
    assert len(wins) == 2


def test_continuous_same_direction_merges(session):
    """连续多根同向急跌(扫描点相邻) → 合并成 1 个窗口、segment_count > 1。"""
    prices = [10000.0] * 3 + [9930.0, 9860.0, 9790.0, 9720.0] + [9720.0] * 3
    _series(session, prices)
    wins = load_price_windows(session, "TEST", hours=24)
    assert len(wins) == 1
    assert wins[0].segment_count >= 2


def test_direction_flip_closes_window(session):
    """急涨后紧接急跌(连续、变向) → 收口成 2 个窗口，符号相反。"""
    prices = [10000.0] * 3 + [10120.0, 10240.0] + [10120.0, 10000.0] + [10000.0] * 3
    _series(session, prices)
    wins = load_price_windows(session, "TEST", hours=24)
    assert len(wins) == 2
    signs = {1 if w.change_pct > 0 else -1 for w in wins}
    assert signs == {1, -1}


def test_explicit_params_single_scale(session):
    """显式传 threshold/window（调试路径）：单档、阈值即净门槛。"""
    prices = [10000.0] * 3 + [9930.0, 9860.0, 9790.0] + [9790.0] * 3
    _series(session, prices)
    wins = load_price_windows(session, "TEST", hours=24, threshold_pct=0.5, window_minutes=15)
    assert len(wins) >= 1
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `D:\anaconda\python.exe -m pytest tests/test_annotation_window_scales.py -q`
Expected: FAIL（旧 `load_price_windows` 仍多尺度 + net_min；`test_subthreshold_chop_no_window`、`test_gap_over_5min_splits_into_two` 等会挂；fixture 用了尚不存在的 `ANNOTATION_EVENT_MERGE_GAP_MINUTES` 行为）。

- [ ] **Step 3: 配置塌成单档**

`config.py` 把 `ANNOTATION_WINDOW_SCALES` 与上方注释（`:112-126`）替换为：

```python
# 标注窗口（news-impact-engine Phase 2）：每品种**单** 15min 档。
# 触发 = 窗口开收净 (末收 − 初开)/初开 ≥ threshold；threshold 即"算一个事件的最小净幅度"
# （继承旧 net_min 的严格度，非旧低位 trigger）。无 60m 档、无独立 net_min。
# 阈值由 6/10 夜回放校准（docs/specs/news-impact-engine-phase2-plan.md Task 6）。
# 显式传 threshold/window 的调试路径不走本配置。
ANNOTATION_WINDOW_SCALES = {
    "BTC/USDT": [{"window_minutes": 15, "threshold_pct": 1.0, "pre_minutes": 30}],
    "NQ=F":     [{"window_minutes": 15, "threshold_pct": 0.6, "pre_minutes": 30}],
}

# 断档阈值：相邻触发扫描点(end_dt)间隔 > 此值 → 上一个窗口走完。
# 5min = 一个快照步长（跳一格即断档）。开市丢快照造成的虚假劈窗由 gap-repair 补洞后 compute-on-read 自愈。
ANNOTATION_EVENT_MERGE_GAP_MINUTES = 5
```

- [ ] **Step 4: `_scale_events` 删 net_min + 扫描点断档**

`services/annotation_service.py:382-434` 整个函数替换为：

```python
def _scale_events(rows: list[PriceSnapshot], display_cutoff: datetime, tolerance_minutes: int,
                  scale: dict, merge_gap: timedelta) -> list[dict]:
    """单档触发扫描 + 同向相邻合并 → 原始窗口 dict 列表。
    触发：窗口开收净 = (current − baseline_{T−wm})/baseline ≥ threshold（baseline = 窗口初开盘，含第一根 bar）。
    合并：同方向 且 扫描点相邻（end_dt 间隔 ≤ merge_gap）→ 并进上一个；
          变方向 或 扫描点断档（> merge_gap）→ 上一个窗口走完，另起一个。
    无 net_min——threshold 本身就是该窗口必须达到的净幅度。"""
    wm = int(scale["window_minutes"])
    threshold = float(scale["threshold_pct"])

    triggers: list[dict] = []
    for current in rows:
        if current.timestamp < display_cutoff:
            continue
        baseline = _nearest_snapshot(rows, current.timestamp - timedelta(minutes=wm),
                                     current.timestamp, tolerance_minutes)
        if baseline is None or not baseline.price:
            continue
        change_pct = ((current.price - baseline.price) / abs(baseline.price)) * 100
        if abs(change_pct) < threshold:
            continue
        triggers.append({
            "start_dt": baseline.timestamp, "end_dt": current.timestamp,
            "price_start": baseline.price, "price_end": current.price,
            "sign": 1 if change_pct >= 0 else -1,
            "asset_class": current.asset_class, "name": current.name,
        })
    if not triggers:
        return []

    triggers.sort(key=lambda t: t["end_dt"])
    events: list[list[dict]] = []
    for t in triggers:
        if (events and events[-1][-1]["sign"] == t["sign"]
                and (t["end_dt"] - events[-1][-1]["end_dt"]) <= merge_gap):   # 扫描点相邻才并；跳一格即断档
            events[-1].append(t)
        else:
            events.append([t])

    out: list[dict] = []
    for ev in events:
        first, last = ev[0], ev[-1]
        if not first["price_start"]:
            continue
        out.append({
            "start": first["start_dt"], "end": last["end_dt"],
            "sign": first["sign"], "segments": len(ev),
            "asset_class": first["asset_class"], "name": first["name"],
            "wm": wm, "pre": int(scale.get("pre_minutes", CONTEXT_PRE_MINUTES_DEFAULT)),
        })
    return out
```

（关键差异：删了 `net_min` 读取与 `if abs(net) < net_min: continue` 整块；合并条件由 `t["start_dt"] - last["end_dt"]` 改为 `t["end_dt"] - last["end_dt"]`。）

- [ ] **Step 5: `load_price_windows` 单档直出、删跨档合并**

`services/annotation_service.py:437-521`。保留取数/`display_cutoff`/`tolerance`/`ref_rows`/`annotation_index`/`price_at` 不变；把"各档独立生成 + 跨档合并"那段（约 `:477-492`，`raw=[]` 循环到 `merged` 构建完）替换为单档直出：

```python
    # 单档（Phase 2）：直接取第一档，_scale_events 内部已做同向相邻合并，无跨档合并。
    scale = scales[0]
    merge_gap = timedelta(minutes=max(1, int(getattr(config, "ANNOTATION_EVENT_MERGE_GAP_MINUTES", 5))))
    merged = _scale_events(rows, display_cutoff, tolerance_minutes, scale, merge_gap)
```

同时把函数顶部 `max_wm = max(int(s["window_minutes"]) for s in scales)` 改成 `max_wm = int(scales[0]["window_minutes"])`，并删掉原来 `merge_gap = timedelta(...)` 的旧定义行（避免重复）。下游 `for m in merged:` 构建 `PriceWindowSchema` 的循环（`:494-517`）原样保留。

- [ ] **Step 6: `_scales_for` 删 net_min_pct**

`services/annotation_service.py:366-379`：把三处返回 dict 里的 `"net_min_pct": ...` 键删掉（显式参数路径、config 路径、告警规则回退路径）。config 路径直接 `return scales`（现在已无 net_min_pct）；另两处删该键即可。

- [ ] **Step 7: 跑新测试确认 GREEN**

Run: `D:\anaconda\python.exe -m pytest tests/test_annotation_window_scales.py -q`
Expected: PASS（7 个用例全过）。若 `test_gap_over_5min_splits_into_two` 仍合并成 1，检查 Step 4 的合并条件是否改成了 `end_dt` 差。

- [ ] **Step 8: Commit**

```bash
git add config.py services/annotation_service.py tests/test_annotation_window_scales.py
git commit -m "feat(news-engine): Phase2 窗口塌成单15min开收净 + 5min断档"
```

---

## Task 2: 调和 `test_annotation_windows.py` 的跨段合并断言

该文件走显式参数路径（`threshold_pct=0.5, window_minutes=5`），不受 config 影响，但**受新的扫描点断档语义影响**：旧 span-based 合并能跨较大间隔合并，新 `end_dt`-based 在 5min 外就断。需逐个核对断言。

**Files:**
- Test: `tests/test_annotation_windows.py`

- [ ] **Step 1: 跑该文件看 fallout**

Run: `D:\anaconda\python.exe -m pytest tests/test_annotation_windows.py -q`
Expected: 部分用例 FAIL（窗口数/边界因断档判据变化）。

- [ ] **Step 2: 逐个核对并修断言**

对每个失败用例：读它构造的价格序列，按新规则（扫描点 `end_dt` 间隔 > 5min 即断档、变向即收口、无 net_min）**手算**期望的窗口数与方向，更新断言。注意 `window_minutes=5` 下基线是前一根 5min bar，触发更密。**不要为了让旧断言通过而改实现**——实现以 Task 1 为准；这里只改测试期望，并在改动处加一行注释说明新语义下为何是这个数。

- [ ] **Step 3: 跑该文件确认 GREEN**

Run: `D:\anaconda\python.exe -m pytest tests/test_annotation_windows.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_annotation_windows.py
git commit -m "test(news-engine): annotation_windows 断言对齐 5min 扫描点断档语义"
```

---

## Task 3: 全套回归 + spec 状态更新

**Files:**
- Modify: `docs/specs/news-impact-engine-plan.md`（Phase 2 状态）

- [ ] **Step 1: 全套测试**

Run: `D:\anaconda\python.exe -m pytest tests/ -q`
Expected: PASS（全绿）。若有其它文件因窗口语义挂了，按 Task 2 的方式核对修断言（不改实现）。

- [ ] **Step 2: spec 标 Phase 2 已实现**

`docs/specs/news-impact-engine-plan.md` 的 `### Phase 2 — 窗口改单 15min 开收净【小】` 标题追加「【已实现】」，并在其下补一行：
`- **已实现**：单档 15min 开收净触发 + 5min 扫描点断档；删 60m/net_min/跨档合并。阈值待 6/10 夜回放校准（Task 6，非阻塞）。`

- [ ] **Step 3: Commit**

```bash
git add docs/specs/news-impact-engine-plan.md
git commit -m "docs(news-engine): Phase2 标已实现"
```

---

## Task 4（跟进，非阻塞）: 6/10 夜回放校准阈值

实现正确性不依赖此 task；它只定 `threshold_pct` 的最终数值。**在服务器上跑**（本地库自 2026-05-17 停更，无近期数据；见 memory: local-env / remote-data-access）。

- [ ] **Step 1**: 在 mmon.top 上对 BTC/USDT 与 NQ=F 跑 6/10~6/11 夜的 `load_price_windows`，看产出的窗口数与边界。
- [ ] **Step 2**: 若大振幅横跳冒出过多交替小窗 → 调高 `threshold_pct`；若真实事件被漏 → 调低。目标：6/10 夜的真实方向性事件被抓、横跳被压。
- [ ] **Step 3**: 把校准后的值写回 `config.ANNOTATION_WINDOW_SCALES`，commit `chore(news-engine): Phase2 阈值按 6/10 回放校准`。

---

## 验证总览
- Task 1 GREEN：单档语义的 7 个行为用例全过。
- Task 2/3 GREEN：`tests/` 全绿，无实现回改。
- Task 4：服务器回放后阈值定稿（非阻塞）。
- A 策略（标注只放"已 settle+已走完"窗口、改动推复核）= **Phase 3**，本计划不含。
