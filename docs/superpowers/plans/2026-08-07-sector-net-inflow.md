# 板块净资金流入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 板块轮动页的板块榜单与成分币钻取各增加 4 列「净资金流入」（现货/永续分开、绝对额+强度比率），数据来自币安 K 线自带的主动买入额字段。

**Architecture:** 数据服务器端给 BMAC 宽表生成代码打纯增量补丁，把 `quote_volume` 与 `taker_buy_quote_asset_volume` 两个矩阵加进现有 pivot pkl —— 资金流因此与价格天生同文件、同就绪信号、同截止时刻，本项目拉取器零改动。本项目侧新增 `services/sector_flows.py` 承担全部资金流计算与勾稽校验；scanner 每小时过闸后把窗口聚合值写进 `sector_returns` 新增的 18 列；任一勾稽失败则该市场资金流写 None 并告警，涨跌链路完全不受影响。

**Tech Stack:** Python 3 / pandas / SQLAlchemy / FastAPI / pydantic / pytest；前端 React + TypeScript + TanStack Query + vitest。

**规范：**
- 本机 python 必须用 `D:\anaconda\python.exe`（PATH 上的 `python` 是会 exit 49 的桩）
- 后端测试：`D:\anaconda\python.exe -m pytest <path> -v`
- 前端测试：在 `frontend/` 下 `npx vitest run <path>`；类型检查 `npx tsc --noEmit`
- 每个 Task 结束提交一次

**设计稿：** `docs/superpowers/specs/2026-08-07-sector-net-inflow-design.md`

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `config.py` | 修改 | 3 个新阈值常量（勾稽容忍度、告警冷却） |
| `models/sector.py` | 修改 | `SectorReturn` 加 18 列 |
| `database.py` | 修改 | sector_returns 补列迁移支持 INTEGER 类型 + 18 个新列名 |
| `services/sector_flows.py` | **新建** | 唯一的资金流计算模块：勾稽门、窗口求和、板块聚合、DB 列名映射 |
| `services/sector_flow_monitoring.py` | **新建** | 勾稽失败告警（marker + 冷却 + AlertLog，仿 price_source_monitoring） |
| `scanners/sector_scanner.py` | 修改 | pivot 只加载一次（MarketData）；聚合结果带资金流；写库 |
| `services/sector_service.py` | 修改 | 榜单读 DB 列→响应；钻取读时现算币级资金流 |
| `schemas/sectors.py` | 修改 | `SectorFlowSide` / `SectorFlows` + 两个 row 模型加 `flows` |
| `scripts/verify_taker_pivot_patch.py` | **新建** | T0 部署验收脚本（独立跑在数据服务器上） |
| `scripts/server_src/preprocess.py` | 修改 | 服务器补丁的仓库留档镜像 |
| `frontend/src/api/types.ts` | 修改 | 前端类型 |
| `frontend/src/pages/sectorFlowFormat.ts` | **新建** | 纯函数：金额缩写、强度比率、排序取值 |
| `frontend/src/pages/SectorRotationPage.tsx` | 修改 | 4 列 + 排序项 + FlowCell |
| `tests/test_sector_flows.py` | **新建** | 计算 + 勾稽门 |
| `tests/test_sector_flow_alerts.py` | **新建** | 告警冷却与去重 |
| `tests/test_sector_flow_migration.py` | **新建** | 18 列补齐幂等 |
| `tests/test_sector_returns.py` | 修改 | 跟随 scanner 重构更新 |
| `frontend/src/pages/sectorFlowFormat.test.ts` | **新建** | 格式化与排序取值 |

**窗口命名统一（全栈一致，务必照抄）：** 窗口键为 `"1h" | "24h" | "168h" | "720h"`；DB 列 `{spot,swap}_net_{窗口}` 与 `{spot,swap}_qv_{窗口}`；前端 UI 显示 1h / 24h / 7d / 30d，但代码里的键一律用 168h / 720h。

---

### Task 1: 配置常量 + 数据库 18 列与迁移

**Files:**
- Modify: `config.py:507`（远程数据配置段末尾）
- Modify: `models/sector.py:68`
- Modify: `database.py:119-129`
- Test: `tests/test_sector_flow_migration.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_sector_flow_migration.py`：

```python
"""sector_returns 资金流列的轻量迁移：旧库补列 + 重复跑不炸。"""
from sqlalchemy import create_engine, inspect, text

import database


FLOW_FLOAT_COLUMNS = [
    f"{market}_{kind}_{window}"
    for market in ("spot", "swap")
    for kind in ("net", "qv")
    for window in ("1h", "24h", "168h", "720h")
]
FLOW_INT_COLUMNS = ["spot_flow_tokens", "swap_flow_tokens"]


def _legacy_engine(tmp_path):
    """造一个只有旧列的 sector_returns —— 模拟线上存量库。"""
    engine = create_engine(f"sqlite:///{tmp_path/'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE sector_returns ("
            " id INTEGER PRIMARY KEY,"
            " snapshot_at DATETIME NOT NULL,"
            " category VARCHAR(120) NOT NULL,"
            " group_name VARCHAR(60),"
            " token_count INTEGER NOT NULL,"
            " ret_1h FLOAT, ret_24h FLOAT, ret_168h FLOAT, ret_720h FLOAT,"
            " created_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO sector_returns (snapshot_at, category, token_count, ret_24h)"
            " VALUES ('2026-08-07 10:00:00', 'AI & Big Data', 12, 3.5)"
        ))
    return engine


def test_migration_adds_flow_columns_and_is_idempotent(tmp_path, monkeypatch):
    engine = _legacy_engine(tmp_path)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "_IS_SQLITE", True)

    database._ensure_sqlite_schema()
    database._ensure_sqlite_schema()  # 第二次必须 no-op，不能抛

    columns = {c["name"]: c["type"].__class__.__name__.upper()
               for c in inspect(engine).get_columns("sector_returns")}
    for name in FLOW_FLOAT_COLUMNS:
        assert name in columns, f"缺列 {name}"
        assert "FLOAT" in columns[name]
    for name in FLOW_INT_COLUMNS:
        assert name in columns, f"缺列 {name}"
        assert "INTEGER" in columns[name]

    # 存量行还在，且新列为 NULL
    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT ret_24h, spot_net_24h, spot_flow_tokens FROM sector_returns"
        )).one()
    assert row[0] == 3.5
    assert row[1] is None
    assert row[2] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flow_migration.py -v`
Expected: FAIL —— `AssertionError: 缺列 spot_net_1h`

- [ ] **Step 3: 加配置常量**

`config.py` 第 507 行 `REMOTE_PULLER_POLL_SECONDS = ...` 之后追加：

```python

# 板块资金流勾稽门（2026-08-07 净资金流入 spec §5.2）。
# 宽表新增 quote_volume / taker_buy_quote_asset_volume 两个矩阵后，每轮扫描先过闸再算钱：
# 任一项不达标 → 该市场资金流整轮写 None（涨跌照常）+ 告警，绝不让错数上页面。
# 恒等式 0 <= 主动买入额 <= 总成交额 的逐格违规占比上限（浮点噪声留 0.1% 余量）
FLOW_IDENTITY_VIOLATION_MAX_RATIO = float(os.getenv("FLOW_IDENTITY_VIOLATION_MAX_RATIO", "0.001"))
# 最新一根 bar 上「成交额缺失率 − 收盘价缺失率」的上限：新字段大面积缺数时拦下
FLOW_NAN_GAP_MAX = float(os.getenv("FLOW_NAN_GAP_MAX", "0.05"))
# 勾稽失败告警的冷却分钟数（同 marker 冷却窗内只推一次）
FLOW_GATE_ALERT_COOLDOWN_MINUTES = int(os.getenv("FLOW_GATE_ALERT_COOLDOWN_MINUTES", "60"))
```

- [ ] **Step 4: 加 18 个模型列**

`models/sector.py` 中 `SectorReturn` 的 `ret_720h_median` 行（第 68 行）之后、`created_at` 之前插入：

```python
    # 资金流（2026-08-07 净资金流入 spec）：现货/永续两条独立口径，绝对额单位 USDT。
    # net = 主动买入额 − 主动卖出额；qv = 同一批 bar 的总成交额（强度比率 = net/qv 读时现算）。
    # {market}_flow_tokens = 该市场在此板块内实际有资金流数据的成分币数（与 token_count 可不等）。
    spot_net_1h = Column(Float, nullable=True)
    spot_net_24h = Column(Float, nullable=True)
    spot_net_168h = Column(Float, nullable=True)
    spot_net_720h = Column(Float, nullable=True)
    spot_qv_1h = Column(Float, nullable=True)
    spot_qv_24h = Column(Float, nullable=True)
    spot_qv_168h = Column(Float, nullable=True)
    spot_qv_720h = Column(Float, nullable=True)
    spot_flow_tokens = Column(Integer, nullable=True)
    swap_net_1h = Column(Float, nullable=True)
    swap_net_24h = Column(Float, nullable=True)
    swap_net_168h = Column(Float, nullable=True)
    swap_net_720h = Column(Float, nullable=True)
    swap_qv_1h = Column(Float, nullable=True)
    swap_qv_24h = Column(Float, nullable=True)
    swap_qv_168h = Column(Float, nullable=True)
    swap_qv_720h = Column(Float, nullable=True)
    swap_flow_tokens = Column(Integer, nullable=True)
```

- [ ] **Step 5: 改迁移分支支持列类型**

`database.py` 第 119-129 行整块替换（原来是 `for column_name in {set}` 硬编码 FLOAT，现在改成 name→type 的 dict）：

```python
        # sector_returns：补中位数列。均值代表强度，中位数代表板块广度。
        # 2026-08-07 追加 18 个资金流列（net/qv × 四窗口 × 两市场 + 两个覆盖币数）。
        if "sector_returns" in table_names:
            existing = {col["name"] for col in inspector.get_columns("sector_returns")}
            sector_new_columns = {
                "ret_1h_median": "FLOAT",
                "ret_24h_median": "FLOAT",
                "ret_168h_median": "FLOAT",
                "ret_720h_median": "FLOAT",
            }
            for market in ("spot", "swap"):
                for kind in ("net", "qv"):
                    for window in ("1h", "24h", "168h", "720h"):
                        sector_new_columns[f"{market}_{kind}_{window}"] = "FLOAT"
                sector_new_columns[f"{market}_flow_tokens"] = "INTEGER"
            for column_name, column_type in sector_new_columns.items():
                if column_name not in existing:
                    conn.execute(text(f"ALTER TABLE sector_returns ADD COLUMN {column_name} {column_type}"))
```

- [ ] **Step 6: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flow_migration.py -v`
Expected: PASS（1 passed）

- [ ] **Step 7: 跑板块相关既有测试确认没改坏**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_returns.py tests/test_sector_alerts.py -v`
Expected: 全部 PASS

- [ ] **Step 8: 提交**

```bash
git add config.py models/sector.py database.py tests/test_sector_flow_migration.py && git commit -m "feat(sectors): sector_returns 加 18 个资金流列 + 勾稽阈值常量"
```

---

### Task 2: 勾稽门（gate）

**Files:**
- Create: `services/sector_flows.py`
- Test: `tests/test_sector_flows.py`（新建）

勾稽门在算钱之前跑，四项全过才允许用这份数据。任一项不过，该市场整轮资金流作废。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_sector_flows.py`：

```python
"""板块资金流：勾稽门 + 窗口求和 + 板块聚合。"""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

import config
from services import sector_flows


def _frame(columns, rows):
    """rows: [(ts, [values...]), ...] -> DataFrame(index=时间, columns=币)"""
    return pd.DataFrame(
        [values for _, values in rows],
        index=pd.DatetimeIndex([ts for ts, _ in rows]),
        columns=columns,
    )


def _pivot(columns, close_rows, qv_rows, taker_rows):
    """造一份合规的 pivot dict（close + 两个资金流矩阵）。"""
    return {
        "close": _frame(columns, close_rows),
        "quote_volume": _frame(columns, qv_rows),
        "taker_buy_quote_asset_volume": _frame(columns, taker_rows),
    }


T0 = datetime(2026, 8, 7, 9, 0)
T1 = datetime(2026, 8, 7, 10, 0)


def _good_pivot():
    return _pivot(
        ["BTCUSDT", "ETHUSDT"],
        close_rows=[(T0, [100.0, 10.0]), (T1, [110.0, 11.0])],
        qv_rows=[(T0, [1000.0, 200.0]), (T1, [2000.0, 400.0])],
        taker_rows=[(T0, [600.0, 80.0]), (T1, [1400.0, 100.0])],
    )


# ---------------- 勾稽门 ----------------

def test_gate_passes_on_wellformed_pivot():
    assert sector_flows.check_flow_gate(_good_pivot()) is None


def test_gate_rejects_missing_keys():
    pivot = _good_pivot()
    del pivot["taker_buy_quote_asset_volume"]
    reason = sector_flows.check_flow_gate(pivot)
    assert reason is not None
    assert "缺字段" in reason


def test_gate_rejects_index_mismatch():
    pivot = _good_pivot()
    pivot["quote_volume"] = _frame(["BTCUSDT", "ETHUSDT"], [(T1, [2000.0, 400.0])])
    reason = sector_flows.check_flow_gate(pivot)
    assert reason is not None
    assert "对齐" in reason


def test_gate_rejects_column_mismatch():
    pivot = _good_pivot()
    pivot["quote_volume"] = _frame(
        ["BTCUSDT", "SOLUSDT"],
        [(T0, [1000.0, 200.0]), (T1, [2000.0, 400.0])],
    )
    reason = sector_flows.check_flow_gate(pivot)
    assert reason is not None
    assert "对齐" in reason


def test_gate_rejects_identity_violation_over_threshold():
    """主动买入额 > 总成交额 = 数据串列，超过容忍占比就拦下。"""
    pivot = _good_pivot()
    pivot["taker_buy_quote_asset_volume"] = _frame(
        ["BTCUSDT", "ETHUSDT"],
        [(T0, [9999.0, 80.0]), (T1, [1400.0, 100.0])],  # 4 格里 1 格违规 = 25%
    )
    reason = sector_flows.check_flow_gate(pivot)
    assert reason is not None
    assert "恒等式" in reason


def test_gate_tolerates_float_noise_within_ratio():
    """浮点噪声导致的极小超出不该拦（相对容差 1e-6 内视为合规）。"""
    pivot = _good_pivot()
    pivot["taker_buy_quote_asset_volume"] = _frame(
        ["BTCUSDT", "ETHUSDT"],
        [(T0, [1000.0000001, 80.0]), (T1, [1400.0, 100.0])],
    )
    assert sector_flows.check_flow_gate(pivot) is None


def test_gate_rejects_nan_gap_over_threshold(monkeypatch):
    """收盘价有值但成交额大面积缺失 = 新字段没写好。"""
    monkeypatch.setattr(config, "FLOW_NAN_GAP_MAX", 0.05)
    pivot = _pivot(
        ["BTCUSDT", "ETHUSDT"],
        close_rows=[(T0, [100.0, 10.0]), (T1, [110.0, 11.0])],
        qv_rows=[(T0, [1000.0, 200.0]), (T1, [2000.0, np.nan])],  # 最新 bar 缺 50%
        taker_rows=[(T0, [600.0, 80.0]), (T1, [1400.0, np.nan])],
    )
    reason = sector_flows.check_flow_gate(pivot)
    assert reason is not None
    assert "缺失" in reason


def test_gate_allows_nan_gap_when_close_also_missing():
    """收盘价本身就缺的币不算资金流的账（缺口 = 0）。"""
    pivot = _pivot(
        ["BTCUSDT", "ETHUSDT"],
        close_rows=[(T0, [100.0, 10.0]), (T1, [110.0, np.nan])],
        qv_rows=[(T0, [1000.0, 200.0]), (T1, [2000.0, np.nan])],
        taker_rows=[(T0, [600.0, 80.0]), (T1, [1400.0, np.nan])],
    )
    assert sector_flows.check_flow_gate(pivot) is None


def test_gate_rejects_none_pivot():
    assert sector_flows.check_flow_gate(None) is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flows.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'services.sector_flows'`

- [ ] **Step 3: 写勾稽门实现**

新建 `services/sector_flows.py`：

```python
"""板块资金流（净流入）计算与勾稽 —— 净流入相关的所有口径只在这一个文件里。

数据来源：BMAC 宽表 pivot（2026-08-07 服务器补丁后）新增两个矩阵
  - quote_volume                    每根 1h bar 的总成交额（USDT）
  - taker_buy_quote_asset_volume    其中「主动买入」（吃单方向为买）的成交额

口径（设计稿 docs/superpowers/specs/2026-08-07-sector-net-inflow-design.md §2）：
  单 bar 净流入 = 主动买入额 − 主动卖出额 = 2 × taker_buy − quote_volume
  窗口值        = 最近 N 根 bar 求和（N = 1 / 24 / 168 / 720，与涨跌四档对齐）
  强度比率      = 窗口净流入 ÷ 窗口总成交额（不落库，读时现算）
  板块级        = 成分币先求和再算比率（成交量加权），现货与永续**永不混加**

安全底线：check_flow_gate() 不通过的市场，该轮资金流全部作废写 None，
涨跌链路完全不受影响 —— 宁可页面显示「—」，不可显示错数。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

import config

# 宽表里两个新矩阵的键名（与服务器补丁写入的列名一致，零映射层）
QUOTE_VOLUME_KEY = "quote_volume"
TAKER_BUY_KEY = "taker_buy_quote_asset_volume"

# 窗口键 → 回看 bar 数。键名同时用于 DB 列名与 API 字段名，全栈一致。
FLOW_WINDOWS: dict[str, int] = {"1h": 1, "24h": 24, "168h": 168, "720h": 720}

# 恒等式判定的相对容差：允许 taker_buy 超出 quote_volume 十亿分之一（浮点累加噪声）
_IDENTITY_RTOL = 1e-6


def check_flow_gate(pivot: Optional[dict]) -> Optional[str]:
    """资金流勾稽门。通过返回 None，不通过返回中文失败原因（进告警正文）。

    四项检查，任一不过即整市场作废：
      1. 两个新矩阵都在
      2. 与 close 的行索引、列集合完全一致（防半写/陈旧/串表）
      3. 0 <= 主动买入额 <= 总成交额 的逐格违规占比不超阈值（防数据串列）
      4. 最新 bar 上「成交额缺失率 − 收盘价缺失率」不超阈值（防新字段大面积没写）
    """
    if pivot is None:
        return "pivot 未加载"

    close = pivot.get("close")
    if close is None:
        return "缺字段: close"

    missing = [k for k in (QUOTE_VOLUME_KEY, TAKER_BUY_KEY) if pivot.get(k) is None]
    if missing:
        return f"缺字段: {', '.join(missing)}（服务器补丁未生效或已被升级覆盖）"

    qv: pd.DataFrame = pivot[QUOTE_VOLUME_KEY]
    tb: pd.DataFrame = pivot[TAKER_BUY_KEY]

    for name, frame in ((QUOTE_VOLUME_KEY, qv), (TAKER_BUY_KEY, tb)):
        if not frame.index.equals(close.index):
            return f"{name} 与 close 行索引不对齐（{len(frame.index)} vs {len(close.index)} 行）"
        if list(frame.columns) != list(close.columns):
            return f"{name} 与 close 列集合不对齐（{len(frame.columns)} vs {len(close.columns)} 列）"

    if close.empty:
        return "close 为空表"

    # 3) 恒等式：0 <= taker_buy <= quote_volume（只看两边都有值的格子）
    both = qv.notna() & tb.notna()
    total = int(both.to_numpy().sum())
    if total == 0:
        return "无任何有效成交额格子"
    negative = (tb < 0) & both
    over = (tb > qv * (1 + _IDENTITY_RTOL)) & both
    violations = int((negative | over).to_numpy().sum())
    max_ratio = float(getattr(config, "FLOW_IDENTITY_VIOLATION_MAX_RATIO", 0.001))
    ratio = violations / total
    if ratio > max_ratio:
        return (f"恒等式违规占比 {ratio:.4%} 超过上限 {max_ratio:.4%}"
                f"（{violations}/{total} 格 taker_buy 为负或大于 quote_volume）")

    # 4) 最新 bar 的缺失缺口：收盘价有值、成交额没值的比例
    latest_close = close.iloc[-1]
    latest_qv = qv.iloc[-1]
    width = len(close.columns)
    if width == 0:
        return "close 无任何列"
    gap = (latest_qv.isna().sum() - latest_close.isna().sum()) / width
    max_gap = float(getattr(config, "FLOW_NAN_GAP_MAX", 0.05))
    if gap > max_gap:
        return f"最新 bar 成交额缺失率比收盘价高 {gap:.2%}，超过上限 {max_gap:.2%}"

    return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flows.py -v`
Expected: 10 passed

- [ ] **Step 5: 提交**

```bash
git add services/sector_flows.py tests/test_sector_flows.py && git commit -m "feat(sectors): 资金流勾稽门(缺字段/不对齐/恒等式/缺失缺口四检查)"
```

---

### Task 3: 币级窗口求和

**Files:**
- Modify: `services/sector_flows.py`
- Test: `tests/test_sector_flows.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_sector_flows.py` 末尾：

```python
# ---------------- 币级窗口求和 ----------------

def test_per_symbol_flows_single_bar_window():
    """1h 窗口 = 最近 1 根 bar。BTC: 2×1400−2000 = +800，qv=2000。"""
    flows = sector_flows.per_symbol_flows(_good_pivot(), as_of=T1)
    assert flows["BTC"]["net_1h"] == pytest.approx(800.0)
    assert flows["BTC"]["qv_1h"] == pytest.approx(2000.0)
    # ETH 主动买入 100 / 总额 400 → 净 −200（卖压）
    assert flows["ETH"]["net_1h"] == pytest.approx(-200.0)


def test_per_symbol_flows_partial_window_sums_available_bars():
    """窗口 24 根但只有 2 根 bar → 按实际存在的 bar 求和，不补零不作废。"""
    flows = sector_flows.per_symbol_flows(_good_pivot(), as_of=T1)
    # BTC 两根：(2×600−1000) + (2×1400−2000) = 200 + 800 = 1000；qv = 3000
    assert flows["BTC"]["net_24h"] == pytest.approx(1000.0)
    assert flows["BTC"]["qv_24h"] == pytest.approx(3000.0)


def test_per_symbol_flows_respects_as_of_cutoff():
    """as_of 早于最新 bar 时，晚于 as_of 的 bar 不进窗口。"""
    flows = sector_flows.per_symbol_flows(_good_pivot(), as_of=T0)
    assert flows["BTC"]["net_1h"] == pytest.approx(200.0)
    assert flows["BTC"]["qv_24h"] == pytest.approx(1000.0)


def test_per_symbol_flows_skips_all_nan_symbol():
    """整窗口全 NaN 的币不产出该窗口字段（而不是产出 0）。"""
    pivot = _pivot(
        ["BTCUSDT", "DEADUSDT"],
        close_rows=[(T0, [100.0, np.nan]), (T1, [110.0, np.nan])],
        qv_rows=[(T0, [1000.0, np.nan]), (T1, [2000.0, np.nan])],
        taker_rows=[(T0, [600.0, np.nan]), (T1, [1400.0, np.nan])],
    )
    flows = sector_flows.per_symbol_flows(pivot, as_of=T1)
    assert "DEAD" not in flows
    assert "BTC" in flows


def test_per_symbol_flows_pairs_net_and_qv_from_same_bars():
    """某根 bar 只有一边有值时，两边都不算 —— 保证强度比率内部自洽。"""
    pivot = _pivot(
        ["BTCUSDT"],
        close_rows=[(T0, [100.0]), (T1, [110.0])],
        qv_rows=[(T0, [1000.0]), (T1, [2000.0])],
        taker_rows=[(T0, [np.nan]), (T1, [1400.0])],  # 第一根缺主动买入额
    )
    flows = sector_flows.per_symbol_flows(pivot, as_of=T1)
    assert flows["BTC"]["qv_24h"] == pytest.approx(2000.0)   # 只算第二根
    assert flows["BTC"]["net_24h"] == pytest.approx(800.0)


def test_per_symbol_flows_merges_duplicate_normalized_symbols():
    """同一市场内两个交易对归一到同一 symbol（如 BEAMX→BEAM）时合并求和。"""
    pivot = _pivot(
        ["BEAMUSDT", "BEAMXUSDT"],
        close_rows=[(T0, [1.0, 1.0]), (T1, [1.0, 1.0])],
        qv_rows=[(T0, [100.0, 100.0]), (T1, [100.0, 100.0])],
        taker_rows=[(T0, [80.0, 80.0]), (T1, [80.0, 80.0])],
    )
    flows = sector_flows.per_symbol_flows(pivot, as_of=T1)
    # 单个交易对 1h：2×80−100 = 60；两个合并 = 120
    assert flows["BEAM"]["net_1h"] == pytest.approx(120.0)
    assert flows["BEAM"]["qv_1h"] == pytest.approx(200.0)


def test_per_symbol_flows_returns_empty_for_none_pivot():
    assert sector_flows.per_symbol_flows(None, as_of=T1) == {}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flows.py -k per_symbol -v`
Expected: FAIL —— `AttributeError: module 'services.sector_flows' has no attribute 'per_symbol_flows'`

- [ ] **Step 3: 写实现**

先把 `services/sector_flows.py` 的 import 区（`from __future__ import annotations` 之后到 `import config` 为止）替换为：

```python
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

import config
from scanners.sector_scanner import _slice_close_as_of, normalize_pivot_symbol
```

> 顶层 import `scanners.sector_scanner` 不会循环 —— sector_scanner 到 Task 5 才会 import sector_flows，
> 且届时用**函数内延迟 import**（与它现有的 `_load_pivot_cached` 延迟 import 同一手法）。

然后在 `check_flow_gate` 之后追加：

```python
def per_symbol_flows(
    pivot: Optional[dict],
    *,
    as_of: Optional[datetime],
) -> dict[str, dict[str, float]]:
    """单市场 pivot → {规范化 symbol: {net_1h, qv_1h, net_24h, qv_24h, ...}}。

    调用前必须先过 check_flow_gate()。约定：
    - 窗口 = 截到 as_of 的最近 N 根 bar；bar 不够就按实际有的求和（新币不作废）
    - 某根 bar 只要 net/qv 任一为缺失，这根 bar 两边都不计入 —— 强度比率才自洽
    - 整窗口无有效 bar 的币不产出该窗口的键（区别于「净流入恰好为 0」）
    - 同一市场内多个交易对归一到同一 symbol 时合并求和（BEAMX/BEAM 这类）
    """
    if pivot is None:
        return {}
    qv_all = pivot.get(QUOTE_VOLUME_KEY)
    tb_all = pivot.get(TAKER_BUY_KEY)
    if qv_all is None or tb_all is None:
        return {}

    qv_all = _slice_close_as_of(qv_all, as_of)
    tb_all = _slice_close_as_of(tb_all, as_of)
    if qv_all.empty:
        return {}

    out: dict[str, dict[str, float]] = {}
    for window, lookback in FLOW_WINDOWS.items():
        qv = qv_all.iloc[-lookback:]
        tb = tb_all.iloc[-lookback:]
        valid = qv.notna() & tb.notna()
        qv_sum = qv.where(valid).sum(min_count=1)
        tb_sum = tb.where(valid).sum(min_count=1)
        net_sum = 2.0 * tb_sum - qv_sum
        for col in qv_all.columns:
            qv_val = qv_sum.get(col)
            if qv_val is None or pd.isna(qv_val):
                continue
            nsym = normalize_pivot_symbol(str(col))
            if not nsym:
                continue
            bucket = out.setdefault(nsym, {})
            bucket[f"qv_{window}"] = bucket.get(f"qv_{window}", 0.0) + float(qv_val)
            bucket[f"net_{window}"] = bucket.get(f"net_{window}", 0.0) + float(net_sum[col])
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flows.py -v`
Expected: 17 passed

- [ ] **Step 5: 提交**

```bash
git add services/sector_flows.py tests/test_sector_flows.py && git commit -m "feat(sectors): 币级资金流窗口求和(部分窗口/成对缺失/重名合并)"
```

---

### Task 4: 板块聚合 + DB 列名映射

**Files:**
- Modify: `services/sector_flows.py`
- Test: `tests/test_sector_flows.py`

聚合与列名映射放同一个 Task：列名约定只此一处定义，scanner 写库和 service 读库都调它，避免两边各写一遍拼错。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_sector_flows.py` 末尾：

```python
# ---------------- 板块聚合 ----------------

def test_aggregate_sums_members_and_ignores_outsiders():
    per_symbol = {
        "BTC": {"net_1h": 100.0, "qv_1h": 1000.0, "net_24h": 300.0, "qv_24h": 3000.0},
        "ETH": {"net_1h": -40.0, "qv_1h": 400.0, "net_24h": -60.0, "qv_24h": 600.0},
        "DOGE": {"net_1h": 999.0, "qv_1h": 999.0},   # 不在板块里
    }
    side = sector_flows.aggregate_side(per_symbol, {"BTC", "ETH"})
    assert side.tokens == 2
    assert side.net["1h"] == pytest.approx(60.0)
    assert side.qv["1h"] == pytest.approx(1400.0)
    assert side.net["24h"] == pytest.approx(240.0)
    assert side.qv["24h"] == pytest.approx(3600.0)
    # 无人有 168h 数据 → None（不是 0）
    assert side.net["168h"] is None
    assert side.qv["168h"] is None


def test_aggregate_returns_none_when_no_member_has_flow_data():
    assert sector_flows.aggregate_side({"BTC": {"net_1h": 1.0, "qv_1h": 2.0}}, {"SOL"}) is None


def test_aggregate_counts_only_members_with_data():
    per_symbol = {"BTC": {"net_1h": 10.0, "qv_1h": 100.0}}
    side = sector_flows.aggregate_side(per_symbol, {"BTC", "ETH", "SOL"})
    assert side.tokens == 1


# ---------------- DB 列名映射 ----------------

def test_to_columns_roundtrips_through_from_row():
    per_symbol = {"BTC": {"net_24h": 500.0, "qv_24h": 5000.0}}
    sides = {
        "spot": sector_flows.aggregate_side(per_symbol, {"BTC"}),
        "swap": None,
    }
    columns = sector_flows.to_columns(sides)
    assert columns["spot_net_24h"] == pytest.approx(500.0)
    assert columns["spot_qv_24h"] == pytest.approx(5000.0)
    assert columns["spot_flow_tokens"] == 1
    assert columns["spot_net_1h"] is None
    assert columns["swap_net_24h"] is None
    assert columns["swap_flow_tokens"] is None
    assert len(columns) == 18

    class Row:
        pass
    row = Row()
    for name, value in columns.items():
        setattr(row, name, value)

    flows = sector_flows.from_row(row)
    assert flows["spot"]["net_24h"] == pytest.approx(500.0)
    assert flows["spot"]["tokens"] == 1
    assert flows["swap"] is None


def test_from_row_returns_none_side_when_all_values_missing():
    class Row:
        pass
    row = Row()
    for name in sector_flows.to_columns({"spot": None, "swap": None}):
        setattr(row, name, None)
    flows = sector_flows.from_row(row)
    assert flows == {"spot": None, "swap": None}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flows.py -k "aggregate or columns or from_row" -v`
Expected: FAIL —— `AttributeError: module 'services.sector_flows' has no attribute 'aggregate_side'`

- [ ] **Step 3: 写实现**

在 `services/sector_flows.py` 末尾追加（文件头 import 区补 `from dataclasses import dataclass`）：

```python
MARKETS = ("spot", "swap")


@dataclass
class FlowSide:
    """单市场、单板块的资金流聚合。net/qv 的键是窗口名（1h/24h/168h/720h）。"""
    tokens: int
    net: dict[str, Optional[float]]
    qv: dict[str, Optional[float]]


def aggregate_side(
    per_symbol: dict[str, dict[str, float]],
    members: set[str],
) -> Optional[FlowSide]:
    """把板块成分币的币级资金流加总。无任何成分币有数据时返回 None。

    tokens = 该市场下**实际有资金流数据**的成分币数，与涨跌口径的 token_count 可以不等
    （一个板块可能 30 个币有现货、35 个币有永续）。
    """
    matched = [sym for sym in members if sym in per_symbol]
    if not matched:
        return None

    net: dict[str, Optional[float]] = {}
    qv: dict[str, Optional[float]] = {}
    for window in FLOW_WINDOWS:
        net_total = 0.0
        qv_total = 0.0
        found = False
        for sym in matched:
            values = per_symbol[sym]
            qv_val = values.get(f"qv_{window}")
            if qv_val is None:
                continue
            found = True
            qv_total += qv_val
            net_total += values.get(f"net_{window}", 0.0)
        net[window] = round(net_total, 4) if found else None
        qv[window] = round(qv_total, 4) if found else None

    return FlowSide(tokens=len(matched), net=net, qv=qv)


def to_columns(sides: dict[str, Optional[FlowSide]]) -> dict[str, Optional[float]]:
    """{market: FlowSide|None} → 18 个 sector_returns 列的 kwargs。列名约定只此一处。"""
    out: dict[str, Optional[float]] = {}
    for market in MARKETS:
        side = sides.get(market)
        out[f"{market}_flow_tokens"] = side.tokens if side else None
        for window in FLOW_WINDOWS:
            out[f"{market}_net_{window}"] = side.net.get(window) if side else None
            out[f"{market}_qv_{window}"] = side.qv.get(window) if side else None
    return out


def from_row(row) -> dict[str, Optional[dict]]:
    """sector_returns 行 → {market: {tokens, net_1h, qv_1h, ...} | None}，供 API 序列化。

    整侧所有窗口都为空 → 该侧为 None（页面显示「—」而不是一排 0）。
    """
    out: dict[str, Optional[dict]] = {}
    for market in MARKETS:
        payload: dict[str, Optional[float]] = {
            "tokens": getattr(row, f"{market}_flow_tokens", None),
        }
        has_value = False
        for window in FLOW_WINDOWS:
            net = getattr(row, f"{market}_net_{window}", None)
            qv = getattr(row, f"{market}_qv_{window}", None)
            payload[f"net_{window}"] = net
            payload[f"qv_{window}"] = qv
            if net is not None or qv is not None:
                has_value = True
        out[market] = payload if has_value else None
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flows.py -v`
Expected: 22 passed

- [ ] **Step 5: 提交**

```bash
git add services/sector_flows.py tests/test_sector_flows.py && git commit -m "feat(sectors): 板块资金流聚合 + DB 列名映射(写读共用一处约定)"
```

---

### Task 5: scanner 接入（pivot 只加载一次 + 写库）

**Files:**
- Modify: `scanners/sector_scanner.py:215-368`（`_load_aligned_market_returns` / `_load_per_symbol_returns` / `SectorAggregate` / `SectorComputeResult` / `compute_all_sector_returns` / `SectorScanner.scan`）
- Modify: `tests/test_sector_returns.py`（跟随重构）
- Test: `tests/test_sector_flows.py`

**为什么要重构：** 资金流和涨跌要用同一份 pivot。现在 `_load_per_symbol_returns` 内部自己加载 pivot，资金流再加载一次就要多反序列化约 66MB。改成先 `_load_market_data()` 拿到 pivot，涨跌和资金流各自从它派生。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_sector_flows.py` 末尾：

```python
# ---------------- scanner 接入 ----------------
from sqlalchemy import create_engine            # noqa: E402
from sqlalchemy.orm import sessionmaker          # noqa: E402

from database import Base                        # noqa: E402
from models.sector import SectorReturn           # noqa: E402
import scanners.sector_scanner as sector_scanner  # noqa: E402


def _memory_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _wire_scanner(monkeypatch, *, spot_pivot, swap_pivot, symbols):
    """把 scanner 的 pivot 加载与板块映射都换成内存假数据。"""
    monkeypatch.setattr(
        sector_scanner, "_load_market_data",
        lambda use_pivot_cache=False: sector_scanner.MarketData(
            snapshot_at=T1, spot_pivot=spot_pivot, swap_pivot=swap_pivot),
    )
    monkeypatch.setattr(config, "all_whitelisted_cmc_categories", lambda: ["AI"])
    monkeypatch.setattr(config, "cmc_category_to_group", lambda name: "测试")
    monkeypatch.setattr(sector_scanner, "MIN_TOKENS_PER_SECTOR", 1)
    monkeypatch.setattr(
        sector_scanner.cmc_client, "load_category_to_symbols",
        lambda session: {"AI": symbols},
    )


def test_scan_writes_flow_columns(monkeypatch):
    _wire_scanner(monkeypatch, spot_pivot=_good_pivot(), swap_pivot=None,
                  symbols={"BTC", "ETH"})
    session = _memory_session()
    try:
        stats = sector_scanner.SectorScanner(session=session).scan()
        assert stats["sectors_written"] == 1
        row = session.query(SectorReturn).one()
        # 现货：BTC(+800) + ETH(−200) = +600；qv = 2000 + 400 = 2400
        assert row.spot_net_1h == pytest.approx(600.0)
        assert row.spot_qv_1h == pytest.approx(2400.0)
        assert row.spot_flow_tokens == 2
        # 无永续 pivot → 整侧为空
        assert row.swap_net_1h is None
        assert row.swap_flow_tokens is None
        # 涨跌照常
        assert row.ret_1h is not None
    finally:
        session.close()


def test_scan_nulls_flows_but_keeps_returns_when_gate_fails(monkeypatch):
    """勾稽门失败：资金流全空、涨跌照常、失败原因进 result。"""
    broken = _good_pivot()
    del broken[sector_flows.TAKER_BUY_KEY]
    _wire_scanner(monkeypatch, spot_pivot=broken, swap_pivot=None, symbols={"BTC", "ETH"})
    session = _memory_session()
    try:
        result = sector_scanner.compute_all_sector_returns(session)
        assert "spot" in result.flow_gate_failures
        assert "缺字段" in result.flow_gate_failures["spot"]
        aggregate = result.aggregates[0]
        assert aggregate.ret_1h is not None
        assert aggregate.flows["spot"] is None
    finally:
        session.close()


def test_gate_failure_on_one_market_does_not_affect_the_other(monkeypatch):
    broken_spot = _good_pivot()
    del broken_spot[sector_flows.TAKER_BUY_KEY]
    _wire_scanner(monkeypatch, spot_pivot=broken_spot, swap_pivot=_good_pivot(),
                  symbols={"BTC", "ETH"})
    session = _memory_session()
    try:
        result = sector_scanner.compute_all_sector_returns(session)
        assert set(result.flow_gate_failures) == {"spot"}
        aggregate = result.aggregates[0]
        assert aggregate.flows["spot"] is None
        assert aggregate.flows["swap"].net["1h"] == pytest.approx(600.0)
    finally:
        session.close()


def test_legacy_pivot_without_flow_fields_degrades_quietly(monkeypatch):
    """服务器补丁没上/已回滚时，涨跌照常产出，资金流全空 —— 本项目可先于补丁上线。"""
    legacy = {"close": _frame(["BTCUSDT", "ETHUSDT"],
                              [(T0, [100.0, 10.0]), (T1, [110.0, 11.0])])}
    _wire_scanner(monkeypatch, spot_pivot=legacy, swap_pivot=None, symbols={"BTC", "ETH"})
    session = _memory_session()
    try:
        stats = sector_scanner.SectorScanner(session=session).scan()
        assert stats["sectors_written"] == 1
        row = session.query(SectorReturn).one()
        assert row.ret_1h is not None
        assert row.spot_net_24h is None
    finally:
        session.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flows.py -k scan -v`
Expected: FAIL —— `AttributeError: module 'scanners.sector_scanner' has no attribute '_load_market_data'`

- [ ] **Step 3: 用 MarketData 替换两个加载函数**

`scanners/sector_scanner.py` 第 215-287 行（`_load_aligned_market_returns` 与 `_load_per_symbol_returns` 两个函数整体）替换为：

```python
@dataclass
class MarketData:
    """一次 pivot 加载的产物 —— 涨跌与资金流共用，避免重复反序列化 30MB pkl。

    snapshot_at = 现货/永续最新 bar 的**较早者**，两个市场对齐到同一时刻，
    免得一个板块快照里混两个市场的不同时间。
    """
    snapshot_at: Optional[datetime]
    spot_pivot: Optional[dict]
    swap_pivot: Optional[dict]

    def pivot(self, market: str) -> Optional[dict]:
        return self.spot_pivot if market == "spot" else self.swap_pivot


def _close_of(pivot: Optional[dict]):
    return None if pivot is None else pivot.get("close")


def _load_market_data(*, use_pivot_cache: bool = False) -> MarketData:
    """加载 spot + swap pivot 并对齐快照时刻。

    Args:
        use_pivot_cache: True 时调 sector_service._load_pivot_cached（mtime 缓存，
                         供 live 读取路径用以避免每次反序列化）；False 时调
                         _load_pivot（无缓存，更适合定时 scanner，每次都用最新文件）
    """
    if use_pivot_cache:
        # 延迟 import 避免循环 (sector_service 导入了 sector_scanner)
        from services.sector_service import _load_pivot_cached as _loader
    else:
        _loader = _load_pivot

    spot_pivot = _loader("spot")
    swap_pivot = _loader("swap")

    latest_times = [
        ts for ts in (
            _latest_snapshot_for_close(_close_of(spot_pivot)) if _close_of(spot_pivot) is not None else None,
            _latest_snapshot_for_close(_close_of(swap_pivot)) if _close_of(swap_pivot) is not None else None,
        )
        if ts is not None
    ]
    snapshot_at = min(latest_times) if len(latest_times) > 1 else (
        latest_times[0] if latest_times else None)
    return MarketData(snapshot_at=snapshot_at, spot_pivot=spot_pivot, swap_pivot=swap_pivot)


def _per_symbol_returns_from(market: MarketData) -> dict[str, dict[str, float]]:
    """MarketData → {规范化 symbol: {ret_1h: ...}}。现货优先（后写覆盖永续）。"""
    if market.snapshot_at is None:
        return {}

    sym_to_returns: dict[str, dict[str, float]] = {}
    # 顺序敏感：先永续后现货，现货有的就盖掉永续（现货价格更干净）
    for market_name in ("swap", "spot"):
        close = _close_of(market.pivot(market_name))
        if close is None:
            continue
        _, returns = _compute_returns_for_close(close, as_of=market.snapshot_at)
        for col, rets in returns.items():
            nsym = normalize_pivot_symbol(col)
            if nsym:
                sym_to_returns[nsym] = rets
    return sym_to_returns
```

- [ ] **Step 4: 聚合结果加资金流字段**

同文件 `SectorAggregate`（第 189-202 行）末尾加一个字段，`SectorComputeResult`（第 205-212 行）加一个字段：

`SectorAggregate` 的 `ret_720h_median: Optional[float]` 之后追加：

```python
    # {market: FlowSide|None} —— 勾稽门没过或该市场无数据时该侧为 None
    flows: dict = field(default_factory=dict)
```

`SectorComputeResult` 的 `skipped_reason` 之前追加：

```python
    # {market: 中文失败原因} —— 为空表示两个市场的勾稽门都通过
    flow_gate_failures: dict[str, str] = field(default_factory=dict)
```

同时把文件头第 21 行的 `from dataclasses import dataclass` 改为：

```python
from dataclasses import dataclass, field
```

- [ ] **Step 5: 改 compute_all_sector_returns**

同文件第 290-368 行（`compute_all_sector_returns` 整体）替换为：

```python
def compute_all_sector_returns(
    session: Session, *, use_pivot_cache: bool = False
) -> SectorComputeResult:
    """对当前本地 pivot + DB 板块映射做完整的板块聚合计算（不写 DB）。

    被两边共用:
    - SectorScanner.scan() 调，拿到结果后写 DB
    - sector_service.get_leaderboard() 调，拿到结果直接序列化给前端
    保证两者用同一份 pivot 算出同一个 snapshot_at + 同一组聚合数。

    资金流（2026-08-07）与涨跌共用这一份 pivot；每个市场先过勾稽门，
    没过的市场整轮资金流写 None，涨跌不受任何影响。
    """
    from services import sector_flows  # 延迟 import 避免循环

    market_data = _load_market_data(use_pivot_cache=use_pivot_cache)
    snapshot_at = market_data.snapshot_at
    sym_to_returns = _per_symbol_returns_from(market_data)

    if snapshot_at is None:
        return SectorComputeResult(
            snapshot_at=None, aggregates=[], active_symbols=0,
            considered_cats=0, skipped_thin=[], skipped_reason="no_pivot",
        )
    if not sym_to_returns:
        return SectorComputeResult(
            snapshot_at=snapshot_at, aggregates=[], active_symbols=0,
            considered_cats=0, skipped_thin=[], skipped_reason="no_symbols",
        )

    # 资金流：每个市场各自过闸，一边失败不连坐另一边
    flow_gate_failures: dict[str, str] = {}
    flows_by_market: dict[str, dict[str, dict[str, float]]] = {}
    for market_name in sector_flows.MARKETS:
        pivot = market_data.pivot(market_name)
        if pivot is None:
            continue  # 该市场 pivot 本来就没拉到，不算勾稽失败（涨跌侧同样没有）
        reason = sector_flows.check_flow_gate(pivot)
        if reason:
            flow_gate_failures[market_name] = reason
            logger.warning("资金流勾稽门未通过 market={}: {}", market_name, reason)
            continue
        flows_by_market[market_name] = sector_flows.per_symbol_flows(
            pivot, as_of=snapshot_at)

    cat_to_syms = cmc_client.load_category_to_symbols(session)
    if not cat_to_syms:
        return SectorComputeResult(
            snapshot_at=snapshot_at, aggregates=[], active_symbols=len(sym_to_returns),
            considered_cats=0, skipped_thin=[], skipped_reason="no_mapping",
            flow_gate_failures=flow_gate_failures,
        )

    whitelist = set(config.all_whitelisted_cmc_categories())
    aggregates: list[SectorAggregate] = []
    considered_cats = 0
    skipped_thin: list[str] = []

    for category, cmc_symbols in sorted(cat_to_syms.items()):
        if category not in whitelist:
            continue
        considered_cats += 1
        matched = cmc_symbols & sym_to_returns.keys()
        if len(matched) < MIN_TOKENS_PER_SECTOR:
            skipped_thin.append(f"{category}({len(matched)})")
            continue
        agg: dict[str, list[float]] = {k: [] for k in RETURN_LOOKBACKS}
        for sym in matched:
            rets = sym_to_returns[sym]
            for ret_name in RETURN_LOOKBACKS:
                if ret_name in rets:
                    agg[ret_name].append(rets[ret_name])
        means: dict[str, Optional[float]] = {
            ret_name: (round(sum(values) / len(values), 4) if values else None)
            for ret_name, values in agg.items()
        }
        medians: dict[str, Optional[float]] = {
            ret_name: (round(float(median(values)), 4) if values else None)
            for ret_name, values in agg.items()
        }
        # 资金流的成员集合按各市场宽表实际有的列取，与涨跌口径的 matched 无关
        flows = {
            market_name: sector_flows.aggregate_side(
                flows_by_market.get(market_name, {}), cmc_symbols)
            for market_name in sector_flows.MARKETS
        }
        aggregates.append(SectorAggregate(
            category=category,
            group_name=config.cmc_category_to_group(category),
            token_count=len(matched),
            ret_1h=means["ret_1h"],
            ret_24h=means["ret_24h"],
            ret_168h=means["ret_168h"],
            ret_720h=means["ret_720h"],
            ret_1h_median=medians["ret_1h"],
            ret_24h_median=medians["ret_24h"],
            ret_168h_median=medians["ret_168h"],
            ret_720h_median=medians["ret_720h"],
            flows=flows,
        ))

    return SectorComputeResult(
        snapshot_at=snapshot_at,
        aggregates=aggregates,
        active_symbols=len(sym_to_returns),
        considered_cats=considered_cats,
        skipped_thin=skipped_thin,
        flow_gate_failures=flow_gate_failures,
        skipped_reason=None,
    )
```

- [ ] **Step 6: 写库时带上 18 列**

同文件 `SectorScanner.scan()` 里构造 `SectorReturn(...)` 的列表推导（原第 392-408 行），把 `ret_720h_median=a.ret_720h_median,` 之后加一行展开：

```python
            from services import sector_flows  # 延迟 import 避免循环

            rows = [
                SectorReturn(
                    snapshot_at=result.snapshot_at,
                    category=a.category,
                    group_name=a.group_name,
                    token_count=a.token_count,
                    ret_1h=a.ret_1h,
                    ret_24h=a.ret_24h,
                    ret_168h=a.ret_168h,
                    ret_720h=a.ret_720h,
                    ret_1h_median=a.ret_1h_median,
                    ret_24h_median=a.ret_24h_median,
                    ret_168h_median=a.ret_168h_median,
                    ret_720h_median=a.ret_720h_median,
                    **sector_flows.to_columns(a.flows),
                )
                for a in result.aggregates
            ]
```

并在 `return {...}` 的 stats dict 里加一项，便于日志与后续告警：

```python
            return {
                "snapshot_at": result.snapshot_at,
                "sectors_written": len(rows),
                "considered_cats": result.considered_cats,
                "skipped_thin": len(result.skipped_thin),
                "active_symbols": result.active_symbols,
                "flow_gate_failures": dict(result.flow_gate_failures),
            }
```

- [ ] **Step 7: 更新既有测试跟随重构**

`tests/test_sector_returns.py` 第 23-49 行的第一个测试改为：

```python
def test_per_symbol_returns_use_common_spot_swap_snapshot(monkeypatch):
    spot = _pivot(
        ["BTCUSDT"],
        [
            (datetime(2026, 1, 1, 0, 0), [100.0]),
            (datetime(2026, 1, 1, 1, 0), [110.0]),
        ],
    )
    swap = _pivot(
        ["BTCUSDT", "ETHUSDT"],
        [
            (datetime(2026, 1, 1, 0, 0), [100.0, 100.0]),
            (datetime(2026, 1, 1, 1, 0), [120.0, 105.0]),
            (datetime(2026, 1, 1, 2, 0), [240.0, 110.0]),
        ],
    )

    def load_pivot(market: str):
        return {"spot": spot, "swap": swap}[market]

    monkeypatch.setattr(sector_scanner, "_load_pivot", load_pivot)

    market_data = sector_scanner._load_market_data()
    returns = sector_scanner._per_symbol_returns_from(market_data)

    assert market_data.snapshot_at == datetime(2026, 1, 1, 1, 0)
    assert returns["BTC"]["ret_1h"] == 10.0
    assert returns["ETH"]["ret_1h"] == 5.0
```

第 68-72 行的两处 monkeypatch 改为（把加载与派生分开打桩，两个 pivot 都给 None 让资金流自然为空）：

```python
    monkeypatch.setattr(
        sector_scanner,
        "_load_market_data",
        lambda use_pivot_cache=False: sector_scanner.MarketData(
            snapshot_at=datetime(2026, 1, 1, 1, 0), spot_pivot=None, swap_pivot=None),
    )
    monkeypatch.setattr(
        sector_scanner,
        "_per_symbol_returns_from",
        lambda market: returns,
    )
```

- [ ] **Step 8: 跑测试确认全绿**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flows.py tests/test_sector_returns.py tests/test_sector_alerts.py -v`
Expected: 全部 PASS（test_sector_flows 26 passed）

- [ ] **Step 9: 提交**

```bash
git add scanners/sector_scanner.py tests/test_sector_flows.py tests/test_sector_returns.py && git commit -m "feat(sectors): scanner 接资金流(pivot 单次加载 + 过闸 + 写 18 列)"
```

---

### Task 6: 勾稽失败告警

**Files:**
- Create: `services/sector_flow_monitoring.py`
- Modify: `scanners/sector_scanner.py`（`SectorScanner.scan()` 末尾调用）
- Test: `tests/test_sector_flow_alerts.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_sector_flow_alerts.py`：

```python
"""资金流勾稽门失败告警：按市场去重 + 冷却窗内只推一次 + 发送失败不占冷却。"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.alert_log import AlertLog
from services import sector_flow_monitoring


NOW = datetime(2026, 8, 7, 10, 0)


class FakeChannel:
    name = "wechat_work"

    def __init__(self, delivered: bool = True):
        self.sent: list[tuple[str, str]] = []
        self._delivered = delivered

    def send(self, title: str, content: str) -> bool:
        self.sent.append((title, content))
        return self._delivered


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_sends_one_alert_per_failing_market():
    session, channel = _session(), FakeChannel()
    try:
        sent = sector_flow_monitoring.alert_flow_gate_failures(
            {"spot": "缺字段: taker_buy_quote_asset_volume", "swap": "恒等式违规占比 5%"},
            session=session, channel=channel, now=NOW,
        )
        assert len(sent) == 2
        assert len(channel.sent) == 2
        titles = " ".join(title for title, _ in channel.sent)
        assert "spot" in titles and "swap" in titles
        # 正文要带失败原因，人一眼能判是补丁没上还是数据坏了
        assert any("taker_buy_quote_asset_volume" in content for _, content in channel.sent)
    finally:
        session.close()


def test_no_alert_when_all_gates_pass():
    session, channel = _session(), FakeChannel()
    try:
        assert sector_flow_monitoring.alert_flow_gate_failures(
            {}, session=session, channel=channel, now=NOW) == []
        assert channel.sent == []
    finally:
        session.close()


def test_second_failure_within_cooldown_is_suppressed(monkeypatch):
    monkeypatch.setattr(config, "FLOW_GATE_ALERT_COOLDOWN_MINUTES", 60)
    session, channel = _session(), FakeChannel()
    try:
        failures = {"spot": "缺字段: taker_buy_quote_asset_volume"}
        sector_flow_monitoring.alert_flow_gate_failures(
            failures, session=session, channel=channel, now=NOW)
        again = sector_flow_monitoring.alert_flow_gate_failures(
            failures, session=session, channel=channel, now=NOW + timedelta(minutes=30))
        assert again == []
        assert len(channel.sent) == 1
    finally:
        session.close()


def test_alert_resumes_after_cooldown(monkeypatch):
    monkeypatch.setattr(config, "FLOW_GATE_ALERT_COOLDOWN_MINUTES", 60)
    session, channel = _session(), FakeChannel()
    try:
        failures = {"spot": "缺字段: taker_buy_quote_asset_volume"}
        sector_flow_monitoring.alert_flow_gate_failures(
            failures, session=session, channel=channel, now=NOW)
        later = sector_flow_monitoring.alert_flow_gate_failures(
            failures, session=session, channel=channel, now=NOW + timedelta(minutes=61))
        assert len(later) == 1
        assert len(channel.sent) == 2
    finally:
        session.close()


def test_failed_delivery_does_not_consume_cooldown(monkeypatch):
    monkeypatch.setattr(config, "FLOW_GATE_ALERT_COOLDOWN_MINUTES", 60)
    session = _session()
    failing, working = FakeChannel(delivered=False), FakeChannel(delivered=True)
    try:
        failures = {"spot": "缺字段: taker_buy_quote_asset_volume"}
        sector_flow_monitoring.alert_flow_gate_failures(
            failures, session=session, channel=failing, now=NOW)
        retry = sector_flow_monitoring.alert_flow_gate_failures(
            failures, session=session, channel=working, now=NOW + timedelta(minutes=5))
        assert len(retry) == 1
        assert len(working.sent) == 1
    finally:
        session.close()


def test_writes_alert_log_rows():
    session, channel = _session(), FakeChannel()
    try:
        sector_flow_monitoring.alert_flow_gate_failures(
            {"spot": "缺字段: taker_buy_quote_asset_volume"},
            session=session, channel=channel, now=NOW)
        logs = session.query(AlertLog).all()
        assert len(logs) == 1
        assert logs[0].rule_name == sector_flow_monitoring.RULE_NAME
        assert logs[0].delivered is True
    finally:
        session.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flow_alerts.py -v`
Expected: FAIL —— `ImportError: cannot import name 'sector_flow_monitoring' from 'services'`

- [ ] **Step 3: 写实现**

新建 `services/sector_flow_monitoring.py`：

```python
# -*- coding: utf-8 -*-
"""板块资金流勾稽门失败告警（2026-08-07 净资金流入 spec §5.2）。

资金流数据来自数据服务器的 BMAC 宽表补丁。补丁被 BMAC 升级覆盖、或宽表数据损坏时，
勾稽门会把该市场的资金流整轮作废（页面显示「—」）。页面上的「—」很安静，没人会注意到，
所以这里主动推一条企业微信 —— 判定与 scanner 同源，直接吃 compute 结果里的失败原因。

结构仿 services/price_source_monitoring.py：marker 去重 + 冷却 + AlertLog 落库，
且**发送失败不占冷却**（下一轮继续重试）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger

import config
from alerts.channels.wechat_work import WeChatWorkChannel
from database import SessionLocal
from models.alert_log import AlertLog

RULE_NAME = "sector_flow_gate"


def alert_flow_gate_failures(
    failures: dict[str, str],
    *,
    session=None,
    channel=None,
    now: datetime | None = None,
) -> list[dict]:
    """对每个勾稽失败的市场推一条告警（带冷却去重）；返回本次实际发出的条目。"""
    if not failures:
        return []

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    own_session = session is None
    session = session or SessionLocal()
    channel = channel or WeChatWorkChannel()
    sent: list[dict] = []
    try:
        for market in sorted(failures):
            reason = failures[market]
            marker = f"sector-flow:gate:{market}"
            if _recently_delivered(session, marker, now):
                continue
            title = f"板块资金流数据异常：{market}"
            content = (
                f"{market} 市场的资金流勾稽未通过，本轮该市场净流入已作废（页面显示「—」）。\n"
                f"原因：{reason}\n"
                f"板块涨跌不受影响。常见成因：数据服务器 BMAC 升级覆盖了宽表补丁，"
                f"或宽表本轮写入损坏。处理见 "
                f"docs/superpowers/specs/2026-08-07-sector-net-inflow-design.md §10。"
            )
            delivered = channel.send(title, content)
            session.add(AlertLog(
                timestamp=now,
                rule_name=RULE_NAME,
                message=f"{marker}\n{content}"[:8000],
                channel=getattr(channel, "name", "wechat_work"),
                delivered=delivered,
            ))
            sent.append({"market": market, "reason": reason,
                         "marker": marker, "delivered": delivered})
        if own_session:
            session.commit()
        else:
            session.flush()
    except Exception:
        if own_session:
            session.rollback()
        logger.exception("sector flow gate alert failed")
        raise
    finally:
        if own_session:
            session.close()
    return sent


def _recently_delivered(session, marker: str, now: datetime) -> bool:
    """冷却窗内**成功送达过**同一 marker 才算数：发送失败的不占冷却，下一轮会重试。"""
    cooldown = max(1, int(getattr(config, "FLOW_GATE_ALERT_COOLDOWN_MINUTES", 60)))
    cutoff = now - timedelta(minutes=cooldown)
    rows = (
        session.query(AlertLog.message)
        .filter(
            AlertLog.rule_name == RULE_NAME,
            AlertLog.timestamp >= cutoff,
            AlertLog.delivered == True,   # noqa: E712 - SQLAlchemy 列比较
        )
        .all()
    )
    return any(marker in (message or "").splitlines() for (message,) in rows)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flow_alerts.py -v`
Expected: 6 passed

- [ ] **Step 5: 在 scanner 里接上告警**

`scanners/sector_scanner.py` 的 `SectorScanner.scan()` 中，`logger.info("sector_scan 完成: ...")` 那段之后、`return {...}` 之前插入：

```python
            # 勾稽失败主动推送（页面上的「—」太安静，没人会注意到）
            if result.flow_gate_failures:
                try:
                    from services import sector_flow_monitoring
                    sector_flow_monitoring.alert_flow_gate_failures(
                        result.flow_gate_failures)
                except Exception as exc:
                    # 告警失败绝不能拖垮扫描本身
                    logger.warning("资金流勾稽告警推送失败: {}", exc)
```

- [ ] **Step 6: 挡住 Task 5 测试里的真实推送**

Task 5 的 `test_legacy_pivot_without_flow_fields_degrades_quietly` 会走到勾稽失败分支 ——
Step 5 接上告警后，它会真的去建 `SessionLocal()` 和 `WeChatWorkChannel()` 发企业微信。
在 `tests/test_sector_flows.py` 的 `_wire_scanner` 里补一行打桩（放在函数体最后）：

```python
    monkeypatch.setattr(
        "services.sector_flow_monitoring.alert_flow_gate_failures",
        lambda failures, **kwargs: [],
    )
```

- [ ] **Step 7: 跑全部板块测试确认没改坏**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flows.py tests/test_sector_returns.py tests/test_sector_alerts.py tests/test_sector_flow_alerts.py -v`
Expected: 全部 PASS

- [ ] **Step 8: 提交**

```bash
git add services/sector_flow_monitoring.py scanners/sector_scanner.py tests/test_sector_flow_alerts.py && git commit -m "feat(sectors): 资金流勾稽失败推企业微信(marker 去重+冷却)"
```

---

### Task 7: API 契约 + 榜单读路径

**Files:**
- Modify: `schemas/sectors.py`
- Modify: `services/sector_service.py:118-136`
- Test: `tests/test_sector_flows.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_sector_flows.py` 末尾：

```python
# ---------------- 榜单读路径 ----------------
from services import sector_service  # noqa: E402


def test_leaderboard_serializes_flows_from_db_columns():
    session = _memory_session()
    try:
        session.add(SectorReturn(
            snapshot_at=T1, category="AI", group_name="测试", token_count=12,
            ret_24h=3.0, ret_24h_median=2.0,
            spot_net_24h=500.0, spot_qv_24h=5000.0, spot_flow_tokens=11,
        ))
        session.commit()

        response = sector_service.get_leaderboard(session)
        row = response.rows[0]
        assert row.flows is not None
        assert row.flows.spot.net_24h == pytest.approx(500.0)
        assert row.flows.spot.qv_24h == pytest.approx(5000.0)
        assert row.flows.spot.tokens == 11
        assert row.flows.spot.net_1h is None
        assert row.flows.swap is None
    finally:
        session.close()


def test_leaderboard_row_without_flow_data_has_both_sides_none():
    session = _memory_session()
    try:
        session.add(SectorReturn(
            snapshot_at=T1, category="AI", group_name="测试", token_count=12, ret_24h=3.0))
        session.commit()
        row = sector_service.get_leaderboard(session).rows[0]
        assert row.flows.spot is None
        assert row.flows.swap is None
    finally:
        session.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flows.py -k leaderboard -v`
Expected: FAIL —— `AttributeError: 'SectorLeaderboardRow' object has no attribute 'flows'`

- [ ] **Step 3: 加 schema**

`schemas/sectors.py` 的 `SectorLeaderboardRow` 之前插入：

```python
class SectorFlowSide(BaseModel):
    """单市场（现货 or 永续）的资金流。绝对额单位 USDT；强度比率由前端用 net/qv 现算。"""
    tokens: int | None = None      # 该市场下有资金流数据的成分币数（板块行才有，币行为 None）
    net_1h: float | None = None
    net_24h: float | None = None
    net_168h: float | None = None
    net_720h: float | None = None
    qv_1h: float | None = None
    qv_24h: float | None = None
    qv_168h: float | None = None
    qv_720h: float | None = None


class SectorFlows(BaseModel):
    """现货与永续永不混加，各自一侧；该侧无数据时为 null。"""
    spot: SectorFlowSide | None = None
    swap: SectorFlowSide | None = None
```

并给 `SectorLeaderboardRow` 与 `SectorTokenRow` 各加一行（放在各自最后一个字段之后）：

```python
    flows: SectorFlows | None = None
```

- [ ] **Step 4: 榜单读路径接上**

`services/sector_service.py` 顶部 import 区把 `from schemas.sectors import (...)` 补上两个新模型：

```python
from schemas.sectors import (
    SectorFlows,
    SectorFlowSide,
    SectorLeaderboardResponse,
    SectorLeaderboardRow,
    SectorTokenRow,
    SectorTokensResponse,
)
```

同文件 import 区补：

```python
from services import remote_fs, sector_flows
```

（原为 `from services import remote_fs`）

在 `get_leaderboard` 之前加一个共用的转换函数：

```python
def _flows_of(source) -> SectorFlows:
    """sector_returns 行 / 币级现算 dict → API 的 flows 结构。列名约定在 sector_flows。"""
    payload = sector_flows.from_row(source)
    return SectorFlows(
        spot=SectorFlowSide(**payload["spot"]) if payload["spot"] else None,
        swap=SectorFlowSide(**payload["swap"]) if payload["swap"] else None,
    )
```

并在 `get_leaderboard` 构造 `SectorLeaderboardRow(...)` 的地方，`ret_720h_median=r.ret_720h_median,` 之后加一行：

```python
                flows=_flows_of(r),
```

- [ ] **Step 5: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flows.py -v`
Expected: 28 passed

- [ ] **Step 6: 提交**

```bash
git add schemas/sectors.py services/sector_service.py tests/test_sector_flows.py && git commit -m "feat(sectors): API 加 flows 字段 + 榜单读 DB 资金流列"
```

---

### Task 8: 成分币钻取的资金流（读时现算）

**Files:**
- Modify: `services/sector_service.py:142-240`（`get_sector_tokens`）
- Test: `tests/test_sector_flows.py`

顺带消掉重复：`get_sector_tokens` 自己写了一遍 spot/swap 对齐逻辑，改用 Task 5 的 `_load_market_data`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_sector_flows.py` 末尾：

```python
# ---------------- 成分币钻取 ----------------
from models.sector import CmcSymbolCategory  # noqa: E402


def test_token_row_carries_both_market_flows(monkeypatch):
    """一行币同时挂现货与永续两侧资金流，与它价格取自哪个市场无关。"""
    session = _memory_session()
    try:
        session.add(CmcSymbolCategory(symbol="BTC", category="AI"))
        session.commit()
        monkeypatch.setattr(config, "cmc_category_to_group", lambda name: "测试")
        # 注意：要打 sector_service 上的名字 —— 它是 from ... import 进来的绑定，
        # 打 sector_scanner 上的原名对已绑定的引用无效。
        monkeypatch.setattr(
            sector_service, "_load_market_data",
            lambda use_pivot_cache=False: sector_scanner.MarketData(
                snapshot_at=T1, spot_pivot=_good_pivot(), swap_pivot=_good_pivot()),
        )

        response = sector_service.get_sector_tokens(session, "AI")
        row = next(t for t in response.tokens if t.symbol == "BTC")
        assert row.market == "spot"           # 价格仍是现货优先
        assert row.flows.spot.net_1h == pytest.approx(800.0)
        assert row.flows.swap.net_1h == pytest.approx(800.0)
        assert row.flows.spot.tokens is None  # 币行不带覆盖币数
    finally:
        session.close()


def test_token_row_flow_side_is_none_when_market_absent(monkeypatch):
    session = _memory_session()
    try:
        session.add(CmcSymbolCategory(symbol="BTC", category="AI"))
        session.commit()
        monkeypatch.setattr(config, "cmc_category_to_group", lambda name: "测试")
        # 注意：要打 sector_service 上的名字 —— 它是 from ... import 进来的绑定，
        # 打 sector_scanner 上的原名对已绑定的引用无效。
        monkeypatch.setattr(
            sector_service, "_load_market_data",
            lambda use_pivot_cache=False: sector_scanner.MarketData(
                snapshot_at=T1, spot_pivot=_good_pivot(), swap_pivot=None),
        )
        row = sector_service.get_sector_tokens(session, "AI").tokens[0]
        assert row.flows.spot is not None
        assert row.flows.swap is None
    finally:
        session.close()


def test_token_flows_empty_when_gate_fails(monkeypatch):
    session = _memory_session()
    try:
        session.add(CmcSymbolCategory(symbol="BTC", category="AI"))
        session.commit()
        broken = _good_pivot()
        del broken[sector_flows.TAKER_BUY_KEY]
        monkeypatch.setattr(config, "cmc_category_to_group", lambda name: "测试")
        # 注意：要打 sector_service 上的名字 —— 它是 from ... import 进来的绑定，
        # 打 sector_scanner 上的原名对已绑定的引用无效。
        monkeypatch.setattr(
            sector_service, "_load_market_data",
            lambda use_pivot_cache=False: sector_scanner.MarketData(
                snapshot_at=T1, spot_pivot=broken, swap_pivot=None),
        )
        row = sector_service.get_sector_tokens(session, "AI").tokens[0]
        assert row.ret_1h is not None      # 涨跌照常
        assert row.flows.spot is None      # 资金流作废
    finally:
        session.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flows.py -k token_ -v`
Expected: FAIL —— `AttributeError: 'NoneType' object has no attribute 'spot'`（flows 恒为 None）

- [ ] **Step 3: 重写 get_sector_tokens**

`services/sector_service.py` 第 142-240 行（`get_sector_tokens` 整体）替换为：

```python
def get_sector_tokens(session: Session, category: str) -> SectorTokensResponse:
    """对一个板块返回其下所有 symbol 当前的涨跌 + 两个市场的资金流。

    步骤：
    1. 查 cmc_symbol_categories 拿这个板块的 symbol 集合
    2. 加载两份 pivot（spot + swap），对齐到同一 snapshot（走 scanner 的 MarketData）
    3. 涨跌：现货优先匹配，缺现货才用永续（一行一个 market）
    4. 资金流：现货与永续各自独立算，同一行同时挂两侧（与 market 字段无关）
    5. 返回按 24h 涨跌排好序的列表
    """
    cmc_symbols = {
        row[0]
        for row in session.execute(
            select(CmcSymbolCategory.symbol).where(CmcSymbolCategory.category == category)
        ).all()
    }

    if not cmc_symbols:
        return SectorTokensResponse(category=category, group=None, snapshot_at=None, tokens=[])

    market_data = _load_market_data(use_pivot_cache=True)
    snapshot_at = market_data.snapshot_at
    if snapshot_at is None:
        return SectorTokensResponse(
            category=category,
            group=config.cmc_category_to_group(category),
            snapshot_at=None,
            tokens=[],
        )

    # 涨跌：两个市场各自算（列名 → 涨跌），现货优先
    returns_by_market: dict[str, dict[str, dict[str, float]]] = {}
    for market_name in sector_flows.MARKETS:
        close = _close_of(market_data.pivot(market_name))
        if close is None:
            continue
        _, returns_by_market[market_name] = _compute_returns_for_close(
            close, as_of=snapshot_at)

    # 资金流：过闸后按规范化 symbol 现算（两侧独立）
    flows_by_market: dict[str, dict[str, dict[str, float]]] = {}
    for market_name in sector_flows.MARKETS:
        pivot = market_data.pivot(market_name)
        if pivot is None or sector_flows.check_flow_gate(pivot):
            continue
        flows_by_market[market_name] = sector_flows.per_symbol_flows(
            pivot, as_of=snapshot_at)

    def _token_flows(nsym: str) -> SectorFlows:
        sides: dict[str, SectorFlowSide | None] = {}
        for market_name in sector_flows.MARKETS:
            values = flows_by_market.get(market_name, {}).get(nsym)
            sides[market_name] = SectorFlowSide(**values) if values else None
        return SectorFlows(spot=sides["spot"], swap=sides["swap"])

    rows: list[SectorTokenRow] = []
    seen_normalized: set[str] = set()
    # spot 优先（先扫 spot，得到的 base sym 标记 seen，swap 里再有同名 sym 就跳过）
    for market_name in ("spot", "swap"):
        for col, rets in returns_by_market.get(market_name, {}).items():
            nsym = normalize_pivot_symbol(col)
            if not nsym or nsym not in cmc_symbols or nsym in seen_normalized:
                continue
            seen_normalized.add(nsym)
            rows.append(SectorTokenRow(
                symbol=nsym,
                binance_symbol=col,
                market=market_name,
                ret_1h=rets.get("ret_1h"),
                ret_24h=rets.get("ret_24h"),
                ret_168h=rets.get("ret_168h"),
                ret_720h=rets.get("ret_720h"),
                flows=_token_flows(nsym),
            ))

    # 按 24h 降序，NaN 末尾
    def _sort_key(r: SectorTokenRow) -> tuple[int, float]:
        if r.ret_24h is None:
            return (1, 0.0)
        return (0, -r.ret_24h)

    rows.sort(key=_sort_key)

    return SectorTokensResponse(
        category=category,
        group=config.cmc_category_to_group(category),
        snapshot_at=timestamp_pair(snapshot_at) if snapshot_at else None,
        tokens=rows,
    )
```

同文件顶部把 scanner 的 import 整块替换为（原第 24-30 行）。改写后
`_latest_snapshot_for_close` 与 `RETURN_LOOKBACKS` 在本文件已无人使用，一并删掉：

```python
from scanners.sector_scanner import (
    MIN_TOKENS_PER_SECTOR,
    _close_of,
    _compute_returns_for_close,
    _load_market_data,
    normalize_pivot_symbol,
)
```

> `_load_pivot_cached`（本文件定义）仍要保留 —— scanner 的 `_load_market_data(use_pivot_cache=True)`
> 会延迟 import 它，删了会在运行时炸。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_sector_flows.py -v`
Expected: 31 passed

- [ ] **Step 5: 跑后端全量确认没改坏别的**

Run: `D:\anaconda\python.exe -m pytest -q`
Expected: 全绿（无 failed）

- [ ] **Step 6: 提交**

```bash
git add services/sector_service.py tests/test_sector_flows.py && git commit -m "feat(sectors): 成分币钻取带两侧资金流(顺带消掉重复的快照对齐逻辑)"
```

---

### Task 9: 前端格式化纯函数

**Files:**
- Create: `frontend/src/pages/sectorFlowFormat.ts`
- Modify: `frontend/src/api/types.ts:611-645`
- Test: `frontend/src/pages/sectorFlowFormat.test.ts`（新建）

- [ ] **Step 1: 加前端类型**

`frontend/src/api/types.ts` 第 611 行 `export type SectorLeaderboardResponse` 之前插入：

```ts
export type SectorFlowSide = {
  tokens: number | null;
  net_1h: number | null;
  net_24h: number | null;
  net_168h: number | null;
  net_720h: number | null;
  qv_1h: number | null;
  qv_24h: number | null;
  qv_168h: number | null;
  qv_720h: number | null;
};

export type SectorFlows = {
  spot: SectorFlowSide | null;
  swap: SectorFlowSide | null;
};
```

给 `SectorLeaderboardRow` 与 `SectorTokenRow` 各加一行（放在各自最后一个字段之后）：

```ts
  flows: SectorFlows | null;
```

- [ ] **Step 2: 写失败测试**

新建 `frontend/src/pages/sectorFlowFormat.test.ts`：

```ts
import { describe, expect, it } from "vitest";
import type { SectorFlowSide } from "../api/types";
import {
  FLOW_SORTS,
  flowSortValue,
  flowStrength,
  fmtMoney,
  fmtStrength,
} from "./sectorFlowFormat";

const side = (over: Partial<SectorFlowSide> = {}): SectorFlowSide => ({
  tokens: 10,
  net_1h: null, net_24h: null, net_168h: null, net_720h: null,
  qv_1h: null, qv_24h: null, qv_168h: null, qv_720h: null,
  ...over,
});

describe("fmtMoney", () => {
  it("缩写到 K/M/B 并带正负号", () => {
    expect(fmtMoney(980_000)).toBe("+$980.0K");
    expect(fmtMoney(46_200_000)).toBe("+$46.2M");
    expect(fmtMoney(-1_240_000_000)).toBe("-$1.2B");
  });

  it("小额不缩写", () => {
    expect(fmtMoney(842)).toBe("+$842");
    expect(fmtMoney(-30)).toBe("-$30");
  });

  it("零与缺失都显示破折号", () => {
    expect(fmtMoney(0)).toBe("—");
    expect(fmtMoney(null)).toBe("—");
    expect(fmtMoney(undefined)).toBe("—");
  });
});

describe("flowStrength", () => {
  it("净流入除以总成交额得百分比", () => {
    expect(flowStrength(500, 5000)).toBeCloseTo(10, 6);
    expect(flowStrength(-1200, 4000)).toBeCloseTo(-30, 6);
  });

  it("成交额为零或缺失时无强度可言", () => {
    expect(flowStrength(500, 0)).toBeNull();
    expect(flowStrength(500, null)).toBeNull();
    expect(flowStrength(null, 5000)).toBeNull();
  });
});

describe("fmtStrength", () => {
  it("取整并带符号", () => {
    expect(fmtStrength(12.4)).toBe("+12%");
    expect(fmtStrength(-30)).toBe("-30%");
    expect(fmtStrength(0)).toBe("0%");
    expect(fmtStrength(null)).toBe("");
  });
});

describe("flowSortValue", () => {
  it("按排序键取到对应市场与窗口的净流入", () => {
    const row = {
      flows: { spot: side({ net_24h: 500 }), swap: side({ net_24h: -900 }) },
    } as any;
    expect(flowSortValue(row, "flow_spot_24h")).toBe(500);
    expect(flowSortValue(row, "flow_swap_24h")).toBe(-900);
    expect(flowSortValue(row, "flow_spot_168h")).toBeNull();
  });

  it("整侧缺失或 flows 为空时返回 null（排序会沉底）", () => {
    expect(flowSortValue({ flows: { spot: null, swap: null } } as any, "flow_spot_24h")).toBeNull();
    expect(flowSortValue({ flows: null } as any, "flow_spot_24h")).toBeNull();
  });

  it("四个排序键都定义齐全", () => {
    expect(Object.keys(FLOW_SORTS).sort()).toEqual([
      "flow_spot_168h", "flow_spot_24h", "flow_swap_168h", "flow_swap_24h",
    ]);
  });
});
```

- [ ] **Step 3: 跑测试确认失败**

在 `frontend/` 目录下 Run: `npx vitest run src/pages/sectorFlowFormat.test.ts`
Expected: FAIL —— `Failed to resolve import "./sectorFlowFormat"`

- [ ] **Step 4: 写实现**

新建 `frontend/src/pages/sectorFlowFormat.ts`：

```ts
import type { SectorFlows, SectorFlowSide } from "../api/types";

/** 窗口键与后端、DB 列名保持一致；UI 上 168h/720h 显示为 7d/30d。 */
export type FlowWindow = "1h" | "24h" | "168h" | "720h";

export const FLOW_WINDOWS: FlowWindow[] = ["1h", "24h", "168h", "720h"];

/** 榜单排序下拉里的资金流选项 → 取哪个市场哪个窗口的净流入。 */
export const FLOW_SORTS = {
  flow_spot_24h: { market: "spot", window: "24h" },
  flow_swap_24h: { market: "swap", window: "24h" },
  flow_spot_168h: { market: "spot", window: "168h" },
  flow_swap_168h: { market: "swap", window: "168h" },
} as const;

export type FlowSortKey = keyof typeof FLOW_SORTS;

export function isFlowSortKey(key: string): key is FlowSortKey {
  return key in FLOW_SORTS;
}

/** 金额缩写：+$46.2M / -$1.2B / +$842。0 与缺失一律「—」（0 净流入不值得占一格视线）。 */
export function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0 || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "-";
  const abs = Math.abs(v);
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${Math.round(abs)}`;
}

/** 强度比率（百分数）= 净流入 ÷ 总成交额。成交额为 0/缺失时无从谈起，返回 null。 */
export function flowStrength(
  net: number | null | undefined,
  qv: number | null | undefined,
): number | null {
  if (net === null || net === undefined) return null;
  if (qv === null || qv === undefined || qv <= 0) return null;
  return (net / qv) * 100;
}

export function fmtStrength(pct: number | null): string {
  if (pct === null) return "";
  const rounded = Math.round(pct);
  const sign = rounded > 0 ? "+" : "";
  return `${sign}${rounded}%`;
}

export function sideValue(
  side: SectorFlowSide | null | undefined,
  kind: "net" | "qv",
  window: FlowWindow,
): number | null {
  if (!side) return null;
  return side[`${kind}_${window}` as keyof SectorFlowSide] as number | null;
}

/** 榜单排序取值。整侧缺失返回 null，调用方把 null 排到末尾。 */
export function flowSortValue(
  row: { flows?: SectorFlows | null },
  key: FlowSortKey,
): number | null {
  const { market, window } = FLOW_SORTS[key];
  return sideValue(row.flows?.[market], "net", window);
}
```

- [ ] **Step 5: 跑测试确认通过**

在 `frontend/` 下 Run: `npx vitest run src/pages/sectorFlowFormat.test.ts`
Expected: 10 passed

- [ ] **Step 6: 类型检查**

在 `frontend/` 下 Run: `npx tsc --noEmit`
Expected: 无输出（SectorRotationPage 还没用 flows，不会报错）

- [ ] **Step 7: 提交**

```bash
git add frontend/src/api/types.ts frontend/src/pages/sectorFlowFormat.ts frontend/src/pages/sectorFlowFormat.test.ts && git commit -m "feat(sectors): 前端资金流格式化纯函数(金额缩写/强度/排序取值)"
```

---

### Task 10: 板块页四列资金流

**Files:**
- Modify: `frontend/src/pages/SectorRotationPage.tsx`

- [ ] **Step 1: 加排序项与排序取值**

`SectorRotationPage.tsx` 第 1-30 行的 import 与 SortKey/SORT_OPTIONS 段替换为：

```tsx
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, RefreshCcw } from "lucide-react";
import { api } from "../api/client";
import type { SectorFlowSide, SectorLeaderboardRow, SectorTokenRow } from "../api/types";
import { Button, PageHeader, SelectControl } from "../components/Controls";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import {
  FLOW_WINDOWS,
  type FlowSortKey,
  type FlowWindow,
  flowSortValue,
  flowStrength,
  fmtMoney,
  fmtStrength,
  isFlowSortKey,
  sideValue,
} from "./sectorFlowFormat";

type ReturnSortKey =
  | "ret_1h_median"
  | "ret_24h_median"
  | "ret_168h_median"
  | "ret_720h_median"
  | "ret_1h"
  | "ret_24h"
  | "ret_168h"
  | "ret_720h";

type SortKey = ReturnSortKey | FlowSortKey | "token_count";

const SORT_OPTIONS: { label: string; value: SortKey }[] = [
  { label: "24 小时中位", value: "ret_24h_median" },
  { label: "1 小时中位", value: "ret_1h_median" },
  { label: "7 天中位", value: "ret_168h_median" },
  { label: "30 天中位", value: "ret_720h_median" },
  { label: "24 小时均值", value: "ret_24h" },
  { label: "1 小时均值", value: "ret_1h" },
  { label: "7 天均值", value: "ret_168h" },
  { label: "30 天均值", value: "ret_720h" },
  { label: "现货 24h 净流入", value: "flow_spot_24h" },
  { label: "永续 24h 净流入", value: "flow_swap_24h" },
  { label: "现货 7d 净流入", value: "flow_spot_168h" },
  { label: "永续 7d 净流入", value: "flow_swap_168h" },
  { label: "成分币数量", value: "token_count" },
];

/** UI 上的窗口名（代码里一律用 168h/720h，给人看的是 7d/30d）。 */
const WINDOW_LABELS: Record<FlowWindow, string> = {
  "1h": "1h",
  "24h": "24h",
  "168h": "7d",
  "720h": "30d",
};
```

- [ ] **Step 2: 改排序函数**

同文件 `sortedRows` 的 useMemo（原第 69-76 行）替换为：

```tsx
  const sortedRows: SectorLeaderboardRow[] = useMemo(() => {
    const rows = leaderboard.data?.rows ?? [];
    const valueOf = (row: SectorLeaderboardRow): number | null => {
      if (sortBy === "token_count") return row.token_count;
      if (isFlowSortKey(sortBy)) return flowSortValue(row, sortBy);
      return row[sortBy];
    };
    return [...rows].sort((a, b) => compareDescNullsLast(valueOf(a), valueOf(b)));
  }, [leaderboard.data, sortBy]);
```

- [ ] **Step 3: 加表头列**

同文件榜单 `<thead>`（原第 125-135 行）的 `<th style={{ textAlign: "right" }}>30d</th>` 之后追加：

```tsx
                  {FLOW_WINDOWS.map((w) => (
                    <th key={w} style={{ textAlign: "right" }} title="净资金流入 = 主动买入 − 主动卖出；上行现货、下行永续">
                      资金 {WINDOW_LABELS[w]}
                    </th>
                  ))}
```

- [ ] **Step 4: 加 FlowCell 组件**

同文件末尾（`ReturnCell` 之后）追加：

```tsx
function FlowCell({
  flows,
  window,
  showTokens,
}: {
  flows: { spot: SectorFlowSide | null; swap: SectorFlowSide | null } | null | undefined;
  window: FlowWindow;
  showTokens: boolean;
}) {
  const line = (market: "spot" | "swap", label: string) => {
    const side = flows?.[market] ?? null;
    const net = sideValue(side, "net", window);
    const qv = sideValue(side, "qv", window);
    const strength = fmtStrength(flowStrength(net, qv));
    const cls = net === null || net === 0 ? "ret-flat" : net > 0 ? "ret-up" : "ret-down";
    const hint = showTokens && side?.tokens != null
      ? `${label}：${side.tokens} 个成分币有数据，窗口总成交额 ${fmtMoney(qv)}`
      : `${label}：窗口总成交额 ${fmtMoney(qv)}`;
    return (
      <div className={cls} title={hint}>
        <span className="muted" style={{ fontSize: 12 }}>{label} </span>
        {fmtMoney(net)}
        {strength && <span className="muted" style={{ fontSize: 12 }}> · {strength}</span>}
      </div>
    );
  };
  return (
    <td style={{ textAlign: "right", fontSize: 13.5 }}>
      {line("spot", "现")}
      {line("swap", "永")}
    </td>
  );
}
```

- [ ] **Step 5: 榜单行渲染四列 + 修 colSpan**

同文件 `RowGroup` 的 `<ReturnCell median={row.ret_720h_median} mean={row.ret_720h} />` 之后追加：

```tsx
        {FLOW_WINDOWS.map((w) => (
          <FlowCell key={w} flows={row.flows} window={w} showTokens />
        ))}
```

并把展开行的 `<td colSpan={8}` 改为 `<td colSpan={12}`（4 个新列）。

- [ ] **Step 6: 钻取表加四列**

同文件钻取 `<thead>` 的 `<th style={{ textAlign: "right" }}>30d</th>` 之后追加：

```tsx
                      {FLOW_WINDOWS.map((w) => (
                        <th key={w} style={{ textAlign: "right" }}>资金 {WINDOW_LABELS[w]}</th>
                      ))}
```

并在钻取 `<tbody>` 每行的 `{fmtPct(t.ret_720h)}</td>` 之后追加：

```tsx
                        {FLOW_WINDOWS.map((w) => (
                          <FlowCell key={w} flows={t.flows} window={w} showTokens={false} />
                        ))}
```

- [ ] **Step 7: 类型检查 + 前端测试**

在 `frontend/` 下 Run: `npx tsc --noEmit`
Expected: 无输出

在 `frontend/` 下 Run: `npx vitest run`
Expected: 全部 PASS

- [ ] **Step 8: 起服务肉眼验收**

用 preview 启动 API（8000）与前端（5173），打开板块轮动页确认：
- 榜单出现 4 个「资金 …」列，每格两行「现 / 永」
- 服务器补丁未上时全部显示「—」，页面不报错、涨跌列照常
- 排序下拉能选到 4 个资金流选项，选中后排序生效
- 展开某板块，钻取表同样出现 4 列且不错位（展开行横跨全部 12 列）

浏览器控制台无 error。

- [ ] **Step 9: 提交**

```bash
git add frontend/src/pages/SectorRotationPage.tsx && git commit -m "feat(sectors): 板块页加 4 列净资金流入(现/永两行 + 4 个排序项)"
```

---

### Task 11: T0 部署验收脚本

**Files:**
- Create: `scripts/verify_taker_pivot_patch.py`

这个脚本跑在**数据服务器**上（用 BMAC 自己的 python，numpy 2，不需要本项目的 shim），只依赖 pandas 与标准库。

- [ ] **Step 1: 写脚本**

新建 `scripts/verify_taker_pivot_patch.py`：

```python
# -*- coding: utf-8 -*-
"""BMAC 宽表 taker 补丁的部署验收（2026-08-07 净资金流入 spec §5.1）。

跑在**数据服务器**上，用 BMAC 自己的 python（本脚本只依赖 pandas + 标准库）：

    python scripts/verify_taker_pivot_patch.py \
        --data-root /root/data_center/data --offset 30m --year 2026 \
        --backup /root/backup/market_pivot_spot_2026.pkl.bak

四项检查，全过才算补丁部署成功：
  1. 结构     两个新矩阵存在，且行索引/列集合与 close 完全一致
  2. 抽样勾稽 随机抽币 × 抽时点，宽表值 vs 单币原始 pkl 逐值核对
  3. 回归勾稽 与补丁前的备份宽表比，旧字段一个值都不许变
  4. 备用源   data_api 目录的单币文件是否也带这两个字段（评估容错触发概率）

退出码 0 = 全过；1 = 有 FAIL。
"""
from __future__ import annotations

import argparse
import pickle
import random
import sys
from pathlib import Path

import pandas as pd

QUOTE_VOLUME_KEY = "quote_volume"
TAKER_BUY_KEY = "taker_buy_quote_asset_volume"
LEGACY_KEYS_SPOT = ("open", "close", "vwap1m")
LEGACY_KEYS_SWAP = ("open", "close", "vwap1m", "funding_rate")
TOLERANCE = 1e-6

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def load_pickle(path: Path):
    with open(path, "rb") as fh:
        return pickle.load(fh)


def check_structure(pivot: dict, market: str) -> bool:
    close = pivot.get("close")
    if close is None:
        record(f"结构/{market}", False, "pivot 里没有 close")
        return False
    missing = [k for k in (QUOTE_VOLUME_KEY, TAKER_BUY_KEY) if k not in pivot]
    if missing:
        record(f"结构/{market}", False, f"缺新键 {missing}（补丁未生效）")
        return False
    for key in (QUOTE_VOLUME_KEY, TAKER_BUY_KEY):
        frame = pivot[key]
        if not frame.index.equals(close.index):
            record(f"结构/{market}", False, f"{key} 行索引与 close 不一致")
            return False
        if list(frame.columns) != list(close.columns):
            record(f"结构/{market}", False, f"{key} 列集合与 close 不一致")
            return False
    record(f"结构/{market}", True,
           f"{len(close.index)} 行 × {len(close.columns)} 列，两个新矩阵齐备")
    return True


def check_sampling(pivot: dict, per_symbol_dir: Path, market: str,
                   n_symbols: int, n_times: int) -> bool:
    close = pivot["close"]
    candidates = [c for c in close.columns if (per_symbol_dir / f"{c}.pkl").exists()]
    if not candidates:
        record(f"抽样勾稽/{market}", False, f"{per_symbol_dir} 下找不到任何单币文件")
        return False
    picked = random.sample(candidates, min(n_symbols, len(candidates)))
    mismatches: list[str] = []
    checked = 0
    for symbol in picked:
        df = load_pickle(per_symbol_dir / f"{symbol}.pkl")
        df = df.set_index(pd.DatetimeIndex(df["candle_begin_time"]))
        common = close.index.intersection(df.index)
        if len(common) == 0:
            mismatches.append(f"{symbol}: 与宽表无共同时点")
            continue
        for ts in random.sample(list(common), min(n_times, len(common))):
            for key, column in ((QUOTE_VOLUME_KEY, "quote_volume"),
                                (TAKER_BUY_KEY, TAKER_BUY_KEY)):
                wide = pivot[key].at[ts, symbol]
                raw = df.at[ts, column]
                checked += 1
                if pd.isna(wide) and pd.isna(raw):
                    continue
                if pd.isna(wide) != pd.isna(raw) or abs(float(wide) - float(raw)) > TOLERANCE:
                    mismatches.append(f"{symbol}@{ts} {key}: 宽表 {wide} vs 原始 {raw}")
    if mismatches:
        record(f"抽样勾稽/{market}", False,
               f"{len(mismatches)} 处不一致，前 3 条：{mismatches[:3]}")
        return False
    record(f"抽样勾稽/{market}", True, f"{len(picked)} 个币 × 共 {checked} 个值全对上")
    return True


def check_regression(pivot: dict, backup_path: Path, market: str) -> bool:
    if not backup_path.exists():
        record(f"回归勾稽/{market}", False, f"备份不存在: {backup_path}")
        return False
    old = load_pickle(backup_path)
    legacy = LEGACY_KEYS_SWAP if market == "swap" else LEGACY_KEYS_SPOT
    for key in legacy:
        if key not in old:
            continue
        if key not in pivot:
            record(f"回归勾稽/{market}", False, f"补丁后丢了旧键 {key}")
            return False
        old_df, new_df = old[key], pivot[key]
        rows = old_df.index.intersection(new_df.index)
        cols = [c for c in old_df.columns if c in set(new_df.columns)]
        if len(rows) == 0 or not cols:
            record(f"回归勾稽/{market}", False, f"{key} 与备份无重叠区间，无法比对")
            return False
        a, b = old_df.loc[rows, cols], new_df.loc[rows, cols]
        diff = ((a - b).abs() > TOLERANCE) & a.notna() & b.notna()
        n_diff = int(diff.to_numpy().sum())
        if n_diff:
            record(f"回归勾稽/{market}", False, f"{key} 有 {n_diff} 个格子被改动")
            return False
    record(f"回归勾稽/{market}", True, f"旧字段 {legacy} 在重叠区间逐值未变")
    return True


def check_data_api(data_root: Path, offset: str, market: str, n: int) -> bool:
    api_dir = data_root / f"data_api_{market}_1h_resample" / offset
    if not api_dir.exists():
        record(f"备用源/{market}", True, f"{api_dir} 不存在（未启用备用源，无风险）")
        return True
    files = sorted(api_dir.glob("*USDT.pkl"))
    if not files:
        record(f"备用源/{market}", True, "备用源目录为空（无风险）")
        return True
    missing = []
    for path in random.sample(files, min(n, len(files))):
        columns = set(load_pickle(path).columns)
        if not {"quote_volume", TAKER_BUY_KEY} <= columns:
            missing.append(path.name)
    if missing:
        record(f"备用源/{market}", False,
               f"{len(missing)}/{min(n, len(files))} 个备用源文件缺字段（缺列容错会被触发）："
               f"{missing[:3]}")
        return False
    record(f"备用源/{market}", True, f"抽查 {min(n, len(files))} 个备用源文件字段齐备")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="BMAC taker 宽表补丁验收")
    parser.add_argument("--data-root", default="/root/data_center/data")
    parser.add_argument("--offset", default="30m")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--markets", default="spot,swap")
    parser.add_argument("--backup", default=None,
                        help="补丁前的 spot 宽表备份路径（swap 备份按同目录同名规则推断）")
    parser.add_argument("--symbols", type=int, default=10, help="抽样币数")
    parser.add_argument("--times", type=int, default=50, help="每个币抽的时点数")
    parser.add_argument("--seed", type=int, default=20260807)
    args = parser.parse_args()

    random.seed(args.seed)
    data_root = Path(args.data_root)

    for market in [m.strip() for m in args.markets.split(",") if m.strip()]:
        pivot_path = (data_root / "preprocess_1h_resample" / args.offset
                      / f"market_pivot_{market}_{args.year}.pkl")
        if not pivot_path.exists():
            record(f"结构/{market}", False, f"宽表不存在: {pivot_path}")
            continue
        pivot = load_pickle(pivot_path)

        if not check_structure(pivot, market):
            continue
        check_sampling(pivot, data_root / f"binance_{market}_1h_resample" / args.offset,
                       market, args.symbols, args.times)
        if args.backup:
            backup = Path(args.backup)
            if market == "swap":
                backup = backup.parent / backup.name.replace("_spot_", "_swap_")
            check_regression(pivot, backup, market)
        else:
            record(f"回归勾稽/{market}", False, "未传 --backup，无法证明旧数未变")
        check_data_api(data_root, args.offset, market, args.symbols)

    failed = [name for name, ok, _ in results if not ok]
    print("\n" + "=" * 60)
    if failed:
        print(f"验收未通过：{len(failed)}/{len(results)} 项 FAIL → {failed}")
        return 1
    print(f"验收通过：{len(results)}/{len(results)} 项全过，补丁可以留在服务器上。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 本机做语法与参数自检**

Run: `D:\anaconda\python.exe -c "import ast,pathlib; ast.parse(pathlib.Path('scripts/verify_taker_pivot_patch.py').read_text(encoding='utf-8')); print('syntax ok')"`
Expected: 输出 `syntax ok`

Run: `D:\anaconda\python.exe scripts/verify_taker_pivot_patch.py --help`
Expected: 打印 usage，退出码 0

- [ ] **Step 3: 用假数据跑一遍确认能判 FAIL**

Run（临时脚本，跑完删掉）：

```bash
D:\anaconda\python.exe scripts/verify_taker_pivot_patch.py --data-root C:\Users\Lenovo\AppData\Local\Temp\claude\D--market-monitor\df0dc978-444c-4a13-a5b9-27d94df3ca64\scratchpad\nonexistent --year 2026
```
Expected: 两行 `[FAIL] 结构/...  — 宽表不存在: ...`，末尾"验收未通过"，退出码 1

- [ ] **Step 4: 提交**

```bash
git add scripts/verify_taker_pivot_patch.py && git commit -m "feat(sectors): BMAC taker 宽表补丁的 T0 验收脚本"
```

---

### Task 12: 服务器补丁 + 部署 runbook

**Files:**
- Modify: `scripts/server_src/preprocess.py:18,76-88`（仓库留档镜像）
- Modify: `docs/superpowers/specs/2026-08-07-sector-net-inflow-design.md`（补 runbook 落地细节）

**注意：本 Task 只改仓库里的留档镜像并写 runbook，不自动登服务器执行。** 真正上服务器的操作由用户确认后按 runbook 手动执行（改动会影响交易框架供数）。

- [ ] **Step 1: 改留档镜像的字段清单**

`scripts/server_src/preprocess.py` 第 18 行替换为：

```python
# market_monitor 本地补丁（2026-08-07）：追加 quote_volume / taker_buy_quote_asset_volume
# 两列，供板块净资金流入使用。纯增量——原有列一个不动，旧消费者向后兼容。
PIVOT_COLUMNS = ['candle_begin_time', 'symbol', 'open', 'close', 'avg_price_1m', 'funding_fee',
                 'quote_volume', 'taker_buy_quote_asset_volume']
```

- [ ] **Step 2: 改留档镜像的宽表生成**

同文件第 76-88 行（`make_market_pivot` 整体）替换为：

```python
def make_market_pivot(market_dict, market_type='spot'):
    # market_monitor 本地补丁（2026-08-07）：缺列补 NaN 而不是 KeyError。
    # data_api 备用源个别文件可能没有 taker 字段，硬取会让整轮预处理崩掉——
    # 预处理停产会波及交易框架供数，此处宁可缺数不可崩溃。
    df_list = [
        df.reindex(columns=PIVOT_COLUMNS).dropna(subset='symbol')
        for df in market_dict.values()
    ]
    df_all_market = pd.concat(df_list, ignore_index=True)
    df_all_market['symbol'] = pd.Categorical(df_all_market['symbol'])
    df_open = df_all_market.pivot(values='open', index='candle_begin_time', columns='symbol')
    df_close = df_all_market.pivot(values='close', index='candle_begin_time', columns='symbol')
    df_vwap1m = df_all_market.pivot(values='avg_price_1m', index='candle_begin_time', columns='symbol')
    # market_monitor 本地补丁（2026-08-07）：资金流两个矩阵
    df_qv = df_all_market.pivot(values='quote_volume', index='candle_begin_time', columns='symbol')
    df_taker = df_all_market.pivot(values='taker_buy_quote_asset_volume',
                                   index='candle_begin_time', columns='symbol')
    result = {'open': df_open, 'close': df_close, 'vwap1m': df_vwap1m,
              'quote_volume': df_qv, 'taker_buy_quote_asset_volume': df_taker}
    if market_type == 'swap':
        df_rate = df_all_market.pivot(values='funding_fee', index='candle_begin_time', columns='symbol')
        df_rate.fillna(value=0, inplace=True)
        result['funding_rate'] = df_rate
    return result
```

- [ ] **Step 3: 确认镜像语法可解析**

Run: `D:\anaconda\python.exe -c "import ast,pathlib; ast.parse(pathlib.Path('scripts/server_src/preprocess.py').read_text(encoding='utf-8')); print('syntax ok')"`
Expected: 输出 `syntax ok`

- [ ] **Step 4: 把 runbook 写进设计稿**

在 `docs/superpowers/specs/2026-08-07-sector-net-inflow-design.md` 的 `## 10. 上线顺序与回滚` 一节末尾追加：

```markdown
### 10.1 服务器补丁操作 runbook（人工执行）

前置：本项目代码已全量上线（页面资金流列显示「—」，功能不受影响）。

```bash
# 1) 备份原文件 + 当前宽表（回归勾稽要用）
ssh mmon-data 'cp /root/data_center/bmac/preprocess.py /root/backup/preprocess.py.$(date +%Y%m%d) && \
  cp /root/data_center/data/preprocess_1h_resample/30m/market_pivot_spot_2026.pkl /root/backup/ && \
  cp /root/data_center/data/preprocess_1h_resample/30m/market_pivot_swap_2026.pkl /root/backup/'

# 2) 上传补丁后的 preprocess.py（内容 = 本仓库 scripts/server_src/preprocess.py 的两处改动）
#    与验收脚本
scp scripts/server_src/preprocess.py mmon-data:/root/data_center/bmac/preprocess.py
scp scripts/verify_taker_pivot_patch.py mmon-data:/root/

# 3) 重启 BMAC（选在整点写入刚结束的安静窗口，即每小时 :40 之后）

# 4) 等下一个整点周期写完，跑验收
ssh mmon-data 'python /root/verify_taker_pivot_patch.py --year 2026 --offset 30m \
  --backup /root/backup/market_pivot_spot_2026.pkl'
```

验收脚本退出码必须为 0。任一 FAIL → 立即还原备份的 `preprocess.py` 并重启 BMAC；
本项目侧无需任何操作（勾稽门会自动把资金流退化为「—」）。

验收通过后再盯一个整点周期，确认 mmon.top 拉到新宽表、板块页出数、无 `sector_flow_gate` 告警。
```

> `mmon-data` 是数据服务器（`root@47.243.252.92`）的 ssh 别名；若本机 `~/.ssh/config` 里尚未配置，
> 执行时把它换成 `root@47.243.252.92`。

- [ ] **Step 5: 提交**

```bash
git add scripts/server_src/preprocess.py docs/superpowers/specs/2026-08-07-sector-net-inflow-design.md && git commit -m "feat(sectors): BMAC 宽表补丁留档镜像 + 服务器部署 runbook"
```

- [ ] **Step 6: 全量回归**

Run: `D:\anaconda\python.exe -m pytest -q`
Expected: 全绿

在 `frontend/` 下 Run: `npx vitest run` 与 `npx tsc --noEmit`
Expected: 测试全 PASS，类型检查无输出

---

## 完成标准

- [ ] 后端 pytest 全量绿；前端 vitest 全量绿；`tsc --noEmit` 无输出
- [ ] 板块页 4 列资金流可见；服务器补丁未上时全列「—」且不报错
- [ ] 排序下拉 4 个资金流选项可用
- [ ] `scripts/verify_taker_pivot_patch.py --help` 可跑，假路径下正确判 FAIL
- [ ] 服务器补丁 runbook 已写进设计稿（实际执行由用户确认后手动进行）
