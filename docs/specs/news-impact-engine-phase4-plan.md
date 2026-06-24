# Phase 4 — 警报层（两镜像 + 两模式）Implementation Plan 【草案 / DRAFT】

> **状态：草案，待用户拍板下方「## 待定项」后再展开成 bite-sized TDD 步骤。** 本文先给结构 + 复用点 + 待定决策，供过目。

**Goal:** 把主题台账（Phase 1）+ 窗口（Phase 2）+ driver 标注（Phase 3）接成两个镜像警报——**A 有重要新闻、价没按预期动（脱敏）** 与 **B 没新闻、价在动（情绪）**——并支持**反应**（事后，窗口触发）与**预判**（事前，事件/日历触发）两种模式。

**Architecture:** 新增 `services/impact_alerts.py`，**纯计算**（台账「预期」vs「实际」对比，非模型）。复用：台账度量 `services/theme_ledger.py`；窗口 `annotation_service.load_price_windows`；driver 标注 `news_price_annotations`；推送通道 `alerts/channels/wechat_work.py`（现成企业微信 markdown）+ `AlertLog` 冷却去重（现成）。出口 `/api/alerts/impact`。

**Tech Stack:** Python 3 / SQLAlchemy / pytest（本地 `D:\anaconda\python.exe`）。

---

## 锁定的设计（来自 spec §0 警报，已定）

- **A 脱敏**：三档全中才报 —— 该新闻 **a-priori 量级=大** 且 台账**这主题历史档=强/中**（=重要、历史动过价）且 **实际反应=弱/无（或反向）**。
  - 两子类（台账自动算）：近期同主题平均都弱 → **渐进脱敏**（主题在死）；近期仍强、就这次弱 → **单次失灵**（多半已提前定价）。
  - 附**历史先例**：拉这主题历史最强几次反应（= 盲区覆盖）。
- **B 情绪**：窗口触发了但归因 = **无 driver**。强/弱 = **该次振幅在最近 ~30 次无新闻（无 driver）异动里的百分位**；再叠加趋势方向（升势无故再涨=情绪强买点；跌势反之）。
- **两层防误报**：重要性筛子（只对历史档=强/中的主题报 A）+ 量级 a-priori（内容判、不看价格）。
- **两模式**：反应（价格窗口触发，事后，含 A/B）+ 预判（新闻/日历事件触发，事前，查台账给预期 + 盯盘标记，只有 A 味）。共用同一套「预期 vs 实际」对比。
- **A 类「无反应」是结构化判定**（Phase 3 已定）：量级大的新闻当不上其邻近任何窗口的 driver → 无反应。**依赖 driver 标注先做**；bootstrap 期人工标。

---

## ⚠️ 待定项（请拍板，决定 plan 细节）

1. **B 类百分位阈值**：spec 定了「振幅在最近 ~30 次无 driver 异动里的百分位」。需要你定：百分位 ≥ 多少算「强」（强买/卖点）？比如 ≥80% 强、≤20% 弱、中间不报？「最近 ~30 次」的 30 是否就用 30？
2. **强/中/弱「档」的分桶函数**：A 的「主题历史档=强/中」要把 `topic_recent_reactions(magnitude='大')` 的一组反应映射成 强/中/弱。按什么分？(a) 用 net% 绝对值的固定档（如 |net|≥X 强 / ≥Y 中 / 否则弱）？(b) 还是百分位/相对？「历史动过价」的最低门槛是多少？
3. **「实际反应=弱/无/反向」的判据**：A 第三档。reaction 的 net%/range% 低于多少算「弱/无」？方向与新闻 direction 相反算「反向」（多严格）？
4. **推送**：这次就接企业微信（复用 `WeChatWorkChannel` + `AlertLog` 冷却）推 A/B？还是**先只做 `/api/alerts/impact` 出数、先不推**（攒着部署时再开推送）？
5. **反应模式的触发时机/Job**：A/B 反应模式跑在哪？建议挂在每小时 settle 作业集（gap_repair_cycle）之后——窗口已 settle、driver 标注（人工）也大概率就绪。认可否？
6. **预判模式的事件来源**：预判（CPI 等日历事件）的事件清单从哪来？先**手工配一个事件→topic 映射表**（config），还是接已有的经济日历？建议先手工小表。

> 这 6 点里 **1/2/3 是阈值类**（不定也能先写「函数骨架 + 占位阈值 + 单测用注入阈值」，回放校准时再定数值，跟 Phase 2 阈值一个套路）；**4/5/6 是范围类**（决定这次做多少）。

---

## 复用点（已确认存在）

- `theme_ledger.topic_recent_reactions(session, topic, symbol, n, magnitude, minutes, now)` → 同主题最近 N 次反应（可 severity 匹配 magnitude='大'）。
- `theme_ledger.rank_percentile(value, population)` → |value| 在 |population| 里的百分位（B 类强弱直接用）。
- `theme_ledger.forward_reaction(...)` → 单条新闻前向反应（net%/range%）。
- `annotation_service.load_price_windows(session, symbol, hours)` → 窗口（带 annotation_id；可判某窗口有没有 driver 标注）。
- `news_price_annotations.news_roles`（JSON {news_id: role}）→ 查 driver。
- `alerts/channels/wechat_work.py:WeChatWorkChannel.send(title, content)` + `models/alert_log.AlertLog` → 推送 + 冷却去重（参考 `alerts/engine.py` 的 `_is_in_cooldown` / `_dispatch`）。

---

## 任务分解（草案，待待定项敲定后展开 TDD 步骤）

### Task 1 — 台账查询底座（A/B 共用）
- **新增** `services/impact_alerts.py` 起步：
  - `topic_strength_tier(session, topic, symbol) -> "强"|"中"|"弱"|None`（用待定项 2 的分桶函数；None=无历史数据）。
  - `is_important_topic(session, topic, symbol) -> bool`（历史档 ∈ {强,中}）。
- **单测**：用合成台账数据（几条 NewsItem + PriceSnapshot 造已知反应），断言分桶。**阈值用注入**（fixture 设常量），数值待校准。

### Task 2 — A 类脱敏判定
- `desensitization_alert(session, news, symbol) -> AlertA | None`：三档全中→出 A；子类 渐进/单次（看近期同主题档）；附先例（`topic_recent_reactions` 最强几条）。
- 「无反应」结构化判定：该新闻是否当上邻近窗口 driver（查 annotation news_roles）。
- **单测**：构造「量级大 + 主题历史强 + 这次没动」→ A 触发、子类正确、带先例；反例（量级小 / 主题没历史 / 这次动了）不触发。

### Task 3 — B 类情绪判定
- `sentiment_alert(session, window, symbol) -> AlertB | None`：窗口无 driver + 振幅百分位（待定项 1）+ 趋势方向叠加。
- 「最近 ~30 次无 driver 异动」取数：近窗口里 annotation 判无 driver 的那些的振幅 population。
- **单测**：无 driver 的高振幅窗口（百分位高）→ B 强；有 driver 的不报；低百分位不报。

### Task 4 — 两模式 + API
- `scan_reactive(session, symbol) -> list[Alert]`（反应：扫近窗口，有 driver→查 A，无 driver→查 B）。
- `scan_predictive(session, event) -> AlertA-ish`（预判：事件→topic→查台账给预期+盯盘标记）。
- `api/routes.py` 加 `GET /api/alerts/impact`（返回当前 A/B 列表）。
- **单测**：反应模式在合成数据上产出预期 A/B；预判模式给定事件返回预期档。

### Task 5 —（待定项 4）推送
- 若启用：复用 `WeChatWorkChannel` + `AlertLog` 冷却，把 A/B 推企业微信；挂在 settle 作业集后。
- 若先不推：跳过，仅 `/api/alerts/impact` 出数。

### Task 6 — spec 状态 + 全套回归

---

## 验证 & 依赖说明
- **逻辑可用合成数据单测**（不依赖真实标注）；**真实校准/验证依赖你手工标一批 driver**（spec：bootstrap 人工）+ 待定阈值定稿。
- 依赖：Phase 1（台账）✅ / Phase 2（窗口）✅ / Phase 3（driver 标注）✅。
- 历史已知案例（6/11 BTC 无视升级）可作 A 类回放验证素材。
