# Polymarket × 事件池合并 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 预测市场并入研究模块——扫描降频到 1 小时、快照永久保留、AI 提案把事件挂上 Polymarket 市场、事件详情叠概率曲线、池页新增「市场定价」页签、预测页退役。

**Architecture:** 新表 `research_event_markets`(事件↔跟踪项挂接,留痕模式同新闻挂接) + `tracked_markets.market` 线归属列;`services/market_sweep.py`(AI 提案管线,仿 pool_sweep 提案确认制) + `services/event_markets.py`(挂接 CRUD 与事件市场卡);扫描门控进 `run_scan_once`;前端在两个池页加第三页签,事件详情加市场区块,删除 PredictionsPage。

**Tech Stack:** FastAPI + SQLAlchemy + SQLite(轻量迁移走 `_ensure_sqlite_schema`),DeepSeek v4-flash(**必须 `thinking: {"type": "disabled"}`**,否则思考吃光 max_tokens 返空 content——2026-08-15 实锤),Polymarket Gamma `public-search`(2026-08-28 实测可用),React + TanStack Query + vitest。

**Spec:** docs/superpowers/specs/2026-08-28-polymarket-event-pool-merge-design.md
**分支:** feat/polymarket-event-pool(已建)
**Python 一律用 `D:\anaconda\python.exe`(PATH 里的 python 是坏桩,exit 49)。测试命令统一:`D:\anaconda\python.exe -m pytest <file> -v`。**

---

## 全局约定

- 每个任务:先写失败测试 → 亲眼看它失败 → 最小实现 → 通过 → 提交。
- 提交信息用中文 conventional commits(仿 `git log` 风格),结尾加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- 新测试文件头两行照抄现有惯例:
  ```python
  import sys, os
  sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
  ```
- 内存库 fixture 照抄 tests/test_pool_sweep.py:21-27:
  ```python
  @pytest.fixture
  def session():
      engine = create_engine("sqlite:///:memory:")
      Base.metadata.create_all(bind=engine)
      s = sessionmaker(bind=engine)()
      yield s
      s.close()
  ```

---

### Task 1: 数据模型——`research_event_markets` 表 + `tracked_markets.market` 列

**Files:**
- Create: `models/event_market.py`
- Modify: `models/tracked_market.py`(加一列)、`models/__init__.py`(注册)、`database.py:114-118`(补列迁移)
- Test: `tests/test_event_markets_service.py`(新建,本任务先放模型测试)

- [ ] **Step 1.1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""事件↔预测市场挂接(spec 2026-08-28 §1):模型约束、挂接/摘下/归属列表、事件市场卡。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from database import Base
from models.event_market import ResearchEventMarket
from models.tracked_market import TrackedMarket


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_link_defaults_and_unique(session):
    link = ResearchEventMarket(event_id=1, tracked_id=2, link_source="human")
    session.add(link)
    session.commit()
    assert (link.detached, link.confidence, link.prompt_version) == (False, None, None)
    session.add(ResearchEventMarket(event_id=1, tracked_id=2, link_source="auto"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_tracked_market_line_defaults_macro(session):
    row = TrackedMarket(kind="slug", identifier="some-slug", enabled=True)
    session.add(row)
    session.commit()
    assert row.market == "macro"
```

- [ ] **Step 1.2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_event_markets_service.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'models.event_market'`

- [ ] **Step 1.3: 建模型文件 `models/event_market.py`**

```python
# -*- coding: utf-8 -*-
"""事件↔预测市场挂接(spec 2026-08-28 §1):tracked_markets(slug)粒度,
留痕模式与 research_event_links 完全对齐——摘下=标记不删行,auto 记置信度与提示词版本。
同一跟踪项可挂多个事件;跟踪项被软删后卡片不展示,挂接行留审计。"""
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, Index, UniqueConstraint
from database import Base


class ResearchEventMarket(Base):
    __tablename__ = "research_event_markets"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, nullable=False, index=True)
    tracked_id = Column(Integer, nullable=False)
    link_source = Column(String(8), nullable=False)    # auto=AI 提案人工确认 / human=手动挂
    confidence = Column(Float, nullable=True)          # 三档 0.9/0.65/0.3;仅 auto
    prompt_version = Column(String(40), nullable=True)
    detached = Column(Boolean, nullable=False, default=False)
    detach_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("event_id", "tracked_id", name="uq_research_event_market"),
        Index("ix_research_event_market_tracked", "tracked_id"),
    )
```

- [ ] **Step 1.4: `models/tracked_market.py` 加线归属列**

在 `identifier` 行之后插入:

```python
    # 线归属(spec 2026-08-28 §1):宏观/加密两池是独立页面,跟踪项要知道住哪个页面。
    # 存量默认 macro(现存种子全为 Fed/通胀/地缘题材)。
    market = Column(String(8), nullable=False, default="macro")
```

- [ ] **Step 1.5: `models/__init__.py` 注册**

在 `from models.research import ...` 行后加:

```python
from models.event_market import ResearchEventMarket
```

- [ ] **Step 1.6: `database.py` 补列迁移**

`_ensure_sqlite_schema` 中 tracked_markets 段(约 :114-118)改为:

```python
        # tracked_markets：补软删除墓碑列 + 线归属列(2026-08-28 事件池合并:存量默认宏观)。
        if "tracked_markets" in table_names:
            existing = {col["name"] for col in inspector.get_columns("tracked_markets")}
            if "dismissed" not in existing:
                conn.execute(text("ALTER TABLE tracked_markets ADD COLUMN dismissed BOOLEAN NOT NULL DEFAULT 0"))
            if "market" not in existing:
                conn.execute(text("ALTER TABLE tracked_markets ADD COLUMN market VARCHAR(8) NOT NULL DEFAULT 'macro'"))
```

(新表 `research_event_markets` 由 `create_all` 自动建,无需手写。)

- [ ] **Step 1.7: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_event_markets_service.py tests/test_tracked_markets_service.py tests/test_research_models.py -v`
Expected: 全 PASS

- [ ] **Step 1.8: 提交**

```bash
git add models/event_market.py models/tracked_market.py models/__init__.py database.py tests/test_event_markets_service.py
git commit -m "feat(models): 事件↔预测市场挂接表 + tracked_markets 线归属列——留痕模式对齐新闻挂接"
```

---

### Task 2: 配置换挡——扫描 1 小时、保留永久、宽限期联动、提案交易量门槛

**Files:**
- Modify: `config.py:136`(SCAN_INTERVALS)、`config.py:166`(grace)、`config.py:465-487`(POLYMARKET)、`config.py:748`(retention)
- Test: `tests/test_config.py`(追加)、`tests/test_data_retention.py:155`(改断言)

- [ ] **Step 2.1: 在 `tests/test_config.py` 末尾追加失败测试**

```python
def test_prediction_scan_hourly_and_grace_scales():
    """2026-08-28 事件池合并:预测扫描降频到 1 小时;宽限期随间隔联动(interval×2+30),
    否则小时节奏下单次抓取失败市场就从图上消失一小时。"""
    assert config.SCAN_INTERVALS["prediction"] == 60
    assert config.PREDICTION_ACTIVE_GRACE_MINUTES == config.SCAN_INTERVALS["prediction"] * 2 + 30


def test_prediction_retention_permanent_and_proposal_volume_floor():
    assert config.DATA_RETENTION["prediction_markets_days"] is None
    assert config.POLYMARKET["proposal_min_volume"] == 10_000
```

同时改 `tests/test_data_retention.py:155` 的断言(原 `== 30    # 不动`):

```python
    assert config.DATA_RETENTION["prediction_markets_days"] is None    # 2026-08-28 起永久
```

(该文件 :19 的 `"prediction_markets_days": 30` 是测试自带的 retention 覆盖字典,测的是清理机制本身,**不要动**。)

- [ ] **Step 2.2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_config.py tests/test_data_retention.py -v`
Expected: 新增断言 FAIL(60 != 5 等)

- [ ] **Step 2.3: 改 `config.py` 四处**

① `SCAN_INTERVALS`(:133-137)的 prediction 值:

```python
    "prediction": 60,   # 2026-08-28 起小时级:降频+永久保留替代"5min+30天滚动删除"
```

② `PREDICTION_ACTIVE_GRACE_MINUTES`(:166)默认值改为随间隔联动:

```python
PREDICTION_ACTIVE_GRACE_MINUTES = int(os.getenv(
    "PREDICTION_ACTIVE_GRACE_MINUTES",
    str(SCAN_INTERVALS["prediction"] * 2 + 30),   # 小时扫描下=150:单次抓取失败不掉图
))
```

③ `POLYMARKET` 字典(:465-487)加一项(放 `gamma_url` 之后):

```python
    # AI 提案管线的垃圾市场门槛(USD):public-search 候选低于此交易量直接不要。
    # 手动搜索通道不受此限(人是有意找的)。
    "proposal_min_volume": 10_000,
```

④ `DATA_RETENTION`(:748):

```python
    "prediction_markets_days": None,  # 2026-08-28 起永久(小时级快照,年增量约 30 万行)
```

- [ ] **Step 2.4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_config.py tests/test_data_retention.py tests/test_predictions_active_filter.py -v`
Expected: 全 PASS。若 `test_predictions_active_filter.py` 因宽限期从 30→150 失败(它按相对时间造数据),在该文件受影响测试里固定旧值:`monkeypatch.setattr(config, "PREDICTION_ACTIVE_GRACE_MINUTES", 30)`——测的是"宽限期语义",不是默认值大小。

- [ ] **Step 2.5: 提交**

```bash
git add config.py tests/test_config.py tests/test_data_retention.py tests/test_predictions_active_filter.py
git commit -m "feat(config): 预测扫描 5min→60min、快照永久保留、宽限期随间隔联动 150、提案交易量门槛 1 万刀"
```

---

### Task 3: 小时门控——`prediction_scan_due` + scan_runtime 接线 + 跳过状态

**Files:**
- Modify: `scanners/prediction_scanner.py`(加模块级函数)、`services/scan_runtime.py:234-236`(门控)、`services/scan_runtime.py:268`(日志过滤)
- Test: `tests/test_prediction_scan_gate.py`(新建)

- [ ] **Step 3.1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""小时门控(spec 2026-08-28 §2):基准取 DB 最新快照,重启不丢节拍、失败下轮自愈。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.prediction import PredictionMarket
from scanners.prediction_scanner import prediction_scan_due
from services.time_utils import utc_now_naive


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _snapshot(s, minutes_ago: int):
    now = utc_now_naive()
    s.add(PredictionMarket(timestamp=now - timedelta(minutes=minutes_ago),
                           market_id="m1", question="q", outcome="Yes", probability=0.5))
    s.commit()


def test_due_on_empty_table(session, monkeypatch):
    monkeypatch.setitem(config.SCAN_INTERVALS, "prediction", 60)
    assert prediction_scan_due(session) is True


def test_not_due_when_fresh(session, monkeypatch):
    monkeypatch.setitem(config.SCAN_INTERVALS, "prediction", 60)
    _snapshot(session, minutes_ago=30)
    assert prediction_scan_due(session) is False


def test_due_when_interval_elapsed(session, monkeypatch):
    monkeypatch.setitem(config.SCAN_INTERVALS, "prediction", 60)
    _snapshot(session, minutes_ago=61)
    assert prediction_scan_due(session) is True
```

- [ ] **Step 3.2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_prediction_scan_gate.py -v`
Expected: FAIL,`ImportError: cannot import name 'prediction_scan_due'`

- [ ] **Step 3.3: 实现 `prediction_scan_due`**

`scanners/prediction_scanner.py` 顶部 import 区加 `from datetime import timedelta`、`from sqlalchemy import func`,文件末尾(类外)加:

```python
def prediction_scan_due(session, now: datetime | None = None) -> bool:
    """小时门控(spec 2026-08-28 §2):表内最新快照距 now ≥ SCAN_INTERVALS['prediction']
    分钟才到点。基准取 DB 不取内存——重启不丢节拍;Gamma 全挂那轮没写快照,
    下轮 5 分钟 scan_cycle 自动重试(自愈,优于独立小时 job)。"""
    interval = max(1, int(config.SCAN_INTERVALS.get("prediction", 60)))
    latest = session.query(func.max(PredictionMarket.timestamp)).scalar()
    if latest is None:
        return True
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return now - latest >= timedelta(minutes=interval)
```

- [ ] **Step 3.4: scan_runtime 接线**

`services/scan_runtime.py`:

① 函数内 import 区(:210 附近)保持 `from scanners.prediction_scanner import PredictionScanner` 并追加 `prediction_scan_due`;同处加 `from scanners.base import SourceFetchStatus`、`from database import get_session`。

② :234-236 的三行替换为:

```python
        pred_scanner = PredictionScanner()
        gate_session = get_session()
        try:
            pred_due = prediction_scan_due(gate_session)
        finally:
            gate_session.close()
        if pred_due:
            logger.info("[Scan] 开始预测市场扫描...")
            pred_records = pred_scanner.scan()
        else:
            # 未到间隔:跳过但要在源健康面板留"主动跳过"痕迹,不算采集异常也不算空手
            logger.info("[Scan] 预测市场未到 {} 分钟间隔,本轮跳过", config.SCAN_INTERVALS.get("prediction", 60))
            pred_records = []
            pred_scanner.source_statuses = [SourceFetchStatus(
                source="polymarket", ok=True, record_count=0, empty=False, stage="skipped")]
```

(scan_runtime 顶部若无 `import config` 则补上;`logger.info` 用 loguru 花括号风格与文件内一致。)

③ `_log_source_statuses`(:268)的 empty 过滤加 skipped:

```python
        empty = [s for s in statuses if s["ok"] and s["empty"] and s.get("stage") not in ("closed", "skipped")]
```

- [ ] **Step 3.5: 告警小时粒度回溯测试(spec §7 点名)**

在 `tests/test_alert_engine.py` 追加用例,**沿用该文件既有的规则构造与 `get_alert_session` 替换方式**(先读它的现有 prediction_shift 用例,照最近的一个改):

```python
def test_prediction_shift_reads_hourly_baseline(...沿用该文件 fixture...):
    """小时扫描(2026-08-28)下 window_minutes=15 的回溯自然取到上一小时快照:
    阈值 5pp 不动,语义从"15 分钟跳变"变为"逐小时跳变"。"""
    now = utc_now_naive()
    # 上一小时快照 0.50,本轮记录 0.57 → shift 7pp ≥ 5 → 触发
    session.add(PredictionMarket(timestamp=now - timedelta(minutes=60), market_id="m1",
                                 question="q?", outcome="Yes", probability=0.50))
    session.add(PredictionMarket(timestamp=now, market_id="m1", question="q?",
                                 outcome="Yes", probability=0.57, prev_probability=0.50))
    session.commit()
    records = [PredictionRecord(market_id="m1", question="q?", outcome="Yes", probability=0.57)]
    engine.evaluate_predictions(records)
    assert len(dispatched) == 1        # dispatched 的捕获方式照该文件既有用例
```

Run: `D:\anaconda\python.exe -m pytest tests/test_alert_engine.py -v`
Expected: 新用例 PASS(评估代码零改动,这是"钉住兼容行为"的回归钉)

- [ ] **Step 3.6: 跑测试确认通过 + 回归**

Run: `D:\anaconda\python.exe -m pytest tests/test_prediction_scan_gate.py tests/test_scan_runtime_crypto.py tests/test_source_health.py tests/test_alert_engine.py -v`
Expected: 全 PASS

- [ ] **Step 3.7: 提交**

```bash
git add scanners/prediction_scanner.py services/scan_runtime.py tests/test_prediction_scan_gate.py tests/test_alert_engine.py
git commit -m "feat(scan): 预测市场小时门控进 scan_cycle——DB 基准自愈重试,跳过轮记 stage=skipped,告警小时回溯钉回归"
```

---

### Task 4: Gamma 搜索——client.search_events + 候选归一化

**Files:**
- Modify: `scanners/sources/polymarket/client.py`
- Create: `services/market_sweep.py`(本任务只放候选归一化,AI 管线在 Task 6)
- Test: `tests/test_market_sweep.py`(新建)

- [ ] **Step 4.1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""市场提案管线(spec 2026-08-28 §3):候选归一化、防幻觉解析、run/apply。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import config
from services import market_sweep


def _search_event(**kw):
    base = {
        "slug": "fed-rate-cut-by-629", "title": "Fed rate cut by...?",
        "description": "Resolves Yes if the Fed cuts rates.", "endDate": "2027-01-08T04:59:00Z",
        "active": True, "closed": False, "archived": False, "volume": 3_239_062.96,
        "markets": [{"question": "Fed rate cut by January 2026 meeting?",
                     "outcomes": '["Yes", "No"]', "outcomePrices": '["0.62", "0.38"]'}],
    }
    base.update(kw)
    return base


def test_candidate_normalizes_single_market_event():
    c = market_sweep._candidate(_search_event())
    assert c["slug"] == "fed-rate-cut-by-629"
    assert c["current_probability"] == pytest.approx(0.62)
    assert (c["market_count"], c["end_date"]) == (1, "2027-01-08")


def test_candidate_drops_closed_and_low_volume():
    assert market_sweep._candidate(_search_event(closed=True)) is None
    assert market_sweep._candidate(_search_event(volume=9_999)) is None


def test_candidate_multi_market_has_no_single_probability():
    ev = _search_event(markets=[{"outcomes": '["Yes","No"]', "outcomePrices": '["0.1","0.9"]'},
                                {"outcomes": '["Yes","No"]', "outcomePrices": '["0.2","0.8"]'}])
    c = market_sweep._candidate(ev)
    assert c["current_probability"] is None and c["market_count"] == 2
```

- [ ] **Step 4.2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_market_sweep.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'services.market_sweep'`

- [ ] **Step 4.3: client 加搜索方法**

`scanners/sources/polymarket/client.py` 在 `get_markets_by_slug` 之后加:

```python
    def search_events(self, query: str, limit_per_type: int = 5) -> list[dict]:
        """public-search:按英文关键词搜事件(spec 2026-08-28 §0 实测)。
        返回事件 dict 列表(含嵌套 markets);非 200 或坏形状返回空列表,网络异常上抛。"""
        url = f"{self.gamma_url}/public-search"
        response = self.request_with_retry(url, {"q": query, "limit_per_type": limit_per_type})
        if response and response.status_code == 200:
            data = response.json()
            events = data.get("events", []) if isinstance(data, dict) else []
            return events if isinstance(events, list) else []
        return []
```

- [ ] **Step 4.4: 建 `services/market_sweep.py`(候选归一化部分)**

```python
# -*- coding: utf-8 -*-
"""事件池·找市场提案(spec 2026-08-28 §3):三入口一管线——
素材 → AI①英文搜索词 → Gamma public-search → AI②配对打分(剔价格目标类) → 提案。
提案不落库,勾选走 apply(与 pool_sweep 提案确认制同型);防幻觉=slug 白名单。"""
from __future__ import annotations

import json
import re
import threading

from loguru import logger
from sqlalchemy.orm import Session

import config
from models.event_market import ResearchEventMarket
from models.news import NewsItem
from models.research import ResearchEvent, ResearchEventLink
from models.tracked_market import TrackedMarket
from scanners.sources.polymarket.client import PolymarketGammaClient
from scanners.sources.polymarket.parser import _json_list
from scanners.sources.polymarket.source import PolymarketSource
from services.deepseek_client import call_deepseek_chat
from services.event_linking import MARKET_EVENT_TYPE, VALID_CONFIDENCES, _active_events

MARKET_SWEEP_PROMPT_VERSION = "market-sweep-v1-20260828"


def _yes_probability(markets: list) -> float | None:
    """单市场事件取 Yes 概率;多子市场(降息分桶类)无单一概率,返回 None(spec §4)。"""
    if len(markets) != 1 or not isinstance(markets[0], dict):
        return None
    outcomes = _json_list(markets[0].get("outcomes", ""))
    prices = _json_list(markets[0].get("outcomePrices", ""))
    if (not isinstance(outcomes, list) or not isinstance(prices, list)
            or not outcomes or len(outcomes) != len(prices)):
        return None
    for i, outcome in enumerate(outcomes):
        if str(outcome).strip().lower() == "yes":
            try:
                prob = float(prices[i])
            except (TypeError, ValueError):
                return None
            return prob if 0.0 <= prob <= 1.0 else None
    return None


def _candidate(event: dict, min_volume: float | None = None) -> dict | None:
    """public-search 事件 → 提案候选。剔已关闭/未活跃/低交易量;布尔字段可能是字符串,
    复用 PolymarketSource 的判定。"""
    if not isinstance(event, dict):
        return None
    slug = str(event.get("slug") or "").strip()
    title = str(event.get("title") or "").strip()
    if not slug or not title:
        return None
    if PolymarketSource._is_closed_or_inactive_market(event):
        return None
    try:
        volume = float(event.get("volume") or 0)
    except (TypeError, ValueError):
        volume = 0.0
    floor = float(config.POLYMARKET.get("proposal_min_volume", 10_000)) if min_volume is None else min_volume
    if volume < floor:
        return None
    markets = event.get("markets") if isinstance(event.get("markets"), list) else []
    return {
        "slug": slug,
        "title": title[:200],
        "description": str(event.get("description") or "")[:200],
        "volume": volume,
        "end_date": str(event.get("endDate") or "")[:10],
        "market_count": len(markets) or 1,
        "current_probability": _yes_probability(markets),
    }
```

- [ ] **Step 4.5: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_market_sweep.py tests/test_polymarket_source_db.py -v`
Expected: 全 PASS

- [ ] **Step 4.6: 提交**

```bash
git add scanners/sources/polymarket/client.py services/market_sweep.py tests/test_market_sweep.py
git commit -m "feat(polymarket): Gamma public-search 客户端 + 提案候选归一化(闭市/低量剔除,单市场取 Yes 概率)"
```

---

### Task 5: event_markets 服务——挂接/摘下/归属列表/事件市场卡

**Files:**
- Create: `services/event_markets.py`
- Modify: `schemas/predictions.py`(PredictionMarketSummary 加 origin)
- Test: `tests/test_event_markets_service.py`(追加)

- [ ] **Step 5.1: 追加失败测试(接 Task 1 的文件)**

```python
# --- 追加到 tests/test_event_markets_service.py ---
from datetime import timedelta

from models.prediction import PredictionMarket
from models.research import ResearchEvent
from services import event_markets
from services.time_utils import utc_now_naive


def _event(s, name="俄乌停火", event_type="macro", status="active"):
    e = ResearchEvent(name=name, event_type=event_type, status=status, display_no=1)
    s.add(e); s.commit()
    return e


def _tracked(s, slug="ceasefire-2026", market="macro", dismissed=False):
    t = TrackedMarket(kind="slug", identifier=slug, market=market,
                      enabled=True, dismissed=dismissed)
    s.add(t); s.commit()
    return t


def test_attach_detach_and_human_reattach(session):
    e, t = _event(session), _tracked(session)
    link = event_markets.attach_market(session, e.id, t.id)
    assert (link.link_source, link.detached) == ("human", False)
    # 幂等:重复挂返回同一行
    assert event_markets.attach_market(session, e.id, t.id).id == link.id
    assert event_markets.detach_market(session, link.id, "配错了") is True
    session.refresh(link)
    assert link.detached is True and link.detach_reason == "配错了"
    # 人工复挂撤销摘下
    relink = event_markets.attach_market(session, e.id, t.id)
    assert relink.id == link.id and relink.detached is False


def test_attach_rejects_missing_targets(session):
    e, t = _event(session), _tracked(session, dismissed=True)
    with pytest.raises(ValueError):
        event_markets.attach_market(session, e.id, t.id)          # 跟踪项已删
    with pytest.raises(ValueError):
        event_markets.attach_market(session, 999, t.id)           # 事件不存在


def test_list_event_markets_summary_and_settled(session, monkeypatch):
    monkeypatch.setattr(config, "PREDICTION_ACTIVE_GRACE_MINUTES", 150)
    e = _event(session)
    t_live, t_stale = _tracked(session, "live-slug"), _tracked(session, "stale-slug")
    event_markets.attach_market(session, e.id, t_live.id)
    event_markets.attach_market(session, e.id, t_stale.id)
    now = utc_now_naive()
    for minutes_ago, origin, market_id, prob in [
        (30, "slug:live-slug", "m-live", 0.62),
        (400, "slug:stale-slug", "m-stale", 0.30),
    ]:
        session.add(PredictionMarket(timestamp=now - timedelta(minutes=minutes_ago),
                                     market_id=market_id, question="q?", outcome="Yes",
                                     probability=prob, origin=origin))
    session.commit()
    items = {i["slug"]: i for i in event_markets.list_event_markets(session, e.id)}
    assert items["live-slug"]["settled"] is False
    assert items["live-slug"]["markets"][0].outcomes[0].probability == pytest.approx(0.62)
    assert items["stale-slug"]["settled"] is True                 # 落后表内最新超宽限期


def test_links_for_tracked_returns_briefs(session):
    e, t = _event(session), _tracked(session)
    link = event_markets.attach_market(session, e.id, t.id)
    briefs = event_markets.links_for_tracked(session, [t.id])
    assert briefs[t.id][0] == {"link_id": link.id, "event_id": e.id,
                               "display_no": 1, "name": "俄乌停火"}
```

同时在文件顶部 import 区补 `import config`。

- [ ] **Step 5.2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_event_markets_service.py -v`
Expected: 新用例 FAIL(`No module named 'services.event_markets'`)

- [ ] **Step 5.3: `schemas/predictions.py` 的 `PredictionMarketSummary` 加字段**

```python
class PredictionMarketSummary(BaseModel):
    market_id: str
    question: str
    volume: float | None = None
    outcomes: list[PredictionRow]
    has_shift: bool
    # 来源跟踪项 "slug:<identifier>"(2026-08-28):前端据此把市场映射回跟踪项/挂接事件
    origin: str | None = None
```

- [ ] **Step 5.4: 建 `services/event_markets.py`**

```python
# -*- coding: utf-8 -*-
"""事件↔预测市场挂接:读取/挂接/摘下(spec 2026-08-28 §1/§4)。
规矩与新闻挂接一致:摘下留痕不删行;auto 摘过的机器不挂回(apply 层跳过),
人工挂接(human)是明确意图、可撤销摘下。"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

import config
from models.event_market import ResearchEventMarket
from models.prediction import PredictionMarket
from models.research import ResearchEvent
from models.tracked_market import TrackedMarket
from schemas.predictions import PredictionMarketSummary
from services import prediction_service


def links_for_tracked(session: Session, tracked_ids: list[int]) -> dict[int, list[dict]]:
    """跟踪管理表的归属列:tracked_id → [{link_id, event_id, display_no, name}]。"""
    if not tracked_ids:
        return {}
    rows = (session.query(ResearchEventMarket, ResearchEvent)
            .join(ResearchEvent, ResearchEvent.id == ResearchEventMarket.event_id)
            .filter(ResearchEventMarket.tracked_id.in_(tracked_ids),
                    ResearchEventMarket.detached.is_(False))
            .order_by(ResearchEventMarket.id.asc())
            .all())
    out: dict[int, list[dict]] = defaultdict(list)
    for link, event in rows:
        out[int(link.tracked_id)].append({
            "link_id": int(link.id), "event_id": int(event.id),
            "display_no": int(event.display_no or event.id), "name": event.name,
        })
    return dict(out)


def attach_market(session: Session, event_id: int, tracked_id: int,
                  source: str = "human") -> ResearchEventMarket:
    event = (session.query(ResearchEvent)
             .filter(ResearchEvent.id == int(event_id), ResearchEvent.status == "active")
             .first())
    if event is None:
        raise ValueError("事件不存在或非进行中")
    tracked = (session.query(TrackedMarket)
               .filter(TrackedMarket.id == int(tracked_id), TrackedMarket.dismissed.is_(False))
               .first())
    if tracked is None:
        raise ValueError("跟踪项不存在或已删除")
    link = (session.query(ResearchEventMarket)
            .filter_by(event_id=int(event_id), tracked_id=int(tracked_id)).first())
    if link is not None:
        if link.detached and source == "human":
            link.detached = False
            link.detach_reason = None
            link.link_source = "human"
        session.commit()
        return link
    link = ResearchEventMarket(event_id=int(event_id), tracked_id=int(tracked_id),
                               link_source=source)
    session.add(link)
    session.commit()
    return link


def detach_market(session: Session, link_id: int, reason: str | None = None) -> bool:
    link = session.query(ResearchEventMarket).filter(ResearchEventMarket.id == int(link_id)).first()
    if link is None:
        return False
    link.detached = True
    link.detach_reason = (reason or "").strip() or None
    session.commit()
    return True


def list_event_markets(session: Session, event_id: int) -> list[dict]:
    """事件详情市场卡:每条未摘挂接 → 跟踪项 + 旗下各市场最新概率摘要 + 断流判定。
    断流(settled)=该跟踪项最新快照落后表内最新超宽限期——结算/停更都长这样,
    曲线定格、卡片打徽章;跟踪项被软删则整卡不显示(挂接行留审计)。"""
    links = (session.query(ResearchEventMarket)
             .filter(ResearchEventMarket.event_id == int(event_id),
                     ResearchEventMarket.detached.is_(False))
             .order_by(ResearchEventMarket.created_at.asc(), ResearchEventMarket.id.asc())
             .all())
    if not links:
        return []
    tracked_rows = {int(t.id): t for t in session.query(TrackedMarket)
                    .filter(TrackedMarket.id.in_([l.tracked_id for l in links])).all()}
    table_latest = session.query(func.max(PredictionMarket.timestamp)).scalar()
    grace = timedelta(minutes=max(1, int(config.PREDICTION_ACTIVE_GRACE_MINUTES)))
    items: list[dict] = []
    for link in links:
        tracked = tracked_rows.get(int(link.tracked_id))
        if tracked is None or tracked.dismissed:
            continue
        origin = f"{tracked.kind}:{tracked.identifier}"
        rows = (session.query(PredictionMarket)
                .filter(PredictionMarket.origin == origin)
                .order_by(PredictionMarket.timestamp.desc())
                .limit(200).all())
        latest = prediction_service.latest_predictions(rows)
        by_market: dict[str, list[PredictionMarket]] = defaultdict(list)
        for row in latest:
            by_market[row.market_id].append(row)
        summaries: list[PredictionMarketSummary] = []
        newest = None
        for market_id, outcomes in by_market.items():
            ordered = sorted(outcomes, key=lambda item: item.outcome)
            summaries.append(PredictionMarketSummary(
                market_id=market_id,
                question=ordered[0].question,
                volume=ordered[0].volume,
                origin=origin,
                outcomes=[prediction_service._row_schema(r) for r in ordered],
                has_shift=any(r.prev_probability is not None
                              and abs(r.probability - r.prev_probability) >= 0.03
                              for r in ordered),
            ))
            market_newest = max(r.timestamp for r in ordered)
            if newest is None or market_newest > newest:
                newest = market_newest
        settled = bool(summaries) and table_latest is not None and newest is not None \
            and newest < table_latest - grace
        items.append({
            "link_id": int(link.id), "tracked_id": int(tracked.id),
            "slug": tracked.identifier, "display_name": tracked.display_name,
            "market": tracked.market or "macro", "enabled": bool(tracked.enabled),
            "link_source": link.link_source, "confidence": link.confidence,
            "settled": settled,
            "waiting_first_scan": not summaries,   # 新挂市场首轮采集前的占位标记
            "markets": summaries,
        })
    return items
```

- [ ] **Step 5.5: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_event_markets_service.py -v`
Expected: 全 PASS

- [ ] **Step 5.6: 提交**

```bash
git add services/event_markets.py schemas/predictions.py tests/test_event_markets_service.py
git commit -m "feat(research): 事件市场挂接服务——attach/detach 留痕、归属列表、事件市场卡含断流判定"
```

---

### Task 6: market_sweep AI 管线——提示词、防幻觉解析、run、apply

**Files:**
- Modify: `services/market_sweep.py`(接 Task 4 文件续写)
- Test: `tests/test_market_sweep.py`(追加)

- [ ] **Step 6.1: 追加失败测试**

```python
# --- 追加到 tests/test_market_sweep.py ---
import json
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.event_market import ResearchEventMarket
from models.news import NewsItem
from models.research import ResearchEvent
from models.tracked_market import TrackedMarket
from services.time_utils import utc_now_naive


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _event(s, name="俄乌停火", event_type="macro"):
    e = ResearchEvent(name=name, event_type=event_type, status="active", display_no=1)
    s.add(e); s.commit()
    return e


class FakeGamma:
    def __init__(self, events):
        self._events = events

    def search_events(self, query, limit_per_type=5):
        return self._events


def test_parse_matches_whitelist_and_price_target_drop():
    candidates = {7: {"good-slug": {"title": "t"}, "pt-slug": {"title": "p"}}}
    raw = json.dumps({"matches": [
        {"event_id": 7, "slug": "good-slug", "confidence": 0.9, "price_target": False, "reason": "对"},
        {"event_id": 7, "slug": "pt-slug", "confidence": 0.9, "price_target": True, "reason": "价格影子"},
        {"event_id": 7, "slug": "hallucinated", "confidence": 0.9, "price_target": False},
        {"event_id": 99, "slug": "good-slug", "confidence": 0.9, "price_target": False},
        {"event_id": 7, "slug": "good-slug", "confidence": 0.5, "price_target": False},
    ]})
    matches, dropped = market_sweep._parse_matches(raw, candidates)
    assert matches == [{"event_id": 7, "slug": "good-slug", "confidence": 0.9, "reason": "对"}]
    assert dropped == 1


def test_run_market_sweep_end_to_end(session, monkeypatch):
    e = _event(session)
    canned_terms = json.dumps({"terms": [{"event_id": e.id, "queries": ["russia ukraine ceasefire"]}]})
    canned_pairs = json.dumps({"matches": [{"event_id": e.id, "slug": "ceasefire-2026",
                                            "confidence": 0.9, "price_target": False,
                                            "reason": "就是这件事"}]})
    replies = iter([(canned_terms, 1.0), (canned_pairs, 2.0)])
    monkeypatch.setattr(market_sweep, "_call_market_ai", lambda *a, **k: next(replies))
    gamma = FakeGamma([{
        "slug": "ceasefire-2026", "title": "Russia x Ukraine ceasefire in 2026?",
        "description": "...", "endDate": "2026-12-31T00:00:00Z",
        "active": True, "closed": False, "archived": False, "volume": 500_000,
        "markets": [{"outcomes": '["Yes","No"]', "outcomePrices": '["0.41","0.59"]'}],
    }])
    out = market_sweep.run_market_sweep(session, event_type="macro", client=gamma)
    assert out["scanned_events"] == 1 and out["candidates"] == 1
    p = out["proposals"][0]
    assert (p["slug"], p["event_id"], p["confidence"]) == ("ceasefire-2026", e.id, 0.9)
    assert p["current_probability"] == pytest.approx(0.41)
    # run 全程不落库
    assert session.query(TrackedMarket).count() == 0
    assert session.query(ResearchEventMarket).count() == 0


def test_apply_creates_revives_links_idempotently(session):
    e = _event(session)
    # 预置一个已软删的同名 slug:apply 应复活而非新建
    session.add(TrackedMarket(kind="slug", identifier="revive-me", market="macro",
                              enabled=False, dismissed=True))
    session.commit()
    items = [
        {"event_id": e.id, "slug": "brand-new", "title": "New market", "confidence": 0.9},
        {"event_id": e.id, "slug": "revive-me", "title": "Old market", "confidence": 0.65},
    ]
    out = market_sweep.apply_market_proposals(session, "macro", items)
    assert (out["added"], out["revived"], out["linked"]) == (["brand-new"], ["revive-me"], 2)
    revived = session.query(TrackedMarket).filter_by(identifier="revive-me").one()
    assert (revived.dismissed, revived.enabled) == (False, True)
    # 重复提交:全部跳过、零新增
    out2 = market_sweep.apply_market_proposals(session, "macro", items)
    assert out2["linked"] == 0 and len(out2["skipped"]) == 2
    # 已摘下的不挂回
    link = session.query(ResearchEventMarket).filter_by(tracked_id=revived.id).one()
    link.detached = True
    session.commit()
    out3 = market_sweep.apply_market_proposals(session, "macro", [items[1]])
    assert out3["linked"] == 0 and "已摘下" in out3["skipped"][0]


def test_run_rejects_bad_inputs(session):
    with pytest.raises(ValueError):
        market_sweep.run_market_sweep(session, event_type="stocks")
    with pytest.raises(ValueError):
        market_sweep.run_market_sweep(session, event_type="macro", event_id=12345)
```

- [ ] **Step 6.2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_market_sweep.py -v`
Expected: 新用例 FAIL(缺 `_parse_matches` / `run_market_sweep` / `apply_market_proposals`)

- [ ] **Step 6.3: `services/market_sweep.py` 续写管线**

在 Task 4 代码之后追加(文件级):

```python
SEARCH_TERMS_PROMPT = (
    "你是宏观/加密研究助理。输入是一批研究事件(中文名+免闸关键词+近期新闻标题)。\n"
    "为每个事件生成 1-3 组**英文搜索词**,用于在 Polymarket(英文预测市场)搜索相关市场。\n"
    "要求:每组 2-6 个英文单词;具体优先(实体名、专有名词、政策名);同一事件多组词角度错开。\n"
    "事件与预测市场明显无缘(纯行情波动、公司财报点评)就给空列表。\n"
    '只返回 JSON,不要 Markdown:{"terms": [{"event_id": 1, "queries": ["russia ukraine ceasefire"]}]}\n'
    "event_id 必须来自输入。"
)

PAIR_PROMPT = (
    "你是研究助理。输入是【研究事件】(中文)和每个事件搜到的【候选预测市场】(英文)。\n"
    "判断每个候选是否是对应事件结局的市场定价。规则:\n"
    "- 只挂真正相关的:市场的结算条件必须与事件走向/结局直接相关,主题擦边不算;不相关的候选不要输出。\n"
    "- 剔除纯价格目标类:结算条件为某资产价格达到/越过某数值的(如 'Will BTC reach $150k'、"
    "'oil above $100'),一律标 price_target=true——它们是价格的影子,不是事件概率。\n"
    "- confidence 三档:0.9=明确就是这件事;0.65=大概率相关;0.3=勉强沾边(倾向不挂)。\n"
    '只返回 JSON,不要 Markdown:{"matches": [{"event_id": 1, "slug": "xxx", "confidence": 0.9,'
    ' "price_target": false, "reason": "一句话中文理由"}]}\n'
    "slug 必须来自该事件的候选列表。"
)

_MARKET_SWEEP_LOCK = threading.Lock()


class MarketSweepBusy(RuntimeError):
    """已有一次找市场提案在进行中(路由层译成 409)。"""


def _json_loads_loose(raw: str) -> dict:
    """剥 Markdown 围栏后解析(与 pool_sweep._parse_sweep 同一容错)。"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            raise ValueError(f"AI 返回非 JSON: {text[:200]}")
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("AI 返回不是 JSON 对象")
    return data


def _call_market_ai(system_prompt: str, user_content: str, max_tokens: int) -> tuple[str, float]:
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置,无法找市场提案")
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        # flash 别名默认开思考,思考会吃光 max_tokens 让 content 返空(2026-08-15 实锤)——显式关
        "thinking": {"type": "disabled"},
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    result = call_deepseek_chat(
        payload, api_key=config.DEEPSEEK_API_KEY,
        timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, config.DEEPSEEK_READ_TIMEOUT),
        http_error_prefix="DeepSeek 市场提案返回", error_preview_chars=200,
        normalize_error_newlines=False,
    )
    if not result.content:
        raise RuntimeError("DeepSeek 市场提案返回空 content")
    return result.content, result.duration_seconds


def _parse_terms(raw: str, valid_event_ids: set[int]) -> dict[int, list[str]]:
    """AI① 输出 → {event_id: [英文搜索词]}:id 白名单、词长夹紧、每事件最多 3 组。"""
    data = _json_loads_loose(raw)
    out: dict[int, list[str]] = {}
    for item in (data.get("terms") or []):
        if not isinstance(item, dict):
            continue
        try:
            eid = int(item.get("event_id"))
        except (TypeError, ValueError):
            continue
        if eid not in valid_event_ids:
            continue
        queries: list[str] = []
        for q in (item.get("queries") or []):
            q = str(q).strip()
            if 2 <= len(q) <= 80 and q not in queries:
                queries.append(q)
        if queries:
            out[eid] = queries[:3]
    return out


def _parse_matches(raw: str, candidates: dict[int, dict[str, dict]]) -> tuple[list[dict], int]:
    """AI② 输出 → 合法配对列表 + 被剔的价格目标数。防幻觉:event_id 与 slug 都必须在
    本次候选白名单里;confidence 三档;price_target=true 整条剔除(计数不静默)。"""
    data = _json_loads_loose(raw)
    matches: list[dict] = []
    seen: set[tuple[int, str]] = set()
    dropped_price_targets = 0
    for item in (data.get("matches") or []):
        if not isinstance(item, dict):
            continue
        try:
            eid = int(item.get("event_id"))
        except (TypeError, ValueError):
            continue
        slug = str(item.get("slug") or "").strip()
        if eid not in candidates or slug not in candidates[eid] or (eid, slug) in seen:
            continue
        if item.get("confidence") not in VALID_CONFIDENCES:
            continue
        if bool(item.get("price_target")):
            dropped_price_targets += 1
            continue
        seen.add((eid, slug))
        matches.append({"event_id": eid, "slug": slug,
                        "confidence": float(item["confidence"]),
                        "reason": str(item.get("reason") or "").strip()[:120]})
    return matches, dropped_price_targets


def _recent_titles(session: Session, event_id: int, limit: int = 5) -> list[str]:
    rows = (session.query(NewsItem.title)
            .join(ResearchEventLink, ResearchEventLink.news_id == NewsItem.id)
            .filter(ResearchEventLink.event_id == int(event_id),
                    ResearchEventLink.detached.is_(False))
            .order_by(NewsItem.timestamp.desc())
            .limit(limit).all())
    return [(title or "")[:80] for (title,) in rows]


def _target_events(session: Session, event_type: str, event_id: int | None):
    if event_id is None:
        return _active_events(session, event_type)
    event = (session.query(ResearchEvent)
             .filter(ResearchEvent.id == int(event_id),
                     ResearchEvent.status == "active",
                     ResearchEvent.event_type == event_type)
             .first())
    if event is None:
        raise ValueError("事件不存在、已关闭或不属于该线")
    return [event]


def _drop_already_linked(session: Session, candidates: dict[int, dict[str, dict]]) -> None:
    """已跟踪且已挂到该事件(未摘)的候选没有提案价值,剔除。"""
    slugs = {slug for per_event in candidates.values() for slug in per_event}
    if not slugs:
        return
    tracked = (session.query(TrackedMarket)
               .filter(TrackedMarket.kind == "slug",
                       TrackedMarket.identifier.in_(slugs),
                       TrackedMarket.dismissed.is_(False)).all())
    by_slug = {t.identifier: int(t.id) for t in tracked}
    if not by_slug:
        return
    links = (session.query(ResearchEventMarket)
             .filter(ResearchEventMarket.tracked_id.in_(by_slug.values()),
                     ResearchEventMarket.detached.is_(False)).all())
    linked_pairs = {(int(l.event_id), int(l.tracked_id)) for l in links}
    for eid, per_event in candidates.items():
        for slug in list(per_event):
            tid = by_slug.get(slug)
            if tid is not None and (eid, tid) in linked_pairs:
                per_event.pop(slug)


def run_market_sweep(session: Session, event_type: str = "macro",
                     event_id: int | None = None,
                     client: PolymarketGammaClient | None = None) -> dict:
    """找市场提案:素材 → AI①搜索词 → Gamma 搜索 → AI②配对 → 提案(不落库)。
    run 全程零写库,天然就是演练;写入只发生在 apply_market_proposals(人勾选后)。"""
    if event_type not in MARKET_EVENT_TYPE:
        raise ValueError(f"非法 event_type: {event_type!r}")
    if not _MARKET_SWEEP_LOCK.acquire(blocking=False):
        raise MarketSweepBusy("已有一次找市场提案在进行中,等它跑完再点")
    try:
        events = _target_events(session, event_type, event_id)
        base = {"event_type": event_type, "scanned_events": len(events),
                "searched_terms": 0, "candidates": 0, "dropped_price_targets": 0,
                "proposals": [], "duration_seconds": 0.0}
        if not events:
            return base
        materials = [{"id": int(e.id), "name": e.name,
                      "keywords": [k.strip() for k in (e.gate_keywords or "").split("、") if k.strip()],
                      "recent_titles": _recent_titles(session, int(e.id))}
                     for e in events]
        raw_terms, duration1 = _call_market_ai(
            SEARCH_TERMS_PROMPT, json.dumps({"events": materials}, ensure_ascii=False), 1500)
        terms = _parse_terms(raw_terms, {m["id"] for m in materials})
        base["searched_terms"] = sum(len(v) for v in terms.values())
        base["duration_seconds"] = round(duration1, 1)
        if not terms:
            return base
        client = client or PolymarketGammaClient(
            config.POLYMARKET.get("gamma_url", "https://gamma-api.polymarket.com"),
            config.proxies())
        candidates: dict[int, dict[str, dict]] = {}
        for eid, queries in terms.items():
            for query in queries:
                try:
                    found = client.search_events(query, limit_per_type=5)
                except Exception as exc:
                    logger.warning("[MarketSweep] Gamma 搜索失败 q={}: {}", query, exc)
                    continue
                for event_payload in found:
                    c = _candidate(event_payload)
                    if c is not None:
                        candidates.setdefault(eid, {}).setdefault(c["slug"], c)
        _drop_already_linked(session, candidates)
        candidates = {eid: per for eid, per in candidates.items() if per}
        base["candidates"] = sum(len(per) for per in candidates.values())
        if not candidates:
            return base
        name_by_id = {int(e.id): e.name for e in events}
        pair_payload = {
            "events": [{"id": eid, "name": name_by_id[eid]} for eid in candidates],
            "candidates": {str(eid): [{k: c[k] for k in
                                       ("slug", "title", "description", "end_date", "volume")}
                                      for c in per.values()]
                           for eid, per in candidates.items()},
        }
        raw_pairs, duration2 = _call_market_ai(
            PAIR_PROMPT, json.dumps(pair_payload, ensure_ascii=False), 3000)
        matches, dropped = _parse_matches(raw_pairs, candidates)
        base["dropped_price_targets"] = dropped
        proposals = []
        for m in matches:
            c = candidates[m["event_id"]][m["slug"]]
            proposals.append({
                "event_id": m["event_id"], "event_name": name_by_id[m["event_id"]],
                "slug": m["slug"], "title": c["title"],
                "current_probability": c["current_probability"],
                "market_count": c["market_count"], "volume": c["volume"],
                "end_date": c["end_date"], "confidence": m["confidence"],
                "reason": m["reason"],
            })
        proposals.sort(key=lambda p: (-(p["confidence"] or 0), p["event_id"]))
        base["proposals"] = proposals
        base["duration_seconds"] = round(duration1 + duration2, 1)
        logger.info("[MarketSweep] {} 提案完成:事件 {},候选 {},提案 {},剔价格类 {}",
                    event_type, len(events), base["candidates"], len(proposals), dropped)
        return base
    finally:
        _MARKET_SWEEP_LOCK.release()


def apply_market_proposals(session: Session, event_type: str, items: list[dict]) -> dict:
    """采纳勾选的市场提案(签字环节):写 tracked_markets(新建/复活)+ 挂接。
    幂等:已挂跳过;已摘下不挂回(人摘过=否决,与新闻挂接同规矩)。"""
    if event_type not in MARKET_EVENT_TYPE:
        raise ValueError(f"非法 event_type: {event_type!r}")
    if len(items) > 30:
        raise ValueError("一次最多采纳 30 条")
    added: list[str] = []
    revived: list[str] = []
    skipped: list[str] = []
    linked = 0
    for item in items:
        slug = str(item.get("slug") or "").strip()
        if not slug:
            raise ValueError("提案缺 slug")
        try:
            eid = int(item.get("event_id"))
        except (TypeError, ValueError):
            raise ValueError(f"提案「{slug}」缺 event_id")
        event = (session.query(ResearchEvent)
                 .filter(ResearchEvent.id == eid, ResearchEvent.status == "active",
                         ResearchEvent.event_type == event_type).first())
        if event is None:
            skipped.append(f"{slug}(事件 #{eid} 不存在或非进行中)")
            continue
        tracked = session.query(TrackedMarket).filter_by(kind="slug", identifier=slug).first()
        if tracked is None:
            tracked = TrackedMarket(
                kind="slug", identifier=slug, market=event_type, enabled=True,
                display_name=(str(item.get("title") or "").strip()[:255] or None))
            session.add(tracked)
            session.flush()
            added.append(slug)
        elif tracked.dismissed:
            tracked.dismissed = False
            tracked.enabled = True
            tracked.market = event_type
            revived.append(slug)
        link = (session.query(ResearchEventMarket)
                .filter_by(event_id=eid, tracked_id=int(tracked.id)).first())
        if link is not None:
            skipped.append(f"{slug}(已{'摘下' if link.detached else '挂接'})")
            continue
        confidence = item.get("confidence")
        session.add(ResearchEventMarket(
            event_id=eid, tracked_id=int(tracked.id), link_source="auto",
            confidence=float(confidence) if confidence in VALID_CONFIDENCES else None,
            prompt_version=MARKET_SWEEP_PROMPT_VERSION))
        linked += 1
    session.commit()
    logger.info("[MarketSweep] {} 采纳:新建 {},复活 {},挂接 {},跳过 {}",
                event_type, len(added), len(revived), linked, len(skipped))
    return {"event_type": event_type, "added": added, "revived": revived,
            "linked": linked, "skipped": skipped}


def search_markets(query: str) -> list[dict]:
    """手动搜索通道(spec §3):Gamma 搜索代理,不剔价格类、不设交易量门槛(人是有意找的),
    只剔已关闭/未活跃。"""
    query = (query or "").strip()
    if not query:
        return []
    client = PolymarketGammaClient(
        config.POLYMARKET.get("gamma_url", "https://gamma-api.polymarket.com"),
        config.proxies())
    results = []
    for event_payload in client.search_events(query, limit_per_type=10):
        c = _candidate(event_payload, min_volume=0.0)
        if c is not None:
            results.append(c)
    return results[:10]
```

- [ ] **Step 6.4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_market_sweep.py -v`
Expected: 全 PASS

- [ ] **Step 6.5: 提交**

```bash
git add services/market_sweep.py tests/test_market_sweep.py
git commit -m "feat(research): 找市场提案管线——AI 英文搜索词+Gamma 搜索+配对打分,slug 白名单防幻觉,价格目标类两线剔除,apply 幂等"
```

---

### Task 7: prediction_service 线过滤 + tracked 归属扩展

**Files:**
- Modify: `services/prediction_service.py`、`schemas/predictions.py`
- Test: `tests/test_tracked_markets_service.py`(追加)、`tests/test_predictions_active_filter.py`(追加)

- [ ] **Step 7.1: 写失败测试**

`tests/test_predictions_active_filter.py` 末尾追加(沿用该文件既有 fixture/建行帮助函数;若字段名不同,照它现有用例改写):

```python
def test_load_prediction_rows_filters_by_line(session):
    """market 线过滤(2026-08-28):加密页只看加密跟踪项的市场;旧无 origin 快照算宏观。"""
    from services.prediction_service import load_prediction_rows
    now = utc_now_naive()
    session.add(TrackedMarket(kind="slug", identifier="m-slug", market="macro", enabled=True))
    session.add(TrackedMarket(kind="slug", identifier="c-slug", market="crypto", enabled=True))
    for market_id, origin in [("mk1", "slug:m-slug"), ("ck1", "slug:c-slug"), ("legacy", None)]:
        session.add(PredictionMarket(timestamp=now, market_id=market_id, question="q",
                                     outcome="Yes", probability=0.5, origin=origin))
    session.commit()
    all_ids = {r.market_id for r in load_prediction_rows(session, hours=24)}
    macro_ids = {r.market_id for r in load_prediction_rows(session, hours=24, market="macro")}
    crypto_ids = {r.market_id for r in load_prediction_rows(session, hours=24, market="crypto")}
    assert all_ids == {"mk1", "ck1", "legacy"}
    assert macro_ids == {"mk1", "legacy"}
    assert crypto_ids == {"ck1"}
```

`tests/test_tracked_markets_service.py` 末尾追加:

```python
def test_tracked_schema_carries_line_and_event_briefs(session):
    """列表带线归属与挂接事件简报;create 支持 market 与顺手挂接 event_id(2026-08-28)。"""
    from models.research import ResearchEvent
    from schemas.predictions import TrackedMarketCreate
    from services.prediction_service import create_tracked_market, list_tracked_markets
    event = ResearchEvent(name="测试事件", event_type="crypto", status="active", display_no=1)
    session.add(event); session.commit()
    created = create_tracked_market(session, TrackedMarketCreate(
        kind="slug", identifier="crypto-slug", market="crypto", event_id=event.id))
    assert created.market == "crypto"
    rows = list_tracked_markets(session, market="crypto")
    assert [r.identifier for r in rows] == ["crypto-slug"]
    assert rows[0].events[0].name == "测试事件"
    assert list_tracked_markets(session, market="macro") == []
```

- [ ] **Step 7.2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_predictions_active_filter.py tests/test_tracked_markets_service.py -v`
Expected: 新用例 FAIL(unexpected keyword `market` 等)

- [ ] **Step 7.3: schemas 扩展**

`schemas/predictions.py` 追加/修改:

```python
class TrackedEventBrief(BaseModel):
    """跟踪项挂着哪些事件(跟踪管理表归属列,2026-08-28)。"""
    link_id: int
    event_id: int
    display_no: int
    name: str


class TrackedMarketSchema(BaseModel):
    id: int
    kind: Literal["slug"]
    identifier: str
    display_name: str | None = None
    enabled: bool
    notes: str | None = None
    market: str = "macro"
    events: list[TrackedEventBrief] = []


class TrackedMarketCreate(BaseModel):
    kind: Literal["slug"]
    identifier: str
    display_name: str | None = None
    notes: str | None = None
    market: Literal["macro", "crypto"] = "macro"
    event_id: int | None = None          # 传了=添加即挂接(link_source=human)
```

- [ ] **Step 7.4: prediction_service 修改**

① `load_prediction_rows` 签名与过滤(:42-85)——`hours` 上限放宽到一年(小时数据"全部"窗口),加 `market` 参数:

```python
def load_prediction_rows(session: Session, hours: int = 24, market: str | None = None) -> list[PredictionMarket]:
    hours = max(1, min(int(hours or 24), 24 * 366))
```

tracked 查询改为三列并建线集合(替换 :58-66 的 active_keys 构建):

```python
    tracked = (session.query(TrackedMarket.kind, TrackedMarket.identifier, TrackedMarket.market)
               .filter(TrackedMarket.dismissed.is_(False), TrackedMarket.kind == "slug")
               .all())
    active_keys = {f"{t.kind}:{t.identifier}" for t in tracked}
    # 线过滤(2026-08-28):market 传入时只留该线跟踪项的市场;旧无 origin 快照算宏观
    line_keys = ({f"{t.kind}:{t.identifier}" for t in tracked if (t.market or "macro") == market}
                 if market else None)
```

`_visible`(:78-82)改为:

```python
    def _visible(market_id: str) -> bool:
        origins = origins_by_market.get(market_id)
        if origins:
            if not (origins & active_keys):
                return False
            return line_keys is None or bool(origins & line_keys)
        if line_keys is not None and market != "macro":
            return False
        return latest_by_market[market_id] >= latest_ts - grace
```

② `get_predictions` / `get_prediction_families` / `get_market_history` 各加 `market: str | None = None` 参数并透传给 `load_prediction_rows`。`get_predictions` 里组装 `PredictionMarketSummary` 时补 `origin=ordered[0].origin`。

③ tracked 三件套:

```python
def _tracked_to_schema(row: TrackedMarket, events: list[dict] | None = None) -> TrackedMarketSchema:
    return TrackedMarketSchema(
        id=row.id,
        kind=row.kind,
        identifier=row.identifier,
        display_name=row.display_name,
        enabled=row.enabled,
        notes=row.notes,
        market=row.market or "macro",
        events=[TrackedEventBrief(**e) for e in (events or [])],
    )


def list_tracked_markets(session: Session, market: str | None = None) -> list[TrackedMarketSchema]:
    query = session.query(TrackedMarket).filter(
        TrackedMarket.dismissed.is_(False), TrackedMarket.kind == "slug")
    if market:
        query = query.filter(TrackedMarket.market == market)
    rows = query.order_by(TrackedMarket.kind, TrackedMarket.identifier).all()
    from services import event_markets
    briefs = event_markets.links_for_tracked(session, [int(r.id) for r in rows])
    return [_tracked_to_schema(r, briefs.get(int(r.id))) for r in rows]
```

`create_tracked_market` 整体替换为(在原逻辑上加 market/event_id,复活与新建两条路径都走统一收尾):

```python
def create_tracked_market(session: Session, payload: TrackedMarketCreate) -> TrackedMarketSchema:
    identifier = (payload.identifier or "").strip()
    if not identifier:
        raise ValueError("identifier empty")

    exists = (
        session.query(TrackedMarket)
        .filter(TrackedMarket.kind == payload.kind, TrackedMarket.identifier == identifier)
        .first()
    )
    if exists is not None and not exists.dismissed:
        raise ValueError("duplicate")

    if exists is not None:
        # 之前被软删的同名项 → 复活而不是报重复;线归属按本次添加意图覆盖。
        exists.dismissed = False
        exists.enabled = True
        exists.market = payload.market
        new_name = (payload.display_name or "").strip()
        if new_name:
            exists.display_name = new_name
        new_notes = (payload.notes or "").strip()
        if new_notes:
            exists.notes = new_notes
        row = exists
    else:
        row = TrackedMarket(
            kind=payload.kind,
            identifier=identifier,
            market=payload.market,
            display_name=(payload.display_name or "").strip() or None,
            notes=(payload.notes or "").strip() or None,
            enabled=True,
        )
        session.add(row)
    session.commit()
    session.refresh(row)
    if payload.event_id:
        # 添加即挂接(spec §4):人工通道,link_source=human;事件非法会抛 ValueError → 路由译 400
        from services import event_markets
        event_markets.attach_market(session, int(payload.event_id), int(row.id), source="human")
    from services import event_markets
    briefs = event_markets.links_for_tracked(session, [int(row.id)])
    return _tracked_to_schema(row, briefs.get(int(row.id)))
```

(文件顶部**不要**模块级 import event_markets——函数内延迟 import,避免 services 包互指;两处函数内 import 合并成一处写在 commit 之后即可。)

- [ ] **Step 7.5: 跑测试确认通过 + 回归**

Run: `D:\anaconda\python.exe -m pytest tests/test_predictions_active_filter.py tests/test_tracked_markets_service.py tests/test_predictions_tracked_api.py tests/test_market_history.py tests/test_classify_market_family.py -v`
Expected: 全 PASS

- [ ] **Step 7.6: 提交**

```bash
git add services/prediction_service.py schemas/predictions.py tests/test_predictions_active_filter.py tests/test_tracked_markets_service.py
git commit -m "feat(predictions): 快照读取按线过滤+summary 暴露 origin;tracked 带线归属与挂接简报,create 支持顺手挂事件"
```

---

### Task 8: API 层——市场提案/事件市场/搜索端点 + market 参数 + 事件卡计数

**Files:**
- Modify: `schemas/research.py`(MarketSweep 系列 + ResearchEventItem.market_count)、`schemas/predictions.py`(搜索/事件市场 schema)、`api/routes.py`、`services/event_pool.py`(≈:293 事件列表 dict)
- Test: `tests/test_research_api.py`(追加)

- [ ] **Step 8.1: 写失败测试**

`tests/test_research_api.py` 末尾追加(沿用该文件既有 TestClient/依赖覆盖 fixture;若命名不同照现有用例改写):

```python
def test_market_sweep_apply_and_event_markets_roundtrip(client, session, monkeypatch):
    """市场提案 API 闭环:提案(mock 管线)→ 采纳 → 事件市场卡 → 摘下。"""
    from models.research import ResearchEvent
    from services import market_sweep
    event = ResearchEvent(name="俄乌停火", event_type="macro", status="active", display_no=9)
    session.add(event); session.commit()
    canned = {"event_type": "macro", "scanned_events": 1, "searched_terms": 1,
              "candidates": 1, "dropped_price_targets": 0, "duration_seconds": 1.0,
              "proposals": [{"event_id": event.id, "event_name": "俄乌停火",
                             "slug": "ceasefire-2026", "title": "Ceasefire in 2026?",
                             "current_probability": 0.41, "market_count": 1,
                             "volume": 500000.0, "end_date": "2026-12-31",
                             "confidence": 0.9, "reason": "就是这件事"}]}
    monkeypatch.setattr(market_sweep, "run_market_sweep", lambda *a, **k: canned)
    r = client.post("/api/research/market-sweep", json={"event_type": "macro"})
    assert r.status_code == 200 and r.json()["proposals"][0]["slug"] == "ceasefire-2026"

    r = client.post("/api/research/market-sweep/apply",
                    json={"event_type": "macro", "items": canned["proposals"]})
    assert r.status_code == 200
    assert r.json()["added"] == ["ceasefire-2026"] and r.json()["linked"] == 1

    r = client.get(f"/api/research/events/{event.id}/markets")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["slug"] == "ceasefire-2026" and item["waiting_first_scan"] is True

    r = client.post(f"/api/research/event-markets/{item['link_id']}/detach",
                    json={"reason": "试摘"})
    assert r.status_code == 200
    assert client.get(f"/api/research/events/{event.id}/markets").json()["items"] == []


def test_predictions_search_proxy(client, monkeypatch):
    from services import market_sweep
    monkeypatch.setattr(market_sweep, "search_markets", lambda q: [{
        "slug": "s", "title": "T?", "description": "", "volume": 1.0,
        "end_date": "2026-01-01", "market_count": 1, "current_probability": 0.5}])
    r = client.get("/api/predictions/search", params={"q": "fed"})
    assert r.status_code == 200 and r.json()[0]["slug"] == "s"
```

- [ ] **Step 8.2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_research_api.py -v`
Expected: 新用例 FAIL(404)

- [ ] **Step 8.3: schemas**

`schemas/research.py` 追加(放 Sweep 系列之后),并给 `ResearchEventItem` 加 `market_count: int = 0`:

```python
class MarketSweepRequest(BaseModel):
    """找市场提案(spec 2026-08-28 §3):run 全程不落库,天然演练,无需 dry_run。"""
    event_type: str = "macro"
    event_id: int | None = None              # 传了=单事件按钮/找后继;不传=整线


class MarketProposal(BaseModel):
    event_id: int
    event_name: str = ""
    slug: str
    title: str = ""
    current_probability: float | None = None   # 单市场取 Yes;多子市场为空(spec §4)
    market_count: int = 1
    volume: float | None = None
    end_date: str = ""
    confidence: float | None = None
    reason: str = ""


class MarketSweepResponse(BaseModel):
    event_type: str
    scanned_events: int = 0
    searched_terms: int = 0
    candidates: int = 0
    dropped_price_targets: int = 0           # 被剔的价格目标类(不静默)
    proposals: list[MarketProposal] = Field(default_factory=list)
    duration_seconds: float = 0.0


class MarketSweepApplyRequest(BaseModel):
    """采纳勾选提案(签字环节):前端把提案勾选子集原样传回,无状态。"""
    event_type: str = "macro"
    items: list[MarketProposal] = Field(default_factory=list)


class MarketSweepApplyResponse(BaseModel):
    event_type: str
    added: list[str] = Field(default_factory=list)
    revived: list[str] = Field(default_factory=list)
    linked: int = 0
    skipped: list[str] = Field(default_factory=list)


class AttachMarketRequest(BaseModel):
    tracked_id: int


class DetachMarketRequest(BaseModel):
    reason: str | None = None
```

`schemas/predictions.py` 追加:

```python
class MarketSearchResult(BaseModel):
    """手动搜索通道的 Gamma 候选(不剔价格类,spec §3)。"""
    slug: str
    title: str = ""
    description: str = ""
    volume: float | None = None
    end_date: str = ""
    market_count: int = 1
    current_probability: float | None = None


class EventMarketItem(BaseModel):
    """事件详情市场卡(spec §5):跟踪项 + 旗下市场最新摘要 + 三种断流语义徽章素材。"""
    link_id: int
    tracked_id: int
    slug: str
    display_name: str | None = None
    market: str = "macro"
    enabled: bool = True
    link_source: str = "human"
    confidence: float | None = None
    settled: bool = False
    waiting_first_scan: bool = False
    markets: list[PredictionMarketSummary] = []


class EventMarketsResponse(BaseModel):
    items: list[EventMarketItem] = []
```

- [ ] **Step 8.4: `services/event_pool.py` 事件列表加 market_count**

找到构建事件卡 dict 的函数(锚点 `"evidence_count": len(rows)`,≈:299)。在其所在循环**之前**加一次性统计(函数内、拿到事件列表后):

```python
    from models.event_market import ResearchEventMarket
    market_counts = dict(session.query(ResearchEventMarket.event_id, func.count())
                         .filter(ResearchEventMarket.detached.is_(False))
                         .group_by(ResearchEventMarket.event_id).all())
```

(该文件若未导入 `func`,从 sqlalchemy 补。)dict 里加一行:

```python
            "market_count": int(market_counts.get(int(e.id), 0)),
```

- [ ] **Step 8.5: `api/routes.py` 路由**

① import 区:schemas 两处补新名字;`from services import ...` 行加 `event_markets, market_sweep`。

② 预测区(:234-277)——`predictions`、`prediction_families` 加 `market: str | None = None` 形参并透传;`list_tracked` 加 `market: str | None = None` 透传;`prediction_history` 不动。新增:

```python
@router.get("/predictions/search", response_model=list[MarketSearchResult])
def predictions_search(q: str, db: Session = Depends(get_db)) -> list[MarketSearchResult]:
    """手动搜索通道:Gamma 搜索代理(不剔价格类)。"""
    try:
        return [MarketSearchResult(**c) for c in market_sweep.search_markets(q)]
    except Exception as exc:
        raise ApiError("SEARCH_FAILED", f"Polymarket 搜索失败: {exc}", status_code=502) from exc
```

(**注意**:放在 `@router.get("/predictions/{market_id}/history")` **之前**,字面路径优先注册。)

③ research 区(sweep 端点之后)新增:

```python
@router.post("/research/market-sweep", response_model=MarketSweepResponse)
def research_market_sweep(request: MarketSweepRequest, db: Session = Depends(get_db)) -> MarketSweepResponse:
    """找市场提案(spec 2026-08-28 §3):AI 搜索词+Gamma+配对,提案不落库等人勾选。"""
    try:
        return MarketSweepResponse(**market_sweep.run_market_sweep(
            db, event_type=request.event_type, event_id=request.event_id))
    except market_sweep.MarketSweepBusy as exc:
        raise ApiError("MARKET_SWEEP_BUSY", str(exc), status_code=409) from exc
    except ValueError as exc:
        raise ApiError("MARKET_SWEEP_INVALID", str(exc), status_code=400) from exc
    except RuntimeError as exc:
        raise ApiError("MARKET_SWEEP_FAILED", str(exc), status_code=502) from exc


@router.post("/research/market-sweep/apply", response_model=MarketSweepApplyResponse)
def research_market_sweep_apply(request: MarketSweepApplyRequest,
                                db: Session = Depends(get_db)) -> MarketSweepApplyResponse:
    try:
        return MarketSweepApplyResponse(**market_sweep.apply_market_proposals(
            db, request.event_type, [i.model_dump() for i in request.items]))
    except ValueError as exc:
        raise ApiError("MARKET_SWEEP_APPLY_INVALID", str(exc), status_code=400) from exc


@router.get("/research/events/{event_id}/markets", response_model=EventMarketsResponse)
def research_event_markets(event_id: int, db: Session = Depends(get_db)) -> EventMarketsResponse:
    return EventMarketsResponse(items=[EventMarketItem(**it)
                                       for it in event_markets.list_event_markets(db, event_id)])


@router.post("/research/events/{event_id}/markets")
def research_event_market_attach(event_id: int, payload: AttachMarketRequest,
                                 db: Session = Depends(get_db)) -> dict:
    try:
        link = event_markets.attach_market(db, event_id, payload.tracked_id)
    except ValueError as exc:
        raise ApiError("MARKET_ATTACH_INVALID", str(exc), status_code=400) from exc
    return {"ok": True, "link_id": int(link.id)}


@router.post("/research/event-markets/{link_id}/detach")
def research_event_market_detach(link_id: int, payload: DetachMarketRequest,
                                 db: Session = Depends(get_db)) -> dict:
    if not event_markets.detach_market(db, link_id, payload.reason):
        raise ApiError("MARKET_LINK_NOT_FOUND", "挂接不存在", status_code=404)
    return {"ok": True}
```

④ 创建跟踪端点(:249-257)不改签名(payload 已带新字段),但事件挂接失败要可见——`create_tracked` 的 try 块把 `event_markets` 的 ValueError 一并译成 400(现有 except ValueError 已覆盖,确认即可)。

- [ ] **Step 8.6: 跑测试确认通过 + 回归**

Run: `D:\anaconda\python.exe -m pytest tests/test_research_api.py tests/test_api.py tests/test_predictions_tracked_api.py tests/test_event_pool.py tests/test_event_pool_crypto.py -v`
Expected: 全 PASS

- [ ] **Step 8.7: 提交**

```bash
git add schemas/research.py schemas/predictions.py api/routes.py services/event_pool.py tests/test_research_api.py
git commit -m "feat(api): 市场提案/采纳/事件市场卡/摘下/手动搜索五端点,预测接口加线参数,事件卡带关联市场计数"
```

---

### Task 9: OpenAPI 类型再生成 + 前端 api client

**Files:**
- Modify: `frontend/src/api/types.ts`(生成产物)、`frontend/src/api/client.ts`

- [ ] **Step 9.1: 再生成类型**

Run: `D:\anaconda\python.exe scripts/generate_openapi_types.py`
然后:`D:\anaconda\python.exe -m pytest tests/test_openapi_types.py -v`
Expected: PASS(types.ts 出现 MarketSweepResponse / EventMarketsResponse / MarketSearchResult 等)

- [ ] **Step 9.2: client.ts 加函数**

预测区(:132-150)改造+追加:

```ts
  predictions: (params: { hours?: number; search?: string; market?: string }) =>
    request<PredictionsResponse>(`/predictions${buildQuery(params)}`),
  predictionFamilies: (params: { hours?: number; search?: string; market?: string }) =>
    request<PredictionFamily[]>(`/predictions/families${buildQuery(params)}`),
  predictionTracked: (market?: string) =>
    request<TrackedMarket[]>(`/predictions/tracked${buildQuery({ market })}`),
  predictionSearch: (q: string) =>
    request<import("./types").MarketSearchResult[]>(`/predictions/search${buildQuery({ q })}`),
```

(`createPredictionTracked` 的 body 类型来自生成的 types,新增字段 market/event_id 由调用方传入即可,签名不用改。)

研究区(researchSweepApply 之后)追加:

```ts
  researchMarketSweep: (eventType: "macro" | "crypto", eventId?: number) =>
    request<import("./types").MarketSweepResponse>("/research/market-sweep", {
      method: "POST",
      body: JSON.stringify({ event_type: eventType, ...(eventId ? { event_id: eventId } : {}) })
    }),
  researchMarketSweepApply: (eventType: "macro" | "crypto", items: import("./types").MarketProposal[]) =>
    request<import("./types").MarketSweepApplyResponse>("/research/market-sweep/apply", {
      method: "POST", body: JSON.stringify({ event_type: eventType, items })
    }),
  researchEventMarkets: (eventId: number) =>
    request<import("./types").EventMarketsResponse>(`/research/events/${eventId}/markets`),
  researchEventMarketAttach: (eventId: number, trackedId: number) =>
    request<{ ok: boolean; link_id: number }>(`/research/events/${eventId}/markets`, {
      method: "POST", body: JSON.stringify({ tracked_id: trackedId })
    }),
  researchEventMarketDetach: (linkId: number, reason?: string) =>
    request<{ ok: boolean }>(`/research/event-markets/${linkId}/detach`, {
      method: "POST", body: JSON.stringify({ reason: reason ?? null })
    }),
```

- [ ] **Step 9.3: 编译验证 + 提交**

Run: `npm --prefix frontend run build`(若脚本名不同,用 `cd frontend; npx tsc -b; npx vite build`)
Expected: tsc 零错误(PredictionsPage 还在,旧调用兼容)

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts
git commit -m "feat(frontend-api): OpenAPI 类型再生成,client 接市场提案/事件市场/搜索/线参数"
```

---

### Task 10: 前端——共享图表工具 + 事件详情「市场定价」区块

**Files:**
- Create: `frontend/src/components/predictionChart.ts`、`frontend/src/components/EventMarkets.tsx`
- Modify: `frontend/src/pages/ResearchPage.tsx`(EventDetail 插区块)
- Test: `frontend/src/components/EventMarkets.test.tsx`(新建;mock 方式照抄 `ResearchPage.test.tsx` 顶部的 `vi.mock("../api/client", ...)` 写法)

- [ ] **Step 10.1: 建 `predictionChart.ts`(从 PredictionsPage 平移,先不删原页)**

```ts
import type { PredictionFamily, PredictionRow } from "../api/types";
import { type ChartPoint } from "./Charts";

// 小时级快照(2026-08-28 降频)下 2h/6h 窗口只剩 2-6 个点,窗口档位重定
export const predictionWindowOptions = [
  { label: "24小时", value: "24" },
  { label: "7天", value: "168" },
  { label: "30天", value: "720" },
  { label: "1年", value: "8760" }
];

export function buildMarketChart(history: PredictionRow[]): { data: ChartPoint[]; keys: string[] } {
  const byTime = new Map<string, ChartPoint>();
  const keys = Array.from(new Set(history.map((row) => row.outcome)));
  history.forEach((row) => {
    const time = row.timestamp_bj?.slice(5, 16) ?? "";
    const entry = byTime.get(time) ?? { time, sort_key: row.timestamp_utc ?? row.timestamp_bj ?? time };
    entry[row.outcome] = row.probability_pct;
    byTime.set(time, entry);
  });
  const data = Array.from(byTime.values())
    .sort((a, b) => String(a.sort_key ?? a.time).localeCompare(String(b.sort_key ?? b.time)))
    .map(({ sort_key, ...row }) => row);
  return { data, keys };
}

export function buildFamilyChart(family: PredictionFamily): { data: ChartPoint[]; keys: string[] } {
  const byTime = new Map<string, ChartPoint>();
  const keys: string[] = [];
  family.series.forEach((series) => {
    keys.push(series.label);
    series.points.forEach((point) => {
      const time = point.timestamp_bj?.slice(5, 16) ?? "";
      const row = byTime.get(time) ?? { time, sort_key: point.timestamp_utc ?? point.timestamp_bj ?? time };
      row[series.label] = point.probability_pct;
      byTime.set(time, row);
    });
  });
  const data = Array.from(byTime.values())
    .sort((a, b) => String(a.sort_key ?? a.time).localeCompare(String(b.sort_key ?? b.time)))
    .map(({ sort_key, ...row }) => row);
  return { data, keys };
}
```

- [ ] **Step 10.2: 建 `MarketProposalPanel.tsx`(提案确认面板,池页与事件详情共用)**

```tsx
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, apiErrorText } from "../api/client";
import type { MarketProposal, MarketSweepResponse } from "../api/types";
import { Button } from "./Controls";
import { EmptyState } from "./StateViews";

export function fmtProb(p: number | null | undefined, count: number): string {
  if (p != null) return `${(p * 100).toFixed(0)}%`;
  return count > 1 ? `${count} 个子市场` : "—";
}

/** 市场提案确认面板(spec §3 提案确认制):勾选采纳才写库,交互仿 AI 梳理。
 *  池页整线提案与事件详情单事件提案(含已结算市场的"找后继")共用。 */
export function MarketProposalPanel({ eventType, result, onClose, onApplied }: {
  eventType: "macro" | "crypto";
  result: MarketSweepResponse;
  onClose: () => void;
  onApplied: (summary: string) => void;
}) {
  const [checked, setChecked] = useState<boolean[]>(result.proposals.map(() => true));
  const [errorMsg, setErrorMsg] = useState("");
  const apply = useMutation({
    mutationFn: (items: MarketProposal[]) => api.researchMarketSweepApply(eventType, items),
    onSuccess: (r) => onApplied(
      `已采纳:新建 ${r.added.length} · 复活 ${r.revived.length} · 挂接 ${r.linked}`
      + (r.skipped.length ? ` · 跳过 ${r.skipped.length}` : "")),
    onError: (err) => setErrorMsg(apiErrorText(err, "采纳失败"))
  });
  const meta = `事件 ${result.scanned_events} · 搜索词 ${result.searched_terms} · 候选 ${result.candidates}`
    + (result.dropped_price_targets ? ` · 剔价格类 ${result.dropped_price_targets}` : "");
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>市场提案</h2>
        <span className="muted">{meta}</span>
        <button type="button" className="link-button" onClick={onClose}>全部忽略</button>
      </div>
      {errorMsg && <span style={{ color: "var(--danger)" }}>{errorMsg}</span>}
      {result.proposals.length === 0
        ? <EmptyState title="没找到可挂的市场(叙事类事件在 Polymarket 常无对应盘,属正常)" />
        : (
          <>
            {result.proposals.map((p, i) => (
              <label key={`${p.event_id}-${p.slug}`} className="rp-news-item"
                     style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                <input type="checkbox" checked={checked[i] ?? true}
                       onChange={(ev) => {
                         const next = [...checked];
                         next[i] = ev.target.checked;
                         setChecked(next);
                       }} />
                <span className="s-badge none">#{p.event_id} {p.event_name}</span>
                <span className="rp-title">{p.title}</span>
                <span className="s-badge mid">{fmtProb(p.current_probability, p.market_count)}</span>
                <span className="muted">
                  量 ${Math.round((p.volume ?? 0) / 1000)}k · 到期 {p.end_date || "—"} · {p.reason}
                </span>
              </label>
            ))}
            <div style={{ display: "flex", justifyContent: "flex-end", paddingTop: 8 }}>
              <Button kind="primary"
                      disabled={apply.isPending || checked.filter(Boolean).length === 0}
                      onClick={() => apply.mutate(result.proposals.filter((_, i) => checked[i]))}>
                {apply.isPending ? "写入中…" : `采纳选中(${checked.filter(Boolean).length})并跟踪`}
              </Button>
            </div>
          </>
        )}
    </div>
  );
}
```

- [ ] **Step 10.3: 建 `EventMarkets.tsx`**

要点:①`eventType` 必传(单事件提案要带线);②**区块永远渲染**——没挂市场时显示空态与「找市场提案」按钮(spec 入口②),否则最需要提案的空事件反而没入口;③已结算市场的「找后继」就是同一个按钮(提案素材=事件,与 spec 入口③同一后端,前端不再做每卡按钮——spec §6 同步注记)。

```tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiErrorText } from "../api/client";
import type { EventMarketItem, MarketSweepResponse, PredictionMarketSummary } from "../api/types";
import { Button, SelectControl } from "./Controls";
import { MarketProposalPanel } from "./MarketProposalPanel";
import { PredictionCard } from "./PredictionCard";
import { buildMarketChart, predictionWindowOptions } from "./predictionChart";
import { ErrorState, LoadingState } from "./StateViews";

export function MarketChartCard({ summary, hours }: { summary: PredictionMarketSummary; hours: number }) {
  const history = useQuery({
    queryKey: ["prediction-history", summary.market_id, hours],
    queryFn: () => api.predictionHistory(summary.market_id, hours)
  });
  const chart = buildMarketChart(history.data ?? []);
  const yes = summary.outcomes.find((o) => o.outcome.toLowerCase() === "yes");
  return (
    <PredictionCard
      title={summary.question}
      data={chart.data}
      keys={chart.keys}
      meta={{
        volume: summary.volume,
        outcomes: summary.outcomes.length,
        latestPct: yes?.probability_pct ?? summary.outcomes[0]?.probability_pct,
        updatedAt: summary.outcomes[0]?.timestamp_bj ?? null
      }}
    />
  );
}

function linkBadges(item: EventMarketItem): { cls: string; text: string }[] {
  const badges: { cls: string; text: string }[] = [];
  // 三种断流语义分开打(spec §5):摘下不在此(摘了就不显示),停用/结算各一枚
  if (!item.enabled) badges.push({ cls: "s-badge weak", text: "已停用" });
  if (item.settled) badges.push({ cls: "s-badge mid", text: "已结算/断流" });
  if (item.link_source === "auto")
    badges.push({ cls: "s-badge none", text: `AI ${item.confidence ?? "—"}` });
  return badges;
}

/** 事件详情·市场定价区块(spec §5):新闻叙事 vs 市场定价对照看。
 *  永远渲染:空事件也有「找市场提案」入口;已结算市场的"找后继"=同一按钮。 */
export function EventMarkets({ eventId, eventType }: {
  eventId: number; eventType: "macro" | "crypto" }) {
  const qc = useQueryClient();
  const [hours, setHours] = useState("720");
  const markets = useQuery({
    queryKey: ["event-markets", eventId],
    queryFn: () => api.researchEventMarkets(eventId)
  });
  const [actionError, setActionError] = useState("");
  const [applied, setApplied] = useState("");
  const [sweepResult, setSweepResult] = useState<MarketSweepResponse | null>(null);
  const sweep = useMutation({
    mutationFn: () => api.researchMarketSweep(eventType, eventId),
    onSuccess: (r) => { setActionError(""); setSweepResult(r); },
    onError: (err) => setActionError(apiErrorText(err, "找市场提案失败"))
  });
  const detach = useMutation({
    mutationFn: (linkId: number) => api.researchEventMarketDetach(linkId),
    onSuccess: () => {
      setActionError("");
      void qc.invalidateQueries({ queryKey: ["event-markets", eventId] });
      void qc.invalidateQueries({ queryKey: ["prediction-tracked"] });
    },
    onError: () => setActionError("摘下失败")
  });

  if (markets.isLoading) return <LoadingState label="加载关联市场" />;
  if (markets.isError) return <ErrorState error={markets.error} />;
  const items = markets.data?.items ?? [];

  return (
    <div className="panel">
      <div className="panel-head">
        <h2>市场定价</h2>
        <SelectControl label="窗口" value={hours} onChange={setHours}
                       options={predictionWindowOptions} />
        <Button kind="secondary" disabled={sweep.isPending} onClick={() => sweep.mutate()}>
          {sweep.isPending ? "找市场中…" : "找市场提案"}
        </Button>
        {actionError && <span style={{ color: "var(--danger)" }}>{actionError}</span>}
        {applied && <span className="muted">{applied}</span>}
      </div>
      {sweepResult && (
        <MarketProposalPanel eventType={eventType} result={sweepResult}
          onClose={() => setSweepResult(null)}
          onApplied={(summary) => {
            setSweepResult(null);
            setApplied(summary);
            void qc.invalidateQueries({ queryKey: ["event-markets", eventId] });
            void qc.invalidateQueries({ queryKey: ["prediction-tracked"] });
          }} />
      )}
      {!items.length && !sweepResult && (
        <div className="muted">
          尚未关联市场——点「找市场提案」让 AI 去 Polymarket 找,或去市场定价页签手动搜索。
        </div>
      )}
      {items.map((item) => (
        <div key={item.link_id} style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="rp-title">{item.display_name || item.slug}</span>
            {linkBadges(item).map((b, i) => <span key={i} className={b.cls}>{b.text}</span>)}
            <span style={{ flex: 1 }} />
            <button type="button" className="link-button danger"
                    disabled={detach.isPending}
                    onClick={() => {
                      if (window.confirm(`把 ${item.slug} 从本事件摘下?(跟踪照旧,留痕可查)`))
                        detach.mutate(item.link_id);
                    }}>
              摘下
            </button>
          </div>
          {item.waiting_first_scan
            ? <div className="muted">等待首轮采集(最长 1 小时)…</div>
            : (
              <div className="prediction-grid">
                {item.markets.map((m) => (
                  <MarketChartCard key={m.market_id} summary={m} hours={Number(hours)} />
                ))}
              </div>
            )}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 10.4: EventDetail 插区块**

`frontend/src/pages/ResearchPage.tsx`:import 区加 `import { EventMarkets } from "../components/EventMarkets";`。在 `EventDetail` 返回的 `<div className="rp-detail">` 里、`rp-detail-head` 那个 `</div>` 之后,插入:

```tsx
      <EventMarkets eventId={eventId} eventType={eventType} />
```

- [ ] **Step 10.5: 写渲染测试并跑**

`frontend/src/components/EventMarkets.test.tsx`(mock 写法若与 `ResearchPage.test.tsx` 顶部的 `vi.mock` 结构冲突,以那边为准):

```tsx
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { EventMarkets } from "./EventMarkets";

vi.mock("../api/client", () => ({
  api: {
    researchEventMarkets: vi.fn().mockResolvedValue({
      items: [{ link_id: 1, tracked_id: 2, slug: "ceasefire-2026", display_name: null,
                market: "macro", enabled: true, link_source: "auto", confidence: 0.9,
                settled: false, waiting_first_scan: true, markets: [] }]
    }),
    predictionHistory: vi.fn().mockResolvedValue([]),
    researchMarketSweep: vi.fn(),
    researchMarketSweepApply: vi.fn(),
    researchEventMarketDetach: vi.fn()
  },
  apiErrorText: () => "err"
}));

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("EventMarkets", () => {
  it("渲染区块头、找市场按钮与首轮采集占位", async () => {
    wrap(<EventMarkets eventId={7} eventType="macro" />);
    expect(await screen.findByText("市场定价")).toBeTruthy();
    expect(screen.getByText("找市场提案")).toBeTruthy();
    expect(await screen.findByText(/等待首轮采集/)).toBeTruthy();
  });
});
```

Run: `npm --prefix frontend run test -- --run EventMarkets`(脚本名不同就 `cd frontend; npx vitest run EventMarkets`)
Expected: PASS

- [ ] **Step 10.6: 提交**

```bash
git add frontend/src/components/predictionChart.ts frontend/src/components/MarketProposalPanel.tsx frontend/src/components/EventMarkets.tsx frontend/src/components/EventMarkets.test.tsx frontend/src/pages/ResearchPage.tsx
git commit -m "feat(frontend): 事件详情市场定价区块——单事件提案入口+概率曲线+断流徽章+摘下留痕,提案面板抽共享组件"
```

---

### Task 11: 前端——「市场定价」页签(提案 + 手动搜索 + 常设观测 + 跟踪管理)

**Files:**
- Create: `frontend/src/components/MarketPricingTab.tsx`
- Modify: `frontend/src/components/TrackedMarketsPanel.tsx`(加线 + 归属列)
- Test: `frontend/src/components/MarketPricingTab.test.tsx`

- [ ] **Step 11.1: TrackedMarketsPanel 改造**

组件签名改为 `export function TrackedMarketsPanel({ eventType }: { eventType: "macro" | "crypto" })`:
- `list` 查询改 `queryKey: ["prediction-tracked", eventType]`,`queryFn: () => api.predictionTracked(eventType)`;
- `create.mutationFn` 的 body 加 `market: eventType`;
- 表格加「归属」列:每行 `row.events` 渲染成 `#{e.display_no} {e.name} ×` 徽章(× 调 `api.researchEventMarketDetach(e.link_id)` 后 invalidate `["prediction-tracked"]` 与 `["event-markets"]`),行尾一个「挂接→」下拉:选项来自 `useQuery(["research-events","active",eventType], () => api.researchEvents({ status: "active", event_type: eventType }))`,选中调 `api.researchEventMarketAttach(eventId, row.id)`。
- 其余(启用开关、删除、URL 提取)原样保留。

- [ ] **Step 11.2: 建 `MarketPricingTab.tsx`**

```tsx
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiErrorText } from "../api/client";
import type { MarketSearchResult, MarketSweepResponse } from "../api/types";
import { Button, SelectControl, TextInput } from "./Controls";
import { MarketChartCard } from "./EventMarkets";
import { MarketProposalPanel, fmtProb } from "./MarketProposalPanel";
import { PredictionCard } from "./PredictionCard";
import { buildFamilyChart, predictionWindowOptions } from "./predictionChart";
import { TrackedMarketsPanel } from "./TrackedMarketsPanel";
import { EmptyState, ErrorState, LoadingState } from "./StateViews";

/** 池页·市场定价页签(spec §5):提案确认制 + 手动搜索 + 常设观测 + 跟踪管理。 */
export function MarketPricingTab({ eventType }: { eventType: "macro" | "crypto" }) {
  const qc = useQueryClient();
  const [hours, setHours] = useState("720");
  const predictions = useQuery({
    queryKey: ["predictions", eventType, hours],
    queryFn: () => api.predictions({ hours: Number(hours), market: eventType })
  });
  const families = useQuery({
    queryKey: ["prediction-families", eventType, hours],
    queryFn: () => api.predictionFamilies({ hours: Number(hours), market: eventType })
  });
  const tracked = useQuery({
    queryKey: ["prediction-tracked", eventType],
    queryFn: () => api.predictionTracked(eventType)
  });
  const activeEvents = useQuery({
    queryKey: ["research-events", "active", eventType],
    queryFn: () => api.researchEvents({ status: "active", event_type: eventType })
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["predictions"] });
    void qc.invalidateQueries({ queryKey: ["prediction-families"] });
    void qc.invalidateQueries({ queryKey: ["prediction-tracked"] });
    void qc.invalidateQueries({ queryKey: ["research-events"] });
  };

  // 找市场提案(确认制,面板复用 MarketProposalPanel)
  const [sweepResult, setSweepResult] = useState<MarketSweepResponse | null>(null);
  const [sweepMeta, setSweepMeta] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const sweep = useMutation({
    mutationFn: () => api.researchMarketSweep(eventType),
    onSuccess: (r) => { setErrorMsg(""); setSweepMeta(""); setSweepResult(r); },
    onError: (err) => setErrorMsg(apiErrorText(err, "找市场提案失败"))
  });

  // 手动搜索通道(不剔价格类)
  const [searchQ, setSearchQ] = useState("");
  const [searchResults, setSearchResults] = useState<MarketSearchResult[] | null>(null);
  const search = useMutation({
    mutationFn: () => api.predictionSearch(searchQ),
    onSuccess: (r) => { setErrorMsg(""); setSearchResults(r); },
    onError: (err) => setErrorMsg(apiErrorText(err, "搜索失败"))
  });
  const [attachTarget, setAttachTarget] = useState("");   // "" = 常设(不挂事件)
  const addTracked = useMutation({
    mutationFn: (row: MarketSearchResult) => api.createPredictionTracked({
      kind: "slug", identifier: row.slug, display_name: row.title || null,
      market: eventType,
      ...(attachTarget ? { event_id: Number(attachTarget) } : {})
    } as Parameters<typeof api.createPredictionTracked>[0]),
    onSuccess: () => { setErrorMsg(""); refresh(); },
    onError: (err) => setErrorMsg(apiErrorText(err, "添加失败"))
  });

  // 常设观测 = 本线未挂任何事件的跟踪项对应的市场卡
  const standaloneCards = useMemo(() => {
    const unlinkedOrigins = new Set(
      (tracked.data ?? []).filter((t) => !t.events.length).map((t) => `${t.kind}:${t.identifier}`));
    const familyIds = new Set(
      (families.data ?? []).flatMap((f) => f.series.map((s) => s.market_id)));
    return (predictions.data?.markets ?? []).filter((m) =>
      !familyIds.has(m.market_id)
      && (m.origin ? unlinkedOrigins.has(m.origin) : eventType === "macro"));
  }, [predictions.data, families.data, tracked.data, eventType]);

  return (
    <section>
      <div className="toolbar">
        <SelectControl label="时间窗口" value={hours} onChange={setHours}
                       options={predictionWindowOptions} />
        <Button kind="secondary" disabled={sweep.isPending}
                onClick={() => {
                  if (window.confirm("找市场提案:AI 把本线进行中事件翻译成英文搜索词,"
                    + "去 Polymarket 找相关市场(价格目标类不提)。提案只展示,勾选采纳才跟踪。开始?"))
                    sweep.mutate();
                }}>
          {sweep.isPending ? "找市场中…" : "找市场提案"}
        </Button>
        <TextInput label="手动搜索 Polymarket" value={searchQ} onChange={setSearchQ}
                   placeholder="fed rate cut / btc etf" />
        <Button onClick={() => searchQ.trim() && search.mutate()} disabled={search.isPending}>
          {search.isPending ? "搜索中…" : "搜索"}
        </Button>
      </div>
      {errorMsg && <div className="panel"><span style={{ color: "var(--danger)" }}>{errorMsg}</span></div>}
      {sweepMeta && !sweepResult && <div className="muted">{sweepMeta}</div>}

      {sweepResult && (
        <MarketProposalPanel eventType={eventType} result={sweepResult}
          onClose={() => setSweepResult(null)}
          onApplied={(summary) => { setSweepResult(null); setSweepMeta(summary); refresh(); }} />
      )}

      {searchResults && (
        <div className="panel">
          <div className="panel-head">
            <h2>搜索结果</h2>
            <SelectControl label="添加时挂到" value={attachTarget} onChange={setAttachTarget}
                           options={[{ label: "常设(不挂事件)", value: "" },
                                     ...(activeEvents.data?.items ?? []).map((e) => ({
                                       label: `#${e.display_no} ${e.name}`, value: String(e.id) }))]} />
            <button type="button" className="link-button" onClick={() => setSearchResults(null)}>收起</button>
          </div>
          {searchResults.length === 0 ? <EmptyState title="没搜到活跃市场" /> : searchResults.map((r) => (
            <div key={r.slug} className="rp-news-item" style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span className="rp-title">{r.title}</span>
              <span className="s-badge mid">{fmtProb(r.current_probability, r.market_count)}</span>
              <span className="muted">量 ${Math.round((r.volume ?? 0) / 1000)}k · 到期 {r.end_date || "—"}</span>
              <span style={{ flex: 1 }} />
              <Button onClick={() => addTracked.mutate(r)} disabled={addTracked.isPending}>添加</Button>
            </div>
          ))}
        </div>
      )}

      <TrackedMarketsPanel eventType={eventType} />

      <section className="panel">
        <div className="panel-head"><h2>常设观测</h2>
          <span className="muted">未挂接事件的跟踪市场(仪表盘类);挂了事件的曲线在事件详情里看</span>
        </div>
        {predictions.isLoading ? <LoadingState /> : predictions.error ? <ErrorState error={predictions.error} /> : (
          <div className="prediction-grid">
            {(families.data ?? []).map((family) => {
              const chart = buildFamilyChart(family);
              return <PredictionCard key={family.id} title={family.name}
                                     subtitle={`${family.series.length} 个分支`}
                                     data={chart.data} keys={chart.keys} />;
            })}
            {standaloneCards.map((m) => (
              <MarketChartCard key={m.market_id} summary={m} hours={Number(hours)} />
            ))}
            {!(families.data ?? []).length && !standaloneCards.length &&
              <EmptyState title="本线暂无常设市场" />}
          </div>
        )}
      </section>
    </section>
  );
}
```

- [ ] **Step 11.3: 渲染测试**

`frontend/src/components/MarketPricingTab.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { MarketPricingTab } from "./MarketPricingTab";

vi.mock("../api/client", () => ({
  api: {
    predictions: vi.fn().mockResolvedValue({ markets: [], latest_timestamp: null }),
    predictionFamilies: vi.fn().mockResolvedValue([]),
    predictionTracked: vi.fn().mockResolvedValue([]),
    predictionSearch: vi.fn().mockResolvedValue([]),
    predictionHistory: vi.fn().mockResolvedValue([]),
    researchEvents: vi.fn().mockResolvedValue({ items: [] }),
    researchMarketSweep: vi.fn(),
    researchMarketSweepApply: vi.fn(),
    researchEventMarketAttach: vi.fn(),
    researchEventMarketDetach: vi.fn(),
    createPredictionTracked: vi.fn(),
    updatePredictionTracked: vi.fn(),
    deletePredictionTracked: vi.fn()
  },
  apiErrorText: () => "err",
  ApiError: class extends Error {}
}));

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("MarketPricingTab", () => {
  it("渲染提案按钮、常设观测与空态", async () => {
    wrap(<MarketPricingTab eventType="macro" />);
    expect(screen.getByText("找市场提案")).toBeTruthy();
    expect(screen.getByText("常设观测")).toBeTruthy();
    expect(await screen.findByText("本线暂无常设市场")).toBeTruthy();
  });
});
```

(TrackedMarketsPanel 在同树内渲染,它的查询也走上面 mock 的 `predictionTracked`;若它还需要别的 api 名,按报错补空 mock。)

Run: `npm --prefix frontend run test -- --run MarketPricingTab`
Expected: PASS

- [ ] **Step 11.4: 提交**

```bash
git add frontend/src/components/MarketPricingTab.tsx frontend/src/components/TrackedMarketsPanel.tsx frontend/src/components/MarketPricingTab.test.tsx
git commit -m "feat(frontend): 市场定价页签——找市场提案确认制、手动搜索添加、常设观测区、跟踪管理带归属列"
```

---

### Task 12: 页签接线 + 事件卡徽章 + 预测页退役

**Files:**
- Modify: `frontend/src/pages/ResearchPage.tsx`(第三页签+徽章)、`frontend/src/main.tsx:12,36`、`frontend/src/components/AppShell.tsx:9`
- Delete: `frontend/src/pages/PredictionsPage.tsx`
- Test: `frontend/src/components/AppShell.test.tsx`、`frontend/src/pages/ResearchPage.test.tsx`(调整)

- [ ] **Step 12.1: EventPoolPage 加页签**

`ResearchPage.tsx`:
- import 加 `import { MarketPricingTab } from "../components/MarketPricingTab";`
- `:489` 的 tab 状态类型改 `useState<"events" | "markets" | "revival">("events")`
- 页签按钮区(:554-555)在「事件」「旧事重提」之间插:

```tsx
        <Button kind={tab === "markets" ? "primary" : "ghost"} onClick={() => setTab("markets")}>市场定价</Button>
```

- 渲染区(:631 附近)加:

```tsx
      {tab === "markets" && <MarketPricingTab eventType={eventType} />}
```

- 事件卡 `rp-card-foot`(:662-664)追加计数徽章:

```tsx
                <span className="rp-card-foot">
                  证据 {e.evidence_count} · 最新 {fmtBjShort(e.last_evidence_bj)}
                  {(e.market_count ?? 0) > 0 && ` · 市场 ${e.market_count}`}
                </span>
```

- [ ] **Step 12.2: 预测页退役**

- 删除 `frontend/src/pages/PredictionsPage.tsx`
- `frontend/src/main.tsx`:删 `:12` 的 import 与 `:36` 的 `{ path: "predictions", element: <PredictionsPage /> }` 路由行
- `frontend/src/components/AppShell.tsx`:删 `:9` 的 `{ to: "/predictions", label: "预测市场", icon: Radar },`(若 `Radar` 图标从此无人用,一并从 import 删掉)

- [ ] **Step 12.3: 修测试 + 全量前端验证**

- `AppShell.test.tsx`:若断言导航项列表,删掉「预测市场」预期。
- `ResearchPage.test.tsx`:若渲染 EventPoolPage 的用例因新页签/新组件的 api mock 缺失而挂,给 mock 补 `researchEventMarkets: () => Promise.resolve({ items: [] })` 等空实现。

Run: `npm --prefix frontend run test -- --run` 然后 `npm --prefix frontend run build`
Expected: vitest 全 PASS;tsc/vite 构建零错误(PredictionsPage 的引用应已清干净)

- [ ] **Step 12.4: 提交**

```bash
git add -A frontend/src
git commit -m "feat(frontend): 池页第三页签市场定价上线,事件卡带市场计数,预测页退役(路由/导航/页面删除)"
```

---

### Task 13: 全量回归 + 文档同步

- [ ] **Step 13.1: 全量回归**

```bash
D:\anaconda\python.exe -m pytest
```
Expected: 全 PASS(基线约 500+;新增约 20)。任何失败逐个修完再继续。

```bash
npm --prefix frontend run test -- --run
```
Expected: 全 PASS。

- [ ] **Step 13.2: OpenAPI 勾稽复核**

```bash
D:\anaconda\python.exe -m pytest tests/test_openapi_types.py -v
```
Expected: PASS(若中途改过路由,重跑 `D:\anaconda\python.exe scripts/generate_openapi_types.py` 并提交 types.ts)

- [ ] **Step 13.3: 文档同步**

- `PENDING.md`:第 77 行「预测市场 × 事件池联动」改 `- [x] **2026-08-28 完成**`,尾注指向 spec;第 106 行 slug 过期条目补一句"2026-08-28 起可用『找后继』提案半自动接续"。(PENDING.md 是否入库以 `git ls-files PENDING.md` 为准,入库则随代码提交。)
- 本地私有文档(不入库):`ARCHITECTURE.md` 模块清单加 `services/market_sweep.py`、`services/event_markets.py`、`models/event_market.py`,前端七页改六页;`DATAFLOW.md` 扫描顺序注明预测小时门控;`DECISIONS.md` 记一条 2026-08-28 决策(四拍板+拒绝备选,素材在 spec §0)。
- spec 状态行改「已实施(2026-08-28),实施计划 docs/superpowers/plans/2026-08-28-polymarket-event-pool-merge.md」。

- [ ] **Step 13.4: 提交**

```bash
git add PENDING.md docs/superpowers/specs/2026-08-28-polymarket-event-pool-merge-design.md
git commit -m "docs: 预测×事件池联动 PENDING 清账,spec 标已实施"
```

---

## 部署清单(合并回 main 之后,按 docs/specs/deployment.md 的 runbook 走)

1. 服务器先备份数据库(runbook 既有姿势:`VACUUM INTO` 快照 + sha256)。
2. `git pull` + `python run.py frontend-build` + 重启 systemd 服务。无新依赖、Nginx 零改动(600s 超时已覆盖 /api)。
3. 重启即自动迁移:建 `research_event_markets`、补 `tracked_markets.market`(存量=macro)。
4. **可选一次性瘦身**(把存量 30 天 5 分钟粒度快照瘦成小时粒度,~90MB→~7MB;跑不跑用户定):

```sql
DELETE FROM prediction_markets
WHERE id NOT IN (
  SELECT MAX(id) FROM prediction_markets
  GROUP BY market_id, outcome, strftime('%Y-%m-%d %H', timestamp)
);
```

(之后不跑 VACUUM 也行,空间会被后续写入复用;要立刻缩文件就在低峰期 VACUUM。)
5. 存量清偿(人工):市场定价页签的跟踪管理表,把现存约 15 个跟踪项逐个「挂接→」到对应事件;挂不上的留常设观测。
6. 验收:宏观线点一次「找市场提案」核对提案质量;观察 24h——快照每小时一批、`prediction_shift` 告警不误报、源健康面板跳过轮显示正常。

## 明确不做(YAGNI,与 spec §6/§8 对齐)

- 不做概率跳变自动写事件时间轴节点(告警已覆盖异动提醒)。
- 不动告警阈值/规则/冷却(阈值重校另起话题)。
- 不删旧 `filters.py`(tag 时代遗留,不复用不删除)。
- 不做定期归档任务(小时级+永久保留后不需要)。
- 「找后继」不做独立端点——就是事件详情/市场卡上用 `event_id` 参数调既有提案接口。
