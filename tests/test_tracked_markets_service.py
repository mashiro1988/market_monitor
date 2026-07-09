"""Tests for tracked-market service CRUD."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
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
        session, TrackedMarketCreate(kind="slug", identifier="fed-decision-in-june-825")
    )
    updated = prediction_service.update_tracked_market(
        session, created.id, TrackedMarketUpdate(enabled=False)
    )
    assert updated.enabled is False
    assert updated.identifier == "fed-decision-in-june-825"


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


def test_delete_already_dismissed_returns_false(session):
    created = prediction_service.create_tracked_market(
        session, TrackedMarketCreate(kind="slug", identifier="foo")
    )
    assert prediction_service.delete_tracked_market(session, created.id) is True
    assert prediction_service.delete_tracked_market(session, created.id) is False  # 已删→幂等 404


def test_create_strips_whitespace_and_validates_empty(session):
    with pytest.raises(ValueError, match="empty"):
        prediction_service.create_tracked_market(
            session, TrackedMarketCreate(kind="slug", identifier="   ")
        )


def test_create_reactivates_dismissed(session):
    """软删除后再添加同名项 → 复活，不报 duplicate（5b 修复）。"""
    created = prediction_service.create_tracked_market(
        session, TrackedMarketCreate(kind="slug", identifier="foo")
    )
    prediction_service.delete_tracked_market(session, created.id)
    assert prediction_service.list_tracked_markets(session) == []
    again = prediction_service.create_tracked_market(
        session, TrackedMarketCreate(kind="slug", identifier="foo")
    )
    assert again.identifier == "foo"
    listed = prediction_service.list_tracked_markets(session)
    assert len(listed) == 1 and listed[0].identifier == "foo"
