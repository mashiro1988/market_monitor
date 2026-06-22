# -*- coding: utf-8 -*-
"""导出角色派生（news-impact-engine Phase 3a）：人工 driver 标注 + Phase1 topic/量级
→ 每条候选的导出角色 driver/redundant/noise。驱动主题里量级最大+最早=driver 代表，
同主题其余=redundant，非驱动主题/无topic=noise；人标 driver 但无 topic 的保留 driver。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from services.annotation_service import _derive_export_roles


def _c(id, topic, mag, t):
    return {"id": id, "topic": topic, "magnitude": mag, "time": t}


def test_rep_is_biggest_then_earliest_rest_redundant():
    cands = [
        _c(1, "地缘冲突", "中", datetime(2026, 6, 1, 12, 0)),
        _c(2, "地缘冲突", "大", datetime(2026, 6, 1, 12, 10)),   # 量级大但更晚
        _c(3, "地缘冲突", "大", datetime(2026, 6, 1, 12, 5)),    # 量级大且最早 → driver 代表
    ]
    # 人只标了 #1 是 driver（指认"地缘冲突"在驱动）；代表由量级+时间自动定
    out = _derive_export_roles(cands, {1: "driver"})
    assert out == {3: "driver", 1: "redundant", 2: "redundant"}


def test_non_driving_topic_is_noise():
    cands = [
        _c(1, "通胀数据", "大", datetime(2026, 6, 1, 12, 0)),     # 驱动主题
        _c(2, "加密生态", "大", datetime(2026, 6, 1, 12, 1)),     # 非驱动主题
    ]
    out = _derive_export_roles(cands, {1: "driver"})
    assert out == {1: "driver", 2: "noise"}


def test_driver_without_topic_stays_driver():
    cands = [_c(1, None, None, datetime(2026, 6, 1, 12, 0))]
    out = _derive_export_roles(cands, {1: "driver"})
    assert out == {1: "driver"}


def test_no_human_driver_all_noise():
    cands = [_c(1, "通胀数据", "大", datetime(2026, 6, 1, 12, 0))]
    assert _derive_export_roles(cands, {}) == {1: "noise"}


def test_two_driving_topics_each_has_representative():
    cands = [
        _c(1, "通胀数据", "大", datetime(2026, 6, 1, 12, 0)),     # 主题A 代表
        _c(2, "通胀数据", "小", datetime(2026, 6, 1, 12, 1)),     # 主题A 冗余
        _c(3, "地缘冲突", "中", datetime(2026, 6, 1, 12, 2)),     # 主题B 代表
        _c(4, "加密生态", "大", datetime(2026, 6, 1, 12, 3)),     # 非驱动 → noise
    ]
    out = _derive_export_roles(cands, {2: "driver", 3: "driver"})   # 人标了 A 的冗余条 + B
    assert out == {1: "driver", 2: "redundant", 3: "driver", 4: "noise"}
