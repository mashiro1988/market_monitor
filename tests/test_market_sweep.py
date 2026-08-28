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
