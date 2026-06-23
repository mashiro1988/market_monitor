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


def test_short_silence_now_splits(session):
    """Phase 2：5min 断档下，哪怕只静默 ~15min（旧 60min 合并间隔会并成 1）也拆成 2。"""
    now = utc_now_naive()
    bars = (
        [(120, 100.0), (115, 101.0), (110, 102.0)]             # 段 A：触发 @-115,-110
        + [(105, 102.0), (100, 102.0)]                         # 静默 ~15min（< 旧 60，但 > 5）
        + [(95, 103.0), (90, 104.0)]                           # 段 B：触发 @-95,-90
    )
    _seed(session, now, bars)
    assert len(_call(session)) == 2                            # end_dt 间隔 15min > 5 → 断档拆开


def test_window_annotatable_gate(session, monkeypatch):
    """Phase3b A策略①：窗口 window_end ≤ now − 余量 才 annotatable；尾部/暂定窗口不可标。"""
    monkeypatch.setattr(config, "ANNOTATION_SETTLE_MARGIN_MINUTES", 90)
    now = utc_now_naive()
    _seed(session, now, [(300, 100.0), (295, 101.0)])           # 远窗口：结束于 ~295min 前 → 可标
    _seed(session, now, [(20, 100.0), (15, 101.0)])             # 近窗口：结束于 ~15min 前 → 不可标
    wins = _call(session)
    assert any(w.annotatable for w in wins)                     # 远窗口可标
    assert any(not w.annotatable for w in wins)                 # 近窗口不可标


def test_two_segments_beyond_gap_split(session):
    now = utc_now_naive()
    bars = (
        [(160, 100.0), (155, 101.0), (150, 102.0)]              # 段 A
        + [(m, 102.0) for m in range(145, 45, -5)]              # 长静默期
        + [(40, 103.0), (35, 104.0)]                            # 段 B：与 A 间隔远 > 5min → 拆开
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


def _add_nq(session, now, minutes_ago, price):
    session.add(PriceSnapshot(
        timestamp=now - timedelta(minutes=minutes_ago),
        asset_class="futures", symbol="NQ=F", name="纳指期货",
        price=price, source="test",
    ))


def _add_ref(session, now, symbol, minutes_ago, price):
    session.add(PriceSnapshot(
        timestamp=now - timedelta(minutes=minutes_ago),
        asset_class="commodity", symbol=symbol, name=symbol,
        price=price, source="test",
    ))


def test_window_carries_references(session):
    now = utc_now_naive()
    _seed(session, now, [(20, 100.0), (15, 101.0)])          # TEST 窗口 [-20,-15]
    _add_nq(session, now, 20, 20000.0)
    _add_nq(session, now, 15, 20100.0)                       # 纳指 +0.5%
    _add_ref(session, now, "CL=F", 20, 60.0)
    _add_ref(session, now, "CL=F", 15, 60.6)                 # 原油 +1.0%
    session.commit()
    wins = _call(session)
    assert len(wins) == 1
    refs = {r.label: r for r in wins[0].references}
    assert set(refs) == {"纳指", "原油", "黄金", "美债10Y", "美元指数", "BTC"}   # 来自 config 清单
    assert refs["纳指"].pct == pytest.approx(0.5, abs=0.01)
    assert refs["原油"].pct == pytest.approx(1.0, abs=0.01)
    assert refs["黄金"].pct is None                          # 无快照 → 无
    assert refs["美债10Y"].unit == "bp"                      # 收益率类品种 bp 口径
    assert all(not r.is_self for r in wins[0].references)


def test_references_none_when_market_closed(session):
    now = utc_now_naive()
    _seed(session, now, [(20, 100.0), (15, 101.0)])          # 无任何对标快照
    wins = _call(session)
    assert [r.label for r in wins[0].references] == ["纳指", "原油", "黄金", "美债10Y", "美元指数", "BTC"]
    assert all(r.pct is None and not r.is_self for r in wins[0].references)


def test_reference_self_for_annotated_symbol(session):
    now = utc_now_naive()
    for m, p in [(20, 20000.0), (15, 20200.0)]:              # 标注 NQ 自身，+1% 触发
        _add_nq(session, now, m, p)
    session.commit()
    wins = load_price_windows(session, "NQ=F", hours=24, threshold_pct=0.5, window_minutes=5)
    assert len(wins) == 1
    refs = {r.label: r for r in wins[0].references}
    assert refs["纳指"].is_self is True and refs["纳指"].pct is None    # 本身不对标
    assert refs["原油"].is_self is False and refs["原油"].pct is None   # 无数据


def test_list_annotations_carries_references(session):
    from models.news import NewsPriceAnnotation
    now = utc_now_naive()
    ws, we = now - timedelta(minutes=20), now - timedelta(minutes=15)
    session.add(NewsPriceAnnotation(
        symbol="BTC/USDT", window_start=ws, window_end=we,
        context_start=ws, context_end=we,           # NOT NULL 无默认，必须给
        change_pct=1.0, no_clear_news=False, created_at=now, updated_at=now,
    ))
    _add_nq(session, now, 20, 20000.0)
    _add_nq(session, now, 15, 20100.0)
    session.commit()
    items = annotation_service.list_annotations(session, symbol=None, hours=24)
    assert len(items) == 1
    refs = {r.label: r for r in items[0].references}
    assert refs["纳指"].pct == pytest.approx(0.5, abs=0.01)
