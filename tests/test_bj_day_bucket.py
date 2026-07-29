# -*- coding: utf-8 -*-
"""北京日界（2026-07-29 口径切换）：换算函数 + 跨界归属 + 口径隔离 + 日报目标日。"""
from datetime import datetime, timedelta

from services.time_utils import bj_date_of, bj_day_bounds


def test_bj_date_of_maps_utc_instant_to_beijing_day():
    # 北京 = UTC+8：UTC 15:59 还是当天，UTC 16:00 就翻到次日
    assert bj_date_of(datetime(2026, 7, 28, 15, 59)) == "2026-07-28"
    assert bj_date_of(datetime(2026, 7, 28, 16, 0)) == "2026-07-29"
    assert bj_date_of(datetime(2026, 7, 28, 0, 0)) == "2026-07-28"
    assert bj_date_of(None) is None


def test_bj_day_bounds_spans_utc_16_to_16():
    start, end = bj_day_bounds("2026-07-29")
    assert start == datetime(2026, 7, 28, 16, 0)
    assert end == datetime(2026, 7, 29, 16, 0)


def test_bounds_and_date_of_are_consistent():
    start, end = bj_day_bounds("2026-07-29")
    assert bj_date_of(start) == "2026-07-29"                      # 左闭
    assert bj_date_of(end - timedelta(seconds=1)) == "2026-07-29"
    assert bj_date_of(end) == "2026-07-30"                        # 右开
