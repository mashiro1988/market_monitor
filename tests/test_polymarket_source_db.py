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

    import scanners.sources.polymarket.source as source_module
    monkeypatch.setattr(source_module, "get_session", lambda: Session())
    return Session


def test_fetch_uses_enabled_db_rows(db_with_tracked):
    client = FakeClient()
    source = PolymarketSource(client=client)
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
    assert len(records) == 2
