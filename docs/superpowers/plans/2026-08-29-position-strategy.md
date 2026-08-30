# 持仓策略模块实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按已批准设计稿 `docs/superpowers/specs/2026-08-28-position-strategy-design.md` 落地「持仓策略」模块：录入持仓批次 → 每日 UTC 收盘后自动算 EWMA 波动率与 4×/6×ATR 防线 → 页面画图 → 转换型动作提示推企业微信。

**Architecture:** 纯函数计算引擎（`strategy_engine`）与编排服务（`strategy_service`）分离；公式只存在于 engine 一处，每日定时任务、overview 接口、计算器接口全部复用。事件表 `strategy_events` 同时充当页面提示流与"状态转换去重"依据。前端新页 + 专用图表组件，不改动共享 `MultiLineChart`。

**Tech Stack:** FastAPI + SQLAlchemy(SQLite) + APScheduler + ccxt(OKX 公开行情) + React/recharts/@tanstack/react-query。

**执行须知（每个任务通用）：**
- 开工前建分支：`git switch -c feat/position-strategy`（若已在该分支则跳过）。
- 后端测试命令一律 `D:/anaconda/python.exe -m pytest <path> -v`（本机 PATH 里的 python 是坏桩，退出码 49）。
- 前端命令在 `frontend/` 下执行：`npx vitest run <path>`、`npx tsc --noEmit`。
- 项目约定：DB 时间全部 UTC-naive；注释写中文；提交信息格式 `feat(strategy): ...`。

---

## 文件结构总览

| 文件 | 职责 |
|---|---|
| `services/strategy_engine.py`（新建） | 纯数学：EWMA、25% 闩锁、锚 H、软/硬线、占用、模拟建仓。零 IO |
| `services/strategy_service.py`（新建） | 编排：OKX 日线拉取、四张表读写、事件转换检测、企业微信推送、overview 组装 |
| `models/strategy.py`（新建） | 4 张表：positions / settings / symbol_state / events |
| `schemas/strategy.py`（新建） | API 请求/响应 pydantic 模型 |
| `api/routes.py`（修改） | `/api/strategy/*` 9 条路由 |
| `api/app.py`（修改） | `CRON_SCHEDULES` 加 `strategy_daily_check`（北京 08:05）+ add_job |
| `config.py`（修改） | crypto 采集清单加 VIRTUAL |
| `tests/test_strategy_engine.py`（新建） | 引擎纯函数单测 |
| `tests/test_strategy_service.py`（新建） | 服务层 + 事件状态机 + 推送去重（内存 SQLite） |
| `tests/test_strategy_api.py`（新建） | 路由冒烟（TestClient） |
| `tests/test_scheduler_timezone.py`（修改） | 新 cron 进钉死清单 |
| `frontend/src/api/types.ts` / `client.ts`（修改） | 类型 + API 函数 |
| `frontend/src/pages/strategyFormat.ts`（新建） | 纯展示格式化（vitest 覆盖） |
| `frontend/src/components/StrategyChart.tsx`（新建） | 专用图表（ComposedChart） |
| `frontend/src/pages/StrategyPage.tsx`（新建） | 页面：横幅+图+批次表/提示流/计算器 |
| `frontend/src/main.tsx` / `components/AppShell.tsx`（修改） | 路由 + 导航「持仓策略」 |

---

### Task 1: 数据模型（4 张表）

**Files:**
- Create: `models/strategy.py`
- Modify: `models/__init__.py`
- Test: `tests/test_strategy_models.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_strategy_models.py
# -*- coding: utf-8 -*-
"""strategy 四张表能建表、能写读（冒烟）。"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.strategy import (
    StrategyEvent,
    StrategyPosition,
    StrategySettings,
    StrategySymbolState,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_tables_roundtrip():
    s = _session()
    s.add(StrategyPosition(
        symbol="VIRTUAL-USDT-SWAP", batch_label="B1",
        entry_at=datetime(2026, 8, 26, 23, 33), entry_price=0.743,
        quantity=23590, forecast=10, status="open",
    ))
    s.add(StrategySettings(capital=13915.0, risk_budget_pct=0.15))
    s.add(StrategySymbolState(symbol="VIRTUAL-USDT-SWAP", v_used=0.0494,
                              v_used_at=datetime(2026, 8, 28)))
    s.add(StrategyEvent(symbol="VIRTUAL-USDT-SWAP", kind="daily_ok",
                        message="未破线", payload_json="{}"))
    s.commit()

    pos = s.query(StrategyPosition).one()
    assert pos.status == "open" and pos.quantity == 23590
    assert s.query(StrategySettings).one().x_soft == 4          # 默认值生效
    assert s.query(StrategySymbolState).one().reentry_level is None
    assert s.query(StrategyEvent).one().pushed is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_strategy_models.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'models.strategy'`

- [ ] **Step 3: 写模型**

```python
# models/strategy.py
# -*- coding: utf-8 -*-
"""持仓策略模块的 4 张表。

设计稿：docs/superpowers/specs/2026-08-28-position-strategy-design.md §3。
时间字段一律 UTC naive（项目约定）。
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String, Text

from database import Base


class StrategyPosition(Base):
    """一行 = 一个批次（B1/B2…）。系统永不自动改 status，平仓只由用户在页面操作。"""
    __tablename__ = "strategy_positions"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(40), nullable=False)          # OKX instId，如 VIRTUAL-USDT-SWAP
    batch_label = Column(String(20), nullable=False)     # B1/B2…
    entry_at = Column(DateTime, nullable=False)          # UTC naive
    entry_price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    forecast = Column(Integer, nullable=False, default=10)
    status = Column(String(10), nullable=False, default="open")   # open / closed
    closed_at = Column(DateTime, nullable=True)
    close_price = Column(Float, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (Index("ix_strategy_pos_symbol_status", "symbol", "status"),)


class StrategySettings(Base):
    """单行参数表（页面可改，改了下次计算生效）。默认值 = 用户 2026-08-28 拍板值。"""
    __tablename__ = "strategy_settings"

    id = Column(Integer, primary_key=True)
    capital = Column(Float, nullable=False, default=13915.0)
    risk_budget_pct = Column(Float, nullable=False, default=0.15)
    x_soft = Column(Integer, nullable=False, default=4)
    x_hard = Column(Integer, nullable=False, default=6)
    ewma_alpha = Column(Float, nullable=False, default=0.054)
    vol_update_threshold = Column(Float, nullable=False, default=0.25)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class StrategySymbolState(Base):
    """按币种的路径依赖状态：在用波动率闩锁 + 重入场观察水位。"""
    __tablename__ = "strategy_symbol_state"

    symbol = Column(String(40), primary_key=True)
    v_used = Column(Float, nullable=True)
    v_used_at = Column(DateTime, nullable=True)
    reentry_level = Column(Float, nullable=True)         # 观察态水位；空 = 不在观察态
    reentry_breached_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class StrategyEvent(Base):
    """动作提示流 + 状态转换去重依据。kind 见设计稿 §5。"""
    __tablename__ = "strategy_events"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    symbol = Column(String(40), nullable=False)
    position_id = Column(Integer, nullable=True)
    kind = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    payload_json = Column(Text, nullable=True)
    pushed = Column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("ix_strategy_events_symbol_created", "symbol", "created_at"),)
```

- [ ] **Step 4: 注册进 models 包** — `models/__init__.py` 在 `from models.crypto import NewsCoin` 之后加一行：

```python
from models.strategy import StrategyEvent, StrategyPosition, StrategySettings, StrategySymbolState
```

- [ ] **Step 5: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_strategy_models.py -v`
Expected: PASS（1 passed）

- [ ] **Step 6: Commit**

```bash
git add models/strategy.py models/__init__.py tests/test_strategy_models.py
git commit -m "feat(strategy): 持仓策略 4 张表——批次/参数/币种状态/事件流"
```

---

### Task 2: 计算引擎——EWMA 波动率与 25% 闩锁

**Files:**
- Create: `services/strategy_engine.py`
- Test: `tests/test_strategy_engine.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_strategy_engine.py
# -*- coding: utf-8 -*-
"""strategy_engine 纯函数单测。手算基准见各断言旁注释。"""
import math
from datetime import datetime, timedelta

import pytest

from services.strategy_engine import (
    DailyCandle,
    anchor_high,
    batch_state,
    ewma_vol_series,
    simulate_entry,
    walk_latch,
)


def _mk_candles(closes, start=datetime(2026, 8, 1)):
    return [
        DailyCandle(date=start + timedelta(days=i), open=c, high=c * 1.02, low=c * 0.98, close=c)
        for i, c in enumerate(closes)
    ]


def test_ewma_vol_matches_hand_calc():
    # 收盘 100 -> 110：r=ln(1.1)；首日方差=r^2（热身种子）
    # 110 -> 99：r2=ln(0.9)；var2 = 0.054*r2^2 + 0.946*r1^2
    closes = [100.0, 110.0, 99.0]
    vols = ewma_vol_series(closes, alpha=0.054)
    r1 = math.log(1.1)
    r2 = math.log(0.9)
    assert vols[0] == pytest.approx(abs(r1))
    assert vols[1] == pytest.approx(math.sqrt(0.054 * r2 * r2 + 0.946 * r1 * r1))
    assert len(vols) == 2


def test_walk_latch_only_updates_beyond_threshold():
    # 起点 5%；5.5% 偏离 10% 不更新；6.5% 偏离 30% 更新
    vols = [0.050, 0.055, 0.065]
    used = walk_latch(vols, threshold=0.25)
    assert used == [pytest.approx(0.050), pytest.approx(0.050), pytest.approx(0.065)]


def test_walk_latch_seed_carries_over():
    # 已持久化在用值 0.04，最新 0.049（+22.5%）不更新；0.051（+27.5%）更新
    assert walk_latch([0.049], threshold=0.25, seed=0.040) == [pytest.approx(0.040)]
    assert walk_latch([0.051], threshold=0.25, seed=0.040) == [pytest.approx(0.051)]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_strategy_engine.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'services.strategy_engine'`

- [ ] **Step 3: 写引擎第一部分**

```python
# services/strategy_engine.py
# -*- coding: utf-8 -*-
"""持仓策略计算引擎：纯函数，零 IO。

公式单一来源（设计稿 §2）；每日任务、overview、计算器全部只能调这里，
禁止在别处重写任何公式。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class DailyCandle:
    """一根已确认的 UTC 日 K（date = bar 起始 00:00，UTC naive）。"""
    date: datetime
    open: float
    high: float
    low: float
    close: float

    @property
    def close_time(self) -> datetime:
        return self.date + timedelta(days=1)


def ewma_vol_series(closes: list[float], alpha: float) -> list[float]:
    """对数收益的 EWMA 标准差序列。返回长度 = len(closes) - 1。

    热身：首个方差 = 首个收益的平方（起点影响在 ~37 天后衰减殆尽，设计稿 §2.2）。
    """
    vols: list[float] = []
    var: float | None = None
    for prev, cur in zip(closes, closes[1:]):
        r = math.log(cur / prev)
        var = r * r if var is None else alpha * r * r + (1 - alpha) * var
        vols.append(math.sqrt(var))
    return vols


def walk_latch(vols: list[float], threshold: float, seed: float | None = None) -> list[float]:
    """25% 守则闩锁：逐日走一遍"偏离超阈值才更新在用值"。

    seed = 已持久化的在用值（None = 冷启动，首日直接采用）。
    返回与 vols 等长的"在用波动率"序列，末位即当前应持久化的值。
    """
    used: list[float] = []
    current = seed
    for v in vols:
        if current is None or abs(v / current - 1) > threshold:
            current = v
        used.append(current)
    return used
```

- [ ] **Step 4: 跑测试确认前三个通过（batch 相关的仍收集失败）**

Run: `D:/anaconda/python.exe -m pytest tests/test_strategy_engine.py -v`
Expected: 3 passed 后仍有 collection error（anchor_high 等未定义）——先注释掉文件顶部 import 里还没实现的 `anchor_high, batch_state, simulate_entry` 再跑可见 3 passed；Task 3 实现后恢复 import。

- [ ] **Step 5: Commit**

```bash
git add services/strategy_engine.py tests/test_strategy_engine.py
git commit -m "feat(strategy): 引擎第一块——EWMA36 波动率与 25% 闩锁"
```

---

### Task 3: 计算引擎——锚 H、双防线、占用、模拟建仓

**Files:**
- Modify: `services/strategy_engine.py`
- Test: `tests/test_strategy_engine.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_strategy_engine.py 末尾


def test_anchor_high_floors_at_entry_price_and_respects_entry_time():
    candles = _mk_candles([0.70, 0.7518, 0.7323], start=datetime(2026, 8, 25))
    # 8-26 23:33 入场：8-25 的 K（收盘时刻 8-26 00:00 < 入场）不算，
    # 8-26 的 K（收盘时刻 8-27 00:00 > 入场）算 => H = max(0.743, 0.7518, 0.7323)
    h = anchor_high(entry_price=0.743, entry_at=datetime(2026, 8, 26, 23, 33), candles=candles)
    assert h == pytest.approx(0.7518)
    # 入场价保底：所有入场后收盘都低于成本时 H = 入场价
    h2 = anchor_high(entry_price=0.80, entry_at=datetime(2026, 8, 26, 23, 33), candles=candles)
    assert h2 == pytest.approx(0.80)


def test_batch_state_lines_occupancy_and_flags():
    candles = _mk_candles([0.70, 0.7518, 0.7323], start=datetime(2026, 8, 25))
    st = batch_state(
        entry_price=0.743, entry_at=datetime(2026, 8, 26, 23, 33), quantity=23590,
        candles=candles, v_used=0.0494, x_soft=4, x_hard=6,
    )
    # soft = 0.7518*(1-4*0.0494)=0.7518*0.8024=0.60325…；hard = 0.7518*(1-6*0.0494)
    assert st.soft_stop == pytest.approx(0.7518 * (1 - 4 * 0.0494))
    assert st.hard_stop == pytest.approx(0.7518 * (1 - 6 * 0.0494))
    assert st.breached is False           # 昨收 0.7323 > soft
    assert st.locked is False             # soft < 成本 0.743
    assert st.occupy_usd == pytest.approx(23590 * (0.743 - st.soft_stop))


def test_batch_state_breach_and_lock():
    # 大涨后 soft 抬过成本 => locked、占用归零；再暴跌收盘 < soft => breached
    up = _mk_candles([0.70, 1.00, 0.79], start=datetime(2026, 8, 25))
    st = batch_state(entry_price=0.70, entry_at=datetime(2026, 8, 25, 1, 0), quantity=1000,
                     candles=up, v_used=0.05, x_soft=4, x_hard=6)
    # H=1.00, soft=0.80；昨收 0.79 < 0.80 => breached；soft(0.80) > 成本(0.70) => locked
    assert st.locked is True and st.breached is True
    assert st.occupy_usd == 0.0


def test_simulate_entry_matches_framework_example():
    # 设计稿 §7 沙盘：本金19000*25%=4750 预算、价0.70、vol 5%、X=4、forecast=15
    sim = simulate_entry(price=0.70, forecast=15, budget_usd=4750.0, vol=0.05, x_soft=4)
    assert sim["stop_price"] == pytest.approx(0.56)
    assert sim["stop_distance"] == pytest.approx(0.14)
    # 数量 = 预算*(F/10)/距离 = 4750*1.5/0.14 = 50892.86…；名义 = 数量*价
    assert sim["quantity"] == pytest.approx(4750 * 1.5 / 0.14)
    assert sim["notional_usd"] == pytest.approx(sim["quantity"] * 0.70)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_strategy_engine.py -v`
Expected: 新增 4 个 FAIL（NameError / ImportError）

- [ ] **Step 3: 追加引擎实现**

```python
# 追加到 services/strategy_engine.py 末尾


@dataclass(frozen=True)
class BatchState:
    """单批次在"最新确认收盘"时点的全部读数。"""
    anchor_high: float
    soft_stop: float
    hard_stop: float
    last_close: float
    breached: bool          # 最新确认收盘 < soft
    locked: bool            # soft > 入场价（锁盈，B2 额度释放）
    occupy_usd: float       # quantity * max(0, entry - soft)
    distance_pct: float     # (last_close - soft) / soft


def anchor_high(entry_price: float, entry_at: datetime, candles: list[DailyCandle]) -> float:
    """锚 H = max(入场价, 入场后已收盘日 K 的收盘价)。设计稿 §2.4。"""
    closes = [c.close for c in candles if c.close_time > entry_at]
    return max([entry_price, *closes])


def batch_state(
    *, entry_price: float, entry_at: datetime, quantity: float,
    candles: list[DailyCandle], v_used: float, x_soft: int, x_hard: int,
) -> BatchState:
    h = anchor_high(entry_price, entry_at, candles)
    soft = h * (1 - x_soft * v_used)
    hard = h * (1 - x_hard * v_used)
    last_close = candles[-1].close
    return BatchState(
        anchor_high=h,
        soft_stop=soft,
        hard_stop=hard,
        last_close=last_close,
        breached=last_close < soft,
        locked=soft > entry_price,
        occupy_usd=quantity * max(0.0, entry_price - soft),
        distance_pct=(last_close - soft) / soft,
    )


def simulate_entry(*, price: float, forecast: int, budget_usd: float, vol: float, x_soft: int) -> dict:
    """建仓计算器（设计稿 §2.7）：给定价格/信心/预算/波动率，输出止损与数量。"""
    stop_distance = price * x_soft * vol
    quantity = budget_usd * (forecast / 10.0) / stop_distance
    return {
        "stop_price": price - stop_distance,
        "stop_distance": stop_distance,
        "quantity": quantity,
        "notional_usd": quantity * price,
    }
```

同时把 Task 2 Step 4 中临时注释的 import 恢复为完整行。

- [ ] **Step 4: 跑测试确认全部通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_strategy_engine.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add services/strategy_engine.py tests/test_strategy_engine.py
git commit -m "feat(strategy): 引擎第二块——锚H/双防线/占用/模拟建仓,含8-26入场夹具手算对账"
```

---

### Task 4: 服务层——OKX 日线拉取与参数/批次 CRUD

**Files:**
- Create: `services/strategy_service.py`
- Test: `tests/test_strategy_service.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_strategy_service.py
# -*- coding: utf-8 -*-
"""strategy_service：拉取解析、CRUD、每日检查状态机、推送去重。全程内存 SQLite + 假蜡烛。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import services.strategy_service as svc
from database import Base
from models.strategy import StrategyEvent, StrategyPosition, StrategySettings, StrategySymbolState
from services.strategy_engine import DailyCandle


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(svc, "SessionLocal", Session)
    return Session()


def test_parse_okx_candles_keeps_confirmed_ascending():
    raw = {"code": "0", "data": [
        ["1787875200000", "0.73", "0.75", "0.70", "0.716", "1", "1", "1", "0"],   # 未确认，丢弃
        ["1787788800000", "0.752", "0.782", "0.718", "0.7323", "1", "1", "1", "1"],
        ["1787702400000", "0.739", "0.775", "0.710", "0.7518", "1", "1", "1", "1"],
    ]}
    candles = svc._parse_okx_candles(raw)
    assert [c.close for c in candles] == [0.7518, 0.7323]          # 升序 + 只留 confirm=1
    assert candles[0].date == datetime(2026, 8, 26)                 # 1787702400000 = 2026-08-26 00:00 UTC


def test_get_settings_creates_singleton(db):
    s1 = svc.get_settings(db)
    s2 = svc.get_settings(db)
    assert s1.id == s2.id and s1.capital == 13915.0 and s1.x_soft == 4
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_strategy_service.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'services.strategy_service'`

- [ ] **Step 3: 写服务第一部分**

```python
# services/strategy_service.py
# -*- coding: utf-8 -*-
"""持仓策略编排服务：OKX 日线现取现算 + 每日检查 + 事件推送 + overview 组装。

公式一律调 services/strategy_engine.py；本文件只做 IO 与状态机。
设计稿：docs/superpowers/specs/2026-08-28-position-strategy-design.md。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import ccxt
from loguru import logger

import config
from alerts.channels.wechat_work import WeChatWorkChannel
from database import SessionLocal
from models.alert_log import AlertLog
from models.price import PriceSnapshot
from models.strategy import StrategyEvent, StrategyPosition, StrategySettings, StrategySymbolState
from services import strategy_engine as eng

RULE_NAME = "strategy_action"          # AlertLog.rule_name，告警页可见
DEFAULT_SYMBOL = "VIRTUAL-USDT-SWAP"
CANDLE_LIMIT = 300
REENTRY_WINDOW_DAYS = 30
_TIMEOUT_MS = 15_000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------- OKX 日线（1Dutc = UTC 00:00 切日，设计稿 §2.1） ----------

def _parse_okx_candles(payload: dict) -> list[eng.DailyCandle]:
    """OKX 返回最新在前、九列 [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]；只留已确认，转升序。"""
    rows = payload.get("data") or []
    candles = [
        eng.DailyCandle(
            date=datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc).replace(tzinfo=None),
            open=float(r[1]), high=float(r[2]), low=float(r[3]), close=float(r[4]),
        )
        for r in rows if len(r) >= 9 and r[8] == "1"
    ]
    candles.sort(key=lambda c: c.date)
    return candles


def fetch_daily_candles(symbol: str) -> list[eng.DailyCandle]:
    """拉最近 300 根已确认 UTC 日 K。失败抛异常，由调用方决定降级语义。"""
    exchange = ccxt.okx({"enableRateLimit": True, "timeout": _TIMEOUT_MS})
    proxy = config.proxy_url()
    if proxy:
        exchange.httpsProxy = proxy
    payload = exchange.publicGetMarketCandles({
        "instId": symbol, "bar": "1Dutc", "limit": str(CANDLE_LIMIT),
    })
    return _parse_okx_candles(payload)


# ---------- 参数与批次 ----------

def get_settings(db) -> StrategySettings:
    """单行参数表 get-or-create（默认值即用户 2026-08-28 拍板值，定义在模型列默认里）。"""
    row = db.query(StrategySettings).first()
    if row is None:
        row = StrategySettings()
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def open_positions(db, symbol: str) -> list[StrategyPosition]:
    return (
        db.query(StrategyPosition)
        .filter(StrategyPosition.symbol == symbol, StrategyPosition.status == "open")
        .order_by(StrategyPosition.entry_at.asc())
        .all()
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_strategy_service.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add services/strategy_service.py tests/test_strategy_service.py
git commit -m "feat(strategy): 服务层第一块——OKX 1Dutc 日线解析与参数单行表"
```

---

### Task 5: 服务层——每日检查状态机（事件 + 重入场 + 推送去重）

**Files:**
- Modify: `services/strategy_service.py`
- Test: `tests/test_strategy_service.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_strategy_service.py 末尾

def _seed_b1(db, entry_price=0.70, qty=1000.0, entry_at=datetime(2026, 8, 1, 1, 0)):
    pos = StrategyPosition(symbol="VIRTUAL-USDT-SWAP", batch_label="B1", entry_at=entry_at,
                           entry_price=entry_price, quantity=qty, forecast=10, status="open")
    db.add(pos)
    db.commit()
    return pos


def _candles(closes, start=datetime(2026, 8, 1)):
    return [DailyCandle(date=start + timedelta(days=i), open=c, high=c, low=c, close=c)
            for i, c in enumerate(closes)]


class SpyChannel:
    def __init__(self):
        self.sent = []

    def send(self, title, content):
        self.sent.append((title, content))
        return True


def _run(db, monkeypatch, closes, channel):
    monkeypatch.setattr(svc, "fetch_daily_candles", lambda symbol: _candles(closes))
    return svc.run_daily_check(db=db, channel=channel)


def test_daily_ok_then_breach_pushes_once(db, monkeypatch):
    _seed_b1(db)                       # 入场 0.70@8-1 01:00
    ch = SpyChannel()
    # 平稳日：收盘远高于 soft => daily_ok 不推送
    _run(db, monkeypatch, [0.70, 0.71, 0.72], ch)
    kinds = [e.kind for e in db.query(StrategyEvent).all()]
    assert kinds == ["daily_ok"] and ch.sent == []
    # 暴跌收盘跌破 soft => stop_breach 推送一次，并登记重入场观察
    _run(db, monkeypatch, [0.70, 0.71, 0.72, 0.40], ch)
    assert [e.kind for e in db.query(StrategyEvent).all()][-1] == "stop_breach"
    assert len(ch.sent) == 1
    state = db.query(StrategySymbolState).one()
    assert state.reentry_level is not None and state.reentry_level > 0.40
    # 次日仍破线：不重复推
    _run(db, monkeypatch, [0.70, 0.71, 0.72, 0.40, 0.39], ch)
    assert len(ch.sent) == 1


def test_b2_unlocked_fires_once(db, monkeypatch):
    _seed_b1(db)
    ch = SpyChannel()
    # 大涨：soft = H*(1-4v) 抬过成本 0.70 => b2_unlocked 推一次
    _run(db, monkeypatch, [0.70, 0.90, 1.10, 1.20], ch)
    assert [e.kind for e in db.query(StrategyEvent).all()].count("b2_unlocked") == 1
    _run(db, monkeypatch, [0.70, 0.90, 1.10, 1.20, 1.25], ch)
    assert [e.kind for e in db.query(StrategyEvent).all()].count("b2_unlocked") == 1


def test_reentry_ready_after_restand(db, monkeypatch):
    pos = _seed_b1(db)
    ch = SpyChannel()
    _run(db, monkeypatch, [0.70, 0.71, 0.72, 0.40], ch)            # 破线，进观察态
    pos.status = "closed"                                           # 用户手动平仓
    db.commit()
    level = db.query(StrategySymbolState).one().reentry_level
    _run(db, monkeypatch, [0.70, 0.71, 0.72, 0.40, level * 1.05], ch)
    events = [e.kind for e in db.query(StrategyEvent).all()]
    assert events[-1] == "reentry_ready"
    assert db.query(StrategySymbolState).one().reentry_level is None   # 一次性，发完清除
    assert len(ch.sent) == 2                                            # breach + reentry


def test_reentry_expires_silently(db, monkeypatch):
    _seed_b1(db)
    ch = SpyChannel()
    _run(db, monkeypatch, [0.70, 0.71, 0.72, 0.40], ch)
    state = db.query(StrategySymbolState).one()
    state.reentry_breached_at = datetime(2026, 6, 1)                # 拨回 31+ 天前
    db.commit()
    _run(db, monkeypatch, [0.70, 0.71, 0.72, 0.40, 0.41], ch)
    events = [e.kind for e in db.query(StrategyEvent).all()]
    assert "reentry_expired" in events
    assert len(ch.sent) == 1                                            # 过期不推送
    assert db.query(StrategySymbolState).one().reentry_level is None


def test_vol_update_with_overbudget_suggests_reduce(db, monkeypatch):
    # 波动率闩锁被打破且占用超预算 => vol_update + reduce_suggest 推送
    _seed_b1(db, entry_price=0.70, qty=200000.0)                    # 巨仓保证超 15% 预算
    ch = SpyChannel()
    calm = [0.70] * 40                                              # 零波动热身
    _run(db, monkeypatch, calm + [0.70], ch)
    burst = calm + [0.70, 0.90, 0.60, 0.85, 0.55]                   # 波动率暴增，触发 25% 闩锁
    _run(db, monkeypatch, burst, ch)
    kinds = [e.kind for e in db.query(StrategyEvent).all()]
    assert "vol_update" in kinds and "reduce_suggest" in kinds
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_strategy_service.py -v`
Expected: 新增 5 个 FAIL（`AttributeError: ... has no attribute 'run_daily_check'`）

- [ ] **Step 3: 追加服务实现**

```python
# 追加到 services/strategy_service.py 末尾


def _last_status_kind(db, position_id: int) -> str | None:
    """该批次最近一次 daily_ok/stop_breach 事件的 kind，用于"未破→破"转换检测。"""
    row = (
        db.query(StrategyEvent.kind)
        .filter(StrategyEvent.position_id == position_id,
                StrategyEvent.kind.in_(["daily_ok", "stop_breach"]))
        .order_by(StrategyEvent.id.desc())
        .first()
    )
    return row[0] if row else None


def _has_event(db, position_id: int, kind: str) -> bool:
    return (
        db.query(StrategyEvent.id)
        .filter(StrategyEvent.position_id == position_id, StrategyEvent.kind == kind)
        .first()
        is not None
    )


def _emit(db, *, symbol: str, kind: str, message: str, payload: dict,
          position_id: int | None = None, push: bool = False, channel=None) -> StrategyEvent:
    """写事件；push=True 时经企业微信发出并镜像一条 AlertLog（告警页可见）。"""
    delivered = False
    if push:
        channel = channel or WeChatWorkChannel()
        title = f"【持仓策略】{message.splitlines()[0]}"
        content = message
        delivered = bool(channel.send(title, content))
        log = AlertLog(timestamp=_utc_now(), rule_name=RULE_NAME,
                       message=f"{title}\n{content}"[:8000],
                       channel="wechat_work", delivered=delivered)
        db.add(log)
    event = StrategyEvent(symbol=symbol, position_id=position_id, kind=kind,
                          message=message, payload_json=json.dumps(payload, ensure_ascii=False),
                          pushed=delivered)
    db.add(event)
    db.commit()
    return event


def run_daily_check(*, db=None, symbol: str = DEFAULT_SYMBOL, channel=None) -> list[str]:
    """每日 UTC 收盘后的核心检查（设计稿 §5）。返回本次产生的事件 kind 列表（含未推送）。"""
    own = db is None
    db = db or SessionLocal()
    produced: list[str] = []
    try:
        settings = get_settings(db)
        candles = fetch_daily_candles(symbol)
        if not candles:
            logger.warning(f"[strategy] {symbol} 无已确认日K，跳过本轮")
            return produced

        closes = [c.close for c in candles]
        vols = eng.ewma_vol_series(closes, alpha=settings.ewma_alpha)
        if not vols:
            return produced
        vol_latest = vols[-1]

        state = db.get(StrategySymbolState, symbol)
        if state is None:
            state = StrategySymbolState(symbol=symbol)
            db.add(state)
        prev_used = state.v_used
        new_used = eng.walk_latch([vol_latest], threshold=settings.vol_update_threshold,
                                  seed=prev_used)[-1]
        vol_changed = prev_used is not None and new_used != prev_used
        state.v_used = new_used
        state.v_used_at = _utc_now()
        db.commit()

        budget_usd = settings.capital * settings.risk_budget_pct
        positions = open_positions(db, symbol)
        total_occupy = 0.0
        breach_soft_level: float | None = None

        for pos in positions:
            st = eng.batch_state(
                entry_price=pos.entry_price, entry_at=pos.entry_at, quantity=pos.quantity,
                candles=candles, v_used=new_used, x_soft=settings.x_soft, x_hard=settings.x_hard,
            )
            total_occupy += st.occupy_usd
            payload = {"soft": st.soft_stop, "hard": st.hard_stop, "close": st.last_close,
                       "anchor": st.anchor_high, "v_used": new_used}

            if st.breached:
                if _last_status_kind(db, pos.id) != "stop_breach":
                    _emit(db, symbol=symbol, position_id=pos.id, kind="stop_breach", push=True,
                          channel=channel, payload=payload,
                          message=(f"{pos.batch_label} 日收盘 {st.last_close:.4f} 跌破软止损 "
                                   f"{st.soft_stop:.4f}，按框架应清仓；已进入重入场观察（30 天）"))
                    produced.append("stop_breach")
                breach_soft_level = st.soft_stop        # 最新一次覆盖（设计稿 §2.6）
            else:
                if _last_status_kind(db, pos.id) != "daily_ok":
                    pass  # 状态从破->未破的自然恢复不推送，只落 daily_ok
                _emit(db, symbol=symbol, position_id=pos.id, kind="daily_ok", payload=payload,
                      message=(f"{pos.batch_label} 收盘 {st.last_close:.4f} ≥ 软止损 "
                               f"{st.soft_stop:.4f}（余量 {st.distance_pct:+.1%}），持有"))
                produced.append("daily_ok")

            if st.locked and not _has_event(db, pos.id, "b2_unlocked"):
                _emit(db, symbol=symbol, position_id=pos.id, kind="b2_unlocked", push=True,
                      channel=channel, payload=payload,
                      message=(f"{pos.batch_label} 软止损 {st.soft_stop:.4f} 已抬过成本 "
                               f"{pos.entry_price:.4f}：锁盈，额度释放，可开始找微观确认事件"))
                produced.append("b2_unlocked")

        if breach_soft_level is not None:
            state.reentry_level = breach_soft_level
            state.reentry_breached_at = _utc_now()
            db.commit()

        if vol_changed:
            msg = f"波动率闩锁更新：{prev_used:.2%} → {new_used:.2%}"
            _emit(db, symbol=symbol, kind="vol_update", payload={"prev": prev_used, "new": new_used},
                  message=msg)
            produced.append("vol_update")
            if total_occupy > budget_usd and positions:
                target_qty = sum(
                    budget_usd / (p.entry_price - eng.batch_state(
                        entry_price=p.entry_price, entry_at=p.entry_at, quantity=p.quantity,
                        candles=candles, v_used=new_used, x_soft=settings.x_soft,
                        x_hard=settings.x_hard).soft_stop)
                    for p in positions
                    if p.entry_price > eng.batch_state(
                        entry_price=p.entry_price, entry_at=p.entry_at, quantity=p.quantity,
                        candles=candles, v_used=new_used, x_soft=settings.x_soft,
                        x_hard=settings.x_hard).soft_stop
                )
                _emit(db, symbol=symbol, kind="reduce_suggest", push=True, channel=channel,
                      payload={"total_occupy": total_occupy, "budget": budget_usd,
                               "target_qty": target_qty},
                      message=(f"波动率变更后占用 ${total_occupy:,.0f} 超预算 ${budget_usd:,.0f}，"
                               f"贴预算目标持仓约 {target_qty:,.0f} 枚"))
                produced.append("reduce_suggest")

        # 重入场观察（独立于持仓存在，用户平仓后仍继续盯，设计稿 §2.6）
        if state.reentry_level is not None and breach_soft_level is None:
            last_close = closes[-1]
            aged_days = (_utc_now() - (state.reentry_breached_at or _utc_now())).days
            if last_close > state.reentry_level:
                _emit(db, symbol=symbol, kind="reentry_ready", push=True, channel=channel,
                      payload={"level": state.reentry_level, "close": last_close},
                      message=(f"价格收盘 {last_close:.4f} 站回原止损线 {state.reentry_level:.4f} 上方，"
                               f"可按计算器评估重入场（全新批次、新预算、新止损）"))
                produced.append("reentry_ready")
                state.reentry_level = None
                state.reentry_breached_at = None
                db.commit()
            elif aged_days > REENTRY_WINDOW_DAYS:
                _emit(db, symbol=symbol, kind="reentry_expired",
                      payload={"level": state.reentry_level},
                      message=f"重入场观察满 {REENTRY_WINDOW_DAYS} 天未站回，观察结束")
                produced.append("reentry_expired")
                state.reentry_level = None
                state.reentry_breached_at = None
                db.commit()

        return produced
    finally:
        if own:
            db.close()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_strategy_service.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add services/strategy_service.py tests/test_strategy_service.py
git commit -m "feat(strategy): 每日检查状态机——转换去重推送/锁盈单发/重入场观察30天窗"
```

---

### Task 6: 服务层——overview 组装与模拟接口

**Files:**
- Modify: `services/strategy_service.py`
- Test: `tests/test_strategy_service.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_strategy_service.py 末尾

def test_overview_shape_and_stale_fallback(db, monkeypatch):
    _seed_b1(db, entry_price=0.743, qty=23590.0, entry_at=datetime(2026, 8, 26, 23, 33))
    monkeypatch.setattr(svc, "fetch_daily_candles",
                        lambda symbol: _candles([0.70, 0.7518, 0.7323], start=datetime(2026, 8, 25)))
    ov = svc.get_overview(db, symbol="VIRTUAL-USDT-SWAP")
    assert ov["verdict"] == "hold"
    b1 = ov["batches"][0]
    assert b1["soft_stop"] == pytest.approx(0.7518 * (1 - 4 * ov["v_used"]))
    assert ov["chart"]["days"][-1]["close"] == pytest.approx(0.7323)
    assert len(ov["chart"]["soft_line"]) == len(ov["chart"]["days"])
    assert ov["data_stale"] is False

    # 拉取失败 => data_stale=True 且不抛
    def boom(symbol):
        raise RuntimeError("okx down")
    monkeypatch.setattr(svc, "fetch_daily_candles", boom)
    ov2 = svc.get_overview(db, symbol="VIRTUAL-USDT-SWAP")
    assert ov2["data_stale"] is True and ov2["batches"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_strategy_service.py::test_overview_shape_and_stale_fallback -v`
Expected: FAIL（no attribute 'get_overview'）

- [ ] **Step 3: 追加实现**

```python
# 追加到 services/strategy_service.py 末尾


def _live_price(db, symbol: str) -> tuple[float | None, datetime | None]:
    """横幅现价：5m 管道最新价（instId 前缀 → PriceSnapshot symbol "BASE/USDT"）。"""
    base = symbol.split("-")[0]
    row = (
        db.query(PriceSnapshot.price, PriceSnapshot.timestamp)
        .filter(PriceSnapshot.symbol == f"{base}/USDT")
        .order_by(PriceSnapshot.timestamp.desc())
        .first()
    )
    return (row[0], row[1]) if row else (None, None)


def get_overview(db, *, symbol: str = DEFAULT_SYMBOL) -> dict:
    """页面主接口：横幅 + 图 + 批次读数。拉取失败降级 data_stale=True（设计稿 §4）。"""
    settings = get_settings(db)
    state = db.get(StrategySymbolState, symbol)
    positions = open_positions(db, symbol)
    live, live_at = _live_price(db, symbol)
    base = {
        "symbol": symbol,
        "generated_at": _utc_now().isoformat(),
        "data_stale": False,
        "live_price": live,
        "live_price_at": live_at.isoformat() if live_at else None,
        "settings": {
            "capital": settings.capital, "risk_budget_pct": settings.risk_budget_pct,
            "x_soft": settings.x_soft, "x_hard": settings.x_hard,
            "ewma_alpha": settings.ewma_alpha,
            "vol_update_threshold": settings.vol_update_threshold,
        },
        "reentry": (
            {"level": state.reentry_level,
             "breached_at": state.reentry_breached_at.isoformat() if state.reentry_breached_at else None}
            if state and state.reentry_level is not None else None
        ),
    }
    try:
        candles = fetch_daily_candles(symbol)
    except Exception as exc:
        logger.warning(f"[strategy] overview 拉取 {symbol} 失败: {exc}")
        candles = []
    if not candles:
        return {**base, "data_stale": True, "vol_latest": None, "v_used": None,
                "verdict": "no_data", "budget_usd": settings.capital * settings.risk_budget_pct,
                "total_occupy_usd": 0.0, "batches": [],
                "chart": {"days": [], "soft_line": [], "hard_current": None,
                          "cost_lines": [], "anchor_point": None, "entry_markers": []}}

    closes = [c.close for c in candles]
    vols = eng.ewma_vol_series(closes, alpha=settings.ewma_alpha)
    vol_latest = vols[-1] if vols else None
    v_used = state.v_used if state and state.v_used is not None else vol_latest

    batches = []
    total_occupy = 0.0
    any_breach = False
    for pos in positions:
        st = eng.batch_state(entry_price=pos.entry_price, entry_at=pos.entry_at,
                             quantity=pos.quantity, candles=candles, v_used=v_used,
                             x_soft=settings.x_soft, x_hard=settings.x_hard)
        total_occupy += st.occupy_usd
        any_breach = any_breach or st.breached
        batches.append({
            "id": pos.id, "batch_label": pos.batch_label,
            "entry_at": pos.entry_at.isoformat(), "entry_price": pos.entry_price,
            "quantity": pos.quantity, "forecast": pos.forecast, "note": pos.note,
            "anchor_high": st.anchor_high, "soft_stop": st.soft_stop, "hard_stop": st.hard_stop,
            "breached": st.breached, "locked": st.locked,
            "occupy_usd": st.occupy_usd, "distance_pct": st.distance_pct,
            "pnl_usd": pos.quantity * (closes[-1] - pos.entry_price),
        })

    # 图：从最早批次入场前 5 根起截窗；软止损历史 = 闩锁重放近似（设计稿 §2.3 注）
    chart_days = candles
    soft_line: list[float | None] = [None] * len(candles)
    entry_markers = []
    anchor_point = None
    if positions:
        first_entry = min(p.entry_at for p in positions)
        start_idx = max(0, next((i for i, c in enumerate(candles) if c.close_time > first_entry), len(candles)) - 5)
        chart_days = candles[start_idx:]
        replay_used = eng.walk_latch(vols, threshold=settings.vol_update_threshold)
        soft_line = []
        for i, c in enumerate(chart_days):
            gi = start_idx + i
            vu = replay_used[gi - 1] if gi - 1 < len(replay_used) and gi >= 1 else (v_used or 0.0)
            stops = [
                eng.batch_state(entry_price=p.entry_price, entry_at=p.entry_at, quantity=p.quantity,
                                candles=candles[: gi + 1], v_used=vu,
                                x_soft=settings.x_soft, x_hard=settings.x_hard).soft_stop
                for p in positions if c.close_time > p.entry_at
            ]
            soft_line.append(max(stops) if stops else None)
        for p in positions:
            entry_markers.append({"date": p.entry_at.strftime("%m-%d"), "label": p.batch_label,
                                  "value": p.entry_price})
        top = max(batches, key=lambda b: b["anchor_high"])
        anchor_candles = [c for c in chart_days if c.close == top["anchor_high"]]
        if anchor_candles:
            anchor_point = {"date": anchor_candles[-1].date.strftime("%m-%d"), "value": top["anchor_high"]}

    budget_usd = settings.capital * settings.risk_budget_pct
    verdict = "no_position" if not positions else ("breach" if any_breach else "hold")
    return {
        **base,
        "vol_latest": vol_latest, "v_used": v_used,
        "verdict": verdict, "budget_usd": budget_usd,
        "total_occupy_usd": total_occupy, "batches": batches,
        "chart": {
            "days": [{"date": c.date.strftime("%m-%d"), "close": c.close} for c in chart_days],
            "soft_line": soft_line,
            "hard_current": min((b["hard_stop"] for b in batches), default=None),
            "cost_lines": [{"label": b["batch_label"], "value": b["entry_price"]} for b in batches],
            "anchor_point": anchor_point,
            "entry_markers": entry_markers,
        },
    }


def simulate(db, *, price: float, forecast: int, vol: float | None = None,
             budget_pct: float | None = None, symbol: str = DEFAULT_SYMBOL) -> dict:
    """建仓计算器：vol/budget 缺省时用在用值与参数表值。"""
    settings = get_settings(db)
    if vol is None:
        state = db.get(StrategySymbolState, symbol)
        vol = state.v_used if state and state.v_used is not None else None
    if vol is None:
        candles = fetch_daily_candles(symbol)
        vols = eng.ewma_vol_series([c.close for c in candles], alpha=settings.ewma_alpha)
        vol = vols[-1]
    pct = budget_pct if budget_pct is not None else settings.risk_budget_pct
    budget_usd = settings.capital * pct
    sim = eng.simulate_entry(price=price, forecast=forecast, budget_usd=budget_usd,
                             vol=vol, x_soft=settings.x_soft)
    return {**sim, "vol": vol, "budget_usd": budget_usd,
            "leverage": sim["notional_usd"] / settings.capital if settings.capital else None}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_strategy_service.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add services/strategy_service.py tests/test_strategy_service.py
git commit -m "feat(strategy): overview 组装(图表序列+降级stale)与建仓计算器服务"
```

---

### Task 7: schemas + API 路由

**Files:**
- Create: `schemas/strategy.py`
- Modify: `api/routes.py`
- Test: `tests/test_strategy_api.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_strategy_api.py
# -*- coding: utf-8 -*-
"""strategy 路由冒烟：CRUD + overview + simulate（拉取全程 monkeypatch，不出网）。"""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import services.strategy_service as svc
from api.app import create_app
from services.strategy_engine import DailyCandle


def _client():
    return TestClient(create_app(enable_scheduler=False))


def _fake_candles(symbol):
    start = datetime(2026, 8, 25)
    return [DailyCandle(date=start + timedelta(days=i), open=c, high=c, low=c, close=c)
            for i, c in enumerate([0.70, 0.7518, 0.7323])]


def test_positions_crud_and_overview(monkeypatch):
    monkeypatch.setattr(svc, "fetch_daily_candles", _fake_candles)
    c = _client()

    created = c.post("/api/strategy/positions", json={
        "symbol": "VIRTUAL-USDT-SWAP", "batch_label": "B1",
        "entry_at": "2026-08-26T23:33:00", "entry_price": 0.743,
        "quantity": 23590, "forecast": 10,
    })
    assert created.status_code == 200
    pos_id = created.json()["id"]

    ov = c.get("/api/strategy/overview").json()
    assert ov["verdict"] == "hold" and len(ov["batches"]) == 1

    patched = c.patch(f"/api/strategy/positions/{pos_id}", json={"forecast": 15})
    assert patched.json()["forecast"] == 15

    sim = c.post("/api/strategy/simulate", json={"price": 0.70, "forecast": 15, "vol": 0.05})
    assert abs(sim.json()["stop_price"] - 0.56) < 1e-9

    settings = c.get("/api/strategy/settings").json()
    assert settings["capital"] == 13915.0
    updated = c.put("/api/strategy/settings", json={**settings, "capital": 20000})
    assert updated.json()["capital"] == 20000

    events = c.get("/api/strategy/events")
    assert events.status_code == 200

    deleted = c.delete(f"/api/strategy/positions/{pos_id}")
    assert deleted.status_code == 200
```

注意：TestClient 用的是真实 `market_monitor.db`（项目 API 测试现状即如此），position 建了要删，settings 测试完要把 capital 改回——在测试末尾补 `c.put("/api/strategy/settings", json=settings)` 还原原值。

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_strategy_api.py -v`
Expected: FAIL（404，路由不存在）

- [ ] **Step 3: 写 schemas**

```python
# schemas/strategy.py
# -*- coding: utf-8 -*-
"""持仓策略 API 模型。overview 结构较深且是内部页面专用，用宽松 dict 透传；
写路径（positions/settings/simulate）用严格模型校验。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class StrategyPositionCreate(BaseModel):
    symbol: str = "VIRTUAL-USDT-SWAP"
    batch_label: str
    entry_at: datetime                      # naive UTC ISO 字符串
    entry_price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    forecast: int = Field(ge=-20, le=20, default=10)
    note: str | None = None


class StrategyPositionUpdate(BaseModel):
    batch_label: str | None = None
    entry_at: datetime | None = None
    entry_price: float | None = Field(default=None, gt=0)
    quantity: float | None = Field(default=None, gt=0)
    forecast: int | None = Field(default=None, ge=-20, le=20)
    status: str | None = None               # open / closed
    closed_at: datetime | None = None
    close_price: float | None = None
    note: str | None = None


class StrategyPositionSchema(BaseModel):
    id: int
    symbol: str
    batch_label: str
    entry_at: datetime
    entry_price: float
    quantity: float
    forecast: int
    status: str
    closed_at: datetime | None
    close_price: float | None
    note: str | None

    class Config:
        from_attributes = True


class StrategySettingsSchema(BaseModel):
    capital: float = Field(gt=0)
    risk_budget_pct: float = Field(gt=0, le=1)
    x_soft: int = Field(gt=0)
    x_hard: int = Field(gt=0)
    ewma_alpha: float = Field(gt=0, lt=1)
    vol_update_threshold: float = Field(gt=0, lt=1)


class StrategySimulateRequest(BaseModel):
    price: float = Field(gt=0)
    forecast: int = Field(ge=-20, le=20, default=10)
    vol: float | None = Field(default=None, gt=0)
    budget_pct: float | None = Field(default=None, gt=0, le=1)
    symbol: str = "VIRTUAL-USDT-SWAP"


class StrategyEventSchema(BaseModel):
    id: int
    created_at: datetime
    symbol: str
    position_id: int | None
    kind: str
    message: str
    pushed: bool

    class Config:
        from_attributes = True
```

- [ ] **Step 4: 加路由** — `api/routes.py`：imports 区加

```python
from models.strategy import StrategyEvent, StrategyPosition
from schemas.strategy import (
    StrategyEventSchema,
    StrategyPositionCreate,
    StrategyPositionSchema,
    StrategyPositionUpdate,
    StrategySettingsSchema,
    StrategySimulateRequest,
)
from services import strategy_service
```

文件末尾（最后一条路由之后）加：

```python
# ---------- 持仓策略（docs/superpowers/specs/2026-08-28-position-strategy-design.md） ----------

@router.get("/strategy/overview")
def strategy_overview(symbol: str = strategy_service.DEFAULT_SYMBOL,
                      db: Session = Depends(get_db)) -> dict:
    return strategy_service.get_overview(db, symbol=symbol)


@router.get("/strategy/events", response_model=list[StrategyEventSchema])
def strategy_events(symbol: str = strategy_service.DEFAULT_SYMBOL, limit: int = 50,
                    db: Session = Depends(get_db)) -> list[StrategyEventSchema]:
    rows = (db.query(StrategyEvent).filter(StrategyEvent.symbol == symbol)
            .order_by(StrategyEvent.id.desc()).limit(min(limit, 200)).all())
    return rows


@router.get("/strategy/positions", response_model=list[StrategyPositionSchema])
def strategy_positions(db: Session = Depends(get_db)) -> list[StrategyPositionSchema]:
    return db.query(StrategyPosition).order_by(StrategyPosition.entry_at.asc()).all()


@router.post("/strategy/positions", response_model=StrategyPositionSchema)
def strategy_position_create(payload: StrategyPositionCreate,
                             db: Session = Depends(get_db)) -> StrategyPositionSchema:
    row = StrategyPosition(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/strategy/positions/{position_id}", response_model=StrategyPositionSchema)
def strategy_position_update(position_id: int, payload: StrategyPositionUpdate,
                             db: Session = Depends(get_db)) -> StrategyPositionSchema:
    row = db.get(StrategyPosition, position_id)
    if row is None:
        raise ApiError("STRATEGY_POSITION_NOT_FOUND", "批次不存在", status_code=404)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/strategy/positions/{position_id}")
def strategy_position_delete(position_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(StrategyPosition, position_id)
    if row is None:
        raise ApiError("STRATEGY_POSITION_NOT_FOUND", "批次不存在", status_code=404)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/strategy/settings", response_model=StrategySettingsSchema)
def strategy_settings_get(db: Session = Depends(get_db)) -> StrategySettingsSchema:
    return StrategySettingsSchema.model_validate(strategy_service.get_settings(db), from_attributes=True)


@router.put("/strategy/settings", response_model=StrategySettingsSchema)
def strategy_settings_put(payload: StrategySettingsSchema,
                          db: Session = Depends(get_db)) -> StrategySettingsSchema:
    row = strategy_service.get_settings(db)
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    db.commit()
    return payload


@router.post("/strategy/simulate")
def strategy_simulate(payload: StrategySimulateRequest, db: Session = Depends(get_db)) -> dict:
    return strategy_service.simulate(db, price=payload.price, forecast=payload.forecast,
                                     vol=payload.vol, budget_pct=payload.budget_pct,
                                     symbol=payload.symbol)


@router.post("/strategy/run-check")
def strategy_run_check(symbol: str = strategy_service.DEFAULT_SYMBOL) -> dict:
    """手动触发一次每日检查（验收/补跑用），语义与定时任务完全一致。"""
    produced = strategy_service.run_daily_check(symbol=symbol)
    return {"ok": True, "events": produced}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_strategy_api.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add schemas/strategy.py api/routes.py tests/test_strategy_api.py
git commit -m "feat(strategy): /api/strategy/* 九条路由——overview/事件/批次CRUD/参数/模拟/手动检查"
```

---

### Task 8: 定时任务注册 + VIRTUAL 进采集清单

**Files:**
- Modify: `api/app.py`、`config.py`
- Test: `tests/test_scheduler_timezone.py`（加参数行）

- [ ] **Step 1: 给钉死测试加行** — `tests/test_scheduler_timezone.py` 的 `@pytest.mark.parametrize` 列表加：

```python
    ("strategy_daily_check", {"hour": "8", "minute": "5"}),               # 北京 08:05 = UTC 00:05,日K确认后
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_scheduler_timezone.py -v`
Expected: 新参数 FAIL（KeyError: 'strategy_daily_check'）

- [ ] **Step 3: 注册任务** — `api/app.py`：`CRON_SCHEDULES` 字典加一行：

```python
    "strategy_daily_check": {"hour": 8, "minute": 5},                 # UTC 日K确认后的持仓策略检查
```

在 `_start_background_scheduler` 内其他 `scheduler.add_job` 同级处加（照抄相邻 job 的写法风格）：

```python
    def strategy_daily_check() -> None:
        try:
            from services.strategy_service import run_daily_check
            produced = run_daily_check()
            logger.info(f"[FastAPI Scheduler] strategy_daily_check events={produced}")
        except Exception as exc:
            logger.error(f"[FastAPI Scheduler] strategy_daily_check failed: {exc}")

    scheduler.add_job(
        strategy_daily_check,
        trigger=_cron_trigger("strategy_daily_check"),
        id="strategy_daily_check",
        max_instances=1,
        coalesce=True,
    )
```

（先看相邻 add_job 实际传参，保持一致；`coalesce` 若相邻 job 未传则也不传。）

- [ ] **Step 4: VIRTUAL 进 5m 采集** — `config.py` crypto 字典改为：

```python
    "crypto": {
        "BTC": "BTCUSDT",
        "ETH": "ETHUSDT",
        # 持仓策略标的（2026-08-29）：横幅现价走此管道，策略日线另拉 OKX 1Dutc
        "VIRTUAL": "VIRTUALUSDT",
    },
```

- [ ] **Step 5: 跑定时与全量回归**

Run: `D:/anaconda/python.exe -m pytest tests/test_scheduler_timezone.py -v`
Expected: PASS
Run: `D:/anaconda/python.exe -m pytest`
Expected: 全部通过。若有测试硬编码 crypto 清单只有 BTC/ETH 而失败，按测试意图补 VIRTUAL 进期望值（语义不变）。

- [ ] **Step 6: Commit**

```bash
git add api/app.py config.py tests/test_scheduler_timezone.py
git commit -m "feat(strategy): 北京08:05每日检查进时刻表(显式时区钉死)+VIRTUAL进OKX 5m采集"
```

---

### Task 9: 前端类型与 API 客户端

**Files:**
- Modify: `frontend/src/api/types.ts`、`frontend/src/api/client.ts`

- [ ] **Step 1: types.ts 末尾追加**

```typescript
// ---------- 持仓策略（/api/strategy/*） ----------
export interface StrategyBatchReadout {
  id: number;
  batch_label: string;
  entry_at: string;
  entry_price: number;
  quantity: number;
  forecast: number;
  note: string | null;
  anchor_high: number;
  soft_stop: number;
  hard_stop: number;
  breached: boolean;
  locked: boolean;
  occupy_usd: number;
  distance_pct: number;
  pnl_usd: number;
}

export interface StrategySettings {
  capital: number;
  risk_budget_pct: number;
  x_soft: number;
  x_hard: number;
  ewma_alpha: number;
  vol_update_threshold: number;
}

export interface StrategyOverview {
  symbol: string;
  generated_at: string;
  data_stale: boolean;
  live_price: number | null;
  live_price_at: string | null;
  vol_latest: number | null;
  v_used: number | null;
  verdict: "hold" | "breach" | "no_position" | "no_data";
  budget_usd: number;
  total_occupy_usd: number;
  settings: StrategySettings;
  reentry: { level: number; breached_at: string | null } | null;
  batches: StrategyBatchReadout[];
  chart: {
    days: { date: string; close: number }[];
    soft_line: (number | null)[];
    hard_current: number | null;
    cost_lines: { label: string; value: number }[];
    anchor_point: { date: string; value: number } | null;
    entry_markers: { date: string; label: string; value: number }[];
  };
}

export interface StrategyEvent {
  id: number;
  created_at: string;
  symbol: string;
  position_id: number | null;
  kind: string;
  message: string;
  pushed: boolean;
}

export interface StrategyPosition {
  id: number;
  symbol: string;
  batch_label: string;
  entry_at: string;
  entry_price: number;
  quantity: number;
  forecast: number;
  status: string;
  closed_at: string | null;
  close_price: number | null;
  note: string | null;
}

export interface StrategyPositionPayload {
  symbol: string;
  batch_label: string;
  entry_at: string;
  entry_price: number;
  quantity: number;
  forecast: number;
  note?: string | null;
}

export interface StrategySimulateResult {
  stop_price: number;
  stop_distance: number;
  quantity: number;
  notional_usd: number;
  vol: number;
  budget_usd: number;
  leverage: number | null;
}
```

- [ ] **Step 2: client.ts** — import 区补 `StrategyEvent, StrategyOverview, StrategyPosition, StrategyPositionPayload, StrategySettings, StrategySimulateResult`；`api` 对象末尾追加：

```typescript
  strategyOverview: (symbol?: string) =>
    request<StrategyOverview>(`/strategy/overview${buildQuery({ symbol })}`),
  strategyEvents: (params: { symbol?: string; limit?: number } = {}) =>
    request<StrategyEvent[]>(`/strategy/events${buildQuery(params)}`),
  strategyPositions: () => request<StrategyPosition[]>("/strategy/positions"),
  strategyPositionCreate: (payload: StrategyPositionPayload) =>
    request<StrategyPosition>("/strategy/positions", { method: "POST", body: JSON.stringify(payload) }),
  strategyPositionUpdate: (id: number, payload: Partial<StrategyPositionPayload> & { status?: string; close_price?: number; closed_at?: string }) =>
    request<StrategyPosition>(`/strategy/positions/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  strategyPositionDelete: (id: number) =>
    request<{ ok: boolean }>(`/strategy/positions/${id}`, { method: "DELETE" }),
  strategySettings: () => request<StrategySettings>("/strategy/settings"),
  strategySettingsUpdate: (payload: StrategySettings) =>
    request<StrategySettings>("/strategy/settings", { method: "PUT", body: JSON.stringify(payload) }),
  strategySimulate: (payload: { price: number; forecast: number; vol?: number; budget_pct?: number; symbol?: string }) =>
    request<StrategySimulateResult>("/strategy/simulate", { method: "POST", body: JSON.stringify(payload) }),
  strategyRunCheck: () => request<{ ok: boolean; events: string[] }>("/strategy/run-check", { method: "POST" })
```

- [ ] **Step 3: 类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 零错误

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts
git commit -m "feat(strategy): 前端类型与 API 客户端"
```

---

### Task 10: 展示格式化纯函数 + 专用图表组件

**Files:**
- Create: `frontend/src/pages/strategyFormat.ts`
- Create: `frontend/src/pages/strategyFormat.test.ts`
- Create: `frontend/src/components/StrategyChart.tsx`

- [ ] **Step 1: 写失败测试**

```typescript
// frontend/src/pages/strategyFormat.test.ts
import { describe, expect, it } from "vitest";
import { fmtPct, fmtUsd, verdictMeta } from "./strategyFormat";

describe("strategyFormat", () => {
  it("fmtPct 带符号两位", () => {
    expect(fmtPct(0.214)).toBe("+21.4%");
    expect(fmtPct(-0.049)).toBe("-4.9%");
    expect(fmtPct(null)).toBe("—");
  });
  it("fmtUsd 千分位", () => {
    expect(fmtUsd(3300.4)).toBe("$3,300");
    expect(fmtUsd(null)).toBe("—");
  });
  it("verdictMeta 文案与色档", () => {
    expect(verdictMeta("hold").label).toBe("持有");
    expect(verdictMeta("breach").tone).toBe("danger");
    expect(verdictMeta("no_data").label).toContain("数据");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/pages/strategyFormat.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写格式化模块**

```typescript
// frontend/src/pages/strategyFormat.ts
// 持仓策略页纯展示函数：不碰网络与组件，vitest 直测。

export function fmtPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  const pct = value * 100;
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(1)}%`;
}

export function fmtUsd(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `$${Math.round(value).toLocaleString("en-US")}`;
}

export function fmtPrice(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(4);
}

export type VerdictTone = "ok" | "danger" | "muted";

export function verdictMeta(verdict: string): { label: string; tone: VerdictTone } {
  switch (verdict) {
    case "hold":
      return { label: "持有", tone: "ok" };
    case "breach":
      return { label: "跌破软止损：按框架应清仓", tone: "danger" };
    case "no_position":
      return { label: "无持仓批次", tone: "muted" };
    default:
      return { label: "数据滞后", tone: "muted" };
  }
}

const KIND_LABEL: Record<string, string> = {
  stop_breach: "清仓提示",
  vol_update: "波动率更新",
  reduce_suggest: "减仓建议",
  b2_unlocked: "B2 额度释放",
  reentry_ready: "可评估重入场",
  reentry_expired: "重入场观察过期",
  daily_ok: "收盘检查",
};

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/pages/strategyFormat.test.ts`
Expected: PASS

- [ ] **Step 5: 写图表组件**（视觉定稿：价格线 + stepAfter 软止损 + 硬防线以下红区 + 成本虚线 + 锚金点 + 批次入场标记；参考 `.superpowers/brainstorm/185-1787902123/content/layout-v2.html`）

```tsx
// frontend/src/components/StrategyChart.tsx
// 持仓策略专用图。不复用 MultiLineChart：需要 stepAfter/散点/横向参考区，塞进共享组件会污染它。
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import type { StrategyOverview } from "../api/types";
import { EmptyState } from "./StateViews";

export function StrategyChart({ overview, height = 380 }: { overview: StrategyOverview; height?: number }) {
  const { chart } = overview;
  if (!chart.days.length) {
    return <EmptyState title="暂无日K数据（数据滞后或标的无历史）" />;
  }
  const rows = chart.days.map((d, i) => ({
    date: d.date,
    close: d.close,
    soft: chart.soft_line[i] ?? null
  }));
  const values = rows.flatMap((r) => [r.close, r.soft ?? r.close]);
  if (chart.hard_current != null) values.push(chart.hard_current);
  chart.cost_lines.forEach((c) => values.push(c.value));
  const yMin = Math.min(...values) * 0.96;
  const yMax = Math.max(...values) * 1.03;
  const entryRows = chart.entry_markers
    .map((m) => ({ date: m.date, entry: m.value, label: m.label }))
    .filter((m) => rows.some((r) => r.date === m.date));

  return (
    <div className="chart-shell" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ left: 0, right: 16, top: 8, bottom: 0 }}>
          <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
          <XAxis dataKey="date" tick={{ fill: "#94a3b8", fontSize: 12 }} minTickGap={28} />
          <YAxis
            tick={{ fill: "#94a3b8", fontSize: 12 }}
            domain={[yMin, yMax]}
            width={56}
            tickFormatter={(v: number) => v.toFixed(3)}
            allowDataOverflow
          />
          <Tooltip
            contentStyle={{ background: "#0f172a", border: "1px solid #263142", color: "#e2e8f0" }}
            formatter={(value, name) => [Number(value).toFixed(4), name === "close" ? "日收盘" : "软止损"]}
          />
          {chart.hard_current != null ? (
            <ReferenceArea y1={yMin} y2={chart.hard_current} fill="#7f1d1d" fillOpacity={0.22}
              label={{ value: `6×ATR 硬防线 ${chart.hard_current.toFixed(4)}`, position: "insideBottomLeft", fill: "#f87171", fontSize: 12 }} />
          ) : null}
          {chart.cost_lines.map((c) => (
            <ReferenceLine key={`cost-${c.label}`} y={c.value} stroke="#22d3ee" strokeDasharray="3 3"
              label={{ value: `${c.label} 成本 ${c.value.toFixed(4)}`, position: "insideTopLeft", fill: "#22d3ee", fontSize: 12 }} />
          ))}
          <Line dataKey="soft" type="stepAfter" stroke="#f59e0b" strokeWidth={2.4}
            strokeDasharray="7 4" dot={false} connectNulls name="soft" />
          <Line dataKey="close" type="monotone" stroke="#5eead4" strokeWidth={2.4} dot={false} name="close" />
          <Scatter data={entryRows} dataKey="entry" fill="#22d3ee" shape="triangle" name="入场" />
          {chart.anchor_point ? (
            <ReferenceDot x={chart.anchor_point.date} y={chart.anchor_point.value} r={5}
              fill="#fbbf24" stroke="none"
              label={{ value: "最高收盘（移动基准）", position: "top", fill: "#fbbf24", fontSize: 12 }} />
          ) : null}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 6: 类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 零错误

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/strategyFormat.ts frontend/src/pages/strategyFormat.test.ts frontend/src/components/StrategyChart.tsx
git commit -m "feat(strategy): 展示格式化纯函数(vitest)与专用图表组件"
```

---

### Task 11: 页面 + 路由 + 导航

**Files:**
- Create: `frontend/src/pages/StrategyPage.tsx`
- Modify: `frontend/src/main.tsx`、`frontend/src/components/AppShell.tsx`

- [ ] **Step 1: 写页面**（结构=视觉定稿：横幅 → 大图 → 批次表/提示流/计算器三栏；所有参数可编辑）

```tsx
// frontend/src/pages/StrategyPage.tsx
// 持仓策略页（设计稿 §6）。数据全部来自 /api/strategy/*；本页不算任何公式。
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiErrorText } from "../api/client";
import type { StrategyOverview, StrategyPositionPayload, StrategySettings, StrategySimulateResult } from "../api/types";
import { StrategyChart } from "../components/StrategyChart";
import { EmptyState } from "../components/StateViews";
import { fmtPct, fmtPrice, fmtUsd, kindLabel, verdictMeta } from "./strategyFormat";

const SYMBOL = "VIRTUAL-USDT-SWAP";
const TONE_BG: Record<string, string> = { ok: "banner-ok", danger: "banner-danger", muted: "banner-muted" };

/** datetime-local（用户本机=北京时间）→ naive UTC ISO。 */
function localInputToUtcIso(value: string): string {
  return new Date(value).toISOString().slice(0, 19);
}

export function StrategyPage() {
  const qc = useQueryClient();
  const overviewQ = useQuery({ queryKey: ["strategy-overview"], queryFn: () => api.strategyOverview(SYMBOL), refetchInterval: 60_000 });
  const eventsQ = useQuery({ queryKey: ["strategy-events"], queryFn: () => api.strategyEvents({ symbol: SYMBOL, limit: 30 }) });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["strategy-overview"] });
    qc.invalidateQueries({ queryKey: ["strategy-events"] });
  };

  if (overviewQ.isLoading) return <EmptyState title="加载中…" />;
  if (overviewQ.isError || !overviewQ.data) return <EmptyState title="加载失败，请刷新" />;
  const ov = overviewQ.data;
  const meta = verdictMeta(ov.verdict);
  const overBudget = ov.total_occupy_usd > ov.budget_usd;

  return (
    <div className="page strategy-page">
      {/* 决策横幅 */}
      <section className={`strategy-banner ${TONE_BG[meta.tone]}`}>
        <div>
          <strong>今日动作：{meta.label}</strong>
          <span className="banner-sub">
            {ov.batches[0]
              ? `昨收 ${fmtPrice(ov.chart.days.at(-1)?.close)} vs 软止损 ${fmtPrice(ov.batches[0].soft_stop)}（余量 ${fmtPct(ov.batches[0].distance_pct)}）· 检查于北京 08:05`
              : "录入批次后开始跟踪"}
          </span>
          {ov.reentry ? (
            <span className="banner-sub">重入场观察中：等待收盘站回 {fmtPrice(ov.reentry.level)}</span>
          ) : null}
          {ov.data_stale ? <span className="banner-sub">⚠ 数据滞后：OKX 拉取失败，展示可能过期</span> : null}
        </div>
        <div className="banner-kpis">
          <span className={overBudget ? "kpi-danger" : ""}>风险占用 {fmtUsd(ov.total_occupy_usd)} / 预算 {fmtUsd(ov.budget_usd)}</span>
          <span>在用波动率 {fmtPct(ov.v_used)}（最新 {fmtPct(ov.vol_latest)}）</span>
          <span>现价 {fmtPrice(ov.live_price)}</span>
        </div>
      </section>

      <StrategyChart overview={ov} />

      <div className="strategy-grid">
        <BatchPanel ov={ov} onChanged={invalidate} />
        <section className="panel">
          <h3>动作提示流</h3>
          {(eventsQ.data ?? []).length === 0 ? <p className="muted">暂无事件</p> : (
            <ul className="event-feed">
              {(eventsQ.data ?? []).map((e) => (
                <li key={e.id} className={`event-${e.kind}`}>
                  <span className="event-kind">{kindLabel(e.kind)}{e.pushed ? " ·已推送" : ""}</span>
                  <span className="event-msg">{e.message}</span>
                  <span className="event-time">{e.created_at.replace("T", " ").slice(5, 16)} UTC</span>
                </li>
              ))}
            </ul>
          )}
        </section>
        <CalculatorPanel defaultVol={ov.v_used} />
      </div>

      <SettingsPanel settings={ov.settings} onChanged={invalidate} />
    </div>
  );
}

function BatchPanel({ ov, onChanged }: { ov: StrategyOverview; onChanged: () => void }) {
  const [draft, setDraft] = useState({ batch_label: "B1", entry_at: "", entry_price: "", quantity: "", forecast: "10" });
  const [error, setError] = useState<string | null>(null);
  const createM = useMutation({
    mutationFn: (payload: StrategyPositionPayload) => api.strategyPositionCreate(payload),
    onSuccess: onChanged,
    onError: (err) => setError(apiErrorText(err, "保存失败"))
  });
  const closeM = useMutation({
    mutationFn: (vars: { id: number; close_price: number }) =>
      api.strategyPositionUpdate(vars.id, { status: "closed", close_price: vars.close_price, closed_at: new Date().toISOString().slice(0, 19) }),
    onSuccess: onChanged
  });
  const deleteM = useMutation({ mutationFn: (id: number) => api.strategyPositionDelete(id), onSuccess: onChanged });

  return (
    <section className="panel">
      <h3>批次表</h3>
      <table className="data-table">
        <thead><tr><th>批次</th><th>入场价</th><th>数量</th><th>预测值</th><th>软止损</th><th>占用</th><th>状态</th><th /></tr></thead>
        <tbody>
          {ov.batches.map((b) => (
            <tr key={b.id}>
              <td>{b.batch_label}</td>
              <td>{fmtPrice(b.entry_price)}</td>
              <td>{Math.round(b.quantity).toLocaleString()}</td>
              <td>{b.forecast > 0 ? `+${b.forecast}` : b.forecast}</td>
              <td>{fmtPrice(b.soft_stop)}{b.locked ? " 🔒已锁盈" : ""}{b.breached ? " ⚠破线" : ""}</td>
              <td>{fmtUsd(b.occupy_usd)}</td>
              <td>{fmtUsd(b.pnl_usd)} 浮动</td>
              <td>
                <button onClick={() => {
                  const p = window.prompt("平仓价格？");
                  if (p) closeM.mutate({ id: b.id, close_price: Number(p) });
                }}>平仓</button>
                <button onClick={() => { if (window.confirm("删除该批次记录？")) deleteM.mutate(b.id); }}>删</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="batch-add">
        <input placeholder="批次名 B2" value={draft.batch_label} onChange={(e) => setDraft({ ...draft, batch_label: e.target.value })} />
        <input type="datetime-local" value={draft.entry_at} onChange={(e) => setDraft({ ...draft, entry_at: e.target.value })} />
        <input placeholder="入场价" value={draft.entry_price} onChange={(e) => setDraft({ ...draft, entry_price: e.target.value })} />
        <input placeholder="数量" value={draft.quantity} onChange={(e) => setDraft({ ...draft, quantity: e.target.value })} />
        <input placeholder="预测值" value={draft.forecast} onChange={(e) => setDraft({ ...draft, forecast: e.target.value })} />
        <button
          disabled={createM.isPending}
          onClick={() => {
            setError(null);
            if (!draft.entry_at || !draft.entry_price || !draft.quantity) { setError("入场时间/价格/数量必填"); return; }
            createM.mutate({
              symbol: SYMBOL, batch_label: draft.batch_label || "B?",
              entry_at: localInputToUtcIso(draft.entry_at),
              entry_price: Number(draft.entry_price), quantity: Number(draft.quantity),
              forecast: Number(draft.forecast) || 10
            });
          }}
        >录入批次</button>
        {error ? <span className="form-error">{error}</span> : null}
      </div>
    </section>
  );
}

function CalculatorPanel({ defaultVol }: { defaultVol: number | null }) {
  const [form, setForm] = useState({ price: "", forecast: "10", vol: "", budget_pct: "" });
  const [result, setResult] = useState<StrategySimulateResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const simM = useMutation({
    mutationFn: () => api.strategySimulate({
      price: Number(form.price),
      forecast: Number(form.forecast) || 10,
      vol: form.vol ? Number(form.vol) / 100 : undefined,
      budget_pct: form.budget_pct ? Number(form.budget_pct) / 100 : undefined,
      symbol: SYMBOL
    }),
    onSuccess: setResult,
    onError: (err) => setError(apiErrorText(err, "计算失败"))
  });
  return (
    <section className="panel">
      <h3>建仓计算器</h3>
      <div className="calc-form">
        <label>价格 <input value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} /></label>
        <label>预测值 <input value={form.forecast} onChange={(e) => setForm({ ...form, forecast: e.target.value })} /></label>
        <label>波动率% <input placeholder={defaultVol != null ? (defaultVol * 100).toFixed(2) : "在用值"} value={form.vol} onChange={(e) => setForm({ ...form, vol: e.target.value })} /></label>
        <label>预算% <input placeholder="参数表值" value={form.budget_pct} onChange={(e) => setForm({ ...form, budget_pct: e.target.value })} /></label>
        <button disabled={!form.price || simM.isPending} onClick={() => { setError(null); simM.mutate(); }}>计算</button>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      {result ? (
        <ul className="calc-result">
          <li>止损价 {fmtPrice(result.stop_price)}（距离 {fmtPrice(result.stop_distance)}）</li>
          <li>应买数量 {Math.round(result.quantity).toLocaleString()} 枚</li>
          <li>名义金额 {fmtUsd(result.notional_usd)}（杠杆 {result.leverage?.toFixed(2)}×）</li>
          <li>动用预算 {fmtUsd(result.budget_usd)} · 采用波动率 {fmtPct(result.vol)}</li>
        </ul>
      ) : null}
    </section>
  );
}

function SettingsPanel({ settings, onChanged }: { settings: StrategySettings; onChanged: () => void }) {
  const [form, setForm] = useState<StrategySettings>(settings);
  const saveM = useMutation({ mutationFn: () => api.strategySettingsUpdate(form), onSuccess: onChanged });
  const fields: { key: keyof StrategySettings; label: string; scale?: number }[] = [
    { key: "capital", label: "本金 $" },
    { key: "risk_budget_pct", label: "风险预算 %", scale: 100 },
    { key: "x_soft", label: "软止损乘数" },
    { key: "x_hard", label: "硬防线乘数" },
    { key: "ewma_alpha", label: "EWMA α" },
    { key: "vol_update_threshold", label: "闩锁阈值 %", scale: 100 }
  ];
  return (
    <section className="panel settings-panel">
      <h3>参数（改了下次计算生效）</h3>
      <div className="calc-form">
        {fields.map((f) => (
          <label key={f.key}>{f.label}
            <input
              value={f.scale ? String(Number((form[f.key] * f.scale).toFixed(4))) : String(form[f.key])}
              onChange={(e) => {
                const raw = Number(e.target.value);
                setForm({ ...form, [f.key]: f.scale ? raw / f.scale : raw });
              }}
            />
          </label>
        ))}
        <button disabled={saveM.isPending} onClick={() => saveM.mutate()}>保存</button>
      </div>
    </section>
  );
}
```

- [ ] **Step 2: 接线** — `main.tsx`：import 区加 `import { StrategyPage } from "./pages/StrategyPage";`，children 里 `{ path: "market", ... }` 之后加：

```tsx
      { path: "strategy", element: <StrategyPage /> },
```

`AppShell.tsx`：lucide import 加 `Crosshair`，navItems 在「市场概览」之后插入：

```tsx
  { to: "/strategy", label: "持仓策略", icon: Crosshair },
```

- [ ] **Step 3: 样式** — `frontend/src/styles.css` 末尾追加（复用现有暗色变量的观感）：

```css
/* ---------- 持仓策略页 ---------- */
.strategy-banner { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 14px 18px; border-radius: 10px; margin-bottom: 14px; }
.strategy-banner strong { font-size: 17px; display: block; }
.banner-ok { background: rgba(5, 95, 70, 0.55); }
.banner-danger { background: rgba(127, 29, 29, 0.6); }
.banner-muted { background: rgba(51, 65, 85, 0.6); }
.banner-sub { display: block; font-size: 12.5px; color: #a7f3d0; opacity: 0.9; margin-top: 2px; }
.banner-danger .banner-sub { color: #fecaca; }
.banner-kpis { display: flex; flex-direction: column; gap: 4px; font-size: 12.5px; color: #cbd5e1; text-align: right; }
.kpi-danger { color: #f87171; font-weight: 600; }
.strategy-grid { display: grid; grid-template-columns: 1.5fr 1fr 1fr; gap: 12px; margin-top: 14px; }
.strategy-grid .panel, .settings-panel { background: #111a2b; border: 1px solid #1e293b; border-radius: 10px; padding: 12px 14px; }
.event-feed { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; max-height: 320px; overflow-y: auto; }
.event-feed li { font-size: 12.5px; display: flex; flex-direction: column; gap: 2px; border-left: 3px solid #334155; padding-left: 8px; }
.event-feed .event-kind { color: #94a3b8; }
.event-stop_breach { border-left-color: #ef4444; }
.event-reduce_suggest, .event-vol_update { border-left-color: #f59e0b; }
.event-b2_unlocked, .event-reentry_ready { border-left-color: #22c55e; }
.event-msg { color: #e2e8f0; white-space: pre-wrap; }
.event-time { color: #64748b; font-size: 11px; }
.calc-form { display: flex; flex-wrap: wrap; gap: 8px; align-items: end; }
.calc-form label { display: flex; flex-direction: column; font-size: 12px; color: #94a3b8; gap: 3px; }
.calc-form input, .batch-add input { background: #0b1322; border: 1px solid #263142; color: #e2e8f0; border-radius: 6px; padding: 6px 8px; width: 110px; }
.calc-result { margin: 10px 0 0; padding-left: 18px; font-size: 13px; color: #e2e8f0; }
.batch-add { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.form-error { color: #f87171; font-size: 12.5px; }
.settings-panel { margin-top: 14px; }
@media (max-width: 1100px) { .strategy-grid { grid-template-columns: 1fr; } }
```

- [ ] **Step 4: 类型检查 + 全量前端测试 + 构建**

Run: `cd frontend && npx tsc --noEmit && npx vitest run && npm run build`
Expected: 全部通过、构建成功

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/StrategyPage.tsx frontend/src/main.tsx frontend/src/components/AppShell.tsx frontend/src/styles.css
git commit -m "feat(strategy): 持仓策略页——决策横幅+大图+批次表/提示流/计算器/参数编辑,入导航"
```

---

### Task 12: 端到端验收 + 文档同步

**Files:**
- Modify: `ARCHITECTURE.md`、`DATAFLOW.md`、`DECISIONS.md`、`PENDING.md`、`GLOSSARY.md`（均为本地不入库文档）

- [ ] **Step 1: 全量回归**

Run: `D:/anaconda/python.exe -m pytest`
Expected: 全部通过（原有 375+ 与新增全绿）
Run: `cd frontend && npx tsc --noEmit && npx vitest run && npm run build`
Expected: 全部通过

- [ ] **Step 2: 本地实弹验收**（对照设计稿 §8）

1. `D:/anaconda/python.exe run.py api-dev` 起后端（dev 模式无调度器）。
2. 浏览器开页面 → 「持仓策略」→ 录入真实 B1：`2026-08-27 07:33`（本机北京时间输入）、价 0.7430、数量 23590、预测 +10。
3. 核对读数与设计稿 §8 基准：H=0.7518、soft≈0.6031、hard≈0.5288、占用≈$3,300（注意：若验收日晚于 2026-08-29，蜡烛序列已前移，数字按当日重算——用页面上的在用波动率手工复算一遍 `soft = H × (1 − 4v)` 即可）。
4. `POST /api/strategy/run-check`（或页面加的手动按钮/curl）：产生 daily_ok 事件，提示流可见。
5. 断网验收：临时把 `fetch_daily_candles` 指向不存在域名（或断网）刷新页面 → 横幅显示「数据滞后」，页面不白屏。
6. 图上五要素齐全：价格线、软止损阶梯线、红区、成本虚线、锚金点、B1 三角标记。

- [ ] **Step 3: 文档同步**（AGENTS.md 硬规则；这些文件不入 git，本地改即生效）

- `ARCHITECTURE.md`：模块清单加 `services/strategy_engine.py` / `strategy_service.py` / `models/strategy.py` / `StrategyPage`。
- `DATAFLOW.md`：加「OKX 1Dutc 日线（现取现算，不入库）→ strategy_daily_check（北京 08:05）→ strategy_events → 企业微信」链路。
- `DECISIONS.md`：三条口径决策——UTC 00:00 切日、闩锁持久化（不重放推导）、H = max(入场价, 入场后最高收盘)；外加重入场观察 30 天窗。
- `PENDING.md`：把 2026-08-28 那条任务状态改为「已实施待部署验收」，记录验收读数。
- `GLOSSARY.md`：若实施中向用户新解释了术语再补（按会话实际发生为准）。

- [ ] **Step 4: Commit（代码已各任务提交，此处仅收尾）**

```bash
git status --short
git add -A -- ':!*.db'
git commit -m "feat(strategy): 端到端验收收尾" --allow-empty
```

（若 Step 3 只改了 gitignore 文档则无可提交内容，跳过 commit。）

- [ ] **Step 5: 交付分支** — 用 superpowers:finishing-a-development-branch 技能走合并/PR 决策；部署由用户按 `deploy.sh` 惯例执行，部署后次日北京 08:05 观察首次自动检查与企业微信推送。
