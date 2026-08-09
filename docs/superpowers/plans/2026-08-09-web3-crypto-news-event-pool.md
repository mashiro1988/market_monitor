# Web3 二期 A（加密快讯 + 加密事件池）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 接入 BlockBeats Pro API 与币安官方公告两个加密新闻源，用独立的加密打分口径（重要性/方向/提及币种/币圈事务）驱动一个与宏观线同构、但走语义闸的加密事件池，并给它一个独立的加密快讯页面。

**Architecture:** 加密源配置独立于 `config.NEWS_SOURCES`（宏观白名单），新闻表加 `market` 列做总开关；宏观的补评分/打标/行为命中/挂接四条路径全部加 `market != 'crypto'` 护栏；加密新闻走新的 `crypto_tagging` 服务一次调用出四件套，`event_linking` 加 `market` 参数复用同一套游标语义与留痕表，闸门从分数换成 `is_crypto_affair` 语义判定。

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy / SQLite（WAL）/ DeepSeek chat completions / React 18 + TanStack Query + Vite / pytest + vitest

**设计稿:** `docs/superpowers/specs/2026-08-09-web3-news-crypto-event-pool-design.md`

---

## 文件结构

| 文件 | 职责 | 新建/修改 |
|---|---|---|
| `config.py` | `CRYPTO_NEWS_SOURCES`、`CRYPTO_NEWS_ENABLED`、`BLOCKBEATS_API_KEY`、`BINANCE_ANN_CATALOGS` | 修改 |
| `models/news.py` | `NewsItem.market` / `NewsItem.is_crypto_affair` 两列 | 修改 |
| `models/crypto.py` | `NewsCoin`（新闻↔币种对照表） | 新建 |
| `database.py` | SQLite 轻量迁移：补两列 + 存量盖 `macro` | 修改 |
| `scanners/base.py` | `NewsRecord.market` 字段 | 修改 |
| `scanners/sources/blockbeats_source.py` | BlockBeats Pro API 采集器 | 新建 |
| `scanners/sources/binance_ann_source.py` | 币安公告采集器 | 新建 |
| `scanners/news_scanner.py` | 挂加密源、加密跳过宏观 scorer、`market` 落库 | 修改 |
| `services/crypto_tagging.py` | 加密打分四件套 + 写 `news_coins` | 新建 |
| `services/news_rescore.py` / `services/news_tagging.py` / `services/behavior_classifier.py` | 宏观护栏 | 修改 |
| `services/event_linking.py` | `market` 参数化 + 加密语义闸 + 加密挂接提示词 | 修改 |
| `services/event_pool.py` | 事件 `event_type` 贯通 + 事件币种读时派生 | 修改 |
| `services/news_service.py` | `get_crypto_news` + 加密源枚举 | 修改 |
| `api/routes.py` / `schemas/*` | 加密快讯与加密事件的接口 | 修改 |
| `frontend/src/pages/CryptoNewsPage.tsx` | 加密快讯页 | 新建 |
| `frontend/src/main.tsx` / `components/AppShell.tsx` | 路由与导航 | 修改 |

---

### Task 1: `market` 列 + 宏观路径护栏

**Files:**
- Modify: `models/news.py`
- Modify: `database.py`（`_ensure_sqlite_schema` 的 news_items 补列 dict）
- Modify: `services/news_rescore.py`、`services/news_tagging.py`、`services/behavior_classifier.py`
- Test: `tests/test_crypto_market_guard.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""加密新闻必须被宏观四条路径挡在外面(web3 二期A design §2 零污染)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.news import NewsItem
from services import behavior_classifier, news_tagging


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _news(s, title, market="macro", score=8, tagged=False):
    n = NewsItem(timestamp=datetime(2026, 8, 9, 12, 0), source="jin10", title=title,
                 language="zh", llm_importance=score, market=market,
                 traditional_open=True,
                 tagged_at=datetime(2026, 8, 9, 12, 1) if tagged else None)
    s.add(n); s.commit()
    return n


def test_macro_tagging_skips_crypto_news(session, monkeypatch):
    _news(session, "宏观新闻", market="macro")
    _news(session, "币圈新闻", market="crypto")
    seen = []
    monkeypatch.setattr(news_tagging, "tag_news_batch",
                        lambda s, chunk: seen.extend(n.title for n in chunk) or len(chunk))
    news_tagging.tag_untagged(session)
    assert seen == ["宏观新闻"]


def test_behavior_has_news_ignores_crypto(session):
    _news(session, "宏观新闻", market="macro")
    _news(session, "币圈新闻", market="crypto")
    ids = behavior_classifier._news_ids_in_window(
        session, datetime(2026, 8, 9, 11, 50), datetime(2026, 8, 9, 12, 10))
    titles = [session.get(NewsItem, i).title for i in ids]
    assert titles == ["宏观新闻"]


def test_rescore_skips_crypto_news(session):
    from services import news_rescore

    _news(session, "宏观未评分", market="macro", score=None)
    _news(session, "币圈未评分", market="crypto", score=None)

    class FakeScorer:
        enabled = True
        def __init__(self): self.seen = []
        def score_batch(self, records):
            self.seen.extend(r.title for r in records)
            return [7] * len(records)

    scorer = FakeScorer()
    news_rescore.rescore_unscored(session, scorer=scorer, now=datetime(2026, 8, 9, 12, 30))
    assert scorer.seen == ["宏观未评分"]


def test_default_market_is_macro(session):
    n = NewsItem(timestamp=datetime(2026, 8, 9, 12, 0), source="jin10",
                 title="默认", language="zh")
    session.add(n); session.commit()
    assert n.market == "macro"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_crypto_market_guard.py -q`
Expected: FAIL —— `TypeError: 'market' is an invalid keyword argument for NewsItem`

- [ ] **Step 3: 加列**

`models/news.py` 在 `rescore_attempts` 之后加两列：

```python
    # 市场归属:macro=宏观线(现有四源) / crypto=加密线(web3 二期A)。
    # 分流总开关——宏观的补评分/打标/行为命中/挂接全部只认 macro。
    market = Column(String(8), nullable=False, default="macro", server_default="macro")
    # 加密线语义闸(design §2/§3):加密源转载的纯宏观新闻为 0,不进加密事件挂接。
    # 宏观新闻恒为 NULL(该判定只对加密线有意义)。
    is_crypto_affair = Column(Boolean, nullable=True)
```

`__table_args__` 加一条复合索引（加密页与加密挂接都按 market+时间取数）：

```python
        Index("ix_news_market_ts", "market", "timestamp"),
```

- [ ] **Step 4: 加迁移**

`database.py` 的 news_items 补列 dict 里加两项：

```python
                "rescore_attempts": "INTEGER",
                # web3 二期A:市场归属 + 加密语义闸判定
                "market": "VARCHAR(8) NOT NULL DEFAULT 'macro'",
                "is_crypto_affair": "BOOLEAN",
```

紧跟补列循环之后（`migrate_news_event_cursor(conn)` 之前）加索引：

```python
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_news_market_ts ON news_items (market, timestamp)"))
```

存量行由 `DEFAULT 'macro'` 直接盖住，无需 UPDATE。

- [ ] **Step 5: 加三处护栏**

`services/news_tagging.py::tag_untagged` 的查询加一行过滤：

```python
        .filter(NewsItem.tagged_at.is_(None), NewsItem.traditional_open.isnot(None),
                NewsItem.market == "macro")
```

`services/news_rescore.py::rescore_unscored` 的查询加同款过滤：

```python
            .filter(NewsItem.llm_importance.is_(None),
                    NewsItem.market == "macro",
                    NewsItem.created_at >= cutoff,
                    func.coalesce(NewsItem.rescore_attempts, 0) < max_attempts)
```

`services/behavior_classifier.py::_news_ids_in_window` 的查询加同款过滤：

```python
        .filter(NewsItem.timestamp >= start - pad,
                NewsItem.timestamp <= end + pad,
                NewsItem.market == "macro",
                (NewsItem.llm_importance.is_(None))
                | (NewsItem.llm_importance >= config.EVENT_LINK_MIN_IMPORTANCE))
```

- [ ] **Step 6: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_crypto_market_guard.py -q`
Expected: PASS（4 passed）

- [ ] **Step 7: 跑全量回归**

Run: `D:/anaconda/python.exe -m pytest -q`
Expected: 除已知的 Windows 编码假失败外全绿

- [ ] **Step 8: 提交**

```bash
git add models/news.py database.py services/news_tagging.py services/news_rescore.py services/behavior_classifier.py tests/test_crypto_market_guard.py
git commit -m "feat(crypto): news_items 加 market/is_crypto_affair 列 + 宏观三路护栏"
```

---

### Task 2: `news_coins` 对照表

**Files:**
- Create: `models/crypto.py`
- Modify: `models/__init__.py`
- Test: `tests/test_news_coins_model.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""新闻↔币种对照表:同一新闻同一币种只留一行(B 的归因反查地基)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from database import Base
from models.crypto import NewsCoin


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_same_news_same_coin_rejected(session):
    session.add(NewsCoin(news_id=1, coin="SOL")); session.commit()
    session.add(NewsCoin(news_id=1, coin="SOL"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_same_news_multiple_coins_ok(session):
    session.add_all([NewsCoin(news_id=1, coin="SOL"), NewsCoin(news_id=1, coin="ARB")])
    session.commit()
    assert session.query(NewsCoin).count() == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_news_coins_model.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'models.crypto'`

- [ ] **Step 3: 建模型**

`models/crypto.py`：

```python
# -*- coding: utf-8 -*-
"""加密线数据模型(web3 二期A design §2/§5)。

news_coins = 新闻实际在讨论哪几个币,由加密打分调用顺手抽取。
**不存"是否可交易"**——交易所上新/下架随时变,冻结成列会过期;
可交易性由读侧拿 Binance symbol 全集现算(设计稿"读时派生"铁律)。"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, UniqueConstraint

from database import Base


class NewsCoin(Base):
    __tablename__ = "news_coins"

    id = Column(Integer, primary_key=True, index=True)
    news_id = Column(Integer, nullable=False, index=True)
    coin = Column(String(20), nullable=False)      # 归一化大写代码,如 BTC / SOL
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("news_id", "coin", name="uq_news_coin"),
        Index("ix_news_coin_coin", "coin"),        # B 的反查方向:按币找新闻
    )
```

- [ ] **Step 4: 注册模型**

`models/__init__.py` 加导入（照现有写法追加一行，确保 `create_all` 能建表）：

```python
from models.crypto import NewsCoin  # noqa: F401
```

- [ ] **Step 5: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_news_coins_model.py -q`
Expected: PASS（2 passed）

- [ ] **Step 6: 提交**

```bash
git add models/crypto.py models/__init__.py tests/test_news_coins_model.py
git commit -m "feat(crypto): news_coins 新闻币种对照表"
```

---

### Task 3: 加密源配置

**Files:**
- Modify: `config.py`
- Test: `tests/test_crypto_config.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""加密源配置必须与宏观白名单物理隔离(否则污染标注候选池)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


def test_crypto_sources_not_in_macro_whitelist():
    assert set(config.CRYPTO_NEWS_SOURCES) & set(config.NEWS_SOURCES) == set()


def test_crypto_sources_shape():
    for key, cfg in config.CRYPTO_NEWS_SOURCES.items():
        assert "enabled" in cfg and "name" in cfg and "language" in cfg


def test_blockbeats_defaults():
    bb = config.CRYPTO_NEWS_SOURCES["blockbeats"]
    assert bb["api_url"].startswith("https://api-pro.theblockbeats.info")
    assert bb["page_size"] <= 50            # Pro API 上限
    assert bb["lang"] == "cn"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_crypto_config.py -q`
Expected: FAIL —— `AttributeError: module 'config' has no attribute 'CRYPTO_NEWS_SOURCES'`

- [ ] **Step 3: 加配置**

`config.py` 在 `NEWS_SOURCES` 定义之后插入：

```python
# ============================================================
# 加密新闻源（web3 二期A design §1）
# **刻意不并进 NEWS_SOURCES**：那个字典是宏观白名单，标注候选新闻与自动标注
# 都从它取源（services/annotation_service.py::_annotation_news_sources），
# 加密源混进去会直接污染已校准的标注池。分开放 = 结构上防污染。
# ============================================================
CRYPTO_NEWS_ENABLED = os.getenv("CRYPTO_NEWS_ENABLED", "1") == "1"
BLOCKBEATS_API_KEY = os.getenv("BLOCKBEATS_API_KEY", "")

CRYPTO_NEWS_SOURCES = {
    # BlockBeats Pro API：老的 open-api/open-flash 已软下线（匿名请求恒返回空数组，
    # 2026-08-09 实探），现走 Pro API + api-key 请求头。取全量非仅重要档：
    # 二期B 要归因的小币新闻基本都在非重要档里。
    "blockbeats": {
        "enabled": True,
        "language": "zh",
        "name": "BlockBeats",
        "api_url": "https://api-pro.theblockbeats.info/v1/newsflash",
        "page_size": 30,          # Pro API 单页上限 50
        "max_pages": 2,           # 5 分钟一轮，60 条足够覆盖（实测约 70-150 条/天）
        "lang": "cn",
    },
    # 币安官方公告：上新/下架/合约上市——"某币为什么突然拉起来"命中率最高的官方口径。
    # catalogId 见 BINANCE_ANN_CATALOGS；营销活动类目录刻意不订。
    "binance_ann": {
        "enabled": True,
        "language": "en",
        "name": "Binance公告",
        "api_url": "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query",
        "page_size": 20,
    },
}

# 币安公告目录：(catalogId, 中文说明)。48=新币上线（含合约上新），161=下架。
BINANCE_ANN_CATALOGS = (
    (48, "新币上线"),
    (161, "下架"),
)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_crypto_config.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add config.py tests/test_crypto_config.py
git commit -m "feat(crypto): 加密源配置独立于宏观白名单"
```

---

### Task 4: BlockBeats 采集器

**Files:**
- Modify: `scanners/base.py`（`NewsRecord.market`）
- Create: `scanners/sources/blockbeats_source.py`
- Test: `tests/test_blockbeats_source.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""BlockBeats Pro API 采集器:北京时间转 UTC、HTML 去标签、分页、失败上抛。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import pytest

from scanners.sources.blockbeats_source import BlockBeatsSource


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
    def json(self):
        return self._payload


def _payload(items):
    return {"status": 0, "message": "", "data": {"page": 1, "data": items}}


ITEM = {
    "id": 360591,
    "title": "pump.fun 挖角竞对 KOL",
    "content": "<p>BlockBeats 消息，<strong>据多位 KOL</strong> 爆料……</p>",
    "pic": "https://img/x.png",
    "link": "https://m.theblockbeats.info/flash/360591",
    "url": "",
    "create_time": "2026-08-09 13:30:17",
}


def test_beijing_time_converted_to_utc_naive(monkeypatch):
    src = BlockBeatsSource(api_key="k")
    monkeypatch.setattr(src, "_get_page", lambda page: [ITEM] if page == 1 else [])
    rec = src.fetch()[0]
    assert rec.published_at == datetime(2026, 8, 9, 5, 30, 17)   # 13:30:17 北京 = 05:30:17 UTC
    assert rec.published_at.tzinfo is None


def test_html_stripped_and_fields_mapped(monkeypatch):
    src = BlockBeatsSource(api_key="k")
    monkeypatch.setattr(src, "_get_page", lambda page: [ITEM] if page == 1 else [])
    rec = src.fetch()[0]
    assert rec.source == "blockbeats"
    assert rec.source_id == "360591"
    assert rec.market == "crypto"
    assert rec.language == "zh"
    assert "<p>" not in rec.content and "<strong>" not in rec.content
    assert "据多位 KOL 爆料" in rec.content
    assert rec.url == ITEM["link"]


def test_pagination_stops_on_empty_page(monkeypatch):
    src = BlockBeatsSource(api_key="k", max_pages=3)
    calls = []
    def fake_get_page(page):
        calls.append(page)
        return [dict(ITEM, id=100 + page)] if page == 1 else []
    monkeypatch.setattr(src, "_get_page", fake_get_page)
    recs = src.fetch()
    assert calls == [1, 2]          # 第 2 页空即止,不白跑第 3 页
    assert len(recs) == 1


def test_missing_key_raises(monkeypatch):
    src = BlockBeatsSource(api_key="")
    with pytest.raises(RuntimeError, match="BLOCKBEATS_API_KEY"):
        src.fetch()


def test_api_error_status_raises(monkeypatch):
    src = BlockBeatsSource(api_key="k")
    monkeypatch.setattr("scanners.sources.blockbeats_source.requests.get",
                        lambda *a, **kw: FakeResp({"status": 100, "message": "Missing API key", "data": None}))
    with pytest.raises(RuntimeError, match="Missing API key"):
        src.fetch()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_blockbeats_source.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'scanners.sources.blockbeats_source'`

- [ ] **Step 3: `NewsRecord` 加 market 字段**

`scanners/base.py` 的 `NewsRecord` 末尾加：

```python
    published_at: Optional[datetime] = None  # 原始发布时间
    market: str = "macro"                    # macro=宏观线 / crypto=加密线（web3 二期A）
```

- [ ] **Step 4: 写采集器**

`scanners/sources/blockbeats_source.py`：

```python
# -*- coding: utf-8 -*-
"""BlockBeats Pro API 快讯源（web3 二期A design §1.1）。

老的 open-api/open-flash 已软下线：匿名请求恒返回 {"status":0,"data":[]}（2026-08-09
服务器实探），文档没写但接口体系已换代。现走 Pro API：api-key 请求头认证，
create_time 是**北京时间**，入库前减 8 小时转 UTC naive（与 Jin10 同款处理）。
"""
import html
import re
from datetime import datetime, timedelta

import requests
from loguru import logger

import config
from scanners.base import BaseSource, NewsRecord

_TAG_RE = re.compile(r"<[^>]+>")
BEIJING_OFFSET = timedelta(hours=8)


def _strip_html(raw: str | None) -> str:
    """去标签 + 反转义 + 压空白。正文是富文本 HTML，直接入库会污染打分提示词。"""
    if not raw:
        return ""
    return " ".join(html.unescape(_TAG_RE.sub(" ", raw)).split())


def _parse_beijing(text: str | None) -> datetime | None:
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text.strip(), fmt) - BEIJING_OFFSET
        except ValueError:
            continue
    # 兼容老接口的 Unix 秒字符串
    if str(text).isdigit():
        return datetime.utcfromtimestamp(int(text))
    return None


class BlockBeatsSource(BaseSource):
    """BlockBeats 快讯（取全量中文档，不只取 important）。"""

    name = "blockbeats"

    def __init__(self, api_key: str | None = None, page_size: int | None = None,
                 max_pages: int | None = None):
        cfg = config.CRYPTO_NEWS_SOURCES["blockbeats"]
        self.api_key = config.BLOCKBEATS_API_KEY if api_key is None else api_key
        self.api_url = cfg["api_url"]
        self.page_size = int(page_size or cfg["page_size"])
        self.max_pages = int(max_pages or cfg["max_pages"])
        self.lang = cfg["lang"]

    def _get_page(self, page: int) -> list[dict]:
        """取一页；接口层错误一律抛（不返空数组——空数组是"没有新内容"的合法语义）。"""
        resp = requests.get(
            self.api_url,
            params={"page": page, "size": self.page_size, "lang": self.lang},
            headers={"api-key": self.api_key, "Accept": "application/json"},
            timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, 20),
            proxies=config.proxies(),
        )
        if resp.status_code != 200:
            raise RuntimeError(f"BlockBeats HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        if body.get("status") != 0:
            raise RuntimeError(f"BlockBeats 接口错误: {body.get('message') or body}")
        data = body.get("data") or {}
        items = data.get("data") if isinstance(data, dict) else data
        return items or []

    def fetch(self) -> list[NewsRecord]:
        if not self.api_key:
            raise RuntimeError("BLOCKBEATS_API_KEY 未配置，无法采集 BlockBeats")
        records: list[NewsRecord] = []
        for page in range(1, self.max_pages + 1):
            items = self._get_page(page)
            if not items:
                break                      # 空页即止，不白跑后续页
            for item in items:
                title = (item.get("title") or "").strip()
                if not title:
                    continue
                records.append(NewsRecord(
                    source=self.name,
                    source_id=str(item.get("id") or ""),
                    title=title,
                    content=_strip_html(item.get("content")),
                    url=item.get("link") or item.get("url") or None,
                    language="zh",
                    published_at=_parse_beijing(item.get("create_time")),
                    market="crypto",
                ))
        logger.info(f"[BlockBeats] 取回 {len(records)} 条快讯")
        return records
```

- [ ] **Step 5: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_blockbeats_source.py -q`
Expected: PASS（5 passed）

- [ ] **Step 6: 提交**

```bash
git add scanners/base.py scanners/sources/blockbeats_source.py tests/test_blockbeats_source.py
git commit -m "feat(crypto): BlockBeats Pro API 快讯采集器"
```

---

### Task 5: 币安公告采集器

**Files:**
- Create: `scanners/sources/binance_ann_source.py`
- Test: `tests/test_binance_ann_source.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""币安公告采集器:毫秒时间戳转 UTC、多目录合并、标题带目录名。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import pytest

from scanners.sources.binance_ann_source import BinanceAnnouncementSource

ARTICLE = {
    "id": 281779,
    "code": "307687ad279e42e6909ee1be8c472b50",
    "title": "Binance Futures Will Launch Multiple USDⓈ-Margined Perpetual Contracts",
    "type": 1,
    "releaseDate": 1785983412862,
}


def _payload(articles):
    return {"code": "000000", "message": None,
            "data": {"catalogs": [{"catalogId": 48, "articles": articles}]}}


def test_release_date_ms_to_utc_naive(monkeypatch):
    src = BinanceAnnouncementSource()
    monkeypatch.setattr(src, "_get_catalog", lambda cid: [ARTICLE])
    rec = src.fetch()[0]
    assert rec.published_at == datetime.utcfromtimestamp(1785983412862 / 1000)
    assert rec.published_at.tzinfo is None


def test_fields_mapped(monkeypatch):
    src = BinanceAnnouncementSource()
    monkeypatch.setattr(src, "_get_catalog", lambda cid: [ARTICLE])
    rec = src.fetch()[0]
    assert rec.source == "binance_ann"
    assert rec.source_id == "281779"
    assert rec.market == "crypto"
    assert rec.url.endswith(ARTICLE["code"])
    assert "新币上线" in rec.title            # 目录名前缀，便于人与模型识别公告类型


def test_all_catalogs_polled(monkeypatch):
    src = BinanceAnnouncementSource()
    seen = []
    monkeypatch.setattr(src, "_get_catalog", lambda cid: seen.append(cid) or [])
    src.fetch()
    assert seen == [48, 161]


def test_api_error_code_raises(monkeypatch):
    src = BinanceAnnouncementSource()

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": "500001", "message": "boom", "data": None}

    monkeypatch.setattr("scanners.sources.binance_ann_source.requests.get",
                        lambda *a, **kw: FakeResp())
    with pytest.raises(RuntimeError, match="boom"):
        src.fetch()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_binance_ann_source.py -q`
Expected: FAIL —— `ModuleNotFoundError`

- [ ] **Step 3: 写采集器**

`scanners/sources/binance_ann_source.py`：

```python
# -*- coding: utf-8 -*-
"""币安官方公告源（web3 二期A design §1.1）。

上新/下架/合约上市是"某币为什么突然拉起来"命中率最高的官方口径，且可审计。
接口是币安站点的 CMS 接口（无服务承诺，可能改版）：失败上抛由 NewsScanner
记源错误，与 FinancialJuice 同款处置。releaseDate 是毫秒 Unix 时间戳（UTC）。
"""
from datetime import datetime

import requests
from loguru import logger

import config
from scanners.base import BaseSource, NewsRecord

ARTICLE_URL_PREFIX = "https://www.binance.com/en/support/announcement/"


class BinanceAnnouncementSource(BaseSource):
    """币安公告（只订 config.BINANCE_ANN_CATALOGS 里的目录）。"""

    name = "binance_ann"

    def __init__(self, page_size: int | None = None):
        cfg = config.CRYPTO_NEWS_SOURCES["binance_ann"]
        self.api_url = cfg["api_url"]
        self.page_size = int(page_size or cfg["page_size"])
        self.catalogs = dict(config.BINANCE_ANN_CATALOGS)

    def _get_catalog(self, catalog_id: int) -> list[dict]:
        resp = requests.get(
            self.api_url,
            params={"type": 1, "pageNo": 1, "pageSize": self.page_size,
                    "catalogId": catalog_id},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                     "Accept": "application/json"},
            timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, 20),
            proxies=config.proxies(),
        )
        if resp.status_code != 200:
            raise RuntimeError(f"币安公告 HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        if body.get("code") != "000000":
            raise RuntimeError(f"币安公告接口错误: {body.get('message') or body}")
        catalogs = ((body.get("data") or {}).get("catalogs") or [])
        out: list[dict] = []
        for cat in catalogs:
            out.extend(cat.get("articles") or [])
        return out

    def fetch(self) -> list[NewsRecord]:
        records: list[NewsRecord] = []
        for catalog_id, label in self.catalogs.items():
            for art in self._get_catalog(catalog_id):
                title = (art.get("title") or "").strip()
                if not title:
                    continue
                released = art.get("releaseDate")
                published = datetime.utcfromtimestamp(released / 1000) if released else None
                records.append(NewsRecord(
                    source=self.name,
                    source_id=str(art.get("id") or ""),
                    title=f"[{label}] {title}"[:500],
                    content=None,
                    url=f"{ARTICLE_URL_PREFIX}{art.get('code')}" if art.get("code") else None,
                    language="en",
                    published_at=published,
                    market="crypto",
                ))
        logger.info(f"[BinanceAnn] 取回 {len(records)} 条公告")
        return records
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_binance_ann_source.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add scanners/sources/binance_ann_source.py tests/test_binance_ann_source.py
git commit -m "feat(crypto): 币安官方公告采集器"
```

---

### Task 6: 接入 NewsScanner（加密跳过宏观打分 + market 落库）

**Files:**
- Modify: `scanners/news_scanner.py`
- Test: `tests/test_news_scanner_crypto.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""加密新闻不进宏观 scorer(口径不同会打歪),且 market 必须落库。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import pytest

from scanners.base import NewsRecord
from scanners.news_scanner import NewsScanner


def test_only_macro_records_go_to_scorer():
    scanner = NewsScanner.__new__(NewsScanner)      # 不跑 __init__（免起真源）
    macro = NewsRecord(source="jin10", source_id="1", title="宏观", market="macro")
    crypto = NewsRecord(source="blockbeats", source_id="2", title="币圈", market="crypto")
    assert [r.title for r in scanner._macro_only([macro, crypto])] == ["宏观"]


def test_save_records_persists_market(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import scanners.news_scanner as ns
    from database import Base
    from models.news import NewsItem

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    monkeypatch.setattr(ns, "get_session", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)

    scanner = NewsScanner.__new__(NewsScanner)
    scanner._save_records(
        [NewsRecord(source="blockbeats", source_id="9", title="币圈", market="crypto",
                    published_at=datetime(2026, 8, 9, 5, 30))],
        datetime(2026, 8, 9, 6, 0))
    row = session.query(NewsItem).one()
    assert row.market == "crypto"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_news_scanner_crypto.py -q`
Expected: FAIL —— `AttributeError: 'NewsScanner' object has no attribute '_macro_only'`

- [ ] **Step 3: 改 NewsScanner**

`__init__` 里 `self.sources.extend(create_rss_sources())` 之后追加：

```python
        # 加密源（web3 二期A）：与宏观源同池采集、同表落库，靠 NewsRecord.market 分流。
        if getattr(config, "CRYPTO_NEWS_ENABLED", False):
            crypto_cfg = getattr(config, "CRYPTO_NEWS_SOURCES", {})
            if crypto_cfg.get("blockbeats", {}).get("enabled") and config.BLOCKBEATS_API_KEY:
                from scanners.sources.blockbeats_source import BlockBeatsSource
                self.sources.append(BlockBeatsSource())
            if crypto_cfg.get("binance_ann", {}).get("enabled"):
                from scanners.sources.binance_ann_source import BinanceAnnouncementSource
                self.sources.append(BinanceAnnouncementSource())
```

加一个静态过滤方法（放在 `_filter_scan_window` 旁边）：

```python
    @staticmethod
    def _macro_only(records: list[NewsRecord]) -> list[NewsRecord]:
        """宏观 scorer 的提示词按宏观冲击校准，喂加密新闻会打歪并污染口径；
        加密线的分由 services/crypto_tagging 用独立口径给（design §2）。"""
        return [r for r in records if getattr(r, "market", "macro") != "crypto"]
```

`scan()` 里打分那段改成只打宏观（保持加密记录在列表里、顺序不变）：

```python
        # 对所有保留新闻补充 DeepSeek V4 价格波动重要性评分；不覆盖源端 importance。
        if self.scorer.enabled:
            macro_records = self._macro_only(all_records)
            scored = {id(r): r for r in self.scorer.enrich_batch(macro_records)}
            all_records = [scored.get(id(r), r) for r in all_records]
```

`_save_records` 建 `NewsItem` 时补一行：

```python
                    traditional_open=market_calendar.is_traditional_open(item_ts),
                    market=getattr(r, "market", "macro"),
```

同段的回补路径 `backfill_missing_history` 若也调 `self.scorer.enrich_batch`，同样套 `self._macro_only(...)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_news_scanner_crypto.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add scanners/news_scanner.py tests/test_news_scanner_crypto.py
git commit -m "feat(crypto): 加密源接入扫描器,跳过宏观打分并落 market"
```

---

### Task 7: 加密打分四件套服务

**Files:**
- Create: `services/crypto_tagging.py`
- Test: `tests/test_crypto_tagging.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""加密打分四件套:重要性+方向+币圈事务判定+提及币种,一次调用落库。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.crypto import NewsCoin
from models.news import NewsItem
from services import crypto_tagging


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _news(s, title, market="crypto"):
    n = NewsItem(timestamp=datetime(2026, 8, 9, 12, 0), source="blockbeats", title=title,
                 language="zh", market=market, traditional_open=False)
    s.add(n); s.commit()
    return n


def test_writes_all_four_fields(session, monkeypatch):
    n = _news(session, "币安将上线 XYZ 永续合约")
    monkeypatch.setattr(crypto_tagging, "_call_crypto_tagger", lambda c: json.dumps(
        {"items": [{"id": n.id, "importance": 7, "direction": "利多",
                    "is_crypto_affair": True, "coins": ["XYZ"], "reason": "上新"}]}))
    assert crypto_tagging.tag_untagged_crypto(session) == 1
    session.refresh(n)
    assert n.llm_importance == 7
    assert n.news_direction == "利多"
    assert n.is_crypto_affair is True
    assert n.tagged_at is not None
    assert n.llm_scored_at is not None
    assert [c.coin for c in session.query(NewsCoin).all()] == ["XYZ"]


def test_macro_passthrough_marked_not_crypto_affair(session, monkeypatch):
    n = _news(session, "美联储维持利率不变")
    monkeypatch.setattr(crypto_tagging, "_call_crypto_tagger", lambda c: json.dumps(
        {"items": [{"id": n.id, "importance": 8, "direction": "中性",
                    "is_crypto_affair": False, "coins": []}]}))
    crypto_tagging.tag_untagged_crypto(session)
    session.refresh(n)
    assert n.is_crypto_affair is False
    assert session.query(NewsCoin).count() == 0


def test_coins_normalized_and_deduped(session, monkeypatch):
    n = _news(session, "SOL 生态")
    monkeypatch.setattr(crypto_tagging, "_call_crypto_tagger", lambda c: json.dumps(
        {"items": [{"id": n.id, "importance": 5, "direction": "中性",
                    "is_crypto_affair": True, "coins": [" sol ", "SOL", "arb", "", 123]}]}))
    crypto_tagging.tag_untagged_crypto(session)
    assert sorted(c.coin for c in session.query(NewsCoin).all()) == ["ARB", "SOL"]


def test_only_crypto_market_selected(session, monkeypatch):
    _news(session, "宏观新闻", market="macro")
    n = _news(session, "币圈新闻")
    seen = []
    monkeypatch.setattr(crypto_tagging, "_call_crypto_tagger",
                        lambda c: seen.append(c) or json.dumps(
                            {"items": [{"id": n.id, "importance": 5, "direction": "中性",
                                        "is_crypto_affair": True, "coins": []}]}))
    crypto_tagging.tag_untagged_crypto(session)
    assert "币圈新闻" in seen[0] and "宏观新闻" not in seen[0]


def test_hallucinated_id_and_bad_enum_dropped(session, monkeypatch):
    n = _news(session, "真新闻")
    monkeypatch.setattr(crypto_tagging, "_call_crypto_tagger", lambda c: json.dumps(
        {"items": [{"id": 999999, "importance": 9, "direction": "利多",
                    "is_crypto_affair": True, "coins": ["FAKE"]},
                   {"id": n.id, "importance": 99, "direction": "暴涨",
                    "is_crypto_affair": True, "coins": []}]}))
    assert crypto_tagging.tag_untagged_crypto(session) == 0
    session.refresh(n)
    assert n.tagged_at is None            # 非法条目不盖章,下轮重试
    assert session.query(NewsCoin).count() == 0


def test_retag_replaces_old_coins(session, monkeypatch):
    n = _news(session, "SOL")
    session.add(NewsCoin(news_id=n.id, coin="OLD")); session.commit()
    monkeypatch.setattr(crypto_tagging, "_call_crypto_tagger", lambda c: json.dumps(
        {"items": [{"id": n.id, "importance": 5, "direction": "中性",
                    "is_crypto_affair": True, "coins": ["SOL"]}]}))
    crypto_tagging.tag_untagged_crypto(session)
    assert [c.coin for c in session.query(NewsCoin).all()] == ["SOL"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_crypto_tagging.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'services.crypto_tagging'`

- [ ] **Step 3: 写服务**

`services/crypto_tagging.py`：

```python
# -*- coding: utf-8 -*-
"""加密新闻打分（web3 二期A design §2）：一次调用出四件套。

与宏观口径**完全分开**：宏观提示词按"对 BTC/纳指的宏观冲击"校准，喂加密新闻会把
"小币上合约"这类币圈关键事打成低分，还会反向污染已校准的宏观标注池。

四件套 = 重要性(对币圈整体 1-10) + 方向(对币圈整体) + 币圈事务判定(语义闸) +
提及币种(B 的归因反查地基)。**不判每个币单独的方向**——归因场景币价自己会说话。
"""
from __future__ import annotations

import json
import re

from loguru import logger
from sqlalchemy.orm import Session

import config
from models.crypto import NewsCoin
from models.news import NewsItem
from services.deepseek_client import call_deepseek_chat
from services.time_utils import utc_now_naive

CRYPTO_TAG_SYSTEM_PROMPT = (
    "你是加密市场新闻标注器。对每条新闻给出四项判断：\n\n"
    "1. importance（1-10 整数）：这条新闻对**加密市场整体**引发可交易价格波动的可能性与强度。\n"
    "   10=极可能立即引发全市场大幅波动（重大监管落地、头部交易所/稳定币暴雷、ETF 重大进展、国家级政策）；\n"
    "   8-9=很可能引发明显波动（头部资产上新/下架、重要机构大额动作、知名协议被盗、宏观政策对加密的直接表态）；\n"
    "   6-7=局部或中等波动（单个项目重大更新、二线资产上所、生态基金、大额解锁）；\n"
    "   4-5=有相关性但通常需其他因素配合；\n"
    "   1-3=噪音、重复、行情回顾、纯观点。\n"
    "2. direction：相对**加密市场整体**的应然影响，三选一：利多 / 利空 / 中性。\n"
    "3. is_crypto_affair（true/false）：这条新闻本身是不是**加密行业内部的事**。\n"
    "   加密媒体常转载纯宏观新闻（美联储决议、CPI、地缘冲突、美股）——那些一律 false；\n"
    "   加密行业自己的监管/ETF/交易所/协议/项目/链上/融资事件 → true。\n"
    "4. coins：新闻**实际在讨论**的加密资产代码列表，大写，如 [\"BTC\",\"SOL\"]。\n"
    "   只填真正被讨论的标的，不填顺带提及的背景资产；没有就给 []。\n"
    "   用交易所通用代码（比特币→BTC、以太坊→ETH）；拿不准代码的项目不要硬编。\n\n"
    "只返回 JSON，不要 Markdown：\n"
    '{"items": [{"id": int, "importance": 1-10, "direction": "利多", '
    '"is_crypto_affair": true, "coins": ["BTC"], "reason": "不超过40字"}]}\n'
    "每条输入新闻在 items 里有且仅有一项，id 严格对应输入。"
)

# 版本戳：每次实质修改提示词时更新（与挂接侧同款约定）。
CRYPTO_TAG_PROMPT_VERSION = "crypto-tag-v1-20260809"

_COIN_RE = re.compile(r"^[A-Z0-9]{2,15}$")


def _build_payload(news_list: list[NewsItem]) -> str:
    items = [{
        "id": n.id,
        "source": n.source,
        "title": (n.title or "")[:160],
        "content": (n.content or "")[:200],
    } for n in news_list]
    return f"共 {len(items)} 条新闻。\n{json.dumps({'news': items}, ensure_ascii=False)}"


def _call_crypto_tagger(user_content: str) -> str:
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，无法打加密标")
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": CRYPTO_TAG_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 4000,
        "temperature": 0,
    }
    result = call_deepseek_chat(
        payload, api_key=config.DEEPSEEK_API_KEY,
        timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, config.DEEPSEEK_READ_TIMEOUT),
        http_error_prefix="DeepSeek 加密打标返回", error_preview_chars=200,
        normalize_error_newlines=False,
    )
    if not result.content:
        raise RuntimeError("DeepSeek 加密打标返回空 content")
    return result.content


def _normalize_coins(raw) -> list[str]:
    """归一化成大写代码并去重保序；非字符串/异常长度一律丢弃（不劳模型兜底）。"""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        code = item.strip().upper()
        if _COIN_RE.match(code) and code not in out:
            out.append(code)
    return out


def _parse_response(raw: str, valid_ids: set[int]) -> dict[int, dict]:
    """防幻觉：id 必须在本批、importance 必须 1-10 整数、direction 必须合法枚举、
    is_crypto_affair 必须是布尔。任一不合法整条丢弃（不盖章，下轮重试）。"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            raise ValueError(f"加密打标返回非 JSON: {text[:200]}")
        data = json.loads(m.group(0))
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("加密打标返回缺少 items 列表")

    out: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            nid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if nid not in valid_ids:
            continue
        try:
            importance = int(item.get("importance"))
        except (TypeError, ValueError):
            continue
        if not 1 <= importance <= 10:
            continue
        direction = item.get("direction")
        if direction not in config.NEWS_DIRECTIONS:
            continue
        affair = item.get("is_crypto_affair")
        if not isinstance(affair, bool):
            continue
        reason = item.get("reason")
        out[nid] = {
            "importance": importance,
            "direction": direction,
            "is_crypto_affair": affair,
            "coins": _normalize_coins(item.get("coins")),
            "reason": (str(reason)[:200] if reason else None),
        }
    return out


def tag_crypto_batch(session: Session, news_list: list[NewsItem]) -> int:
    """对一批加密新闻打四件套并落库，返回成功条数。"""
    news_list = [n for n in news_list if n is not None]
    if not news_list:
        return 0
    parsed = _parse_response(_call_crypto_tagger(_build_payload(news_list)),
                             {int(n.id) for n in news_list})
    now = utc_now_naive()
    by_id = {int(n.id): n for n in news_list}
    for nid, tags in parsed.items():
        n = by_id.get(nid)
        if n is None:
            continue
        n.llm_importance = tags["importance"]
        n.llm_importance_reason = tags["reason"]
        n.llm_model = config.DEEPSEEK_MODEL
        n.llm_scored_at = now
        n.news_direction = tags["direction"]
        n.is_crypto_affair = tags["is_crypto_affair"]
        n.tagged_at = now
        # 币种整组替换：重打标时旧行必须清掉，否则残留旧判定
        session.query(NewsCoin).filter(NewsCoin.news_id == nid).delete(synchronize_session=False)
        for coin in tags["coins"]:
            session.add(NewsCoin(news_id=nid, coin=coin))
    session.commit()
    return len(parsed)


def tag_untagged_crypto(session: Session, limit: int = 200,
                        batch_size: int | None = None) -> int:
    """给未打标的加密新闻分片打四件套。加密线不看 traditional_open（7×24 市场）。"""
    batch_size = int(batch_size or config.DEEPSEEK_BATCH_SIZE)
    todo = (session.query(NewsItem)
            .filter(NewsItem.market == "crypto", NewsItem.tagged_at.is_(None))
            .order_by(NewsItem.timestamp.desc())
            .limit(max(1, limit)).all())
    total = 0
    for i in range(0, len(todo), batch_size):
        chunk = todo[i:i + batch_size]
        try:
            total += tag_crypto_batch(session, chunk)
        except Exception as exc:            # 单片失败不阻断后续，不盖章下轮重试
            logger.error(f"[CryptoTag] 分片打标失败（{len(chunk)} 条）: {exc}")
    return total
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_crypto_tagging.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add services/crypto_tagging.py tests/test_crypto_tagging.py
git commit -m "feat(crypto): 加密打分四件套服务(重要性/方向/币圈事务/币种)"
```

---

### Task 8: 接入 5 分钟 tick

**Files:**
- Modify: `services/scan_runtime.py`
- Test: `tests/test_scan_runtime_crypto.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""加密打标必须挂进 tick,且与打标同款守卫(无 key/开关关静默跳过,异常自吞)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from services import scan_runtime


def test_skipped_when_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(config, "CRYPTO_NEWS_ENABLED", False)
    monkeypatch.setattr("services.crypto_tagging.tag_untagged_crypto",
                        lambda s, **kw: calls.append(1))
    scan_runtime._tag_crypto_news()
    assert calls == []


def test_skipped_without_api_key(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(config, "CRYPTO_NEWS_ENABLED", True)
    monkeypatch.setattr("services.crypto_tagging.tag_untagged_crypto",
                        lambda s, **kw: calls.append(1))
    scan_runtime._tag_crypto_news()
    assert calls == []


def test_exception_swallowed(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(config, "CRYPTO_NEWS_ENABLED", True)

    class FakeSession:
        def close(self): pass

    monkeypatch.setattr(scan_runtime, "get_session", lambda: FakeSession())
    def boom(session, **kw):
        raise RuntimeError("接口挂了")
    monkeypatch.setattr("services.crypto_tagging.tag_untagged_crypto", boom)
    scan_runtime._tag_crypto_news()          # 不抛=本轮扫描不受影响
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_scan_runtime_crypto.py -q`
Expected: FAIL —— `AttributeError: module 'services.scan_runtime' has no attribute '_tag_crypto_news'`

- [ ] **Step 3: 加 tick 函数**

`services/scan_runtime.py` 在 `_rescore_unscored_news` 之后加：

```python
def _tag_crypto_news() -> None:
    """加密新闻打四件套（web3 二期A design §2）。放在挂接之前：本轮打上的
    is_crypto_affair 当轮就参与加密语义闸。守卫与宏观打标同款，异常自吞。"""
    if not getattr(config, "DEEPSEEK_API_KEY", ""):
        return
    if not getattr(config, "CRYPTO_NEWS_ENABLED", False):
        return
    from services.crypto_tagging import tag_untagged_crypto
    session = get_session()
    try:
        tagged = tag_untagged_crypto(session)
        if tagged:
            logger.info(f"[CryptoTag] 本轮打标 {tagged} 条")
    except Exception as exc:
        logger.exception(f"[CryptoTag] 打标失败，不影响本轮扫描: {exc}")
    finally:
        session.close()
```

在 `run_scan_once()` 里 `_rescore_unscored_news()` 与 `_link_new_news()` 之间插一行调用：

```python
    _tag_crypto_news()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_scan_runtime_crypto.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add services/scan_runtime.py tests/test_scan_runtime_crypto.py
git commit -m "feat(crypto): 加密打标接入 5 分钟 tick"
```

---

### Task 9: 加密事件挂接（语义闸）

**Files:**
- Modify: `services/event_linking.py`
- Modify: `services/scan_runtime.py`（`_link_new_news` 加第二次调用）
- Test: `tests/test_event_linking_crypto.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""加密挂接:语义闸(is_crypto_affair)取代分数闸,且两条线的池子互不越界。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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


def _news(s, title, market="crypto", score=3, affair=True):
    n = NewsItem(timestamp=datetime(2026, 8, 9, 12, 0), source="blockbeats", title=title,
                 language="zh", llm_importance=score, market=market,
                 is_crypto_affair=affair, tagged_at=datetime(2026, 8, 9, 12, 1))
    s.add(n); s.commit()
    return n


def _event(s, name, event_type="crypto"):
    e = ResearchEvent(name=name, event_type=event_type, status="active")
    s.add(e); s.commit()
    return e


def test_low_score_crypto_affair_still_calls_model(session, monkeypatch):
    """小币新闻分数天然低——加密线不设分数闸,3 分照样进模型(design §3)。"""
    e = _event(session, "某小币生态")
    n = _news(session, "XYZ 上线新功能", score=3, affair=True)
    monkeypatch.setattr(event_linking, "_call_linker", lambda c: json.dumps(
        {"items": [{"id": n.id, "event_id": e.id, "confidence": 0.9}]}))
    stats = event_linking.link_unprocessed(session, market="crypto")
    assert stats["called"] == 1
    assert stats["linked"] == 1


def test_non_crypto_affair_stamped_without_call(session, monkeypatch):
    """加密源转载的纯宏观新闻:语义闸拦下,零调用盖章(不入池≠丢弃)。"""
    _event(session, "某事件")
    n = _news(session, "美联储维持利率不变", score=9, affair=False)
    monkeypatch.setattr(event_linking, "_call_linker",
                        lambda c: pytest.fail("非币圈事务不该进模型"))
    stats = event_linking.link_unprocessed(session, market="crypto")
    assert stats["called"] == 0
    assert stats["processed"] == 1
    session.refresh(n)
    assert n.event_linked_at is not None


def test_macro_pool_and_crypto_pool_do_not_mix(session, monkeypatch):
    macro_evt = _event(session, "宏观事件", event_type="macro")
    crypto_evt = _event(session, "加密事件", event_type="crypto")
    crypto_news = _news(session, "币安上新", score=3, affair=True)
    seen_pool = {}
    monkeypatch.setattr(event_linking, "_call_linker", lambda c: seen_pool.setdefault("payload", c) or json.dumps(
        {"items": [{"id": crypto_news.id, "event_id": crypto_evt.id, "confidence": 0.9}]}))
    event_linking.link_unprocessed(session, market="crypto")
    assert "加密事件" in seen_pool["payload"]
    assert "宏观事件" not in seen_pool["payload"]


def test_macro_run_ignores_crypto_news(session, monkeypatch):
    _event(session, "宏观事件", event_type="macro")
    _news(session, "币圈新闻", market="crypto", score=9, affair=True)
    monkeypatch.setattr(event_linking, "_call_linker",
                        lambda c: pytest.fail("宏观轮不该看加密新闻"))
    stats = event_linking.link_unprocessed(session, market="macro")
    assert stats["called"] == 0


def test_untagged_crypto_news_not_selected(session, monkeypatch):
    """未打标 = is_crypto_affair 还没判,不能靠 NULL 蒙混过闸。"""
    _event(session, "加密事件")
    n = NewsItem(timestamp=datetime(2026, 8, 9, 12, 0), source="blockbeats",
                 title="还没打标", language="zh", market="crypto")
    session.add(n); session.commit()
    monkeypatch.setattr(event_linking, "_call_linker",
                        lambda c: pytest.fail("未打标不该进模型"))
    stats = event_linking.link_unprocessed(session, market="crypto")
    assert stats["processed"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_event_linking_crypto.py -q`
Expected: FAIL —— `TypeError: link_unprocessed() got an unexpected keyword argument 'market'`

- [ ] **Step 3: 改 event_linking**

在 `LINK_PROMPT_VERSION` 之后加加密线提示词：

```python
CRYPTO_LINK_SYSTEM_PROMPT = (
    "你是加密市场研究助理。下面给你一份【活跃事件池】和一批加密新闻，判断每条新闻"
    "是否是池中某个事件的新证据。\n"
    "规则：\n"
    "- 只做归类，不评判新闻重要性；新闻与所有事件都无关 → event_id 给 null（不挂）。\n"
    "- 不确定就不挂：只有主体（项目/协议/交易所/资产）与事态确实属于该事件才挂。\n"
    "- 同一个币可能同时有多条事件线（如解锁与生态基金是两件事），按事态归属，别只看币名。\n"
    "- 转载/同一起源的重复报道照挂（时间轴自会显示簇拥，人工把关兜底）。\n"
    "只返回 JSON，不要 Markdown：\n"
    '{"items": [{"id": 新闻id, "event_id": 事件编号或null, "confidence": 0.9}, ...]}\n'
    "confidence 三档：0.9=明确属于；0.65=大概率属于；0.3=勉强（倾向不挂）。\n"
    "每条输入新闻在 items 里有且仅有一项，id 严格对应输入；event_id 必须是池中编号。"
)
CRYPTO_LINK_PROMPT_VERSION = "crypto-link-v1-20260809"
```

`_active_events` 加类型参数：

```python
def _active_events(session: Session, event_type: str = "macro") -> list[ResearchEvent]:
    return (session.query(ResearchEvent)
            .filter(ResearchEvent.status == "active",
                    ResearchEvent.event_type == event_type)
            .order_by(ResearchEvent.id.asc()).all())
```

加市场↔类型映射与加密闸：

```python
# 市场 → 事件类型（两条线各自的池子，互不越界；人工跨挂在 event_pool.py，不受此限）
MARKET_EVENT_TYPE = {"macro": "macro", "crypto": "crypto"}


def passes_crypto_gate(news: NewsItem) -> bool:
    """加密线的闸是**语义闸**（design §3）：加密源转载的纯宏观新闻不进加密事件池。
    分数闸在加密线不设——小币新闻分数天然低，用分数拦等于把二期B 的原料掐掉。"""
    return news.is_crypto_affair is True
```

`_call_linker` 加提示词参数：

```python
def _call_linker(user_content: str, system_prompt: str = LINK_SYSTEM_PROMPT) -> str:
```

（函数体内 `{"role": "system", "content": system_prompt}`。）

`link_unprocessed` 改造为按 market 分流：

```python
def link_unprocessed(session: Session, limit: int = 200,
                     batch_size: int | None = None, market: str = "macro") -> dict:
    """tick 入口(spec §4.1)：处理游标为空的新闻，四种结果都盖章。
    market="macro" 走分数闸+关键词免闸；market="crypto" 走语义闸（design §3）。"""
    stats = {"processed": 0, "linked": 0, "called": 0}
    is_crypto = market == "crypto"
    events = _active_events(session, MARKET_EVENT_TYPE.get(market, "macro"))
    if not events:
        return stats                     # 池空整段跳过，零调用、游标不动
    keywords = _keyword_pool(events)
    todo = (session.query(NewsItem)
            .filter(NewsItem.tagged_at.isnot(None), NewsItem.event_linked_at.is_(None),
                    NewsItem.market == market)
            .order_by(NewsItem.timestamp.desc())
            .limit(max(1, limit)).all())
    now = utc_now_naive()
    to_llm: list[NewsItem] = []
    for n in todo:
        gate_ok = passes_crypto_gate(n) if is_crypto else passes_gate(n, keywords)
        if _is_blacklisted(n) or not gate_ok:
            n.event_linked_at = now      # 不够格/黑名单：盖章零调用
            stats["processed"] += 1
        else:
            to_llm.append(n)
    session.commit()
    if not to_llm:
        return stats
    system_prompt = CRYPTO_LINK_SYSTEM_PROMPT if is_crypto else LINK_SYSTEM_PROMPT
    prompt_version = CRYPTO_LINK_PROMPT_VERSION if is_crypto else LINK_PROMPT_VERSION
    pool_summary = _pool_summary(session, events)
    valid_event_ids = {int(e.id) for e in events}
    batch_size = int(batch_size or config.DEEPSEEK_BATCH_SIZE)
    for i in range(0, len(to_llm), batch_size):
        chunk = to_llm[i:i + batch_size]
        stats["called"] += len(chunk)
        try:
            raw = _call_linker(_build_link_payload(pool_summary, chunk), system_prompt)
            parsed = _parse_link_response(raw, {int(n.id) for n in chunk}, valid_event_ids)
        except Exception as exc:         # 整批失败：不盖游标，下轮重试
            logger.error(f"[EventLink] 分片挂接失败({len(chunk)} 条): {exc}")
            continue
        now = utc_now_naive()
        by_id = {int(n.id): n for n in chunk}
        for nid, r in parsed.items():
            n = by_id.get(nid)
            if n is None:
                continue
            if r["event_id"] is not None:
                _create_auto_link(session, r["event_id"], nid, r["confidence"], prompt_version)
                stats["linked"] += 1
            n.event_linked_at = now      # 只有合法解析条目盖章(含"不挂")
            stats["processed"] += 1
        session.commit()
    return stats
```

`_create_auto_link` 收版本参数：

```python
def _create_auto_link(session: Session, event_id: int, news_id: int,
                      confidence: float | None,
                      prompt_version: str = LINK_PROMPT_VERSION) -> ResearchEventLink:
```

（函数体内 `prompt_version=prompt_version`。）

`clear_link_cursor`（回扫）同样按 market 分流：

```python
def clear_link_cursor(session: Session, hours: float, now: datetime | None = None,
                      market: str = "macro") -> int:
    now = now or utc_now_naive()
    is_crypto = market == "crypto"
    events = _active_events(session, MARKET_EVENT_TYPE.get(market, "macro"))
    keywords = _keyword_pool(events)
    cutoff = now - timedelta(hours=hours)
    linked_ids = {row[0] for row in session.query(ResearchEventLink.news_id)
                  .filter(ResearchEventLink.detached.is_(False)).all()}
    rows = (session.query(NewsItem)
            .filter(NewsItem.timestamp >= cutoff,
                    NewsItem.market == market,
                    NewsItem.event_linked_at.isnot(None)).all())
    cleared = 0
    for n in rows:
        if int(n.id) in linked_ids:
            continue
        gate_ok = passes_crypto_gate(n) if is_crypto else passes_gate(n, keywords)
        if _is_blacklisted(n) or not gate_ok:
            continue
        n.event_linked_at = None
        cleared += 1
    session.commit()
    return cleared
```

- [ ] **Step 4: tick 加第二次调用**

`services/scan_runtime.py::_link_new_news` 里，宏观调用之后追加：

```python
        stats = link_unprocessed(session, limit=200)
        if stats["processed"] or stats["linked"]:
            logger.info(f"[EventLink] 本轮盖章 {stats['processed']} 条,新挂 {stats['linked']} 条")
        if getattr(config, "CRYPTO_NEWS_ENABLED", False):
            c_stats = link_unprocessed(session, limit=200, market="crypto")
            if c_stats["processed"] or c_stats["linked"]:
                logger.info(f"[EventLink/crypto] 本轮盖章 {c_stats['processed']} 条,"
                            f"新挂 {c_stats['linked']} 条")
```

- [ ] **Step 5: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_event_linking_crypto.py tests/test_event_linking.py -q`
Expected: PASS（5 + 18 passed；宏观既有测试必须不受影响）

- [ ] **Step 6: 提交**

```bash
git add services/event_linking.py services/scan_runtime.py tests/test_event_linking_crypto.py
git commit -m "feat(crypto): 加密事件挂接走语义闸,两线池子互不越界"
```

---

### Task 10: 事件池 crypto 类型贯通 + 事件币种派生

**Files:**
- Modify: `services/event_pool.py`
- Modify: `api/routes.py`、`schemas/research.py`
- Test: `tests/test_event_pool_crypto.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""事件池 crypto 类型:立案带类型、列表按类型筛、币种读时派生。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.crypto import NewsCoin
from models.news import NewsItem
from services import event_pool


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _crypto_news(s, title, coins=()):
    n = NewsItem(timestamp=datetime(2026, 8, 9, 12, 0), source="blockbeats", title=title,
                 language="zh", market="crypto", is_crypto_affair=True,
                 tagged_at=datetime(2026, 8, 9, 12, 1))
    s.add(n); s.commit()
    for c in coins:
        s.add(NewsCoin(news_id=n.id, coin=c))
    s.commit()
    return n


def test_create_event_with_crypto_type(session):
    n = _crypto_news(session, "币安上新 XYZ")
    evt = event_pool.create_event(session, name="XYZ 上所", news_ids=[n.id],
                                  created_from="manual", event_type="crypto")
    assert evt.event_type == "crypto"


def test_list_events_filtered_by_type(session):
    n = _crypto_news(session, "币安上新")
    event_pool.create_event(session, name="加密事件", news_ids=[n.id],
                            created_from="manual", event_type="crypto")
    event_pool.create_event(session, name="宏观事件", news_ids=[n.id],
                            created_from="manual", event_type="macro")
    crypto = event_pool.list_events(session, event_type="crypto")
    assert [e["name"] for e in crypto] == ["加密事件"]


def test_event_coins_derived_from_timeline(session):
    a = _crypto_news(session, "SOL 生态基金", coins=["SOL"])
    b = _crypto_news(session, "SOL 与 ARB 跨链", coins=["SOL", "ARB"])
    evt = event_pool.create_event(session, name="SOL 生态", news_ids=[a.id, b.id],
                                  created_from="manual", event_type="crypto")
    rows = event_pool.list_events(session, event_type="crypto")
    assert rows[0]["coins"] == ["ARB", "SOL"]      # 并集,排序稳定


def test_macro_event_has_no_coins(session):
    n = _crypto_news(session, "随便", coins=["BTC"])
    event_pool.create_event(session, name="宏观事件", news_ids=[n.id],
                            created_from="manual", event_type="macro")
    rows = event_pool.list_events(session, event_type="macro")
    assert rows[0]["coins"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_event_pool_crypto.py -q`
Expected: FAIL —— `create_event() got an unexpected keyword argument 'event_type'`

- [ ] **Step 3: 改 event_pool**

先读现有 `create_event` / `list_events` 签名，然后：
- `create_event(...)` 加 `event_type: str = "macro"` 参数并传给 `ResearchEvent(...)`；
- `list_events(...)` 加 `event_type: str | None = None`，非空时加 `.filter(ResearchEvent.event_type == event_type)`；
- 列表每行加 `coins` 字段，仅 crypto 类型事件计算（宏观恒 `[]`）：

```python
def _event_coins(session: Session, event_id: int) -> list[str]:
    """事件涉及哪些币 = 时间轴新闻提及币种的并集（design §4：读时派生，不落库）。"""
    rows = (session.query(NewsCoin.coin)
            .join(ResearchEventLink, ResearchEventLink.news_id == NewsCoin.news_id)
            .filter(ResearchEventLink.event_id == event_id,
                    ResearchEventLink.detached.is_(False))
            .distinct().all())
    return sorted({r[0] for r in rows})
```

- [ ] **Step 4: 接口与类型暴露**

`schemas/research.py` 的事件项 schema 加两个字段：

```python
    event_type: str = "macro"
    coins: list[str] = []
```

`api/routes.py` 的事件列表路由加类型筛选参数、立案请求体加类型：

```python
@router.get("/research/events", response_model=ResearchEventsResponse)
def research_events(status: str | None = Query(default=None),
                    q: str | None = Query(default=None),
                    event_type: str | None = Query(default=None),
                    db: Session = Depends(get_db)) -> ResearchEventsResponse:
```

（转调 `event_pool.list_events(db, status=status, q=q, event_type=event_type)`；`ResearchEventCreateRequest` 加 `event_type: str = "macro"` 并透传。）

- [ ] **Step 5: 重新生成前端类型**

Run: `cd frontend && npm run generate:api-types`
Expected: `frontend/src/api/types.ts` 出现 `event_type` 与 `coins` 字段

- [ ] **Step 6: 跑测试确认通过**

Run: `D:/anaconda/python.exe -m pytest tests/test_event_pool_crypto.py tests/test_openapi_types.py -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add services/event_pool.py api/routes.py schemas/research.py frontend/src/api/types.ts tests/test_event_pool_crypto.py
git commit -m "feat(crypto): 事件池 crypto 类型贯通 + 事件币种读时派生"
```

---

### Task 11: 加密快讯接口

**Files:**
- Modify: `services/news_service.py`、`api/routes.py`、`schemas/news.py`
- Test: `tests/test_crypto_news_api.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""加密快讯接口:只回 market=crypto,且带币种与币圈事务标记。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.crypto import NewsCoin
from models.news import NewsItem
from services import news_service
from services.time_utils import utc_now_naive


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _news(s, title, market="crypto", affair=True, coins=(), hours_ago=1):
    n = NewsItem(timestamp=utc_now_naive() - timedelta(hours=hours_ago),
                 source="blockbeats", title=title, language="zh", market=market,
                 is_crypto_affair=affair, llm_importance=6)
    s.add(n); s.commit()
    for c in coins:
        s.add(NewsCoin(news_id=n.id, coin=c))
    s.commit()
    return n


def test_only_crypto_market_returned(session):
    _news(session, "币圈新闻")
    _news(session, "宏观新闻", market="macro")
    resp = news_service.get_crypto_news(session)
    assert [i.title for i in resp.items] == ["币圈新闻"]


def test_coins_and_affair_exposed(session):
    _news(session, "SOL 生态基金", coins=["SOL", "ARB"])
    item = news_service.get_crypto_news(session).items[0]
    assert item.coins == ["ARB", "SOL"]
    assert item.is_crypto_affair is True


def test_affair_only_filter(session):
    _news(session, "币圈事务", affair=True)
    _news(session, "转载宏观", affair=False)
    resp = news_service.get_crypto_news(session, affair_only=True)
    assert [i.title for i in resp.items] == ["币圈事务"]


def test_coin_filter(session):
    _news(session, "SOL 新闻", coins=["SOL"])
    _news(session, "ARB 新闻", coins=["ARB"])
    resp = news_service.get_crypto_news(session, coin="sol")
    assert [i.title for i in resp.items] == ["SOL 新闻"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/anaconda/python.exe -m pytest tests/test_crypto_news_api.py -q`
Expected: FAIL —— `AttributeError: module 'services.news_service' has no attribute 'get_crypto_news'`

- [ ] **Step 3: 加 schema 字段**

`schemas/news.py` 的 `NewsItemSchema` 加两个可选字段：

```python
    is_crypto_affair: bool | None = None
    coins: list[str] = []
```

`schemas/news.py` 加加密源枚举响应（复用 `NewsSourceMeta`，无需新类型）。

- [ ] **Step 4: 写服务**

`services/news_service.py` 追加：

```python
def _enabled_crypto_sources() -> list[str]:
    return [k for k, v in getattr(config, "CRYPTO_NEWS_SOURCES", {}).items()
            if v.get("enabled")]


def list_crypto_sources() -> list[NewsSourceMeta]:
    return [NewsSourceMeta(key=k, name=v.get("name") or k.upper(),
                           language=v.get("language", "zh"))
            for k, v in getattr(config, "CRYPTO_NEWS_SOURCES", {}).items()
            if v.get("enabled")]


def get_crypto_news(
    session: Session,
    sources: list[str] | None = None,
    hours_back: int = 24,
    min_llm_importance: int = 0,
    affair_only: bool = False,
    coin: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> NewsResponse:
    """加密快讯（web3 二期A design §4）：独立页面，与宏观新闻页互不干扰。
    默认 min_llm_importance=0——加密线不按分数拦（小币新闻分数天然低）。"""
    from models.crypto import NewsCoin

    page, page_size = clamp_page(page, page_size)
    hours_back = max(1, min(int(hours_back or 24), 24 * 30))
    cutoff = utc_now_naive() - timedelta(hours=hours_back)

    query = (session.query(NewsItem)
             .filter(NewsItem.market == "crypto", NewsItem.timestamp >= cutoff))
    query = query.filter(NewsItem.source.in_(sources or _enabled_crypto_sources()))
    if affair_only:
        query = query.filter(NewsItem.is_crypto_affair.is_(True))
    if coin:
        code = coin.strip().upper()
        query = query.filter(NewsItem.id.in_(
            session.query(NewsCoin.news_id).filter(NewsCoin.coin == code)))
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(or_(NewsItem.title.ilike(like), NewsItem.content.ilike(like)))

    candidates = query.order_by(NewsItem.timestamp.desc()).limit(5000).all()
    filtered = [i for i in candidates
                if passes_default_importance_filter(i, min_llm_importance)]
    total = len(filtered)
    start = (page - 1) * page_size
    page_items = filtered[start:start + page_size]

    coin_map: dict[int, list[str]] = {}
    if page_items:
        rows = (session.query(NewsCoin.news_id, NewsCoin.coin)
                .filter(NewsCoin.news_id.in_([i.id for i in page_items])).all())
        for news_id, code in rows:
            coin_map.setdefault(news_id, []).append(code)

    items = []
    for item in page_items:
        schema = to_news_schema(item)
        schema.is_crypto_affair = item.is_crypto_affair
        schema.coins = sorted(coin_map.get(item.id, []))
        items.append(schema)

    return NewsResponse(items=items, total=total, page=page, page_size=page_size,
                        zh_count=sum(1 for i in filtered if i.language == "zh"),
                        en_count=sum(1 for i in filtered if i.language == "en"))
```

- [ ] **Step 5: 加路由**

`api/routes.py` 在 `/news/sources` 之后加：

```python
@router.get("/crypto/news", response_model=NewsResponse)
def crypto_news(sources: list[str] | None = Query(default=None),
                hours_back: int = 24,
                min_llm_importance: int = 0,
                affair_only: bool = False,
                coin: str | None = None,
                search: str | None = None,
                page: int = 1,
                page_size: int = 50,
                db: Session = Depends(get_db)) -> NewsResponse:
    return news_service.get_crypto_news(
        db, sources=_csv_list(sources), hours_back=hours_back,
        min_llm_importance=min_llm_importance, affair_only=affair_only,
        coin=coin, search=search, page=page, page_size=page_size)


@router.get("/crypto/news/sources", response_model=list[NewsSourceMeta])
def crypto_news_sources() -> list[NewsSourceMeta]:
    return news_service.list_crypto_sources()
```

- [ ] **Step 6: 跑测试并重生成类型**

Run: `D:/anaconda/python.exe -m pytest tests/test_crypto_news_api.py -q`
Expected: PASS（4 passed）

Run: `cd frontend && npm run generate:api-types`
Expected: types.ts 出现 `coins` / `is_crypto_affair`

- [ ] **Step 7: 提交**

```bash
git add services/news_service.py api/routes.py schemas/news.py frontend/src/api/types.ts tests/test_crypto_news_api.py
git commit -m "feat(crypto): 加密快讯接口(币种/币圈事务筛选)"
```

---

### Task 12: 加密快讯页面

**Files:**
- Create: `frontend/src/pages/CryptoNewsPage.tsx`
- Modify: `frontend/src/api/client.ts`、`frontend/src/main.tsx`、`frontend/src/components/AppShell.tsx`
- Test: `frontend/src/pages/CryptoNewsPage.test.tsx`（新建）

- [ ] **Step 1: 写失败测试**

```tsx
import { describe, expect, it } from "vitest";
import { coinChips, affairLabel } from "./CryptoNewsPage";
import type { NewsItem } from "../api/types";

const base = { id: 1, source: "blockbeats", title: "t", coins: [] } as unknown as NewsItem;

describe("coinChips", () => {
  it("币种按字母序展示,最多 6 个后折叠计数", () => {
    const chips = coinChips({ ...base, coins: ["SOL", "ARB", "BTC", "ETH", "OP", "TON", "SUI"] });
    expect(chips.shown).toEqual(["ARB", "BTC", "ETH", "OP", "SOL", "SUI"]);
    expect(chips.more).toBe(1);
  });

  it("没有币种时不占位", () => {
    expect(coinChips({ ...base, coins: [] }).shown).toEqual([]);
  });
});

describe("affairLabel", () => {
  it("非币圈事务明示'转载宏观',让不入池有解释", () => {
    expect(affairLabel(false)).toBe("转载宏观");
  });

  it("币圈事务与未判定都不加标签,避免噪音", () => {
    expect(affairLabel(true)).toBe("");
    expect(affairLabel(null)).toBe("");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm test -- src/pages/CryptoNewsPage.test.tsx`
Expected: FAIL —— 找不到模块 `./CryptoNewsPage`

- [ ] **Step 3: 加 API 客户端方法**

`frontend/src/api/client.ts` 在 `newsSources` 之后加：

```typescript
  cryptoNews: (params: {
    sources?: string[];
    hours_back?: number;
    min_llm_importance?: number;
    affair_only?: boolean;
    coin?: string;
    search?: string;
    page?: number;
    page_size?: number;
  }) => request<NewsResponse>(`/crypto/news${buildQuery(params)}`),
  cryptoNewsSources: () => request<NewsSourceMeta[]>("/crypto/news/sources"),
```

- [ ] **Step 4: 写页面**

`frontend/src/pages/CryptoNewsPage.tsx`：结构照抄 `NewsPage.tsx`（筛选条 + 紧凑行 + 分页 + 立案条），差异：
- 数据源换 `api.cryptoNews`，源下拉换 `api.cryptoNewsSources`；
- 筛选项：小时窗、分数（默认「不限」）、`affair_only` 勾选（默认关）、币种输入框、搜索；
- 每行展示币种 chips 与「转载宏观」标记；
- 复用 `TriageBar`，立案时传 `event_type: "crypto"`。

导出两个纯函数供测试：

```tsx
export function coinChips(item: NewsItem): { shown: string[]; more: number } {
  const all = [...(item.coins ?? [])].sort();
  return { shown: all.slice(0, 6), more: Math.max(0, all.length - 6) };
}

export function affairLabel(isCryptoAffair: boolean | null | undefined): string {
  return isCryptoAffair === false ? "转载宏观" : "";
}
```

- [ ] **Step 5: 挂路由与导航**

`frontend/src/main.tsx`：`import { CryptoNewsPage } from "./pages/CryptoNewsPage";` + 路由 `{ path: "crypto-news", element: <CryptoNewsPage /> }`（放在 `news` 之后）。

`frontend/src/components/AppShell.tsx`：`navItems` 在「新闻快讯」之后插入 `{ to: "/crypto-news", label: "加密快讯", icon: Coins }`，并从 `lucide-react` 导入 `Coins`。

- [ ] **Step 6: 跑测试与类型检查**

Run: `cd frontend && npm test`
Expected: 全绿（含新 4 项）

Run: `cd frontend && npx tsc -b`
Expected: 无输出

- [ ] **Step 7: 提交**

```bash
git add frontend/src/pages/CryptoNewsPage.tsx frontend/src/pages/CryptoNewsPage.test.tsx frontend/src/api/client.ts frontend/src/main.tsx frontend/src/components/AppShell.tsx
git commit -m "feat(crypto): 加密快讯页面 + 导航入口"
```

---

### Task 13: 关闭事件提醒留词 + 研究页类型筛选

**Files:**
- Modify: `frontend/src/pages/ResearchPage.tsx`
- Test: `frontend/src/pages/ResearchPage.test.tsx`（追加）

- [ ] **Step 1: 写失败测试**

在 `ResearchPage.test.tsx` 末尾追加：

```tsx
describe("closeEventPrompt", () => {
  it("关键词为空时提醒留沉睡词(design §4:宏观加密一体)", () => {
    const msg = closeEventPrompt(null);
    expect(msg).toContain("沉睡关键词");
  });

  it("已有关键词时不啰嗦", () => {
    expect(closeEventPrompt("霍尔木兹、美伊")).toBe("");
  });
});
```

并在文件顶部 import 里加入 `closeEventPrompt`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm test -- src/pages/ResearchPage.test.tsx`
Expected: FAIL —— `closeEventPrompt is not a function`

- [ ] **Step 3: 实现**

`ResearchPage.tsx` 加导出函数：

```tsx
/** 关闭事件前的留词提醒（design §4）：关键词空 = 沉睡监听失灵，旧事重提再也不会响。 */
export function closeEventPrompt(gateKeywords: string | null | undefined): string {
  return (gateKeywords ?? "").trim()
    ? ""
    : "该事件还没有沉睡关键词——关闭后「旧事重提」将无法唤醒它。建议先补关键词再关闭。";
}
```

「关闭事件」菜单项改为先提醒：

```tsx
          {event.status === "active" ? (
            <MenuItem onClick={() => {
              const warn = closeEventPrompt(event.gate_keywords);
              if (warn && !window.confirm(`${warn}\n\n仍要关闭吗？`)) return;
              const reason = window.prompt("关闭原因", "");
              if (reason !== null) patchEvent.mutate({ status: "closed", closed_reason: reason });
            }}>关闭事件</MenuItem>
          ) : (
```

事件列表加类型筛选（宏观/加密/全部）：`ResearchPage` 顶部加 `const [type, setType] = useState<"macro" | "crypto" | "">("macro")`，查询串进 `api.researchEvents({ ...(type ? { event_type: type } : {}), ...})`，并把 `type` 加进 `queryKey`。

- [ ] **Step 4: 跑测试与类型检查**

Run: `cd frontend && npm test && npx tsc -b`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/ResearchPage.tsx frontend/src/pages/ResearchPage.test.tsx
git commit -m "feat(research): 关闭事件提醒留沉睡词 + 事件列表按类型筛选"
```

---

### Task 14: 端到端联调与部署

- [ ] **Step 1: 全量回归**

Run: `D:/anaconda/python.exe -m pytest -q`
Expected: 除 Windows 编码已知项外全绿

Run: `cd frontend && npm test && npx tsc -b`
Expected: 全绿

- [ ] **Step 2: 本地起服务实测采集器**

Run: `D:/anaconda/python.exe -c "from scanners.sources.binance_ann_source import BinanceAnnouncementSource as S; rs=S().fetch(); print(len(rs), rs[0].title[:60], rs[0].published_at)"`
Expected: 打印条数与最新一条公告（BlockBeats 需要 key，本地 .env 无 key 时跳过）

- [ ] **Step 3: 部署**

```bash
ssh mmon "cd /opt/market_monitor && ./deploy.sh"
```

- [ ] **Step 4: 线上验收（跑完等 10 分钟再查）**

```bash
ssh mmon "cd /opt/market_monitor && .venv/bin/python -c \"
import sqlite3
db = sqlite3.connect('file:market_monitor.db?mode=ro', uri=True)
print('crypto news:', db.execute('SELECT COUNT(*) FROM news_items WHERE market=\\\"crypto\\\"').fetchone()[0])
print('tagged:', db.execute('SELECT COUNT(*) FROM news_items WHERE market=\\\"crypto\\\" AND tagged_at IS NOT NULL').fetchone()[0])
print('affairs:', db.execute('SELECT is_crypto_affair, COUNT(*) FROM news_items WHERE market=\\\"crypto\\\" GROUP BY 1').fetchall())
print('coins:', db.execute('SELECT COUNT(*) FROM news_coins').fetchone()[0])
\""
```

Expected: crypto news > 0、tagged 追平、affairs 两档都有、coins > 0

---

## Self-Review

**Spec coverage:** §1 数据接入=Task 3/4/5/6；§2 打分四件套=Task 7；§3 语义闸=Task 9；§4 事件池与页面=Task 10/12/13；§5 给 B 的预埋=Task 1（market）+ Task 2（news_coins）+ Task 10（币种派生）；§6 负面清单已遵守（无 WeCom、无叙事映射、无历史回填、无 tradable 落库）；§7 风险=Task 14 线上验收；§8 验收=Task 14。

**偏离设计稿一处（更优解，已在 Task 2 注释说明）：** 设计稿写「币安可交易」是入库时打的查表标记，实施改为**不落库、读时派生**——上新/下架随时变，冻结成列会过期；这与一期「读时派生」铁律一致，也让打标写路径不依赖 BMAC pivot 文件。

**Type consistency:** `market` 取值全程 `"macro"|"crypto"`；`event_type` 同名同值；`NewsRecord.market` 与 `NewsItem.market` 同名；`coins` 在 schema/服务/前端统一为 `list[str]` 升序。
