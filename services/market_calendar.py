# -*- coding: utf-8 -*-
"""交易时段判定（news-impact-engine Phase 1）。

用途：入库/打标时记一条新闻发生时"传统市场(美式期货)开没开"(traditional_open)，
让台账取数能在 SQL 里直接把休市时段的新闻滤掉——比事后逐条算反应、空了再回扫干净得多。

加密(BTC/ETH)7×24 永远开；NQ/ES/YM/CL/GC/DX 这类 CME 期货按其大致时段：
周日 18:00 ET 开 ~ 周五 17:00 ET 收，工作日每天 17:00-18:00 ET 维护暂停。
DST 由 zoneinfo 的 America/New_York 自动处理（无需手判夏令时）。
（现金股指 ^IXIC 等时段更短，但台账只跑在 BTC/NQ 上，不细分。）
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def is_crypto(symbol: str) -> bool:
    return symbol.endswith("/USDT") or symbol in ("BTCUSDT", "ETHUSDT")


def is_traditional_open(ts_utc_naive: datetime) -> bool:
    """美式期货(CME e-mini 等)大致是否在交易时段。ts 为 UTC-naive。"""
    et = ts_utc_naive.replace(tzinfo=timezone.utc).astimezone(_ET)
    wd, h = et.weekday(), et.hour      # Mon=0 .. Sun=6
    if wd == 5:                         # 周六：全天休
        return False
    if wd == 6:                         # 周日：18:00 ET 才开
        return h >= 18
    if wd == 4:                         # 周五：17:00 ET 收
        return h < 17
    return h != 17                      # 周一~周四：除 17:00-18:00 维护外都开


def is_open(symbol: str, ts_utc_naive: datetime) -> bool:
    """该品种在 ts 时刻是否可交易（→ 是否可能量到价格反应）。"""
    if is_crypto(symbol):
        return True
    return is_traditional_open(ts_utc_naive)
