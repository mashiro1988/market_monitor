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
