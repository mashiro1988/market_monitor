"""图表与展示工具函数"""
from datetime import datetime, time, timedelta

import pandas as pd


def normalize_prices(prices: list[float]) -> list[float]:
    """将价格序列转为相对第一个点的涨跌幅（%）。"""
    if not prices:
        return []
    base = prices[0]
    if base == 0:
        return [0.0] * len(prices)
    return [(p / base - 1) * 100 for p in prices]


def to_beijing_time(ts):
    """把数据库中的 UTC naive / aware 时间转成北京时间 naive，用于前端展示。"""
    if ts is None or pd.isna(ts):
        return None
    value = pd.Timestamp(ts)
    if value.tzinfo is not None:
        value = value.tz_convert("UTC").tz_localize(None)
    return (value.to_pydatetime() + timedelta(hours=8)).replace(tzinfo=None)


def format_beijing_time(ts, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """格式化为北京时间字符串。"""
    bj = to_beijing_time(ts)
    return bj.strftime(fmt) if bj else "—"


def today_beijing_anchor_utc(hour: int, minute: int = 0, now_utc=None) -> datetime:
    """Return UTC naive datetime for today's Beijing wall-clock anchor."""
    now_utc = now_utc or datetime.utcnow()
    now_bj = to_beijing_time(now_utc)
    anchor_bj = datetime.combine(now_bj.date(), time(hour=hour, minute=minute))
    return anchor_bj - timedelta(hours=8)
