# 宏观事件池(新闻研究一期)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 docs/specs/news-research-phase1-event-pool.md(v1.0,已终审)落地宏观事件池:人立案、机器挂证据、价格两层标记、工作台与每日清单。

**Architecture:** 两张新表(research_events / research_event_links)+ news_items 游标列;挂接 LLM 调用寄生在既有 5 分钟扫描 tick;事件生命周期纯人工操作;观测层读时现算;前端新增研究页 + 标注页接线。

**Tech Stack:** Python 3 / SQLAlchemy / FastAPI / APScheduler / DeepSeek v4-flash / React 18 + TanStack Query + Vite / pytest + vitest。

---

## 执行前须知(每个任务都适用)

- **spec 是唯一需求源**:docs/specs/news-research-phase1-event-pool.md。本计划引用 spec 章节号(如 §4.1)。
- **Python 一律用 `D:/anaconda/python.exe`**(PATH 里的 python 是坏 stub,直接退出)。测试命令:`D:/anaconda/python.exe -m pytest tests/xxx.py -v`。
- **前端类型是生成的**:改完 API 后必须跑 `D:/anaconda/python.exe scripts/generate_openapi_types.py` 再 `cd frontend && npx tsc -b`。不要手改 frontend/src/api/types.ts。
- **不在本计划内**(spec §13.4,并行 7 天观察期满后另行小改):删打标提示词 topic 槽位、冻结 NEWS_TOPICS/theme_ledger 注释、退役 tag-options topics 键与前端 topic 下拉。
- 提交信息格式照仓库惯例 `feat(research): ...` / `test(research): ...`,末尾加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。

---

### Task 1: config 常量 + ORM 模型 + 游标列迁移(含存量盖章)

**Files:**
- Modify: `config.py`(文件末尾、DATA_RETENTION 段之前加一节)
- Create: `models/research.py`
- Modify: `models/__init__.py`
- Modify: `database.py`(news_items 补列块 + 新迁移函数)
- Test: `tests/test_research_models.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_research_models.py
# -*- coding: utf-8 -*-
"""研究事件池模型与游标迁移(news-research-phase1 spec §3)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from database import Base
from models.news import NewsItem
from models.research import ResearchEvent, ResearchEventLink


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_event_defaults(session):
    e = ResearchEvent(name="日本央行加息预期提前")
    session.add(e); session.commit()
    assert e.event_type == "macro"
    assert e.status == "active"
    assert e.created_from == "manual"
    assert e.gate_keywords is None


def test_link_unique_per_event_news(session):
    e = ResearchEvent(name="x"); session.add(e); session.commit()
    session.add(ResearchEventLink(event_id=e.id, news_id=1, link_source="human"))
    session.commit()
    session.add(ResearchEventLink(event_id=e.id, news_id=1, link_source="auto"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    # 同一新闻挂另一个事件不受限(人工多挂,spec §3.2)
    e2 = ResearchEvent(name="y"); session.add(e2); session.commit()
    session.add(ResearchEventLink(event_id=e2.id, news_id=1, link_source="human"))
    session.commit()


def test_news_item_has_event_cursor_column(session):
    n = NewsItem(timestamp=datetime(2026, 8, 1), source="jin10", title="t", language="zh")
    session.add(n); session.commit()
    assert n.event_linked_at is None    # 新库新新闻:游标空=待处理


def test_migrate_news_event_cursor_stamps_legacy_rows(tmp_path):
    """旧库(无列)→ 补列 + 存量一次性盖章;幂等(spec §3.3/§13.1)。"""
    from database import migrate_news_event_cursor
    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE news_items (id INTEGER PRIMARY KEY, timestamp DATETIME, "
            "source VARCHAR(50), title VARCHAR(500), language VARCHAR(5))"
        ))
        conn.execute(text("INSERT INTO news_items (timestamp, source, title, language) "
                          "VALUES ('2026-07-01 00:00:00', 'jin10', '存量新闻', 'zh')"))
    with eng.begin() as conn:
        assert migrate_news_event_cursor(conn) is True
    with eng.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("news_items")}
        assert "event_linked_at" in cols
        stamped = conn.execute(text(
            "SELECT COUNT(*) FROM news_items WHERE event_linked_at IS NOT NULL")).scalar()
        assert stamped == 1                       # 存量全部盖章(历史默认出池)
    with eng.begin() as conn:
        assert migrate_news_event_cursor(conn) is False   # 第二次跑:无操作


def test_event_config_constants():
    import config
    assert config.EVENT_LINK_MIN_IMPORTANCE == 6
    assert config.EVENT_OBS_REACTION_MINUTES == 10
    assert config.EVENT_OBS_SYMBOLS == ("BTC/USDT",)
    assert config.EVENT_BACKSCAN_DEFAULT_HOURS == 72
    assert ("jin10", r"^金十数据整理：") in config.NEWS_EVENT_LINK_BLACKLIST
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_research_models.py -v`
Expected: FAIL(`ModuleNotFoundError: models.research` / `AttributeError: EVENT_LINK_MIN_IMPORTANCE`)

- [ ] **Step 3: 实现**

`config.py`(插在"数据清理配置"段之前):

```python
# ============================================================
# 研究事件池(docs/specs/news-research-phase1-event-pool.md)
# ============================================================
# 挂接总开关 = 回滚阀(spec §13.5):置 0 停挂接,已建表与数据原地保留。
EVENT_LINK_ENABLED = os.getenv("EVENT_LINK_ENABLED", "1") == "1"
# 挂接闸门线:llm_importance ≥ 6 **或未评分放行**(未评分=评分调用失败,不是不重要;
# 2026-07-28 线上库 30 天校准,71 条人工 driver 召回 96%,见 spec §4.2)。
EVENT_LINK_MIN_IMPORTANCE = int(os.getenv("EVENT_LINK_MIN_IMPORTANCE", "6"))
# 挂接黑名单:(来源, 标题正则),命中直接盖游标不发调用。人工维护,
# **禁止按频率自动生成**(会误杀 FinancialJuice 统一前缀这类真新闻,spec §4.4)。
NEWS_EVENT_LINK_BLACKLIST = (
    ("jin10", r"^金十数据整理："),
    ("jin10", r"^金十数据全球财经早餐"),
)
# 观测层(spec §8.1):基线=新闻前最近快照,终点=新闻后 N 分钟内最后快照。
EVENT_OBS_REACTION_MINUTES = int(os.getenv("EVENT_OBS_REACTION_MINUTES", "10"))
EVENT_OBS_SYMBOLS = ("BTC/USDT",)
# 立案/重开自动回扫范围(小时);深回扫由工作台按钮传天数。
EVENT_BACKSCAN_DEFAULT_HOURS = int(os.getenv("EVENT_BACKSCAN_DEFAULT_HOURS", "72"))
```

`models/research.py`(新文件,全文):

```python
# -*- coding: utf-8 -*-
"""研究事件池模型(news-research-phase1 spec §3):事件 + 时间轴挂接。
铁律:事件=名字+状态+时间轴;时间轴展示的时间/方向标/观测值/徽章全部读时派生,
挂接表不存任何业务数值。gate_keywords 是路由配置(免闸),不是语义字段。"""
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, Index, UniqueConstraint
from datetime import datetime
from database import Base


class ResearchEvent(Base):
    """事件主表:两态(active/closed),仅人工立案(spec §6.1)。"""
    __tablename__ = "research_events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(16), nullable=False, default="macro")   # 二期叙事共用本表时加 narrative
    name = Column(String(80), nullable=False)
    status = Column(String(10), nullable=False, default="active")      # active / closed
    gate_keywords = Column(Text, nullable=True)       # 顿号分隔;空=不免闸;已关闭事件的词走沉睡监听
    merged_into_id = Column(Integer, nullable=True)
    closed_reason = Column(Text, nullable=True)
    created_from = Column(String(12), nullable=False, default="manual")  # annotation / manual
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    status_changed_at = Column(DateTime, nullable=True)


class ResearchEventLink(Base):
    """时间轴挂接:只增不删;摘下=标记(留痕);模型原判 auto_event_id 人改后保留。"""
    __tablename__ = "research_event_links"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, nullable=False, index=True)
    news_id = Column(Integer, nullable=False)
    link_source = Column(String(8), nullable=False)    # auto=模型挂且未经人动 / human=人挂或人改过
    auto_event_id = Column(Integer, nullable=True)     # 模型原判事件;纯人工挂接 NULL
    confidence = Column(Float, nullable=True)          # 三档 0.9/0.65/0.3;仅 auto
    prompt_version = Column(String(40), nullable=True)
    detached = Column(Boolean, nullable=False, default=False)
    detach_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("event_id", "news_id", name="uq_research_link_event_news"),
        Index("ix_research_link_news", "news_id"),
    )
```

`models/__init__.py`:在既有导入后加

```python
from models.research import ResearchEvent, ResearchEventLink
```

`models/news.py`:NewsItem 的 `tagged_at` 列之后加

```python
    event_linked_at = Column(DateTime, nullable=True)    # 挂接游标:空=待处理;四种结果都盖章(spec §3.3)
```

`database.py`:新增迁移函数(放 `migrate_legacy_annotations` 附近),并在 `_ensure_sqlite_schema` 的 news_items 块(补列 for 循环之后)调用 `migrate_news_event_cursor(conn)`:

```python
def migrate_news_event_cursor(conn) -> bool:
    """news_items.event_linked_at 补列 + 存量一次性盖章(研究事件池,spec §3.3/§13.1)。
    只在缺列那一次盖章:历史新闻默认"已处理出池",要历史靠回扫清游标召回。
    新库 create_all 自带该列不走此分支(且新库无存量),幂等。"""
    from sqlalchemy import inspect as _inspect
    existing = {col["name"] for col in _inspect(conn).get_columns("news_items")}
    if "event_linked_at" in existing:
        return False
    conn.execute(text("ALTER TABLE news_items ADD COLUMN event_linked_at DATETIME"))
    conn.execute(text("UPDATE news_items SET event_linked_at = CURRENT_TIMESTAMP"))
    return True
```

注意:`event_linked_at` **不要**加进 news_items 那个补列 dict(那条路径只补列不盖章,会把 6.6 万条存量当成待办,spec §13.1 明确要防)。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_research_models.py -v`
Expected: 5 passed

- [ ] **Step 5: 回归**

Run: `D:/anaconda/python.exe -m pytest tests/test_data_retention.py tests/test_news_tagging.py -q`
Expected: all passed(确认模型导入与既有表共存无冲突)

- [ ] **Step 6: Commit**

```bash
git add config.py models/research.py models/__init__.py models/news.py database.py tests/test_research_models.py
git commit -m "feat(research): 事件池模型+游标列迁移+config 常量 (spec §3/§11)"
```

---

### Task 2: 观测层 observed_reaction(theme_ledger)

**Files:**
- Modify: `services/theme_ledger.py`(文件末尾追加;**冻结的旧函数一行不动**)
- Test: `tests/test_theme_ledger.py`(追加)

- [ ] **Step 1: 写失败测试**(追加到 tests/test_theme_ledger.py,沿用该文件既有 session fixture / 造快照 helper;若无现成 helper 用下面的)

```python
# ---- 观测层(news-research-phase1 spec §8.1) ----
from datetime import datetime, timedelta
from services.theme_ledger import observed_reaction, observed_reaction_from_rows

T0 = datetime(2026, 8, 1, 10, 2)   # 新闻时间(两根 5min bar 之间)


def _rows(*pairs):
    """[(分钟偏移, 价格)] → [(timestamp, price)],相对 10:00 整点。"""
    base = datetime(2026, 8, 1, 10, 0)
    return [(base + timedelta(minutes=m), p) for m, p in pairs]


def test_obs_pending_before_window_completes():
    r = observed_reaction_from_rows(_rows((0, 100.0)), T0, minutes=10,
                                    now=T0 + timedelta(minutes=5))
    assert r == {"status": "pending"}


def test_obs_baseline_is_pre_news_snapshot():
    # 基线=新闻前 10:00 的 100,终点=10:10 的 103 → +3%;冲刺段(10:00→10:05)没被吃掉
    rows = _rows((0, 100.0), (5, 102.0), (10, 103.0), (15, 999.0))   # 15min 的在窗外
    r = observed_reaction_from_rows(rows, T0, minutes=10, now=T0 + timedelta(minutes=30))
    assert r["status"] == "ok"
    assert abs(r["net_pct"] - 3.0) < 1e-9
    assert r["actual_minutes"] == 10.0        # 10:00 → 10:10


def test_obs_no_baseline_within_tolerance():
    # 新闻前最近快照在 9:50(距新闻 12min > 容差 6min)→ no_data
    rows = _rows((-12, 100.0), (5, 102.0), (10, 103.0))
    r = observed_reaction_from_rows(rows, T0, minutes=10, now=T0 + timedelta(minutes=30))
    assert r == {"status": "no_data"}


def test_obs_session_wrapper(session):
    # session fixture 与本文件其它测试共用;造 3 根 BTC 快照再走 DB 路径
    from models.price import PriceSnapshot
    base = datetime(2026, 8, 1, 10, 0)
    for m, p in ((0, 100.0), (5, 102.0), (10, 103.0)):
        session.add(PriceSnapshot(timestamp=base + timedelta(minutes=m), asset_class="crypto",
                                  symbol="BTC/USDT", name="BTC", price=p, source="test"))
    session.commit()
    r = observed_reaction(session, "BTC/USDT", T0, minutes=10, now=T0 + timedelta(minutes=30))
    assert r["status"] == "ok" and abs(r["net_pct"] - 3.0) < 1e-9
```

(若 test_theme_ledger.py 没有 session fixture,在文件顶部按 tests/test_news_tagging.py 的 in-memory fixture 抄一份。)

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_theme_ledger.py -v -k obs`
Expected: FAIL(ImportError: observed_reaction)

- [ ] **Step 3: 实现**(services/theme_ledger.py 末尾追加)

```python
# ---- 研究事件池·观测层(news-research-phase1 spec §8.1)----
# 与 forward_reaction 的差别:基线取新闻**前**最近快照(容差内),防 5min K 线粒度
# 把 0-5 分钟冲刺段吃掉;窗口没走完返回 pending(前端显示"计算中",不给半熟数)。

OBS_BASELINE_TOLERANCE_MINUTES = 6


def observed_reaction_from_rows(rows, news_time: datetime, minutes: int | None = None,
                                now: datetime | None = None,
                                tolerance_minutes: int = OBS_BASELINE_TOLERANCE_MINUTES) -> dict:
    """rows: 时间升序的 (timestamp, price) 序列(单一品种)。纯函数,时间轴批量取数复用。
    返回 {"status": "pending"} / {"status": "no_data"} /
    {"status": "ok", "net_pct", "actual_minutes", "start", "end"}。"""
    import config as _config
    minutes = minutes or _config.EVENT_OBS_REACTION_MINUTES
    now = now or utc_now_naive()
    if now < news_time + timedelta(minutes=minutes):
        return {"status": "pending"}
    baseline = None
    end = None
    for ts, price in rows:
        if not price:
            continue
        if ts <= news_time:
            if news_time - ts <= timedelta(minutes=tolerance_minutes):
                baseline = (ts, price)          # 不断覆盖 → 留下新闻前最近的一根
        elif ts <= news_time + timedelta(minutes=minutes):
            end = (ts, price)
        else:
            break
    if baseline is None or end is None:
        return {"status": "no_data"}
    return {
        "status": "ok",
        "net_pct": (end[1] - baseline[1]) / abs(baseline[1]) * 100,
        "actual_minutes": round((end[0] - baseline[0]).total_seconds() / 60, 1),
        "start": baseline[1], "end": end[1],
    }


def observed_reaction(session: Session, symbol: str, news_time: datetime,
                      minutes: int | None = None, now: datetime | None = None) -> dict:
    """单条新闻的观测值(自带小范围查库);时间轴批量场景用 observed_reaction_from_rows。"""
    import config as _config
    minutes = minutes or _config.EVENT_OBS_REACTION_MINUTES
    rows = (
        session.query(PriceSnapshot.timestamp, PriceSnapshot.price)
        .filter(
            PriceSnapshot.symbol == symbol,
            PriceSnapshot.timestamp >= news_time - timedelta(minutes=OBS_BASELINE_TOLERANCE_MINUTES),
            PriceSnapshot.timestamp <= news_time + timedelta(minutes=minutes),
        )
        .order_by(PriceSnapshot.timestamp.asc())
        .all()
    )
    return observed_reaction_from_rows(rows, news_time, minutes=minutes, now=now)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_theme_ledger.py -v`
Expected: 既有 + 新增全部 passed(冻结函数未动)

- [ ] **Step 5: Commit**

```bash
git add services/theme_ledger.py tests/test_theme_ledger.py
git commit -m "feat(research): 观测层 observed_reaction:前基线+10min 窗+pending (spec §8.1)"
```

---

### Task 3: 挂接资格判定(黑名单/闸门/关键词,纯函数)

**Files:**
- Create: `services/event_linking.py`(本任务只写资格部分)
- Test: `tests/test_event_linking.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_event_linking.py
# -*- coding: utf-8 -*-
"""挂接调用(news-research-phase1 spec §4-§5):资格判定 + 解析防幻觉 + 游标语义。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.news import NewsItem
from models.research import ResearchEvent, ResearchEventLink
from services import event_linking


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _news(s, title, score=8, source="jin10", content="", tagged=True, ts=None):
    n = NewsItem(timestamp=ts or datetime(2026, 8, 1, 12, 0), source=source, title=title,
                 content=content, language="zh", llm_importance=score,
                 tagged_at=datetime(2026, 8, 1, 12, 1) if tagged else None)
    s.add(n); s.commit()
    return n


def _event(s, name, keywords=None, status="active"):
    e = ResearchEvent(name=name, gate_keywords=keywords, status=status)
    s.add(e); s.commit()
    return e


def test_blacklist_matches_source_and_title(session):
    junk = _news(session, "金十数据整理：每日全球大宗商品要闻", score=7)
    real = _news(session, "据伊朗媒体Fars News:交火升级", score=7)
    other_src = _news(session, "金十数据整理：xxx", score=7, source="cnbc")
    assert event_linking._is_blacklisted(junk) is True
    assert event_linking._is_blacklisted(real) is False
    assert event_linking._is_blacklisted(other_src) is False   # 黑名单绑定来源


def test_gate_score_or_unscored(session):
    assert event_linking.passes_gate(_news(session, "a", score=6), []) is True
    assert event_linking.passes_gate(_news(session, "b", score=5), []) is False
    assert event_linking.passes_gate(_news(session, "c", score=None), []) is True   # 未评分放行


def test_gate_keyword_bypass_any_hit(session):
    kw = ["苹果", "Apple"]
    low = _news(session, "Apple 供应链传出新一轮调价", score=3)
    low2 = _news(session, "苹果公司回应调价传闻", score=2)
    miss = _news(session, "特斯拉降价", score=3)
    assert event_linking.passes_gate(low, kw) is True     # 或的关系:命中任一即免闸
    assert event_linking.passes_gate(low2, kw) is True
    assert event_linking.passes_gate(miss, kw) is False
    # 英文不分大小写;匹配范围=标题+摘要
    body = _news(session, "科技股盘前动态", score=3, content="apple iphone pricing rumor")
    assert event_linking.passes_gate(body, kw) is True


def test_split_keywords_tolerates_commas():
    assert event_linking._split_keywords("苹果、Apple,iPhone，调价") == ["苹果", "Apple", "iPhone", "调价"]
    assert event_linking._split_keywords(None) == []
    assert event_linking._split_keywords(" 、 ") == []


def test_keyword_pool_only_active_events(session):
    _event(session, "苹果调价", keywords="苹果、Apple")
    _event(session, "已关闭的", keywords="客机", status="closed")
    events = event_linking._active_events(session)
    assert event_linking._keyword_pool(events) == ["苹果", "Apple"]   # closed 的词不进免闸(走沉睡监听)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_event_linking.py -v`
Expected: FAIL(ModuleNotFoundError: services.event_linking)

- [ ] **Step 3: 实现**(services/event_linking.py 新文件,本任务先写到资格判定为止)

```python
# -*- coding: utf-8 -*-
"""研究事件池·挂接调用(news-research-phase1 spec §4-§5)。

模型只有挂接权:把过闸新闻挂到某个进行中事件,或判不挂。闸门/黑名单/关键词免闸
全部在代码里判(可审计)。游标 news_items.event_linked_at 四种结果都盖章
(挂/不挂/不够格/黑名单),回扫=清游标。人工挂接无视本文件所有闸门(在 event_pool.py)。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy.orm import Session

import config
from models.news import NewsItem
from models.research import ResearchEvent, ResearchEventLink
from services.deepseek_client import call_deepseek_chat
from services.time_utils import utc_now_naive


def _is_blacklisted(news: NewsItem) -> bool:
    """固定栏目黑名单(spec §4.4):来源+标题正则都命中才算。"""
    for source, pattern in config.NEWS_EVENT_LINK_BLACKLIST:
        if news.source == source and re.search(pattern, news.title or ""):
            return True
    return False


def _split_keywords(raw: str | None) -> list[str]:
    """顿号分隔(容忍中英文逗号);空白剔除。"""
    if not raw:
        return []
    return [w.strip() for w in re.split(r"[、,，]", raw) if w.strip()]


def _active_events(session: Session) -> list[ResearchEvent]:
    return (session.query(ResearchEvent)
            .filter(ResearchEvent.status == "active")
            .order_by(ResearchEvent.id.asc()).all())


def _keyword_pool(events: list[ResearchEvent]) -> list[str]:
    """全部进行中事件关键词的并集(免闸用;已关闭事件的词走沉睡监听,不在此)。"""
    out: list[str] = []
    for e in events:
        out.extend(_split_keywords(e.gate_keywords))
    return out


def _news_text(news: NewsItem) -> str:
    return f"{news.title or ''} {(news.content or '')[:200]}".lower()


def passes_gate(news: NewsItem, keywords: list[str]) -> bool:
    """闸门(spec §4.1):≥6 或未评分 或命中任一进行中事件关键词。
    免闸≠指定归属——挂到哪仍由模型对整个活跃池判断。"""
    if news.llm_importance is None or news.llm_importance >= config.EVENT_LINK_MIN_IMPORTANCE:
        return True
    text = _news_text(news)
    return any(k.lower() in text for k in keywords)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_event_linking.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add services/event_linking.py tests/test_event_linking.py
git commit -m "feat(research): 挂接资格判定:黑名单/闸门/关键词免闸 (spec §4.1/§4.4/§5.1)"
```

---

### Task 4: 挂接调用:提示词/解析防幻觉/游标语义

**Files:**
- Modify: `services/event_linking.py`(追加)
- Test: `tests/test_event_linking.py`(追加)

- [ ] **Step 1: 写失败测试**(追加)

```python
# ---- 解析防幻觉 + link_unprocessed 游标语义(spec §4.3)----

def test_parse_filters_hallucination():
    raw = json.dumps({"items": [
        {"id": 1, "event_id": 17, "confidence": 0.9},     # 合法
        {"id": 2, "event_id": None, "confidence": 0.9},   # 合法"不挂"
        {"id": 3, "event_id": 99, "confidence": 0.9},     # 池外事件 → 丢弃
        {"id": 4, "event_id": 17, "confidence": 0.82},    # 非三档 → 丢弃
        {"id": 88, "event_id": 17, "confidence": 0.9},    # 幻觉新闻 id → 丢弃
    ]})
    out = event_linking._parse_link_response(raw, valid_news_ids={1, 2, 3, 4},
                                             valid_event_ids={17})
    assert out == {1: {"event_id": 17, "confidence": 0.9},
                   2: {"event_id": None, "confidence": None}}


def test_link_unprocessed_stamps_and_links(session, monkeypatch):
    e = _event(session, "苹果调价", keywords="苹果")
    hit = _news(session, "苹果宣布调价", score=8)
    no = _news(session, "无关新闻不挂", score=7)
    low = _news(session, "低分且不命中", score=3)
    junk = _news(session, "金十数据整理：每日热门ETF", score=9)

    def fake_call(user_content):
        assert "苹果调价" in user_content        # 活跃池摘要进了提示词
        return json.dumps({"items": [
            {"id": hit.id, "event_id": e.id, "confidence": 0.9},
            {"id": no.id, "event_id": None, "confidence": 0.9},
        ]})
    monkeypatch.setattr(event_linking, "_call_linker", fake_call)

    stats = event_linking.link_unprocessed(session)
    assert stats["linked"] == 1
    # 四种结果都盖章:挂/不挂/不够格/黑名单
    for n in (hit, no, low, junk):
        session.refresh(n)
        assert n.event_linked_at is not None
    link = session.query(ResearchEventLink).filter_by(news_id=hit.id).one()
    assert (link.event_id, link.link_source, link.auto_event_id, link.confidence) == \
        (e.id, "auto", e.id, 0.9)
    assert link.prompt_version == event_linking.LINK_PROMPT_VERSION


def test_link_unprocessed_empty_pool_skips_everything(session, monkeypatch):
    _news(session, "有新闻但没事件", score=9)
    called = []
    monkeypatch.setattr(event_linking, "_call_linker",
                        lambda c: called.append(c) or "{}")
    stats = event_linking.link_unprocessed(session)
    assert stats == {"processed": 0, "linked": 0, "called": 0}
    assert not called                    # 池空:零调用,游标也不动(spec §4.1)


def test_link_unprocessed_batch_failure_keeps_cursor(session, monkeypatch):
    _event(session, "事件X")
    n = _news(session, "会失败的批", score=9)
    def boom(user_content):
        raise RuntimeError("网络超时")
    monkeypatch.setattr(event_linking, "_call_linker", boom)
    stats = event_linking.link_unprocessed(session)
    session.refresh(n)
    assert n.event_linked_at is None     # 整批失败不盖章,下轮重试
    assert stats["linked"] == 0


def test_link_unprocessed_invalid_item_not_stamped(session, monkeypatch):
    e = _event(session, "事件X")
    good = _news(session, "合法条", score=9)
    bad = _news(session, "被模型漏答的条", score=9)
    monkeypatch.setattr(event_linking, "_call_linker", lambda c: json.dumps(
        {"items": [{"id": good.id, "event_id": None, "confidence": 0.9}]}))
    event_linking.link_unprocessed(session)
    session.refresh(good); session.refresh(bad)
    assert good.event_linked_at is not None
    assert bad.event_linked_at is None   # 未被合法解析:不盖章,下轮重试


def test_untagged_news_not_picked(session, monkeypatch):
    _event(session, "事件X")
    n = _news(session, "还没打标", score=9, tagged=False)
    monkeypatch.setattr(event_linking, "_call_linker", lambda c: json.dumps({"items": []}))
    event_linking.link_unprocessed(session)
    session.refresh(n)
    assert n.event_linked_at is None     # tagged_at 为空的不进挂接(评分未必跑过)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_event_linking.py -v -k "parse or unprocessed or untagged"`
Expected: FAIL(AttributeError: _parse_link_response)

- [ ] **Step 3: 实现**(services/event_linking.py 追加)

```python
LINK_SYSTEM_PROMPT = (
    "你是宏观新闻研究助理。下面给你一份【活跃事件池】和一批新闻,判断每条新闻是否是池中某个事件的新证据。\n"
    "规则:\n"
    "- 只做归类,不评判新闻重要性;新闻与所有事件都无关 → event_id 给 null(不挂)。\n"
    "- 不确定就不挂:只有主体与事态确实属于该事件才挂,模糊相似不算。\n"
    "- 转载/同一起源的重复报道照挂(时间轴自会显示簇拥,人工把关兜底)。\n"
    "只返回 JSON,不要 Markdown:\n"
    '{"items": [{"id": 新闻id, "event_id": 事件编号或null, "confidence": 0.9}, ...]}\n'
    "confidence 三档:0.9=明确属于;0.65=大概率属于;0.3=勉强(倾向不挂)。\n"
    "每条输入新闻在 items 里有且仅有一项,id 严格对应输入;event_id 必须是池中编号。"
)

# 版本戳:每次实质性修改 LINK_SYSTEM_PROMPT 时更新;随每条 auto 挂接落库。
LINK_PROMPT_VERSION = "link-v1-20260802"

VALID_CONFIDENCES = (0.9, 0.65, 0.3)


def _pool_summary(session: Session, events: list[ResearchEvent]) -> str:
    """活跃池摘要:编号+名称+首条证据标题(定义锚)+最近证据日期(spec §4.3)。"""
    lines = []
    for e in events:
        rows = (session.query(NewsItem)
                .join(ResearchEventLink, ResearchEventLink.news_id == NewsItem.id)
                .filter(ResearchEventLink.event_id == e.id,
                        ResearchEventLink.detached.is_(False))
                .order_by(NewsItem.timestamp.asc()).all())
        first_title = (rows[0].title or "")[:60] if rows else "(暂无证据)"
        last_date = rows[-1].timestamp.strftime("%m-%d") if rows else "—"
        lines.append(f"#{e.id} {e.name} | 首条证据: {first_title} | 最近证据: {last_date}")
    return "\n".join(lines)


def _build_link_payload(pool_summary: str, news_list: list[NewsItem]) -> str:
    items = [{"id": n.id, "source": n.source, "title": (n.title or "")[:160],
              "content": (n.content or "")[:200]} for n in news_list]
    return (f"【活跃事件池】\n{pool_summary}\n\n"
            f"【新闻,共 {len(items)} 条】\n{json.dumps({'news': items}, ensure_ascii=False)}")


def _call_linker(user_content: str) -> str:
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置,无法挂接")
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": LINK_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 2000,
        "temperature": 0,
    }
    result = call_deepseek_chat(
        payload, api_key=config.DEEPSEEK_API_KEY,
        timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, config.DEEPSEEK_READ_TIMEOUT),
        http_error_prefix="DeepSeek 挂接返回", error_preview_chars=200,
        normalize_error_newlines=False,
    )
    if not result.content:
        raise RuntimeError("DeepSeek 挂接返回空 content")
    return result.content


def _parse_link_response(raw: str, valid_news_ids: set[int],
                         valid_event_ids: set[int]) -> dict[int, dict]:
    """防幻觉(spec §4.3):新闻 id 必须在本批、event_id 必须在池内(或 null)、
    confidence 必须三档;非法条目整条丢弃(不盖游标,下轮重试)。"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            raise ValueError(f"挂接返回非 JSON: {text[:200]}")
        data = json.loads(m.group(0))
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("挂接返回缺少 items 列表")
    out: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            nid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if nid not in valid_news_ids:
            continue
        event_id = item.get("event_id")
        if event_id is None:
            out[nid] = {"event_id": None, "confidence": None}
            continue
        try:
            event_id = int(event_id)
        except (TypeError, ValueError):
            continue
        if event_id not in valid_event_ids:
            continue
        if item.get("confidence") not in VALID_CONFIDENCES:
            continue
        out[nid] = {"event_id": event_id, "confidence": float(item["confidence"])}
    return out


def _create_auto_link(session: Session, event_id: int, news_id: int,
                      confidence: float | None) -> ResearchEventLink:
    existing = (session.query(ResearchEventLink)
                .filter_by(event_id=event_id, news_id=news_id).first())
    if existing:
        return existing        # 唯一约束:已有挂接(含人工/已摘下)不重复建、不覆盖
    link = ResearchEventLink(event_id=event_id, news_id=news_id, link_source="auto",
                             auto_event_id=event_id, confidence=confidence,
                             prompt_version=LINK_PROMPT_VERSION)
    session.add(link)
    return link


def link_unprocessed(session: Session, limit: int = 200,
                     batch_size: int | None = None) -> dict:
    """tick 入口(spec §4.1):处理游标为空的新闻,四种结果都盖章。
    返回 {"processed": 盖章数, "linked": 新增挂接数, "called": 进LLM条数}。"""
    stats = {"processed": 0, "linked": 0, "called": 0}
    events = _active_events(session)
    if not events:
        return stats                     # 池空整段跳过,零调用、游标不动
    keywords = _keyword_pool(events)
    todo = (session.query(NewsItem)
            .filter(NewsItem.tagged_at.isnot(None), NewsItem.event_linked_at.is_(None))
            .order_by(NewsItem.timestamp.desc())
            .limit(max(1, limit)).all())
    now = utc_now_naive()
    to_llm: list[NewsItem] = []
    for n in todo:
        if _is_blacklisted(n) or not passes_gate(n, keywords):
            n.event_linked_at = now      # 不够格/黑名单:盖章零调用
            stats["processed"] += 1
        else:
            to_llm.append(n)
    session.commit()
    if not to_llm:
        return stats
    pool_summary = _pool_summary(session, events)
    valid_event_ids = {int(e.id) for e in events}
    batch_size = int(batch_size or config.DEEPSEEK_BATCH_SIZE)
    for i in range(0, len(to_llm), batch_size):
        chunk = to_llm[i:i + batch_size]
        stats["called"] += len(chunk)
        try:
            raw = _call_linker(_build_link_payload(pool_summary, chunk))
            parsed = _parse_link_response(raw, {int(n.id) for n in chunk}, valid_event_ids)
        except Exception as exc:         # 整批失败:不盖游标,下轮重试
            logger.error(f"[EventLink] 分片挂接失败({len(chunk)} 条): {exc}")
            continue
        now = utc_now_naive()
        by_id = {int(n.id): n for n in chunk}
        for nid, r in parsed.items():
            n = by_id.get(nid)
            if n is None:
                continue
            if r["event_id"] is not None:
                _create_auto_link(session, r["event_id"], nid, r["confidence"])
                stats["linked"] += 1
            n.event_linked_at = now      # 只有合法解析条目盖章(含"不挂")
            stats["processed"] += 1
        session.commit()
    return stats
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_event_linking.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add services/event_linking.py tests/test_event_linking.py
git commit -m "feat(research): 挂接调用:提示词+防幻觉解析+四态盖章游标 (spec §4.3)"
```

---

### Task 5: 回扫(清游标)+ scan_runtime 接线

**Files:**
- Modify: `services/event_linking.py`(追加 clear_link_cursor)
- Modify: `services/scan_runtime.py`(`_tag_new_news()` 调用点之后接 `_link_new_news()`)
- Test: `tests/test_event_linking.py`(追加)

- [ ] **Step 1: 写失败测试**(追加)

```python
# ---- 回扫=清游标(spec §6.3)+ tick 接线 ----
from datetime import timedelta


def test_clear_link_cursor_scope(session):
    now = datetime(2026, 8, 1, 12, 0)
    e = _event(session, "苹果调价", keywords="苹果")
    stamped = datetime(2026, 8, 1, 11, 0)
    def mk(title, score, ts, linked_to=None):
        n = _news(session, title, score=score, ts=ts)
        n.event_linked_at = stamped
        if linked_to:
            session.add(ResearchEventLink(event_id=linked_to.id, news_id=n.id,
                                          link_source="human"))
        session.commit()
        return n
    in_range_ok = mk("苹果的旧证据", 3, now - timedelta(hours=10))       # 命中关键词 → 清
    in_range_high = mk("高分旧新闻", 8, now - timedelta(hours=10))       # 过闸 → 清
    in_range_low = mk("低分不命中", 3, now - timedelta(hours=10))        # 不够格 → 不清
    out_range = mk("范围外的苹果新闻", 8, now - timedelta(hours=100))    # 超 72h → 不清
    already = mk("已挂过的苹果新闻", 8, now - timedelta(hours=10), linked_to=e)  # 有挂接 → 不清

    cleared = event_linking.clear_link_cursor(session, hours=72, now=now)
    assert cleared == 2
    for n, expect in ((in_range_ok, None), (in_range_high, None),
                      (in_range_low, stamped), (out_range, stamped), (already, stamped)):
        session.refresh(n)
        assert n.event_linked_at == expect


def test_scan_runtime_link_hook(monkeypatch):
    """_link_new_news:开关关/无 key 时静默跳过;异常自吞不影响扫描。"""
    from services import scan_runtime
    calls = []
    monkeypatch.setattr("services.event_linking.link_unprocessed",
                        lambda s, limit=200: calls.append(limit) or {"processed": 0, "linked": 0, "called": 0})
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(config, "EVENT_LINK_ENABLED", True)
    scan_runtime._link_new_news()
    assert calls == [200]
    monkeypatch.setattr(config, "EVENT_LINK_ENABLED", False)
    scan_runtime._link_new_news()
    assert calls == [200]                 # 开关关:没再调
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_event_linking.py -v -k "cursor or hook"`
Expected: FAIL(clear_link_cursor / _link_new_news 不存在)

- [ ] **Step 3: 实现**

services/event_linking.py 追加:

```python
def clear_link_cursor(session: Session, hours: float, now: datetime | None = None) -> int:
    """回扫=清游标(spec §6.3):范围内**当前够格**(过闸或命中关键词、不在黑名单)
    且**无未摘下挂接**的新闻,游标清空 → 下轮 tick 对着更新后的池子自然重收。
    立案/重开/改关键词勾选时用默认 72h;深回扫按钮传更大的 hours。返回清空条数。"""
    now = now or utc_now_naive()
    events = _active_events(session)
    keywords = _keyword_pool(events)
    cutoff = now - timedelta(hours=hours)
    linked_ids = {row[0] for row in session.query(ResearchEventLink.news_id)
                  .filter(ResearchEventLink.detached.is_(False)).all()}
    rows = (session.query(NewsItem)
            .filter(NewsItem.timestamp >= cutoff,
                    NewsItem.event_linked_at.isnot(None)).all())
    cleared = 0
    for n in rows:
        if int(n.id) in linked_ids:
            continue
        if _is_blacklisted(n) or not passes_gate(n, keywords):
            continue
        n.event_linked_at = None
        cleared += 1
    session.commit()
    return cleared
```

services/scan_runtime.py:在 `_tag_new_news()` 函数定义之后加,并在 `run_scan_once` 里 `_tag_new_news()` 调用行之后加一行 `_link_new_news()`:

```python
def _link_new_news() -> None:
    """挂接游标为空的新闻到活跃事件池(news-research-phase1 spec §4.1)。
    与打标同模式:无 key/开关关静默跳过;异常自吞不影响本轮扫描。"""
    if not getattr(config, "DEEPSEEK_API_KEY", ""):
        return
    if not getattr(config, "EVENT_LINK_ENABLED", False):
        return
    from services.event_linking import link_unprocessed
    session = get_session()
    try:
        stats = link_unprocessed(session, limit=200)
        if stats["processed"] or stats["linked"]:
            logger.info(f"[EventLink] 本轮盖章 {stats['processed']} 条,新挂 {stats['linked']} 条")
    except Exception as exc:
        logger.exception(f"[EventLink] 挂接失败,不影响本轮扫描: {exc}")
    finally:
        session.close()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_event_linking.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add services/event_linking.py services/scan_runtime.py tests/test_event_linking.py
git commit -m "feat(research): 回扫=清游标 + 扫描 tick 接线 EVENT_LINK_ENABLED (spec §4.1/§6.3)"
```

---

### Task 6: 事件生命周期写操作(event_pool.py)

**Files:**
- Create: `services/event_pool.py`
- Test: `tests/test_event_pool.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_event_pool.py
# -*- coding: utf-8 -*-
"""事件生命周期(news-research-phase1 spec §6)+ 读取层(§8-§10)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.news import NewsItem, NewsPriceAnnotation
from models.research import ResearchEvent, ResearchEventLink
from services import event_pool


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _news(s, title, score=8, ts=None, source="jin10"):
    n = NewsItem(timestamp=ts or datetime(2026, 8, 1, 12, 0), source=source, title=title,
                 content="", language="zh", llm_importance=score,
                 tagged_at=datetime(2026, 8, 1, 12, 1))
    s.add(n); s.commit()
    return n


def test_create_event_requires_news(session):
    with pytest.raises(ValueError):
        event_pool.create_event(session, "空壳事件", news_ids=[])


def test_create_event_links_seed_and_backscans(session):
    n = _news(session, "种子新闻", score=3)      # 低分:人工立案无视闸门
    old = _news(session, "72h 内的旧证据", score=8, ts=datetime(2026, 8, 1, 2, 0))
    old.event_linked_at = datetime(2026, 8, 1, 3, 0); session.commit()
    e = event_pool.create_event(session, "苹果调价", news_ids=[n.id],
                                gate_keywords="苹果、Apple", created_from="annotation",
                                now=datetime(2026, 8, 1, 13, 0))
    assert (e.status, e.created_from) == ("active", "annotation")
    link = session.query(ResearchEventLink).filter_by(event_id=e.id, news_id=n.id).one()
    assert (link.link_source, link.auto_event_id, link.confidence) == ("human", None, None)
    session.refresh(old)
    assert old.event_linked_at is None            # 立案自动回扫 72h 清了旧证据游标


def test_close_reopen(session):
    n = _news(session, "x")
    e = event_pool.create_event(session, "事件", news_ids=[n.id])
    event_pool.close_event(session, e.id, reason="已定价")
    session.refresh(e)
    assert (e.status, e.closed_reason) == ("closed", "已定价")
    event_pool.reopen_event(session, e.id)
    session.refresh(e)
    assert e.status == "active"


def test_merge_moves_links_keywords_and_closes(session):
    n1, n2, shared = _news(session, "a"), _news(session, "b"), _news(session, "共有")
    a = event_pool.create_event(session, "A", news_ids=[n1.id, shared.id], gate_keywords="苹果")
    b = event_pool.create_event(session, "B", news_ids=[n2.id, shared.id], gate_keywords="Apple、苹果")
    moved = event_pool.merge_event(session, source_id=a.id, target_id=b.id)
    assert moved == 1                              # 只有 n1 迁移;shared 撞唯一索引跳过
    session.refresh(a); session.refresh(b)
    assert (a.status, a.merged_into_id) == ("closed", b.id)
    assert a.closed_reason == f"合并入 #{b.id}"
    assert b.gate_keywords == "Apple、苹果"        # 并入去重(苹果已有)
    b_news = {l.news_id for l in session.query(ResearchEventLink).filter_by(event_id=b.id)}
    assert b_news == {n1.id, n2.id, shared.id}


def test_reassign_keeps_auto_origin(session):
    n = _news(session, "x")
    e1 = event_pool.create_event(session, "E1", news_ids=[_news(session, "seed1").id])
    e2 = event_pool.create_event(session, "E2", news_ids=[_news(session, "seed2").id])
    link = ResearchEventLink(event_id=e1.id, news_id=n.id, link_source="auto",
                             auto_event_id=e1.id, confidence=0.9, prompt_version="link-v1")
    session.add(link); session.commit()
    event_pool.reassign_link(session, link.id, new_event_id=e2.id)
    session.refresh(link)
    assert (link.event_id, link.auto_event_id, link.link_source) == (e2.id, e1.id, "human")


def test_detach_flags_not_deletes(session):
    n = _news(session, "x")
    e = event_pool.create_event(session, "E", news_ids=[n.id])
    link = session.query(ResearchEventLink).filter_by(event_id=e.id).one()
    event_pool.detach_link(session, link.id, reason="挂错了")
    session.refresh(link)
    assert (link.detached, link.detach_reason) == (True, "挂错了")
    assert session.query(ResearchEventLink).count() == 1     # 不删行


def test_attach_news_revives_detached(session):
    n = _news(session, "x")
    e = event_pool.create_event(session, "E", news_ids=[n.id])
    link = session.query(ResearchEventLink).filter_by(event_id=e.id).one()
    event_pool.detach_link(session, link.id, reason="误摘")
    revived = event_pool.attach_news(session, e.id, n.id)
    assert revived.id == link.id and revived.detached is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_event_pool.py -v`
Expected: FAIL(ModuleNotFoundError: services.event_pool)

- [ ] **Step 3: 实现**(services/event_pool.py 新文件;本任务写到生命周期为止)

```python
# -*- coding: utf-8 -*-
"""研究事件池·生命周期与读取(news-research-phase1 spec §6-§10)。

全部写操作只走人工入口(API),模型无立案/关闭/重开/合并权。
人工挂接无视闸门与黑名单(spec §1)。留痕规则见 spec §6.2 表。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

import config
from models.news import NewsItem, NewsPriceAnnotation
from models.price import PriceSnapshot
from models.research import ResearchEvent, ResearchEventLink
from services.event_linking import (
    _is_blacklisted, _split_keywords, clear_link_cursor, passes_gate, _active_events, _keyword_pool,
)
from services.time_utils import bj_date_of, bj_day_bounds, utc_now_naive


def _get_event(session: Session, event_id: int) -> ResearchEvent:
    e = session.query(ResearchEvent).filter_by(id=event_id).first()
    if e is None:
        raise ValueError(f"事件 #{event_id} 不存在")
    return e


def create_event(session: Session, name: str, news_ids: list[int],
                 gate_keywords: str | None = None, created_from: str = "manual",
                 backscan_hours: float | None = None,
                 now: datetime | None = None) -> ResearchEvent:
    """立案(spec §6.1):仅人工,强制 ≥1 条新闻;出生即 active;自动回扫 72h。"""
    if not news_ids:
        raise ValueError("立案必须至少挂一条新闻")
    if created_from not in ("annotation", "manual"):
        raise ValueError(f"非法 created_from: {created_from!r}")
    found = {int(i) for (i,) in session.query(NewsItem.id)
             .filter(NewsItem.id.in_(news_ids)).all()}
    missing = {int(i) for i in news_ids} - found
    if missing:
        raise ValueError(f"新闻不存在: {sorted(missing)}")
    now = now or utc_now_naive()
    event = ResearchEvent(name=name, status="active",
                          gate_keywords=(gate_keywords or None),
                          created_from=created_from, status_changed_at=now)
    session.add(event)
    session.flush()
    for nid in news_ids:
        session.add(ResearchEventLink(event_id=event.id, news_id=int(nid),
                                      link_source="human"))
    session.commit()
    clear_link_cursor(session, backscan_hours or config.EVENT_BACKSCAN_DEFAULT_HOURS, now=now)
    return event


def rename_event(session: Session, event_id: int, name: str) -> ResearchEvent:
    e = _get_event(session, event_id)
    e.name = name
    session.commit()
    return e


def set_keywords(session: Session, event_id: int, gate_keywords: str | None,
                 backscan: bool = False) -> ResearchEvent:
    """改关键词只对之后的新闻自动生效;backscan=True 追溯最近 72h(spec §5.1)。"""
    e = _get_event(session, event_id)
    e.gate_keywords = gate_keywords or None
    session.commit()
    if backscan:
        clear_link_cursor(session, config.EVENT_BACKSCAN_DEFAULT_HOURS)
    return e


def close_event(session: Session, event_id: int, reason: str | None) -> ResearchEvent:
    e = _get_event(session, event_id)
    e.status = "closed"
    e.closed_reason = reason
    e.status_changed_at = utc_now_naive()
    session.commit()
    return e


def reopen_event(session: Session, event_id: int) -> ResearchEvent:
    """重开(spec §6.2/§7):closed→active,免闸恢复,自动回扫 72h。时间轴同一条。"""
    e = _get_event(session, event_id)
    e.status = "active"
    e.status_changed_at = utc_now_naive()
    session.commit()
    clear_link_cursor(session, config.EVENT_BACKSCAN_DEFAULT_HOURS)
    return e


def merge_event(session: Session, source_id: int, target_id: int) -> int:
    """合并 A→B(spec §6.2):未摘下挂接迁移(撞唯一索引跳过保 B 现有),关键词并入去重,
    A 关闭并记 merged_into;A 的已摘下记录留在 A(审计痕迹不迁移)。返回迁移条数。"""
    if source_id == target_id:
        raise ValueError("不能合并到自身")
    src = _get_event(session, source_id)
    dst = _get_event(session, target_id)
    dst_news = {row[0] for row in session.query(ResearchEventLink.news_id)
                .filter_by(event_id=target_id).all()}
    moved = 0
    for link in (session.query(ResearchEventLink)
                 .filter_by(event_id=source_id, detached=False).all()):
        if link.news_id in dst_news:
            continue
        link.event_id = target_id
        moved += 1
    merged_kw = _split_keywords(dst.gate_keywords)
    for k in _split_keywords(src.gate_keywords):
        if k not in merged_kw:
            merged_kw.append(k)
    dst.gate_keywords = "、".join(merged_kw) or None
    src.status = "closed"
    src.merged_into_id = target_id
    src.closed_reason = f"合并入 #{target_id}"
    src.status_changed_at = utc_now_naive()
    session.commit()
    return moved


def attach_news(session: Session, event_id: int, news_id: int) -> ResearchEventLink:
    """人工挂接:无视闸门/黑名单;同 (event,news) 已有记录则复活(detached→False)。"""
    _get_event(session, event_id)
    existing = (session.query(ResearchEventLink)
                .filter_by(event_id=event_id, news_id=news_id).first())
    if existing:
        existing.detached = False
        existing.detach_reason = None
        existing.link_source = "human"
        session.commit()
        return existing
    link = ResearchEventLink(event_id=event_id, news_id=news_id, link_source="human")
    session.add(link)
    session.commit()
    return link


def reassign_link(session: Session, link_id: int, new_event_id: int) -> ResearchEventLink:
    """改归属:auto_event_id 保模型原判,link_source 变 human(spec §6.2)。"""
    link = session.query(ResearchEventLink).filter_by(id=link_id).first()
    if link is None:
        raise ValueError(f"挂接 #{link_id} 不存在")
    _get_event(session, new_event_id)
    dup = (session.query(ResearchEventLink)
           .filter_by(event_id=new_event_id, news_id=link.news_id).first())
    if dup is not None and dup.id != link.id:
        raise ValueError(f"目标事件已有这条新闻的挂接(#{dup.id})")
    link.event_id = new_event_id
    link.link_source = "human"
    session.commit()
    return link


def detach_link(session: Session, link_id: int, reason: str | None) -> ResearchEventLink:
    """摘下=标记不删行(留痕,spec §6.2)。"""
    link = session.query(ResearchEventLink).filter_by(id=link_id).first()
    if link is None:
        raise ValueError(f"挂接 #{link_id} 不存在")
    link.detached = True
    link.detach_reason = reason
    link.link_source = "human"
    session.commit()
    return link
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_event_pool.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add services/event_pool.py tests/test_event_pool.py
git commit -m "feat(research): 事件生命周期:立案/关闭/重开/合并/改归属/摘下 (spec §6)"
```

---

### Task 7: 读取层:列表/时间轴/缓冲区/旧事重提/统计/日报文本

**Files:**
- Modify: `services/event_pool.py`(追加)
- Test: `tests/test_event_pool.py`(追加)

- [ ] **Step 1: 写失败测试**(追加)

```python
# ---- 读取层(spec §8-§10)----

def _annotation(s, news_id, symbol="BTC/USDT", change=1.8):
    a = NewsPriceAnnotation(symbol=symbol, window_start=datetime(2026, 8, 1, 10, 0),
                            window_end=datetime(2026, 8, 1, 10, 15),
                            context_start=datetime(2026, 8, 1, 9, 30),
                            context_end=datetime(2026, 8, 1, 10, 15),
                            change_pct=change,
                            news_roles=json.dumps({str(news_id): "driver"}))
    s.add(a); s.commit()
    return a


def test_list_events_sort_and_derived(session):
    early, late = (_news(session, "早", ts=datetime(2026, 7, 20, 8, 0)),
                   _news(session, "晚", ts=datetime(2026, 8, 1, 8, 0)))
    e1 = event_pool.create_event(session, "老事件", news_ids=[early.id])
    e2 = event_pool.create_event(session, "新事件", news_ids=[late.id])
    _annotation(session, late.id)
    rows = event_pool.list_events(session, now=datetime(2026, 8, 3, 8, 0))
    assert [r["id"] for r in rows] == [e2.id, e1.id]      # 最新证据倒序
    top = rows[0]
    assert top["evidence_count"] == 1
    assert top["badge_count"] == 1
    assert top["days_since_last"] == 2
    assert rows[1]["days_since_last"] == 14


def test_timeline_obs_badge_and_score_miss(session):
    from models.price import PriceSnapshot
    n = _news(session, "低分driver", score=3, ts=datetime(2026, 8, 1, 10, 2))
    for m, p in ((0, 100.0), (5, 102.0), (10, 103.0)):
        session.add(PriceSnapshot(timestamp=datetime(2026, 8, 1, 10, 0) + timedelta(minutes=m),
                                  asset_class="crypto", symbol="BTC/USDT", name="BTC",
                                  price=p, source="test"))
    session.commit()
    e = event_pool.create_event(session, "E", news_ids=[n.id])
    _annotation(session, n.id, change=1.8)
    tl = event_pool.event_timeline(session, e.id, now=datetime(2026, 8, 1, 11, 0))
    item = tl["items"][0]
    assert item["news"]["id"] == n.id
    assert item["obs"]["status"] == "ok" and abs(item["obs"]["net_pct"] - 3.0) < 1e-9
    assert item["driver_badge"] == {"symbol": "BTC/USDT", "change_pct": 1.8}
    assert item["score_miss"] is True                     # 3 分 < 闸门线且已挂(spec §8.3)
    assert item["link"]["link_source"] == "human"


def test_buffer_excludes_linked_and_junk(session):
    e = event_pool.create_event(session, "E", news_ids=[_news(session, "seed").id],
                                gate_keywords="苹果")
    now = datetime(2026, 8, 1, 13, 0)
    plain = _news(session, "过闸未挂", score=7, ts=datetime(2026, 8, 1, 12, 0))
    kw = _news(session, "苹果低分未挂", score=3, ts=datetime(2026, 8, 1, 12, 0))
    low = _news(session, "低分不命中", score=3, ts=datetime(2026, 8, 1, 12, 0))
    junk = _news(session, "金十数据整理：每日ETF", score=9, ts=datetime(2026, 8, 1, 12, 0))
    ids = {r["id"] for r in event_pool.buffer_news(session, days=3, now=now)}
    assert plain.id in ids and kw.id in ids
    assert low.id not in ids and junk.id not in ids
    seed_id = session.query(ResearchEventLink.news_id).filter_by(event_id=e.id).first()[0]
    assert seed_id not in ids                              # 已挂的不在缓冲区


def test_revival_matches_closed_event_keywords(session):
    e = event_pool.create_event(session, "苹果调价", news_ids=[_news(session, "seed").id],
                                gate_keywords="苹果、Apple")
    event_pool.close_event(session, e.id, reason="退潮")
    hit = _news(session, "苹果再次传出调价", score=3, ts=datetime(2026, 8, 1, 9, 0))
    _news(session, "无关", score=3, ts=datetime(2026, 8, 1, 9, 0))
    rows = event_pool.revival_matches(session, days=7, now=datetime(2026, 8, 1, 12, 0))
    assert [(r["news"]["id"], r["event_id"]) for r in rows] == [(hit.id, e.id)]


def test_daily_brief_text(session):
    now = datetime(2026, 8, 2, 0, 10)                      # 北京 08:10
    y = _news(session, "昨日证据", score=9, ts=datetime(2026, 8, 1, 6, 0))
    hot = _news(session, "昨日高分未挂", score=8, ts=datetime(2026, 8, 1, 7, 0))
    hot.event_linked_at = datetime(2026, 8, 1, 7, 5)
    e = event_pool.create_event(session, "事件A", news_ids=[y.id], now=datetime(2026, 8, 1, 8, 0))
    # create_event 的挂接 created_at=昨日北京日内 → 计入"昨日新增证据"
    title, content = event_pool.daily_brief_text(session, now=now)
    assert "事件池" in title
    assert "事件A" in content and "+1" in content
    assert "≥8 分未挂 1 条" in content

    title2, content2 = event_pool.daily_brief_text(
        session, now=datetime(2026, 9, 1, 0, 10))          # 无动静的一天
    assert "无动静" in content2
```

注意:`create_event` 里 link 的 `created_at` 默认 `datetime.utcnow`,测试跑在真实时间——`daily_brief_text` 的"昨日"按 **link.created_at** 落在昨日北京日内计数,上面第一个断言要求测试里把链接的 created_at 改到昨日:在 `create_event` 后补一句
```python
    for l in session.query(ResearchEventLink).filter_by(event_id=e.id).all():
        l.created_at = datetime(2026, 8, 1, 8, 0)
    session.commit()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_event_pool.py -v -k "list or timeline or buffer or revival or brief"`
Expected: FAIL(list_events 不存在)

- [ ] **Step 3: 实现**(services/event_pool.py 追加)

```python
# ---- 读取层(spec §8-§10):全部读时派生,不落库 ----
from services.news_service import to_news_schema
from services.theme_ledger import (
    OBS_BASELINE_TOLERANCE_MINUTES, observed_reaction_from_rows,
)


def _driver_badge_map(session: Session) -> dict[int, dict]:
    """确认层徽章(spec §8.2):news_id → {symbol, change_pct}(标注 news_roles 反查 driver)。"""
    out: dict[int, dict] = {}
    rows = (session.query(NewsPriceAnnotation)
            .filter(NewsPriceAnnotation.news_roles.isnot(None)).all())
    for a in rows:
        try:
            roles = json.loads(a.news_roles or "{}")
        except json.JSONDecodeError:
            continue
        for nid, role in (roles or {}).items():
            if role == "driver":
                out[int(nid)] = {"symbol": a.symbol, "change_pct": a.change_pct}
    return out


def _event_links(session: Session, event_id: int, include_detached: bool = False):
    q = (session.query(ResearchEventLink, NewsItem)
         .join(NewsItem, NewsItem.id == ResearchEventLink.news_id)
         .filter(ResearchEventLink.event_id == event_id))
    if not include_detached:
        q = q.filter(ResearchEventLink.detached.is_(False))
    return q.order_by(NewsItem.timestamp.desc()).all()


def list_events(session: Session, status: str | None = None, q: str | None = None,
                now: datetime | None = None) -> list[dict]:
    """事件列表(spec §9.1):最新证据倒序 + 派生徽章;搜索覆盖名称+关键词(含已关闭)。"""
    now = now or utc_now_naive()
    badge_map = _driver_badge_map(session)
    today_start, today_end = bj_day_bounds(bj_date_of(now))
    query = session.query(ResearchEvent)
    if status:
        query = query.filter(ResearchEvent.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter((ResearchEvent.name.like(like)) |
                             (ResearchEvent.gate_keywords.like(like)))
    out = []
    for e in query.all():
        rows = _event_links(session, e.id)
        last_ts = rows[0][1].timestamp if rows else None
        out.append({
            "id": e.id, "name": e.name, "status": e.status,
            "gate_keywords": e.gate_keywords, "created_from": e.created_from,
            "merged_into_id": e.merged_into_id, "closed_reason": e.closed_reason,
            "evidence_count": len(rows),
            "today_new": sum(1 for l, _ in rows
                             if l.created_at and today_start <= l.created_at < today_end),
            "badge_count": sum(1 for _, n in rows if int(n.id) in badge_map),
            "last_evidence_at": last_ts,
            "days_since_last": (now - last_ts).days if last_ts else None,
        })
    out.sort(key=lambda r: (r["last_evidence_at"] is None,
                            -(r["last_evidence_at"].timestamp() if r["last_evidence_at"] else 0)))
    return out


def event_timeline(session: Session, event_id: int, now: datetime | None = None) -> dict:
    """时间轴(spec §8):每条证据 = 新闻 + 观测值(现算)+ 确认徽章 + 评分失手 + 挂接留痕。"""
    now = now or utc_now_naive()
    e = _get_event(session, event_id)
    rows = _event_links(session, event_id)
    badge_map = _driver_badge_map(session)
    obs_symbol = config.EVENT_OBS_SYMBOLS[0]
    snaps: list[tuple[datetime, float]] = []
    if rows:
        times = [n.timestamp for _, n in rows]
        # spec §8.1:一次批量捞时间范围快照。跨年老事件此范围会变大,届时换 per-news 小查询即可。
        snaps = (session.query(PriceSnapshot.timestamp, PriceSnapshot.price)
                 .filter(PriceSnapshot.symbol == obs_symbol,
                         PriceSnapshot.timestamp >= min(times) - timedelta(minutes=OBS_BASELINE_TOLERANCE_MINUTES),
                         PriceSnapshot.timestamp <= max(times) + timedelta(minutes=config.EVENT_OBS_REACTION_MINUTES))
                 .order_by(PriceSnapshot.timestamp.asc()).all())
    items = []
    for link, n in rows:
        items.append({
            "news": to_news_schema(n).model_dump(),
            "obs": observed_reaction_from_rows(snaps, n.timestamp, now=now),
            "obs_symbol": obs_symbol,
            "driver_badge": badge_map.get(int(n.id)),
            "score_miss": (n.llm_importance is not None
                           and n.llm_importance < config.EVENT_LINK_MIN_IMPORTANCE),
            "link": {"id": link.id, "link_source": link.link_source,
                     "auto_event_id": link.auto_event_id, "confidence": link.confidence,
                     "prompt_version": link.prompt_version, "detached": link.detached},
        })
    pending_relink = (session.query(NewsItem)
                      .filter(NewsItem.tagged_at.isnot(None),
                              NewsItem.event_linked_at.is_(None)).count())
    return {"event": {"id": e.id, "name": e.name, "status": e.status,
                      "gate_keywords": e.gate_keywords, "created_from": e.created_from,
                      "closed_reason": e.closed_reason, "merged_into_id": e.merged_into_id},
            "items": items, "pending_relink": pending_relink}


def news_links(session: Session, news_id: int) -> list[dict]:
    """某条新闻挂在哪些事件上(标注页只读徽章用,spec §9.2)。"""
    rows = (session.query(ResearchEventLink, ResearchEvent)
            .join(ResearchEvent, ResearchEvent.id == ResearchEventLink.event_id)
            .filter(ResearchEventLink.news_id == news_id,
                    ResearchEventLink.detached.is_(False)).all())
    return [{"link_id": l.id, "event_id": e.id, "event_name": e.name,
             "event_status": e.status} for l, e in rows]


def buffer_news(session: Session, days: int = 3, min_score: int | None = None,
                q: str | None = None, drivers_only: bool = False,
                now: datetime | None = None, limit: int = 200) -> list[dict]:
    """缓冲区(spec §6.4):过闸 + 不在黑名单 + 无未摘下挂接。"""
    now = now or utc_now_naive()
    events = _active_events(session)
    keywords = _keyword_pool(events)
    linked = {row[0] for row in session.query(ResearchEventLink.news_id)
              .filter(ResearchEventLink.detached.is_(False)).all()}
    badge_map = _driver_badge_map(session) if drivers_only else {}
    query = (session.query(NewsItem)
             .filter(NewsItem.tagged_at.isnot(None),
                     NewsItem.timestamp >= now - timedelta(days=days))
             .order_by(NewsItem.timestamp.desc()))
    if min_score is not None:
        query = query.filter(NewsItem.llm_importance >= min_score)
    if q:
        query = query.filter(NewsItem.title.like(f"%{q}%"))
    out = []
    for n in query.all():
        if int(n.id) in linked:
            continue
        if _is_blacklisted(n) or not passes_gate(n, keywords):
            continue
        if drivers_only and int(n.id) not in badge_map:
            continue
        out.append(to_news_schema(n).model_dump())
        if len(out) >= limit:
            break
    return out


def revival_matches(session: Session, days: int = 7, now: datetime | None = None) -> list[dict]:
    """沉睡监听(spec §7):近 N 天新闻命中**已关闭**事件关键词;纯文本现算,零 LLM。"""
    now = now or utc_now_naive()
    closed = (session.query(ResearchEvent)
              .filter(ResearchEvent.status == "closed",
                      ResearchEvent.gate_keywords.isnot(None)).all())
    watchlist = [(e, [k.lower() for k in _split_keywords(e.gate_keywords)])
                 for e in closed]
    watchlist = [(e, ks) for e, ks in watchlist if ks]
    if not watchlist:
        return []
    out = []
    rows = (session.query(NewsItem)
            .filter(NewsItem.timestamp >= now - timedelta(days=days))
            .order_by(NewsItem.timestamp.desc()).all())
    for n in rows:
        if _is_blacklisted(n):
            continue
        text = f"{n.title or ''} {(n.content or '')[:200]}".lower()
        for e, ks in watchlist:
            if any(k in text for k in ks):
                out.append({"news": to_news_schema(n).model_dump(),
                            "event_id": e.id, "event_name": e.name})
                break
    return out


def daily_stats(session: Session, now: datetime | None = None) -> dict:
    """并行期观察数字(spec §9.1/§13.3):当日(北京日)挂接率/纠错率,近似口径。"""
    now = now or utc_now_naive()
    start, end = bj_day_bounds(bj_date_of(now))
    events = _active_events(session)
    keywords = _keyword_pool(events)
    processed = (session.query(NewsItem)
                 .filter(NewsItem.event_linked_at >= start,
                         NewsItem.event_linked_at < end).all())
    gated = [n for n in processed if not _is_blacklisted(n) and passes_gate(n, keywords)]
    auto_today = (session.query(ResearchEventLink)
                  .filter(ResearchEventLink.auto_event_id.isnot(None),
                          ResearchEventLink.created_at >= start,
                          ResearchEventLink.created_at < end).all())
    corrected = [l for l in auto_today if l.detached or l.event_id != l.auto_event_id]
    pending_relink = (session.query(NewsItem)
                      .filter(NewsItem.tagged_at.isnot(None),
                              NewsItem.event_linked_at.is_(None)).count())
    return {
        "gated_processed_today": len(gated),
        "auto_linked_today": len(auto_today),
        "link_rate": round(len(auto_today) / len(gated), 3) if gated else None,
        "corrected_today": len(corrected),
        "correction_rate": round(len(corrected) / len(auto_today), 3) if auto_today else None,
        "pending_relink": pending_relink,
    }


def daily_brief_text(session: Session, now: datetime | None = None) -> tuple[str, str]:
    """每日 WeCom 清单(spec §10):昨日北京日,纯查库拼文本。返回 (title, markdown)。"""
    now = now or utc_now_naive()
    y_date = (datetime.strptime(bj_date_of(now), "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    start, end = bj_day_bounds(y_date)
    badge_map = _driver_badge_map(session)
    lines: list[str] = []
    total_new = 0
    total_badge = 0
    for e in session.query(ResearchEvent).filter(ResearchEvent.status == "active").all():
        rows = [(l, n) for l, n in _event_links(session, e.id)
                if l.created_at and start <= l.created_at < end]
        if not rows:
            continue
        badges = sum(1 for _, n in rows if int(n.id) in badge_map)
        total_new += len(rows)
        total_badge += badges
        lines.append(f"- {e.name} +{len(rows)}" + (f"(徽章{badges})" if badges else ""))
    events_kw = _keyword_pool(_active_events(session))
    linked = {row[0] for row in session.query(ResearchEventLink.news_id)
              .filter(ResearchEventLink.detached.is_(False)).all()}
    hot = [n for n in session.query(NewsItem)
           .filter(NewsItem.timestamp >= start, NewsItem.timestamp < end,
                   NewsItem.llm_importance >= 8).all()
           if int(n.id) not in linked and not _is_blacklisted(n)]
    revival = [r for r in revival_matches(session, days=1, now=end)
               if start <= datetime.fromisoformat(r["news"]["timestamp_utc"]) < end]
    title = f"事件池日报 {y_date}"
    if not lines and not hot and not revival:
        return title, "事件池无动静"
    parts = []
    if lines:
        parts.append(f"进行中事件昨日新增证据 {total_new} 条(带确认徽章 {total_badge})")
        parts.extend(lines)
    parts.append(f"缓冲区昨日 ≥8 分未挂 {len(hot)} 条")
    for r in revival:
        parts.append(f"旧事重提:『{r['event_name']}』命中 {r['news']['title'][:40]}")
    return title, "\n".join(parts)
```

注意 `to_news_schema` 返回的 `timestamp_utc` 是 `isoformat(timespec="seconds")` 无 Z 后缀的 naive UTC(项目惯例),`datetime.fromisoformat` 可直接解析。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_event_pool.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add services/event_pool.py tests/test_event_pool.py
git commit -m "feat(research): 读取层:列表/时间轴/缓冲区/旧事重提/统计/日报文本 (spec §8-§10)"
```

---

### Task 8: AI 建议关键词

**Files:**
- Modify: `services/event_linking.py`(追加)
- Test: `tests/test_event_linking.py`(追加)

- [ ] **Step 1: 写失败测试**(追加)

```python
# ---- AI 建议关键词(spec §5.2)----

def test_suggest_keywords_parses_and_caps(session, monkeypatch):
    n = _news(session, "苹果供应链传出调价")
    monkeypatch.setattr(event_linking, "_call_keyword_suggester", lambda c: json.dumps(
        {"keywords": ["苹果", "Apple", "iPhone", "调价", "供应链", "库克", "第七个"]}))
    out = event_linking.suggest_keywords(session, "苹果调价", [n.id])
    assert out == ["苹果", "Apple", "iPhone", "调价", "供应链", "库克"]   # 截 6 个


def test_suggest_keywords_rejects_bad_json(session, monkeypatch):
    n = _news(session, "x")
    monkeypatch.setattr(event_linking, "_call_keyword_suggester", lambda c: "不是JSON")
    with pytest.raises(ValueError):
        event_linking.suggest_keywords(session, "e", [n.id])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_event_linking.py -v -k suggest`
Expected: FAIL

- [ ] **Step 3: 实现**(services/event_linking.py 追加)

```python
KEYWORD_SUGGEST_PROMPT = (
    "你是研究助理。给一个宏观研究事件起 3-6 个'免闸关键词',用于从新闻标题+摘要匹配该事件的后续报道。\n"
    "取词规则(spec §5.2):\n"
    "1. 实体词优先,中英别名都要(如:苹果、Apple、iPhone——中文源与英文源都要能命中);\n"
    "2. 每个词单独命中时应大概率与本事件相关('植田'行,'加息'不行——太泛);\n"
    "3. 禁单字与泛词('油''美股''关税'会让闸门虚设);\n"
    "4. 3-6 个。\n"
    '只返回 JSON:{"keywords": ["词1", "词2"]}'
)


def _call_keyword_suggester(user_content: str) -> str:
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": KEYWORD_SUGGEST_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 500,
        "temperature": 0,
    }
    result = call_deepseek_chat(
        payload, api_key=config.DEEPSEEK_API_KEY,
        timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, config.DEEPSEEK_READ_TIMEOUT),
        http_error_prefix="DeepSeek 关键词建议返回", error_preview_chars=200,
        normalize_error_newlines=False,
    )
    if not result.content:
        raise RuntimeError("DeepSeek 关键词建议返回空 content")
    return result.content


def suggest_keywords(session: Session, name: str, news_ids: list[int]) -> list[str]:
    """AI 建议关键词(spec §5.2):即用即弃不留痕;落库的永远是人确认后的版本。"""
    rows = session.query(NewsItem).filter(NewsItem.id.in_(news_ids)).all()
    items = [{"title": (n.title or "")[:160], "content": (n.content or "")[:200]} for n in rows]
    user = f"事件名:{name}\n种子新闻:\n{json.dumps(items, ensure_ascii=False)}"
    raw = _call_keyword_suggester(user).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError(f"关键词建议返回非 JSON: {raw[:100]}")
    kws = data.get("keywords") if isinstance(data, dict) else None
    if not isinstance(kws, list):
        raise ValueError("关键词建议缺少 keywords 列表")
    out = [str(k).strip() for k in kws if str(k).strip()]
    return out[:6]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_event_linking.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add services/event_linking.py tests/test_event_linking.py
git commit -m "feat(research): AI 建议关键词(人确认才落库) (spec §5.2)"
```

---

### Task 9: schemas + API 路由 + 前端类型再生成

**Files:**
- Create: `schemas/research.py`
- Modify: `api/routes.py`(文件末尾追加"研究事件池"段)
- Test: `tests/test_research_api.py`
- 再生成: `frontend/src/api/types.ts`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_research_api.py
# -*- coding: utf-8 -*-
"""研究事件池 API(news-research-phase1 spec §9.3)。参照 tests/test_api.py 的 TestClient 模式。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # 与 tests/test_api.py 相同方式:临时库 + 无调度;若 test_api.py 已有等价 fixture,直接复用其写法
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    for mod in list(sys.modules):
        if mod in ("database", "config") or mod.startswith(("models", "api", "services", "schemas", "alerts")):
            sys.modules.pop(mod)
    from api.app import create_app
    return TestClient(create_app(enable_scheduler=False))


def _mk_news(client_unused, title="种子新闻"):
    from database import SessionLocal
    from models.news import NewsItem
    s = SessionLocal()
    n = NewsItem(timestamp=datetime(2026, 8, 1, 12, 0), source="jin10", title=title,
                 language="zh", llm_importance=8, tagged_at=datetime(2026, 8, 1, 12, 1))
    s.add(n); s.commit(); nid = n.id; s.close()
    return nid


def test_create_list_timeline_roundtrip(client):
    nid = _mk_news(client)
    r = client.post("/api/research/events", json={
        "name": "苹果调价", "news_ids": [nid], "gate_keywords": "苹果、Apple",
        "created_from": "manual"})
    assert r.status_code == 200, r.text
    eid = r.json()["id"]
    rows = client.get("/api/research/events").json()["items"]
    assert rows[0]["id"] == eid and rows[0]["evidence_count"] == 1
    tl = client.get(f"/api/research/events/{eid}/timeline").json()
    assert tl["event"]["name"] == "苹果调价"
    assert tl["items"][0]["news"]["id"] == nid


def test_create_requires_news(client):
    r = client.post("/api/research/events", json={"name": "空壳", "news_ids": []})
    assert r.status_code == 400


def test_patch_close_reopen_merge(client):
    n1, n2 = _mk_news(client, "a"), _mk_news(client, "b")
    e1 = client.post("/api/research/events", json={"name": "A", "news_ids": [n1]}).json()["id"]
    e2 = client.post("/api/research/events", json={"name": "B", "news_ids": [n2]}).json()["id"]
    assert client.patch(f"/api/research/events/{e1}",
                        json={"status": "closed", "closed_reason": "测试关闭"}).status_code == 200
    assert client.patch(f"/api/research/events/{e1}",
                        json={"status": "active"}).status_code == 200
    r = client.patch(f"/api/research/events/{e1}", json={"merge_into_id": e2})
    assert r.status_code == 200
    rows = {x["id"]: x for x in client.get("/api/research/events?status=closed").json()["items"]}
    assert rows[e1]["merged_into_id"] == e2


def test_links_attach_detach(client):
    nid = _mk_news(client)
    n2 = _mk_news(client, "第二条")
    eid = client.post("/api/research/events", json={"name": "E", "news_ids": [nid]}).json()["id"]
    r = client.post("/api/research/links", json={"event_id": eid, "news_id": n2})
    assert r.status_code == 200
    link_id = r.json()["id"]
    assert client.patch(f"/api/research/links/{link_id}",
                        json={"detached": True, "detach_reason": "挂错"}).status_code == 200
    briefs = client.get(f"/api/research/news/{n2}/links").json()["items"]
    assert briefs == []                                   # 摘下后不再显示


def test_buffer_revival_stats_endpoints(client):
    _mk_news(client)
    assert client.get("/api/research/buffer?days=3").status_code == 200
    assert client.get("/api/research/revival").status_code == 200
    stats = client.get("/api/research/stats").json()
    assert "link_rate" in stats and "correction_rate" in stats
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_research_api.py -v`
Expected: FAIL(404)

- [ ] **Step 3: 实现**

`schemas/research.py`(新文件,全文):

```python
from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.news import NewsItemSchema


class ResearchEventItem(BaseModel):
    id: int
    name: str
    status: str
    gate_keywords: str | None = None
    created_from: str
    merged_into_id: int | None = None
    closed_reason: str | None = None
    evidence_count: int = 0
    today_new: int = 0
    badge_count: int = 0
    days_since_last: int | None = None
    last_evidence_at: str | None = None      # naive UTC isoformat(项目惯例,无 Z)


class ResearchEventsResponse(BaseModel):
    items: list[ResearchEventItem] = Field(default_factory=list)


class ResearchEventCreateRequest(BaseModel):
    name: str
    news_ids: list[int] = Field(default_factory=list)
    gate_keywords: str | None = None
    created_from: str = "manual"             # annotation / manual


class ResearchEventPatchRequest(BaseModel):
    """改名/关键词/关闭/重开/合并,一个 PATCH 承载(spec §9.3);全部可选,传了才动。"""
    name: str | None = None
    gate_keywords: str | None = None
    keywords_backscan: bool = False          # 改关键词时勾选:追溯回扫 72h(spec §5.1)
    status: str | None = None                # "closed"(带 closed_reason)/ "active"(重开)
    closed_reason: str | None = None
    merge_into_id: int | None = None


class SuggestKeywordsRequest(BaseModel):
    name: str
    news_ids: list[int] = Field(default_factory=list)


class SuggestKeywordsResponse(BaseModel):
    keywords: list[str] = Field(default_factory=list)


class BackscanRequest(BaseModel):
    days: float = 3.0


class BackscanResponse(BaseModel):
    cleared: int


class ObsResult(BaseModel):
    status: str                              # pending / no_data / ok
    net_pct: float | None = None
    actual_minutes: float | None = None
    start: float | None = None
    end: float | None = None


class DriverBadge(BaseModel):
    symbol: str
    change_pct: float | None = None


class LinkBrief(BaseModel):
    id: int
    link_source: str
    auto_event_id: int | None = None
    confidence: float | None = None
    prompt_version: str | None = None
    detached: bool = False


class TimelineItem(BaseModel):
    news: NewsItemSchema
    obs: ObsResult
    obs_symbol: str
    driver_badge: DriverBadge | None = None
    score_miss: bool = False
    link: LinkBrief


class TimelineEventHead(BaseModel):
    id: int
    name: str
    status: str
    gate_keywords: str | None = None
    created_from: str
    closed_reason: str | None = None
    merged_into_id: int | None = None


class TimelineResponse(BaseModel):
    event: TimelineEventHead
    items: list[TimelineItem] = Field(default_factory=list)
    pending_relink: int = 0                  # >0 → 前端显示"回扫进行中(剩 N 条)"


class LinkCreateRequest(BaseModel):
    event_id: int
    news_id: int


class LinkPatchRequest(BaseModel):
    event_id: int | None = None              # 传了 = 改归属
    detached: bool | None = None             # True = 摘下
    detach_reason: str | None = None


class LinkResponse(BaseModel):
    id: int
    event_id: int
    news_id: int
    link_source: str
    detached: bool


class NewsLinksResponse(BaseModel):
    items: list[dict] = Field(default_factory=list)   # {link_id, event_id, event_name, event_status}


class BufferResponse(BaseModel):
    items: list[NewsItemSchema] = Field(default_factory=list)


class RevivalItem(BaseModel):
    news: NewsItemSchema
    event_id: int
    event_name: str


class RevivalResponse(BaseModel):
    items: list[RevivalItem] = Field(default_factory=list)


class ResearchStats(BaseModel):
    gated_processed_today: int = 0
    auto_linked_today: int = 0
    link_rate: float | None = None
    corrected_today: int = 0
    correction_rate: float | None = None
    pending_relink: int = 0
```

`api/routes.py` 末尾追加(imports 区补 `from schemas.research import ...` 全部类名、`from services import event_pool` 与 `from services import event_linking`):

```python
# ============================================================
# 研究事件池(docs/specs/news-research-phase1-event-pool.md §9.3)
# ============================================================

@router.get("/research/events", response_model=ResearchEventsResponse)
def research_events_list(status: str | None = Query(default=None),
                         q: str | None = Query(default=None),
                         db: Session = Depends(get_db)) -> ResearchEventsResponse:
    rows = event_pool.list_events(db, status=status, q=q)
    for r in rows:
        ts = r.pop("last_evidence_at")
        r["last_evidence_at"] = ts.isoformat(timespec="seconds") if ts else None
    return ResearchEventsResponse(items=[ResearchEventItem(**r) for r in rows])


@router.post("/research/events", response_model=ResearchEventItem)
def research_event_create(request: ResearchEventCreateRequest,
                          db: Session = Depends(get_db)) -> ResearchEventItem:
    try:
        e = event_pool.create_event(db, request.name, request.news_ids,
                                    gate_keywords=request.gate_keywords,
                                    created_from=request.created_from)
    except ValueError as exc:
        raise ApiError("INVALID_EVENT", str(exc), status_code=400) from exc
    row = next(r for r in event_pool.list_events(db) if r["id"] == e.id)
    ts = row.pop("last_evidence_at")
    row["last_evidence_at"] = ts.isoformat(timespec="seconds") if ts else None
    return ResearchEventItem(**row)


@router.patch("/research/events/{event_id}", response_model=ResearchEventItem)
def research_event_patch(event_id: int, request: ResearchEventPatchRequest,
                         db: Session = Depends(get_db)) -> ResearchEventItem:
    try:
        if request.name is not None:
            event_pool.rename_event(db, event_id, request.name)
        if request.gate_keywords is not None:
            event_pool.set_keywords(db, event_id, request.gate_keywords,
                                    backscan=request.keywords_backscan)
        if request.merge_into_id is not None:
            event_pool.merge_event(db, source_id=event_id, target_id=request.merge_into_id)
        elif request.status == "closed":
            event_pool.close_event(db, event_id, reason=request.closed_reason)
        elif request.status == "active":
            event_pool.reopen_event(db, event_id)
    except ValueError as exc:
        raise ApiError("INVALID_EVENT_OP", str(exc), status_code=400) from exc
    row = next((r for r in event_pool.list_events(db) if r["id"] == event_id), None)
    if row is None:
        raise ApiError("NOT_FOUND", f"事件 #{event_id} 不存在", status_code=404)
    ts = row.pop("last_evidence_at")
    row["last_evidence_at"] = ts.isoformat(timespec="seconds") if ts else None
    return ResearchEventItem(**row)


@router.post("/research/events/suggest-keywords", response_model=SuggestKeywordsResponse)
def research_suggest_keywords(request: SuggestKeywordsRequest,
                              db: Session = Depends(get_db)) -> SuggestKeywordsResponse:
    try:
        kws = event_linking.suggest_keywords(db, request.name, request.news_ids)
    except (ValueError, RuntimeError) as exc:
        raise ApiError("SUGGEST_FAILED", str(exc), status_code=400) from exc
    return SuggestKeywordsResponse(keywords=kws)


@router.post("/research/events/{event_id}/backscan", response_model=BackscanResponse)
def research_event_backscan(event_id: int, request: BackscanRequest,
                            db: Session = Depends(get_db)) -> BackscanResponse:
    # event_id 仅作语义定位(回扫是全池行为,spec §6.3);校验事件存在即可
    try:
        event_pool._get_event(db, event_id)
    except ValueError as exc:
        raise ApiError("NOT_FOUND", str(exc), status_code=404) from exc
    cleared = event_linking.clear_link_cursor(db, hours=request.days * 24)
    return BackscanResponse(cleared=cleared)


@router.get("/research/events/{event_id}/timeline", response_model=TimelineResponse)
def research_event_timeline(event_id: int, db: Session = Depends(get_db)) -> TimelineResponse:
    try:
        data = event_pool.event_timeline(db, event_id)
    except ValueError as exc:
        raise ApiError("NOT_FOUND", str(exc), status_code=404) from exc
    return TimelineResponse(**data)


@router.post("/research/links", response_model=LinkResponse)
def research_link_create(request: LinkCreateRequest, db: Session = Depends(get_db)) -> LinkResponse:
    try:
        link = event_pool.attach_news(db, request.event_id, request.news_id)
    except ValueError as exc:
        raise ApiError("INVALID_LINK", str(exc), status_code=400) from exc
    return LinkResponse(id=link.id, event_id=link.event_id, news_id=link.news_id,
                        link_source=link.link_source, detached=link.detached)


@router.patch("/research/links/{link_id}", response_model=LinkResponse)
def research_link_patch(link_id: int, request: LinkPatchRequest,
                        db: Session = Depends(get_db)) -> LinkResponse:
    try:
        if request.event_id is not None:
            link = event_pool.reassign_link(db, link_id, request.event_id)
        elif request.detached:
            link = event_pool.detach_link(db, link_id, request.detach_reason)
        else:
            raise ValueError("PATCH 必须传 event_id(改归属)或 detached=true(摘下)")
    except ValueError as exc:
        raise ApiError("INVALID_LINK_OP", str(exc), status_code=400) from exc
    return LinkResponse(id=link.id, event_id=link.event_id, news_id=link.news_id,
                        link_source=link.link_source, detached=link.detached)


@router.get("/research/news/{news_id}/links", response_model=NewsLinksResponse)
def research_news_links(news_id: int, db: Session = Depends(get_db)) -> NewsLinksResponse:
    return NewsLinksResponse(items=event_pool.news_links(db, news_id))


@router.get("/research/buffer", response_model=BufferResponse)
def research_buffer(days: int = Query(default=3, ge=1, le=30),
                    min_score: int | None = Query(default=None),
                    q: str | None = Query(default=None),
                    drivers_only: bool = Query(default=False),
                    db: Session = Depends(get_db)) -> BufferResponse:
    return BufferResponse(items=event_pool.buffer_news(
        db, days=days, min_score=min_score, q=q, drivers_only=drivers_only))


@router.get("/research/revival", response_model=RevivalResponse)
def research_revival(days: int = Query(default=7, ge=1, le=30),
                     db: Session = Depends(get_db)) -> RevivalResponse:
    return RevivalResponse(items=event_pool.revival_matches(db, days=days))


@router.get("/research/stats", response_model=ResearchStats)
def research_stats(db: Session = Depends(get_db)) -> ResearchStats:
    return ResearchStats(**event_pool.daily_stats(db))
```

注意:`event_timeline` 返回的 `items[].news` 是 `model_dump()` 后的 dict,`TimelineItem.news: NewsItemSchema` 会重新校验——保持一致即可。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_research_api.py tests/test_api.py -v`
Expected: all passed

- [ ] **Step 5: 再生成前端类型并验证同步**

```bash
D:/anaconda/python.exe scripts/generate_openapi_types.py
D:/anaconda/python.exe -m pytest tests/test_openapi_types.py -q
```
Expected: passed;`git diff frontend/src/api/types.ts` 应出现 ResearchEventItem 等新类型

- [ ] **Step 6: Commit**

```bash
git add schemas/research.py api/routes.py tests/test_research_api.py frontend/src/api/types.ts
git commit -m "feat(research): 事件池 API 全套 + 前端类型再生成 (spec §9.3)"
```

---

### Task 10: 数据保留:清理保护扩展 + 永久保留配置

**Files:**
- Modify: `services/data_retention.py`(`_annotation_news_ids` 并入事件挂接)
- Modify: `config.py`(DATA_RETENTION 两键改 None)
- Test: `tests/test_data_retention.py`(追加)

- [ ] **Step 1: 写失败测试**(追加到 tests/test_data_retention.py)

```python
# ---- 研究事件池引用保护 + 永久保留(news-research-phase1 spec §12)----
from models.research import ResearchEvent, ResearchEventLink


def test_event_linked_news_survives_cleanup():
    session = _session()
    try:
        old_ts = NOW - timedelta(days=120)
        kept = _news(session, "被事件引用", old_ts)
        kept_detached = _news(session, "被摘下但留痕", old_ts)
        gone = _news(session, "无引用", old_ts)
        e = ResearchEvent(name="E")
        session.add(e); session.flush()
        session.add(ResearchEventLink(event_id=e.id, news_id=kept.id, link_source="human"))
        session.add(ResearchEventLink(event_id=e.id, news_id=kept_detached.id,
                                      link_source="auto", auto_event_id=e.id, detached=True))
        session.flush()
        deleted = cleanup_retained_data(session=session, now=NOW, retention=RETENTION)
        titles = {t for (t,) in session.query(NewsItem.title).all()}
        assert "被事件引用" in titles
        assert "被摘下但留痕" in titles          # 含 detached(审计痕迹也保,spec §12)
        assert "无引用" not in titles
        assert deleted["news_items"] == 1
    finally:
        session.close()


def test_none_retention_skips_table():
    session = _session()
    try:
        _news(session, "很老但永久保留", NOW - timedelta(days=1000))
        r = dict(RETENTION); r["news_items_days"] = None; r["price_snapshots_days"] = None
        deleted = cleanup_retained_data(session=session, now=NOW, retention=r)
        assert deleted["news_items"] == 0 and deleted["price_snapshots"] == 0
    finally:
        session.close()


def test_config_default_retention_is_permanent_for_news_and_prices():
    import config
    assert config.DATA_RETENTION["news_items_days"] is None
    assert config.DATA_RETENTION["price_snapshots_days"] is None
    assert config.DATA_RETENTION["prediction_markets_days"] == 30    # 不动
    assert config.DATA_RETENTION["alert_logs_days"] == 90            # 不动
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_data_retention.py -v`
Expected: 新增 3 个 FAIL(保护未扩展 / config 还是 90)

- [ ] **Step 3: 实现**

services/data_retention.py:头部 import 加 `from models.research import ResearchEventLink`;`_annotation_news_ids` 改为:

```python
def _annotation_news_ids(session) -> set[int]:
    """标注 + 研究事件时间轴引用的新闻都受保护(含已摘下——审计痕迹也要保,
    news-research-phase1 spec §12:防未来重新开启清理时静默斩断时间轴)。"""
    protected: set[int] = set()
    rows = session.query(
        NewsPriceAnnotation.causal_news_ids,
        NewsPriceAnnotation.candidate_news_ids,
        NewsPriceAnnotation.news_roles,
    ).all()
    for causal_ids, candidate_ids, news_roles in rows:
        protected.update(_parse_news_ids(causal_ids))
        protected.update(_parse_news_ids(candidate_ids))
        protected.update(_parse_news_ids(news_roles))
    protected.update(nid for (nid,) in session.query(ResearchEventLink.news_id).all())
    return protected
```

config.py 的 DATA_RETENTION 改为(注释一并更新):

```python
DATA_RETENTION = {
    # 2026-08-02 用户拍板(news-research-phase1 spec §12):价格快照与新闻**永久保留**
    # (None=永不清理)。年增约 0.7-1GB 不值得为省磁盘毁掉事件池历史;观测层因此
    # 维持读时现算。预测市场快照(全库最大表,~2.4万行/天)与告警日志和事件历史
    # 无关,维持原值。清理代码对 None 的跳过守卫见 services/data_retention.py::_cutoff。
    "price_snapshots_days": None,   # 永久(2026-07-09 曾拍 90,已被 08-02 决定取代)
    "news_items_days": None,        # 永久
    "prediction_markets_days": 30,
    "alert_logs_days": 90,
}
```

(`_cutoff` 已有 `days is None → None` 守卫,四个 `_delete_*` 对 None cutoff 均返回 0,无需改删除代码——测试会证实。)

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_data_retention.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add services/data_retention.py config.py tests/test_data_retention.py
git commit -m "feat(research): 时间轴引用清理保护 + 价格/新闻永久保留 (spec §12)"
```

---

### Task 11: WeCom 日报调度(北京 08:10)

**Files:**
- Modify: `api/app.py`(新 job + 注册)
- Test: `tests/test_api.py`(扩展调度注册断言)

- [ ] **Step 1: 写失败测试**

找到 `tests/test_api.py::test_scheduler_registers_operational_jobs`,在期望的 job id 集合断言里加 `"research_daily_brief"`(该测试用 FakeScheduler 收集 add_job 的 kwargs["id"];按现有断言风格追加)。

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_api.py -v -k scheduler`
Expected: FAIL(缺 research_daily_brief)

- [ ] **Step 3: 实现**(api/app.py `_start_background_scheduler` 内,`cmc_refresh` 定义后加函数,注册区加 job)

```python
    def research_daily_brief() -> None:
        """北京 08:10 事件池日报(news-research-phase1 spec §10):纯查库拼文本,零 LLM。"""
        try:
            from database import SessionLocal
            from services.event_pool import daily_brief_text
            from alerts.channels.wechat_work import WeChatWorkChannel

            session = SessionLocal()
            try:
                title, content = daily_brief_text(session)
            finally:
                session.close()
            WeChatWorkChannel().send(title, content)
        except Exception as exc:
            logger.exception("[FastAPI Scheduler] research_daily_brief failed: {}", exc)
```

注册(behavior_daily_summary 的 add_job 之后):

```python
    # 事件池日报:北京 08:10 = UTC 00:10,紧跟 08:05 行为日报(spec §10)。
    scheduler.add_job(
        research_daily_brief,
        CronTrigger(hour=0, minute=10),
        id="research_daily_brief",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_api.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add api/app.py tests/test_api.py
git commit -m "feat(research): 每日 WeCom 事件池清单 job(北京 08:10) (spec §10)"
```

---

### Task 12: 前端:client 方法 + 研究页(列表/详情)+ 导航

**Files:**
- Modify: `frontend/src/api/client.ts`(api 对象追加 research 方法)
- Create: `frontend/src/pages/ResearchPage.tsx`
- Modify: `frontend/src/components/AppShell.tsx`(navItems 加一项)
- Modify: `frontend/src/main.tsx`(路由;照 /behavior 现有写法加 /research)
- Test: `frontend/src/pages/ResearchPage.test.tsx`

- [ ] **Step 1: client.ts 追加**(import 区补新类型名,api 对象追加;类型名以 Task 9 生成的 types.ts 为准)

```typescript
  // ---- 研究事件池(spec §9.3)----
  researchEvents: (params: { status?: string; q?: string } = {}) =>
    request<ResearchEventsResponse>(`/research/events${buildQuery(params)}`),
  researchEventCreate: (body: ResearchEventCreateRequest) =>
    request<ResearchEventItem>("/research/events", { method: "POST", body: JSON.stringify(body) }),
  researchEventPatch: (id: number, body: ResearchEventPatchRequest) =>
    request<ResearchEventItem>(`/research/events/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  researchSuggestKeywords: (body: SuggestKeywordsRequest) =>
    request<SuggestKeywordsResponse>("/research/events/suggest-keywords", { method: "POST", body: JSON.stringify(body) }),
  researchBackscan: (id: number, days: number) =>
    request<BackscanResponse>(`/research/events/${id}/backscan`, { method: "POST", body: JSON.stringify({ days }) }),
  researchTimeline: (id: number) =>
    request<TimelineResponse>(`/research/events/${id}/timeline`),
  researchLinkCreate: (body: LinkCreateRequest) =>
    request<LinkResponse>("/research/links", { method: "POST", body: JSON.stringify(body) }),
  researchLinkPatch: (id: number, body: LinkPatchRequest) =>
    request<LinkResponse>(`/research/links/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  researchNewsLinks: (newsId: number) =>
    request<NewsLinksResponse>(`/research/news/${newsId}/links`),
  researchBuffer: (params: { days?: number; min_score?: number; q?: string; drivers_only?: boolean } = {}) =>
    request<BufferResponse>(`/research/buffer${buildQuery(params)}`),
  researchRevival: () => request<RevivalResponse>("/research/revival"),
  researchStats: () => request<ResearchStats>("/research/stats"),
```

- [ ] **Step 2: ResearchPage.tsx**(新文件;工作台模式,与 AnnotationsPage 同风格。本任务实现列表+详情两块,缓冲区/旧事重提下一任务补)

```tsx
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderSearch, RotateCcw, Sparkles } from "lucide-react";
import { api } from "../api/client";
import type { ResearchEventItem, TimelineResponse } from "../api/types";
import { Button, PageHeader } from "../components/Controls";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";

// 观测值 chip:pending=计算中 / no_data=— / ok=实际分钟数+净变动(spec §8.1)
function ObsChip({ obs }: { obs: TimelineResponse["items"][number]["obs"] }) {
  if (obs.status === "pending") return <span className="s-badge weak">计算中</span>;
  if (obs.status !== "ok" || obs.net_pct == null) return <span className="s-badge none">—</span>;
  const cls = obs.net_pct >= 0 ? "up-text" : "down-text";
  return (
    <span className={`s-badge ${cls}`} title={`基线→终点实际跨度 ${obs.actual_minutes} 分钟`}>
      {obs.actual_minutes}min {obs.net_pct >= 0 ? "+" : ""}{obs.net_pct.toFixed(2)}%
    </span>
  );
}

function EventDetail({ eventId, onChanged }: { eventId: number; onChanged: () => void }) {
  const qc = useQueryClient();
  const timeline = useQuery({ queryKey: ["research-timeline", eventId],
                              queryFn: () => api.researchTimeline(eventId) });
  const events = useQuery({ queryKey: ["research-events", "active"],
                            queryFn: () => api.researchEvents({ status: "active" }) });
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["research-timeline", eventId] });
    onChanged();
  };
  const patchLink = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Parameters<typeof api.researchLinkPatch>[1] }) =>
      api.researchLinkPatch(id, body),
    onSuccess: invalidate,
  });
  const patchEvent = useMutation({
    mutationFn: (body: Parameters<typeof api.researchEventPatch>[1]) =>
      api.researchEventPatch(eventId, body),
    onSuccess: invalidate,
  });
  const backscan = useMutation({
    mutationFn: (days: number) => api.researchBackscan(eventId, days),
    onSuccess: invalidate,
  });

  if (timeline.isLoading) return <LoadingState label="加载时间轴" />;
  if (timeline.isError || !timeline.data) return <ErrorState error={timeline.error} />;
  const { event, items, pending_relink } = timeline.data;
  const activeOptions = (events.data?.items ?? []).filter((e) => e.id !== eventId);

  return (
    <div className="subsection">
      <div className="subsection-head" style={{ gap: 8, flexWrap: "wrap" }}>
        <span className="subsection-title">{event.name}</span>
        <span className="s-badge none">{event.status === "active" ? "进行中" : "已关闭"}</span>
        {pending_relink > 0 && <span className="s-badge mid">回扫进行中(剩 {pending_relink} 条)</span>}
        <Button kind="secondary" onClick={() => {
          const name = window.prompt("改名", event.name);
          if (name) patchEvent.mutate({ name });
        }}>改名</Button>
        <Button kind="secondary" onClick={() => {
          const kw = window.prompt("免闸关键词(顿号分隔;每个词单独命中都应与本事件相关)",
                                   event.gate_keywords ?? "");
          if (kw !== null) patchEvent.mutate({ gate_keywords: kw, keywords_backscan: true });
        }}>关键词</Button>
        {event.status === "active" ? (
          <Button kind="secondary" onClick={() => {
            const reason = window.prompt("关闭原因", "");
            if (reason !== null) patchEvent.mutate({ status: "closed", closed_reason: reason });
          }}>关闭</Button>
        ) : (
          <Button kind="secondary" onClick={() => patchEvent.mutate({ status: "active" })}>重开</Button>
        )}
        <select title="合并到…" value="" onChange={(e) => {
          const target = Number(e.target.value);
          if (target && window.confirm(`把「${event.name}」合并入 #${target}?`))
            patchEvent.mutate({ merge_into_id: target });
        }}>
          <option value="">合并到…</option>
          {activeOptions.map((o) => <option key={o.id} value={o.id}>#{o.id} {o.name}</option>)}
        </select>
        <Button kind="secondary" onClick={() => {
          const days = Number(window.prompt("深回扫天数", "14"));
          if (days > 0) backscan.mutate(days);
        }}><RotateCcw size={14} />深回扫</Button>
      </div>
      {items.length === 0 && <EmptyState label="时间轴暂无证据" />}
      {items.map((it) => (
        <div key={it.link.id} className="evidence-row" style={{ alignItems: "center", gap: 6 }}>
          <span style={{ whiteSpace: "nowrap" }}>{it.news.timestamp_bj?.slice(5, 16)}</span>
          <span className="ref-neutral">{it.news.source}</span>
          {it.news.news_direction && (
            <span className={it.news.news_direction === "利多" ? "up-text"
                             : it.news.news_direction === "利空" ? "down-text" : "ref-neutral"}>
              {it.news.news_direction}
            </span>
          )}
          <ObsChip obs={it.obs} />
          {it.driver_badge && (
            <span className="s-badge strong"
                  title={`人工确认为标注窗口 driver(${it.driver_badge.symbol})`}>
              driver {it.driver_badge.change_pct != null
                ? `${it.driver_badge.change_pct > 0 ? "+" : ""}${it.driver_badge.change_pct.toFixed(2)}%` : ""}
            </span>
          )}
          {it.score_miss && (
            <span className="s-badge mid" title="llm_importance 低于闸门线却被确认挂上——打分校准素材(spec §8.3)">
              评分失手 {it.news.llm_importance}分
            </span>
          )}
          {it.link.link_source === "auto" && <span className="ref-neutral" title={`模型挂接 conf=${it.link.confidence}`}>auto</span>}
          <span style={{ flex: 1 }}>{it.news.title}</span>
          <select title="改归属" value="" onChange={(e) => {
            const target = Number(e.target.value);
            if (target) patchLink.mutate({ id: it.link.id, body: { event_id: target } });
          }}>
            <option value="">改归属…</option>
            {activeOptions.map((o) => <option key={o.id} value={o.id}>#{o.id} {o.name}</option>)}
          </select>
          <Button kind="secondary" onClick={() => {
            const reason = window.prompt("摘下原因", "");
            if (reason !== null) patchLink.mutate({ id: it.link.id, body: { detached: true, detach_reason: reason } });
          }}>摘下</Button>
        </div>
      ))}
    </div>
  );
}

export function ResearchPage() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<number | null>(null);
  const [q, setQ] = useState("");
  const events = useQuery({ queryKey: ["research-events", "all", q],
                            queryFn: () => api.researchEvents(q ? { q } : {}) });
  const stats = useQuery({ queryKey: ["research-stats"], queryFn: api.researchStats,
                           refetchInterval: 60_000 });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["research-events"] });

  const rows = events.data?.items ?? [];
  const active = rows.filter((r) => r.status === "active");
  const closed = rows.filter((r) => r.status === "closed");

  return (
    <section>
      <PageHeader title="研究" />
      {stats.data && (
        <div className="panel" style={{ display: "flex", gap: 16, padding: 8 }}>
          <span title="过闸新闻里模型挂上的占比(并行期观察,spec §13.3)">
            挂接率 {stats.data.link_rate != null ? `${(stats.data.link_rate * 100).toFixed(0)}%` : "—"}
          </span>
          <span title="模型挂的里被人工改归属/摘下的占比,连续3天<20%才删旧 topic 槽位">
            纠错率 {stats.data.correction_rate != null ? `${(stats.data.correction_rate * 100).toFixed(0)}%` : "—"}
          </span>
        </div>
      )}
      <div className="panel">
        <div className="panel-head">
          <h2><FolderSearch size={16} /> 进行中({active.length})</h2>
          <input placeholder="搜事件名/关键词(含已关闭)" value={q}
                 onChange={(e) => setQ(e.target.value)} />
        </div>
        {events.isLoading && <LoadingState label="加载事件" />}
        {events.isError && <ErrorState error={events.error} />}
        {active.map((e) => (
          <div key={e.id}
               className={`evidence-row${selected === e.id ? " self" : ""}`}
               style={{ cursor: "pointer" }}
               onClick={() => setSelected(selected === e.id ? null : e.id)}>
            <span style={{ flex: 1 }}>#{e.id} {e.name}</span>
            <span className="ref-neutral">证据 {e.evidence_count}</span>
            {e.today_new > 0 && <span className="up-text">今日 +{e.today_new}</span>}
            {e.badge_count > 0 && <span className="s-badge strong">徽章 {e.badge_count}</span>}
            {e.days_since_last != null && e.days_since_last >= 3 && (
              <span className="ref-neutral">{e.days_since_last} 天无新证据</span>
            )}
          </div>
        ))}
        {selected != null && <EventDetail eventId={selected} onChanged={refresh} />}
        {closed.length > 0 && (
          <details>
            <summary>已关闭({closed.length})</summary>
            {closed.map((e) => (
              <div key={e.id} className="evidence-row" style={{ cursor: "pointer" }}
                   onClick={() => setSelected(selected === e.id ? null : e.id)}>
                <span style={{ flex: 1 }}>#{e.id} {e.name}</span>
                <span className="ref-neutral">{e.closed_reason ?? ""}</span>
                {e.merged_into_id != null && <span className="ref-neutral">→ #{e.merged_into_id}</span>}
              </div>
            ))}
          </details>
        )}
      </div>
    </section>
  );
}

export default ResearchPage;
```

- [ ] **Step 3: 导航与路由**

AppShell.tsx `navItems`(行为面板之后)加:

```typescript
  { to: "/research", label: "研究", icon: FolderSearch }
```
(icon import 从 lucide-react 补 `FolderSearch`。)

main.tsx:按 `/behavior` 现有路由写法加 `/research` → `ResearchPage`。

- [ ] **Step 4: 渲染测试**(frontend/src/pages/ResearchPage.test.tsx,照 MarketPage.test.tsx 的 QueryClient + mock fetch 模式)

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ResearchPage from "./ResearchPage";

vi.stubGlobal("fetch", vi.fn(async (url: RequestInfo) => {
  const path = String(url);
  const body = path.includes("/research/stats")
    ? { gated_processed_today: 10, auto_linked_today: 5, link_rate: 0.5,
        corrected_today: 1, correction_rate: 0.2, pending_relink: 0 }
    : { items: [{ id: 1, name: "苹果调价", status: "active", gate_keywords: "苹果",
                  created_from: "manual", merged_into_id: null, closed_reason: null,
                  evidence_count: 3, today_new: 1, badge_count: 1,
                  days_since_last: 0, last_evidence_at: "2026-08-01T12:00:00" }] };
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}));

describe("ResearchPage", () => {
  it("renders active events with derived chips", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><ResearchPage /></QueryClientProvider>);
    expect(await screen.findByText(/苹果调价/)).toBeInTheDocument();
    expect(screen.getByText(/今日 \+1/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 5: 类型检查与测试**

```bash
D:/anaconda/python.exe scripts/generate_openapi_types.py
cd frontend && npx tsc -b && npm test
```
Expected: tsc 零错误;vitest 全部通过

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/pages/ResearchPage.tsx frontend/src/pages/ResearchPage.test.tsx frontend/src/components/AppShell.tsx frontend/src/main.tsx
git commit -m "feat(research): 研究页:事件列表+时间轴工作台+导航 (spec §9.1)"
```

---

### Task 13: 前端:缓冲区/旧事重提页签 + 立案表单 + 标注页接线

**Files:**
- Modify: `frontend/src/pages/ResearchPage.tsx`(加页签)
- Create: `frontend/src/components/EventAttach.tsx`
- Modify: `frontend/src/pages/AnnotationsPage.tsx`(newsColumns 加"事件"列)

- [ ] **Step 1: ResearchPage 加页签**(列表 panel 之后追加;tab state `const [tab, setTab] = useState<"events" | "buffer" | "revival">("events")`,顶部三个切换按钮,events 即现有内容)

缓冲区页签核心(含立案表单):

```tsx
function BufferTab({ onCreated }: { onCreated: () => void }) {
  const [days, setDays] = useState(3);
  const [minScore, setMinScore] = useState<number | "">("");
  const [driversOnly, setDriversOnly] = useState(false);
  const [picked, setPicked] = useState<number[]>([]);
  const [name, setName] = useState("");
  const [keywords, setKeywords] = useState("");
  const buffer = useQuery({
    queryKey: ["research-buffer", days, minScore, driversOnly],
    queryFn: () => api.researchBuffer({
      days, drivers_only: driversOnly,
      ...(minScore === "" ? {} : { min_score: minScore }),
    }),
  });
  const events = useQuery({ queryKey: ["research-events", "active"],
                            queryFn: () => api.researchEvents({ status: "active" }) });
  const create = useMutation({
    mutationFn: () => api.researchEventCreate({
      name, news_ids: picked, gate_keywords: keywords || null, created_from: "manual" }),
    onSuccess: () => { setPicked([]); setName(""); setKeywords(""); onCreated(); },
  });
  const suggest = useMutation({
    mutationFn: () => api.researchSuggestKeywords({ name, news_ids: picked }),
    onSuccess: (r) => setKeywords(r.keywords.join("、")),
  });
  const attach = useMutation({
    mutationFn: (eventId: number) =>
      Promise.all(picked.map((nid) => api.researchLinkCreate({ event_id: eventId, news_id: nid }))),
    onSuccess: () => { setPicked([]); onCreated(); },
  });
  return (
    <div className="panel">
      <div className="panel-head" style={{ gap: 8 }}>
        <h2>缓冲区(过闸未挂)</h2>
        <label>天数 <input type="number" value={days} min={1} max={30} style={{ width: 48 }}
                          onChange={(e) => setDays(Number(e.target.value) || 3)} /></label>
        <label>最低分 <input type="number" value={minScore} style={{ width: 48 }}
                            onChange={(e) => setMinScore(e.target.value === "" ? "" : Number(e.target.value))} /></label>
        <label><input type="checkbox" checked={driversOnly}
                      onChange={(e) => setDriversOnly(e.target.checked)} /> 仅看已确认 driver</label>
      </div>
      {picked.length > 0 && (
        <div className="subsection" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <span>已选 {picked.length} 条 →</span>
          <input placeholder="事件名(一个待重定价的变量;中文短名≤20字)" value={name}
                 onChange={(e) => setName(e.target.value)} style={{ width: 260 }} />
          <input placeholder="免闸关键词(顿号分隔,可 AI 建议)" value={keywords}
                 onChange={(e) => setKeywords(e.target.value)} style={{ width: 220 }} />
          <Button kind="secondary" disabled={!name || suggest.isPending}
                  onClick={() => suggest.mutate()}><Sparkles size={14} />AI 建议</Button>
          <Button disabled={!name || create.isPending} onClick={() => create.mutate()}>立事件</Button>
          <select value="" onChange={(e) => { const id = Number(e.target.value); if (id) attach.mutate(id); }}>
            <option value="">挂到事件…</option>
            {(events.data?.items ?? []).map((o) => <option key={o.id} value={o.id}>#{o.id} {o.name}</option>)}
          </select>
        </div>
      )}
      {buffer.isLoading && <LoadingState label="加载缓冲区" />}
      {(buffer.data?.items ?? []).map((n) => (
        <div key={n.id} className="evidence-row" style={{ gap: 6 }}>
          <input type="checkbox" checked={picked.includes(n.id)}
                 onChange={(e) => setPicked(e.target.checked
                   ? [...picked, n.id] : picked.filter((x) => x !== n.id))} />
          <span>{n.timestamp_bj?.slice(5, 16)}</span>
          <span className="ref-neutral">{n.source}</span>
          <span className="ref-neutral">{n.llm_importance ?? "—"}分</span>
          <span style={{ flex: 1 }}>{n.title}</span>
        </div>
      ))}
    </div>
  );
}
```

旧事重提页签:

```tsx
function RevivalTab() {
  const revival = useQuery({ queryKey: ["research-revival"], queryFn: api.researchRevival });
  const qc = useQueryClient();
  const reopen = useMutation({
    mutationFn: (eventId: number) => api.researchEventPatch(eventId, { status: "active" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["research-events"] }),
  });
  return (
    <div className="panel">
      <div className="panel-head"><h2>旧事重提(近 7 天命中已关闭事件关键词)</h2></div>
      {(revival.data?.items ?? []).length === 0 && <EmptyState label="没有沉睡事件被唤醒" />}
      {(revival.data?.items ?? []).map((r, i) => (
        <div key={i} className="evidence-row" style={{ gap: 6 }}>
          <span>{r.news.timestamp_bj?.slice(5, 16)}</span>
          <span className="s-badge mid">『{r.event_name}』</span>
          <span style={{ flex: 1 }}>{r.news.title}</span>
          <Button kind="secondary" onClick={() => reopen.mutate(r.event_id)}>重开该事件</Button>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: EventAttach.tsx**(标注页用的小控件)

```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";

/** 标注页 driver 新闻行的事件控件(spec §9.2):
 * 已挂事件 → 只读徽章 + 跳转研究页;下拉可挂到活跃事件或新建。写操作集中在研究页,这里只有快捷挂接。 */
export function EventAttach({ newsId, isDriver }: { newsId: number; isDriver: boolean }) {
  const qc = useQueryClient();
  const links = useQuery({ queryKey: ["research-news-links", newsId],
                           queryFn: () => api.researchNewsLinks(newsId),
                           staleTime: 60_000 });
  const events = useQuery({ queryKey: ["research-events", "active"],
                            queryFn: () => api.researchEvents({ status: "active" }),
                            staleTime: 60_000 });
  const attach = useMutation({
    mutationFn: (eventId: number) => api.researchLinkCreate({ event_id: eventId, news_id: newsId }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["research-news-links", newsId] }),
  });
  const create = useMutation({
    mutationFn: (name: string) => api.researchEventCreate({
      name, news_ids: [newsId], created_from: "annotation" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["research-news-links", newsId] }),
  });
  const linked = links.data?.items ?? [];
  return (
    <span style={{ display: "inline-flex", gap: 3, alignItems: "center" }}>
      {linked.map((l) => (
        <Link key={l.link_id} to="/research" className="s-badge none"
              title="已挂事件,点击去研究页">#{l.event_id} {l.event_name}</Link>
      ))}
      {isDriver && (
        <select value="" title="挂到事件 / 新建(价格已证明它重要,顺手立案)"
                style={{ fontSize: 12, maxWidth: 90 }}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === "new") {
                    const name = window.prompt("新事件名(一个待重定价的变量;≤20字)", "");
                    if (name) create.mutate(name);
                  } else if (v) attach.mutate(Number(v));
                }}>
          <option value="">挂事件…</option>
          <option value="new">+ 新建…</option>
          {(events.data?.items ?? []).map((o) => (
            <option key={o.id} value={o.id}>#{o.id} {o.name}</option>
          ))}
        </select>
      )}
    </span>
  );
}
```

- [ ] **Step 3: AnnotationsPage 接线**:`newsColumns` 的 useMemo 里(tags 列之后、title 列之前)加一列;文件头 import `{ EventAttach } from "../components/EventAttach"`:

```tsx
    {
      key: "event",
      header: "事件",
      cell: (row: NewsItem) => (
        <EventAttach newsId={row.id} isDriver={(newsRoles[row.id] ?? "noise") === "driver"} />
      )
    },
```
(useMemo 依赖数组已含 `newsRoles`,无需增项。)

- [ ] **Step 4: 类型检查与测试**

```bash
cd frontend && npx tsc -b && npm test
```
Expected: 零错误、全部通过

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/ResearchPage.tsx frontend/src/components/EventAttach.tsx frontend/src/pages/AnnotationsPage.tsx
git commit -m "feat(research): 缓冲区/旧事重提页签 + 立案表单 + 标注页事件接线 (spec §9.1/§9.2)"
```

---

### Task 14: BOJ 沙盒回放脚本(验收)

**Files:**
- Create: `scripts/replay_event_pool.py`

- [ ] **Step 1: 实现**(一次性脚本,无单测;生产库零接触)

```python
# -*- coding: utf-8 -*-
"""BOJ 沙盒回放(news-research-phase1 spec §14):验收挂接质量与时间轴呈现。

用法(沙盒库 = 线上快照副本,拉取流程见记忆 remote-data-access):
  D:/anaconda/python.exe scripts/replay_event_pool.py ^
      --db data/replay-sandbox.db --seed-news-id 12345 ^
      --name "日本央行加息预期提前" --keywords "日本央行、日银、BOJ、植田" ^
      --days 55 --report docs/superpowers/replay-boj-report.md

生产库零接触:必须显式传 --db,脚本会拒绝默认库文件名。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="沙盒 SQLite 文件(线上快照副本)")
    ap.add_argument("--seed-news-id", type=int, required=True, help="人工指定的首条信号新闻 id")
    ap.add_argument("--name", required=True)
    ap.add_argument("--keywords", default=None)
    ap.add_argument("--days", type=float, default=55.0, help="回扫深度(天)")
    ap.add_argument("--report", default="replay-report.md")
    args = ap.parse_args()

    db_path = Path(args.db).resolve()
    if db_path.name == "market_monitor.db":
        raise SystemExit("拒绝:这是生产库文件名,回放只允许沙盒副本(spec §14)")
    if not db_path.exists():
        raise SystemExit(f"沙盒库不存在: {db_path}")
    # 必须在 import database 之前设置(engine 在模块导入时绑定 DATABASE_URL)
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"

    from database import SessionLocal, create_tables
    from services import event_linking, event_pool

    create_tables()
    session = SessionLocal()
    try:
        event = event_pool.create_event(
            session, args.name, news_ids=[args.seed_news_id],
            gate_keywords=args.keywords, created_from="manual",
            backscan_hours=args.days * 24)
        print(f"立案 #{event.id} {event.name};回扫 {args.days} 天,开始分批挂接...")
        rounds = 0
        while True:
            stats = event_linking.link_unprocessed(session, limit=200)
            rounds += 1
            print(f"  round {rounds}: 盖章 {stats['processed']}, 新挂 {stats['linked']}, LLM {stats['called']} 条")
            if stats["processed"] == 0 and stats["called"] == 0:
                break
            if rounds >= 500:      # LLM 持续报错时失败批不盖游标会无限重试,兜个底
                print("  达到 500 轮上限,提前停止(检查 API/网络后可重跑续接——游标幂等)")
                break
        tl = event_pool.event_timeline(session, event.id)
        lines = [f"# 回放报告:{event.name}", "",
                 f"- 证据 {len(tl['items'])} 条;seed news #{args.seed_news_id};关键词:{args.keywords or '(无)'}", "",
                 "| 时间(BJ) | 来源 | 分 | 方向 | 观测 | 徽章 | 挂接 | 标题 |", "|---|---|---|---|---|---|---|---|"]
        for it in tl["items"]:
            obs = it["obs"]
            obs_txt = ("计算中" if obs["status"] == "pending"
                       else "—" if obs["status"] != "ok"
                       else f"{obs['actual_minutes']}min {obs['net_pct']:+.2f}%")
            badge = (f"driver {it['driver_badge']['change_pct']:+.2f}%"
                     if it["driver_badge"] and it["driver_badge"]["change_pct"] is not None
                     else "driver" if it["driver_badge"] else "")
            miss = f"评分失手{it['news']['llm_importance']}" if it["score_miss"] else ""
            src = it["link"]["link_source"] + (f"({it['link']['confidence']})" if it["link"]["confidence"] else "")
            lines.append(f"| {it['news']['timestamp_bj']} | {it['news']['source']} | "
                         f"{it['news']['llm_importance']} | {it['news']['news_direction'] or ''} | "
                         f"{obs_txt} | {badge or miss} | {src} | {it['news']['title'][:60]} |")
        Path(args.report).write_text("\n".join(lines), encoding="utf-8")
        print(f"报告已写入 {args.report};请人工盘点漏挂/误挂(spec §14 通过标准)")
    finally:
        session.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 冒烟**(不发真调用:临时给沙盒库跑 `--days 0.01`,或 mock;确认脚本能起、拒绝生产库文件名)

Run: `D:/anaconda/python.exe scripts/replay_event_pool.py --db market_monitor.db --seed-news-id 1 --name x`
Expected: 退出并打印"拒绝:这是生产库文件名"

- [ ] **Step 3: Commit**

```bash
git add scripts/replay_event_pool.py
git commit -m "feat(research): BOJ 沙盒回放脚本(验收用,生产库零接触) (spec §14)"
```

---

### Task 15: 全量回归 + 项目地图文档

**Files:**
- Modify: `ARCHITECTURE.md` / `DATAFLOW.md` / `DECISIONS.md`(各加一节/一条)

- [ ] **Step 1: 全量回归**

```bash
D:/anaconda/python.exe -m pytest -q
cd frontend && npx tsc -b && npm test
```
Expected: 全部通过。任何失败先修再继续。

- [ ] **Step 2: 文档**

- ARCHITECTURE.md:模块清单加 `services/event_linking.py`(挂接调用)与 `services/event_pool.py`(事件生命周期与读取)、`models/research.py`、前端 ResearchPage,一句话职责各一行。
- DATAFLOW.md:加"研究事件池"数据流:采集→打分→打标→挂接(闸门/黑名单/关键词)→时间轴/缓冲区;立案仅人工;观测层读时现算;引用勾稽 spec §4.1。
- DECISIONS.md:加一条"2026-08-02 事件池立案权收归人工、价格/新闻永久保留",链接 spec 与本计划。

- [ ] **Step 3: Commit**

```bash
git add ARCHITECTURE.md DATAFLOW.md DECISIONS.md
git commit -m "docs: 项目地图补研究事件池模块与数据流"
```

---

## 上线步骤(代码完成后,人工执行——spec §13)

1. 部署重启(迁移自动跑:建表 + 存量盖章);确认 `EVENT_LINK_ENABLED=1`、日志出现 `[EventLink]`。
2. 种子池:研究页缓冲区勾"仅看已确认 driver"+ 天数 14 → 人工归组立案 8-12 个事件(苹果调价、客机这类失手线优先补关键词)→ 各深回扫 14 天(约 3 小时消化完,详情页看"回扫进行中"递减)。
3. 并行 7 天:每天看研究页顶部挂接率/纠错率;WeCom 08:10 清单当天生效。
4. 切换(不在本计划):连续 3 天纠错率 <20% 且无整批失败 → 删打标 topic 槽位 + 冻结注释 + 前端 topic 下拉退役。
5. 回滚:`EVENT_LINK_ENABLED=0`。

## Spec 覆盖自查表

| spec | 任务 |
|---|---|
| §3 数据模型/迁移 | Task 1 |
| §4.1 资格/游标/tick | Task 3/4/5 |
| §4.3 提示词/解析 | Task 4 |
| §4.4 黑名单 | Task 1(config)/3 |
| §5.1 关键词免闸 | Task 3/6(set_keywords) |
| §5.2 AI 建议 | Task 8/13(表单) |
| §6 生命周期/回扫 | Task 5/6 |
| §7 沉睡监听/重开 | Task 7(revival)/13(页签) |
| §8 观测层/徽章/评分失手 | Task 2/7 |
| §9.1 研究页 | Task 12/13 |
| §9.2 标注页接线 | Task 13 |
| §9.3 API | Task 9 |
| §10 WeCom 清单 | Task 7(文本)/11(调度) |
| §11 配置/调度清单 | Task 1/11 |
| §12 保留与保护 | Task 10 |
| §13 上线步骤 | 文末人工清单 |
| §14 BOJ 回放 | Task 14 |
| §15 测试清单 | 各任务 Step 1 |
