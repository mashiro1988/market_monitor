# -*- coding: utf-8 -*-
"""标注页窗口源固定段化（price-behavior-engine-phase2-plan Task 4）：
窗口 = behavior_segments（0.5 档以上，带段证据与簇拥 0.3 计数）；显式调试参数仍走原始扫描。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.behavior import BehaviorSegment
from models.price import PriceSnapshot
from services import annotation_service


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _seed_flat_prices(session, t0, n=60, symbol="BTC/USDT"):
    """平价序列：原始扫描不出任何窗口，便于区分两条路径。"""
    for i in range(n):
        session.add(PriceSnapshot(timestamp=t0 + timedelta(minutes=5 * i), asset_class="crypto",
                                  symbol=symbol, name="BTC", price=100.0, source="test"))
    session.commit()


def _seg_row(session, t0, tier_idx=1, start_min=30, end_min=45):
    import json as _json
    seg = BehaviorSegment(
        symbol="BTC/USDT", start_dt=t0 + timedelta(minutes=start_min),
        end_dt=t0 + timedelta(minutes=end_min), direction=1,
        tier_idx=tier_idx, tier_max=[0.3, 0.5, 0.8][tier_idx],
        net_pct=0.6, amp_pct=0.7, key_ts=t0 + timedelta(minutes=40),
        classification="pure_resonance" if tier_idx >= 1 else "count_only", class_version="v1",
        s_scores=_json.dumps({"NQ=F": {"s": 0.77, "ess": 4.3, "coverage": 1.0}}) if tier_idx >= 1 else None,
    )
    session.add(seg)
    session.commit()
    return seg


def test_windows_read_segments_with_evidence(session):
    t0 = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(hours=5)
    _seed_flat_prices(session, t0)
    seg = _seg_row(session, t0, tier_idx=1)                              # 0.5 档 [30,45] → 进待标
    _seg_row(session, t0, tier_idx=0, start_min=40, end_min=50)          # 0.3 与窗口重叠 → 计数
    _seg_row(session, t0, tier_idx=0, start_min=0, end_min=10)           # 窗口外（±1h 上下文）→ 不计数
    windows = annotation_service.load_price_windows(session, "BTC/USDT", hours=12)
    assert len(windows) == 1
    w = windows[0]
    assert w.window_start.timestamp_utc.startswith((t0 + timedelta(minutes=30)).isoformat()[:16])
    assert w.annotatable is True                  # 段已远离生长边缘
    assert w.price_start == 100.0 and w.price_end == 100.0
    # 段证据随行
    assert w.tier_idx == 1 and w.tier_max == 0.5
    assert w.machine_class == "pure_resonance"
    assert w.s_scores["NQ=F"]["s"] == 0.77
    assert w.cluster03_count == 1                 # 只计窗口区间内重叠的 0.3 段（2026-07-10 拍板：±1h 只画色带不计数）
    assert w.human_class is None


def test_debug_params_bypass_segments(session):
    t0 = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(hours=5)
    _seed_flat_prices(session, t0)
    _seg_row(session, t0)
    # 显式 threshold 调试路径：走原始扫描（平价 → 无窗口），不读段表
    assert annotation_service.load_price_windows(
        session, "BTC/USDT", hours=12, threshold_pct=0.5, window_minutes=15,
    ) == []


def test_annotation_overlap_matching(session):
    """段边界(0.3基座)比旧 0.5 窗口宽：历史标注按重叠≥50% 找回。"""
    from models.news import NewsPriceAnnotation
    t0 = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(hours=5)
    _seed_flat_prices(session, t0)
    _seg_row(session, t0, start_min=20, end_min=60)          # 段 t+20 ~ t+60
    session.add(NewsPriceAnnotation(
        symbol="BTC/USDT",
        window_start=t0 + timedelta(minutes=40),             # 旧 0.5 窗口 t+40 ~ t+55 ⊂ 段
        window_end=t0 + timedelta(minutes=55),
        context_start=t0, context_end=t0 + timedelta(minutes=85),
        threshold_pct=0.5, price_start=100.0, price_end=100.6, change_pct=0.6,
    ))
    session.commit()
    windows = annotation_service.load_price_windows(session, "BTC/USDT", hours=12)
    assert len(windows) == 1
    assert windows[0].annotation_id is not None              # 重叠匹配找回旧标注


def test_unsettled_segment_frozen(session):
    """settle 真空档修复（2026-07-10）：段未 settle（classification 为空 → S/机器类还没落库）
    时窗口不可标，证据不全就不该放进人工/AI 标注流。"""
    t0 = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(hours=5)
    _seed_flat_prices(session, t0)
    seg = _seg_row(session, t0, tier_idx=1)
    seg.classification = None                      # 未 settle
    seg.s_scores = None
    session.commit()
    w = annotation_service.load_price_windows(session, "BTC/USDT", hours=12)[0]
    assert w.annotatable is False                  # 远离生长边缘也不行：等 settle
    seg.classification = "pure_resonance"          # settle 落库后放开
    session.commit()
    w = annotation_service.load_price_windows(session, "BTC/USDT", hours=12)[0]
    assert w.annotatable is True


def test_overlapping_same_direction_windows_collapse(session):
    """稀释回退会吐出一串同向嵌套段（2026-07-09 实弹：01:10/01:20/01:30/01:35/01:40→02:00
    五个重叠跌窗全进待标列表）。待标窗口源折叠：同向重叠≥50%（短边分母）只留最长者；
    反向重叠（V 型顶底交叠）保留。"""
    t0 = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(hours=6)
    _seed_flat_prices(session, t0, n=80)
    _seg_row(session, t0, tier_idx=1, start_min=30, end_min=80)    # 主跌窗（最长）
    for sm in (40, 50, 55):                                        # 嵌套的稀释回退段
        seg = _seg_row(session, t0, tier_idx=1, start_min=sm, end_min=80)
        seg.direction = 1
        session.commit()
    # 上面三个与主窗同向——先都改成同向再改主窗方向，保证语义明确
    rows = session.query(BehaviorSegment).all()
    for r in rows:
        r.direction = -1 if r.start_dt == t0 + timedelta(minutes=30) or r.end_dt == t0 + timedelta(minutes=80) else r.direction
    session.commit()
    # 反向段：与主窗头部交叠（V 型），必须保留
    up = _seg_row(session, t0, tier_idx=1, start_min=25, end_min=40)
    up.direction = 1
    session.commit()
    windows = annotation_service.load_price_windows(session, "BTC/USDT", hours=12)
    downs = [w for w in windows if w.change_pct <= 0]
    ups = [w for w in windows if w.change_pct > 0]
    spans = sorted((w.window_start.timestamp_utc, w.window_end.timestamp_utc) for w in windows)
    # 平价种子下 change_pct 全为 0，用窗口边界判断：只应剩 2 个窗口（最长跌窗 + 反向涨窗）
    assert len(windows) == 2, spans
    assert (t0 + timedelta(minutes=30)).isoformat()[:16] in spans[1][0] or (t0 + timedelta(minutes=30)).isoformat()[:16] in spans[0][0]
