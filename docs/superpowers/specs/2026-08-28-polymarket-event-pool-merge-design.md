# Polymarket × 事件池合并设计稿(预测市场并入研究模块)

版本:v1.0(2026-08-28,四问拍板+五节逐节确认后落盘)
状态:待实施,分支 feat/polymarket-event-pool
上游:PENDING.md「预测市场 × 事件池联动」条目;docs/specs/news-research-phase1-event-pool.md(事件池一期);docs/specs/2026-08-13-pool-sweep-design.md(提案确认制交互范式)

---

## 0. 背景与拍板

现状:预测市场是孤立模块——跟踪清单全靠人去 Polymarket 网站找市场贴 slug;市场
到期断更靠人想起来换(PENDING 已挂号);5 分钟快照 30 天滚动删除;与事件研究零
联动。tag 自动发现 2026-07-08 因噪音停用,只认精确 slug。

目标:**预测市场降级为事件研究的观测层**。每个事件除新闻时间轴外可挂 0-N 个
Polymarket 市场,详情页里"新闻叙事 vs 市场定价"对照看;无事件可挂的仪表盘类
市场(全年降息次数等)住进池页「常设观测」区;预测页退役。

四个拍板(2026-08-28):

| # | 问题 | 拍板 | 拒绝的备选 |
|---|---|---|---|
| 1 | 发现机制 | **AI 提案+人工勾选**,沿用 AI 梳理的提案确认制 | 全自动后台匹配(配错会把无关曲线画进事件,污染研究记录);纯手动搜索框(发现仍靠人想起) |
| 2 | 合并程度 | **彻底合并,一次交付**,预测页不保留 | 两页保留只做联动;先联动后收编的两阶段(用户拍板不留中间态) |
| 3 | 数据保留 | **扫描降频 5min→60min,快照全量永久保留,无归档任务** | 30 天内 5min+之外降采样的每日归档(用户对预测精度无强要求;小时粒度年增量约 30 万行/几十 MB,不值得维护归档机器) |
| 4 | 价格目标类市场 | **提案阶段直接排除,两线统一**(宏观"油价破 100"与加密"BTC 到 15 万"同为价格影子);手动搜索通道不受限 | 提案沉底+标注默认不勾(列表变长);完全不设限(加密搜索结果被价格盘口刷屏) |

技术前提已验证(2026-08-28 经 mmon 服务器实测):Gamma `public-search?q=` 免费
无鉴权,返回事件 slug、标题、描述、量价、到期日、活跃状态及嵌套子市场——slug 可
直接进现有跟踪管道。

## 1. 数据模型

**新表 `research_event_markets`(事件↔市场挂接)**,完全对齐 `research_event_links`
的留痕模式:

| 字段 | 说明 |
|---|---|
| id | 主键 |
| event_id | 挂哪个事件(research_events.id) |
| tracked_id | 挂哪条跟踪项(tracked_markets.id;slug 粒度,event slug 展开的多个子市场共享一条挂接) |
| link_source | "auto"(AI 提案人工确认)/ "human"(手动挂) |
| confidence | 三档 0.9/0.65/0.3,仅 auto(与新闻挂接同刻度) |
| prompt_version | 提示词版本(审计) |
| detached / detach_reason | 摘下留痕,不物理删除 |
| created_at / updated_at | 时间戳 |

约束:UniqueConstraint(event_id, tracked_id)。同一跟踪项可挂多个事件(罕见但真实:
一个停火市场服务两条相关事件线)。

**`tracked_markets` 加 `market` 列**("macro"/"crypto",NOT NULL DEFAULT
'macro'):宏观/加密两池是独立页面(二期 A 用户拍板),跟踪项必须知道自己属于哪条
线才知道住哪个页面。存量(config 种子约 15 个,全为 Fed/通胀/霍尔木兹/地缘题材)
默认即宏观,无需回填。

**零改动**:`prediction_markets` 表结构、`tracked_markets` 其余字段、软删除墓碑
机制。挂接指向 tracked 行,行是墓碑不硬删,挂接永不悬空;跟踪项被删(dismissed)
后事件详情隐藏其卡片,挂接行留审计。

**保留期**:`DATA_RETENTION["prediction_markets_days"]` 30 → None(永久)。

迁移走 `_ensure_sqlite_schema` 幂等建表+补列,与历次加列同款。

## 2. 扫描频率与伴生参数

- `SCAN_INTERVALS["prediction"]` 5 → 60。该配置键一直存在但从未被消费,本次把它
  变成真旋钮:`run_scan_once` 内加门控——`prediction_markets` 表内最新快照距今
  ≥ 间隔才跑 PredictionScanner。基准取 DB 不取内存,重启不丢节拍;Gamma 整体
  失败的那轮没写进快照,下轮 5 分钟周期自动重试(自愈,优于独立小时 job)。
- 跳过轮的源健康状态记 stage="skipped"(显示"未到间隔"),不误报采集异常。
- `PREDICTION_ACTIVE_GRACE_MINUTES`(图表"市场还活着"宽限期)默认值从固定 30
  改为**随扫描间隔联动:interval×2+30(=150)**,env 覆盖保留。不改这个,小时
  节奏下单次抓取偶发失败市场就从图上消失一小时。这不是动校准值,是参数定义随
  频率换算。
- **告警零改动**:`prediction_shift` 规则、5pp 阈值、window_minutes=15 全部原样。
  已核实 alerts/evaluators/predictions.py 的窗口回溯(找"至少 15 分钟前的快照")
  在小时粒度下自然取到上一小时点,代码兼容;语义从"15 分钟内跳 5pp"变为"一小时
  内跳 5pp",告警更少更重。阈值是否重校另起话题,不在本次范围。

## 3. AI 提案管线

**三个入口,同一条管线**(POST /api/research/market-sweep):
①池页「找市场提案」按钮(本线全部 active 事件);②事件详情市场定价区块的单事件
按钮(event_id 参数);③"找后继"=已结算/断流市场所挂事件上的同一个单事件按钮
(提案素材=事件,与②同一实现,不做每卡独立按钮——实施时收敛)。

**五步**:

1. 素材:每个事件取 名称 + gate_keywords + 最近 5 条未摘下挂接新闻的标题;
2. AI 调用①(批量一次):为每个事件生成 1-3 组**英文搜索词**——Polymarket 是英文
   平台,事件名是中文,这步本质是翻译+检索词扩展("俄乌停火"→"Russia Ukraine
   ceasefire");
3. 逐词调 Gamma public-search(limit_per_type=5),按 slug 去重,剔 closed/
   archived/inactive,交易量 < `POLYMARKET["proposal_min_volume"]`(默认
   10,000 USD,新配置项)的垃圾市场不要;
4. AI 调用②(批量一次):候选×事件配对打分——三档置信度、**剔除价格目标类**
   (prompt 定义:结算条件为某资产价格达到/越过某数值的市场,两线统一)、每条给
   中文推荐理由。防幻觉:AI 只能从本次搜索真实返回的 slug 白名单里挑,编造丢弃
   (同 pool_sweep 的 id 白名单思路);
5. 提案当场返回**不落库**;人工勾选后走 apply。

**apply**(POST /api/research/market-sweep/apply):勾选项写入——tracked_markets
(不存在→新建 market=事件线;存在且 dismissed→复活 enabled=True;存在且活着→
不动)+ research_event_markets(link_source="auto"+confidence+prompt_version;
已有未摘下挂接→跳过)。幂等;threading.Lock 防连点(单 worker=全局锁,同
sweep)。不设 dry_run——run 全程零写库,天然就是演练(实施时确认,较原稿收敛)。

AI 走现有 deepseek_client;长耗时复用 /api 既有 Nginx 600s 超时(sweep 先例,
零配置改动)。

**手动通道**(GET /api/predictions/search?q=):Gamma 搜索代理,结果一键添加、
可选归属事件或常设,**不剔价格类**——想跟"BTC 到 15 万"从这里走。

## 4. API 契约

| 接口 | 说明 |
|---|---|
| POST /api/research/market-sweep | {event_type, event_id?, dry_run?} → 提案清单 [{event_id, slug, title, current_probability, volume, end_date, confidence, reason}]。current_probability 口径:单市场事件取 Yes 概率;多子市场事件(降息次数分桶类)置空并带子市场数 |
| POST /api/research/market-sweep/apply | {event_type, items:[{event_id, slug, display_name?}]} → {added, revived, linked, skipped} |
| GET /api/research/events/{id}/markets | 事件详情市场卡数据:关联跟踪项+各子市场最新概率摘要(曲线复用既有 /predictions/{market_id}/history) |
| POST /api/research/event-markets/{link_id}/detach | {reason?} 摘下留痕 |
| GET /api/predictions/search | 手动通道 Gamma 搜索代理 |
| 既有 /predictions、/predictions/families | 加 market 线过滤参数(服务池页市场定价页签) |
| 既有 /predictions/tracked 系列 | 出入参加 market 字段;POST 支持可选 event_id(添加即挂接,link_source="human") |

前端 OpenAPI 类型照常再生成。

## 5. 前端

- **池页加第三页签「市场定价」**(现有:事件/旧事重提):找市场提案按钮(整线,
  提案确认制交互仿 AI 梳理)+ 手动搜索框 + 常设观测区(本线未挂接跟踪项的曲线卡,
  预测页"主题概率对比"聚合卡迁到这里)+ 跟踪管理表(迁自预测页,加"归属事件"列,
  可发起挂接/摘下/改归属)。
- **事件详情加「市场定价」区块**:每个关联市场一张卡——当前概率大字 + 24h 变化 +
  概率曲线(窗口 24h/7 天/30 天/1 年——"全部"以 1 年为上限落地;原预测页 2h/6h
  档在小时粒度下只剩 2-6 个点,删除)+ 交易量/到期日/已结算徽章 + 摘下按钮。新挂市场首轮采集前显示"等待首轮
  采集"占位(最长 1 小时)。
- 事件列表卡片加关联市场计数小徽章。
- 卡片徽章区分三种断流语义:**摘下**(事件断开,跟踪照旧)≠ **停用**(快照停止,
  曲线断流)≠ **已结算**(市场关闭,概率定格)。
- **预测页退役**:路由与导航删除,PredictionsPage.tsx 删除;PredictionCard/
  TrackedMarketsPanel 组件迁移复用。

## 6. 边界与风险

- Gamma 搜索失败或 AI 回复解析失败 → 提案整体报错,不写任何数据。
- 中文事件名的搜索质量依赖 AI①翻译;质量差时体现为提案不相关,由人工勾选把关
  (确认制兜底),不设自动重试。
- 加密线现实预期:Polymarket 加密区以价格盘口为主,叙事类事件(企业稳定币结算等)
  可能搜不到市场——提案为空是正常结果,前端如实显示"未找到",不算失败。
- 「找后继」按钮只出现在**挂了事件**的已结算/断流市场卡上(提案素材来自事件);
  常设市场断更走手动搜索通道,不做无事件推断。
- 存量清偿:上线后人工把现有跟踪项挂到对应事件(跟踪管理表归属操作),挂不上的
  自然留常设观测区,零脚本。
- 一次性瘦身(可选,部署时决定):存量 30 天 5 分钟粒度快照(~30 万行/约几十 MB)
  跑一条 SQL 瘦成每小时留最后一条(~7MB)。此后**没有任何定期归档任务**。
- 旧 filters.py(tag 自动发现时代的关键词过滤)本次不复用不删除,维持现状。

## 7. 测试与验收

- 后端:提案解析防幻觉(白名单外丢弃)/apply 幂等(新建/复活/跳过/重复提交)/
  小时门控(未到间隔跳过、DB 基准、重启不丢节拍)/grace 联动默认值/告警小时粒度
  窗口回溯/market 线过滤/事件市场卡接口。
- 前端:市场定价页签渲染、事件详情区块、归属操作、退役后路由;tsc -b 干净。
- 全量回归 pytest + vitest。
- 线上验收:部署后宏观线跑一轮真实提案人工核对质量;观察 24h 确认小时节拍稳定、
  告警不误报、源健康面板"未到间隔"显示正常。

## 8. 不改什么(红利清单)

- prediction_markets 表结构与既有快照数据;5 分钟 scan_cycle 框架(预测只是在
  框架内被门控);告警规则与用户校准的全部阈值;跟踪清单存量;事件池一期/二期 A
  的立案、挂接、梳理、沉睡监听全部机制;Nginx 配置。
