# 持仓策略模块设计（2026-08-28）

> 来源：用户在 NotebookLM 与《系统化交易》（Robert Carver）学习对话中沉淀的《加密单币持仓框架 V1.1（系统化卡弗融合版）》，原文见用户桌面 `systematic-crypto-framework-v1-1.md`。本设计把该框架落成 Market Monitor 的一个页面 + 一条每日定时检查 + 若干企业微信动作提示。**系统只提醒、不下单。**

## 1. 背景与目标

用户（财务背景）持有 VIRTUAL 永续合约仓位（币安），交易纪律采用「软止损、日收盘确认」流派：不挂止损单，每个交易日收盘后人工核对价格是否跌破防线。痛点：防线位置（随波动率和新高每天变化）靠手算，容易忘、容易算错、容易被情绪干扰。

目标：录入持仓批次与参数后，系统每天自动取日收盘、算波动率、更新移动软止损线，把整个策略画成一张图；触发动作条件时推企业微信。所有参数（本金、预算、乘数、批次数据）页面可编辑——通用性靠「一切皆输入」保证，不写死任何用户校准值。

## 2. 精确口径（公式单一来源，实现照抄本节）

### 2.1 日 K 与「日收盘」
- 数据源：OKX 公开行情 `GET /api/v5/market/candles?instId={symbol}&bar=1Dutc&limit=300`，现取现算，不入库。
- `1Dutc` = 按 **UTC 00:00** 切日（北京时间早 8 点收盘）。**只用 `confirm == "1"`（已完结）的日 K**；当日未走完的 K 线不参与任何计算。
- 实时价（横幅展示用）：复用现有 5 分钟加密采集管道（VIRTUAL 加入现有加密符号清单，跟随 config 现行结构），页面兜底可调 OKX ticker。

### 2.2 日波动率（本项目沿用框架称呼"ATR"，实际口径是 EWMA 标准差）
- 对数收益 `r_t = ln(C_t / C_{t-1})`（C = 日收盘）。
- EWMA36 方差：`var_t = α·r_t² + (1−α)·var_{t−1}`，`α = 0.054`，从可得历史最早处热身（300 根日 K 远超 EWMA 有效记忆 ~37 天）。
- 日波动率 `vol_t = sqrt(var_t)`。

### 2.3 25% 守则（在用波动率的闩锁）
- 系统持久化「在用波动率」`v_used`（按币种，表 `strategy_symbol_state`）。
- 每日检查：`|vol_latest / v_used − 1| > 0.25` 时才更新 `v_used = vol_latest` 并记录「波动率更新」事件；否则沿用。
- 冷启动：首次运行 `v_used = vol_latest`。
- 持久化而非每日重放推导：闩锁是路径依赖状态，重放起点会随 300 根滚动窗漂移，落库才有稳定的「上一次计算时」语义。
- 图表历史线的口径：**当日权威读数以持久化 `v_used` 为准**；图上「软止损逐日阶梯」的历史段允许用重放近似（从入场日波动率起步走 25% 守则），两者在展示上可能有毫厘差，属预期，不做对账。

### 2.4 锚与防线（按批次）
- 锚 `H = max(入场价, {C_d : 该日 K 收盘时刻 > 批次入场时刻})`。入场当天该根日 K 尚未收盘时不计入；收盘时刻 = K 线起始 ts + 24h（UTC）。
- 软止损（4×，日收盘确认执行）：`soft = H × (1 − X_soft × v_used)`，默认 `X_soft = 4`。
- 硬防线（6×，仅图上提示，系统不能也不替用户挂单）：`hard = H × (1 − X_hard × v_used)`，默认 `X_hard = 6`。
- H 单调不减 → 防线只升不降（棘轮）；多批次的软止损随新高自然合流。

### 2.5 预算与锁盈
- 预算金额 `budget_$ = 本金 × 风险预算%`（默认 15%，可改）。
- 批次预算占用 `occupy_$ = 数量 × max(0, 入场价 − soft)`。
- 锁盈判定：`soft > 入场价` ⇒ 该批占用归零、「B2 额度释放」。
- 贴预算目标数量（减仓提示用）：`target_qty = budget_$ / (入场价 − soft)`（仅未锁盈时有意义）。

### 2.6 建仓计算器（模拟，不落库）
输入：价格 P（默认实时价）、预测值 F（默认 +10）、预算 %（默认 settings）、波动率 v（默认 `v_used`，可手改）。
输出：止损距离 `d = P × X_soft × v`、止损价 `P − d`、应买数量 `budget_$ × (F/10) / d`、名义金额、对本金杠杆倍数。

## 3. 数据模型（`models/strategy.py`，`create_all` 自动建表）

- `strategy_positions`：`id`、`symbol`（OKX instId）、`batch_label`（B1/B2…）、`entry_at`（UTC datetime）、`entry_price`、`quantity`、`forecast`（int）、`status`（open/closed）、`closed_at`、`close_price`、`note`、`created_at/updated_at`。
- `strategy_settings`（单行）：`capital`、`risk_budget_pct`、`x_soft`（4）、`x_hard`（6）、`ewma_alpha`（0.054）、`vol_update_threshold`（0.25）、`updated_at`。
- `strategy_symbol_state`：`symbol` PK、`v_used`、`v_used_at`、`updated_at`。
- `strategy_events`：`id`、`created_at`、`symbol`、`position_id`（可空）、`kind`（`stop_breach`/`vol_update`/`reduce_suggest`/`b2_unlocked`/`daily_ok`）、`message`、`payload_json`、`pushed`（bool）。事件既是页面「动作提示流」的数据，也是转换检测（同一状态不重复推送）的依据。
- 种子数据（首次部署后由用户在页面录入，或迁移脚本内置）：B1 = VIRTUAL-USDT-SWAP，entry_at 2026-08-26 23:33 UTC（北京 08-27 07:33），entry 0.7430，qty 23,590，forecast +10；settings capital 13,915 / budget 15%。

## 4. 服务与接口

- `services/strategy_engine.py`：**纯函数**——蜡烛序列 + 参数 → 波动率序列、每批次逐日 H/软硬线序列、占用、判定。不碰网络与数据库，好测。
- `services/strategy_service.py`：编排——取 OKX 蜡烛（带 10s 超时与失败留痕）、读写四张表、调 engine、生成事件、经现有 `alerts/dispatch` 推企业微信。
- API（沿用 `X-App-Token` 校验，全部挂 `/api/strategy/*`）：
  - `GET /overview?symbol=`：横幅 + 图表全部序列（日收盘、软止损逐日阶梯、硬防线、锚点、批次标记）+ 批次读数 + 数据新鲜度。
  - `GET /events?symbol=&limit=`
  - `POST/PUT/DELETE /positions[/{id}]`
  - `GET/PUT /settings`
  - `POST /simulate`：计算器。
- OKX 取数失败：overview 返回最近一次成功时间与 `stale` 标记，前端显示「数据滞后」，不空白、不猜数。

## 5. 每日检查与动作提示

- 调度：`CRON_SCHEDULES` 新增 `strategy_daily_check`，**北京 08:05**（= UTC 00:05，`SCHEDULE_TZ=Asia/Shanghai` 显式时区，遵循 api/app.py 既有 `_cron_trigger` 模式），`max_instances=1`。
- 流程：取蜡烛 → 2.2~2.5 全量计算 → 与上一状态比对 → 写 `strategy_events` → 需推送的经现有企业微信通道发出。
- 提示矩阵（推送均带冷却语义 = 仅状态转换时推，持续状态不重复轰炸）：

| kind | 触发 | 推送 |
|---|---|---|
| `stop_breach` | 最新确认收盘 < 该批 soft（由未破→破的转换） | ✅ 红：跌破软止损，按框架应清仓（附收盘/防线/批次） |
| `vol_update` + `reduce_suggest` | 25% 守则更新 `v_used` 且总占用 > `budget_$` | ✅ 黄：波动率变更，附目标数量与应减数量 |
| `b2_unlocked` | 某批 soft 首次抬过其入场价 | ✅ 绿：该批锁盈、额度释放，可开始找微观确认事件 |
| `daily_ok` | 收盘 ≥ soft | 仅入页面提示流，不推送 |

- 超预算但无波动率变更：页面横幅常驻黄色徽标，不推送（用户知情即可，避免每天挨骂）。
- 批次平仓只由用户在页面操作，系统永不自动改仓位状态。

## 6. 前端（`frontend/src/pages/StrategyPage.tsx`，导航新增「持仓策略」）

已与用户在视觉草图上定稿（`.superpowers/brainstorm/185-1787902123/content/layout-v2.html`）：

1. **决策横幅**：今日动作（持有/清仓提示/减仓提示，颜色区分）+ 昨收 vs 软止损与余量% + 风险占用/预算 + 在用波动率 + B2 状态。
2. **大图**（recharts，扩展现有 `Charts.tsx` 而非引新库；软止损用 `type="stepAfter"` 阶梯线）：日收盘价线、软止损阶梯线、①硬防线以下 `ReferenceArea` 红区、②锚（最高收盘）金点、③成本 `ReferenceLine` 虚线、批次入场标记。选定元素 ①②③，5 分钟细线与盈亏着色明确不做。
3. **底部三块**：批次表（增删改、平仓录入）、动作提示流（`strategy_events` 倒序）、建仓计算器。
4. 所有参数就地可编辑；`timestamp_utc` 处理遵守项目既有约定（naive UTC 需补 Z）。

## 7. 边界（本期不做）

宏观绿灯自动判定（用户明确暂不定义宏观）、微观形态识别（突破回踩/扫针收回靠人判）、多资产相关性与分散化乘数、自动下单、币安账户 API 对接、5 分钟细价格线与盈亏着色。数据结构天然多币种，界面本期只服务 VIRTUAL 跑通。

## 8. 验收标准

1. 录入种子 B1 后，overview 读数与本设计定稿日的手工运行一致（容差=浮点显示位）：`vol ≈ 4.94%`、`H = 0.7518`、`soft ≈ 0.6031`、`hard ≈ 0.5288`、占用 ≈ `$3,300 / 23.7%`、判定=持有、B2 未释放。
2. 改任一参数（本金/预算/乘数/批次字段）后刷新，全部读数即时重算。
3. 用测试夹具蜡烛模拟一根跌破软止损的日收盘，`strategy_daily_check` 产生 `stop_breach` 事件并推送企业微信（测试环境走 console 通道断言）。
4. OKX 接口断链时页面显示「数据滞后」而非空白或报错。
5. 引擎单测覆盖：H 锚定（含入场时刻在当根 K 未收盘时不计入、入场价保底）、EWMA 数值、25% 闩锁转换、锁盈/占用、计算器输出、事件转换去重。后端 pytest 与前端 vitest 全量通过。

## 9. 部署与文档同步

- 建表由启动时 `Base.metadata.create_all` 完成，无破坏性迁移；单 worker 约束不变（每日任务在既有进程内调度器运行）。
- 随实现同一提交同步：`ARCHITECTURE.md`（新模块）、`DATAFLOW.md`（OKX 日线现取 + 每日检查流）、`DECISIONS.md`（UTC 切日、闩锁持久化、H=max(入场价,最高收盘) 三项口径决策）、`PENDING.md`（任务台账）、`GLOSSARY.md`（新术语）。
