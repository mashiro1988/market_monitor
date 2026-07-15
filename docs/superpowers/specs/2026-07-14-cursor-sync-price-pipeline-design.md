# 价格管道"游标同步"重构 设计（v2 定稿）

> 一条路径取代多层补 bar 机制（滚动追平 / 每小时 gap_repair / 启动回补 / 手动回补）：每轮扫描按**单次窗口**
> 拉历史 K 线并幂等入库——采集与回补合并为同一操作，无任何嵌套回看机制。
> gap_repair 价格部分**整体退役**（不留监控 job）；验证 = 部署 3-7 天后出线上只读报告。
> 创建：2026-07-14；v2：用户砍掉小时监控、两源统一窗口公式、yfinance 弃固定 7d（2026-07-14 拍板）。

## 1. 目标与非目标

**目标**
1. 价格数据写入路径收敛为一条：每轮一个窗口、一次拉取、一次幂等写入。退役滚动追平回填（价格部分）、
   启动价格回补、gap_repair（含修复与监控，价格部分整体消失）。
2. 验证不靠常驻 job：scan 周期日志记录各源插入计数（过程 log）；部署 3-7 天后用线上只读查询出
   缺口报告，人工判定新路径是否 robust。偶发缺失接受；极端情况走一次性专项补（手动，无常驻机制）。

**非目标**
- 不换数据源（yfinance/OKX/CNBC 维持现状；Massive Futures 接入是独立后续案）。
- okx_gapfill 休市合成点不动（展示功能，非补真数据）。
- 新闻/预测管道不动（滚动回填的**新闻部分保留**；小时 job 的新闻打标两步保留）。
- 行为引擎、告警评估、调度周期不动。

## 2. 病根（为什么现在有四层）

`yfinance.fetch()`（[yfinance_source.py:159](../../../scanners/sources/yfinance_source.py)）每 5min 用
`yf.download(period="7d", interval="5m")` 把**全品种 7 天的 5m K 线**拿在手里，然后每品种只留最后一根、
其余全部丢弃。`okx.fetch()` 同理（`limit=5` 取最后一根收盘）。于是"迟到/漏掉的 bar"需要三个额外机制
把刚被丢弃的数据再取回来：滚动追平（10min）、每小时 gap_repair（24h）、启动回补（72h）。
四层交互的复杂度正是"48h 段重算"issue 的根源（2026-07-12 决策背景）。

生产数据佐证（2026-07-13 线上只读查询）：BTC 自 06-06 上线以来 10,704 根 bar 零缺口零补写；
补写活动全部在 yfinance 品种（每品种每天约 4-6 根晚到 bar，均由 gap_repair 在 1h 内修复，
即晚到都在 24h 之内）。本案把这份 24h 自愈能力从旁路机制搬进主路径。

**请求量账本（限频视角）**：Yahoo 限频按**请求次数与 TLS 指纹/IP** 计，与单次返回的数据量无关——
无论窗口多宽都是每品种一次 chart 请求（yf.download 无批量端点，见 `_all_tickers` docstring）。
现状每个 5min 周期实际下载**两遍**全品种（`fetch()` 一遍 + 滚动追平 `fetch_history` 又一遍），
加上每小时 gap_repair 再一遍：约 16 品种 × (2×288 + 24) ≈ **9,600 请求/天**。
新路径每周期一遍、无小时 job：16 × 288 ≈ **4,600 请求/天，减半**——本案本身就是对
"yfinance 间歇限频"的最有效缓解。若仍偶发 429：`yf.download(threads=False)` 串行化消突发
（指纹会话 curl_cffi 已在用）；最终手段 = 付费源（另案）。

## 3. 设计

### 3.1 同步路径（唯一写路径，两源同一条公式）

每轮 `scan()` 对每个源计算**一个窗口**，拉一次，写一次：

```
cursor = 该源各品种"库内最新 bar 时刻"的最小值（最落后品种；库空品种视为 now − CAP）
start  = max(now − CAP, min(cursor − 30min, now − 24h))
拉取    = source.fetch_history(start, now)
写入    = _save_records（(symbol,timestamp) 唯一键已存在即跳过；真实覆盖合成点逻辑不变）
```

三种形态（同一条公式，无分支机制）：
- **平时**（cursor ≈ now−5min）：`min()` 取 now−24h → **固定回看 24h**。原 gap_repair 的 24h 覆盖
  搬进主路径：晚到 ≤24h 的 bar，源哪一轮给、下一轮就进库（比原来的小时级还快）。
- **停机 >24h**：cursor 落后 → 窗口自动拉长到停机前 30min，第一轮 scan 即追平（启动回补不再需要）。
- **封顶**：yfinance CAP = 7d（yahoo 5m 数据可得范围内留裕量）、OKX CAP = 72h
  （沿用 `PRICE_BACKFILL_MAX_HOURS`，分页 8 页/100h 上限现成）。更早历史放弃，与现状上限一致。

| 源 | 实现变化 | 正常轮成本 |
|---|---|---|
| yfinance | `yf.download(start=, end=)` 精确窗口取代 `period="7d"` | 每品种 1 请求（不变）；解析 ~290 bar/品种，是 7d 方案的 **1/5**，写入查重同比缩小 |
| OKX | `fetch_history(start, now)` 取代 `fetch()` | 24h = 288 bar，单页（limit 300）恰好 1 次调用/品种 |
| CNBC 债券 | `fetch()` 维持当前报价口径 | 无历史 K 线，不参与缺口语义（现状） |

- **游标就是数据库本身**，不新增任何状态表。
- `_save_records` 返回值从计数改为**本轮实际插入的记录列表**；`scan()` 返回及日志随之改为
  "新插入 N 条"（过程 log 的主体，供验证报告对照）。消费方 `task_service` / `source_statuses` 数字随之有意义。
- 失败语义：本轮拉不到就不写，下一轮同一公式自动覆盖——**故障恢复 = 正常路径**。

### 3.2 验证与报告（不新增任何 job）

- **过程 log**：每轮 scan 记录"各源返回 N 条 / 新插入 M 条"（loguru 落 `logs/market_monitor.log`）。
- **报告**（部署后 3-7 天，约 2026-07-17 ~ 07-21，由 Claude 以线上只读查询出具）：
  1. 各品种内部缺口清单（相邻 bar 间隔 > 7.5min，`timestamp` 序列扫描）；
  2. 晚到统计（`created_at − timestamp > 30min` 的 bar 数与分布）；
  3. 判定：缺口应全部可解释为休市形状；无系统性缺失即验收通过。
- 偶发缺失 = 接受，不修；若某次极端事件（如源长期故障）造成大段缺失，走一次性专项补脚本（手动、事后、
  无常驻机制），并沿用 2026-07-12"段重算脚本"存档设计处理下游段修正。

### 3.3 退役清单

| 退役项 | 位置 | 处置 |
|---|---|---|
| 滚动追平回填·价格部分 | `scan_runtime._run_rolling_backfill`（[scan_runtime.py:258](../../../services/scan_runtime.py)） | 删价格段；**新闻段保留**，函数改名 `_run_news_rolling_backfill` |
| 启动回补·价格部分 | `scan_runtime.run_startup_backfill_once`（[scan_runtime.py:315](../../../services/scan_runtime.py)） | 删 `backfill_missing_history` 调用及超时追加段；新闻回补保留；调用方 [app.py:70](../../../api/app.py) 随之只收新闻 |
| `run_price_backfill_once` | [scan_runtime.py:281](../../../services/scan_runtime.py) | 整个删除（全仓无调用方；[run.py:30](../../../run.py) 死 import 一并清） |
| `PriceScanner.backfill_missing_history` / `backfill_range` | [price_scanner.py:66](../../../scanners/price_scanner.py) | 均删除；新路径直接组合 `source.fetch_history` + `_save_records` |
| **`services/gap_repair.py` 整个文件** | 含 `find_gaps` / `repair_symbols` / `run_gap_repair` | **删除**（v2：监控也不要）；`tests/test_gap_repair.py` 删除 |
| 小时 job 的价格步骤 | [app.py:99](../../../api/app.py) `gap_repair_cycle` ① | 删除；job 只剩 traditional_open + news_tagging 两步，函数与 job id 更名 `news_tagging_cycle`（cron :37 不变） |
| `yfinance.fetch()` / `okx.fetch()` 及 live 取最后一根的私有帮手 | 两个 source 文件 | 删除（`cnbc.fetch()` 保留；`okx.fetch_instrument_bars` 是 gapfill 依赖，保留） |
| `config.SCAN_ROLLING_BACKFILL_INTERVALS` | [config.py:123](../../../config.py) | 删除 |
| `config.GAP_REPAIR_LOOKBACK_HOURS` | config.py | 删除（无消费方） |

`PRICE_BACKFILL_MAX_HOURS`（72）语义改为"OKX 同步窗口 CAP"；新增 `SYNC_MIN_LOOKBACK_HOURS = 24`
（两源共用的"至少回看"地板）与 yfinance CAP 常量（7d，放 source 文件）。

### 3.4 明确不变

5min 调度与扫描锁、告警评估时序（评估读库，自愈 bar 自动参与窗口）、`gap_filler` 的位置与全部逻辑、
`_save_records` 真实覆盖合成点语义、新闻/预测扫描与新闻打标小时步骤、行为引擎
（本案落地后补写事件仅剩长停机一种来源，"段重算脚本"维持 2026-07-12 决策：存档设计、出事再建）。

## 4. 边界情况

- **新增品种**（库内无游标）：该品种不参与 cursor 取 min？——取 min 时库空品种按 now−CAP 计，
  即首轮自动拉满 CAP 深度做种子（yfinance 7d / OKX 72h），之后回到 24h 地板。
- **晚到 >24h 的 bar**（历史 37 天未观察到）：不再自愈 → 落缺口 → 报告可见 → 接受或专项补。
- **停机 > CAP**：起点截断，更早历史接受缺失（与现状一致）。
- **债券 record.timestamp=None**：沿用 `scan_time` 兜底（现状）。
- **时区**：全链 UTC naive（现状约定），`fetch_history` 两端已做 tz 归一。

## 5. 测试（TDD，先红后绿）

新增 `tests/test_cursor_sync.py`（fake source 注入）：
1. **窗口公式三态**：平时 → start = now−24h；库落后 3 天 → start = 停机前−30min；落后 10 天 → start = now−CAP。
2. **自愈**：第一轮源响应中段缺 3 根 → 库有洞；第二轮源补全 → 洞被填。
3. **幂等**：同一响应连跑两轮 → 第二轮插入 0。
4. **返回口径**：scan 返回仅含本轮新插入记录。
5. **种子**：库空品种首轮拉 CAP 深度。

删除 `tests/test_gap_repair.py`。回归：`test_save_records_overwrite`、`test_gap_filler`、`test_scan_windows`、
启动路径（价格回补不再被调用）、`test_api`（若引用 gap_repair job 名）全绿。

## 6. 涉及文件

`scanners/price_scanner.py`、`scanners/sources/yfinance_source.py`、`scanners/sources/okx_source.py`、
`services/scan_runtime.py`、`services/gap_repair.py`（删）、`api/app.py`、`run.py`、`config.py`、
`tests/`（新增 1 / 删除 1 / 回归若干）、`ARCHITECTURE.md` / `DATAFLOW.md` / `DECISIONS.md` / `PENDING.md`（同 commit 契约）。

## 7. 部署与验证

服务器 `git pull && ./deploy.sh`。部署当天记录部署时刻；**3-7 天后（≈ 2026-07-17 ~ 07-21）出验收报告**
（§3.2 的三项只读查询，PENDING.md 挂一条带日期的待办）。验收通过后本案关闭；
若发现系统性缺失，按报告定位是源问题还是路径 bug，修因不加层。

## 8. 后续关联(不在本案)

- Massive Futures Starter（$29/月，10min 延迟）接入：只需新增 source 类实现 `fetch_history`，本架构不动；
  连续合约与 gapfill staleness 联动两点在该案核实。
- 段重算脚本：维持存档（2026-07-12 ~ 07-14 讨论定稿），触发条件 = 长停机后需修正历史段。
- 合并判据 `≤5min` → `<5min`（"覆盖无空隙"语义）：用户尚未拍板，独立校准变更。
