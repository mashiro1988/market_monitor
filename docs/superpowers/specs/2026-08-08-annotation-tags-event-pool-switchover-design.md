# 标注页切换：待标注 7/16 截断 + 内容标签并入事件池 + 量级退役

日期：2026-08-08 · 状态：用户已授权直接实施+部署（免逐段确认）

## 0. 触发与依据

用户指令（2026-08-08）：

1. 待标注列表去掉 7 月 16 日以前的窗口；
2. 候选新闻窗口的内容标签与事件池联动，不再各顾各的；
3. 冲击大中小（=量级 magnitude_tier）的判断拿掉；
4. 所有信息从其他模块复用。

其中第 2 条 = 执行 news-research-phase1-event-pool.md §13.4 既定的"切换"步骤（并行期
2026-08-03 上线起观察，切换动作原文：删打标提示词 topic 槽位、停写 news_items.topic、
NEWS_TOPICS/theme_ledger/backfill_ledger 冻结为遗留、tag-options 的 topics 键与前端
topic 下拉退役、标注页位置由事件徽章接替 §9.2）。第 3 条是对既定方案的加码：思路稿
v0.3 曾写"方向/量级现状完全不动"，本次用户拍板量级判断一并退役。方向（利多/利空/中性）
无人要求动，保留——研究页时间轴方向标、新闻页行都在用。

## 1. 改动 A：待标注窗口 7/16 截断

- `config.py` 新增 `ANNOTATION_WINDOW_MIN_START_UTC = datetime(2026, 7, 15, 16, 0)`
  （= 北京 2026-07-16 00:00，注释写明口径）。
- `annotation_service.load_price_windows` 全量模式（标注页唯一路径）：
  `display_cutoff = max(最早行为段, 该下限)`。hours>0 与调试路径不动（时间只会往后走，
  回溯窗永远晚于该下限）。
- **勾稽联动**：`list_annotations` 的 needs_review 时代守卫同步抬升为
  `max(era_anchor, 下限)`，否则 7/16 前老标注因窗口不再返回而全体误亮"需复核"。
- 收益：待标注列表、"批量自动标注(剩余 N)"计数、批量 DeepSeek 推理共用同一来源，
  老窗口不再出现也不再耗 token。

## 2. 改动 B：候选新闻窗口（AnnotationsPage）列改造

现列：角色｜时间｜来源｜LLM｜内容标签（可改：主题/量级/方向三联下拉）｜事件｜标题
新列：角色｜时间｜来源｜LLM｜方向（只读）｜事件｜标题

- 主题下拉退役，语义分类由"事件"列（EventAttach：已挂徽章+driver 快捷挂接）接替——
  正是 spec §9.2 设计 EventAttach 时预留的位置。
- 量级下拉删除，不迁移。
- 方向改只读展示，复用新闻页同款着色（利多绿/利空红/其余灰）。
- 删 AnnotationsPage 里 tagOptions query、updateTags mutation；client.ts 删两个方法。

## 3. 改动 C：打标切换（后端）

- `news_tagging.py`：提示词只判 direction；解析/落库只写 news_direction + tagged_at；
  头注释记录切换日期与原因（spec §13.4 要求）。`update_news_tags` 删除（唯一消费者是
  标注页下拉）。tagged_at 继续盖章——事件挂接游标依赖它（§4.1 捞 tagged_at 非空）。
- `api/routes.py`：删 `GET /annotations/tag-options`、`PATCH /news/{id}/tags`；
  schemas 删 NewsTagUpdateRequest；重新生成前端 OpenAPI 类型。
- `config.py`：NEWS_TOPICS、NEWS_MAGNITUDE_TIERS 标注"已冻结·遗留（历史数据仍持有该
  枚举值）"；NEWS_DIRECTIONS 仍在役。`BEHAVIOR_NEWS_MAGNITUDES` 删除（唯一消费者见下）。
- `theme_ledger.py` / `scripts/backfill_ledger.py`：头注释标冻结·遗留。
  注意 forward_reaction/observed_reaction 等价格反应函数**在役**（事件池时间轴观测值
  在用），冻结的只是 topic 维度的台账聚合。
- 数据不动：news_items.topic / magnitude_tier 列与历史值原地保留，不迁移不清洗。

## 4. 改动 D：行为机器分类 has_news 信号换口径（量级唯一活下游）

`behavior_classifier._news_ids` 现用 `magnitude_tier ∈ ("大","中")` 判"窗口附近有无
可指认新闻"。量级停写后该过滤器对新新闻永远落空，机器分类的新闻命中信号会无声死亡。

换成事件池闸门同款口径：`llm_importance IS NULL OR llm_importance >= EVENT_LINK_MIN_IMPORTANCE(6)`。

依据：spec §4.2 线上 30 天校准，"≥6 或未评分"对人工 driver 召回 96%；对照组
"≥6 且量级非小"仅 77%（11 条真 driver 被模型打成"小"）——量级本就是较差的信号。
不用"已挂事件"做信号：新事件只能人工立案，突发冲击当刻池子里没有对应事件，
机器首判会系统性漏掉最重要的窗口；分数在扫描时即有，无延迟无覆盖缺口。

**这是一处校准口径变更**（preserve-calibrated-config 红线），在完工报告里单独向用户
标出：机器分类的"有新闻"判定从"量级大/中"改为"分数≥6 或未评分"，触发面会变宽；
human_class 人工复核照旧覆盖。

## 5. 不做什么

- 不动 NewsItem 模型列、不清洗历史 topic/magnitude 数据。
- 不动方向判定、不动评分/挂接管道。
- 不动已标注列表展示、训练导出字段（历史标注照常读）。
- 不做"已挂事件"作为 has_news 信号（理由见 §4）。

## 6. 任务清单

1. 后端：config 下限常量 + load_price_windows 截断 + needs_review 守卫（含测试）。
2. 后端：news_tagging 提示词/解析/落库瘦身，删 update_news_tags（改测试）。
3. 后端：删 tag-options / PATCH news tags 路由与 schema（改测试）。
4. 后端：behavior_classifier 闸门口径 + 删 BEHAVIOR_NEWS_MAGNITUDES（改测试）。
5. 冻结注释：config / theme_ledger / backfill_ledger / news_tagging 头注。
6. 重新生成 OpenAPI 类型；前端 AnnotationsPage 列改造 + client.ts 清理。
7. 全量测试（pytest + 前端 build/test），提交推送，按 runbook 部署 mmon.top，线上验证。

## 7. 验收

- 线上待标注列表最早窗口 ≥ 北京 2026-07-16；已标注列表 7/16 前旧标注不亮"需复核"。
- 候选新闻表无主题/量级下拉；方向只读着色；事件列可挂接照旧。
- 新入库新闻 tagged_at 正常盖章、news_direction 有值、topic/magnitude_tier 为空；
  事件挂接管道照常运转。
- 行为页机器分类照常产出（新窗口 has_news 走分数口径）。
