# -*- coding: utf-8 -*-
"""段检测纯函数（price-behavior-engine-plan Task 2）：
触发+合并语义与 annotation_service._scale_events 对齐，另加 tier_max/key_ts/net/amp。"""
from datetime import datetime, timedelta

from services.behavior_segments import Segment, detect_segments

T0 = datetime(2026, 7, 1, 0, 0, 0)
TIERS = [0.3, 0.5, 0.8]


def _series(prices, start=T0, step_min=5):
    return [(start + timedelta(minutes=step_min * i), p) for i, p in enumerate(prices)]


def test_single_segment_basic_shape():
    # 平静 → 爬升触发两次（相邻合并）→ 平静（0.31 避开 0.30 的浮点边界）
    prices = [100.00, 100.00, 100.00, 100.00, 100.15, 100.31, 100.45, 100.45, 100.45]
    segs = detect_segments(_series(prices), TIERS)
    assert len(segs) == 1
    s = segs[0]
    assert isinstance(s, Segment)
    assert s.direction == 1
    # 首触发 = i5(vs i2)，start 回看 15min = t10；末触发 i6 → end = t30
    assert s.start_dt == T0 + timedelta(minutes=10)
    assert s.end_dt == T0 + timedelta(minutes=30)
    assert abs(s.net_pct - 0.45) < 1e-6
    assert abs(s.amp_pct - 0.45) < 1e-6
    assert s.tier_idx == 0 and s.tier_max == 0.3   # 峰值 15min 变动 0.45% < 0.5
    # |5min| 最大 = t25 那根（+0.16）
    assert s.key_ts == T0 + timedelta(minutes=25)


def test_tier_max_labeling():
    # 峰值 15min 变动 0.55 → 0.5 档
    p1 = [100.00, 100.00, 100.00, 100.20, 100.40, 100.55, 100.55, 100.55, 100.55]
    s1 = detect_segments(_series(p1), TIERS)
    assert len(s1) == 1 and s1[0].tier_idx == 1 and s1[0].tier_max == 0.5
    # 峰值 0.85 → 0.8 档
    p2 = [100.00, 100.00, 100.00, 100.30, 100.60, 100.85, 100.85, 100.85, 100.85]
    s2 = detect_segments(_series(p2), TIERS)
    assert len(s2) == 1 and s2[0].tier_idx == 2 and s2[0].tier_max == 0.8


def test_direction_flip_splits():
    # 急涨后急跌：同向合并、反向劈段
    prices = [100.00, 100.00, 100.00, 100.35, 100.35, 100.35, 99.95, 99.95, 99.95, 99.95]
    segs = detect_segments(_series(prices), TIERS)
    assert len(segs) == 2
    assert segs[0].direction == 1 and segs[1].direction == -1


def test_quiet_gap_splits():
    # 两波爬升之间隔 30 分钟平静 → 覆盖区间不相邻 → 两段
    prices = ([100.00] * 3 + [100.35] * 3          # 第一波（i3 vs i0 触发）
              + [100.35] * 6                        # 平静 30min
              + [100.75] * 4)                       # 第二波（vs 100.35 ≈ +0.40）
    segs = detect_segments(_series(prices), TIERS)
    assert len(segs) == 2
    assert all(s.direction == 1 for s in segs)
    assert segs[1].start_dt - segs[0].end_dt > timedelta(minutes=5)


def test_data_hole_no_baseline_no_trigger():
    # 数据洞：基线取不到（容差 10min）就不触发
    pts = _series([100.00, 100.00, 100.00])
    # 洞后突价：15min 基线目标点（t25）±10min 容差内无数据 → 不触发
    pts.append((T0 + timedelta(minutes=40), 100.6))
    segs = detect_segments(pts, TIERS)
    assert segs == []


def test_below_base_tier_no_segment():
    prices = [100.00, 100.05, 100.10, 100.15, 100.20, 100.25]
    assert detect_segments(_series(prices), TIERS) == []
