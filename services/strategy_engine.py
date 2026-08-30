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
