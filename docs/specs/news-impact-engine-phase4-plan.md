# Phase 4 — 警报层（A 脱敏 + B 情绪，纯 API 出数）Implementation Plan

> **状态：设计已与用户对齐（2026-06-23），待开工。** 仅剩 B 类百分位阈值占位待校准、预判模式可选。

**Goal:** 把主题台账（Phase 1）+ 窗口（Phase 2）+ driver 标注（Phase 3）接成两个镜像信号——**A 有重要新闻、价没按预期动（脱敏）** 与 **B 没新闻、价在动（情绪）**——经 `/api/alerts/impact` 出数。**不加定时 job、不自动标窗口、先不推送**；A/B 只对已人工标注的窗口存在。

**Architecture:** 新增 `services/impact_alerts.py`，**纯计算 compute-on-read**（台账「预期」vs「实际」对比，非模型）。复用：台账度量 `services/theme_ledger.py`；窗口 `annotation_service.load_price_windows`；driver 标注 `news_price_annotations`。出口 `/api/alerts/impact`。（推送通道 `WeChatWorkChannel` + `AlertLog` 冷却现成，本期不接、留作以后开推送用。）

**Tech Stack:** Python 3 / SQLAlchemy / pytest（本地 `D:\anaconda\python.exe`）。

---

## 锁定的设计（来自 spec §0 警报，已定）

- **A 脱敏**（2026-06-23 简化）：**只两件** —— 该新闻 **a-priori 量级=大** 且 **被标为 noise**（结构化：当不上邻近任何窗口的 driver）→ 脱敏无反应标记。**去掉了"历史强弱"那道粗闸**。
  - **台账该主题最近几次反应只作参考展示、不作门槛**（topic 颗粒度问题对判定无关；量级=大 是唯一主闸）。
  - 可选描述（非门槛）：参考里近期都弱→「渐进脱敏」；近期仍强就这次弱→「单次失灵」。附历史先例供人审。
- **B 情绪**：窗口触发了但归因 = **无 driver**。强/弱 = **该次振幅在最近 ~30 次无新闻（无 driver）异动里的百分位**；再叠加趋势方向（升势无故再涨=情绪强买点；跌势反之）。
- **两层防误报**：重要性筛子（只对历史档=强/中的主题报 A）+ 量级 a-priori（内容判、不看价格）。
- **两模式**：反应（价格窗口触发，事后，含 A/B）+ 预判（新闻/日历事件触发，事前，查台账给预期 + 盯盘标记，只有 A 味）。共用同一套「预期 vs 实际」对比。
- **A 类「无反应」是结构化判定**（Phase 3 已定）：量级大的新闻当不上其邻近任何窗口的 driver → 无反应。**依赖 driver 标注先做**；bootstrap 期人工标。

---

## 已定（2026-06-23 对齐）

- **量级 = a-priori 内容判**（大/中/小），与"历史动价强弱"是**两条轴**，5min tag 不吃历史（反循环）。
- **不加任何定时 job、不自动标窗口**：窗口 driver 标注仍是人/LLM 在标注页手动做（settle 门控）。**Phase 4 = 纯 `/api/alerts/impact` 出数（compute-on-read）**，A/B 只对**已人工标注**的窗口存在。
- **A 去掉历史强弱粗闸**：只 `量级=大 且 被标 noise` → 脱敏；历史反应只作参考展示。→ **原待定 ②（分桶）③（弱/无阈值）取消**。
- **redundant = 同一具体事件实例**（同次公布/讲话/袭击/政策；非同 topic）。prompt 已写"同事件簇"，再加一句"同 topic 不同事件不算同簇"澄清。
- **先不推送**（攒部署时再开）：本期不接企业微信。→ **原待定 ④/⑤ 取消**。

## ⚠️ 仅剩待定

1. **B 类百分位阈值**（占位 + 回放校准，跟 Phase 2 一个套路）：占位 = 振幅在最近 **30** 次无 driver 异动里 **≥70% 强 / ≤30% 弱 / 中间不报**（用户 2026-06-23 定起始值）。函数骨架 + 注入阈值单测，数值待标注数据攒够后校准。
2. **预判模式事件来源**：先**手工配小表**（config 里 `事件名→topic`），不接经济日历。（预判优先级低，可放最后或先跳过。）

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

### Task 1 — 台账参考底座（A 用，仅展示）
- **新增** `services/impact_alerts.py` 起步：`topic_reference(session, topic, symbol, n=5) -> list[reaction]` —— 直接包 `theme_ledger.topic_recent_reactions`，给 A 的参考展示用（**不分桶、不当门槛**）。可选 `decay_hint(reactions) -> "渐进"|"单次"|None`（纯描述）。
- **单测**：合成台账数据，断言返回最近 N 次反应；decay_hint 在"全弱"/"近强这次弱"上给对标签。

### Task 2 — A 类脱敏判定（简化）
- `desensitization_alerts(session, symbol, hours) -> list[AlertA]`：扫该 symbol 近窗口里**被标 noise 且 a-priori 量级=大**的新闻 → 每条出一个脱敏标记，带 `topic_reference`（参考）+ `decay_hint`（描述）。
- 「被标 noise」= 该新闻在其窗口的 `news_roles` 里不是 driver/redundant（或所有邻近窗口都没把它标 driver）。查 `news_price_annotations.news_roles`。
- **单测**：构造「量级大 + 标成 noise」→ 出脱敏 + 带参考；反例（量级小 / 标成 driver）不出。**无阈值**（结构化判定，干净）。

### Task 3 — B 类情绪判定
- `sentiment_alert(session, window, symbol) -> AlertB | None`：窗口无 driver + 振幅百分位（待定项 1）+ 趋势方向叠加。
- 「最近 ~30 次无 driver 异动」取数：近窗口里 annotation 判无 driver 的那些的振幅 population。
- **单测**：无 driver 的高振幅窗口（百分位高）→ B 强；有 driver 的不报；低百分位不报。

### Task 4 — API 出数（反应模式；compute-on-read，无 job、无推送）
- `impact_alerts(session, symbol, hours) -> {A: [...], B: [...]}`：合并 Task 2（A 脱敏）+ Task 3（B 情绪），从**已人工标注**的近窗口算出。
- `api/routes.py` 加 `GET /api/alerts/impact`（返回当前 A/B 列表）。**不加定时 job、不推企业微信**（用户 2026-06-23）。
- **单测**：合成已标注数据 → 端点产出预期 A/B。

### Task 5 —（可选，低优先）预判模式
- `predict_impact(session, event) -> AlertA-ish`：手工小表 `事件名→topic` → 查台账参考给"预期 + 盯盘"标记。可放最后或先跳过。

### Task 6 — prompt 加事件簇澄清 + spec 状态 + 全套回归
- prompt：在 redundant 说明加"同 topic 不同事件不算同簇"（bump 版本）。

---

## 验证 & 依赖说明
- **逻辑可用合成数据单测**（不依赖真实标注）；**真实校准/验证依赖你手工标一批 driver**（spec：bootstrap 人工）+ 待定阈值定稿。
- 依赖：Phase 1（台账）✅ / Phase 2（窗口）✅ / Phase 3（driver 标注）✅。
- 历史已知案例（6/11 BTC 无视升级）可作 A 类回放验证素材。
