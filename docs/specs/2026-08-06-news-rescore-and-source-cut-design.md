# 新闻评分补扫 + investinglive 下线 设计

日期:2026-08-06。来源:用户发现缓冲区大量"未评分"新闻,排查后拍板三件事:补评分扫描治本、investinglive 源下线 + 评分调用加 token 预算、一次性回补并把流程写进 `.claude/commands/backfill-yf.md`。

## 0. 排查结论(线上库 + 日志实证)

全库 76,926 条新闻里 11,058 条(14.4%)无 `llm_importance`;近几日每天新增 190-220 条。成因四路:

1. **滚动回补侧门(最大稳定来源)**:每轮扫描末尾的 `_run_news_rolling_backfill`(services/scan_runtime.py)把最近 2 个已收盘 5 分钟区间的漏网新闻补进库,**写死 `score_records=False`**。晚到的新闻(investinglive 这类分钟级源是常客)全从这条路进来,天生无分。`backfill_range` 内部先查重再打分(scanners/news_scanner.py 第 181-192 行),所以给这条路开打分只会给**净新增**的少量条目付费,无重复调用。
2. **整批评分失败**:每天约 10 批 × 12 条。日志两类报错:DeepSeek 把预算花在 reasoning 上返回空正文;JSON 截断/畸形(`max_tokens=1800` 对 12 条+中文理由偏紧)。
3. **批内漏单(静默)**:调用成功但回复缺个别 idx,代码静默置 None,无日志。
4. **停机启动回补**:`NEWS_BACKFILL_LLM_ENABLED=False` 有意不打分(防几千条串行调用堵扫描),量小但存在。

investinglive 30 天未评分率 52%(压倒性来自路 1),且为英文长文/综述源,信息价值低——用户拍板整源下线。

## 1. 改动清单

### 1.1 补评分扫描(治本,盖住全部四路)

- 新服务 `services/news_rescore.py`:`rescore_unscored(session, limit, window_hours, max_attempts, now=None) -> dict`。
  - 选取:`llm_importance IS NULL AND created_at >= now - window_hours AND COALESCE(rescore_attempts,0) < max_attempts`,**created_at 倒序**(先补最新,老积压在空闲轮消化),取 limit 条。
  - 执行:由行构造 `NewsRecord`(source/source_id/title/content/importance/language/published_at=timestamp),调 `NewsScorer().enrich_batch`;成功者回写 `llm_importance / llm_importance_reason / llm_model / llm_scored_at`;**所有被选中行 `rescore_attempts` +1**(防毒条目无限重试);返回 `{selected, scored}`。
  - 不看黑名单、不分来源(评分是全量属性;已下线源的存量行也补,便宜且缓冲区立刻干净)。
- 接线 `services/scan_runtime.py`:`_tag_new_news()` 之后、`_link_new_news()` 之前新增 `_rescore_unscored_news()`(补上的分数当轮就能参与挂接闸门)。守卫与打标同款:无 `DEEPSEEK_API_KEY` 或 `NEWS_RESCORE_ENABLED=0` 静默跳过;异常自吞;独立 session;有动作才打日志。
- 新列 `news_items.rescore_attempts INTEGER`(NULL≈0):models/news.py + database.py `_ensure_sqlite_schema` 的 news_items 补列 dict(现有轻量迁移机制,部署自动生效)。
- config.py 新增(默认值即生效值):`NEWS_RESCORE_ENABLED=1` / `NEWS_RESCORE_WINDOW_HOURS=72` / `NEWS_RESCORE_LIMIT=24`(每轮 ≤2 批) / `NEWS_RESCORE_MAX_ATTEMPTS=3`。
- 滚动回补侧门直接关掉:`_run_news_rolling_backfill` 的调用改 `score_records=True`(见 §0.1,查重在打分前,只为净新增付费)。启动回补(72h 大批量)维持不打分,交给补扫慢慢消化。

### 1.2 评分器加固(scanners/scorer.py)

- `max_tokens` 1800 → **2600**(评分调用处;用户拍板"加 token 预算")。
- 批内漏单补日志:响应解析后 `len(by_idx) < len(batch)` 时 WARNING 记缺了几条(缺的仍置 None,交给补扫)。
- **批大小不动**(`DEEPSEEK_BATCH_SIZE=12` 与挂接共用,未获授权不改——preserve-calibrated-config)。

### 1.3 investinglive 下线(config.py)

- `NEWS_SOURCES["investinglive"]["enabled"] = False` + 注释(2026-08-06 下线:未评分重灾区 52% + 信息价值低,用户拍板;条目保留可随时重开)。`create_rss_sources` 会自动跳过 disabled 源,零代码删除。
- 存量 investinglive 行原地保留(新闻列表/标注/缓冲区照常显示,评分由补扫补齐)。
- 更新 `tests/test_rss_source.py`:断言 investinglive **不在**注册源里(记录下线事实),financialjuice 仍在。

### 1.4 一次性回补近 14 天 + 运维手册

- 新脚本 `scripts/rescore_news.py`(服务器上跑,复用 §1.1 服务函数循环调用):`--days 14`(窗口)/ `--dry-run`(默认,只打印按日×来源的未评分分布与预计调用数)/ `--execute` / `--limit-per-round 48`。幂等:只补 NULL,重复运行无害;attempts 上限同样生效。
- `.claude/commands/backfill-yf.md` 追加"新闻评分回补"手册节:沿用该手册的环境铁律(ssh 别名/服务器 venv python/heredoc),步骤 = 备份(VACUUM INTO,不可跳过)→ dry-run 核数 → execute → 验收(未评分占比复查 + 抽样看理由字段)。frontmatter description 扩为"回补工具箱(yfinance 价格 / 新闻评分)"。

## 2. 不做

- 不改挂接闸门语义(未评分照旧放行——补扫会让这类越来越少,语义不动)。
- 不回补 14 天以前的 1.1 万条历史(缓冲区窗口最多 30 天,标注页按需再说;要补时跑同一脚本调大 --days 即可)。
- 不给启动回补开打分(几千条串行会堵启动,补扫已兜底)。
- 不动 DEEPSEEK_BATCH_SIZE、闸门线、黑名单。

## 3. 测试(先写后实现)

- `tests/test_news_rescore.py`(新,fake scorer 注入):补分回写四件套字段;attempts 递增;达 max_attempts 不再选;窗口外不选;created_at 倒序 + limit;已评分行不动;失败(importance=None)只加 attempts 不写分。
- `tests/test_scorer.py` 增:响应缺 idx → 对应位 None 且其余正常(漏单路径显式覆盖)。
- `tests/test_rss_source.py` 改:investinglive 不再注册。
- 全量 pytest 绿(本地 `D:\anaconda\python.exe -m pytest`)。

## 4. 上线与验收

1. 部署走 deploy.sh 标准流程(自动 VACUUM INTO 备份 + 重启;迁移自动补列)。
2. 部署后看两轮扫描日志:`[NewsRescore]` 出现且无异常;滚动回补日志出现打分字样。
3. 跑一次性回补(dry-run → execute),之后复查:近 14 天未评分占比应从 ~14% 降到 <1%(毒条目残留);investinglive 不再产新行。
4. 缓冲区页面肉眼验收:"未评分"从成片变零星。
