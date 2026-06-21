# -*- coding: utf-8 -*-
"""交易时段判定（news-impact-engine Phase 1）：标新闻发生时传统市场(美式期货)开没开。
归因时用它把"休市时段发的新闻"从台账取数里直接滤掉,不必事后逐条试反应。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from services import market_calendar

# June 2026 = EDT(UTC-4)：UTC = ET + 4h。06-12 周五 / 06-13 周六 / 06-14 周日 / 06-15 周一。


def test_crypto_always_open():
    sat = datetime(2026, 6, 13, 16, 0)   # 周六
    assert market_calendar.is_open("BTC/USDT", sat) is True
    assert market_calendar.is_open("ETH/USDT", sat) is True


def test_nq_weekend_closed():
    assert market_calendar.is_open("NQ=F", datetime(2026, 6, 13, 16, 0)) is False   # 周六


def test_nq_sunday_opens_1800_et():
    assert market_calendar.is_open("NQ=F", datetime(2026, 6, 14, 21, 0)) is False    # 周日 17:00 ET
    assert market_calendar.is_open("NQ=F", datetime(2026, 6, 14, 22, 0)) is True     # 周日 18:00 ET


def test_nq_weekday_open_except_daily_halt():
    assert market_calendar.is_open("NQ=F", datetime(2026, 6, 15, 16, 0)) is True     # 周一 12:00 ET
    assert market_calendar.is_open("NQ=F", datetime(2026, 6, 15, 21, 30)) is False   # 周一 17:30 ET 维护


def test_nq_friday_closes_1700_et():
    assert market_calendar.is_open("NQ=F", datetime(2026, 6, 12, 20, 0)) is True      # 周五 16:00 ET
    assert market_calendar.is_open("NQ=F", datetime(2026, 6, 12, 21, 0)) is False     # 周五 17:00 ET
