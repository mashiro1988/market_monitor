# 预测市场 Grid 重设计 + 跟踪管理 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把预测市场页面改成 2 列 grid，并允许通过 UI 添加/删除/启停跟踪的市场（slug）和家族（tag），把跟踪列表从 `config.py` 迁移到 SQLite。

**Architecture:** 新增一张 `tracked_markets` 表 + CRUD REST 端点 + 启动 seed；扫描器从 DB 读跟踪列表（保留 attribute override 以兼容现有测试）；前端拆出 `<PredictionCard>` 和 `<TrackedMarketsPanel>` 两个组件，重写 `PredictionsPage` 用 grid 布局。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy (SQLite) / pytest，前端 React 18 + TypeScript + TanStack Query + Vite + Recharts。

**Spec:** `docs/superpowers/specs/2026-05-05-predictions-grid-redesign-design.md`

---

## Task 1: TrackedMarket 模型

**Files:**
- Create: `models/tracked_market.py`
- Modify: `models/__init__.py`

- [ ] **Step 1: 写模型**

`models/tracked_market.py`：

```python
"""跟踪的预测市场列表（slug 或 tag），由 UI 维护."""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, UniqueConstraint
from database import Base


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TrackedMarket(Base):
    __tablename__ = "tracked_markets"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String(16), nullable=False)        # "slug" | "tag"
    identifier = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utc_naive_now)

    __table_args__ = (
        UniqueConstraint("kind", "identifier", name="uq_tracked_kind_identifier"),
    )
```

- [ ] **Step 2: 注册到 models/__init__.py**

把第 6 行附近改成：

```python
from models.price import PriceSnapshot
from models.news import NewsItem, NewsPriceAnnotation
from models.prediction import PredictionMarket
from models.alert_log import AlertLog
from models.tracked_market import TrackedMarket  # NEW
```

- [ ] **Step 3: 验证**

```bash
python -c "from models.tracked_market import TrackedMarket; print(TrackedMarket.__tablename__)"
```
Expected: `tracked_markets`

- [ ] **Step 4: Commit**

```bash
git add models/tracked_market.py models/__init__.py
git commit -m "feat(predictions): add TrackedMarket model"
```

---

## Task 2: Seed function

**Files:**
- Modify: `database.py`
- Test: `tests/test_tracked_markets_seed.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_tracked_markets_seed.py`：

```python
"""Tests for tracked_markets seeding from config."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.tracked_market import TrackedMarket
from database import seed_tracked_markets


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_seed_inserts_slugs_and_tags(session):
    seed_tracked_markets(
        session,
        slugs=["how-many-fed-rate-cuts-in-2026", "fed-decision-in-june-825"],
        tags=["fed", "inflation"],
    )

    rows = session.query(TrackedMarket).order_by(TrackedMarket.kind, TrackedMarket.identifier).all()
    assert len(rows) == 4
    kinds = {(r.kind, r.identifier) for r in rows}
    assert ("slug", "how-many-fed-rate-cuts-in-2026") in kinds
    assert ("slug", "fed-decision-in-june-825") in kinds
    assert ("tag", "fed") in kinds
    assert ("tag", "inflation") in kinds
    assert all(r.enabled for r in rows)


def test_seed_is_idempotent(session):
    seed_tracked_markets(session, slugs=["foo"], tags=["bar"])
    seed_tracked_markets(session, slugs=["foo", "baz"], tags=["bar"])

    rows = session.query(TrackedMarket).all()
    assert len(rows) == 3  # foo, bar 复用，baz 新增


def test_seed_does_not_overwrite_user_changes(session):
    seed_tracked_markets(session, slugs=["foo"], tags=[])
    row = session.query(TrackedMarket).first()
    row.enabled = False
    row.display_name = "user override"
    session.commit()

    seed_tracked_markets(session, slugs=["foo"], tags=[])
    row = session.query(TrackedMarket).first()
    assert row.enabled is False
    assert row.display_name == "user override"
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_tracked_markets_seed.py -v
```
Expected: ImportError on `seed_tracked_markets`

- [ ] **Step 3: 实现 seed_tracked_markets**

在 `database.py` 末尾追加：

```python
def seed_tracked_markets(session=None, *, slugs: list[str] | None = None, tags: list[str] | None = None):
    """从给定 slug/tag 列表 upsert tracked_markets。已存在的 (kind, identifier) 行跳过，
    不覆盖用户已修改的 enabled / display_name。
    """
    from models.tracked_market import TrackedMarket
    import config

    if slugs is None:
        slugs = list(config.POLYMARKET.get("tracked_slugs", []))
    if tags is None:
        tags = list(config.POLYMARKET.get("tracked_tags", []))

    own_session = session is None
    if own_session:
        session = SessionLocal()

    try:
        existing = {
            (row.kind, row.identifier)
            for row in session.query(TrackedMarket.kind, TrackedMarket.identifier).all()
        }
        for slug in slugs:
            slug = (slug or "").strip()
            if slug and ("slug", slug) not in existing:
                session.add(TrackedMarket(kind="slug", identifier=slug, enabled=True))
        for tag in tags:
            tag = (tag or "").strip()
            if tag and ("tag", tag) not in existing:
                session.add(TrackedMarket(kind="tag", identifier=tag, enabled=True))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        if own_session:
            session.close()
```

- [ ] **Step 4: 在 create_tables 末尾调用 seed**

在 `database.py` 的 `create_tables()` 末尾（`_ensure_sqlite_schema()` 之后）追加：

```python
    seed_tracked_markets()
```

- [ ] **Step 5: 跑测试**

```bash
pytest tests/test_tracked_markets_seed.py -v
```
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add database.py tests/test_tracked_markets_seed.py
git commit -m "feat(predictions): seed tracked_markets from config on init"
```

---

## Task 3: Pydantic Schemas

**Files:**
- Modify: `schemas/predictions.py`

- [ ] **Step 1: 在文件末尾追加**

```python
from typing import Literal


class TrackedMarketSchema(BaseModel):
    id: int
    kind: Literal["slug", "tag"]
    identifier: str
    display_name: str | None = None
    enabled: bool
    notes: str | None = None


class TrackedMarketCreate(BaseModel):
    kind: Literal["slug", "tag"]
    identifier: str
    display_name: str | None = None
    notes: str | None = None


class TrackedMarketUpdate(BaseModel):
    enabled: bool | None = None
    display_name: str | None = None
    notes: str | None = None
```

- [ ] **Step 2: 验证 import**

```bash
python -c "from schemas.predictions import TrackedMarketSchema, TrackedMarketCreate, TrackedMarketUpdate; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add schemas/predictions.py
git commit -m "feat(predictions): tracked-market pydantic schemas"
```

---

## Task 4: Service CRUD functions

**Files:**
- Modify: `services/prediction_service.py`
- Test: `tests/test_tracked_markets_service.py`

- [ ] **Step 1: 写失败测试**

`tests/test_tracked_markets_service.py`：

```python
"""Tests for tracked-market service CRUD."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.tracked_market import TrackedMarket
from services import prediction_service
from schemas.predictions import TrackedMarketCreate, TrackedMarketUpdate


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_create_and_list(session):
    created = prediction_service.create_tracked_market(
        session, TrackedMarketCreate(kind="slug", identifier="foo", display_name="Foo")
    )
    assert created.id is not None
    assert created.enabled is True

    rows = prediction_service.list_tracked_markets(session)
    assert len(rows) == 1
    assert rows[0].identifier == "foo"


def test_create_duplicate_raises(session):
    prediction_service.create_tracked_market(
        session, TrackedMarketCreate(kind="slug", identifier="foo")
    )
    with pytest.raises(ValueError, match="duplicate"):
        prediction_service.create_tracked_market(
            session, TrackedMarketCreate(kind="slug", identifier="foo")
        )


def test_update_toggles_enabled(session):
    created = prediction_service.create_tracked_market(
        session, TrackedMarketCreate(kind="tag", identifier="fed")
    )
    updated = prediction_service.update_tracked_market(
        session, created.id, TrackedMarketUpdate(enabled=False)
    )
    assert updated.enabled is False
    assert updated.identifier == "fed"


def test_update_unknown_id_returns_none(session):
    result = prediction_service.update_tracked_market(
        session, 99999, TrackedMarketUpdate(enabled=False)
    )
    assert result is None


def test_delete(session):
    created = prediction_service.create_tracked_market(
        session, TrackedMarketCreate(kind="slug", identifier="foo")
    )
    ok = prediction_service.delete_tracked_market(session, created.id)
    assert ok is True
    assert prediction_service.list_tracked_markets(session) == []


def test_delete_unknown_id_returns_false(session):
    assert prediction_service.delete_tracked_market(session, 99999) is False


def test_create_strips_whitespace_and_validates_empty(session):
    with pytest.raises(ValueError, match="empty"):
        prediction_service.create_tracked_market(
            session, TrackedMarketCreate(kind="slug", identifier="   ")
        )
```

- [ ] **Step 2: 跑测试，确认失败**

```bash
pytest tests/test_tracked_markets_service.py -v
```
Expected: ImportError or AttributeError on `create_tracked_market`

- [ ] **Step 3: 实现 service 函数**

在 `services/prediction_service.py` 顶部 import 区域追加：

```python
from models.tracked_market import TrackedMarket
from schemas.predictions import TrackedMarketCreate, TrackedMarketSchema, TrackedMarketUpdate
```

在文件末尾追加：

```python
def _tracked_to_schema(row: TrackedMarket) -> TrackedMarketSchema:
    return TrackedMarketSchema(
        id=row.id,
        kind=row.kind,
        identifier=row.identifier,
        display_name=row.display_name,
        enabled=row.enabled,
        notes=row.notes,
    )


def list_tracked_markets(session: Session) -> list[TrackedMarketSchema]:
    rows = (
        session.query(TrackedMarket)
        .order_by(TrackedMarket.kind, TrackedMarket.identifier)
        .all()
    )
    return [_tracked_to_schema(r) for r in rows]


def create_tracked_market(session: Session, payload: TrackedMarketCreate) -> TrackedMarketSchema:
    identifier = (payload.identifier or "").strip()
    if not identifier:
        raise ValueError("identifier empty")

    exists = (
        session.query(TrackedMarket)
        .filter(TrackedMarket.kind == payload.kind, TrackedMarket.identifier == identifier)
        .first()
    )
    if exists:
        raise ValueError("duplicate")

    row = TrackedMarket(
        kind=payload.kind,
        identifier=identifier,
        display_name=(payload.display_name or "").strip() or None,
        notes=(payload.notes or "").strip() or None,
        enabled=True,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _tracked_to_schema(row)


def update_tracked_market(session: Session, tracked_id: int, payload: TrackedMarketUpdate) -> TrackedMarketSchema | None:
    row = session.query(TrackedMarket).filter(TrackedMarket.id == tracked_id).first()
    if row is None:
        return None
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.display_name is not None:
        row.display_name = payload.display_name.strip() or None
    if payload.notes is not None:
        row.notes = payload.notes.strip() or None
    session.commit()
    session.refresh(row)
    return _tracked_to_schema(row)


def delete_tracked_market(session: Session, tracked_id: int) -> bool:
    row = session.query(TrackedMarket).filter(TrackedMarket.id == tracked_id).first()
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True
```

- [ ] **Step 4: 跑测试**

```bash
pytest tests/test_tracked_markets_service.py -v
```
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add services/prediction_service.py tests/test_tracked_markets_service.py
git commit -m "feat(predictions): tracked-market CRUD service"
```

---

## Task 5: REST endpoints

**Files:**
- Modify: `api/routes.py`
- Test: `tests/test_predictions_tracked_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_predictions_tracked_api.py`：

```python
"""Integration tests for /api/predictions/tracked endpoints."""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from api.app import create_app
from database import get_session
from models.tracked_market import TrackedMarket


def _client() -> TestClient:
    return TestClient(create_app(enable_scheduler=False))


def _cleanup_test_rows():
    s = get_session()
    try:
        s.query(TrackedMarket).filter(TrackedMarket.identifier.like("test_%")).delete(synchronize_session=False)
        s.commit()
    finally:
        s.close()


def test_list_tracked_returns_seeded_rows():
    c = _client()
    r = c.get("/api/predictions/tracked")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    # 至少有 seed 进来的一些 slug
    assert any(row["kind"] == "slug" for row in data)


def test_create_update_delete_cycle():
    _cleanup_test_rows()
    c = _client()

    create = c.post("/api/predictions/tracked", json={
        "kind": "slug",
        "identifier": "test_create_market",
        "display_name": "Test display",
    })
    assert create.status_code == 200
    body = create.json()
    assert body["enabled"] is True
    new_id = body["id"]

    # 重复 → 409
    duplicate = c.post("/api/predictions/tracked", json={
        "kind": "slug",
        "identifier": "test_create_market",
    })
    assert duplicate.status_code == 409

    # PATCH enabled=False
    patch = c.patch(f"/api/predictions/tracked/{new_id}", json={"enabled": False})
    assert patch.status_code == 200
    assert patch.json()["enabled"] is False

    # 不存在 → 404
    patch_404 = c.patch("/api/predictions/tracked/999999", json={"enabled": False})
    assert patch_404.status_code == 404

    delete = c.delete(f"/api/predictions/tracked/{new_id}")
    assert delete.status_code == 200

    delete_404 = c.delete(f"/api/predictions/tracked/{new_id}")
    assert delete_404.status_code == 404

    _cleanup_test_rows()


def test_create_validates_kind():
    c = _client()
    r = c.post("/api/predictions/tracked", json={"kind": "bogus", "identifier": "x"})
    assert r.status_code == 422


def test_create_rejects_empty_identifier():
    _cleanup_test_rows()
    c = _client()
    r = c.post("/api/predictions/tracked", json={"kind": "slug", "identifier": "   "})
    assert r.status_code == 400
    _cleanup_test_rows()
```

- [ ] **Step 2: 跑测试，确认失败**

```bash
pytest tests/test_predictions_tracked_api.py -v
```
Expected: 404 on POST/DELETE/PATCH endpoints

- [ ] **Step 3: 在 api/routes.py 注册路由**

`api/routes.py` 顶部 import 增加：

```python
from schemas.predictions import (
    PredictionFamily, PredictionRow, PredictionsResponse,
    TrackedMarketCreate, TrackedMarketSchema, TrackedMarketUpdate,
)
```
（替换原本的那一行 import）

把现有 `/predictions/{market_id}/history` 路由**前**插入新路由（必须在 wildcard 前，FastAPI 路由按定义顺序匹配，避免 `tracked` 被当作 market_id）：

```python
@router.get("/predictions/tracked", response_model=list[TrackedMarketSchema])
def list_tracked(db: Session = Depends(get_db)) -> list[TrackedMarketSchema]:
    return prediction_service.list_tracked_markets(db)


@router.post("/predictions/tracked", response_model=TrackedMarketSchema)
def create_tracked(payload: TrackedMarketCreate, db: Session = Depends(get_db)) -> TrackedMarketSchema:
    try:
        return prediction_service.create_tracked_market(db, payload)
    except ValueError as e:
        if str(e) == "duplicate":
            raise ApiError(code="DUPLICATE", message="已存在相同的 kind+identifier", status=409)
        raise ApiError(code="INVALID", message=str(e), status=400)


@router.patch("/predictions/tracked/{tracked_id}", response_model=TrackedMarketSchema)
def update_tracked(tracked_id: int, payload: TrackedMarketUpdate, db: Session = Depends(get_db)) -> TrackedMarketSchema:
    result = prediction_service.update_tracked_market(db, tracked_id, payload)
    if result is None:
        raise ApiError(code="NOT_FOUND", message="未找到", status=404)
    return result


@router.delete("/predictions/tracked/{tracked_id}")
def delete_tracked(tracked_id: int, db: Session = Depends(get_db)) -> dict:
    ok = prediction_service.delete_tracked_market(db, tracked_id)
    if not ok:
        raise ApiError(code="NOT_FOUND", message="未找到", status=404)
    return {"ok": True}
```

注意：把上述四个路由放在 `@router.get("/predictions/{market_id}/history", ...)` 之**前**。

- [ ] **Step 4: 验证 ApiError 用法**

```bash
grep -n "class ApiError" api/errors.py
```
查看 ApiError 的 signature；如果它需要不同的参数名（比如 `error_code` 而非 `code`），调整调用。

- [ ] **Step 5: 跑测试**

```bash
pytest tests/test_predictions_tracked_api.py -v
```
Expected: 4 passed

- [ ] **Step 6: 跑全量后端测试确保无回归**

```bash
pytest tests/ -v
```
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add api/routes.py tests/test_predictions_tracked_api.py
git commit -m "feat(predictions): REST endpoints for tracked-market CRUD"
```

---

## Task 6: 扫描器从 DB 读跟踪列表

**Files:**
- Modify: `scanners/sources/polymarket/source.py`
- Test: `tests/test_polymarket_source_db.py`

注意：现有 `tests/test_polymarket_filter.py` 直接 `source.tracked_slugs = [...]`，必须保留这种 attribute override 兼容方式。

- [ ] **Step 1: 写新测试**

`tests/test_polymarket_source_db.py`：

```python
"""Tests for PolymarketSource reading tracked list from DB."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.tracked_market import TrackedMarket
from scanners.sources.polymarket.source import PolymarketSource


def _make_market(question: str, volume: float, slug: str = "abc") -> dict:
    return {
        "conditionId": f"cond_{slug}",
        "slug": slug,
        "question": question,
        "outcomePrices": '["0.6", "0.4"]',
        "outcomes": '["Yes", "No"]',
        "volume": str(volume),
    }


class FakeClient:
    def __init__(self):
        self.slug_calls: list[str] = []
        self.tag_calls: list[tuple[str, int]] = []

    def get_markets_by_slug(self, slug: str):
        self.slug_calls.append(slug)
        return [_make_market(f"Q for {slug}", 500_000, slug=slug)]

    def search_markets(self, tag: str, limit: int = 10):
        self.tag_calls.append((tag, limit))
        return [_make_market(f"Will the Fed do something via {tag}?", 500_000, slug=f"{tag}_q")]

    def health_check(self) -> bool:
        return True


@pytest.fixture
def db_with_tracked(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.add(TrackedMarket(kind="slug", identifier="enabled-slug", enabled=True))
    s.add(TrackedMarket(kind="slug", identifier="disabled-slug", enabled=False))
    s.add(TrackedMarket(kind="tag", identifier="fed", enabled=True))
    s.commit()
    s.close()

    # 让 source 用这个内存 DB 而不是真实 DB
    import scanners.sources.polymarket.source as source_module
    monkeypatch.setattr(source_module, "get_session", lambda: Session())
    return Session


def test_fetch_uses_enabled_db_rows(db_with_tracked):
    client = FakeClient()
    source = PolymarketSource(client=client)
    # 不设置 attribute override → 走 DB
    records = source.fetch()

    assert "enabled-slug" in client.slug_calls
    assert "disabled-slug" not in client.slug_calls
    assert ("fed", 5) in client.tag_calls
    assert len(records) > 0


def test_attribute_override_bypasses_db(db_with_tracked):
    client = FakeClient()
    source = PolymarketSource(client=client)
    source.tracked_slugs = ["override-slug"]
    source.tracked_tags = []
    records = source.fetch()

    assert client.slug_calls == ["override-slug"]
    assert client.tag_calls == []
    assert len(records) == 2  # Yes + No
```

- [ ] **Step 2: 跑新测试，确认失败**

```bash
pytest tests/test_polymarket_source_db.py -v
```
Expected: 测试失败（因为现在 `__init__` 仍从 config 读，且没有 DB-based 路径）

- [ ] **Step 3: 改造 PolymarketSource**

替换 `scanners/sources/polymarket/source.py`，关键改动：
- `__init__` 中 `tracked_tags / tracked_slugs` 默认设为 `None`（表示"未指定，走 DB"）
- 新增 `_load_tracked_from_db()` helper
- `fetch()` 第一步根据 attribute 是否为 None 决定来源

完整文件：

```python
"""Polymarket prediction market source orchestration."""

from loguru import logger

import config
from database import get_session
from models.tracked_market import TrackedMarket
from scanners.base import BaseSource, PredictionRecord
from scanners.sources.polymarket.client import PolymarketGammaClient
from scanners.sources.polymarket.filters import PolymarketMarketFilter
from scanners.sources.polymarket.parser import parse_market


class PolymarketSource(BaseSource):
    """Polymarket 预测市场数据源."""

    name = "polymarket"

    def __init__(
        self,
        client: PolymarketGammaClient | None = None,
        market_filter: PolymarketMarketFilter | None = None,
    ):
        self.gamma_url = config.POLYMARKET.get("gamma_url", "https://gamma-api.polymarket.com")
        # None 表示 "未指定，运行时查 DB"；测试可以直接赋值 list 来覆盖
        self.tracked_tags: list[str] | None = None
        self.tracked_slugs: list[str] | None = None
        self.discovery_limit = int(config.POLYMARKET.get("discovery_limit", 5))
        self.proxy = config.PROXY
        self.client = client or PolymarketGammaClient(self.gamma_url, self.proxy)
        self.market_filter = market_filter or PolymarketMarketFilter(
            min_volume=float(config.POLYMARKET.get("min_volume", 100_000)),
        )

    def _load_tracked_from_db(self) -> tuple[list[str], list[str]]:
        session = get_session()
        try:
            rows = session.query(TrackedMarket).filter(TrackedMarket.enabled.is_(True)).all()
            slugs = [r.identifier for r in rows if r.kind == "slug"]
            tags = [r.identifier for r in rows if r.kind == "tag"]
            return slugs, tags
        finally:
            session.close()

    def _resolve_tracked(self) -> tuple[list[str], list[str]]:
        if self.tracked_slugs is None and self.tracked_tags is None:
            return self._load_tracked_from_db()
        return self.tracked_slugs or [], self.tracked_tags or []

    def _is_noise_market(self, market: dict) -> bool:
        return self.market_filter.is_noise_market(market)

    def _search_markets(self, tag: str, limit: int = 10) -> list[dict]:
        try:
            return self.client.search_markets(tag, limit=limit)
        except Exception as e:
            logger.debug(f"Polymarket 搜索 tag={tag} 失败: {e}")
        return []

    def _get_markets_by_slug(self, slug: str) -> list[dict]:
        try:
            return self.client.get_markets_by_slug(slug)
        except Exception as e:
            logger.debug(f"Polymarket 获取 slug={slug} 失败: {e}")
        return []

    def _append_market_records(
        self,
        records: list[PredictionRecord],
        seen_ids: set[str],
        market: dict,
    ):
        for r in self._parse_market(market):
            key = f"{r.market_id}:{r.outcome}"
            if key in seen_ids:
                continue
            records.append(r)
            seen_ids.add(key)

    def fetch(self) -> list[PredictionRecord]:
        """获取所有跟踪的预测市场最新赔率."""
        slugs, tags = self._resolve_tracked()

        records: list[PredictionRecord] = []
        seen_ids: set[str] = set()

        for slug in slugs:
            for market in self._get_markets_by_slug(slug):
                self._append_market_records(records, seen_ids, market)

        for tag in tags:
            for market in self._search_markets(tag, limit=self.discovery_limit):
                if self._is_noise_market(market):
                    continue
                self._append_market_records(records, seen_ids, market)

        logger.info(f"[Polymarket] 获取 {len(records)} 条预测市场记录")
        return records

    def _parse_market(self, market: dict) -> list[PredictionRecord]:
        return parse_market(market)

    def health_check(self) -> bool:
        return self.client.health_check()
```

- [ ] **Step 4: 跑新测试**

```bash
pytest tests/test_polymarket_source_db.py -v
```
Expected: 2 passed

- [ ] **Step 5: 跑现有 polymarket 测试确保没破**

```bash
pytest tests/test_polymarket_filter.py -v
```
Expected: all pass（attribute override 路径仍工作）

- [ ] **Step 6: 全量后端测试**

```bash
pytest tests/ -v
```
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add scanners/sources/polymarket/source.py tests/test_polymarket_source_db.py
git commit -m "feat(predictions): polymarket source reads tracked list from DB"
```

---

## Task 7: 前端 types + API client

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`

- [ ] **Step 1: 在 types.ts 末尾追加**

```ts
export type TrackedMarket = {
  id: number;
  kind: "slug" | "tag";
  identifier: string;
  display_name: string | null;
  enabled: boolean;
  notes: string | null;
};

export type TrackedMarketCreatePayload = {
  kind: "slug" | "tag";
  identifier: string;
  display_name?: string | null;
  notes?: string | null;
};

export type TrackedMarketUpdatePayload = {
  enabled?: boolean;
  display_name?: string | null;
  notes?: string | null;
};
```

- [ ] **Step 2: 在 client.ts 顶部 import 添加**

```ts
import type {
  // ...existing...
  TrackedMarket,
  TrackedMarketCreatePayload,
  TrackedMarketUpdatePayload
} from "./types";
```

- [ ] **Step 3: 在 client.ts 的 `api = { ... }` 对象内追加方法**

放在 `predictionHistory` 之后：

```ts
  predictionTracked: () => request<TrackedMarket[]>("/predictions/tracked"),
  createPredictionTracked: (payload: TrackedMarketCreatePayload) =>
    request<TrackedMarket>("/predictions/tracked", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updatePredictionTracked: (id: number, payload: TrackedMarketUpdatePayload) =>
    request<TrackedMarket>(`/predictions/tracked/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  deletePredictionTracked: (id: number) =>
    request<{ ok: boolean }>(`/predictions/tracked/${id}`, { method: "DELETE" }),
```

- [ ] **Step 4: 编译检查**

```bash
cd frontend && npx tsc --noEmit
```
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts
git commit -m "feat(predictions): frontend tracked-market api types & client"
```

---

## Task 8: PredictionCard 组件

**Files:**
- Create: `frontend/src/components/PredictionCard.tsx`

- [ ] **Step 1: 写组件**

```tsx
import { MultiLineChart, type ChartPoint } from "./Charts";

type PredictionCardMeta = {
  volume?: number | null;
  outcomes?: number;
  updatedAt?: string | null;
  latestPct?: number | null;
};

export function PredictionCard({
  title,
  subtitle,
  data,
  keys,
  meta,
  height = 240
}: {
  title: string;
  subtitle?: string;
  data: ChartPoint[];
  keys: string[];
  meta?: PredictionCardMeta;
  height?: number;
}) {
  const footers: string[] = [];
  if (meta?.outcomes !== undefined) footers.push(`${meta.outcomes} 个分支`);
  if (meta?.volume !== undefined && meta.volume !== null) {
    footers.push(`成交 $${(meta.volume / 1000).toFixed(0)}k`);
  }
  if (meta?.latestPct !== undefined && meta.latestPct !== null) {
    footers.push(`最新 ${meta.latestPct.toFixed(1)}%`);
  }
  if (meta?.updatedAt) footers.push(`更新 ${meta.updatedAt.slice(5, 16)}`);

  return (
    <article className="prediction-card">
      <header className="prediction-card-head">
        <h3>{title}</h3>
        {subtitle ? <span className="muted-text">{subtitle}</span> : null}
      </header>
      <MultiLineChart data={data} keys={keys} height={height} />
      {footers.length > 0 ? (
        <footer className="prediction-card-foot">
          {footers.map((f) => (
            <span key={f}>{f}</span>
          ))}
        </footer>
      ) : null}
    </article>
  );
}
```

- [ ] **Step 2: 编译检查**

```bash
cd frontend && npx tsc --noEmit
```
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/PredictionCard.tsx
git commit -m "feat(predictions): PredictionCard component"
```

---

## Task 9: TrackedMarketsPanel 组件

**Files:**
- Create: `frontend/src/components/TrackedMarketsPanel.tsx`

- [ ] **Step 1: 写组件**

```tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { TrackedMarket } from "../api/types";
import { Button, SelectControl, TextInput } from "./Controls";
import { ErrorState, LoadingState } from "./StateViews";

const kindOptions = [
  { label: "Slug (单个 market 或 event)", value: "slug" },
  { label: "Tag (家族 / 自动发现)", value: "tag" }
];

export function TrackedMarketsPanel() {
  const queryClient = useQueryClient();
  const list = useQuery({
    queryKey: ["prediction-tracked"],
    queryFn: () => api.predictionTracked()
  });

  const [kind, setKind] = useState("slug");
  const [identifier, setIdentifier] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [errorMsg, setErrorMsg] = useState("");

  const create = useMutation({
    mutationFn: () =>
      api.createPredictionTracked({
        kind: kind as "slug" | "tag",
        identifier: identifier.trim(),
        display_name: displayName.trim() || null
      }),
    onSuccess: () => {
      setIdentifier("");
      setDisplayName("");
      setErrorMsg("");
      queryClient.invalidateQueries({ queryKey: ["prediction-tracked"] });
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setErrorMsg(err.payload.message || "添加失败");
      } else {
        setErrorMsg("添加失败");
      }
    }
  });

  const toggle = useMutation({
    mutationFn: (row: TrackedMarket) =>
      api.updatePredictionTracked(row.id, { enabled: !row.enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["prediction-tracked"] })
  });

  const remove = useMutation({
    mutationFn: (row: TrackedMarket) => api.deletePredictionTracked(row.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["prediction-tracked"] })
  });

  const trimmedId = identifier.trim();
  const submitDisabled = !trimmedId || create.isPending;

  return (
    <details className="panel tracked-panel">
      <summary>
        <h2>跟踪管理</h2>
        <span className="muted-text">{list.data ? `共 ${list.data.length} 条` : ""}</span>
      </summary>

      <div className="tracked-add-row">
        <SelectControl label="类型" value={kind} onChange={setKind} options={kindOptions} />
        <TextInput
          label={kind === "slug" ? "slug" : "tag"}
          value={identifier}
          onChange={setIdentifier}
          placeholder={kind === "slug" ? "fed-decision-in-june-825" : "fed"}
        />
        <TextInput
          label="显示名（可选）"
          value={displayName}
          onChange={setDisplayName}
          placeholder="2026 年 6 月 FOMC"
        />
        <Button onClick={() => create.mutate()} disabled={submitDisabled}>
          {create.isPending ? "添加中..." : "添加"}
        </Button>
      </div>
      {errorMsg ? <div className="state-view error">{errorMsg}</div> : null}

      {list.isLoading ? (
        <LoadingState />
      ) : list.error ? (
        <ErrorState error={list.error} />
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>类型</th>
                <th>Identifier</th>
                <th>显示名</th>
                <th>启用</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {(list.data ?? []).map((row) => (
                <tr key={row.id}>
                  <td>{row.kind}</td>
                  <td><code>{row.identifier}</code></td>
                  <td>{row.display_name || "—"}</td>
                  <td>
                    <input
                      type="checkbox"
                      checked={row.enabled}
                      disabled={toggle.isPending}
                      onChange={() => toggle.mutate(row)}
                    />
                  </td>
                  <td>
                    <button
                      className="link-button danger"
                      disabled={remove.isPending}
                      onClick={() => {
                        if (window.confirm(`删除 ${row.identifier}?`)) remove.mutate(row);
                      }}
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
              {!(list.data ?? []).length ? (
                <tr><td colSpan={5} className="muted-text">尚未跟踪任何 slug/tag</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}
```

- [ ] **Step 2: 编译检查**

```bash
cd frontend && npx tsc --noEmit
```
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/TrackedMarketsPanel.tsx
git commit -m "feat(predictions): TrackedMarketsPanel component"
```

---

## Task 10: 重写 PredictionsPage

**Files:**
- Modify: `frontend/src/pages/PredictionsPage.tsx`

- [ ] **Step 1: 替换整个文件内容**

```tsx
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { PredictionFamily, PredictionMarketSummary } from "../api/types";
import { type ChartPoint } from "../components/Charts";
import { PageHeader, SelectControl, TextInput } from "../components/Controls";
import { PredictionCard } from "../components/PredictionCard";
import { TrackedMarketsPanel } from "../components/TrackedMarketsPanel";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";

const hourOptions = [
  { label: "2小时", value: "2" },
  { label: "6小时", value: "6" },
  { label: "24小时", value: "24" },
  { label: "7天", value: "168" },
  { label: "30天", value: "720" }
];

function buildFamilyChart(family: PredictionFamily): { data: ChartPoint[]; keys: string[] } {
  const byTime = new Map<string, ChartPoint>();
  const keys: string[] = [];
  family.series.forEach((series) => {
    keys.push(series.label);
    series.points.forEach((point) => {
      const time = point.timestamp_bj?.slice(5, 16) ?? "";
      const row = byTime.get(time) ?? { time };
      row[series.label] = point.probability_pct;
      byTime.set(time, row);
    });
  });
  return { data: Array.from(byTime.values()), keys };
}

function buildMarketChart(market: PredictionMarketSummary, history: import("../api/types").PredictionRow[]): { data: ChartPoint[]; keys: string[] } {
  const byTime = new Map<string, ChartPoint>();
  const keys = Array.from(new Set(history.map((row) => row.outcome)));
  history.forEach((row) => {
    const time = row.timestamp_bj?.slice(5, 16) ?? "";
    const entry = byTime.get(time) ?? { time };
    entry[row.outcome] = row.probability_pct;
    byTime.set(time, entry);
  });
  return { data: Array.from(byTime.values()), keys };
}

function MarketCard({ market, hours }: { market: PredictionMarketSummary; hours: number }) {
  const history = useQuery({
    queryKey: ["prediction-history", market.market_id, hours],
    queryFn: () => api.predictionHistory(market.market_id, hours),
    enabled: Boolean(market.market_id)
  });
  const chart = useMemo(
    () => buildMarketChart(market, history.data ?? []),
    [history.data, market]
  );
  const yes = market.outcomes.find((o) => o.outcome.toLowerCase() === "yes");
  const latestPct = yes?.probability_pct ?? market.outcomes[0]?.probability_pct;
  const updatedAt = market.outcomes[0]?.timestamp_bj ?? null;

  return (
    <PredictionCard
      title={market.question}
      data={chart.data}
      keys={chart.keys}
      meta={{
        volume: market.volume,
        outcomes: market.outcomes.length,
        latestPct,
        updatedAt
      }}
    />
  );
}

export function PredictionsPage() {
  const [hours, setHours] = useState("24");
  const [search, setSearch] = useState("");

  const families = useQuery({
    queryKey: ["prediction-families", hours, search],
    queryFn: () => api.predictionFamilies({ hours: Number(hours), search })
  });
  const predictions = useQuery({
    queryKey: ["predictions", hours, search],
    queryFn: () => api.predictions({ hours: Number(hours), search })
  });

  const familyMarketIds = useMemo(() => {
    const ids = new Set<string>();
    (families.data ?? []).forEach((f) =>
      f.series.forEach((s) => ids.add(s.market_id))
    );
    return ids;
  }, [families.data]);

  const standaloneMarkets = useMemo(() => {
    return (predictions.data?.markets ?? []).filter((m) => !familyMarketIds.has(m.market_id));
  }, [predictions.data, familyMarketIds]);

  return (
    <section>
      <PageHeader
        title="预测市场"
        subtitle={`最后更新 ${predictions.data?.latest_timestamp?.timestamp_bj ?? "—"}`}
      />
      <div className="toolbar">
        <SelectControl label="时间窗口" value={hours} onChange={setHours} options={hourOptions} />
        <TextInput label="搜索市场" value={search} onChange={setSearch} placeholder="Fed / inflation / hormuz" />
      </div>

      <TrackedMarketsPanel />

      <section className="panel">
        <div className="panel-head"><h2>主题概率对比</h2></div>
        {families.isLoading ? (
          <LoadingState />
        ) : families.error ? (
          <ErrorState error={families.error} />
        ) : (families.data ?? []).length ? (
          <div className="prediction-grid">
            {(families.data ?? []).map((family) => {
              const chart = buildFamilyChart(family);
              return (
                <PredictionCard
                  key={family.id}
                  title={family.name}
                  subtitle={`${family.series.length} 个分支`}
                  data={chart.data}
                  keys={chart.keys}
                />
              );
            })}
          </div>
        ) : (
          <EmptyState title="当前窗口内没有可聚合的主题组" />
        )}
      </section>

      <section className="panel">
        <div className="panel-head"><h2>单市场</h2></div>
        {predictions.isLoading ? (
          <LoadingState />
        ) : predictions.error ? (
          <ErrorState error={predictions.error} />
        ) : standaloneMarkets.length ? (
          <div className="prediction-grid">
            {standaloneMarkets.map((m) => (
              <MarketCard key={m.market_id} market={m} hours={Number(hours)} />
            ))}
          </div>
        ) : (
          <EmptyState title="没有不属于任何主题组的单市场" />
        )}
      </section>
    </section>
  );
}
```

- [ ] **Step 2: 编译检查**

```bash
cd frontend && npx tsc --noEmit
```
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/PredictionsPage.tsx
git commit -m "feat(predictions): grid layout with PredictionCard + tracked panel"
```

---

## Task 11: CSS

**Files:**
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: 在文件末尾追加**

```css
.prediction-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 12px;
}

.prediction-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--panel);
  padding: 12px;
  min-width: 0;
}

.prediction-card-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.prediction-card-head h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.prediction-card-head .muted-text {
  font-size: 11px;
}

.prediction-card-foot {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 11px;
  color: var(--muted);
}

.prediction-card .chart-shell {
  border: 0;
  padding: 0;
  background: transparent;
}

.tracked-panel > summary {
  display: flex;
  align-items: center;
  gap: 14px;
  cursor: pointer;
  list-style: none;
  margin-bottom: 10px;
}

.tracked-panel > summary::-webkit-details-marker {
  display: none;
}

.tracked-panel > summary h2 {
  margin: 0;
  font-size: 16px;
}

.tracked-add-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  align-items: end;
  padding: 12px 0;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--line-soft);
}

.tracked-add-row .field {
  min-width: 200px;
  flex: 1;
}

@media (max-width: 1080px) {
  .prediction-grid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 2: 构建前端验证 CSS 没语法错**

```bash
cd frontend && npm run build
```
Expected: build succeeds

- [ ] **Step 3: Commit**

```bash
git add frontend/src/styles.css
git commit -m "feat(predictions): grid + tracked-panel CSS"
```

---

## Task 12: 端到端验证

- [ ] **Step 1: 启动后端 dev**

新窗口运行：
```bash
python run.py api-dev
```

- [ ] **Step 2: 启动前端 dev**

另一新窗口运行：
```bash
cd frontend && npm run dev
```

- [ ] **Step 3: 在浏览器手测**

打开 Vite dev URL（通常 http://localhost:5173），导航到"预测市场"页面，依次验证：

1. 页面加载无报错
2. 看到 grid 布局：每行 2 张卡片
3. 卡片高度大约 240px，标题截断为 2 行
4. 1920×1080 视口下，能看到 ≥4 张卡片同时
5. 顶部"跟踪管理"折叠面板可以展开
6. 表格里显示从 config seed 进来的全部 slug + tag
7. 添加一条 `kind=slug, identifier=test_e2e_market`，列表立即更新
8. 切换该行 enabled toggle，列表立即反映
9. 删除该行，确认对话框，列表立即更新
10. 重复添加同一行 → 错误提示

- [ ] **Step 4: 全量测试一次**

```bash
pytest tests/ -v
cd frontend && npm run build
```
Expected: 后端 + 前端 build 都通过

- [ ] **Step 5: 把 PENDING.md 或文档（如有）更新一行**

```bash
grep -n "POLYMARKET\|tracked_slugs" PENDING.md 2>/dev/null
```
如有相关条目，加一行说明"现在通过 UI 管理跟踪列表，config.py 仅作初始 seed"。

- [ ] **Step 6: 最终 commit + push**

```bash
git status
# 如有遗漏的 PENDING.md 改动:
# git add PENDING.md && git commit -m "docs: tracked markets now managed via UI"
git push -u origin predictions-grid-redesign
```

---

## Self-Review Checklist

- [x] **Spec coverage:**
  - 数据模型 (TrackedMarket) → Task 1 ✓
  - Seed → Task 2 ✓
  - Pydantic schemas → Task 3 ✓
  - Service CRUD → Task 4 ✓
  - REST 4 端点 → Task 5 ✓
  - 扫描器从 DB 读 → Task 6 ✓
  - 前端 types + client → Task 7 ✓
  - PredictionCard → Task 8 ✓
  - TrackedMarketsPanel → Task 9 ✓
  - PredictionsPage 重写 → Task 10 ✓
  - CSS grid → Task 11 ✓
  - 验收清单 → Task 12 ✓
- [x] **Placeholder scan:** 无 TBD/TODO/"and so on"
- [x] **Type consistency:** `TrackedMarketSchema` / `TrackedMarketCreate` / `TrackedMarketUpdate` 三处定义一致；前端 `TrackedMarket` 与后端 schema 字段对齐
- [x] **API path consistency:** `/api/predictions/tracked` + `/api/predictions/tracked/{tracked_id}` — 在 Task 5 明确要求放在 `/predictions/{market_id}/history` 之前避免 wildcard 冲突
- [x] **Test compat:** Task 6 保留 `tracked_slugs / tracked_tags` 作为 attribute override，`test_polymarket_filter.py` 现有 7 个测试不需要改

