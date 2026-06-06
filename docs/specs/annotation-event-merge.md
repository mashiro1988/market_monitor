# 标注"事件窗口"跨段合并 设计

> 把标注页的价格"事件窗口"从「只合并 ≤15min 连续触发」升级为「**同方向、相邻段静默间隔 ≤1h（可配）也合并成一个跨段事件窗口**」——一事件一窗口、一次标注，覆盖整段（含中间 <1h 间隙）。
> 创建：2026-06-06。

## 1. 目标

标注一条新闻/事件的价格冲击时，应把该事件造成的多段同向异动（中间有 <1h 的短暂停顿）**合成一个跨段窗口**来标注，而不是拆成多个 15min 小窗口。

## 2. 背景与现状

价格"事件窗口"由 `services/annotation_service.py` 的 `load_price_windows(session, symbol, hours, threshold_pct=None, window_minutes=None)` 产出（消费方：`GET /api/annotations/windows`，返回 `list[PriceWindowSchema]`，约 L183–275）：

- **Step 1 触发检测**：扫每根 5min 快照，算其相对 `window_minutes`（如 15min）前的涨跌；超 `threshold_pct` 记为一个"触发"（一串重叠的滚动窗口）。
- **Step 2 合并**：相邻触发**同方向**且 `window_end` 间隔 ≤ `window_minutes` 时归为一个 run；run 首 = `is_primary=True`（可标注），其余 = `is_primary=False`（secondary，嵌套、不单独标注）。
- **前端** `frontend/src/pages/AnnotationsPage.tsx` 已按 `is_primary` 渲染：primary 为事件卡片，secondary 嵌套其下（L161、L561–567）。

**局限**：① 合并间隔写死 = `window_minutes`（≈15min），太短；② primary 窗口只覆盖**第一个** 15min 触发的 `(window_start, window_end)`，不是整段事件跨度。

## 3. 设计（在现有触发上加"合并层"）

保留 Step 1 触发检测不动（已验证），只重写 Step 2/3 的分组与合成。

### 3.1 配置
- 新增 `config.ANNOTATION_EVENT_MERGE_GAP_MINUTES = int(os.getenv("ANNOTATION_EVENT_MERGE_GAP_MINUTES", "60"))`。
- `window_minutes`（单触发灵敏度）与合并间隔**解耦**：前者管"什么算一次异动"，后者管"哪些异动算同一事件"。

### 3.2 合并逻辑（重写 Step 2/3）

**前置改动（重要，否则条件算不出来）**：现状 Step 1 的触发元组是 `(current.timestamp, kwargs)`，`baseline.timestamp`（即 window_start 的原始 datetime）只以 `timestamp_pair` 字典塞进 `kwargs`、随后被丢弃（`L233-248`）。合并条件要用到它，所以 **Step 1 必须把 `baseline.timestamp` 也带进元组**（如 `(current.timestamp, baseline.timestamp, kwargs)`），Step 2 才能拿到 `t.window_start`。`merge_gap` 必须在**函数体内按调用时读 `config.ANNOTATION_EVENT_MERGE_GAP_MINUTES`**（不要绑成默认参数），否则测试 monkeypatch 不生效。

触发按 `window_end` 升序（现有 `triggers.sort(key=lambda t: t[0])` 已满足）。维护"当前事件"的 `sign` 与 `last_end`（最后并入触发的 `window_end`）：

- 下一个触发 `t` **续入当前事件**，当且仅当：
  `sign(t) == event.sign` **且** 静默间隔 `(t.window_start − event.last_end) ≤ merge_gap`。
  否则**另起新事件**（重置 `sign`、`last_end`，并开新合成累加器）。
- **静默间隔**定义 = 下一段起点 − 上一段终点（分钟）。同一突发内触发重叠 → 间隔为负 → 必然续入；两突发间的安静期 = 真实间隔，用它跟 1h 比，精确对应"两段间隔 ≤1h"。

> 改动量级说明：相对现状这不是"两行改动"——含 ① Step 1 元组多带 `baseline.timestamp`；② 度量换成"静默间隔"`(start − last_end)`；③ 阈值换成 `merge_gap`；④ Step 2/3 由"标 primary/secondary"改成"按事件累加 + 合成一个窗口"（见 3.3），是一次完整重写。

### 3.3 每个事件合成一个窗口
对一组并入同一事件的触发：

| 字段 | 取值 |
|---|---|
| `window_start` | 事件**首**触发的 `window_start`（baseline ts） |
| `window_end` | 事件**末**触发的 `window_end`（current ts） |
| `price_start` | 首触发 `price_start` |
| `price_end` | 末触发 `price_end` |
| `change_pct`（净变动） | `(price_end − price_start) / abs(price_start) × 100` |
| `low_price` / `high_price` | `[window_start, window_end]` 区间内所有快照价的最小 / 最大（用函数里已有的 `rows` 扫） |
| `peak_change_pct`（峰值） | 沿事件方向到极值的偏离：up 取 `(high_price − price_start)/abs(price_start)×100`；down 取 `(low_price − price_start)/abs(price_start)×100` |
| `segment_count` | 并入的触发数 |
| `actual_window_minutes` | 跨度分钟 = `(window_end − window_start)/60` |
| `configured_window_minutes` | `window_minutes`（不变） |
| `is_primary` | 恒 `True`（一窗口 = 一完整事件，**不再发 secondary**） |
| `annotation_id` | 按合并后 `(window_start, window_end)` 查 `annotation_index` |

> 合成实现：遍历触发时按事件累加 `first_window_start / last_window_end / first_price_start / last_price_end / 触发计数`，事件结束（或列表结束）时产出一个 `PriceWindowSchema`。`low/high` 从函数已有的 `rows` 筛 `window_start ≤ ts ≤ window_end`（含端点）求 min/max。`actual_window_minutes` 现语义 = **事件跨度**（不再是滚动窗口长度）；无消费方依赖旧语义。

### 3.4 Schema（`schemas/annotations.py: PriceWindowSchema`）
`PriceWindowSchema` 是**仅作响应**的模型，唯一构造点是 `annotation_service.py:269` 的 `PriceWindowSchema(**kwargs)`（无位置构造、不反序列化外部输入），加字段安全；为自洽给默认值：
- `peak_change_pct: float = 0.0`
- `low_price: float = 0.0`
- `high_price: float = 0.0`
- `segment_count: int = 1`

`is_primary` 保留（恒 True）以最小化前端改动面（前端 `L161`、`types.ts` 仍引用它）。

### 3.5 前端（`frontend/`）
- `src/api/types.ts`：`PriceWindow` 镜像新增字段（**至少 `peak_change_pct`**——TS 结构化类型只在代码*读取*缺失字段时报错；卡片要显示峰值就必须加）。
- `src/pages/AnnotationsPage.tsx`：
  - 后端只发 `is_primary=True` 窗口 → 分组逻辑（`L158-168`）每窗口自成一组、`secondaries=[]`，**渲染照常不崩**；为整洁移除 secondary 子列表（`L560-580`）及随之失效的 dead import（`CornerDownRight`/`Layers` 等，`L3`，留着可能触发 `noUnusedLocals`）与 CSS（`.window-secondary-list`/`.window-item.secondary`）。
  - 事件卡片（`L556-558` 现仅显示 `change_pct`）**加显峰值 `peak_change_pct`**（可附 `low/high`、`segment_count`）。
- `npm run typecheck` + `npm run build` 通过。

> 副作用（符合预期）：合并窗口更宽 → `auto_annotate*` 拉的上下文新闻范围更大、保存的 `candidate_news_ids` 更多，正是"一事件一标注"想要的。保存路径无碍：`upsert_annotation` 用 `_find_window_snapshot` 精确匹配 `window_start/end` 两端快照——合并窗口两端都是真实快照（首触发 baseline、末触发 current），匹配成立；净 `change_pct` 由两端价重算（`L354`）。

### 3.6 旧标注
**不做兼容**（已确认）。新标注按跨段 `(start, end)` 存。旧 15min 标注仍在库，但不再匹配显示窗口（视作待重标）。服务器是全新库（≈0 标注），影响可忽略。

## 4. 边界情况
- **单触发事件**：`segment_count=1`，net=peak=该触发涨跌，窗口即该 15min。
- **全程同向无间隙**：合成一个长窗口。
- **方向翻转**：另起事件（即使时间相邻）。
- **间隔 > merge_gap**：拆成两个事件。
- **`price_start` 为 0/None**：沿用现有 `not baseline.price` 守卫跳过，防除零。
- **200 上限 + 排序**：合成后事件数远少于触发数，`[:200]` 更安全。排序简化为**单键 `window_end`（或 `window_start`）DESC**（最新在前）——一事件一窗口后，现状用于 primary/secondary 的 `(anchor, end)` 两段式排序不再需要。

## 5. 测试（TDD）

> ⚠️ 本仓库**没有 conftest / 内存 DB fixture 先例**：`test_alert_engine.py` 的手搓 `FakeSession` 太简陋，喂不了 `load_price_windows` 的真实 SQLAlchemy 查询链（两个模型、`.filter().order_by().all()`）。本测试需**新建一个真实会话 fixture**：`create_engine("sqlite:///:memory:")` → `Base.metadata.create_all(engine)` → `Session` → `session.add(PriceSnapshot(...))` 造数据。

后端 `tests/test_annotation_windows.py`（新建）：
- **fixture**：内存 SQLite 引擎 + 建表 + session。
- **造数据**：`PriceSnapshot` 时间戳**相对 `utc_now_naive()` 倒推**（否则落在 `display_cutoff = now − hours` 之外，扫不出触发）；以**显式 `threshold_pct` / `window_minutes`** 调 `load_price_windows`，绕开 `load_alert_price_rules` 依赖。
- **用例**：
  1. 两段同向、间隔 40min（<60）→ **1 个**窗口；断言 `window_start/end` 跨整段、`change_pct`(净)/`peak_change_pct`/`low/high`/`segment_count`。
  2. 两段同向、间隔 90min（>60）→ **2 个**窗口。
  3. 相邻一涨一跌 → **2 个**窗口（方向不同不并）。
  4. 单段 → 1 个窗口，`segment_count=1`，net==peak。
  5. `monkeypatch.setattr(config, "ANNOTATION_EVENT_MERGE_GAP_MINUTES", 30)` → 40min 间隔变 **2 个**窗口（依赖 3.2 的"函数内调用时读 config"）。

前端：`npm run typecheck` + `npm run build`。

## 6. 不在本次范围（YAGNI）
- 旧标注迁移（已决定不兼容）。
- 合并事件下的子段（"本事件由 N 段组成"）明细展示——留作以后增强。
- 标注导出 / 训练集生成（独立任务）。

## 7. 涉及文件
- `config.py` — 新增 `ANNOTATION_EVENT_MERGE_GAP_MINUTES`
- `services/annotation_service.py` — 窗口函数 Step 2/3 重写 + 合成
- `schemas/annotations.py` — `PriceWindowSchema` 新字段
- `frontend/src/api/types.ts` — 镜像
- `frontend/src/pages/AnnotationsPage.tsx` — 渲染
- `tests/test_annotation_windows.py` — 后端测试（新建）
