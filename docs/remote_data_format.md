# 远程数据源格式文档

> **服务器**: `root@47.243.252.92:/root/data_center/`
> **生成器**: BMAC（Binance Market Asset Collector），邢不行 / QuantClass 出品的开源数据中心。源码在 `/root/data_center/`。
> **本文档**：探测 + 源码阅读固化的事实，用于编写 market_monitor 的接入层。

## 1. 节奏与触发机制（BMAC 调度）

服务器配置：[`/root/data_center/config.py`](../scripts/server_src/config.py)

| 项 | 值 | 含义 |
|---|---|---|
| `base_interval` | `5m` | 底层 K 线粒度，5m bar |
| `resample_interval` | `1h` | 重采样到 1h |
| `enabled_hour_offsets` | `('10m', '20m', '30m', '35m')` | 同一根 1h bar 用 4 个偏移产出，分别落在 :10 / :20 / :30 / :35 分 |
| `kline_count_1h` | `2000` | 1h pkl 滚动窗口 = 2000 根 ≈ 83 天 |
| `preprocess_num_batch` | `64` | dict_batch 文件每批 64 个 symbol |

**主循环**（[`realtime_data.py:139`](../scripts/server_src/realtime_data.py)）：每个 5min tick 检查 `run_time.minute` 是否命中 `enabled_hour_offsets`：
- 命中（`:00`、`:10`、`:15`、`:20`、`:25`、`:30`、`:35`、`:40`、`:45`、`:50`、`:55` 等大部分）→ `update_all()`
- 不命中 → `sleep(60)` 跳过

每个偏移目录（`/10m/`、`/20m/`、`/30m/`、`/35m/`）由对应偏移的 tick 写入，所以**它们的"新鲜度"不同步**：探测到 `/35m/` 的 cutoff 比 `/10m/` 新 25 分钟。

**写完落 `.ready` flag** 给消费者监听 — 这就是我们要用的"数据就绪信号"。

## 2. 目录结构与每个 family 的 schema

```
/root/data_center/data/
├── binance_spot_1h_resample/        # 现货 1h 重采样，按 symbol 分文件
│   ├── 10m/ {SYMBOL}USDT.pkl + _ready    [425 symbols]
│   ├── 20m/ ...
│   ├── 30m/ ...
│   └── 35m/ ...
├── binance_swap_1h_resample/        # 永续 1h 重采样
│   ├── 10m/ {SYMBOL}USDT.pkl + _ready    [531 symbols]
│   └── 20m/ 30m/ 35m/ ...
├── data_api_spot_1h_resample/       # QuantClass DATA API 镜像（备用源）
├── data_api_swap_1h_resample/       # 同上
├── binance_spot_5m/                 # 5m bar，按 symbol/月 分文件
│   └── {SYMBOL}USDT/
│       └── {YYYYMM}.pkl              [603 symbols × ~4 months]
├── binance_swap_5m/                 # 永续 5m，同结构
├── preprocess_1h_resample/          # 板块/横截面用的合并视图
│   ├── 10m/
│   │   ├── market_pivot_spot_{YYYY}.pkl  (dict: open/close/vwap1m)
│   │   ├── market_pivot_swap_{YYYY}.pkl  (dict: open/close/funding_rate/vwap1m)
│   │   ├── spot_dict_batch{1..7}.pkl     (dict: symbol -> DataFrame)
│   │   └── swap_dict_batch{1..N}.pkl
│   └── 20m/ 30m/ 35m/ ...
├── funding/                         # 资金费率（仅永续）
│   └── {SYMBOL}USDT.pkl + _ready     [1172 symbols, 8h 一根]
├── coin_cap/                        # 市值数据（每日）
│   └── {SYMBOL}USDT.pkl + _ready     [1770 symbols, 日线]
└── exginfo/                         # 交易所元信息 + 现货↔永续映射
    ├── exginfo_spot.pkl
    ├── exginfo_swap.pkl
    └── spot_swap_matches.pkl
```

总占用 ≈ 4.8 GB。

### 2.1 Per-symbol 1h bar（resample/{offset}/）

```
type: pandas.DataFrame, shape ~ (2000, 10)
columns: ['candle_begin_time', 'open', 'high', 'low', 'close',
          'volume', 'quote_volume', 'trade_num',
          'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume']
dtypes:  candle_begin_time -> datetime64[ns, UTC]   ← 显式 UTC
         open/high/low/close/volume/* -> float64
         trade_num -> int64
index:   RangeIndex 1..2000 (NOT a DatetimeIndex; 时间在列里)
spacing: exactly 1h
```

### 2.2 Per-symbol 5m monthly partition（{spot,swap}_5m/{SYMBOL}/）

```
shape ~ (4430, 10)   # 一个月约 30天 × 288根/天 ≈ 8640，pkl 内 ~4430 表示半月
columns: 同 2.1
dtypes:  同 2.1
spacing: 5m
```

### 2.3 Preprocess 板块视图（preprocess_1h_resample/{offset}/）

**`market_pivot_{spot,swap}_{YYYY}.pkl`**:
```
type: dict
spot 的 keys: ['open', 'close', 'vwap1m']
swap 的 keys: ['open', 'close', 'funding_rate', 'vwap1m']
每个 value: DataFrame, shape (2000, 425/531)
  index: DatetimeIndex (UTC) candle_begin_time
  columns: 每列一个 symbol（如 BTCUSDT, ETHUSDT, ...）
```
**这就是 ClsBinanceSymbol 用过的 `market_pivot_spot.pkl` 的等价物**，可直接用于板块涨跌计算。

**`{spot,swap}_dict_batch{N}.pkl`**:
```
type: dict (64 个 symbol)
key: 'BTCUSDT', 'ETHUSDT', ...
value: DataFrame, per-symbol OHLCV（同 2.1 的结构）
```
这是把 per-symbol 文件按 64 个/批聚合后的版本，给框架内部用。**接入层不依赖**。

### 2.4 资金费 funding/

```
shape (612, 3), spacing 8h
columns: ['symbol', 'funding_rate', 'candle_begin_time']
注意: candle_begin_time 这里是 datetime64[ns, UTC]
注意: 探测时最新数据是 2026-02-27, 可能更新有滞后（需上线后再观察）
```

### 2.5 市值 coin_cap/

```
shape (1981, 12), 日线
columns: ['candle_begin_time', 'symbol', 'id', 'name', 'date_added',
          'max_supply', 'circulating_supply', 'total_supply',
          'usd_price', 'max_mcap', 'circulating_mcap', 'total_mcap']
注意: candle_begin_time 这里是 datetime64[ns]（无时区，UTC naive）
范围: 2020-12-09 -> 2026-05-15
```

### 2.6 元数据 exginfo/

```
exginfo_spot.pkl: DataFrame (425, 8)
  columns: ['symbol', 'status', 'base_asset', 'quote_asset',
            'price_tick', 'lot_size', 'min_notional_value', 'pre_market']

exginfo_swap.pkl: DataFrame (531, 9)
  columns: 上 + ['contract_type', 'margin_asset']

spot_swap_matches.pkl: DataFrame (582, 2)
  columns: ['spot', 'swap']
  作用: 把现货和永续 symbol 配对，是处理"BNB 现货 / BNBUSDT 永续"差异的官方映射表
```

## 3. `.ready` flag 约定

```
文件名: {dataset_basename}_{cutoff_unix_ts}.ready
内容:   一个浮点数字符串，= 实际写入完成时间（unix epoch）
        cutoff_ts 是数据应覆盖到的 UTC 时间戳
        content_ts 比 cutoff_ts 晚 5-60s（取决于写入耗时）
```

**消费规则**：
- 想知道某 dataset 的最新可用 cutoff → 列目录，取最大 `_{ts}.ready`
- 想知道写入是否完成 → 文件存在即完成（BMAC 是原子写入：先 `.tmp` 再 rename）
- 想避免读到半写状态 → 等 `.ready` 出现再读对应 `.pkl`

## 4. 时间语义注意

| 数据集 | 时间列时区 | 影响 |
|---|---|---|
| `{spot,swap}_1h_resample/*` | datetime64[ns, **UTC**] | 接入时要做 tz handling |
| `{spot,swap}_5m/*` | datetime64[ns, **UTC**] | 同上 |
| `preprocess` 的 pivot/dict | datetime64[ns, **UTC**] | 同上 |
| `funding/*` | datetime64[ns, **UTC**] | 同上 |
| `coin_cap/*` | datetime64[ns]（**UTC naive**） | 直接当 UTC 用 |
| `.ready` 文件名 cutoff | Unix epoch（UTC） | 标准 unix 时间戳 |

market_monitor 现有 DB 是 **UTC naive**（PENDING.md），接入层要做的事是 `.dt.tz_localize(None)` 后入库。

## 5. numpy 版本兼容性

服务器写 pkl 的 Python 用 **numpy 2.x**（pkl 内含 `numpy._core.*` 引用），本地 anaconda 是 numpy 1.26.4。**不要升级本地 numpy**，用 shim：

```python
# 放在 services/remote_fs.py 的最顶部（import pickle 之前）
import sys, importlib
import numpy.core, numpy.core.numeric, numpy.core.multiarray
sys.modules.setdefault("numpy._core", numpy.core)
sys.modules.setdefault("numpy._core.numeric", numpy.core.numeric)
sys.modules.setdefault("numpy._core.multiarray", numpy.core.multiarray)
for sub in ["umath", "_methods", "fromnumeric", "_dtype",
            "_dtype_ctypes", "_internal", "arrayprint"]:
    try:
        sys.modules.setdefault(f"numpy._core.{sub}",
                               importlib.import_module(f"numpy.core.{sub}"))
    except Exception:
        pass
```

实测：1h resample、5m monthly、preprocess pivot、funding、coin_cap、exginfo 全部能加载。

**风险**：如果 BMAC 升级后引入只在 numpy 2 存在的新 dtype，shim 会失效。短期可接受，长期最好升级 market_monitor 到独立 venv（不影响其它 anaconda 项目）。

## 6. 已知数据质量问题

- **乱码 symbol**：探测出现 `'��̤������USDT'` / `'�Ұ�����USDT'` / `'��ϺUSDT'` 等中文编码错乱的列，约 3-5 个。**接入时按 `re.match(r'^[A-Z0-9]+USDT$', name)` 过滤**。
- **数据源差异**：`binance_*` 是 Binance REST 直接拉的，`data_api_*` 是 QuantClass DATA API（付费源备份）。两者 99% 一致，接入时**优先用 `binance_*`**，DATA API 只在 binance 失败时兜底（其实服务器自己已经会用 binance 数据兜底覆盖到 data_api 目录）。
- **funding 滞后**：探测时最新是 2026-02-27（晚 2.5 个月），需要联系运维确认是否正常。一期接入可以**先不要 funding**。

## 7. 接入层推荐策略

| 用途 | 应该读哪个文件 |
|---|---|
| 板块榜单（沿用 ClsBinanceSymbol 逻辑） | `preprocess_1h_resample/30m/market_pivot_spot_2026.pkl`（用 30m 偏移 = 中等新鲜度，且文件单一好缓存） |
| 单币种 1h 历史 + 多周期涨跌 | `binance_swap_1h_resample/30m/{SYMBOL}USDT.pkl` |
| 单币种 5m K 线（图表用） | `binance_swap_5m/{SYMBOL}USDT/{YYYYMM}.pkl`（按需拼接月份） |
| symbol 映射 / 现货↔永续 | `exginfo/spot_swap_matches.pkl`（每日刷新一次足够） |
| 市值（板块加权用） | `coin_cap/{SYMBOL}USDT.pkl`（每日刷新一次足够） |

**偏移选择**：用 **30m 偏移** —— 既不是最快（避免和 BMAC 的写入边缘竞争），也不是最慢，缓存命中率最高。
