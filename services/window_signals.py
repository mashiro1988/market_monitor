# -*- coding: utf-8 -*-
"""标注窗口的派生信号（news-impact-engine annotation-refinements Part B）。

喂给 auto-annotate 的 reasoner，帮它判 driver：
- first_trigger_segment：窗口内**第一个显著波动的 5min bar**（真正的加速触发时点），driver 通常在其附近。
- pre_window_move：窗口前一段的净变动，用来识别情绪反转（前涨后跌 = 情绪挤压、多半无 driver）。

纯 compute-on-read，从 price_snapshots 的 5min 收盘价算，不落库。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from models.price import PriceSnapshot


def _closes(session: Session, symbol: str, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    rows = (
        session.query(PriceSnapshot.timestamp, PriceSnapshot.price)
        .filter(
            PriceSnapshot.symbol == symbol,
            PriceSnapshot.timestamp >= start,
            PriceSnapshot.timestamp <= end,
            PriceSnapshot.price.isnot(None),
        )
        .order_by(PriceSnapshot.timestamp.asc())
        .all()
    )
    return [(t, float(p)) for t, p in rows if p]


def first_trigger_segment(session: Session, symbol: str, window_start: datetime, window_end: datetime,
                          floor_pct: float = 0.1, peak_frac: float = 0.5) -> dict | None:
    """窗口内**第一个显著波动的 5min bar**（第一个 |Δ%| ≥ max(floor_pct, peak_frac×窗口内峰值 bar)），
    返回 {start, end, pct}。这是价格**开始**剧烈反应的触发时点（跳过前面平的一段）——driver 常在其附近。
    无数据 / 无波动 → None。"""
    closes = _closes(session, symbol, window_start, window_end)
    if len(closes) < 2:
        return None
    bars = []  # (start_ts, end_ts, pct)
    for i in range(1, len(closes)):
        p0, p1 = closes[i - 1][1], closes[i][1]
        if not p0:
            continue
        bars.append((closes[i - 1][0], closes[i][0], (p1 - p0) / abs(p0) * 100))
    if not bars:
        return None
    peak = max(abs(b[2]) for b in bars)
    if peak <= 0:
        return None
    threshold = max(floor_pct, peak_frac * peak)
    for start_ts, end_ts, pct in bars:
        if abs(pct) >= threshold:
            return {"start": start_ts, "end": end_ts, "pct": pct}
    return None


def pre_window_move(session: Session, symbol: str, window_start: datetime, minutes: int = 30) -> float | None:
    """窗口起点前 minutes 分钟的净变动 %（识别情绪反转：前猛涨、窗口猛跌 = 情绪挤压）。<2 点 → None。"""
    closes = _closes(session, symbol, window_start - timedelta(minutes=minutes), window_start)
    if len(closes) < 2:
        return None
    p0, p1 = closes[0][1], closes[-1][1]
    if not p0:
        return None
    return (p1 - p0) / abs(p0) * 100
