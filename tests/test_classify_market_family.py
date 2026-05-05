"""Tests for market-family classifier regex rules."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.prediction_service import classify_market_family


def test_wti_high_may_classifies():
    f = classify_market_family("Will WTI Crude Oil (WTI) hit (HIGH) $120 in May?")
    assert f == {"id": "wti_high_may", "name": "WTI 原油 May 触及上沿", "label": "≥$120", "order": 120.0}


def test_wti_low_may_classifies():
    f = classify_market_family("Will WTI Crude Oil (WTI) hit (LOW) $80 in May?")
    assert f == {"id": "wti_low_may", "name": "WTI 原油 May 触及下沿", "label": "≤$80", "order": 80.0}


def test_wti_april_separate_family_from_may():
    apr = classify_market_family("Will WTI Crude Oil (WTI) hit (HIGH) $150 in April?")
    may = classify_market_family("Will WTI Crude Oil (WTI) hit (HIGH) $150 in May?")
    assert apr["id"] != may["id"]


def test_unrelated_market_returns_none():
    assert classify_market_family("Will Bitcoin reach $1m by 2026?") is None
