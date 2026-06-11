# 标注 v2：从二元勾选到训练级标签体系

> 来源：用户 ChatGPT 方法论讨论（2026-06-10，构建金融新闻数据集）。
> 目标：标注页产出可直接导出为 SFT 训练数据的结构化标签（新闻归因 + 噪音识别 + 情绪波动判断）。
> 已确认取舍：每条新闻只人工标 `causal_role`；自动标注同步升级到新 schema；旧标注自动映射迁移。

## 1. 标签体系

**每条新闻（人工标注唯一维度）— `causal_role` 六分类：**

| 值 | 中文 | 语义 |
|---|---|---|
| `primary_driver` | 主驱动 | 直接触发本窗口异动 |
| `secondary_driver` | 次驱动 | 辅助驱动 / 同事件簇的补充报道 |
| `amplifier` | 放大器 | 放大既有趋势 |
| `noise` | 噪音 | 无关 / 背景（**默认值，不落库**） |
| `post_hoc_explanation` | 事后解释 | 价格先动、新闻找理由（行情综述类） |
| `contradictory` | 方向矛盾 | 新闻方向与价格反应相反（如缓和消息+下跌） |

**窗口级：**

| 字段 | 取值 |
|---|---|
| `market_reaction_type` | `fundamental_repricing` 基本面重估 / `policy_expectation_shift` 政策预期 / `liquidity_shock` 流动性冲击 / `risk_sentiment` 风险偏好 / `positioning_squeeze` 仓位挤压 / `emotional_noise` 情绪波动 / `technical_move` 技术面 / `no_clear_driver` 无明显驱动 |
| `confidence` | 0-1 浮点；UI 三档：高 0.9 / 中 0.65 / 低 0.3 |
| `summary` | 因果链一句话（沿用 notes） |

**兼容映射（双向）：**
- `no_clear_news` ⟺ `market_reaction_type == no_clear_driver`（API 继续返回 no_clear_news 派生值）
- `causal_news_ids` ⟺ roles 里 `primary_driver` + `secondary_driver` 的 id（继续写入，老消费方不破）

## 2. 存储

`news_price_annotations` 新列（`_ensure_sqlite_schema` 裸 ALTER 补列）：
- `news_roles` TEXT — JSON dict `{news_id: role}`，**只存非 noise 条目**
- `market_reaction_type` VARCHAR(40)
- `confidence` FLOAT

**一次性迁移**（schema patch 内，幂等 `WHERE news_roles IS NULL`）：
- 旧 `causal_news_ids` → 第一条 `primary_driver`、其余 `secondary_driver`
- 旧 `no_clear_news=1` → `market_reaction_type='no_clear_driver'`
- `confidence` 留 NULL = 旧样本低保真标记（导出时 `schema_version:1`）

## 3. 自动标注输出契约（单窗口 + 批量同步升级）

```json
{
  "news_roles": {"6515": "primary_driver", "6517": "secondary_driver", "6537": "post_hoc_explanation"},
  "market_reaction_type": "risk_sentiment",
  "confidence": 0.85,
  "summary": "≤80字因果链"
}
```
- 未列出的候选 = noise（输出紧凑，候选可上百条）
- 解析器过滤幻觉 id、非法 role/type，confidence clamp 到 [0,1]
- `no_clear_news` 由解析器派生（无 primary_driver → true）
- 原有保守原则 / 跨资产签名 / 对标不可用条款全部保留，把"不选"语义改写为"标对角色"（综述→post_hoc，重复转述→secondary 或 noise，缓和反向→contradictory）
- 实弹回放双场景升级判定：场景2 期望 6515/6517 ∈ primary/secondary、6537 ∈ post_hoc/noise、type 非 no_clear/emotional

## 4. 导出（落 PENDING「标注导出/训练集生成」）

`GET /api/annotations/export?days=N` → JSONL（application/x-ndjson），每行：
窗口元数据 + reference_changes（实时重算）+ 全量候选新闻（含标题/内容/时间，负样本即未标 noise 条目）+ 标签（news_roles 全量展开含 noise / reaction_type / confidence / summary）+ labeler / schema_version。

## 5. 前端（AnnotationsPage v2）

- 候选新闻表：勾选框列 → 角色 `<select>`（六选项，默认噪音）；草稿 batchByKey 增加 news_roles
- 保存块：no_clear 勾选框 → reaction_type 八选一 `<select>` + 置信度三档按钮组；summary 沿用
- 推理面板/已标注列表：展示 reaction_type 徽章 + 主驱数
- sessionStorage key 升 `annotations.session.v2`（旧草稿 schema 不兼容，直接弃读）
- 写回沿用事件驱动 updateDraft（不回退 effect 镜像）

## 6. 不做（本期）

- affected_assets / expected_direction 人工标注（交给未来自动生成+人审）
- 窗口分档（宏观数据 T±、突发 15m/30m/1h/4h）——沿用价格触发窗口
- EasyDataset 对接 / ChatML 导出变体（JSONL 先行，schema 固定后加一个转换脚本即可）
