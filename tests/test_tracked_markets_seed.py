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
    assert len(rows) == 3


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
