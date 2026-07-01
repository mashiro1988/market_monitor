# Phase 3b — A 策略落地（窗口 settle/走完门 + 已标改动推复核）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** 落地 2026-06-22 锁定的 **A 策略**：① 只有「已 settle + 已走完」的窗口可标；② 标注冻结边界（已存）；③ 已标窗口被 backfill 改动（边界挪/劈/并）→ 标「需复核」，不静默改/丢。

**Architecture:** 窗口仍 compute-on-read。用**时间余量**作为「settle + 走完」的务实判据：窗口 `window_end ≤ now − SETTLE_MARGIN` 才 `annotatable`（余量覆盖：gap-repair 每小时 :37 跑一次 ≈ 最坏 60min + 走完缓冲）。`needs_review` = 已标注的 (start,end) 不在当前重算窗口集合里（被 backfill 劈/并/挪了）。两者都是 compute-on-read 派生、不落库。

**Tech Stack:** Python 3 / SQLAlchemy / pytest（本地 `D:\anaconda\python.exe`）；React/TS 前端按 flag 置灰 + 角标。

---

## 关键设计决策（实现前必读）

1. **`annotatable` = 只冻结最新那一个窗口**（2026-06-28 简化，用户："只冻结最新的窗口就好了，逻辑清晰明了"）。判据：`window_end == max(所有窗口的 end)` **且** `window_end > now − ANNOTATION_SETTLE_MARGIN_MINUTES` → 该窗口 `annotatable=false`（还在生长边缘，可能随新 bar 合并/延伸）；**其余窗口一律可标**。更早的窗口后面都已有更晚窗口出现 → 天然判定「已走完」。最新窗口若超过余量没动（收盘/静默）也判走完、可标，不会被无限冻结。余量默认 **30min**（原 90min 是"settle+走完"双重缓冲，简化后只需覆盖单窗 live 边缘抖动）。backfill 改动已标窗口边界由 `needs_review` 兜底。可配。
2. **不过滤、只打 flag**：被冻结的最新窗口仍返回，但 `annotatable=false`，前端置灰禁止标注（比直接隐藏更可懂——用户能看到"有个新窗口但还在走"）。
3. **`needs_review` 判据**：某条已标注的 `(window_start, window_end)` **不在**当前重算窗口的 (start,end) 集合里 → 被 backfill 改了边界/劈/并 → `needs_review=true`。在 `list_annotations` 里按 symbol 分组、每 symbol 重算一次窗口比对。阈值变化导致的不匹配也归入复核（合理：窗口定义变了就该重看）。
4. **不静默改/丢**：`needs_review` 只是个**提示 flag**，标注数据原样保留，由人决定改不改。导出不受影响。

---

## File Structure

| 文件 | 改动 |
|---|---|
| `config.py` | 新增 `ANNOTATION_SETTLE_MARGIN_MINUTES = 90` |
| `schemas/annotations.py` | `PriceWindowSchema` 加 `annotatable: bool`；`AnnotationListItem` 加 `needs_review: bool` |
| `services/annotation_service.py` | `load_price_windows` 算 `annotatable`；`list_annotations` 算 `needs_review`（按 symbol 重算窗口比对）|
| `frontend/src/pages/AnnotationsPage.tsx` | 非 annotatable 窗口置灰禁标；needs_review 标注显「需复核」角标 |
| `tests/test_annotation_windows.py` / `test_annotation_v2.py` | annotatable 门 + needs_review 单测 |

---

## Task 1: 窗口 `annotatable` 门（settle + 走完）

**Files:** `config.py`、`schemas/annotations.py:45-60`、`services/annotation_service.py:load_price_windows`、`tests/test_annotation_windows.py`

- [ ] **Step 1: 写失败测试（RED）** —— 在 `tests/test_annotation_windows.py` 加：构造一个**刚结束**的窗口（window_end 近 now）与一个**很久前**的窗口，断言前者 `annotatable is False`、后者 `True`。
```python
def test_window_annotatable_gate(session, monkeypatch):
    monkeypatch.setattr(config, "ANNOTATION_SETTLE_MARGIN_MINUTES", 90)
    now = utc_now_naive()
    # 远窗口（>90min 前结束）：annotatable=True
    _seed(session, now, [(300, 100.0), (295, 101.0)])           # 触发 @-295
    # 近窗口（刚结束，<90min）：annotatable=False
    _seed(session, now, [(20, 100.0), (15, 101.0)])             # 触发 @-15
    wins = _call(session)                                       # threshold=0.5,wm=5
    assert any(w.annotatable for w in wins)                     # 远窗口可标
    assert any(not w.annotatable for w in wins)                 # 近窗口不可标
```
（用本文件既有 `_seed`/`_call`；字段名按 `PriceWindowSchema.window_end` 的 `TimeFields` 实际属性对齐。）

- [ ] **Step 2: 跑 RED** → `pytest tests/test_annotation_windows.py -q -k annotatable` → FAIL（无 annotatable 字段）。

- [ ] **Step 3: config 加常量**
```python
# 标注 settle 余量（news-impact-engine Phase 3b）：窗口结束后至少过这么久才放给人标——
# 覆盖 gap-repair 每小时 settle（最坏 ~60min）+「走完」缓冲。可配。
ANNOTATION_SETTLE_MARGIN_MINUTES = int(os.getenv("ANNOTATION_SETTLE_MARGIN_MINUTES", "90"))
```

- [ ] **Step 4: schema 加字段** —— `PriceWindowSchema` 加 `annotatable: bool = True`（默认 True 不破坏既有构造）。

- [ ] **Step 5: `load_price_windows` 算 annotatable** —— 构建 `PriceWindowSchema` 时：
```python
        settle_margin = timedelta(minutes=int(getattr(config, "ANNOTATION_SETTLE_MARGIN_MINUTES", 90)))
        annotatable = m["end"] <= utc_now_naive() - settle_margin
```
把 `annotatable=annotatable` 传进 `PriceWindowSchema(...)`。

- [ ] **Step 6: 跑 GREEN** → `pytest tests/test_annotation_windows.py -q` → PASS。

- [ ] **Step 7: Commit** → `feat(news-engine): Phase3b 窗口 annotatable 门(settle+走完余量)`

---

## Task 2: 已标窗口被改动 → `needs_review`

**Files:** `schemas/annotations.py`（`AnnotationListItem`）、`services/annotation_service.py:list_annotations`、`tests/test_annotation_v2.py`

- [ ] **Step 1: 写失败测试（RED）** —— 在 `tests/test_annotation_v2.py` 加：标一个窗口 (W_START,W_END)，再标一个边界与任何当前重算窗口都不符的"幽灵"标注（直接插 `NewsPriceAnnotation`，给个当前价格序列里不会重算出来的 (start,end)），断言 `list_annotations` 把幽灵那条 `needs_review=True`、正常那条 `False`。
```python
def test_list_annotations_flags_window_changed(session):
    n1, _, _ = _seed(session)
    # 正常：先有价格→会重算出窗口，再按该窗口标注（annotation_id 能对上）
    # 幽灵：插一条 window 边界对不上任何当前重算窗口的标注
    session.add(NewsPriceAnnotation(
        symbol="BTC/USDT", window_start=W_START - timedelta(hours=5), window_end=W_END - timedelta(hours=5),
        context_start=W_START, context_end=W_END, change_pct=-2.0,
        news_roles=json.dumps({str(n1): "driver"}), no_clear_news=False,
        created_at=W_START, updated_at=W_START,
    ))
    session.commit()
    items = annotation_service.list_annotations(session, symbol="BTC/USDT", hours=240)
    ghost = [it for it in items if it.window_start.timestamp_utc.startswith((W_START - timedelta(hours=5)).strftime("%Y-%m-%d"))]
    assert ghost and ghost[0].needs_review is True
```
（具体构造按本文件既有 helper 调整；要点：幽灵标注的 (start,end) 在当前价格里重算不出来 → needs_review。）

- [ ] **Step 2: 跑 RED** → FAIL（无 needs_review 字段）。

- [ ] **Step 3: schema 加字段** —— `AnnotationListItem` 加 `needs_review: bool = False`。

- [ ] **Step 4: `list_annotations` 算 needs_review（id 比对，非字符串）** —— `load_price_windows` 已经把每个当前窗口里**边界精确匹配**到的已有标注的 `id` 填进 `PriceWindowSchema.annotation_id`（services/annotation_service.py:459-470,494）。所以一条标注「被 backfill 改了边界 / 劈 / 并」**等价于：它的 `id` 不在当前重算窗口的 annotation_id 集合里**。用整数 id 比对——无字符串格式漂移、复用既有匹配逻辑、与前端"已标注"状态天然一致。
```python
    # 当前重算窗口里"边界还对得上"的标注 id 集合（按 symbol 缓存）。
    cur_ids_by_symbol: dict[str, set] = {}
    def _cur_ann_ids(sym):
        if sym not in cur_ids_by_symbol:
            wins = load_price_windows(session, sym, hours)
            cur_ids_by_symbol[sym] = {w.annotation_id for w in wins if w.annotation_id is not None}
        return cur_ids_by_symbol[sym]
```
构建 `AnnotationListItem` 时：
```python
            needs_review=(row.id not in _cur_ann_ids(row.symbol)),
```
（两侧都用同一个 `hours`/`utc_now_naive()` 边界——`list_annotations` 与 `load_price_windows` 的 `window_end >= now-hours` 过滤一致，故正常标注一定能在重算窗口里找到自己的 id。）

- [ ] **Step 5: 跑 GREEN** → `pytest tests/test_annotation_v2.py -q` → PASS。注意既有 `test_list_annotations_carries_references` 等不应回归（正常标注 needs_review=False）。

- [ ] **Step 6: Commit** → `feat(news-engine): Phase3b 已标窗口被 backfill 改动→needs_review`

---

## Task 3: 前端置灰禁标 + 需复核角标

**Files:** `frontend/src/pages/AnnotationsPage.tsx`

- [ ] **Step 1:** 窗口列表里 `annotatable === false` 的窗口：禁用其标注入口（按钮置灰 / 点击提示"窗口尚未 settle，稍后再标"），不写死隐藏。
- [ ] **Step 2:** 已标列表里 `needs_review === true` 的项：显「需复核」角标（橙色），提示"窗口边界已被数据回补改动，请重看"。
- [ ] **Step 3:** `npm run typecheck && npm run build`（frontend 目录）通过。
- [ ] **Step 4: Commit** → `feat(news-engine): Phase3b 前端 settle 置灰 + 需复核角标`

---

## Task 4: 全套回归 + spec 状态

- [ ] **Step 1:** `D:\anaconda\python.exe -m pytest tests/ -q`（从 `D:\market_monitor`）→ 全绿。
- [ ] **Step 2:** `news-impact-engine-plan.md` 把 Phase 3「3b（A 策略落地）【待做】」改为【已实现】，记 `annotatable` 余量 90min + `needs_review` 判据。
- [ ] **Step 3: Commit** → `docs(news-engine): Phase3b 标已实现`

---

## 验证总览
- Task 1：远/近窗口 annotatable 真值区分（RED→GREEN）。
- Task 2：幽灵标注 needs_review=True、正常 False；既有列表用例不回归。
- Task 3：前端 typecheck+build。
- 余量 90min 与 needs_review 判据为务实近似，非严格"走完"结构判定——后者要逐窗口扫后继 slot，本期不做，时间门足够。
