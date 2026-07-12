# -*- coding: utf-8 -*-
"""价格行为引擎 · 段检测（docs/specs/price-behavior-engine-plan.md Task 2）。

纯函数：不读库、不碰标注配置。触发 + 合并语义**照搬** `annotation_service._scale_events`
（已校准的生产行为，见其 docstring 里 start_dt 合并判据与稀释回退的线上教训），在其上补
行为引擎需要的段属性：最高触及档位（tier）、关键触发时间（key_ts）、净幅、振幅。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

WINDOW_MINUTES = 15          # 触发原语：15min 开收净（spec v0.4 锁定，与生产标注窗口同源）
DEFAULT_TOLERANCE_MIN = 10   # 基线取点容差（= 生产 max(price_interval*2, 1)）
DEFAULT_MERGE_GAP_MIN = 5    # 覆盖区间相邻判据（= 生产 ANNOTATION_EVENT_MERGE_GAP_MINUTES 现值）


@dataclass
class Segment:
    """一个异动段（归因与计数的唯一单位）。"""
    start_dt: datetime      # 段起（首触发回看 15min 的基线时刻）
    end_dt: datetime        # 段止（末触发扫描点）
    direction: int          # +1 涨 / -1 跌
    tier_idx: int           # 触及的最高档位序 0/1/2（跨资产可比）
    tier_max: float         # 触及的最高档阈值（该资产口径，%）
    net_pct: float          # 段首基准 → 段尾收盘 净幅（%）
    amp_pct: float          # 段内最高最低价差 / 段首基准（%）
    key_ts: datetime        # 段内 |5min 变化| 最大的 bar 时刻（新闻对时锚点）


def _nearest(points: list[tuple[datetime, float]], target: datetime,
             not_after: datetime, tolerance: timedelta) -> tuple[datetime, float] | None:
    """target ±tolerance 内、且不晚于 not_after 的最近点（同距取更早）。"""
    best: tuple[datetime, float] | None = None
    best_gap: timedelta | None = None
    for ts, price in points:
        if ts > not_after:
            break
        gap = abs(ts - target)
        if gap > tolerance:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = (ts, price), gap
    return best


def detect_segments(points: list[tuple[datetime, float]], tiers: list[float],
                    merge_gap_min: int = DEFAULT_MERGE_GAP_MIN,
                    tolerance_min: int = DEFAULT_TOLERANCE_MIN) -> list[Segment]:
    """基档（tiers[0]）触发扫描 + 同向相邻合并 → 段列表（含档位/key_ts/净幅/振幅）。

    - 触发：|(current − baseline_{t−15min}) / baseline| ≥ tiers[0]，baseline 允许 ±tolerance 取最近点。
    - 合并：同方向且新触发 start_dt 与上一段 end_dt 间隔 ≤ merge_gap（**start_dt 判据**，防重叠拆窗）。
    - 稀释回退：合并后首尾净幅 < 基档或方向翻转 → 退回逐触发成段（与生产一致）。
    """
    if not points or not tiers:
        return []
    pts = sorted(points)
    base = float(tiers[0])
    wm = timedelta(minutes=WINDOW_MINUTES)
    tol = timedelta(minutes=tolerance_min)
    merge_gap = timedelta(minutes=merge_gap_min)

    # —— 触发扫描（每个点回看 15min 开收净）——
    # 边界防护（2026-07-12 实弹修复）：回看目标早于数据首点时不成触发——否则 48h 滑动
    # 切片让 ±tol 容差把基线滑到更近的点，10min 变动被当成 15min 净（假触发高档，
    # 事发 48h 后幽灵覆写正确 tier；见 tests::test_no_trigger_when_lookback_target_precedes_data）。
    triggers: list[dict] = []
    data_start = pts[0][0]
    for ts, price in pts:
        if ts - wm < data_start:
            continue
        baseline = _nearest(pts, ts - wm, ts, tol)
        if baseline is None or not baseline[1]:
            continue
        chg = (price - baseline[1]) / abs(baseline[1]) * 100
        if abs(chg) < base:
            continue
        triggers.append({
            "start_dt": baseline[0], "end_dt": ts,
            "price_start": baseline[1], "price_end": price,
            "sign": 1 if chg >= 0 else -1, "abs_chg": abs(chg),
        })
    if not triggers:
        return []

    # —— 同向相邻合并（start_dt 判据）——
    triggers.sort(key=lambda t: t["end_dt"])
    events: list[list[dict]] = []
    for t in triggers:
        if (events and events[-1][-1]["sign"] == t["sign"]
                and (t["start_dt"] - events[-1][-1]["end_dt"]) <= merge_gap):
            events[-1].append(t)
        else:
            events.append([t])

    def _build(ev: list[dict]) -> Segment | None:
        first, last = ev[0], ev[-1]
        if not first["price_start"]:
            return None
        net = (last["price_end"] - first["price_start"]) / abs(first["price_start"]) * 100
        if abs(net) < base:                      # 合并稀释 → 由调用侧退回单触发
            return None
        if (net >= 0) != (first["sign"] >= 0):   # 合并后方向翻转 → 同上
            return None
        start, end = first["start_dt"], last["end_dt"]
        span = [(ts, p) for ts, p in pts if start <= ts <= end]
        # 档位：段内触发 15min 变动峰值触及的最高档
        peak = max(t["abs_chg"] for t in ev)
        tier_idx = 0
        for i, th in enumerate(tiers):
            if peak >= float(th):
                tier_idx = i
        # 振幅：段内最高最低差 / 段首基准
        prices = [p for _, p in span] or [first["price_start"]]
        amp = (max(prices) - min(prices)) / abs(first["price_start"]) * 100
        # key_ts：段内 |5min 变化| 最大的 bar（相邻点间隔超容差不算 5min bar）
        key_ts, key_move = end, -1.0
        for (t1, p1), (t2, p2) in zip(span, span[1:]):
            if t2 - t1 > tol or not p1:
                continue
            move = abs(p2 - p1) / abs(p1)
            if move > key_move:
                key_move, key_ts = move, t2
        return Segment(
            start_dt=start, end_dt=end, direction=first["sign"],
            tier_idx=tier_idx, tier_max=float(tiers[tier_idx]),
            net_pct=round(net, 4), amp_pct=round(amp, 4), key_ts=key_ts,
        )

    out: list[Segment] = []
    for ev in events:
        seg = _build(ev)
        if seg is not None:
            out.append(seg)
        elif len(ev) > 1:
            for single in ev:
                s = _build([single])
                if s is not None:
                    out.append(s)
    return out
