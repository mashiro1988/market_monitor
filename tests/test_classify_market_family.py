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


def test_core_cpi_mom_groups_by_month():
    a = classify_market_family("Will Core CPI MoM be 0.3% in May?")
    b = classify_market_family("Will Core CPI MoM be 0.2% in May?")
    assert a["id"] == b["id"] == "core_cpi_mom_may"
    assert a["label"] == "0.3%" and a["order"] == 0.3


def test_core_cpi_mom_bounds_and_negative():
    lo = classify_market_family("Will Core CPI MoM be -0.3% or less in May?")
    hi = classify_market_family("Will Core CPI MoM be 0.6% or more in May?")
    assert lo["label"] == "≤-0.3%" and lo["order"] == -0.3
    assert hi["label"] == "≥0.6%" and hi["order"] == 0.6


def test_core_cpi_mom_different_months_separate_family():
    may = classify_market_family("Will Core CPI MoM be 0.3% in May?")
    jun = classify_market_family("Will Core CPI MoM be 0.3% in June?")
    assert may["id"] != jun["id"]


def test_monthly_inflation_mom_groups():
    a = classify_market_family("Will monthly inflation increase by 0.3% in May?")
    b = classify_market_family("Will monthly inflation increase by 0.1% or less in May?")
    assert a["id"] == b["id"] == "inflation_mom_may"
    assert b["label"] == "≤0.1%"
