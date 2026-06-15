# 标注 v2：从二元勾选到训练级标签体系

> 来源：用户 ChatGPT 方法论讨论（2026-06-10，构建金融新闻数据集）。
> 目标：标注页产出可直接导出为 SFT 训练数据的结构化标签（新闻归因 + 噪音识别 + 情绪波动判断）。
> 已确认取舍：每条新闻只人工标 `causal_role`；自动标注同步升级到新 schema；旧标注自动映射迁移。

## v2.1 修订（2026-06-11，与用户逐条讨论定稿）

1. **causal_role 六 → 四**：`driver`（不分主次——主次判断主观且训练价值低，"主次"由日级聚合按 幅度×置信度 计算）/ `noise`（默认；**迟到首报也算 noise**：事件发生在窗口前且价格已在前一时段反应）/ `post_hoc_explanation` / `contradictory`（**仅限新发生的事件**方向与价格相反；报迟造成的表观矛盾按迟到首报标 noise）。
2. **market_reaction_type 八 → 三**（单轴=驱动源，与 roles 闭环：前两类⟺有 driver）：`macro_policy`（宏观数据+政策预期，二者传导链不可分）/ `event_driven`（其余明确突发事件）/ `no_news_driver`（情绪/仓位/技术/无法归因合一，确定性由 confidence 表达——确认纯情绪给高置信）。流动性/仓位/技术面类标签因输入数据（无持仓/盘口/指标）不可判定而移除。
3. **新增三列**：`auto_news_roles`（AI 原始标注快照，人机分歧=难例信号）、`prompt_version`、`eval_set`（评估集冻结，训练导出默认排除，`export?split=train|eval|all`）。
4. **双档窗口 + 净变动门槛**（`config.ANNOTATION_WINDOW_SCALES`，阈值按近 5 天分布校准）：15m 档（NQ 0.3%/净0.6%、BTC 0.5%/净1.0%，候选前 30）+ 60m 档（NQ 0.75%/净1.0%、BTC 1.2%/净1.5%，候选前 60）；跨档重叠同向合并。验收锚点=6/10 夜：横跳 11 窗口→3 真实事件、-1.02% 慢跌被 60m 档稳定捕获（含缺 2 根 bar 的生产形态）。
5. **缺口自愈 job**（`services/gap_repair.py`，每小时 :37）：扫近 24h 快照缺口→一次批量回补→复扫→按**回补结果**分类（源端无数据=休市静默）→企业微信完整账目（发现/补回/仍缺+原因），无缺口轮次静默。
6. **日级聚合日界 = 北京时间 06:00 固定**（不判断夏令时）；日级 factor_ranking 层设计待后续讨论。
7. 事件簇去重：**不做**（折叠不过滤=纯装饰，token 非约束）。债券保持 5 分钟采集（CNBC 无限频问题，且 10Y 对标依赖 10 分钟容差）。

## 1. 标签体系（v2.1 定稿）

> 以代码 `schemas/annotations.py` 的 `NEWS_CAUSAL_ROLES` / `MARKET_REACTION_TYPES` 为准。
> 顶部「v2.1 修订」记录了从初版 六分类 role + 八分类 reaction_type 收敛到下面 4+3 的过程。

**每条新闻（人工标注唯一维度）— `causal_role` 四分类：**

| 值 | 中文 | 语义 |
|---|---|---|
| `driver` | 驱动 | 触发本窗口异动；**不分主次**（同事件簇的全部相关报道都算 driver，主次由日级聚合按 幅度×置信度 计算） |
| `noise` | 噪音 | 无关 / 背景（**默认值，不落库**）；**含迟到首报**——事件发生在窗口前、价格已在前一时段反应过 |
| `post_hoc_explanation` | 事后解释 | 价格先动、新闻事后找理由（行情综述类） |
| `contradictory` | 方向矛盾 | **仅限新发生**的事件、方向与价格真实走向相反（如缓和消息却下跌）；报迟造成的表观矛盾按 noise |

**窗口级：**

| 字段 | 取值 |
|---|---|
| `market_reaction_type` | 单轴=驱动源，与 roles 闭环（前两类 ⟺ 存在 driver）：`macro_policy` 宏观与政策预期（数据/央行/官员/财政） / `event_driven` 其他明确事件驱动（地缘/制裁/监管/行业/标的专属） / `no_news_driver` 无新闻驱动（情绪/仓位/技术/无法归因合一，确定性由 confidence 表达） |
| `confidence` | 0-1 浮点；UI 三档：高 0.9 / 中 0.65 / 低 0.3 |
| `summary` | 因果链一句话（沿用 notes） |

**兼容映射（双向）：**
- `no_clear_news` ⟺ `market_reaction_type == no_news_driver`（API 继续返回 no_clear_news 派生值）
- `causal_news_ids` ⟺ roles 里 `driver` 的 id（继续写入，老消费方不破）

## 2. 存储

`news_price_annotations` 新列（`_ensure_sqlite_schema` 裸 ALTER 补列）：
- `news_roles` TEXT — JSON dict `{news_id: role}`，**只存非 noise 条目**
- `market_reaction_type` VARCHAR(40)
- `confidence` FLOAT

**一次性迁移**（`database.py`，两步均幂等）：
- **步骤 1（v1 二元勾选行，`WHERE news_roles IS NULL`）**：`causal_news_ids` 全部 → `driver`（v2.1 不分主次）；`no_clear_news=1` → `market_reaction_type='no_news_driver'`；`confidence` 留 NULL = 旧样本低保真标记（导出时 `schema_version:1`）
- **步骤 2（v2.0 旧枚举行）**：`news_roles` 值按升级映射归一（`primary_driver`/`secondary_driver`/`amplifier` → `driver`），八分类 `market_reaction_type` → 三分类

## 3. 自动标注输出契约（单窗口 + 批量同步升级）

```json
{
  "news_roles": {"6515": "driver", "6517": "driver", "6537": "post_hoc_explanation"},
  "market_reaction_type": "event_driven",
  "confidence": 0.85,
  "summary": "≤80字因果链"
}
```
- 未列出的候选 = noise（输出紧凑，候选可上百条）
- 解析器过滤幻觉 id、非法 role/type，confidence clamp 到 [0,1]
- `no_clear_news` 由解析器派生（无 `driver` → true）
- 原有保守原则 / 跨资产签名 / 对标不可用条款全部保留，把"不选"语义改写为"标对角色"（综述→`post_hoc_explanation`，同事件簇的补充/重复转述→`driver` 或 `noise`，**新发生**且方向反向→`contradictory`）
- 实弹回放双场景升级判定：场景2 期望 6515/6517 标为 `driver`、6537 标为 `post_hoc_explanation` 或 `noise`、type 为 `event_driven`（非 `no_news_driver`）

## 4. 导出（落 PENDING「标注导出/训练集生成」）

`GET /api/annotations/export?days=N` → JSONL（application/x-ndjson），每行：
窗口元数据 + reference_changes（实时重算）+ 全量候选新闻（含标题/内容/时间，负样本即未标 noise 条目）+ 标签（news_roles 全量展开含 noise / reaction_type / confidence / summary）+ labeler / schema_version。

## 5. 前端（AnnotationsPage v2）

- 候选新闻表：勾选框列 → 角色 `<select>`（四选项：驱动/方向矛盾/事后解释/噪音，默认噪音）；草稿 batchByKey 增加 news_roles
- 保存块：reaction_type 三选一 `<select>`（宏观与政策/事件驱动/无新闻驱动）+ 置信度三档按钮组；summary 沿用（`no_clear_news` 由后端从 `market_reaction_type==no_news_driver` 派生，前端不再单独勾选）
- 推理面板/已标注列表：展示 reaction_type 徽章 + driver 数
- sessionStorage key 升 `annotations.session.v2`（旧草稿 schema 不兼容，直接弃读）
- 写回沿用事件驱动 updateDraft（不回退 effect 镜像）

## 6. 不做（本期）

- affected_assets / expected_direction 人工标注（交给未来自动生成+人审）
- 窗口分档（宏观数据 T±、突发 15m/30m/1h/4h）——沿用价格触发窗口
- EasyDataset 对接 / ChatML 导出变体（JSONL 先行，schema 固定后加一个转换脚本即可）
