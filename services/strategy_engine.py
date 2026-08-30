# -*- coding: utf-8 -*-
"""持仓策略计算引擎：纯函数，零 IO。

公式单一来源（设计稿 §2）；每日任务、overview、计算器全部只能调这里，
禁止在别处重写任何公式。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class DailyCandle:
    """一根已确认的 UTC 日 K（date = bar 起始 00:00，UTC naive）。"""
    date: datetime
    open: float
    high: float
    low: float
    close: float

    @property
    def close_time(self) -> datetime:
        return self.date + timedelta(days=1)


def ewma_vol_series(closes: list[float], alpha: float) -> list[float]:
    """对数收益的 EWMA 标准差序列。返回长度 = len(closes) - 1。

    热身：首个方差 = 首个收益的平方（起点影响在 ~37 天后衰减殆尽，设计稿 §2.2）。
    """
    vols: list[float] = []
    var: float | None = None
    for prev, cur in zip(closes, closes[1:]):
        r = math.log(cur / prev)
        var = r * r if var is None else alpha * r * r + (1 - alpha) * var
        vols.append(math.sqrt(var))
    return vols


def walk_latch(vols: list[float], threshold: float, seed: float | None = None) -> list[float]:
    """25% 守则闩锁：逐日走一遍"偏离超阈值才更新在用值"。

    seed = 已持久化的在用值（None = 冷启动，首日直接采用）。
    在用值为 0（长横盘）时下一个非零波动直接采用——否则除零。
    返回与 vols 等长的"在用波动率"序列，末位即当前应持久化的值。
    """
    used: list[float] = []
    current = seed
    for v in vols:
        if current is None or current == 0 or abs(v / current - 1) > threshold:
            current = v
        used.append(current)
    return used


@dataclass(frozen=True)
class BatchState:
    """单批次在"最新确认收盘"时点的全部读数。"""
    anchor_high: float
    soft_stop: float
    hard_stop: float
    last_close: float
    breached: bool          # 最新确认收盘 < soft
    locked: bool            # soft > 入场价（锁盈，B2 额度释放）
    occupy_usd: float       # quantity * max(0, entry - soft)
    distance_pct: float     # (last_close - soft) / soft


def anchor_high(*, entry_price: float, entry_at: datetime, candles: list[DailyCandle]) -> float:
    """锚 H = max(入场价, 入场后已收盘日 K 的收盘价)。设计稿 §2.4。"""
    closes = [c.close for c in candles if c.close_time > entry_at]
    return max([entry_price, *closes])


def batch_state(
    *, entry_price: float, entry_at: datetime, quantity: float,
    candles: list[DailyCandle], v_used: float, x_soft: int, x_hard: int,
) -> BatchState:
    h = anchor_high(entry_price=entry_price, entry_at=entry_at, candles=candles)
    soft = h * (1 - x_soft * v_used)
    hard = h * (1 - x_hard * v_used)
    last_close = candles[-1].close
    return BatchState(
        anchor_high=h,
        soft_stop=soft,
        hard_stop=hard,
        last_close=last_close,
        breached=last_close < soft,
        locked=soft > entry_price,
        occupy_usd=quantity * max(0.0, entry_price - soft),
        distance_pct=(last_close - soft) / soft,
    )


def simulate_entry(*, price: float, forecast: int, budget_usd: float, vol: float, x_soft: int) -> dict:
    """建仓计算器（设计稿 §2.7）：给定价格/信心/预算/波动率，输出止损与数量。"""
    stop_distance = price * x_soft * vol
    quantity = budget_usd * (forecast / 10.0) / stop_distance
    return {
        "stop_price": price - stop_distance,
        "stop_distance": stop_distance,
        "quantity": quantity,
        "notional_usd": quantity * price,
    }
