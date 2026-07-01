# 标注精简 + auto-annotate prompt 强化 Implementation Plan

> 来源：2026-06-28 与用户多轮反馈。承接 news-impact-engine Phase 3（driver/redundant/noise 已上线）。
> 本轮只碰**标注流 + auto-annotate 提示词 + 净值图/相关性**，不动窗口/台账/Phase 4。

**Goal:** 把标注简化到"只标 driver/redundant/noise + 备注"，并给 auto-annotate 的 reasoner 更强的判据（相关性 + 跨资产确认 + 时间权重），减少误标 driver。

**构建顺序：A 精简 → B(#1) ±1h+相关性+prompt → C(#3) 互动标注。** 每步 TDD + commit。窗口设计不动。

---

## Part A — 砍掉 market_reaction_type + confidence（精简）
**理由**：`market_reaction_type` 与 driver 的 topic 冗余（no_news_driver = 没 driver；macro vs event = 看 driver 的 topic）；`confidence` 用户从不看。砍完标注 = 逐条 driver/redundant/noise + 备注。

- **前端**：`AnnotationsPage` 去掉「市场反应类型」下拉 + 「归因置信度」档位；已标列表的「归因」列只留驱动条数。
- **prompt**：两份 auto-annotate prompt 去掉 market_reaction_type + confidence 的输出与说明（顺带把 prompt 砍小）。
- **service**：`_normalize_v2_labels` 不再要求 reaction/confidence；`no_clear_news` 改为**从"有没有 driver"派生**（无 driver → true），不再看 reaction。`_extract_v2_labels` 停止解析这俩（prompt 不吐了）。
- **导出**：`schema_version` 现靠 `confidence is not None` 区分新旧低保真 → 改成靠 `labeler/prompt_version` 判（新行有、旧 v1 行没有）。历史行的 reaction/confidence **原值照出**（不丢历史信号），新行为空。
- **DB / schema**：`NewsPriceAnnotation.market_reaction_type/confidence` 列**保留置空、不删**（历史有值，删要迁移+丢数据）；`AnnotationCreateRequest/Detail/ListItem` 的这俩字段保留为可选（前端不再传/显）。
- **测试**：更新 prompt 守卫（不再断言含 reaction/confidence）、导出 schema_version、no_clear 派生、既有 upsert/parse 用例。

## Part B(#1) — ±1h 取数 + 相关性 + prompt 强化
### B1 窗口拉宽
- 净值图 + auto-annotate 取数从 ±30min → **±1h**（~25 点，够算相关性 + 覆盖滞后/迟报）。

### B2 相关性（派生信号喂 LLM）
- 后端算 target vs 每个对标在 ±1h 上的 **Pearson 相关（5min 收益率）**，塞进 auto-annotate payload：`correlations: {纳指: 0.92, 原油: -0.10, 美元指数: -0.85, 美债10Y: 0.60, ...}`。
- 新增 `services/…correlation`（或 theme_ledger/annotation 里）：从 price_snapshots 取两条 5min 序列对齐算 Pearson；样本 < ~8 返回 None。

### B3 prompt 指令（4 条，写进两份 auto-annotate prompt）
1. **没明显相关新闻就别标 driver**：默认 noise，只有强而具体的因果链才 driver（现有原则，保留并前置强调）。
2. **时间权重**：`window.correlations` + 时间——**窗口起点附近、尤其临前的重要新闻**关注权重最高。长窗口只是兜底：防①市场滞后反应（真 driver 在窗口起点前较久）②新闻源迟报（driver 在起点后较久才推）。别默认"驱动一定在窗口内"，但**离窗口起点越近权重越高**。
3. **相关性用法**：高相关（|corr|≥~0.7）= 本品种在跟那个对标走 → 优先找驱动**那个相关品种**的突发事件新闻；**只描述价格走势、没给出背后真实世界事件**的新闻 → noise（大类联动由相关性解释，不由描述性新闻解释）。
4. **跨资产确认真伪**：一条方向对但直觉冲击小、价格滞后的新闻别急着标 driver——要**整套签名对齐**才认。例：鹰派联储（"通胀在升、年内加息"）应看到 **BTC/纳指下行 + 美元走强 + 美债利率上行 同步**；若 corroborating 资产（美元/利率）不配合，就怀疑不是这条在驱动，降级为 noise。

> 注：现有 prompt 已有"跨资产签名表""发布延迟""长窗口"段，本轮是**收敛强化**（加 correlations 数值 + 起点时间权重 + 候选级确认），不是从零加。

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
