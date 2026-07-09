# 标注体系：Phase3a 新闻归因标签

> 来源：2026-06-10 起的金融新闻数据集方法论讨论；2026-06-23 Phase3a 收敛。
> 当前事实来源：`schemas/annotations.py:11`、`schemas/annotations.py:37`、`services/annotation_service.py:708`、`services/annotation_service.py:795`、`database.py:112`、`tests/test_annotation_v2.py:2`。
> 目标：标注页产出可直接导出为训练数据的结构化标签：窗口归因、噪音识别、人机分歧和评估集切分。

## 1. 当前标签契约

### 每条新闻：`causal_role`

`NEWS_CAUSAL_ROLES` 当前只有 3 个值，见 `schemas/annotations.py:11`。

| 值 | 中文 | 语义 | 落库 |
|---|---|---|---|
| `driver` | 驱动 | 触发或推动本窗口异动的主事件；同一事件簇里信息量最大 / 最主要的一条。 | 是 |
| `redundant` | 同簇冗余 | 与 driver 同一事件簇的其它相关报道；相关但非主驱动，训练时排除、不当负样本。 | 是 |
| `noise` | 噪音 | 默认值：无关、背景、综述、解释、离题、方向相反或已定价。 | 否 |

`post_hoc_explanation` 和 `contradictory` 已退场，并入 `noise`。旧数据迁移会把这两类从 `news_roles` 中移除，见 `database.py:161`。

### 历史兼容字段：`market_reaction_type`

`MARKET_REACTION_TYPES` 保留给历史数据和旧消费方，见 `schemas/annotations.py:17`。Phase3a 后前端不再让人填写，自动标注 prompt 也不再要求模型输出；窗口“是否无明确诱因”主要由 `news_roles` 中是否存在 `driver` 派生。

| 值 | 中文 | 语义 |
|---|---|---|
| `macro_policy` | 宏观与政策 | 宏观数据、政策预期或二者传导链不可分。 |
| `event_driven` | 事件驱动 | 其余明确突发事件驱动。 |
| `no_news_driver` | 无新闻驱动 | 无 driver，可解释为情绪、仓位、技术或无法归因；确定性由 `confidence` 表达。 |

`confidence` 是 0-1 浮点；前端三档固定为高 0.9 / 中 0.65 / 低 0.3。新 Phase3a 保存请求（`news_roles` 路径）必须提供 `confidence`；null 仅用于旧格式/迁移样本的低保真标记。

## 2. 兼容字段

`causal_news_ids` 和 `no_clear_news` 仍保留给旧消费方，但自 Phase3a 起都由 `news_roles` 派生，前端保存时不再手填旧含义；历史请求若显式带 `market_reaction_type == "no_news_driver"`，后端仍会按兼容口径置 `no_clear_news`。

| 字段 | 派生规则 | 代码 |
|---|---|---|
| `causal_news_ids` / `selected_news_ids` | `news_roles` 里全部 `driver` 的 id。 | `services/annotation_service.py:795` |
| `no_clear_news` | 没有任何 `driver`；历史兼容请求里 `market_reaction_type == "no_news_driver"` 也会置 true。 | `services/annotation_service.py:795` |

## 3. 存储与迁移

`news_price_annotations` 中与当前标注相关的列：

- `news_roles` TEXT：JSON dict `{news_id: role}`，只存非 `noise` 条目。
- `market_reaction_type` VARCHAR(40)：三分类之一或 null。
- `confidence` FLOAT：0-1；新 Phase3a 保存请求必填，null 表示旧样本低保真。
- `auto_news_roles` TEXT：AI 原始标注快照，人改前保留。
- `prompt_version` VARCHAR：产生 auto_* 的提示词版本。
- `eval_set` BOOL：评估集冻结，训练导出默认排除。

启动时 `database.migrate_legacy_annotations` 幂等迁移：

1. v1 二元勾选行：`causal_news_ids` 全部转 `driver`；`no_clear_news=1` 转 `no_news_driver`。
2. v2.0 旧枚举：`primary_driver` / `secondary_driver` / `amplifier` 转 `driver`；旧 reaction type 映射到三分类。
3. v2.1 退场角色：`post_hoc_explanation` / `contradictory` 从 `news_roles` 移除，按默认 `noise` 处理。

## 4. 自动标注输出契约

单窗口和批量自动标注都输出同一套结构；非法 role、幻觉 id 会被解析器过滤。解析器仍兼容历史 `market_reaction_type`，但当前 prompt 不要求输出。

```json
{
  "news_roles": {"6515": "driver", "6517": "redundant"},
  "confidence": 0.85,
  "summary": "80字以内因果链",
  "reasoning": "该窗口专属解释，批量路径可选"
}
```

约束：

- 未列出的候选新闻默认是 `noise`。
- `driver` / `redundant` 可由人工或 LLM 逐条直接标。
- `redundant` 不再由导出阶段按 topic / 量级自动派生；这是 2026-06-23 明确反转的方案。
- `post_hoc` / `contradictory` 不得出现在 prompt 输出中；测试见 `tests/test_annotation_v2.py:353`。

## 5. 导出

`GET /api/annotations/export?days=N&split=train|eval|all` 返回 JSONL。每行包含：

- 窗口元数据、`reference_changes`、`reference_change_segments`（前1h / 窗口 / 后1h，包含标注品种本身作为比较基准）和 `s_scores`（共振分 S 证据：`{标签: {s, ess, coverage}}`；2026-07-09 prompt v11 起取代 `correlations`——±1h Pearson 实测判别力≈随机）。
- 全量候选新闻，未标的候选导出为 `causal_role = "noise"`。
- 人工 / LLM 直接标的 `driver` / `redundant` 原样进入 candidates。
- `redundant` 样本训练时排除，不当作负样本。
- `schema_version`：`confidence is not null` 为 2，否则旧样本为 1。

## 6. 前端

- 候选新闻表使用角色下拉：噪音 / 驱动 / 同簇冗余。
- 保存区使用置信度三档 + summary；未选择置信度时提示用户补选，不再展示或保存 reaction type。
- 宏观对标显示标注品种本身和对标资产的前1h / 窗口 / 后1h 涨跌；同步相关只对其它对标资产显示。
- `sessionStorage` key 使用 Phase3a 口径，避免旧草稿残留 retired roles。
- 窗口净值图只标出 `driver` 竖线；`contradictory` marker 已删除。
