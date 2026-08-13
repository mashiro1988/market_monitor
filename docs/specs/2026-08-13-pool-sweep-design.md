# 事件池 AI 梳理(pool sweep)设计稿 — 2026-08-13(已实施)

## §1 背景与问题

一期/二期A 的模型分工是"**模型只有挂接权**":逐条判断新闻挂到哪个**已存在**的进行中
事件,立案权 100% 在人。结构性盲区:池子空(或缺事件)时,模型只能合法地判"不挂"。
实测(2026-08-13,线上库):加密线 7 天 218 条币标注快讯仅 1 条挂接;宏观线 7 天过闸
2193 条、挂接 912 条,漏网中至少有 4 个成簇主题(俄乌港口互袭/阿曼油轮泄漏/AI 算力
链财报/Anthropic)。缺的不是单条判断,是**集合层盘点**——没人负责把光点连成航迹。

## §2 决策(用户拍板 2026-08-13)

| 决策点 | 结论 | 备选与理由 |
|---|---|---|
| 触发方式 | 两个事件池页各一个「AI 梳理」按钮,**不做定时任务** | 备选=周日 cron;用户要按需触发、当场看结果 |
| 落库方式 | **直接立案+挂接**,不做"建议表+采纳"两段式 | 用户以事后审计代替事前签字:"我最后会看挂接率";立错可关闭/合并/摘下(既有机制全留痕) |
| 模型 | `DEEPSEEK_REASONER_MODEL`(v4-pro)+ thinking(`reasoning_effort=max`) | 与自动标注同一套口径(annotation_service);梳理是难任务,给足预算 |
| 铁律修订 | spec §6.1"仅人工立案"→"立案权不给定时任务;人触发的 AI 草稿可立案,`created_from="sweep"` 标记" | 审计链不断:种子挂接记 `link_source="auto"+prompt_version`,纠错率照算 |

## §3 流程(services/pool_sweep.py)

1. **取料** `_gather`:本线近 `RESEARCH_SWEEP_DAYS`(默认 7)天、`tagged_at` 非空、
   缓冲区口径(`buffer_predicate`,与快讯页"只看未挂事件"同源)的快讯,新→旧截取
   `RESEARCH_SWEEP_MAX_NEWS`(默认 800)条;超限 `truncated=true` 返回,**不静默**。
2. **盘点** `_call_sweep`:系统提示词三类产出——new_events(硬门槛:同主题 ≥3 条,或
   单主体重大事态 ≥2 条;名字含主体+事态;关键词循 spec §5.2 取词规则)/ attach(挂到
   现有事件,confidence 三档)/ 其余不动。`max_tokens=RESEARCH_SWEEP_MAX_TOKENS`(24k)。
3. **防幻觉** `_parse_sweep`:news_id 必须在本批、event_id 必须在活跃池、confidence
   必须三档,非法条目整条丢弃;关键词剔单字、名字截 80。与挂接器同一铁律。
4. **落库**:同名撞现有事件 → 降级为向该事件补挂(conf 0.65);新事件走 `create_event(
   created_from="sweep", link_source="auto", prompt_version=SWEEP_PROMPT_VERSION)`,
   立案自动回扫 72h(既有机制);补挂跳过已有挂接记录(**含已摘下**——人摘过的不悄悄
   挂回去)。new_events 超 `RESEARCH_SWEEP_MAX_NEW_EVENTS`(8)截断并计数返回。
5. **并发**:进程内 `threading.Lock` 防连点(单 worker 部署=全局锁),忙时 409。

## §4 观测口径变化

`daily_stats` 增加 `market` 参数,`GET /api/research/stats?event_type=` 各线各算:
分母"过闸"宏观按分数闸、加密按语义闸(`is_crypto_affair`),两个池子页各看各的当日
挂接率(此前两线混算且全按宏观闸,加密页读数失真)。不传参数保持旧混算口径兼容。
已知量纲注意:sweep 补挂的是**往日**新闻(分子计今天、分母不计),梳理当天挂接率
可能虚高甚至 >100%,读数时知道即可,不修口径(它本来就是"并行期观察数字")。

## §5 失败语义

- DeepSeek 空 content / 坏 JSON / HTTP 错误 → 502 `SWEEP_FAILED`,前端红字展示,不静默。
- 正在梳理中再点 → 409 `SWEEP_BUSY`。
- 非法 event_type → 400 `SWEEP_INVALID`。
- 长调用链路:前端 fetch 无超时 → uvicorn 线程池同步执行 → Nginx `proxy_read_timeout
  600s`(deployment.md Phase 5,自动标注时已配好)≥ DeepSeek 读超时 600s。

## §6 验收

用户口径:看两页各自的当日挂接率走势(事前:加密线近乎 0%)。辅助:纠错率应覆盖
sweep 产物(auto_event_id 已置);`dry_run=true` 参数留给验收/调试(只看提案不落库)。

## §7 实施偏离记录

无——按本稿实施。同日手工立案 9 个事件(加密 #2-#6、宏观 #10-#13)属运营操作,
不在本稿范围,记录见 PENDING。
