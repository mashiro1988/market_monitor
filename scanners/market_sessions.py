# -*- coding: utf-8 -*-
"""交易时段表：按交易所本地时区定义会话，zoneinfo 换算，夏令时自动正确。

is_open(symbol, now_utc)      —— 严格"此刻开市吗"（卡片 freshness 判定用）
should_fetch(symbol, now_utc) —— is_open(now) or is_open(now-10min)：
                                  收盘/午休/维护开始后 10 分钟内仍拉一轮，抓最后一根已收盘 bar
active_symbols(symbols, now)  —— 过滤出应拉取的品种集合

节假日不建模（设计取舍，见 spec §4.1）：假日请求返回空数据，浪费可忽略。
债券会话为近似口径，仅影响卡片标注边缘时刻，不影响采集（cnbc 不限流）。
未知品种 fail-open（按开市处理并 warning 一次）：宁可多拉，不可静默漏拉。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from loguru import logger

FETCH_TAIL = timedelta(minutes=10)
_WARNED_UNKNOWN: set[str] = set()


@dataclass(frozen=True)
class DailySessions:
    """常规日内市场：周一~五若干个 (开, 收) 区间，交易所当地时间。"""
    tz: str
    spans: tuple[tuple[time, time], ...]

    def is_open(self, local: datetime) -> bool:
        if local.weekday() > 4:
            return False
        t = local.time()
        return any(start <= t < end for start, end in self.spans)


@dataclass(frozen=True)
class WeeklyNearRoundTheClock:
    """近 24h 市场：周 open_wd open_t 开 → 周 close_wd close_t 收，每日 break 段跳过。
    weekday: Monday=0 … Sunday=6。跨周界（如周日开）用周分钟索引环绕比较。"""
    tz: str
    open_wd: int
    open_t: time
    close_wd: int
    close_t: time
    break_start: time
    break_end: time

    def is_open(self, local: datetime) -> bool:
        idx = local.weekday() * 1440 + local.hour * 60 + local.minute
        open_idx = self.open_wd * 1440 + self.open_t.hour * 60 + self.open_t.minute
        close_idx = self.close_wd * 1440 + self.close_t.hour * 60 + self.close_t.minute
        if open_idx <= close_idx:
            in_span = open_idx <= idx < close_idx
        else:                     # 跨周界（周日开→周五收）
            in_span = idx >= open_idx or idx < close_idx
        if not in_span:
            return False
        return not (self.break_start <= local.time() < self.break_end)


@dataclass(frozen=True)
class AlwaysOpen:
    tz: str = "UTC"

    def is_open(self, local: datetime) -> bool:  # noqa: ARG002
        return True


_US_CASH = DailySessions(tz="America/New_York", spans=((time(9, 30), time(16, 0)),))
_CME = WeeklyNearRoundTheClock(tz="America/Chicago",
                               open_wd=6, open_t=time(17, 0), close_wd=4, close_t=time(16, 0),
                               break_start=time(16, 0), break_end=time(17, 0))
_ICE_NY = WeeklyNearRoundTheClock(tz="America/New_York",
                                  open_wd=6, open_t=time(18, 0), close_wd=4, close_t=time(17, 0),
                                  break_start=time(17, 0), break_end=time(18, 0))
_TOKYO_CASH = DailySessions(tz="Asia/Tokyo",
                            spans=((time(9, 0), time(11, 30)), (time(12, 30), time(15, 30))))
_SEOUL_CASH = DailySessions(tz="Asia/Seoul", spans=((time(9, 0), time(15, 30)),))
_CN_CASH = DailySessions(tz="Asia/Shanghai",
                         spans=((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0))))
_JGB = DailySessions(tz="Asia/Tokyo",
                     spans=((time(9, 0), time(11, 30)), (time(12, 30), time(15, 0))))
_ALWAYS = AlwaysOpen()

SYMBOL_RULES: dict[str, object] = {
    # 美股现指
    "^DJI": _US_CASH, "^IXIC": _US_CASH, "^GSPC": _US_CASH,
    # CME 期货 + 商品（NIY=F 是 CME 日经期货，跟 Globex 时段）
    "ES=F": _CME, "NQ=F": _CME, "YM=F": _CME, "NIY=F": _CME,
    "GC=F": _CME, "SI=F": _CME, "CL=F": _CME,
    # 美元指数（ICE，NY 锚定）
    "DX-Y.NYB": _ICE_NY,
    # 亚洲现指
    "^N225": _TOKYO_CASH, "^KS11": _SEOUL_CASH,
    "000001.SS": _CN_CASH, "399001.SZ": _CN_CASH, "399006.SZ": _CN_CASH,
    # 债券（近似口径，仅用于卡片标注）
    "US_10Y": _ICE_NY, "US_2Y": _ICE_NY, "US_SPREAD": _ICE_NY,
    "JP_10Y": _JGB, "JP_2Y": _JGB, "JP_SPREAD": _JGB,
}
# 加密与代理永续 24×7：按后缀匹配（BTC/USDT、*-USDT-SWAP）
_ALWAYS_SUFFIXES = ("/USDT", "-USDT-SWAP")


def _rule_for(symbol: str):
    rule = SYMBOL_RULES.get(symbol)
    if rule is not None:
        return rule
    if symbol.endswith(_ALWAYS_SUFFIXES):
        return _ALWAYS
    if symbol not in _WARNED_UNKNOWN:
        _WARNED_UNKNOWN.add(symbol)
        logger.warning(f"[MarketSessions] 未知品种 {symbol}，fail-open 按开市处理")
    return _ALWAYS


def is_open(symbol: str, now_utc: datetime) -> bool:
    """now_utc: naive UTC（库内口径）。"""
    rule = _rule_for(symbol)
    local = now_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(rule.tz))
    return rule.is_open(local.replace(tzinfo=None))


def should_fetch(symbol: str, now_utc: datetime) -> bool:
    return is_open(symbol, now_utc) or is_open(symbol, now_utc - FETCH_TAIL)


def active_symbols(symbols, now_utc: datetime) -> set[str]:
    return {s for s in symbols if should_fetch(s, now_utc)}
