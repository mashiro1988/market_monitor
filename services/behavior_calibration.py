# -*- coding: utf-8 -*-
"""价格行为引擎 · 校准核心（docs/specs/price-behavior-engine-plan.md Task 9）。

spec §1.5「T_ref 参数验证四件套」的可复跑实现（逻辑源自 2026-07 会话实测脚本）：
  anchor        稀有度锚定反解三档 + 波动率比例双锚互证（偏差 >15% 红字告警）
  sensitivity   T×{0.5,0.75,1,1.5,2} 的 real/null/lift/分类翻转率
  null_lift     现行 config 阈值下各参照 S 的 ±24h 错位对照 lift
  session_bias  美股 RTH vs 隔夜的 null 命中率分桶（差异 >2× 才考虑分时段 T）

**脚本只建议、不改 config**（preserve-calibrated-config：产出值人工圆整、用户拍板）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

import config
from services.behavior_classifier import _points
from services.behavior_segments import Segment, detect_segments
from services.resonance_score import chg_map, s_score

DAY = timedelta(hours=24)
MULTS = (0.5, 0.75, 1.0, 1.5, 2.0)


# ---------- 公共取数 ----------

def _rolling_abs(points: list[tuple[datetime, float]]) -> list[float]:
    m = chg_map(points)
    return sorted(abs(v) for v in m.values())


def _quantile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    k = min(int(len(sorted_vals) * q), len(sorted_vals) - 1)
    return sorted_vals[k]


def _load(session: Session, symbol: str, days: int, now: datetime) -> list[tuple[datetime, float]]:
    return _points(session, symbol, now - timedelta(days=days), now)


def _btc_segments(session: Session, symbol: str, days: int, now: datetime) -> list[Segment]:
    tiers = config.BEHAVIOR_TIERS.get(symbol) or [0.3, 0.5, 0.8]
    segs = detect_segments(_load(session, symbol, days, now), tiers)
    return [s for s in segs if s.tier_idx >= 1]


def _shift(chg: dict[datetime, float], hours: int) -> dict[datetime, float]:
    off = timedelta(hours=hours)
    return {ts + off: v for ts, v in chg.items()}


def _scores(segs: list[Segment], btc_chg, ref_chg, t_btc: float, t_ref: float) -> list[float | None]:
    out = []
    for s in segs:
        r = s_score(btc_chg, ref_chg, s.start_dt, s.end_dt, t_btc, t_ref,
                    coverage_min=config.BEHAVIOR_COVERAGE_MIN)
        out.append(None if r is None else r[0])
    return out


def _hit_rate(scores: list[float | None], cut: float) -> tuple[float, int]:
    vals = [v for v in scores if v is not None]
    if not vals:
        return 0.0, 0
    return sum(1 for v in vals if abs(v) >= cut) / len(vals), len(vals)


# ---------- 四件套 ----------

def anchor_table(session: Session, symbol: str = "BTC/USDT", days: int = 30,
                 now: datetime | None = None) -> list[dict]:
    """稀有度锚定反解 + 波动率比例双锚（含未启用参照——为它们补齐三档建议值）。"""
    now = now or datetime.utcnow()
    btc_pts = _load(session, symbol, days, now)
    btc_abs = _rolling_abs(btc_pts)
    if not btc_abs:
        return []
    btc_tiers = config.BEHAVIOR_TIERS.get(symbol) or [0.3, 0.5, 0.8]
    rates = [sum(1 for v in btc_abs if v >= t) / len(btc_abs) for t in btc_tiers]
    btc_med = _quantile(btc_abs, 0.5) or 1.0
    rows = []
    for ref in config.BEHAVIOR_REF_SYMBOLS:
        ref_abs = _rolling_abs(_load(session, ref, days, now))
        if not ref_abs:
            rows.append({"symbol": ref, "n_bars": 0})
            continue
        rarity = [_quantile(ref_abs, 1 - r) for r in rates]
        ref_med = _quantile(ref_abs, 0.5) or 0.0
        volratio = [round(t * ref_med / btc_med, 4) for t in btc_tiers]
        div = (abs(volratio[0] - rarity[0]) / rarity[0] * 100) if rarity[0] else None
        rows.append({
            "symbol": ref, "n_bars": len(ref_abs),
            "rarity": [round(v, 4) for v in rarity],
            "volratio": volratio,
            "divergence_pct": round(div, 1) if div is not None else None,
            "alert": bool(div is not None and div > 15),
            "current": config.BEHAVIOR_TIERS.get(ref),
        })
    return rows


def null_lift_table(session: Session, symbol: str = "BTC/USDT", days: int = 30,
                    now: datetime | None = None) -> list[dict]:
    now = now or datetime.utcnow()
    segs = _btc_segments(session, symbol, days, now)
    btc_chg = chg_map(_load(session, symbol, days, now))
    t_btc = float((config.BEHAVIOR_TIERS.get(symbol) or [0.3])[0])
    cut = config.BEHAVIOR_S_HI
    rows = []
    for ref in config.BEHAVIOR_REF_SYMBOLS:
        tiers = config.BEHAVIOR_TIERS.get(ref)
        if not tiers:
            continue
        ref_chg = chg_map(_load(session, ref, days + 2, now))
        real, n = _hit_rate(_scores(segs, btc_chg, ref_chg, t_btc, float(tiers[0])), cut)
        nulls = (_scores(segs, btc_chg, _shift(ref_chg, 24), t_btc, float(tiers[0]))
                 + _scores(segs, btc_chg, _shift(ref_chg, -24), t_btc, float(tiers[0])))
        null, n_null = _hit_rate(nulls, cut)
        rows.append({"symbol": ref, "n": n, "real": round(real, 3), "null": round(null, 3),
                     "lift": round(real / null, 2) if null else None})
    return rows


def sensitivity_table(session: Session, ref: str, symbol: str = "BTC/USDT", days: int = 30,
                      now: datetime | None = None) -> list[dict]:
    """T_ref × 倍数扫描：real/null/lift + 相对 ×1.0 的分类翻转率（参数覆盖思想反打参数）。"""
    now = now or datetime.utcnow()
    tiers = config.BEHAVIOR_TIERS.get(ref)
    if not tiers:
        return []
    segs = _btc_segments(session, symbol, days, now)
    btc_chg = chg_map(_load(session, symbol, days, now))
    t_btc = float((config.BEHAVIOR_TIERS.get(symbol) or [0.3])[0])
    ref_chg = chg_map(_load(session, ref, days + 2, now))
    cut = config.BEHAVIOR_S_HI
    base_cls: list[bool | None] | None = None
    rows = []
    for mult in MULTS:
        t_ref = float(tiers[0]) * mult
        real_scores = _scores(segs, btc_chg, ref_chg, t_btc, t_ref)
        nulls = (_scores(segs, btc_chg, _shift(ref_chg, 24), t_btc, t_ref)
                 + _scores(segs, btc_chg, _shift(ref_chg, -24), t_btc, t_ref))
        real, n = _hit_rate(real_scores, cut)
        null, _ = _hit_rate(nulls, cut)
        cls = [None if v is None else (abs(v) >= cut) for v in real_scores]
        flip = None
        if mult == 1.0:
            base_cls = cls
        elif base_cls is not None:
            pairs = [(a, b) for a, b in zip(cls, base_cls) if a is not None and b is not None]
            flip = round(sum(1 for a, b in pairs if a != b) / len(pairs) * 100, 1) if pairs else None
        rows.append({"mult": mult, "t_ref": round(t_ref, 4), "n": n,
                     "real": round(real, 3), "null": round(null, 3),
                     "lift": round(real / null, 2) if null else None, "flip_pct": flip})
    return rows


def session_bias_table(session: Session, symbol: str = "BTC/USDT", days: int = 30,
                       now: datetime | None = None) -> list[dict]:
    """美股 RTH（13:30-20:00 UTC，夏令时近似）vs 隔夜的 null 命中率分桶。
    诊断单一 T 的时段偏置：某桶 null 率 >2× 另一桶才考虑分时段 T（spec 四件套之四）。"""
    now = now or datetime.utcnow()
    segs = _btc_segments(session, symbol, days, now)
    btc_chg = chg_map(_load(session, symbol, days, now))
    t_btc = float((config.BEHAVIOR_TIERS.get(symbol) or [0.3])[0])
    cut = config.BEHAVIOR_S_HI

    def bucket(seg: Segment) -> str:
        anchor = seg.key_ts or seg.start_dt
        minutes = anchor.hour * 60 + anchor.minute
        return "us_rth" if 13 * 60 + 30 <= minutes < 20 * 60 else "overnight"

    rows = []
    for ref in config.BEHAVIOR_REF_SYMBOLS:
        tiers = config.BEHAVIOR_TIERS.get(ref)
        if not tiers:
            continue
        ref_chg = chg_map(_load(session, ref, days + 2, now))
        by_bucket: dict[str, list[float | None]] = {"us_rth": [], "overnight": []}
        for shift_h in (24, -24):
            shifted = _shift(ref_chg, shift_h)
            for seg in segs:
                r = s_score(btc_chg, shifted, seg.start_dt, seg.end_dt, t_btc, float(tiers[0]),
                            coverage_min=config.BEHAVIOR_COVERAGE_MIN)
                by_bucket[bucket(seg)].append(None if r is None else r[0])
        rth, n_rth = _hit_rate(by_bucket["us_rth"], cut)
        ovn, n_ovn = _hit_rate(by_bucket["overnight"], cut)
        ratio = (max(rth, ovn) / min(rth, ovn)) if rth and ovn else None
        rows.append({"symbol": ref, "rth_null": round(rth, 3), "rth_n": n_rth,
                     "overnight_null": round(ovn, 3), "overnight_n": n_ovn,
                     "ratio": round(ratio, 2) if ratio else None,
                     "alert": bool(ratio and ratio > 2)})
    return rows


# ---------- 报告 ----------

def render_report(anchor: list[dict], null_lift: list[dict],
                  sensitivity: dict[str, list[dict]], session_bias: list[dict],
                  days: int, now: datetime) -> str:
    L = [f"# 行为引擎校准报告", "",
         f"生成：{now.isoformat(timespec='seconds')}Z · 回看 {days} 天 · "
         f"cutoff S_HI={config.BEHAVIOR_S_HI}（脚本只建议不改 config，产出值人工圆整拍板）", ""]
    L += ["## 1. 双锚互证（稀有度锚定 vs 波动率比例；偏差 >15% = regime 断裂警报，先查数再信数）", "",
          "| 参照 | bars | 稀有度反解(0.3/0.5/0.8档) | 波动率比例 | 偏差% | 现行 config |", "|---|---|---|---|---|---|"]
    for r in anchor:
        if not r.get("n_bars"):
            L.append(f"| {r['symbol']} | 0 | 无数据 | — | — | {config.BEHAVIOR_TIERS.get(r['symbol'])} |")
            continue
        mark = " ⚠" if r.get("alert") else ""
        L.append(f"| {r['symbol']} | {r['n_bars']} | {r['rarity']} | {r['volratio']} | {r['divergence_pct']}{mark} | {r['current']} |")
    L += ["", "## 2. 错位对照 lift（现行阈值）", "",
          "| 参照 | n | real P(|S|≥HI) | null | lift |", "|---|---|---|---|---|"]
    for r in null_lift:
        L.append(f"| {r['symbol']} | {r['n']} | {r['real']} | {r['null']} | {r['lift']} |")
    L += ["", "## 3. 敏感性扫描（±25% 误差应为个位数翻转；lift 随 T 是软旋钮不是悬崖）", ""]
    for ref, rows in sensitivity.items():
        L += [f"### {ref}", "", "| mult | T | n | real | null | lift | 翻转% |", "|---|---|---|---|---|---|---|"]
        for r in rows:
            L.append(f"| {r['mult']} | {r['t_ref']} | {r['n']} | {r['real']} | {r['null']} | {r['lift']} | {r['flip_pct'] if r['flip_pct'] is not None else '基准'} |")
        L.append("")
    L += ["## 4. 时段偏置诊断（null 率某桶 >2× 另一桶才考虑分时段 T；RTH=13:30-20:00 UTC 夏令近似）", "",
          "| 参照 | RTH null(n) | 隔夜 null(n) | 比值 |", "|---|---|---|---|"]
    for r in session_bias:
        mark = " ⚠" if r.get("alert") else ""
        L.append(f"| {r['symbol']} | {r['rth_null']} ({r['rth_n']}) | {r['overnight_null']} ({r['overnight_n']}) | {r['ratio']}{mark} |")
    L.append("")
    return "\n".join(L)
