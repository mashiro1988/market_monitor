# 标注精简 + auto-annotate prompt 强化 Implementation Plan

> 来源：2026-06-28 与用户多轮反馈。承接 news-impact-engine Phase 3（driver/redundant/noise 已上线）。
> 本轮只碰**标注流 + auto-annotate 提示词 + 净值图/相关性**，不动窗口/台账/Phase 4。

**Goal:** 把标注简化到"只标 driver/redundant/noise + 备注"，并给 auto-annotate 的 reasoner 更强的判据（相关性 + 跨资产确认 + 时间权重），减少误标 driver。

**构建顺序：A 精简 → B(#1) ±1h+相关性+prompt → C(#3) 互动标注。** 每步 TDD + commit。窗口设计不动。

---

## Part A — 砍掉 market_reaction_type + confidence（精简）
**理由**：`market_reaction_type` 与 driver 的 topic 冗余（no_news_driver = 没 driver；macro vs event = 看 driver 的 topic）→ 删。**`confidence` 保留**（用户 2026-06-28 更正：训模型时作样本置信权重有用）。砍完标注 = 逐条 driver/redundant/noise + **置信度** + 备注。

- **前端【已做，commit ec39b44 + 修正】**：`AnnotationsPage` 去掉「市场反应类型」下拉；**保留「归因置信度」档位**；已标列表「归因」列去掉反应徽章、保留驱动条数 + 置信度。
- **prompt（Part B 里做）**：两份 auto-annotate prompt 去掉 **market_reaction_type**（保留 confidence 输出）。
- **service（Part B 里做）**：`no_clear_news` 改为**从"有没有 driver"派生**（无 driver → true），不再看 reaction。confidence 照旧解析/落库。
- **导出**：`schema_version` 仍靠 `confidence is not None` 判（confidence 保留 → **无需改**）。
- **DB / schema**：`market_reaction_type` 列保留置空、不删（历史有值）；confidence 列继续用。
- **测试（Part B）**：prompt 守卫（不含 market_reaction_type、仍含 confidence）、no_clear 派生。

## Part B(#1) — ±1h 取数 + 相关性 + prompt 强化
### B1 窗口拉宽
- 净值图 + auto-annotate 取数从 ±30min → **±1h**（~25 点，够算相关性 + 覆盖滞后/迟报）。

### B2 派生信号喂 LLM（相关性 + 最猛段 + 窗口前趋势）
- **相关性**：target vs 每个对标在 ±1h 上的 **Pearson 相关（5min 收益率）** → payload `correlations: {纳指: 0.92, 原油: -0.10, 美元指数: -0.85, ...}`。新增算法：两条 5min 序列对齐算 Pearson，样本 < ~8 返 None。
- **首个触发段**：从窗口起点往后扫，**第一个出现显著波动的 5min bar**（第一个 |Δ%| 跨阈值的，不是 |Δ%| 最大的、也不是笼统窗口起点——前面平的一段要跳过）→ payload `trigger_move_start_bj / trigger_move_pct`。这才是价格**开始**剧烈反应的触发时点，driver 通常就在这附近。阈值：`|Δ%| ≥ max(每bar地板, 0.5×窗口内峰值bar)`（可调）。例：25min 窗口 0-10 平、10-15 猛跌 -0.7%、15-25 平 → 触发段 = 10-15。
- **窗口前趋势**：窗口起点前一段（~30-60min）的净变动方向/幅度 → payload `pre_window_move_pct`。用于识别情绪反转（见 B3-5）。

### B3 prompt 指令（写进两份 auto-annotate prompt）
1. **没明显相关新闻就别标 driver**：默认 noise，只有强而具体的因果链才 driver。
2. **时间权重看首个触发段、不是窗口起点**：真正的触发时点 = 窗口内**第一个显著波动的 5min bar**（`trigger_move_start_bj`）。**最高权重区 = 该 bar 往前放宽一根 = [触发起点−5min, 触发终点]**（例：触发段 10-15 → 重点看 5-15 的新闻）。跳过前面平的一段。长窗口只是兜底：防①市场滞后反应（driver 在触发段前较久）②新闻源迟报（driver 在其后较久才推）。
3. **相关性用法**：高相关（|corr|≥~0.7）= 本品种在跟那个对标走 → 优先找驱动**那个相关品种**的突发事件；**只描述价格走势、没给出背后真实世界事件**的新闻 → noise（联动由相关性解释）。
4. **跨资产确认真伪**：方向对但直觉冲击小、价格滞后的新闻别急着标 driver——要**整套签名对齐**才认。例：鹰派联储应看到 **BTC/纳指↓ + 美元↑ + 美债利率↑ 同步**；corroborating 资产不配合 → 降级 noise。
5. **情绪反转（无 driver）识别**：看 `pre_window_move_pct`——若窗口**前一段猛涨、窗口却猛跌**（或反之），这种急反转很可能是**纯情绪/仓位挤压（"四杀"）**、无新闻驱动。这种形态下别硬找 driver，倾向 no driver（情绪 B 类）；除非有量级重大且跨资产签名对齐的硬事件。

> 注：现有 prompt 已有"跨资产签名表""发布延迟""长窗口"段，本轮是**收敛强化**（加 correlations/peak/pre-window 数值 + 最猛段权重 + 候选级确认 + 情绪反转识别），不是从零加。

### B4 前端
- 净值图窗口 ±1h（改 preMinutes/postMinutes）。correlations 可选在图注/候选表里给个提示（次要）。

## Part C(#3) — 互动标注（多轮纠正 reasoner）
- 后端 `POST /annotations/auto/refine`：带 窗口+候选+上一轮输出+用户纠正话 → 多轮对话再调 reasoner → 返回新 news_roles。
- 前端：auto 结果下方对话框，用户打"driver 错了,应标 X 及同簇冗余" → 重标套用。
- 训练价值：auto → 人改 → 对话纠正 三段留痕（难例）。
- **待定**：对话历史要不要落库（先不落、只在前端会话内？还是存进 annotation 供训练？）——建 C 时再定。

---

## 验证 & 依赖
- A/B 的逻辑（no_clear 派生、相关性算法、导出 schema_version）都能合成数据单测；prompt 指令用守卫测试（含/不含关键串）。
- prompt 改动后 bump `ANNOTATION_PROMPT_VERSION`（v6 → v7）。
- 不动窗口检测/台账/Phase 4。真实效果靠用户标注 + auto 对比验证。
