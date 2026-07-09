# 决策日志 - market_monitor

> 最新的放最前。每条 5 行：日期 / 背景 / 决策 / 拒绝的备选 / 影响。

## 2026-07-09 - 价格行为引擎落地：共振分 S 取代相关性判据，prompt v11

- 背景：用户复盘流定案（spec `docs/specs/volume-behavior-engine-discussion.md` v0.4）——判断 BTC 行情由技术面（情绪/庄家）、行业事件、宏观新闻还是纯共振驱动；旧 ±1h Pearson 判据实测判别力≈随机（错位对照 lift≈1.0），量能路线因数据现实整体退出。
- 决策：三档段（0.3 计数 / 0.5 构成起点 / 0.8 重拳）+ 共振分 `S = Σz_btc²·clip(z_ref·sign(z_btc))/Σz_btc²`（判级 max|S|：0.5/0.3 cutoff，ESS<5 证据薄，覆盖<50% 无对照）+ S×新闻十字格分类（无对照≠无宏观新闻）+ PIT 日汇总；auto-annotate payload/prompt v11 换 S 证据链；标注窗口源开关默认关；校准四件套脚本只建议不改 config；rolling S 30 点曲线纯展示不告警。
- 拒绝的备选：量能触发+槽位基线（7-8 个自由度、薄市场 z 爆炸）；区间重叠/三刀/时点日历判共振（lift 1.0-2.5 或被用户判反逻辑）；GPT5.5 外部方案原版（窗口内 z-score 尺度归零，null 下 35-49% 高分）；lag 搜索（实测共振基本同步）；必审/抽审机制（未确认段全留存随时审）。
- 影响：新表 `behavior_segments`/`behavior_daily_summaries`、`/api/behavior/*` 三端点、`/behavior` 页、`behavior_cycle`/`behavior_daily_summary` 两个 job；训练导出 window 契约 `correlations`→`s_scores`；retention 30→90d；CL/^N225/US_2Y 三档待校准脚本首跑后用户拍板（config 置 None=禁用）。

## 2026-07-02 - 三段方向链补标标的本身，美债对标改 2Y

- 背景：固定三段涨跌只显示大类资产时，BTC 窗口缺少 BTC 自己的前1h / 窗口 / 后1h 基准，人工无法直接比较“BTC 是否跟着某类资产走”；利率预期判断更关注短端，用户不希望标注页继续看美债10Y。
- 决策：`ReferenceChange` 对 `is_self=true` 的标注品种同样填 `pre_pct` / `pct` / `post_pct`，但不算同步相关；`reference_change_segments` 包含标注品种本身作为比较基准，`reference_changes` 仍排除本身；默认对标清单把 `US_10Y / 美债10Y` 换成 `US_2Y / 美债2Y`；prompt bump 到 `v10-20260702`。
- 拒绝的备选：继续只展示对标资产三段涨跌；同时展示 2Y 和 10Y；保留 10Y 但在 prompt 里解释短端。
- 影响：标注页能直接横向比较 BTC 本身和纳指/日经/原油/黄金/美债2Y/美元指数的三段方向；利率通道更贴近加息/降息预期变化。

## 2026-07-02 - 宏观对标相关性降级，固定三段涨跌辅助肉眼标注

- 背景：人工查看标注页时发现同步 Pearson 相关性普遍偏低，可能是 BTC 晚几根 5min K 线才跟随大类资产，单一相关系数容易误导；但调窗口或引入 lag 会带来拟合风险。
- 决策：保留 `correlations` 但改称同步相关、只作参考；`ReferenceChange` 增 `pre_pct` / `post_pct`，与原 `pct` 组成固定「前1h / 窗口 / 后1h」三段涨跌；auto payload 和训练导出增 `reference_change_segments`；prompt bump 到 `v9-20260702`，要求优先看三段方向链，再用同步相关和新闻含义交叉验证。
- 拒绝的备选：寻找最佳 lag；按新闻时间动态重算窗口；调大/调小相关性窗口或阈值来提高相关性读数。
- 影响：标注页更贴近“新闻出来后 BTC 是否明显跟着某类资产走”的肉眼判断；低同步相关不再直接否定联动，方向链与新闻含义相反才降级。

## 2026-07-02 - 标注页强制归因置信度，宏观对标加入相关性和日经

- 背景：人工标注页允许空 `confidence` 保存，导致新训练样本可能被导出为低保真；宏观对标只显示同期涨跌，未把已计算的 `correlations` 展示出来；默认对标缺少日经，自动标注 prompt 的实际判断步骤仍偏向先看 `reference_changes`。
- 决策：新 Phase3a 保存请求（`news_roles` 路径）必须带 `confidence`，前端空值保存时提示用户选择高/中/低，后端兜底 400；`ReferenceChange` 增 `correlation`，由 `_reference_correlations_for_window` 计算并在待标注/已标注宏观对标中显示；`ANNOTATION_REFERENCE_ASSETS` 增 `^N225` 日经225；prompt bump 到 `v8-20260702`，判断顺序改为先看相关性、再找相关资产新闻、最后用其它大类资产交叉验证。
- 拒绝的备选：只做前端提示（API 仍可写入空置信度）；把相关性只放 auto-annotate payload 不进 UI schema；继续仅用同期涨跌路由新闻（会忽略“BTC 实际在跟谁走”的证据）。
- 影响：新标注结果不再缺置信度；宏观对标成为“涨跌 + 相关性”双指标；BTC 归因会优先识别与纳指/日经/黄金/原油/美元/美债的联动主线，再用新闻和其它资产方向验证因果链。

## 2026-07-01 - 合并前收口：移除 schedule CLI、实现 sector pending retry、reaction type 退为兼容字段

- 背景：合并前审核发现工作区入口调用不存在的 `config.setup_runtime()`；`python run.py schedule` 与 FastAPI 调度不一致且漏 `gap_repair/news_tagging`；sector scan 下载成功后若下游失败，同 cutoff 不重试；前端隐藏 `market_reaction_type=no_news_driver` 可能把手动 driver 保存成 `no_clear_news=true`。
- 决策：回到当前代码实际支持的 `config.PROXY` import-time 模型；移除独立 `schedule` CLI，调度只由 FastAPI lifespan 负责；`remote_puller` 增加内存态 `pending_sector_retry_cutoff_ts`，只有 `SectorScanner().scan()` 写出 `sectors_written > 0` 才清 pending；前端不再保存 `market_reaction_type`，仅由是否存在 driver 派生 no_clear；新闻标签 PATCH 用显式 null 清空字段。
- 拒绝的备选：临时补一个 `setup_runtime()` 函数（与现有设计/spec 冲突）；继续保留 `schedule` 但补任务（会维持双调度入口）；把部分长周期 ret 为空定义成 scan 失败（会误伤正常历史不足）；继续传隐藏 reactionType（会污染标签）。
- 影响：`python run.py frontend-build` 恢复可运行；同 cutoff sector scan 失败会在后续 cycle 重试；新标注不会再出现“有 driver 但 no_clear=true”的前端保存路径；清空新闻 topic/量级/方向可用。

## 2026-06-29 - 板块扫描采用 pending retry 状态，下载完成不等于下游完成

- 背景：`remote_puller._pull_if_newer()` 下载 pivot 成功后会推进 `last_cutoff_ts`，随后 `_run_sector_scan()` 才计算并写 `sector_returns`；如果下游 scan / 写库失败，同一 cutoff 下一轮会被视为“已拉过”而不重扫。
- 决策：用户同意采用 **pending retry** 语义。后续实现应区分 `last_pulled_cutoff_ts` 与 `last_sector_scanned_cutoff_ts`：只有 `SectorScanner().scan()` 至少写入一批 `sector_returns`，才确认该 cutoff 已完成。部分周期为 null、部分 symbol NaN、部分板块因 token<3 跳过，仍算成功但带 warning。
- 拒绝的备选：继续把“下载成功”当作整条板块管道完成；简单等待下一根 pivot；把部分周期缺失定义为失败（会误把正常历史不足当异常）。
- 影响：Claude 下一步可直接实现状态拆分与 retry/backoff；没有写入任何 `sector_returns`、出现异常或 `skipped_reason` 时，不应确认该 cutoff 的 sector scan 完成。

## 2026-06-29 - Phase3a 文档对齐 + 测试/前端/代理小债务清理

- 背景：代码审核发现真实代码已是 Phase3a 三角色（driver/redundant/noise），但根地图和 `docs/specs/annotation-v2.md` 仍停在 v2.1 四角色；根目录 `python -m pytest` 会收集 `Pending_functions/` 实验测试；前端净值图和草稿派生仍残留 retired roles；`config.py` import 时探测代理有副作用。
- 决策：文档以 `schemas/annotations.py`、`database.migrate_legacy_annotations`、`annotation_service._derive_compat_fields` 和 Phase3a 测试为准：retired roles 并入 noise，selected=全部 driver，no_clear=无 driver 或 no_news_driver。新增 `pytest.ini` 限定 `testpaths=tests` + repo 内 `.pytest_tmp`；前端移除 contradictory marker，sessionStorage key 升 Phase3a；代理探测移到 `config.setup_runtime()`，由 `run.py` / FastAPI lifespan 显式调用。
- 拒绝的备选：让项目地图入库（用户确认这些地图本来就要 ignore）；继续兼容浏览器旧草稿里的 v2 角色（会把 retired roles 带回新 UI）；在本轮顺手合并两套 scheduler 或改 sector retry（仍需业务决策）。
- 影响：普通 import config 不再联网/改环境；根目录 pytest 不再扫实验目录；前端和文档与 Phase3a 口径一致。仍待判断：`run.py schedule` 是否必须补 gap_repair/news_tagging；sector scan 失败是否要同 cutoff 重试。

## 2026-06-10 - 预测快照打 origin 标，图表按跟踪项软删状态精确过滤（断流启发式只作旧数据兜底）

- 背景：跟踪市场软删除（2026-06-09）只挡住了扫描器，已停跟踪市场的历史快照仍在回看窗口里，图表上删不干净。第一版用纯「断流启发式」（最后快照落后表内最新超 30min 即剔除），多智能体审查确认了 4 条真实问题：CPI 公布后 tag 发现型市场整族历史蒸发（与 cec9898 CPI 归族复盘功能冲突）、单 slug 接口抖动 >30min 误删在跟踪市场、掉出 tag top-5 闪变、删光全部跟踪项时启发式失效。
- 决策：快照表加 `origin` 列（`"slug:<identifier>"`/`"tag:<identifier>"`，`PolymarketSource.fetch` 打标，`_ensure_sqlite_schema` 裸 ALTER 补列）。`load_prediction_rows` 两级判定：有 origin → 按 `tracked_markets.dismissed` 精确过滤（删除立即清图、结算/抖动不误伤）；origin NULL（旧数据）→ 断流启发式（`PREDICTION_ACTIVE_GRACE_MINUTES`，基准取表内最新快照而非墙钟）兜底，随数据滚动自然淘汰。
- 拒绝的备选：纯断流启发式（上述 4 条误伤）；按市场 slug join（event slug 拉出的子市场 slug ≠ 跟踪 identifier，对不上）；前端过滤（前端不知道跟踪状态）。
- 影响：`enabled=False`（停用未删）不清图，历史随窗口自然过期；同一市场多 origin 时任一活跃即保留；告警引擎直查表不受影响。

## 2026-06-12 - contradictory 改为关系标签（修"永不可达"）+ 单窗口 max_tokens 提到 8000

- 背景：生产真实案例（6/11 BTC 07:15-08:20 逆势 +1.17%，美伊升级新闻密集、原油反跌）——模型推理轨迹显示它给 contradictory 自行附加了"需确信新闻是价格变化的唯一因素"的因果条件，于是把整窗重大升级新闻全部降级 noise；按该逻辑任何窗口都不会有相反新闻，标签名存实亡（用户指出）。
- 决策：prompt（v4-20260612）明确 **contradictory 是关系标签非因果标签**——事件新发生 + 量级达 driver 水平 + 时间吻合 + 理论方向与价格相反，满足即标，无需因果证明；与 noise 的分界=「重要到本该同向反应却没有」vs「不重要/已定价」。被市场无视的重大消息是 price-in/仓位主导的高价值训练信号。顺带：对标说明纳指=NQ 期货（模型曾困惑美股收盘后纳指含义）；单窗口 max_tokens 4000→8000（thinking 推理偶发吃满预算把 content 截空，回放实测复现）。
- 拒绝的备选：删掉 contradictory（丢失 price-in 信号）；要求模型解释"为什么矛盾"（又变因果标签）。
- 影响：回放升级为三场景（新增"市场无视升级·BTC 逆势"），ALL PASS：升级新闻全标 contradictory、无 driver、no_news_driver conf 0.9；残留宽松度=行情快讯/回顾文章偶被一并标 contradictory（应为 noise/post_hoc），先观察。

## 2026-06-11 - 标注 v2.1 定稿：枚举收敛（4+3）+ 双档窗口/净门槛 + 缺口自愈 job

- 背景：与用户逐条讨论 v2 标签体系后定稿（spec `docs/specs/annotation-v2.md` v2.1 修订段）。关键实证：6/10 夜横跳段产出 5 个垃圾窗口而 -1.36% 慢跌只擦线碎片化触发；整夜间歇限频丢 7.4% bar、唯一连续缺口恰砸在慢跌第二段触发上（滚动回补只追 10 分钟、洞至重启才修）。
- 决策：① causal_role 四分类（driver 不分主次——主次由日级聚合计算；迟到首报标 noise；contradictory 仅限新发生事件）；reaction_type 三分类（macro_policy/event_driven/no_news_driver——机制类标签因无持仓/盘口输入不可判定，砍掉）；情绪与无法判断合并、由 confidence 表达确定性。② 加 auto_news_roles/prompt_version/eval_set 三列（人机分歧沉淀 + 版本切分 + 评估集冻结，导出 split=train/eval/all）。③ 双档窗口（15m+60m）+ 净变动门槛，阈值按近 5 天分布校准（NQ 60m 0.75%/净1.0 恰好让 6/10 慢跌通过=验收锚点）；候选前置窗 15→30/60。④ 缺口自愈 job 每小时 :37：按**回补结果**分类（源端无数据=休市静默，免交易日历维护），企业微信推完整账目（发现/补回/仍缺+原因）。
- 拒绝的备选：事件簇去重（折叠不过滤=纯装饰，砍）；CNBC 债券降频（与 Yahoo 限频无关且会废掉 10Y 对标的 10 分钟容差）；日界按美东 17:00 算夏令时（用户选北京 06:00 固定，简单优先）；窗口内保留主次标签（主观噪声）。
- 影响：v2.0 枚举行启动时自动升级（幂等映射）；实弹回放双场景 ALL PASS（事件簇全 driver、缓和→contradictory、综述→post_hoc）；日级 factor_ranking 层暂缓待讨论。

## 2026-06-11 - 标注 v2：六分类因果角色 + 窗口反应类型/置信度（训练级标签体系）

- 背景：用户基于 ChatGPT 方法论讨论（样本=时间窗口、任务=归因+噪音识别+情绪判断、负样本设计）要求重建标注页；旧二元勾选（选中/无明确触发）信息量不足以产出 SFT 训练数据。spec：`docs/specs/annotation-v2.md`。
- 决策：每条新闻人工只标 `causal_role` 六分类（primary/secondary_driver、amplifier、noise 默认、post_hoc_explanation、contradictory），窗口级 `market_reaction_type` 八分类 + `confidence` 三档——影响品种/理论方向不人工标（负担过重，留给自动生成+人审）。自动标注 prompt 同步升级输出 v2（实弹双场景 ALL PASS：缓和消息→contradictory、综述→post_hoc、事件簇→primary+secondary，质量显著优于二元勾选）。旧标注启动时自动映射迁移（selected 首条→primary、其余→secondary，no_clear→no_clear_driver）；`causal_news_ids`/`no_clear_news` 降级为派生兼容字段。导出 `GET /api/annotations/export`（JSONL，候选全量展开未标=noise 负样本；schema_version 用 confidence 判别：null=v1 低保真）。
- 拒绝的备选：全部 6 类标签人工标（每窗口标注时间 4-5 倍）；先 UI 后 prompt 分两期（一次到位省一轮回归）；新表存角色（加列+JSON 足够，免 join）。
- 影响：前端勾选框→角色下拉，no_clear 勾选→反应类型下拉+置信度档；sessionStorage 草稿 key 升 v2（旧草稿弃读）；浏览器端到端实测（标注→保存→徽章→导出）通过。

## 2026-06-10 - 对标不可用时禁用跨资产否决（修凌晨 CME 日休漏判）+ 签名表降级为路由提示

- 背景：生产真实漏判（用户报告）：06-10 05:15 BTC 窗口（-0.53%），美军打击伊朗的首报就在候选里，模型却以"reference change 无明显变化"拒选——北京时间凌晨 05:00-06:00 是 CME 日休，纳指/原油/黄金全 null，仅剩美元指数/美债10Y 这类低波动品种走平，「走平→只找标的专属新闻」条款误杀宏观归因；而此时 BTC 是全市场唯一即时反应者，恰是地缘突发最干净的样本。
- 决策：① 新增「对标不可用」条款：主力对标为 null、仅剩低波动品种走平时，跨资产签名**既不能确认也不能排除**，退回纯事件判断（量级重大的硬事件 + 分钟级时间吻合才选，防周末假阳性）；地缘例外的"签名一致"要求在此情形免除；"走平→专属"条款限定主力对标在场才适用。② 回放暴露二阶回归（模型把签名表当一票否决清单，用"原油跌≠地缘"否掉升级新闻）→ 补元规则：**签名是路由提示而非否决清单**，多签名混合矛盾时以"风险资产同向共振 + 新闻时点与下跌加速段吻合"为最强证据，单个商品方向不能否决归因（原油常被协议预期/OPEC 独立驱动）。
- 拒绝的备选：完全放开休市时段的选入标准（周末任意下跌配地缘标题会污染训练集，必须保留硬事件+时间吻合双门槛）。
- 影响：实弹回放升级为双场景回归（盘中长窗口·黄金未涨 / 凌晨日休·美军打击），两场景 ALL PASS。

## 2026-06-10 - 美元指数无数据真因：yfinance 单 ticker 组取列坏分支（全量单批量下载修复）

- 背景：换 DX-Y.NYB 并部署后概览仍无美元指数卡片，初判 Yahoo 限频。多智能体审查读 yfinance 1.3.0 包源码 + 计数实验**推翻**了「按组合并可降请求」的假设（yf.download 内部每 ticker 各发一次 chart 请求、无批量端点，分组方式不影响 HTTP 数），并定位确定性真因：yfinance ≥0.2.51 对列表输入恒返回 MultiIndex 列，旧代码 `len(ticker_list)==1` 特判把单列 DataFrame 当 Series 用——currencies 组只有美元指数一个品种，每周期都走坏分支：fetch_history 恒 0 条（回补永远补不出）、fetch 落入未收盘 fallback。
- 决策：所有资产组拍平为一次 yf.download（`_all_tickers`）；取列统一走 `_close_series_for`（isinstance DataFrame → 取 symbol 列），消除单品种组的结构性风险；docstring 写明「合并不减少 HTTP 请求」防止未来误判。
- 拒绝的备选：threads=False 摊平突发 / 无缺口时跳过滚动回补（真正能降请求的手段，留作限频复发时的后手）；锁 yfinance 版本（治标）。
- 影响：限频本身仍靠 curl_cffi 指纹会话 + 等待 Yahoo 解除；解除后美元指数由滚动回补自动补齐（坏分支修复后回补对它首次生效）。

## 2026-06-10 - 地缘签名中黄金降级为佐证（用户实证：美伊冲突金价未涨）

- 背景：prompt 把「黄金涨」写进地缘/避险签名的条件，但 2026-06 美伊冲突实测金价未涨（冲突持久化后市场钝化、强美元/流动性挤兑压制金价），用户指出与事实不符。
- 决策：核心地缘签名改为「股指跌 + 原油涨」；黄金降级为佐证、非必要条件，并明确「黄金没涨不能用来否定地缘归因」。实弹回放改用黄金 -0.30%（贴近当晚事实）重跑，模型仍正确选中 6217 做地缘归因。
- 拒绝的备选：保留黄金条件、只加例外注释（模型仍可能把它当门槛用）。
- 影响：两份 prompt 同步修改（审查程序化比对确认逐字符一致、与全 null 降级等既有条款无新矛盾）。

## 2026-06-10 - 标注页草稿写回改事件驱动（修勾选框高频抖动）

- 背景：用户录屏显示保存标注后「没有明确新闻触发」勾选框持续抖动（选新闻时新闻勾选框同样）。本地合成库 + 浏览器钩子复现实测：React 以 ~6500 次/秒交替写 checked（13.7s 内 89,184 次），主线程饱和。
- 根因：表单→缓存的镜像 useEffect 与缓存→表单的 hydrate effect，在 activeKey 切换（保存后列表刷新）的同一次提交里各自拿对方的旧快照互相覆盖，两个存储的值从此每轮渲染**互换**、永不收敛；幂等护栏只防"两边相等"，防不了"两边互换"。
- 决策：删掉 effect 式写回，改为三个用户事件处理器（勾选新闻 / no_clear / 备注）内同步 `updateDraft` 写 batchByKey；全空草稿删条目（保留"没动过=无草稿"语义，批量 pending 列表依赖它）。hydrate 保持唯一的 缓存→表单 方向，结构上无环。
- 拒绝的备选：给两个 effect 加更强指纹护栏（仍是双向 effect 镜像，时序脆弱）；useReducer 合并表单状态（改动面大）。
- 影响：修复后同口径钩子 29.4s 内 checked 写入 0 次；保存→切窗→草稿完整保留、新闻勾选持久化，均经 preview 浏览器驱动实测（`AnnotationsPage.tsx` updateDraft）。

## 2026-06-10 - 标注 prompt 实弹回放修补：首要原则例外前置 + 长窗口中段触发指引

- 背景：用 2026-06-09 美伊夜窗口 #26 的真实候选新闻重放新 prompt（`scripts/smoke_prompt_iran_replay.py`，直调 DeepSeek），第一发仍 no_clear——模型已读懂跨资产签名，但以「新闻晚于窗口起点、解释不了行情起点」拒选；且「首要原则」段的"宏观背景不选"与百行外的地缘例外构成冲突指令。
- 决策：① 首要原则处加前向引用（跨资产签名确认的重大突发不算"间接联系"）；② 新增「长窗口」段：合并事件窗口的触发新闻常在中段、驱动后半段加速，不得仅因晚于起点排除；升级/缓和并存时以与价格方向一致侧为准；同事件簇选信息量最大的 1-3 条；③ 判断步骤改为先用 reference_changes 定性再扫候选。
- 拒绝的备选：只靠签名表不改判断步骤（实测模型把它当背景略过）；放宽到"窗口内同向即可选"（假阳性风险，保留事件量级+签名相符门槛）。
- 影响：回放第二发 PASS（选中 6217+6251，未选噪音/综述）；冒烟脚本留在 scripts/ 作 prompt 回归工具，跑一次一笔 reasoner 调用费。

## 2026-06-10 - 对标清单扩到 6 资产（+美债10Y/美元指数/BTC），收益率用 bp 口径

- 背景：纳指/原油/黄金三件套能识别地缘共振，但区分不了「利率冲击」和「避险」（CPI/FOMC 日两者都是股指跌、10Y 方向相反），也看不到美元流动性通道。
- 决策：`ANNOTATION_REFERENCE_ASSETS` 扩到 6 项 = 六条独立宏观通道（风险资产/地缘供给/避险/利率/美元/加密贝塔）；config 三元组可选第三项 `"bp"`，收益率类按基点 `(end−start)×100` 计算显示（4.30→4.40 = +10.0bp 而非 +2.33%），`ReferenceChange.unit` 贯穿到前端 `fmtRef` 与 LLM payload；两份 prompt 加跨资产签名表（股指跌+10Y升=利率冲击；股指跌+10Y降+金涨=避险；美元升+BTC跌=流动性收紧）。
- 拒绝的备选：加 ES=F/YM=F（与纳指相关性 0.95+ 冗余）、白银（与黄金冗余）、US_2Y（10Y 够用）、亚指（多数窗口休市全 null）、ETH（与 BTC 冗余）；收益率沿用涨跌%口径（量纲误导模型）。
- 影响：标注 BTC 时 BTC 自动 is_self 不列；6 项是软上限，再加先想清楚代表哪条独立通道。

## 2026-06-10 - 自动标注 payload 加同期对标涨跌（纳指/原油/黄金）

- 背景：美伊交火夜纳指异动，两次自动标注都没归因到地缘新闻——旧 prompt 只看单资产价格 + 候选新闻，且要求排除"宏观背景/间接关联"，地缘突发新闻不提 symbol 就被当间接关联丢掉。
- 决策：复用 2026-06-08 的「宏观同期对标」机制，把 `reference_changes`（纳指/原油/黄金同期涨跌，self 不列）喂进单窗口 + 批量两条 payload；prompt 加跨资产解读指引（股指跌 + 油金涨 = 地缘/供给冲击签名；突发军事/制裁类新闻不因"没提 symbol"而排除，但回顾/评论类仍不选）。
- 拒绝的备选：放宽"宁可空选"总原则（会污染训练集）；只改 prompt 不给数据（模型没有跨资产事实依据，纯靠猜）。
- 影响：对标清单仍由 `config.ANNOTATION_REFERENCE_ASSETS` 单点控制；批量路径对标快照一次性捞出，不增加每窗口查询。

## 2026-06-09 - 债券收益率换 CNBC 行情 API（替代东方财富）

- 背景：东方财富是境内源，从东京服务器抓不稳 → 断点；要换海外可达、连续的源。
- 决策：CNBC 行情 API 一个批量请求覆盖 US2Y/US10Y/JP2Y/JP10Y（symbols 管道分隔），带浏览器 UA 即可全球可达、无 key、盘中实时；10Y-2Y 利差客户端相减；timestamp 留空→price_scanner 用 scan_time 落库保连续。实测真实端点四条收益率解析正确。
- 拒绝的备选：FRED / 日本财务省（用户嫌绕、且日频）；Yahoo（无美 2Y、无日债）；Stooq / investing.com（反爬）。
- 影响：`eastmoney_bond_source.py` 保留为 config 可切备用；asset_class 仍 bond，前端无改。

## 2026-06-09 - 新闻加 InvestingLive + FinancialJuice 英文即时源

- 背景：英文源只有 CNBC RSS（30-60min 才更），要"类似 jin10"的英文即时快讯。
- 决策：调研发现真正快的英文快讯就是 newswire 级 RSS，复用现有 config 驱动 RSS 机制。InvestingLive（原 ForexLive，分钟级、直连稳）做主；FinancialJuice（英文版 jin10，秒级、Cloudflare）做次 —— 给 rss_source 加 `Accept` 头 + 429 退避即稳。
- 拒绝的备选：Benzinga / Marketaux / NewsData 等免费 JSON API（额度撑不住 5min 轮询 / 禁商用 / 偏个股）；Reuters（RSS 已死）。
- 影响：language=en 自动进新闻页右栏 + 标注候选；前端无改。

## 2026-06-09 - 跟踪市场软删除（修"删不掉"）

- 背景：用户删跟踪市场后重启又回来。根因：`seed_tracked_markets` 每次启动按 config 补种，硬删除后行没了 → 被当"缺失"补回。
- 决策：软删除 —— 删除打 `dismissed` 墓碑留行；seed 的 existing 查全表，留行后不补种；list / 扫描器过滤 dismissed；重新添加同名则复活。加 `dismissed` 列 + SQLite 轻量迁移。
- 拒绝的备选：加 protected 标记禁止删除（用户就是要删）；seed 只跑一次（升级时新 config 项进不来）。
- 影响：硬删除 → 软删除；删除从此持久。

## 2026-06-09 - 预测市场 Core CPI MoM 归族聚合

- 背景：Core CPI MoM 各区间桶没聚合成一张图（每桶一市场，同族 <2 条被打散成单市场）。
- 决策：`classify_market_family` 加正则匹配真实 Polymarket 问法 "Will Core CPI MoM be X% [or less/more] in <月>?" 及月度通胀，同月所有桶归 `core_cpi_mom_<月>` / `inflation_mom_<月>`，≥2 条即成图。按真实问法（查 Gamma API）写。
- 拒绝的备选：沿用旧通胀正则（只匹配 "inflation reach more than X% in 2026"，覆盖不到月度桶）。
- 影响：CPI / 月度通胀事件现在渲染成主题概率对比图。

## 2026-06-09 - 加密只留 BTC/ETH + 新增「外汇」类（美元指数）

- 背景：市场概览加密区只留 BTC/ETH，并加美元指数。
- 决策：config crypto 收敛到 BTC/ETH；`get_latest_prices` 加密卡片过滤到配置集（删了立刻消失、不删历史数据）。美元指数单开「外汇」资产类（yfinance currency 组 + 前后端 CLASS_ORDER/classNames 同步），比塞进"商品"更准、可扩展。（2026-06-10 修正：Yahoo 已无 DX=F 期货行情（404），symbol 换 ICE 现货指数 `DX-Y.NYB`，上线后概览才真正出现美元指数卡片。）
- 影响：概览加密区只剩 BTC/ETH；新增「外汇」分组显示美元指数。

## 2026-06-08 - 移除标注窗口「峰」指标（与净变动冗余）

- 背景：峰 `peak_change_pct` 实测绝大多数 == 净变动 `change_pct`。根因：触发式窗口在"价 vs N 分钟前"跌破阈值时停止，`w_end` 天然落在动量峰顶，故 `high/low ≈ price_end` → 峰≈净。只有多段合并、冲过极值后回撤仍持续触发的长事件才分得开。
- 决策：端到端删除 `peak_change_pct`，以及仅为算峰而存在、UI 从未展示的 `low_price`/`high_price`（死字段）。待标注行回到 `时间 · 净涨跌` 清爽三列。
- 拒绝的备选：把峰的扫描窗往 `w_end` 后延 15-30min 以抓"插针回撤"（增复杂度，用户选择直接去掉）；前端仅隐藏（会留后端死字段）。
- 影响：`PriceWindowSchema` / 前端 `PriceWindow` 各少 3 字段；事件合并的 `segment_count` / `change_pct` / 合并逻辑不变。

## 2026-06-08 - 跨资产走势按窗口起点锚定净值

- 背景：「跨资产走势」图每个品种归一到「本窗口内第一个数据点」。对有交易时段缺口的资产（KOSPI/日经/A股/美股指），昨收→今开的隔夜跳空/熔断发生后，今天第一根 bar 已是跌完的开盘价，被设成 0% 基准 → 跳空被基准吞掉看不到。用户反馈韩国今早开盘熔断在图上没体现。
- 决策：`chart_utils.normalize_prices` 加可选 `base`（向后兼容）；`get_history` 取每品种 `timestamp ≤ start` 最后一笔收盘为基准（`config.MARKET_HISTORY_BASELINE_LOOKBACK_DAYS` 回看，默认 7 天覆盖周末），归一相对该基准。无前置数据回退窗口内首点。
- 拒绝的备选：昨收锚定「当日涨跌」曲线（要为各市场分别写收盘时段逻辑，24h 加密的"昨收"还需另设锚点如 UTC 00:00，复杂度高）；不改、只让用户调大窗口（治标，没解决基准吞跳空的根因）。
- 影响：`/api/market/history` 的 `normalized_pct` 语义改变（窗口起点锚定）；前端无需改。注意：跳空只在所选区间覆盖了开盘时刻时显示，看早盘熔断需把区间调到覆盖开盘。

## 2026-06-08 - 标注页「宏观同期对标」（多资产计算字段，不落库）

- 背景：标注 BTC 等价格异动时缺宏观参照，难快速区分"宏观驱动"还是"个体异动"。用户要在标注页看同时段纳指/原油/黄金涨跌。
- 决策：`PriceWindowSchema`/`AnnotationListItem` 加计算字段 `references`（列表），由 `config.ANNOTATION_REFERENCE_ASSETS`（纳指 NQ=F / 原油 CL=F / 黄金 GC=F，可增减）驱动；服务端按窗口端点最近快照算 `(end−start)/start`（容差 10min）；无数据→`pct=null`（前端「无」），标注品种本身→`is_self`（前端「本身」）。用期货而非现货指数，因期货近全天有数据、与各时段窗口重叠多。**展示位置**：待标注列表行保持清爽（只 涨跌/峰），选中窗口后在候选新闻面板上方显示「宏观同期对标」条。
- 拒绝的备选：per-row 显示全部对标（行太挤，已实测重叠 → 改详情区）；持久化到标注表 / 喂进 DeepSeek 自动标注 prompt（可从 price_snapshots 重算，YAGNI）。
- 影响：纯展示，不改库 schema、不改告警。前端选中窗口详情区 + 已标注表「宏观对标」列。

## 2026-05-17 - 接入 BMAC 远程数据源 + numpy shim

- 背景：原 ClsBinanceSymbol 用付费数据源的离线 pkl 快照算板块涨跌。用户有自己的远程服务器（`root@47.243.252.92`，跑邢不行 BMAC 数据中心），数据每 1h 更新。要把"离线批 + CLI"升级成"实时拉流"。探测发现服务器 pkl 用 numpy 2.x 写，本地 anaconda 是 numpy 1.26.4，`pickle.load` 报 `No module named 'numpy._core'`。
- 决策：(a) `services/remote_fs.py` 做 SFTP 客户端（paramiko 长连接单例 + 增量拉取 manifest + 原子写 `os.replace`）；(b) numpy shim：模块加载时把 `numpy._core` 别名到 `numpy.core`（`remote_fs.py:45`），15 行搞定，**不升级本地 numpy**（升级要连带升 pandas/scipy/pyarrow，会影响 anaconda base 环境的其它项目）；(c) 时区：服务器全 UTC tz-aware，入库 `tz_localize(None)` 转 UTC naive。
- 拒绝的备选：升级本地 numpy 到 2.x（牵连太广，base 环境共享）；建独立 venv（改动 launch 方式，暂不值得）；服务器端 introspect（服务器自己的 python3 也是 numpy 1.22，读不了）；HTTP API / DB 直连（服务器只暴露 SSH）。
- 影响：所有远程 pkl 经 `remote_fs.load_pickle` 加载，shim 全程生效。风险：BMAC 升级到只在 numpy 2 存在的新 dtype 时 shim 失效（记入 PENDING / DATAFLOW 关键字段）。

## 2026-05-17 - 板块管道用单一 remote_data_cycle job（取代守护线程）

- 背景：最初把 puller 做成独立守护线程（自旋 60s），sector_scan 作为它的 post-pull 副作用。用户指出：pull、sector_scan、（将来）因子计算都依赖 puller，应该串行在一个 job 里，再跟其它 job 并行。
- 决策：把"远程数据相关工作"打包成一个 APScheduler job `remote_data_cycle`（`run_remote_data_cycle()` `remote_puller.py:276`）：内部串行 pull -> sector_scan ->（phase 4 占位）factor。去掉守护线程（start_puller/stop_puller 及其 lifespan 钩子全删）。`RemotePuller` 退化成状态容器，`cycle()` 是 job 入口。跟 `scan_cycle`（5min）/`hourly_summary`（1h）用各自 `max_instances=1` 锁并行。
- 拒绝的备选：保留守护线程（多一套生命周期 + 锁管理）；sector_scan 单独挂 cron（与 puller 节奏脱钩，就是被这次重构修掉的 bug 根源）。
- 影响：远程数据 lifecycle 统一由 APScheduler 管，测试时直接调 `run_remote_data_cycle()` 不用 spin 线程。phase 4 因子计算只需在 `cycle()` 末尾加一段（已留占位注释）。

## 2026-05-17 - 板块榜单读 DB + post-pull 触发 scan（snapshot 一致性）

- 背景：用户发现 AI & Big Data 板块榜单 1h 显示 −0.83%，但展开成员币大多是正的。诊断：榜单读 `sector_returns` 表（上一次 :32 cron 写的，2h 前），钻取是从 pivot 现算（最新）—— 两个不同时间窗叠在一起。
- 决策：(a) 把 sector_scan 从固定 1h cron 改为**在 puller 拉到新 pivot 后同步触发**（`_pull_if_newer` 返回 bool，`market_pivot_*` 更新就调 `_run_sector_scan`）—— DB 跟 pivot 最新 bar 在 1-2s 内对齐；(b) `get_leaderboard` 读 `sector_returns` 表（单 SELECT），不现算；(c) 抽出 `compute_all_sector_returns` 作为 scanner（写 DB）和未来读侧共享的计算函数。
- 拒绝的备选：让 leaderboard 也现算（一度这么改，但 DB 表变成只写不读的死写入，且每次请求重算浪费）；token 钻取也持久化到 DB（要建逐币快照表，存储爆炸）。
- 影响：榜单与钻取在 pull 完成后绝大多数时刻 snapshot_at 一致；`sector_returns` 表保留写入，给 phase 2 板块告警 + 历史趋势用。残留窗口：pull 进行中那 5-30s 可能不一致，由小时 cron 兜底。

## 2026-05-17 - CMC API 直连绕过代理 + 重试

- 背景：`python run.py refresh-sectors` 在第二页（start=201）报 `SSLError UNEXPECTED_EOF`。根因：`config.PROXY`（Clash 127.0.0.1:7897）被套用到 CMC 请求，CMC 在国内本就可直连，走 Clash 长会话反而掉 SSL。
- 决策：CMC 请求默认直连 —— `requests.Session(trust_env=False)` + `proxies=None`，同时忽略 `config.proxies()` 和 env 里的 `HTTP_PROXY/HTTPS_PROXY`。加 3 次指数退避重试（SSLError/ConnectionError/Timeout/5xx）。opt-in `CMC_USE_PROXY=1` 给真需要走代理的网络。
- 拒绝的备选：调大 timeout（治标）；全局禁代理（其它源如 yfinance 可能需要代理）；只重试不绕代理（代理本身才是不稳定源）。
- 影响：复现用户代理环境验证 347 categories 一次过。CMC 是唯一显式 `trust_env=False` 的外部调用。

## 2026-05-17 - 板块映射 7 天 TTL 本地缓存 + 白名单 + per-dataset 轮询

- 背景：原 ClsBinanceSymbol 每次都重拉全部 CMC 分类（~350 个 × 2.5s 限速 = 很慢）。板块从属关系几天才变一次。另外 pivot 是小时级数据，但最初 puller 默认 60s 轮询，59/60 是空跑。
- 决策：(a) `cmc_symbol_categories` 表带 `updated_at`，`needs_refresh` 检查 `MAX(updated_at)` 距今 ≥ 7 天才刷；(b) 只查 `config.SECTOR_WHITELIST`（45 个精选板块，13 个中文大组），CMC 调用从 350+ 降到 ~45（~2min）；(c) `DatasetSpec.poll_interval_seconds` 做差异化轮询：pivot 1h、spot_swap_matches 1 天，`cycle()` 内用 `_next_check_at` 闸门。
- 拒绝的备选：硬编码 symbol→板块对照表（要人工维护 200+ 行）；每次扫描都刷 CMC（限速 + 浪费）；全局统一轮询节奏（5min 数据来了要快、1h 数据不该跟着快）。
- 影响：白名单需人工维护投资逻辑（体育/隐私/龙头公链等），改完跑 `refresh-sectors`。45 个白名单板块对齐 CMC 实际命名（`Tron`→`TRON Ecosystem`、`DEX`→`Decentralized Exchange (DEX) Token` 等，CMC 改名踩过坑）。

## 2026-05-17 - 只把板块/category 合并 main，因子研究留本地分支

- 背景：同一分支 `feat/remote-data-integration` 上既有板块管道（16 commit，已成熟）又有 PENDLE/ETH 单币因子页（1 commit，实验性）。因子回测发现：capture-ratio 因子在含真实熊市的 OOS 上 Sharpe ~1.0、PENDLE 跌 59% 时策略赚 71%，但还差资金费率验证才能定论；且回测中踩过两个数据 bug（跨数据空档算收益率、误以为缺历史数据其实快照里有全历史 2023-07~2026-02）。
- 决策：在 93ee085（板块工作末尾）建 `feat/sector-rotation` 分支，ff 合并到 main 并推 GitHub；因子 commit（5416637）留在本地 `feat/remote-data-integration`，不上云。
- 拒绝的备选：整分支（含因子）合 main（因子还没定论，不该进主干）；因子 commit 也推一个 wip 分支到云（用户明确"先不做因子界面"）。
- 影响：GitHub main + `feat/sector-rotation` 只有板块功能。因子工作完整保留在本地分支，要继续做 `git checkout feat/remote-data-integration`。因子页 / `services/factors.py` / `schemas/factors.py` / `/api/factors/*` 不在 main，本套地图不覆盖。

## 2026-05-05 - 英文新闻源从 Bloomberg RSS 切到 CNBC

- 背景：Bloomberg RSS（`feeds.bloomberg.com/markets/news.rss`）实测每天产出极少（数小时一条），且内容偏宏观叙事而非突发事件，对 BTC/ETH/期货的短窗口因果归因价值有限。维持中文 jin10 单源对称的设计，英文也只保留一条最有用的源。
- 决策：用 **CNBC Top News**（`https://www.cnbc.com/id/100003114/device/rss/rss.html`）替换 Bloomberg。CNBC 突发覆盖度更广（Fed / 监管 / 公司事件 / 地缘），更新频率更高，且非加密货币专项。同时把硬编码的 `["jin10", "bloomberg"]` 白名单替换为读取 `config.NEWS_SOURCES` 启用集合的 helper（`_enabled_news_sources()` 在 news_service、`_annotation_news_sources()` 在 annotation_service），以后切源 / 加源只改 config。
- 拒绝的备选：Reuters（公开 RSS feed 在 2020 年后多数失效）；WSJ / FT（headlines 可见但内容 paywall）；同时启用 CNBC + Bloomberg + Reuters 多源（用户明确"类似 jin10 只保留一个"）；专做加密的 CoinDesk / The Block（用户明确不要）。
- 影响：旧的 `news_items.source = "bloomberg"` 行保持不变（已在 DB 里），新数据从下次扫描开始打 `cnbc`。`news_service.zh_count` / `en_count` 改为按 `language` 字段统计而不是按 source 名硬匹配，避免每加一种英文源都要改一处。如 CNBC 单源 signal 不够再考虑加第二个英文源（PENDING tier B）。

## 2026-05-05 - 标注表加 3 列冻结训练样本：候选集 + LLM 推理 + LLM 摘要

- 背景：现有 `news_price_annotations.causal_news_ids` 只存"被选中的因果新闻"，未来用作 LLM 微调 / RAG 训练数据时缺两类信息：(1) 负样本——同一窗口里未被选中的候选新闻 ID（"为什么不选这些"是关键训练信号）；(2) 自动标注链路上 DeepSeek v4-pro 返回的 `reasoning_content` 全文，目前只在前端面板展示一次就丢，未来无法用来做 chain-of-thought 蒸馏；(3) `notes` 字段会被人审改写，丢失 LLM 原始 `summary`，无法对比"模型给的原稿"与"人审最终结论"。
- 决策：在 `news_price_annotations` 加 3 列：`candidate_news_ids` (TEXT JSON, 全候选 ID)、`auto_reasoning` (TEXT, LLM 推理原文)、`auto_summary` (TEXT, LLM 摘要原文)。`database._ensure_sqlite_schema()` 加 `ALTER TABLE` 兼容已有库（不动旧行）。`AnnotationCreateRequest` 加可选字段，前端保存时无论手动还是自动流程都把当前 context 窗口里的全部候选 ID 一并提交；自动标注流程额外提交 `auto_reasoning` 和 `auto_summary`。`AnnotationDetail` 把这 3 列回读出来供后续训练导出 / view 模式使用。
- 拒绝的备选：(a) 把候选新闻**全文**复制进标注行——~50KB/标注，年增量 90MB+，且与 `news_items` 重复，加 IO 写入压力；(b) 不存 reasoning，只在 UI 展示——以后想训练时已经丢了；(c) 用单独的 `annotation_training_payload` 表 join——增加查询复杂度，没明显收益。
- 影响：标注表存储年增量约 +10MB（candidate IDs ~290KB + auto_reasoning ~5-9MB），可忽略。已存在的旧标注行 3 列为 NULL，不影响读写。`news_items` 表的"被标注引用的行不可删"是隐性约束，已记入 PENDING tier B（未来实现 retention 任务时必须过滤）。

## 2026-05-04 - 标注模块：分组、撤销、上下文 15/30、DeepSeek v4-pro 自动标注

- 背景：原标注页一次只看一个窗口，看不到当前标注状态，已标注窗口需要查 DB 才知道；上下文新闻窗口对称 ±30min 与价格异动产生的因果链不太匹配（信息往往只在事件前几分钟泄漏，事件后市场有更长反应时间）；想用 LLM 减少手工选新闻的负担，但 v4-flash 不带显式 reasoning，决策依据不可见。
- 决策：(a) `PriceWindowSchema` 加 `annotation_id: int | None`，前端按这个字段把窗口列表分成"未标注 / 已标注"两组；(b) 新增 `GET /api/annotations/{id}`、`DELETE /api/annotations/{id}` 支持详情查看 + 撤销；(c) `context-news` 参数从对称 `minutes` 改为 `pre_minutes=15` / `post_minutes=30`，`upsert_annotation` 同步把 `context_start = window_start - 15min` / `context_end = window_end + 30min` 写库；(d) 新增 `POST /api/annotations/auto`，调 `deepseek-v4-pro`（`thinking.type=enabled`、`reasoning_effort=max`、240s read timeout），返回 `selected_news_ids` + `summary` + `reasoning_content`；(e) 自动标注**不写库**，前端预填 checkbox 后由人 review 再 POST `/api/annotations` 落库，`labeler` 字段标记 `"deepseek-v4-pro (auto, reviewed)"` 区分人工与人审 LLM 标。
- 拒绝的备选：直接落库 + 撤销重做（误标恢复成本高，且削弱"标注"这件事的可信度）；用 `deepseek-reasoner` 旧名（doc 已声明 deprecate，对应 v4-flash thinking 模式，没有 v4-pro 强）；上下文窗口改为可前端调（增加 UI 复杂度，没明显收益）；自动标注做成异步任务（5min 内完成，足以同步等）。
- 影响：历史已标注的行 `context_start/context_end` 仍是对称 ±30min（不回写），新写入的对称切换。`schemas/annotations.py` 增 `AnnotationDetail` / `AutoAnnotateRequest` / `AutoAnnotateResponse` / `DeleteAnnotationResponse`；`config.py` 增 `DEEPSEEK_REASONER_MODEL` / `DEEPSEEK_REASONER_READ_TIMEOUT` / `DEEPSEEK_REASONER_EFFORT` 三个 env。前端 `AnnotationsPage` 重写为左侧分组列表 + 中间窗口元信息 + 右侧编辑/查看面板（按 `annotation_id` 切换模式）。

## 2026-05-04 - Eastmoney 债券源用扫描时间替代源端 `f86`，消除数据空洞

- 背景：实测发现美债（US_10Y / US_2Y / JP_10Y / JP_2Y / *_SPREAD）在 `MarketLatestItem.change_1h` 字段上经常返回 null。查 DB 发现 18:49 BJT 与 22:20 BJT 之间的 US_10Y 没有任何 snapshot —— 跨度 3.5 小时的数据空洞。根因：Eastmoney 行情 API 的 `f86`（行情更新时间）在美债现货收盘期间会停滞数小时，配合 `(symbol, timestamp)` 唯一约束，连续 5m 扫描重复读到的相同 `f86` 全被 DB 去重跳过。`market_service._change_pct_from_latest` 的 1h tolerance 是 ±20min，落在这段空洞里就找不到 baseline 返回 None。
- 决策：在 `EastmoneyBondQuoteSource.fetch()` 内部统一用 `datetime.now(timezone.utc)` 作 scan_time 覆盖每条 `PriceRecord.timestamp`，**不再保留 Eastmoney 源端 `f86`**。这样每 5 分钟的扫描都会产生新 snapshot（价格不变时 `change_pct = 0%`），保证 1h / 24h 窗口对比始终能找到 baseline。spread record 的 `_spread_timestamp` 仍取 max，因 long/short 都是同一 scan_time，结果一致。
- 拒绝的备选：(a) 把 `_change_pct_from_latest` 的 1h tolerance 放宽到 ±60 / ±180min —— tolerance 太大让"1h 涨跌幅"语义失真，可能用 3 小时前的价格冒充 1h 基线；(b) 在 UI 给"—"加 tooltip 解释 —— 治标不治本，用户每次看到都要重新理解；(c) 在 scanner 里检测连续 N 次 `f86` 不变就强制写新 snapshot —— 比 (a) 简单的方案没必要的额外状态。
- 影响：bond DB 数据量从「源端 quote 实际更新次数」增加到「每 5 分钟一条」，6 个债券 symbol 每天 +1728 行（~50MB/年，可接受）。`change_1h` 在修复部署后约 1 小时（即 latest 与 1h 前 baseline 都落在新数据时间段时）自动恢复显示。已存在的历史 gap 不会回填（Eastmoney 不提供历史 API）。`PriceSnapshot.timestamp` 对 bond 的语义从"源端报价时间"改为"扫描时间"，DATAFLOW.md 同步更新。

## 2026-05-04 - 清理 A 级 PENDING：AlertRuleType 枚举 + `config.proxies()` + README 修订

- 背景：PENDING.md 的 A 级（低风险，任意会话都可做）三项整改一直挂着；它们都是单文件影响、无结构变动，但留着会让后续会话继续围绕它们提建议。趁正式开始市场概览前端修改前先清掉。
- 决策：(a) 在 `alerts/rules.py` 新增 `AlertRuleType(str, Enum)`，替换 `alerts/engine.py` 中六处对 `rule_type` 的字符串比较（str-Enum 让 config 字典和测试 fixture 不需要改动）；(b) 在 `config.py` 新增 `proxies()` 函数集中 `{"http": PROXY, "https": PROXY} if PROXY else {}` 模板，统一替换 `coingecko_source` / `eastmoney_bond_source` / `wechat_work` / `jin10_source` / `rss_source` 五处（共 7 个内联调用 + 5 处现在已废的 `self.proxy = config.PROXY`）；(c) 删除 `README.md` 中已不存在的 `python run.py collect` 行。
- 拒绝的备选：把 `rule_type` 字段类型改为枚举（破坏构造方式，需要改测试）；把 proxies 助手放到 `BaseSource`（`wechat_work` 不是 `BaseSource`，覆盖不全）；扩大到 polymarket（其 client 通过构造参数注入 proxy，不源自 config，破坏依赖注入）；扩大到 okx（用 ccxt 的 `httpsProxy` 字段，模式不同）。
- 影响：内部 API 调用方更短一致；`AlertRuleType` 出现拼写错误时仍能在用户写 `config.ALERT_RULES` 时悄悄通过（`_load_rules` 没强制转枚举，向后兼容优先）。`okx_source.py` 和 `polymarket/{client,source}.py` 保留各自代理写法。

## 2026-05-04 - 把项目地图全面切换为中文 + 加入 HTML 可视化

- 背景：用户工作语言是中文，英文地图阅读频率低，事实上不会被反复使用；同时纯文本地图扫起来不够直观，反向依赖、调用链等结构需要"看图"。
- 决策：所有项目地图（`ARCHITECTURE.md` / `DATAFLOW.md` / `DECISIONS.md` / `PENDING.md` / `AGENTS.md`）改成中文；新增 `ARCHITECTURE.html` 和 `DATAFLOW.html` 两份单文件 Mermaid 可视化；同步修改 `building-a-project-map` skill 增加这两条要求；维护规则收紧为"每次 commit 都要校对地图"。
- 拒绝的备选：保留英文 + 中文摘要双语；只用 Markdown 不出 HTML；HTML 用 D3 / 自定义 SVG（需要构建步骤）；只在结构性改动时同步地图。
- 影响：地图的"成本"从写入侧（一次写 + 偶尔翻译）变成阅读侧（每次都用得上）。HTML 与 Markdown 必须事实一致，HTML 只可视化、不新增解释。`.gitignore` 把两份 HTML 也加入了"本地自用"清单。

## 2026-05-04 - 把 `市场监控/` 重命名为 `onchain_data/`

- 背景：中文目录名是早期命名；读者无法从名字推断内容，`services/onchain_service.py` 不得不用 `importlib.import_module("市场监控.dune_queries")` 而不是静态 import。该目录历来只放 Dune Analytics 链上查询封装。
- 决策：`git mv "市场监控/dune_queries.py" onchain_data/dune_queries.py`；新增 `onchain_data/__init__.py`；更新 `importlib` 字符串。动态 import 调用先保留；改成静态 `from onchain_data import dune_queries` 单独走（`PENDING.md` tier B 第 6 条）。
- 拒绝的备选：保留中文名以维持文化连续性；直接命名 `onchain/`（与 `/api/onchain/eth/*` 路由前缀心智上冲突）；命名 `dune/`（如果未来加入非 Dune 链上源会显得过窄）。
- 影响：目录现在 ASCII、含义明确、是正规 Python 包。README / run.py 中的 "宏观市场监控" 是产品名而不是目录引用，保持不变。

## 2026-05-04 - 在死代码清理后重新扫描并重写项目地图

- 背景：`ARCHITECTURE.md` / `DATAFLOW.md` / `PENDING.md` 上次写于 2026-05-03，紧接着发生了 `8681515`（FastAPI / React 替换）和 `a15fb17`（清理 legacy 模块）两次提交；docs 仍引用了已不存在的文件和 tier B/C 任务（`signals/`、`data_collector.py`、`models/legacy.py`、`python run.py collect`）。
- 决策：通过 Explore subagent 重扫当前代码并基于扫描结果重写三份快照文档；今天的决策追加到 `DECISIONS.md`，不覆盖历史。
- 拒绝的备选：手改过时行；信任旧文档只补一段"自那时起"；凭记忆重写不扫描。
- 影响：快照文档反映 2026-05-04 真实代码；决策日志保持只追加的历史记录。

## 2026-05-04 - 删除 legacy 的 signals / collect / 旧 ORM 模块

- 背景：2026-04-21 的几条决策当时明确推迟删除 `signals/`、`models/legacy.py`、`python run.py collect` 路径；FastAPI / React 替换之后已无任何调用方。
- 决策：删除 `signals/`、`data_collector.py`、`models/legacy.py` 和 `collect` CLI 子命令。仅保留 `PriceSnapshot`、`NewsItem`、`NewsPriceAnnotation`、`PredictionMarket`、`AlertLog` 作为活跃 ORM 表面（commit `a15fb17`）。
- 拒绝的备选：保留模块作为 forwarding shim；归档到独立分支；只删 import 不删模块。
- 影响：tier B "wire signals" 和 tier C "处置 legacy 表" 两项作废。`README.md` 仍提到 `python run.py collect`，已过时。无运行时路径在使用这些模块。

## 2026-05-03 - 用 FastAPI + React/Vite 替换 Streamlit

- 背景：仪表盘需要现代前端，而 Python 扫描器 / 告警 / DB / Dune 封装应仍保持本地单用户。
- 决策：用 FastAPI REST 作为唯一数据边界，React/Vite/TypeScript SPA 在 `http://localhost:8000` 上托管。
- 拒绝的备选：保留 Streamlit 与 React 共存；改造为 Next.js；让 React 直接读 SQLite 或重复实现业务计算。
- 影响：删除 `app.py`、`pages/`、`streamlit`、`streamlit-autorefresh`；`api/`、`services/`、`schemas/`、`frontend/` 接管新的 UI 路径。

## 2026-05-03 - API 拥有仪表盘计算

- 背景：行情涨跌幅、预测 family 分组、新闻过滤、标注窗口都已经存在为页面级逻辑；如果照搬到 React，会出现两份漂移。
- 决策：把这些计算下沉到 Python services 层，对外暴露同时含 UTC 和北京时间字段的 Pydantic schema。
- 拒绝的备选：在 TypeScript 重新实现涨跌幅 / family / 窗口；只返回原始 DB 行。
- 影响：React 退化为渲染 + 客户端状态层；`/api/market/*`、`/api/news`、`/api/predictions/*`、`/api/annotations/*` 定义业务契约。

## 2026-05-03 - Dune API 使用 60 分钟内存缓存

- 背景：Dune 数据更新频率较低，本地使用足够；同时实时查询慢且会被限频。
- 决策：每个 Dune 数据集在内存缓存 60 分钟，允许 `force_refresh=true` 强刷。
- 拒绝的备选：每次刷页都查 Dune；现在就把 Dune 结果落 SQLite；UI 设计完成前先去掉 Dune 端点。
- 影响：链上 UI 暂时是占位页，但 `/api/onchain/eth/*` 端点已就绪，TTL 语义可预测。

## 2026-05-03 - 项目地图作为共享架构契约

- 背景：根目录的地图文件存在但已乱码不可读，而 `AGENTS.md` 要求维护这些地图。
- 决策：基于真实代码扫描，重写 `ARCHITECTURE.md`、`DATAFLOW.md`、`DECISIONS.md`、`PENDING.md` 为简洁的 UTF-8 项目地图。
- 拒绝的备选：信任之前不可读的地图；移到 `docs/` 子目录；新建一套并行文档。
- 影响：将来的结构性改动应在同一次提交里更新这些根目录地图。

## 2026-04-29 - 启动期新闻回填默认跳过 LLM 评分

- 背景：72 小时 Jin10 回填可能返回大量条目，串行 DeepSeek 批次会长时间持有 `.scan.lock`，导致后续多个正常扫描被跳过。
- 决策：`NewsScanner.backfill_missing_history()` 默认存源新闻不评分；`NEWS_BACKFILL_LLM_ENABLED=1` 显式打开历史评分。
- 拒绝的备选：所有历史新闻都评分；回填期间释放扫描锁允许并发 DB / API 写；移除新闻回填功能。
- 影响：回填更快完成，但回填行的 `llm_importance` 经常为 null。

## 2026-04-29 - 正常扫描包含滚动追平写

- 背景：长时间启动回填，或源 / API 偶发延迟，会让最近 5m 即使在当前扫描跑完后也仍有空洞。
- 决策：当前价格 / 新闻扫描完后，`run_scan_once()` 把最近 `SCAN_ROLLING_BACKFILL_INTERVALS` 个已收口窗口作为仅写库的追平。
- 拒绝的备选：只靠启动回填；把追平行也算进当前告警评估；单独跑一个追平进程。
- 影响：最近的空洞更可能被修复，同时当前告警评估仍只看实时扫描结果。

## 2026-04-28 - 预测告警使用已保存的 `prev_probability`

- 背景：`PredictionScanner` 在 `AlertEngine.evaluate_predictions()` 之前先存当前行，这样保存后再查"最新"就会看到刚保存的当前行。
- 决策：在 `PredictionMarket` 加 `prev_probability`，最新 DB 行就是刚保存的那行时，告警拿这个字段做对比。
- 拒绝的备选：在 DB save 之前评估预测告警；在 source 层再查一次历史。
- 影响：预测变化能正常告警，无需改变页面快照流。

## 2026-04-28 - 启动回填修复最近的价格 / 新闻空洞

- 背景：Streamlit 或调度器停过一段时间后，正常扫描只从下一个 5m 窗口继续，旧空洞遗留。
- 决策：`run_startup_backfill_once()` 在 app / 调度器启动后跑，回填最多 72 小时的 yfinance / OKX 价格 K 线和可见的 Jin10 / Bloomberg 新闻。
- 拒绝的备选：UI 上仅显示空洞；从最新 DB 时间戳推断空洞；让正常扫描和启动回填并发。
- 影响：重启可修复最近缺失数据，但启动期会长时间持有 `.scan.lock`。

## 2026-04-28 - 整点摘要复用市场默认 symbol 列表

- 背景：整点企业微信摘要试图汇总每一个标的时太吵。
- 决策：让市场默认与整点摘要共用 `MARKET_OVERVIEW_DEFAULT_SYMBOLS`。
- 拒绝的备选：维护独立的整点摘要 symbol 列表；从告警日志推断活跃 symbol。
- 影响：摘要保持简短，并与仪表盘默认观察列表一致。

## 2026-04-27 - 新闻标注复用价格告警窗口

- 背景：标注应解释告警触发的同一段价格异动，而不是页面级独立阈值。
- 决策：`pages/6_新闻标注.py` 读取 `config.ALERT_RULES` 中 BTC/ETH/NQ 的 `price_change` 规则，使用其窗口和阈值。
- 拒绝的备选：标注页保留独立 5m 阈值；只复用阈值数字而不复用配置窗口。
- 影响：标注样本与企业微信价格告警走同一条路径。

## 2026-04-27 - Polymarket source 拆成组件包

- 背景：Polymarket 的抓取、重试、解析、过滤各有独立职责。
- 决策：使用 `scanners/sources/polymarket/` 包，含 `client.py`、`filters.py`、`parser.py`、`source.py`；不再依赖旧的扁平 `polymarket_source.py`。
- 拒绝的备选：保留兼容性 forwarding 模块；保持所有逻辑在一个 source 文件。
- 影响：预测 source 内部更易测；`PredictionRecord` 和 DB 形状保持不变。

## 2026-04-27 - 自动扫描在 5m 边界收口后启动

- 背景：刚好在进程启动或 5m bar 收口前跑会拿到不完整窗口。
- 决策：`next_aligned_run_time()` 返回下一个自然边界 + `SCAN_START_DELAY_SECONDS`。
- 拒绝的备选：启动时立即扫描；调度器相位绑进程启动时间。
- 影响：自动扫描在 `xx:00:10`、`xx:05:10` 等时刻；手动扫描仍立即跑。

## 2026-04-27 - 价格告警使用配置好的 DB 窗口

- 背景：源 `change_pct` 通常是单个 5m bar / 实时变化，但告警需要可配置窗口比如 15m。
- 决策：`AlertEngine.evaluate_prices()` 在 `params.window_minutes` 内从 `price_snapshots` 计算变化。
- 拒绝的备选：继续用源级 `change_pct`；只对 ETH 特殊处理。
- 影响：告警文本含真实时间区间和价格区间；缺失历史基线则不告警。

## 2026-04-26 - 新闻扫描窗口对齐到已收口价格 bar

- 背景：新闻评分 / 标注应该匹配稳定的价格窗口，而不是滚动的"现在 - 5m"切片。
- 决策：`NewsScanner._filter_scan_window()` 保留前一个已收口的 5m 桶。
- 拒绝的备选：用 `scan_time - 5m` 到 `scan_time`；依赖 RSS / Jin10 列表顺序。
- 影响：新闻 DB 写入和告警与后续标注使用的价格区间一致。

## 2026-04-26 - 主新闻路径不做语义去重

- 背景：LLM 语义去重增加延迟，且会隐藏标注需要的候选新闻。
- 决策：保留精确 `(source, source_id)` 去重，但评分前不做语义去重。
- 拒绝的备选：保留 LLM 事件去重；加跨源 title-hash 去重。
- 影响：会出现一些跨源近似重复，但候选覆盖率更好。

## 2026-04-25 - DeepSeek V4 Flash 给新闻评分

- 背景：新闻需要短期价格影响打分，但成本高 / 慢的模型对 5m 循环来说太重。
- 决策：默认用 `deepseek-v4-flash` 通过 `NewsScorer`，batch size、timeout、retry 可配。
- 拒绝的备选：继续用更贵的 Pro 模型；维护多套评分配置。
- 影响：评分倾向速度和稳定性；缺 API key 会让 LLM 字段为空。

## 2026-04-25 - Jin10 important 也触发新闻告警

- 背景：Jin10 源侧 `important` 标志不是 LLM 分数但仍代表用户想推送的条目。
- 决策：新闻告警在 `llm_importance >= min_importance` 或 `source == "jin10" and importance == 1` 时触发。
- 拒绝的备选：仅按 LLM 分数告警；把 Jin10 important 映射为虚拟 LLM 分数。
- 影响：Jin10 重要条目即使 LLM 分缺失或低也能推送。

## 2026-04-25 - 新闻源运行时是 Jin10 + Bloomberg

- 背景：更多 feed 在标注阶段会增加噪音和评分成本。
- 决策：`config.NEWS_SOURCES` 仅启用 Jin10 和 Bloomberg RSS。
- 拒绝的备选：拉取所有配置的 feed 仅在 UI 过滤；活跃扫描路径保留禁用源。
- 影响：除非改配置，新写入的 `news_items` 仅来自 Jin10 / Bloomberg。

## 2026-04-23 - Eastmoney 行情替换 FRED 作为实时债券收益率源

- 背景：FRED 较慢，且对盘中美 / 日收益率监控用处不大。
- 决策：用 Eastmoney 结构化行情 API 取美 / 日 2Y / 10Y 收益率并算 10Y-2Y 利差。
- 拒绝的备选：解析 Eastmoney 新闻标题；保留 FRED 作为主显示源。
- 影响：债券 `PriceSnapshot.source` 是 `eastmoney_bond_quote`；FRED adapter 仍在但当前扫描路径未启用。

## 2026-04-23 - Streamlit 后台调度器发整点摘要

- 背景：只开 Streamlit 的用户也应能收到整点企业微信摘要。
- 决策：`app.py` 在缓存的后台调度器里同时启动扫描和整点摘要任务。
- 拒绝的备选：要求单独跑 `python run.py schedule` 进程。
- 影响：开一个仪表盘就能扫 + 摘要；`.scan.lock` 防止跨进程重复扫描。（注：本决策后被 2026-05-03 FastAPI 替换决策推翻；调度迁移到 FastAPI lifespan / 独立 schedule 进程。）

## 2026-04-23 - Jin10 请求用北京时间

- 背景：Jin10 把 `max_time` 解释为北京时间；传 UTC 会拉错时间片。
- 决策：请求游标转北京本地时间，返回时间戳在落库前转回 UTC naive。
- 拒绝的备选：仅在 UI 加 8 小时；省略 `max_time`。
- 影响：Jin10 抓取 / 回填命中预期的当前北京时间新闻窗口。

## 2026-04-23 - 扫描入口使用跨进程 `.scan.lock`

- 背景：Streamlit 后台任务、手动侧栏扫描、独立调度器可能重叠。
- 决策：扫描和回填入口外面包一个根目录 `.scan.lock`，含 PID 和僵尸锁清理。
- 拒绝的备选：仅靠 APScheduler `max_instances`；仅靠 DB 唯一约束。
- 影响：跨进程同时只有一条扫描 / 回填路径在跑。

## 2026-04-23 - 加密货币主源是 OKX，运行时移除 Binance

- 背景：Binance 全球端点经常因 US 出口 IP 受限错误而失败。
- 决策：用 OKX 5m 原始 K 线作为加密货币主路径；只在 symbol 缺失时兜底用 CoinGecko 实时。
- 拒绝的备选：保留 Binance 优先；每次 OKX 跑前调用 `load_markets()`。
- 影响：加密货币 source 通常是 `okx_swap_5m` 或 `okx_spot_5m`；CoinGecko 时间戳是采集时间。

## 2026-04-22 - 价格采集使用已收口 5m K 线时间戳

- 背景：用户面向的扫描应代表刚收口的 5m bar，而不是当前现货 tick。
- 决策：`PriceRecord.timestamp` 优先存收口 bar end time；source 没有时间戳的才回退到扫描时间。
- 拒绝的备选：加独立 `price_timestamp` 列；所有价格当作当前现货。
- 影响：`(symbol, timestamp)` 去重和窗口计算依赖 source 时间戳语义。

## 2026-04-21 - 用 SQLite WAL 而非 PostgreSQL

- 背景：单机单用户应用，没有多写者部署需求。
- 决策：用 SQLite + WAL 模式，DB 文件放项目根目录。
- 拒绝的备选：PostgreSQL 部署。
- 影响：本地启动简单，仪表盘 + 扫描器的读写并发可接受。

## 2026-04-21 - 保留 legacy 表以保持兼容

- 背景：旧表已被统一 snapshots 替代但本地可能还有历史数据。
- 决策：保留 `models/legacy.py` 和 legacy `collect` 命令，但不再为新扫描器扩展。
- 拒绝的备选：立即迁移并删除旧表。
- 影响：新运行时应写 `PriceSnapshot`、`NewsItem`、`PredictionMarket`；legacy 是独立路径。（注：本决策后被 2026-05-04 死代码清理推翻。）

## 2026-04-21 - Signals 框架在出现具体 signal 之前不接入

- 背景：`signals/` 定义了契约，但目前没有具体 signal 提供运行时价值。
- 决策：保持框架隔离，不从 `run_scan_once()` 调用。
- 拒绝的备选：把空的 registry 接入每次扫描。
- 影响：无运行时开销；将来 signal 工作需要明确的接入决策。（注：本决策后被 2026-05-04 死代码清理推翻；`signals/` 目录已删除。）
