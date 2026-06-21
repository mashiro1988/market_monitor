# 新闻影响力引擎 实施路线图（annotation v3）

> **主路线图**：把整套系统拆成 5 个**可独立构建、独立验证**的子系统（Phase）。每个 Phase 真正执行时再展开成 bite-sized TDD 任务（单独的 phase plan）。本文件取代 `annotation-v2.md` 的若干决定（见「取代关系」）。
> 来源：2026-06-10~12 与用户的多轮设计讨论。

**Goal:** 以价格异动为主轴做新闻归因、以新闻为主轴做影响力衰减监测，共用一个「主题反应台账」，输出反应/预判两种结果 + 日级总结。

**Architecture:** 一个引擎（主题台账）+ 两个入口（价格异动 / 新闻）+ 两个镜像警报（有新闻没动价 / 没新闻在动）+ 日级总结顶层。台账可从历史数据回灌 bootstrap。短期不 fine-tune，靠 DeepSeek prompt + 台账计算。

**Tech Stack:** FastAPI + SQLite + APScheduler；DeepSeek v4-pro（prompt，无微调）；yfinance/OKX 价格；现有标注页 React。

---

## 0. 锁定的设计决策（自包含）

### 引擎 · 主题反应台账
- 每条新闻**自动**打三个内容标签（LLM，入库时）：**主题 topic** / **方向 direction** / **a-priori 量级 大/中/小**（按固定 rubric 判"事件本身多大"，**不看价格**）。
- 每条新闻**自动**算一个价格侧量：**实际前向反应**（news 时刻起 N 分钟价格变动）。
- 按 (主题 × 品种) 跨时间聚合 → 该主题的**方向/量级基线 + 最近 3 次实际反应档位**。
- "预期冲击" = **定性三元组**（方向 + a-priori 量级 + 最近实际强度），**不是数字**。原因：因果量级估不准（混杂/异质/稀疏/regime），只在「同主题·跟自己比·随时间」维度做定性判断。
- "重要主题" = 历史上动过价（主题层经验筛子）。
- **强/弱永远是「在最近同类里排名」，不用绝对阈值**。category = 一个新闻主题 **或** "情绪/无新闻"。数据密度决定表达：主题稀疏 → 最近 N 次档位；情绪频繁 → 该次振幅在最近 ~30 次情绪异动里的**百分位**。
  - 主题排名要 **severity 匹配**：拿这次和过去**同等 a-priori 量级**的实例比（大比大，别拿"放话"比"轰炸"），否则小事件没反应会误报脱敏。样本因此更稀 → 主题侧的脱敏是**提请人审的标记，非自动结论**。
- 可从库里 3 月起的历史新闻+价格**回灌**，不必等一个月。

### 窗口 · 单 15min（触发=开收净，非振幅）
- **一个** 15min 窗口，触发用**窗口开收净变动**：`(窗口末收盘 − 窗口初收盘)/初 ≥ 阈值`；方向 = 净变动符号。无 1h、无多尺度、无单独净门槛（触发阈值本身就是净门槛）。
- **收口规则（= 现 `_scale_events` 合并逻辑）**：同方向且连续 → 并进上一个窗口；**变了方向、或断了档 → 上一个窗口走完**，另起一个。**断档 = 触发间隔 > 5min**（一个快照步长；旧的 60min merge_gap 收紧到 5min，靠每小时 gap-repair 补洞而非宽容窗扛数据缺口）。
- **暂不纳入高/低价**（用户 2026-06-21 定）：触发只看开收净、不看窗口内振幅(高低差)。好处：net≈0 的横跳本来就过不了净阈值、不出窗口，**天然规避了"方向不明该不该收口"的歧义**——所以也暂不做「双向博弈」状态。振幅/双向博弈留到以后真有需要再加。
- 数据限制：现仅存 5min 收盘价，"开/收" = 窗口内首/末快照收盘价。本期不改 OHLC 存储。

### 标注 · 纯归因（Goal 1，简化）
- `causal_role` 存储 **3 个值**（contradictory 与 post_hoc 均退场）：
  - **driver** 驱动代表 — 半自动：人/LLM 确认"哪个 topic 驱动该窗口"，该 topic 里 **a-priori 量级最大 + 时间最早** 那条自动当 driver。
  - **redundant** 同簇冗余 — 全自动：topic 与 driver 同簇但非代表 → 自动归此，**从负样本排除（绝不当 noise）**。由 topic tag 分组得到，不需单独去重引擎。
  - **noise** 噪音 — 默认：topic 非驱动主题 → 自动（含泛泛综述/财经早餐）。
- 去掉 post_hoc 安全：综述当不上 driver（量级低 + 时间晚，选不上代表）；评论同驱动主题的 → 自动 redundant；离题综述 → noise。"别把解释当驱动"由结构保证，无需单独标。
- 人实际只动一件事：**标出哪个 topic 驱动**。driver/redundant/noise 全由规则自动派生。
- 无 driver = 情绪（Axis B）。topic/方向/量级/反应全自动。

### 警报 · 两个镜像
核心：**所有警报 = 台账「预期」vs「实际」对比，只有两个方向**。纯计算，非模型能力。
- **A 有重要新闻、价没按预期动**（脱敏/盲区/反向）。触发三档全中：该新闻 **a-priori 量级=大** 且 台账**这主题历史档=强/中**（=重要，历史动过价）且 **实际反应=弱/无（或反向）**。
  - **「无反应」是结构化判定，非超时阈值**（用户 2026-06-21 定）：看这条新闻**前后邻近**那些触发了阈值的价格窗口，它们的 driver 是哪几条新闻；**只要这条量级大的新闻当不上其中任何一个窗口的 driver**，就判它「无反应」。好处：不用拍一个"等多久算没动"的 X。**代价：依赖 Phase 3 的窗口 driver 标注先完成**才能算出"哪些重要新闻无反应"；bootstrap 期由人工执行标注。
  - 分两种（台账自动算）：近期平均都弱 → **渐进脱敏**（主题在死）；近期仍强、就这次弱 → **单次失灵**（多半已提前定价）。
  - 附**历史先例**（=盲区覆盖）：拉这主题历史最强几次反应。
- **B 没新闻、价在动**（情绪）：窗口触发了但归因 = **无 driver**。**强/弱 = 该次振幅在最近 ~30 次情绪（无新闻）异动里的百分位**（非绝对阈值，可从历史回灌）；再叠加趋势方向（升势无故再涨=情绪强买点；跌势反之）。
- 两层防误报：**重要性筛子**（只对历史档=强/中的主题报 A）+ **量级 a-priori**（内容判、不看价格，与实际反应一比才干净）。
- 两种模式：**反应**（价格窗口触发，事后，含 A/B）+ **预判**（新闻/日历事件触发，事前，如 CPI→查台账给预期+盯盘标记，只有 A 味）。共用同一套对比。

### 日级总结（顶层，第二训练轨）
- 吃当天窗口标签 + 台账 → 主因排序 + 叙事 + 脱敏/情绪提示。自动草拟 → 人审 → 成日级训练数据。
- 日界 = 北京时间 06:00 固定（不判夏令时）。

---

## 取代 / 保留关系（相对 annotation-v2.md）
**取代：** causal_role → `driver / redundant / noise`（去 contradictory 与 post_hoc）；reaction_type 三分类退场（改由「有无 driver + topic」派生）；双档窗口(15m+60m)+净门槛 → 单 15min **开收净触发**（断档=5min；暂不用振幅/双向博弈）。
**新增：** topic/方向/a-priori 量级自动标签 + 主题台账 + 警报层 + 日级层。
**保留：** driver 不分主次；人审归因；JSONL 导出 + split=train/eval/all；eval_set；auto_news_roles / prompt_version；缺口自愈 job；对标 reference_changes。

---

## 子系统（5 个 Phase · 按构建顺序）

### Phase 1 — 主题台账 + 历史回灌【地基，已实现】
建引擎。两条轴都依赖它，且能立刻产出"主题历史影响力"。
- **新增** `services/news_tagging.py`（LLM 打 topic/direction/magnitude，可批量）；`services/theme_ledger.py`（前向反应度量 + 按 主题×品种 聚合 + 最近 N 次）；`scripts/backfill_ledger.py`（回灌历史）。
- **改** `models/news.py` 加列 `topic / news_direction / magnitude_tier / tagged_at`；`database.py` 裸 ALTER 补列。
- **前向反应 = compute-on-read**（`theme_ledger.forward_reaction` 实时从 `price_snapshots` 算，`minutes` 可调），**不落库**——快照持续回补、窗口可变，固化成列反而产生陈旧脏数据。故**无 `forward_reaction_pct` 列**。
- **`traditional_open` 是前置条件，非打标副产物**：新闻**入库时**就由 `news_scanner._save_records` 按纯日历算好（出生属性）。一条新闻进入「可打标」状态 = `traditional_open` 已有 **且** 反应窗口已走完。打标只写内容标签、绝不回写 `traditional_open`。（设计依据：得先知道这条新闻处于开/休市，才知道它该不该进打标/归因；所以前置条件必须先于打标确定。）
- **每小时数据 settle 作业集**（`api/app.py:gap_repair_cycle`，:37），顺序固定：① gap-repair 补价格洞 → ② `backfill_traditional_open` 给历史/漏设的 NULL 行补前置条件（纯日历、无 LLM、幂等） → ③ `tag_untagged` 给「可打标」新闻打内容标签。先补开市洞、再保证前置条件、最后打标。
- **取数三道护栏**（替代旧分页回扫）：① 只量**反应窗口已走完**的新闻（timestamp ≤ now−minutes，未走完反应不完整且数据未 settle）；② 传统市场品种 SQL 滤 `traditional_open=True`（休市从源头排除）；③ gap-repair 保证开市新闻反应窗有数据。三者叠加 → 候选基本都有反应，直接取 n（留 buffer 吸收限频残缺，真补不上的由 gap-repair 自检告警）。crypto(BTC) 不滤休市。
- **severity 匹配边界**：`ledger_overview` 仅展示（recent[] 混量级、每条带 magnitude）；强弱/脱敏判定必须由 Phase 4 调 `topic_recent_reactions(magnitude='大')`，禁止直接拿 overview 做判定。
- **验证**：`tests/test_theme_ledger.py`（9）+ `tests/test_news_tagging.py`（4）；本地端到端 12 条打标→落库→总览跑通；实弹打标正确（美军打击→地缘/利空/大）。
- **产出**：每主题一条最近 N 次反应线（净 + 振幅 + 量级标注）。

### Phase 2 — 窗口改单 15min 开收净【小】
- **改** `services/annotation_service.py:load_price_windows` + `config.ANNOTATION_WINDOW_SCALES`：删 60m 档与多尺度合并；保留**开收净触发**（现 `change_pct` 即是，= 窗口末/初收盘净变动），删单独的 `net_min` 门槛（触发阈值已是净门槛）。
- **收口** = 现 `_scale_events` 同向+merge_gap 合并逻辑原样保留，**只把 `ANNOTATION_EVENT_MERGE_GAP_MINUTES` 由 60 收紧到 5**（断档=一个快照步长）。暂不引入振幅/高低价/双向博弈。
- **改/删** `tests/test_annotation_window_scales.py`（多尺度用例 → 单档开收净 + 5min 断档用例）。
- **验证**：回放脚本在 6/10 夜重跑，确认横跳被压（净≈0 不出窗口）、真实方向性波动被抓；阈值按结果定。

### Phase 3 — 标注层简化（纯归因）
- **改** prompt（causal_role 三分类 + topic 分组取「量级最大+最早」为 driver + 同簇冗余排除）；`schemas/annotations.py` 枚举；`database.migrate_legacy_annotations` 升级映射；前端角色下拉；导出（冗余排除负样本）。
- **验证**：实弹回放（含 6/11 案例）在三分类下正确；`tests/test_annotation_v2.py` 更新；同簇冗余不进负样本的单测。

### Phase 4 — 警报层（两镜像 + 两模式）
- **新增** `services/impact_alerts.py`：A（脱敏：量级大 + 主题历史会动 + 最近弱，附先例）；B（情绪：无 driver + 趋势）；反应模式（窗口触发）+ 预判模式（给定事件查台账）。`/api/alerts/impact` + 企业微信。
- **A 类的「无反应」依赖 Phase 3 的窗口 driver 标注**（结构化判定：量级大的新闻当不上邻近任何窗口的 driver → 无反应），故 Phase 4 实际依赖 1+3，bootstrap 期标注由人工先做。
- **验证**：用历史已知案例（6/11 BTC 无视升级）构造，A 类应触发；情绪信号在无新闻异动窗口触发。

### Phase 5 — 日级总结模块
- **新增** `services/daily_digest.py` + `/api/daily/digest` + 前端日报页；吃当天窗口+台账 → 因子排序 + 叙事草稿；日级审核/导出（第二训练轨）。
- **验证**：对历史某一天生成草稿并人审；`tests/test_daily_digest.py` 覆盖因子排序聚合。

---

## 建议构建顺序
**Phase 1（地基，可回灌立刻见效）→ 2（窗口，小）→ 3（标注简化）→ 4（警报）→ 5（日报）。**
每个 Phase 执行前单独展开成 bite-sized TDD 细化 plan。Phase 1 与 Phase 2 互相独立，可并行；3 依赖 2，**4 依赖 1+3**（A 类无反应判定要 driver 标注），5 依赖 1+3。
