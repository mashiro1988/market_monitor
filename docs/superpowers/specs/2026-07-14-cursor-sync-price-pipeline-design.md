# 价格管道"游标同步"重构 设计

> 一条路径取代多层补 bar 机制（滚动追平 / 每小时 gap_repair / 启动回补 / 手动回补）：每轮扫描按时间区间
> 拉历史 K 线并幂等入库——采集与回补合并为同一操作；
> 原 gap_repair 每小时槽位改为**纯监控**（只报不修），供用户验证新路径是否 robust。
> 创建：2026-07-14（用户拍板：先做架构，Massive 付费源接入另案再议）。

## 1. 目标与非目标

**目标**
1. 价格数据写入路径收敛为一条：区间拉取 + 幂等 upsert。退役滚动追平回填（价格部分）、启动价格回补、gap_repair 修复。
2. 原每小时 gap_repair 槽位改为"缺口监控"：源端有而库里缺 → 告警（= 新路径失灵信号）；源端也没有 → 静默（休市/源缺失）。不写库。

**非目标**
- 不换数据源（yfinance/OKX/CNBC 维持现状；Massive Futures 接入是独立后续案）。
- okx_gapfill 休市合成点不动（展示功能，非补真数据）。
- 新闻/预测管道不动（滚动回填的**新闻部分保留**）。
- 行为引擎、告警评估、调度周期不动。

## 2. 病根（为什么现在有四层）

`yfinance.fetch()`（[yfinance_source.py:159](../../../scanners/sources/yfinance_source.py)）每 5min 已经用
`yf.download(period="7d", interval="5m")` 把**全品种 7 天的 5m K 线**拿在手里，然后每品种只留最后一根、
其余全部丢弃。`okx.fetch()` 同理（`limit=5` 取最后一根收盘）。于是"迟到/漏掉的 bar"需要三个额外机制
把刚被丢弃的数据再取回来：滚动追平（10min）、每小时 gap_repair（24h）、启动回补（72h）。
四层交互的复杂度正是"48h 段重算"issue 的根源（2026-07-12 决策背景）。

生产数据佐证（2026-07-13 线上只读查询）：BTC 自 06-06 上线以来 10,704 根 bar 零缺口零补写；
补写活动全部在 yfinance 品种（每品种每天约 4-6 根晚到 bar，均由 gap_repair 在 1h 内修复）。
即：现有四层最终能自愈，但代价是四套机制；本案把自愈变成主路径的固有属性。

## 3. 设计

### 3.1 同步路径（唯一写路径）

`PriceScanner.scan()` 的价格采集部分改为（[price_scanner.py:27](../../../scanners/price_scanner.py)）：

| 源 | 调用 | 说明 |
|---|---|---|
| yfinance | `fetch_history(now−7d, now)` | **HTTP 请求数与现状完全相同**——`period="7d"` 本来就是全量下载，变化只是"不再丢弃"。7d = 自带 7 天自愈窗口：yahoo 中段稀疏响应、迟到 bar，源什么时候给、下一轮什么时候进库。 |
| OKX | `fetch_history(max(cursor − 30min, now − 72h), now)`，其中 cursor = 各加密品种"库内最新 bar 时刻"的**最小值**（最落后品种决定窗口，保证全部被覆盖；库空品种视为 now−72h） | 正常时刻窗口 ≈ 30min，1 次 API 调用；停机后窗口自动变宽，现成分页逻辑接管（8 页 / 100h 上限）。起点下限 72h 沿用 `PRICE_BACKFILL_MAX_HOURS`。 |
| CNBC 债券 | `fetch()` 维持当前报价口径 | 无历史 K 线，不参与缺口语义（现状）。 |

- 写库仍走 `_save_records`：`(symbol, timestamp)` 唯一键已存在即跳过、真实价覆盖同槽合成点的逻辑不变。
  **游标就是数据库本身**，不新增任何状态。
- `_save_records` 返回值从计数改为**本轮实际插入的记录列表**；`scan()` 返回及日志随之改为
  "新插入 N 条"（否则每轮日志会报 7 万条）。消费方 `task_service` / `source_statuses` 的数字因此变得有意义。
- 自愈语义总结：源端迟到/中段稀疏 → 下一轮进库；停机重启 → 第一轮 scan 即追平（启动回补不再需要）；
  拉取失败 → 本轮不写，下一轮同一路径追上。**故障恢复 = 正常路径**。

### 3.2 缺口监控（原 gap_repair 槽位，只报不修）

新 `services/gap_monitor.py`（改造自 `gap_repair.py`，`find_gaps`/`repair_symbols` 复用）：

- 调度不变：每小时 :37 的 `gap_repair_cycle` job 第①步换成监控；②traditional_open ③news_tagging 原样保留。
- **干跑审计**：调用与扫描路径**同一个** `fetch_history`（同窗口），与库 diff。
  源端返回、库里却没有的 bar（排除尾部 15min 未收口区）→ 推 WeCom
  "**同步失灵：{symbol} {区间} 缺 N 根（源端有数据）**"。这是主路径 bug 的信号——修 bug，不做静默兜底。
- **信息清单**：`find_gaps(24h)` 的库内缺口逐条写日志（不推送——休市段每天都会出现，推送即噪音）。
- 稳态 = 完全静默，延续现有 gap_repair 干净轮静默的习惯。用户验证期看两处：WeCom 无告警 + 日志缺口清单肉眼可解释（全是休市形状）。

### 3.3 退役清单

| 退役项 | 位置 | 处置 |
|---|---|---|
| 滚动追平回填·价格部分 | `scan_runtime._run_rolling_backfill`（[scan_runtime.py:258](../../../services/scan_runtime.py)） | 删价格段；**新闻段保留**，函数改名 `_run_news_rolling_backfill` |
| 启动回补·价格部分 | `scan_runtime.run_startup_backfill_once`（[scan_runtime.py:315](../../../services/scan_runtime.py)） | 删 `backfill_missing_history` 调用及超时追加段；新闻回补保留；调用方 [app.py:70](../../../api/app.py) 随之只收新闻 |
| `run_price_backfill_once` | [scan_runtime.py:281](../../../services/scan_runtime.py) | 整个删除（全仓无调用方；[run.py:30](../../../run.py) 死 import 一并清） |
| `PriceScanner.backfill_missing_history` 与 `backfill_range` | [price_scanner.py:66](../../../scanners/price_scanner.py) | **均删除**。`backfill_range` 内部调 `_save_records`（写库），监控干跑不能用它；scan() 新路径直接组合 `source.fetch_history(各自窗口)` + `_save_records`，监控组合 `source.fetch_history` + 只读 diff。删除后全仓无调用方 |
| gap_repair 修复语义 | [gap_repair.py](../../../services/gap_repair.py) | 改造为 `gap_monitor`（不再调 `backfill_range` 写库） |
| `yfinance.fetch()` / `okx.fetch()` 及 live 取最后一根的私有帮手 | 两个 source 文件 | 删除（`cnbc.fetch()` 保留；`okx.fetch_instrument_bars` 是 gapfill 依赖，保留） |
| `config.SCAN_ROLLING_BACKFILL_INTERVALS` | [config.py:123](../../../config.py) | 删除 |

`GAP_REPAIR_LOOKBACK_HOURS`（24）留给监控用；`PRICE_BACKFILL_MAX_HOURS`（72）语义改为"OKX 同步窗口上限"，注释更新。

### 3.4 明确不变

5min 调度与扫描锁、告警评估时序（评估读库，自愈 bar 自动参与窗口）、`gap_filler` 的位置与全部逻辑、
`_save_records` 真实覆盖合成点语义、新闻/预测扫描、行为引擎（本案落地后补写事件仅剩长停机一种来源，
"段重算脚本"维持 2026-07-12 决策：存档设计、出事再建）。

## 4. 边界情况

- **新增 OKX 品种**（库内无游标）：起点取 now−72h。
- **yahoo 7 天内始终不给的 bar**：7 天后不再自愈；监控干跑同样看不到（源端无）→ 静默，等价于休市/源永久缺失，与现状一致。
- **停机 > 72h（OKX）/ 7d（yfinance）**：起点截断，更早历史接受缺失（与现状 `PRICE_BACKFILL_MAX_HOURS=72` 上限一致）。
- **债券 record.timestamp=None**：沿用 `scan_time` 兜底（现状）。
- **时区**：全链 UTC naive（现状约定），`fetch_history` 两端已做 tz 归一。

## 5. 测试（TDD，先红后绿）

新增 `tests/test_cursor_sync.py`（fake source 注入）：
1. **自愈**：第一轮源响应中段缺 3 根 → 库有洞；第二轮源补全 → 洞被填。
2. **幂等**：同一响应连跑两轮 → 第二轮插入 0。
3. **停机恢复**：库最新 bar 落后 3h → 一轮 scan 追平（OKX 窗口计算正确）。
4. **返回口径**：scan 返回仅含本轮新插入记录。

`tests/test_gap_repair.py` 重写为 monitor 语义：
5. 源有 bar 而库缺 → 告警推送（内容含品种/区间/根数）；源无 → 静默；尾部 15min 不算。
6. 监控不写库（跑完后行数不变）。

回归：`test_save_records_overwrite`、`test_gap_filler`、`test_scan_windows`、启动路径（价格回补不再被调用）全绿。

## 6. 涉及文件

`scanners/price_scanner.py`、`scanners/sources/yfinance_source.py`、`scanners/sources/okx_source.py`、
`services/scan_runtime.py`、`services/gap_repair.py`（→ `gap_monitor.py`）、`api/app.py`、`run.py`、`config.py`、
`tests/`（新增 1 + 重写 1 + 回归）、`ARCHITECTURE.md` / `DATAFLOW.md` / `DECISIONS.md` / `PENDING.md`（同 commit 契约）。

## 7. 部署与验证

服务器 `git pull && ./deploy.sh`。验证期一周：WeCom 应零"同步失灵"告警；
每日肉眼扫一眼监控日志的缺口清单（应全为休市形状）；
可复用 2026-07-13 的线上只读查询（`created_at − timestamp > 30min`）确认无新增补写路径遗漏。

## 8. 后续关联（不在本案）

- Massive Futures Starter（$29/月，10min 延迟）接入：届时只需新增一个 source 类实现 `fetch_history`，
  本架构无需再动；连续合约与 gapfill staleness 联动两点在该案核实。
- 段重算脚本：维持存档（设计已在 2026-07-12 至 07-14 讨论中定稿），触发条件 = 长停机后需要修正历史段。
- 合并判据 `≤5min` → `<5min`（"覆盖无空隙"语义）：用户尚未拍板，独立校准变更。
