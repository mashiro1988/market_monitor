"""load_price_windows 跨段合并行为。用内存 SQLite，时间戳相对 now 倒推。"""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
import models  # noqa: F401  注册模型到 Base.metadata
from models.price import PriceSnapshot
from services import annotation_service
from services.annotation_service import load_price_windows, utc_now_naive


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _isolate_rules(monkeypatch):
    # load_price_windows 会调 load_alert_price_rules()，隔离掉以免读真实库
    monkeypatch.setattr(annotation_service, "load_alert_price_rules", lambda: [])


def _seed(session, now, bars):
    """bars: list[(minutes_ago, price)]，越大越旧。"""
    for minutes_ago, price in bars:
        session.add(PriceSnapshot(
            timestamp=now - timedelta(minutes=minutes_ago),
            asset_class="crypto", symbol="TEST", name="Test",
            price=price, source="test",
        ))
    session.commit()


def _call(session):
    # window_minutes=5（基线=前一根 5min bar）、threshold=0.5%、hours=24
    return load_price_windows(session, "TEST", hours=24, threshold_pct=0.5, window_minutes=5)


def test_two_segments_within_gap_merge_into_one(session):
    now = utc_now_naive()
    bars = (
        [(120, 100.0), (115, 101.0), (110, 102.0)]              # 段 A：触发 @-115,-110
        + [(m, 102.0) for m in (105, 100, 95, 90, 85, 80, 75)]  # 静默期，无触发
        + [(70, 103.0), (65, 104.0)]                            # 段 B：与 A 静默间隔 35min<60
    )
    _seed(session, now, bars)
    wins = _call(session)
    assert len(wins) == 1
    w = wins[0]
    assert w.segment_count == 4
    assert w.change_pct == pytest.approx(4.0, abs=0.05)         # (104-100)/100
    assert w.high_price == pytest.approx(104.0)
    assert w.low_price == pytest.approx(100.0)
    assert w.peak_change_pct >= w.change_pct - 1e-6
    assert w.high_price >= w.price_end and w.low_price <= w.price_start


def test_two_segments_beyond_gap_split(session):
    now = utc_now_naive()
    bars = (
        [(160, 100.0), (155, 101.0), (150, 102.0)]              # 段 A
        + [(m, 102.0) for m in range(145, 45, -5)]              # 长静默期（>60min）
        + [(40, 103.0), (35, 104.0)]                            # 段 B：与 A 间隔 >60 → 拆开
    )
    _seed(session, now, bars)
    wins = _call(session)
    assert len(wins) == 2


def test_opposite_direction_does_not_merge(session):
    now = utc_now_naive()
    bars = [(120, 100.0), (115, 101.0)]                         # 段 A：+1% 触发 @-115
    bars += [(m, 101.0) for m in (110, 105)]                    # 静默
    bars += [(100, 100.0)]                                      # 段 B：-0.99% 触发 @-100（反向）
    _seed(session, now, bars)
    wins = _call(session)
    assert len(wins) == 2                                       # 方向不同不并


def test_single_segment(session):
    now = utc_now_naive()
    _seed(session, now, [(20, 100.0), (15, 101.0)])             # 单触发 @-15
    wins = _call(session)
    assert len(wins) == 1
    assert wins[0].segment_count == 1
    assert wins[0].peak_change_pct == pytest.approx(wins[0].change_pct, abs=1e-6)


def test_merge_gap_is_configurable(session, monkeypatch):
    monkeypatch.setattr(config, "ANNOTATION_EVENT_MERGE_GAP_MINUTES", 30)
    now = utc_now_naive()
    bars = (
        [(120, 100.0), (115, 101.0), (110, 102.0)]
        + [(m, 102.0) for m in (105, 100, 95, 90, 85, 80, 75)]
        + [(70, 103.0), (65, 104.0)]                            # 静默 35min > 30 → 拆成 2
    )
    _seed(session, now, bars)
    assert len(_call(session)) == 2
