# -*- coding: utf-8 -*-
"""价格行为引擎 · 共振分 S（docs/specs/price-behavior-engine-plan.md Task 4）。

公式（spec = volume-behavior-engine-discussion.md v0.4 §1.5，数值案例 §6 已做测试 fixture）：

    z = 15min滚动变动 / T_asset(0.3档)          —— "档位单位"，去 beta、保幅度语义
    S = Σ |z_btc|² · clip(z_ref·sign(z_btc), −1, +1) ÷ Σ |z_btc|²   ∈ [−1, +1]

读法：BTC 使劲的每个时刻，参照朝同方向跟了几成（以参照自身档位为满分）。
- 权重 = |z_btc|²（异动点自动主导，安静时刻无发言权）；
- clip ±1 = 参照动满自身一档记满分（幅度语义：参照没动，噪声同向不得分）；
- ESS = (Σw)²/Σw²（有效样本数，<5 证据薄）；
- coverage = 参照有数时刻的 BTC 权重质量占比，<coverage_min → 不出分（无对照）。
判级/符号语义归调用侧：判 |S|，符号仅展示（美元指数反向为常态）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

STEP = timedelta(minutes=5)
W15 = timedelta(minutes=15)
BIG_WINDOW_MINUTES = 60          # 段锚定大窗口：段前 1h ~ 段后 1h（事件证据窗，非滚动窗）


def chg_map(points: list[tuple[datetime, float]]) -> dict[datetime, float]:
    """15min 滚动变动（%），5min 网格：仅当恰好存在 t−15min 的点才出值（数据洞自然缺位）。"""
    prices = dict(points)
    out: dict[datetime, float] = {}
    for ts, price in prices.items():
        base = prices.get(ts - W15)
        if base:
            out[ts] = (price - base) / abs(base) * 100
    return out


def _weighted_follow(btc_chg: dict[datetime, float], ref_chg: dict[datetime, float],
                     t_btc: float, t_ref: float,
                     start: datetime, end: datetime,
                     coverage_min: float) -> tuple[float, float, float] | None:
    """[start, end] 网格上的加权跟随分。返回 (S, ESS, coverage) 或 None（无数据/覆盖不足）。"""
    num = den = den_all = sw2 = 0.0
    t = start
    while t <= end:
        zb = btc_chg.get(t)
        if zb is not None:
            zb /= t_btc
            w = zb * zb
            den_all += w
            zr = ref_chg.get(t)
            if zr is not None:
                zr /= t_ref
                v = zr if zb > 0 else -zr
                num += w * max(-1.0, min(1.0, v))
                den += w
                sw2 += w * w
        t += STEP
    if den_all <= 0:
        return None
    coverage = den / den_all
    if coverage < coverage_min or den <= 0 or sw2 <= 0:
        return None
    return num / den, den * den / sw2, coverage


def rolling_peak(btc_chg: dict[datetime, float], ref_chg: dict[datetime, float],
                 t_btc: float, t_ref: float,
                 seg_start: datetime, seg_end: datetime,
                 tail_min: int = BIG_WINDOW_MINUTES,
                 points: int = 30,
                 coverage_min: float = 0.5) -> tuple[float, float, float] | None:
    """段的联动读数（Phase 2 统一口径，2026-07-09 用户拍板"所见即所判"）：
    在"段起 → 段止 + tail"的每个 5min 时点上求拖尾 rolling S（与展示曲线同一算法、同一窗口），
    取 |S| 峰值点，返回该点的 (S, ESS, coverage)。曲线爬多高，机器判多强——判级数与
    屏幕上那条 rolling 曲线是同一个数。拖尾窗只看过去 → 数据齐备条件仍是"到 段止+tail"，
    settle 门槛不变。取代旧的事件窗 s_score（段前1h+段+段后1h 单独开窗，已删）。"""
    span = STEP * (points - 1)
    best: tuple[float, float, float] | None = None
    t = seg_start
    end = seg_end + timedelta(minutes=tail_min)
    while t <= end:
        r = _weighted_follow(btc_chg, ref_chg, t_btc, t_ref, t - span, t, coverage_min)
        if r is not None and (best is None or abs(r[0]) > abs(best[0])):
            best = r
        t += STEP
    return best


def rolling_s(btc_chg: dict[datetime, float], ref_chg: dict[datetime, float],
              t_btc: float, t_ref: float,
              start: datetime, end: datetime,
              points: int = 30,
              coverage_min: float = 0.5) -> list[tuple[datetime, float | None]]:
    """rolling S 展示曲线：每个 5min 时点向回看 `points` 个点（拖尾窗，实时可画）。
    纯展示——不触发、不分类、不告警（spec 拍板）。覆盖不足 → None（曲线断线=无对照）。"""
    out: list[tuple[datetime, float | None]] = []
    span = STEP * (points - 1)
    t = start
    while t <= end:
        r = _weighted_follow(btc_chg, ref_chg, t_btc, t_ref, t - span, t, coverage_min)
        out.append((t, None if r is None else r[0]))
        t += STEP
    return out
