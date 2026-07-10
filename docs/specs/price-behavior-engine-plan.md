# 价格行为引擎（异动段 + 共振分 S）Implementation Plan

> **状态：已执行（2026-07-09，分支 `codex/price-behavior-engine`，前置合流 + Task 1-10 全部完成，pytest/vitest/build 全绿）。** 遗留三项均已闭环：T_ref 三档按校准首跑拍板（CL/^N225 启用、US_2Y 数据不足禁用，commit ec7196b）；retention 90 已落 config；切换开关 `BEHAVIOR_REPLACES_ANNOTATION_WINDOWS` 于 **Phase 2 退役**（标注窗口源固定段化）。后续演进见 `price-behavior-engine-phase2-plan.md`（2026-07-10 完成：事件窗 `s_score` → `rolling_peak`、分类三类化、同步相关整链删除、prompt v12）。

**Goal:** 把 v0.4 定稿落成可跑系统：BTC 三档异动段检测 → 段落库 → 共振分 S + 新闻命中自动分类（宏观新闻/纯共振/行业事件/情绪候选/无对照×新闻）→ 日面板（段数/档位/构成/联动曲线套装）→ 标注流对接 + 校准工具。总体目的：**判断当前 BTC 行情由技术面（情绪/庄家）、行业事件、宏观新闻还是纯资产共振驱动，并给出对应行动指引。**

**Architecture:** 新增 `services/behavior_segments.py`（段检测，复用现行 `_scale_events` 语义但不动它）、`services/resonance_score.py`（S/ESS/rolling S）、`services/behavior_classifier.py`（分类 job，落库）、`models/behavior.py`（段表 + point-in-time 日汇总表）、`/api/behavior/*` 三个端点、前端 `BehaviorPage`。现有标注窗口逻辑**原样保留**（preserve-calibrated-config），段→标注候选走开关切换。

**Tech Stack:** Python 3 / SQLAlchemy / APScheduler / pytest（本地 `D:\anaconda\python.exe`）；React + TS + Recharts + vitest；OpenAPI types 再生成。

---

## 锁定的设计（spec v0.4，不再讨论）

- **段**：15min 滚动开收净、5min 步进，0.3% 基档触发；连续同向、断档 5min（`ANNOTATION_EVENT_MERGE_GAP_MINUTES` 现值）合并成段；每段标**最高触及档位**（0.3/0.5/0.8）、净幅、振幅、关键触发时间（段内 |5min| 最大 bar）。
- **职责分层**：0.3 档只计数（日间频率对比 + 档位分布分母），**不归因不进构成不喂 LLM**；构成/归因从 0.5 档起。**无必审/抽审机制**（2026-07-09 用户拍板）：未确认段全部留存、随时可审，标注状态 = 未标注 / AI已标·未确认 / 已人工确认。
- **S 公式**：大窗口（段前 1h～段后 1h）15min 滚动序列，`z = chg / T_asset(0.3档)`，`S = Σ z_btc²·clip(z_ref·sign(z_btc),−1,1) / Σ z_btc²`；`ESS=(Σw)²/Σw²`（<5 标"证据薄"）；参照覆盖权重占比 <50% → 该参照不出分。判级 `max|S|`：≥0.5 共振 / 0.3–0.5 弱共振 / <0.3 独立（cutoff 进 config 回放校准）。**无 lag 概念、无方向期望判据（符号只展示）**。
- **分类十字格**（S × 新闻命中，新闻命中 = 段窗 ±30min 内 a-priori 量级大/中）：S高×命中=宏观新闻驱动；S高×无=纯共振；S低×命中=行业独特事件；S低×无=**情绪/庄家候选**；全参照无分=无对照，其中**无对照×命中=新闻驱动（无价格对照确认）**、无对照×无新闻=弱证据待定（不进情绪占比分母）。
- **日间对比**：UTC 日互比，工作日/周末分桶；构成列基于 0.5+ 段；分母 ≥5 才显示占比，分子/分母同显；日汇总 point-in-time 落库。
- **rolling S 曲线**：**30 点窗**（≈2.5h，2026-07-09 用户定）、5min 步进、逐参照；纯展示（多参照系肉眼套装），**不触发不分类不告警**；面板另给 max|S| 主曲线 + 7d 中枢参考线（脱钩对照读法）。
- **T_ref 定法**：稀有度锚定反解 + 双锚互证 + 敏感性扫描 + 时间走样（spec §1.5 验证四件套）；实测值纳指 0.23/0.40/0.69、黄金 0.23/0.39/0.61、美元 0.043/0.069/0.102（%）。
- **行动层**（spec §1.4b）：宏观新闻→主题跟踪；情绪→频率+强度防崩溃；纯共振→ref 状态锚定；行业事件→加密新闻流。
- **明确不做**：量能、lag、告警机器、"S 突升"触发、应响未响深版、ref 状态面板、周末永续替代（全在 Backlog）。

## 开工前置（2026-07-09 发现，必须先做）

- **分支合流**：当前工作分支 `codex/audit-fixes-20260708` 的标注 prompt 停在 **v7-20260628**（相关性优先、\|corr\|≥0.7 主判据、美债10Y），**不含** PR #15-17 的 v8→v10 工作（三段方向链、同步相关降级、标的自身三段、美债2Y）——那些在 `feat/onchain-market-overview`（生产在跑的分支）。开工前先把 feat 分支合入/换基，否则引擎会在旧 prompt 语义上开发。

## 仅剩待定（不阻塞开工，Task 9 首跑后拍板）

1. ~~T_ref 三档拍板~~ **已定（2026-07-09 校准首跑，报告 docs/reports/behavior-calibration-20260709.md）**：CL=F [0.38,0.63,0.94]、^N225 [0.42,0.68,1.16] 启用；US_2Y 30d 仅 3 个有效样本（CNBC 债券快照撑不起 5min 网格）维持禁用；NQ/GC/DXY 复核吻合不动。观察项：NQ 双锚偏差 15.3% 贴线、NQ RTH/隔夜 null 比 1.95 贴线，季度复跑重点。
2. **retention 延长值**（spec 拍板 60-90 天，具体取 90？改 `DATA_RETENTION` 时注意目前**没有清理 job**，纯声明值，改动无副作用，但远程磁盘要看一眼）。
3. 段→标注候选的**切换时点**（开关默认关，验证 runbook 跑完用户说切才切）。

## 复用点（已确认存在）

- `services/annotation_service.py:378-430 _scale_events`（触发+合并语义参照源，**不修改**）；`load_price_windows` 的 settle 门控思路。
- `config.py:143 ANNOTATION_WINDOW_SCALES`（BTC 0.5/NQ 0.3 现值不动）；`config.py:376 ANNOTATION_EVENT_MERGE_GAP_MINUTES`；`config.py:380 ANNOTATION_SETTLE_MARGIN_MINUTES`。
- `models/price.py PriceSnapshot`（5min 快照，UTC naive）；`services/time_utils.py`。
- 新闻 a-priori 标签（Phase 1）：`news_items` 的 topic/magnitude/direction（`config.py:120-136`）——新闻命中直接查 magnitude ∈ {大,中}。
- APScheduler 注册模式：`api/app.py:129-186`（5min IntervalTrigger + gap-repair CronTrigger 参照）。
- 前端：Recharts、React Query、`frontend/src/api/`（OpenAPI 生成类型，commit 9e2455f 模式）。
- 校准脚本可移植底稿：session scratchpad 的 `resonance_score_test.py` / `tref_sensitivity_test.py`（S、null、双锚、敏感性逻辑已验证）。

---

## 任务分解

> 依赖顺序：1→2→3→4→5→6→7；8 与 8b 均依赖 5（互相独立）；9 依赖 2+4（可与 6-8 并行）；10 收尾。每个 Task = 失败单测 → 实现 → 过测 → 提交。开工前先完成「开工前置」的分支合流。

### Task 1 — config：行为引擎参数块
- `config.py` 新增：`BEHAVIOR_TIERS: dict[symbol, [t03,t05,t08]]`——BTC/USDT `[0.3,0.5,0.8]`；NQ=F `[0.23,0.40,0.69]`、GC=F `[0.23,0.39,0.61]`、DX-Y.NYB `[0.043,0.069,0.102]`（实测反解，待拍板可改）；CL=F/^N225/US_2Y 先 `None`（Task 9 产出后填，None = 该参照仅出 rolling S 不参与分类？**不**——None = 整个参照禁用，避免半配置状态）。
- `BEHAVIOR_S_HI=0.5`、`BEHAVIOR_S_MID=0.3`、`BEHAVIOR_ESS_THIN=5.0`、`BEHAVIOR_COVERAGE_MIN=0.5`、`BEHAVIOR_NEWS_WINDOW_MIN=30`、`BEHAVIOR_NEWS_MAGNITUDES=("大","中")`、`BEHAVIOR_REF_SYMBOLS`（有序清单）、`BEHAVIOR_ROLLING_POINTS=30`、`BEHAVIOR_REPLACES_ANNOTATION_WINDOWS=False`。
- `DATA_RETENTION["price_snapshots"]` 30→90（待定 2，注释标明无清理 job、纯声明）。
- **单测**：tiers 升序、BTC 三档等于现值语义、cutoff HI>MID、启用参照都有三档。

### Task 2 — 段检测服务（纯函数，不碰现有标注）
- **新增** `services/behavior_segments.py`：`detect_segments(points: list[(dt, price)], tiers, merge_gap_min=5) -> list[Segment]`；`Segment` dataclass：`start_dt/end_dt/direction/tier_max/net_pct/amp_pct/key_ts`。
- 语义照搬 `_scale_events`：基档 0.3 触发（15min 开收净）、同向且相邻扫描点 5min 内连段、断档/反向另起；`tier_max` = 段内扫描点 |15min chg| 触及的最高档；`key_ts` = 段内 |5min 变化| 最大 bar；`net_pct` 段首基准到段尾；`amp_pct` 段内最高最低差。数据断档（相邻 bar 间隔 ≠5min）处跳过该扫描点并断段。
- **单测**：合成序列——单段基本形；多峰 5min 内合并；反向劈段；断档劈段；tier_max 标注（0.55 峰 → 0.5 档）；key_ts 取最大 5min bar；净幅/振幅数值。

### Task 3 — 模型与表
- **新增** `models/behavior.py`：
  - `BehaviorSegment`：`symbol, start_dt, end_dt, direction, tier_max, net_pct, amp_pct, key_ts, classification(str|null), class_version(str), s_scores(JSON {ref:{s,ess,coverage}}), news_ids(JSON), created_at, updated_at`；唯一约束 `(symbol, start_dt, end_dt)`。
  - `BehaviorDailySummary`（point-in-time）：`symbol, utc_date, day_type(weekday|weekend), counts(JSON {tier:{up,down}}), composition(JSON {macro_news,pure_resonance,industry_news,sentiment,no_ref_news,no_ref_pending}), down_net_sum, computed_at`；唯一 `(symbol, utc_date, computed_at)` ——**追加不覆盖**，读取取每日最新一条，历史读数永久可回溯。
- 注册进现有 `Base`/create_all 流程（照 `models/__init__` 既有模式）。
- **单测**：roundtrip + 唯一约束 + JSON 字段读写。

### Task 4 — 共振分模块
- **新增** `services/resonance_score.py`：
  - `chg_map(points) -> {dt: pct}`（15min 滚动，仅整 15min 跨度）。
  - `s_score(btc_chg, ref_chg, window_start, window_end, t_btc, t_ref) -> (s, ess, coverage) | None`——v0.4 公式原样；coverage < `BEHAVIOR_COVERAGE_MIN` 返回 None。
  - `rolling_s(btc_chg, ref_chg, t_btc, t_ref, points=25, step=5min) -> list[(dt, s|None)]`（曲线用，逐时点向后看 25 点）。
- **单测**：**spec §6 数值案例做 fixture**——变体 A 断言 `S≈0.774, ESS≈4.34`（25 点数据写死在测试里）；变体 B 断言 `S≈0.012`；DXY 反向场景 S 为负；参照覆盖 40% → None；空窗口 → None。

### Task 5 — 分类 job + 日汇总
- **新增** `services/behavior_classifier.py`：
  - `settled_segments(...)`：段结束 + 60min（后窗）+ settle 余量后才分类（复用 `ANNOTATION_SETTLE_MARGIN_MINUTES` 思路）。
  - `classify(session, symbol, now)`：跑 `detect_segments`（近 48h 快照）→ upsert `BehaviorSegment`；0.3 档段 `classification="count_only"`；0.5+ 段算各参照 `s_score` + 新闻命中（±30min、大/中）→ 十字格：`macro_news / pure_resonance / industry_news / sentiment / no_ref_news / no_ref_pending`；写 `class_version="v1"`。
  - `write_daily_summary(session, symbol, utc_date)`：从段表聚合 counts/composition/down_net_sum，**append** 一条 PIT 记录。
- 注册 APScheduler：`api/app.py` 加 5min IntervalTrigger（跟价格采集同节奏、错峰 offset），及每日 UTC 00:05 汇总昨日（CronTrigger）。**单 worker 约束**：与现有 job 同进程注册即可（部署备忘）。
- **单测**：合成 DB——五个格子各构造一例（含**无对照×新闻命中 → no_ref_news**、无对照×无新闻 → no_ref_pending）；重复跑幂等（upsert 不重复建段）；日汇总 PIT 追加语义。

### Task 6 — API 出数
- `api/routes.py`（或按现有拆分惯例新增 `api/behavior.py` 并挂载）：
  - `GET /api/behavior/segments?symbol&days=2` → 段明细（含 s_scores/ess/news/classification）。
  - `GET /api/behavior/daily?symbol&days=14` → 每日最新 PIT 汇总序列（前端画频率/构成/档位分布）。
  - `GET /api/behavior/linkage?symbol&hours=48` → 各参照 rolling S 曲线（25 点窗）+ 联动广度（每时点 |S|≥0.3 的参照数）。compute-on-read，参照禁用/休市自然出 None 间隙。
- OpenAPI schema + 前端 types 再生成（沿 9e2455f 流程）。
- **单测**：种子数据 → 三端点 200 + 关键字段形状；空数据不 500。

### Task 7 — 前端 BehaviorPage（布局见示意稿 artifact）
- **新增** `frontend/src/pages/BehaviorPage.tsx` + 路由/导航入口 + `frontend/src/api/behavior.ts`（React Query hooks）。
- 区块（自上而下，v2 版，详见示意 HTML artifact）：①日趋势区（14 个 UTC 日四联图共享日期轴、周末底纹：0.3 档涨跌发散柱 + 净差线；强度线（触及 0.5/0.8 档段数）；情绪候选向下段 vs 构成段总数；跌段净幅柱——情绪监测并入此区）；②时间轴叠层（48h 共享 x 轴）：BTC 价格 + 段色带（档位深浅）+ 新闻标记行 → **max|S| 主曲线 + 7d 中枢虚线** → **六张分资产 S 小图（逐行小倍数）** → 同步参照数阶梯（|S|≥0.3 计数 0-6）；③段明细表（0.5+ 档，S 证据 chips、证据厚度 ESS 徽标、分类徽标、新闻标题、标注状态三态，点击展开复盘抽屉：段窗速览 + S 分解表 + DeepSeek 归因 + 确认/改判）；④侧栏（标注进度计数——未确认留存/已确认/应响未响 v2 占位 + 读图语法卡）。
- **单测**（vitest）：格式化函数（S 显示、档位徽标、分类文案映射）；页面骨架 render 不炸；`npm.cmd run build` 过。

### Task 8 — 标注流对接（开关，默认关）
- `BEHAVIOR_REPLACES_ANNOTATION_WINDOWS=False` 时一切照旧；置 True 时标注页待标窗口源改读 `BehaviorSegment`（0.5+ 档 → 待标注列表，默认按档位+时间排序，**无必审/抽审标签**，未确认段全部留存；字段映射到现有窗口结构，`annotatable` 沿用 settle 门控）。
- 验证 runbook（写进本 plan 附录）：远程库拉 7 天数据回放，对比新旧窗口清单差异（应为超集：0.5 语义一致 + 合并规则一致），用户目检后拍板切换（spec 拍板：不长期共存，验证即切）。
- **单测**：开关关= endpoints 不变（回归）；开= 列表来自段表且 0.8 置顶。

### Task 8b — auto-annotate payload / prompt 升级（S 语义接入 DeepSeek）
- **payload 换血**（`services/annotation_service.py` 的 auto-annotate 路径，单窗与批量两份同步）：
  - **加**：`s_scores`（各参照 S 含符号）、`max_ref`（最强参照名 + max|S|）、`ess`、`coverage`、`sync_ref_count`（同步参照数）、机器预分类结果（十字格类别）。
  - **删**：`correlations`（±1h Pearson）——实测判别力≈随机（0.3 档 lift≈1.0），被 S 完全取代；三段 `reference_change_segments` 保留（人读机读都仍有用）。
- **prompt 重写推理链**（bump `ANNOTATION_PROMPT_VERSION`，两份 system prompt 同步）：
  1. 先看机器预分类 + max|S|：|S|≥0.5 共振 / 0.3-0.5 弱 / <0.3 独立；**ESS<5 或覆盖<50% 时明示证据薄，降低置信**；符号只作方向描述（美元反向=正常联动）。
  2. 共振 → 优先找能驱动**最强参照**的宏观突发新闻（沿用现有"资产→新闻域"映射与跨资产签名表——签名表保留，它仍是方向交叉验证的正确工具）。
  3. 独立 + 无新闻 → 情绪/大户候选，**禁止硬归因**；无对照 ≠ 无新闻（周末宏观新闻可照常标 driver，只是少了价格对照佐证）。
  4. 删除所有 "\|corr\|≥0.7" 及"±1h 相关系数"相关旧段落（v7/v10 遗留，与 S 口径打架）。
- **守卫单测**：prompt 文本必须含 `max|S|`/`证据薄`/`无对照`关键词、不得含 `corr`/`±1h 相关`；payload 形状断言（s_scores/ess/coverage/sync_ref_count 存在，correlations 不存在）；prompt_version 已 bump。
- 依赖：Task 5（S 落库）；与 Task 8 的开关无关（payload 升级独立于窗口源切换，先行生效）。

### Task 9 — 校准工具（脚本固化）
- **新增** `scripts/behavior_calibrate.py`（从 scratchpad 验证脚本移植，数据源改本地/远程 DB 而非 API）：
  - `--anchor`：全参照三档稀有度反解 + 波动率比例双锚互证（偏差 >15% 红字告警）。
  - `--sensitivity`：T×{0.5,0.75,1,1.5,2} 的 real/null/lift/翻转率表。
  - `--null-lift`：当前 config 值下各参照 S 的 ±24h 错位对照 lift。
  - `--session-bias`：RTH/隔夜 null 率分桶（spec 验证四件套之四，一次性诊断）。
  - 输出 markdown 报告到 `docs/reports/behavior-calibration-YYYYMMDD.md`；**产出值人工圆整进 config**（preserve-calibrated-config：脚本只建议不改 config）。
- **单测**：合成数据 sanity（反解单调、报告文件生成）。
- 运行节律：上线前一次（补齐 CL/N225/US_2Y 三档 → 待定 1 拍板）；此后每季度 + regime 事件后。

### Task 10 — 文档/地图/全量回归
- spec 状态行更新（讨论稿 → 实施中，指向本 plan）；`ARCHITECTURE.md/DATAFLOW.md/DECISIONS.md`（+HTML 版）同步：新服务/新表/新端点/新页面/新 job（仓库规矩：地图与代码同次提交）。
- 全量：`python -m pytest -q tests`、`npm.cmd test`、`npm.cmd run build`、`git diff --check`。
- 部署备忘：单 worker（job 同进程）；retention 声明值改动；远程回放走 `ssh mmon` 备份流程。

---

## 验证 & 风险

- **验证主线**：Task 4 用 spec §6 数值案例锁公式；Task 9 用真实数据锁参数；Task 8 runbook 用回放锁新旧一致性；面板信息密度按用户目检迭代（预期 1-2 轮）。
- **风险**：N225/US_2Y 分钟数据质量（休市多、债券只有盘中）→ coverage 门槛天然兜底，最坏该参照常年无分（可禁用）；yfinance 15min 延迟 → 分类 job settle 门控已吸收；SQLite 写并发 → 沿用现有单进程 scheduler 模式不新开进程。
