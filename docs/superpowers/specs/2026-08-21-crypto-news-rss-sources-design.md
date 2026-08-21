# 加密新闻源重组(BlockBeats 断供替代)设计稿

版本:v1.1(2026-08-21,与用户确认方案 B 后落盘;实施中修订 §3 前端条)
状态:已实施(2026-08-21),实施计划 docs/superpowers/plans/2026-08-21-crypto-news-rss-sources.md
上游:docs/superpowers/specs/2026-08-09-web3-news-crypto-event-pool-design.md §1(二期 A 数据接入层)

---

## 0. 背景:主力源断供

BlockBeats Pro API 的 credit 耗尽,加密线主力快讯源(约 70-150 条/天,全量中文档)
停摆;加密事件挂接只剩币安公告(上新/下架)一路窄口径,二期 B 归因的原料基本断供。

2026-08-21 从 mmon(东京)服务器实探候选源,结论:**当年二期 A 设计里钦定的
"第二独立快讯源" PANews,真实 RSS 路径这次探明了**(当年猜测路径 404 未接成),
且量级与 BlockBeats 相当。据此用户在三方案(A=纯中文替代 / B=A+英文源 /
C=BlockBeats 续费)中拍板 **方案 B:PANews + 吴说 + CoinDesk,全走现有 RSS
采集器;BlockBeats 停用不删**。

## 1. 源清单变化(2026-08-21 服务器实探结论)

| 源 | 动作 | 实探结论 |
|---|---|---|
| PANews | **新增**,主力中文快讯 | ✅ `https://www.panewslab.com/rss.xml?lang=zh&type=NEWS`,100 条/次 ≈ 148 条/天;体裁与 BlockBeats 同款(链上监测/巨鲸/监管/分析师);pubDate 为标准 GMT,无北京时间坑 |
| 吴说区块链 | **新增**,第二中文源 | ✅ `https://www.wublock123.com/feed`,Atom 格式(feedparser 原生支持),50 条/次 ≈ 69 条/天,快讯+深度混合,全部带摘要 |
| CoinDesk | **新增**,英文视角 | ✅ `https://www.coindesk.com/arc/outboundfeeds/rss`(308 重定向后的终点 URL,直接配终点),25 条/次 ≈ 28 条/天;机构/监管/ETF 类英文首发。选它不选 Cointelegraph(≈19 条/天):后者擅长的小币/散户向内容与中文快讯高度重合 |
| BlockBeats | **停用不删**:`enabled: False` + 注释注明 credit 耗尽日期 | 代码/配置/环境变量全保留,续费后改回 True 即恢复(同 gap-fill 退役模式) |
| 币安公告 | 不动 | 继续做上新/下架的官方口径 |

落选记录(免得将来重复踩):金色财经域名 DNS 已无解析(站点消失);The Block 被
Cloudflare 反爬拦(403);Odaily/Foresight 接口路径未探明,留作备选;
CryptoCompare/CryptoPanic 需注册 key,与本次断供同款 credit 依赖,不引入。

日供给量:148+69+28 ≈ **245 条/天**,回到并略超 BlockBeats 时代,仍在加密线设计
容量(200-300 条/天)内。PANews 与吴说互报同一事件**不去重**——二期 A 设计本意
(声量信号)。

## 2. 工程改动(刻意最小化)

- **rss_source.py**:`RSSSource.__init__` 加 `market: str = "macro"` 参数,传入
  `NewsRecord.market`。宏观线不传参、行为零变化;加密源传 `"crypto"`,落库自动走
  加密打标与加密事件池。
- **news_scanner.py**:加密源注册段加通用循环——`CRYPTO_NEWS_SOURCES` 里标
  `type: "rss"` 且 enabled 的条目自动构造 `RSSSource(market="crypto")`;
  BlockBeats/币安公告两个专用采集器维持原样(仍按各自 key 显式注册)。
- **config.py**:`CRYPTO_NEWS_SOURCES` 加 panews/wublock/coindesk 三条
  (`type: "rss"` + url/name/language),blockbeats 置 `enabled: False`。

## 3. 不改什么(设计红利清单)

- **打标/挂接/事件池零改动**:只认 `market="crypto"`,不认源名(二期 A "物理隔离"
  的红利)。DeepSeek 打标中英文都吃(币安公告本就是英文)。
- **前端零改动**:来源筛选下拉走 `/crypto/news/sources`,从配置动态取 enabled 源,
  BlockBeats 停用后从下拉消失。**实施修订(v1.1)**:原稿以为"历史快讯在全部视图
  照常可见"天然成立,实施时发现 get_crypto_news 默认视图按启用源过滤,停用源历史
  会连带消失;已去掉该过滤(market=crypto 本就圈死范围),显式选源路径不变,
  并加回归测试钉住"停采≠灭史"。
- **宏观线零接触**:`NEWS_SOURCES` 字典一字不动,标注池白名单不受影响。

## 4. 风险与兜底

- 三个新源均为官方免费订阅流,无 key、无 credit,但**无服务承诺**——与币安公告
  同级风险。单源失败由 NewsScanner 记源错误,不影响其他源(现有机制)。
- 首次上线靠现有启动回补机制捞订阅流里过去 72 小时内的存量;**不做更早历史回填**
  (与二期 A 接 BlockBeats 时同一决策:存量未评分,补了徒增混乱)。
- 吴说为快讯+深度混合流,不做源端过滤——"非币圈事务"语义闸(二期 A §2)本来就
  管这件事。

## 5. 测试与验收

- 测试沿用加密源既有套路:配置生效(test_crypto_config 系)、market 路由
  (RSSSource 传 crypto 落到 crypto)、Atom 解析(吴说样本)。
- 部署后验收:三源在扫描日志各自出现且非零返回;加密快讯页下拉出现三个新源;
  新入库新闻 market='crypto' 且进入打标队列;BlockBeats 错误刷屏消失。
