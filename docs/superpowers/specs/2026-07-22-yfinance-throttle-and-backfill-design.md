# yfinance 限流治本 + 缺口回补 设计

日期：2026-07-22 ｜ 状态：已获用户认可（对话中分节确认）

## 1. 背景与事实

- 2026-07-21 22:05 UTC（北京 07-22 06:05）起，yfinance 源 16 品种全停：Yahoo 边缘层对服务器
  IP 全面限流封锁（探测：全新 curl_cffi Chrome 指纹会话请求 chart 接口 → 429 "Edge: Too Many
  Requests"，无 Retry-After）。
- 非突发：journald 显示至少自 07-14 起每天 130~300 条 `YFRateLimitError`（部分品种间歇失败），
  07-22 升级为全灭。非代码回归（无相关改动、无重启相关性、指纹会话从未告警）。
- 请求量账（封禁诱因）：16 ticker × 288 轮/天 ≈ **4,600 次/天**，`threads=True` 每轮 16 并发
  突发，24h 不停含休市时段。
- okx_swap_5m、cnbc_bond_quote 及新闻/预测/板块/告警管道全程正常。
- 本机住宅网络通道已验证：**显式 start/end 调用姿势下全部 16 品种可拉取**
  （`period="1d"` 姿势对期货返回 0~1 根，勿用）。本机 yfinance 1.3.0，服务器 0.2.66。

## 2. 目标 / 非目标

**目标**
1. 回补 07-21 22:05 UTC 起的缺口，恢复 16 品种数据完整性（不等解封）。
2. 把服务器对 Yahoo 的请求量与请求形态降到不再触发封禁：预计 ~4,600 → ~2,000 次/天，
   消灭并发突发。
3. 美国夏令时切换自动正确；亚洲市场无夏令时不受影响。

**非目标**
- 不换数据源、不加代理层、不改 5 分钟采集节奏、不动 okx/cnbc 源。
- 不建模交易所节假日（取舍见 §4.1）。
- 不重启用 okx_gapfill（阶段 1 已退役；不能回填历史，仅覆盖 3 品种）。

## 3. 工作流一：缺口回补（先行）

两个一次性脚本，不改服务代码、不重启服务。今后限流复发时同一对脚本即应急预案。

### 3.1 本机拉取 `scripts/backfill_yfinance_local.py`（Windows，D:\anaconda\python.exe）

- 参数 `--start/--end`（UTC，缺省 `2026-07-21 20:00` → 当前时刻；起点提前于缺口留重叠余量，
  幂等导入下重叠无害）。
- 全部 16 品种**串行**拉取，间隔 ~1s；调用姿势固定为显式 `start/end`（tz-aware UTC）+
  `interval="5m"`。单品种失败不中断其余，末尾汇总报告。
- 导出 CSV：`symbol, timestamp_utc(bar_end, naive UTC ISO), close, volume, asset_class, name`。
  asset_class/name 取自本仓库 `config.PRICE_SOURCES`（与线上同一套定义）。
  bar_end 语义与生产一致：K 线开始时刻 + 5min，只留已收盘 bar（对齐
  `yfinance_source._iter_closed_bars`）。

### 3.2 服务器导入 `scripts/import_price_dump.py`（服务器 venv python）

- **写库前置动作：线上库快照备份**（现成 `sqlite3.Connection.backup()` 在线备份流程，
  备份文件服务器 `/tmp` 与本地各留一份）。这是对生产库写操作的后悔药。
- 导入**复用 `PriceScanner._save_records`**（实例化 PriceScanner 调用，不复制写逻辑）：
  幂等跳重（撞 (symbol, timestamp) 唯一索引即跳过）、prev_price/change_pct 链条自动衔接、
  真实覆盖 gapfill 合成点的既有语义原样生效。
- 行级校验：symbol ∈ 16 品种白名单、price > 0、时间戳在 `--start/--end` 内、NaN 丢弃。
- `--dry-run` 先行：只统计"将写入 N 条/品种"，不写库；用户确认数字后真跑。
- SQLite 并发：WAL 模式 + busy_timeout，导入与服务的 5 分钟写入错峰共存，风险低。

### 3.3 验收

- 每品种 `MAX(timestamp)` 恢复到近端；缺口时段内条数与本机 CSV 一致。
- 抽 2~3 根 K 线对比 CSV 原始值与库内值。
- 解封/治本部署后**再跑一遍近端窗口**收尾（幂等，重复无害）。

## 4. 工作流二：治本改造（随后）

### 4.1 交易时段表 `scanners/market_sessions.py`（新模块）

会话规则一律用**交易所本地时区**定义，经 Python `zoneinfo`（IANA 时区库）换算 UTC：
夏令时切换由时区库自动处理，**代码中不出现硬编码 UTC 小时数**。

| 品种组 | 时区 | 会话规则（当地时间） |
|---|---|---|
| 美股现指 ^DJI ^IXIC ^GSPC | America/New_York | 周一至五 09:30–16:00 |
| CME 期货 ES=F NQ=F YM=F NIY=F + 商品 GC=F SI=F CL=F | America/Chicago | 周日 17:00 → 周五 16:00，每日 16:00–17:00 维护段跳过 |
| 美元指数 DX-Y.NYB（ICE） | America/New_York | 周日 18:00 → 周五 17:00，每日 17:00–18:00 跳过 |
| 日经 ^N225 | Asia/Tokyo | 周一至五 09:00–11:30、12:30–15:30（午休跳过） |
| KOSPI ^KS11 | Asia/Seoul | 周一至五 09:00–15:30 |
| A股 000001.SS 399001.SZ 399006.SZ | Asia/Shanghai | 周一至五 09:30–11:30、13:00–15:00（午休跳过） |

- 每段收盘后延展 **+10 分钟尾巴**（确保最后一根已收盘 bar 落袋）；开盘不提前。
- **节假日不建模**（取舍）：休市日请求返回空数据不报错，一年 ≈10 假日的浪费可忽略，
  换规则表零维护。
- API：`active_symbols(now_utc) -> set[str]`（及内部 `is_active(symbol, now_utc)`）。
- 依赖：`tzdata` 加入 requirements（Windows 本地开发必需；Linux 无害）。

### 4.2 请求行为（`yfinance_source.py` + `price_scanner.py`）

- `fetch_history` 只请求**当前活跃品种**；活跃集为空 → 直接返回 []，零 HTTP。
- 废弃 `yf.download(16 tickers, threads=True)` 单次并发突发 → **逐品种串行**下载
  （每次仍传单元素列表，保持 MultiIndex 列形态兼容 `_close_series_for`），
  品种间**随机抖动 0.3~0.8s**；单品种失败不中断其余。
- **5 分钟周期硬约束**（用户提出）：三道保险——
  1. 单请求超时 10s（curl 会话级，防挂死累积）；
  2. yfinance 阶段**软预算 180s**：超预算即放弃本轮剩余品种并记日志，
     下一轮游标窗口自动拉长补回（机制自愈，无需人工）；
  3. 活跃品种峰值实测 ≈13（美股现指与亚洲现指永不同时开市），
     典型耗时 20~35s，最坏（13 × 10s 超时 + 抖动）≈150s < 300s。
- 游标窗口改为**按本轮活跃品种**计算（`_latest_by_symbol` 限定活跃集）：
  市场重开时该品种游标落后 → 窗口自动拉长覆盖休市断档，`sync_window_start` 公式不变，
  CAP=168h 语义不变。
- 观测语义：全休市轮次源状态记为 `closed`（区别于 0 行异常），
  避免 `[ScanSource] price returned 0 rows` 在周末刷屏成为狼来了。
- `health_check`（fast_info 单请求）保持不动，频次可忽略。

### 4.3 请求量估算（周）

- 美股现指 3 × ~390 轮 ≈ 1,170；期货+美元指数 8 × ~1,380 轮 ≈ 11,040（周末全免）；
  亚洲 5 × ~360 轮 ≈ 1,800。合计 ≈ **14,000/周 vs 现状 32,256/周，降约 57%**，且无突发。

## 5. 测试策略

- 会话表单元测试：**夏令时边界日**（2026-03-08 美国春季拨快、2026-11-01 秋季拨慢，
  切换前后各取样本时刻断言活跃集）、周末边界（周五收盘后/周日重开前后）、CME 维护段、
  午休段、+10min 尾巴。
- fetch 层：活跃过滤、串行与抖动（mock sleep）、单品种失败隔离、软预算截断。
- 扫描层：窗口按活跃子集计算的游标语义；现有游标同步/save_records 测试全量回归。
- 回补脚本：CSV 往返（写→读→构造 PriceRecord）、行级校验、dry-run 不落库、
  幂等重复导入零新增。

## 6. 实施顺序与部署

1. 回补脚本 → 本机拉取 → 备份 → dry-run → 用户确认 → 导入 → 验收（不动服务）。
2. 治本改造 → 全量测试 → push → 服务器 pull → `systemctl restart market-monitor` →
   journalctl 观察 2~3 轮（活跃过滤生效、无 429 之外的新报错）。解封前部署有益无害。
3. 解封判定探测：服务器 venv 内 curl_cffi Chrome 会话请求
   `query1.finance.yahoo.com/v8/finance/chart/ES=F` 看状态码（429 → 仍封锁）。

## 7. 已否决方案

| 方案 | 否决理由 |
|---|---|
| 仅换 EIP | 请求形态不变必复发；可作为解封过慢时的辅助手段，不入本设计 |
| 重启用 okx_gapfill | 只覆盖 NQ/GC/CL、合成价口径、且只能填"当下"槽位，无法回填历史 |
| 降频 5min→15min | 行为引擎参照资产需要 5m 新鲜度，产品面退化；会话过滤已达同级降量 |

## 8. 验收标准

- 回补：16 品种缺口时段数据齐全，验收查询与 CSV 对数通过。
- 治本：测试全绿；上线后 journalctl 连续 24h 无 `YFRateLimitError` 复发
  （解封后观察）；休市时段日志显示 `closed` 而非 0 行告警；
  每日请求量按 §4.3 口径下降 ≥50%。
