# -*- coding: utf-8 -*-
"""_nearest_snapshot_any 行为钉死（2026-08-30 性能修复的前置保险）。

背景：标注页全量回溯下，该函数被调 7,504 次、每次线性扫描约 1 万行快照，
累计 7,500 万次比较 → 单请求 100 秒+。修复 = 换二分查找（数据本就按时间升序）。
本文件先把旧实现的全部语义钉死：精确命中 / 取更近邻 / 平手取更早行 /
容差边界（恰好等于容差算命中）/ 超容差判无 / 空列表 / 目标在序列两端之外。
换实现前后都必须全绿——绿了才能证明"只是快了，行为没变"。
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from services.annotation_service import _nearest_snapshot_any

T0 = datetime(2026, 8, 1, 12, 0, 0)
TOL = 10  # 分钟，与线上 max(SCAN_INTERVALS["price"]*2, 1) = 10 一致


def _rows(*offsets_min: float):
    """按分钟偏移构造升序快照序列（真实调用方都按 timestamp 升序传入）。"""
    return [
        SimpleNamespace(timestamp=T0 + timedelta(minutes=off), price=100.0 + i)
        for i, off in enumerate(offsets_min)
    ]


def test_empty_rows_returns_none():
    assert _nearest_snapshot_any([], T0, TOL) is None


def test_exact_hit():
    rows = _rows(0, 5, 10)
    assert _nearest_snapshot_any(rows, T0 + timedelta(minutes=5), TOL) is rows[1]


def test_picks_nearer_neighbor():
    rows = _rows(0, 5, 10)
    # 目标 6min：距 5min 行 1min、距 10min 行 4min → 取 5min 行
    assert _nearest_snapshot_any(rows, T0 + timedelta(minutes=6), TOL) is rows[1]
    # 目标 8min：距 10min 行更近 → 取 10min 行
    assert _nearest_snapshot_any(rows, T0 + timedelta(minutes=8), TOL) is rows[2]


def test_tie_prefers_earlier_row():
    """等距平手取更早那行（旧实现顺序扫描 + 严格小于 → 先到先得）。"""
    rows = _rows(0, 10)
    got = _nearest_snapshot_any(rows, T0 + timedelta(minutes=5), TOL)
    assert got is rows[0]


def test_tolerance_boundary_inclusive():
    """恰好等于容差（10min 整）算命中（旧实现是 delta > tol 才排除）。"""
    rows = _rows(0)
    assert _nearest_snapshot_any(rows, T0 + timedelta(minutes=10), TOL) is rows[0]


def test_outside_tolerance_returns_none():
    rows = _rows(0)
    assert _nearest_snapshot_any(rows, T0 + timedelta(minutes=10, seconds=1), TOL) is None


def test_target_before_first_row():
    rows = _rows(0, 5)
    assert _nearest_snapshot_any(rows, T0 - timedelta(minutes=9), TOL) is rows[0]
    assert _nearest_snapshot_any(rows, T0 - timedelta(minutes=11), TOL) is None


def test_target_after_last_row():
    rows = _rows(0, 5)
    assert _nearest_snapshot_any(rows, T0 + timedelta(minutes=14), TOL) is rows[1]
    assert _nearest_snapshot_any(rows, T0 + timedelta(minutes=16), TOL) is None


def test_single_row():
    rows = _rows(3)
    assert _nearest_snapshot_any(rows, T0, TOL) is rows[0]
