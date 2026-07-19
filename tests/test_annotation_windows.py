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


def test_window_annotatable_only_latest_frozen(session, monkeypatch):
    """A策略①(2026-06-28 简化)：只冻结**最新**那一个窗口；更早的窗口(哪怕也在 live 余量内)一律可标。"""
    monkeypatch.setattr(config, "ANNOTATION_SETTLE_MARGIN_MINUTES", 30)
    now = utc_now_naive()
    _seed(session, now, [(25, 100.0), (20, 101.0)])             # 较早窗口(end ~-20，也在余量内) → 仍可标
    _seed(session, now, [(10, 101.0), (5, 102.0)])              # 最新窗口(end ~-5) → 冻结
    wins = _call(session)
    frozen = [w for w in wins if not w.annotatable]
    assert len(frozen) == 1                                     # 只冻结一个
    latest = max(wins, key=lambda w: w.window_end.timestamp_utc)
    assert not latest.annotatable                              # 冻结的正是最新那个


def test_window_annotatable_old_latest_is_annotatable(session, monkeypatch):
    """最新窗口若已超 live 余量没动(收盘/静默) → 判走完、可标（不会被无限冻结）。"""
    monkeypatch.setattr(config, "ANNOTATION_SETTLE_MARGIN_MINUTES", 30)
    now = utc_now_naive()
    _seed(session, now, [(300, 100.0), (295, 101.0)])          # 唯一窗口 end ~-295(超余量) → 可标
    wins = _call(session)
    assert wins and all(w.annotatable for w in wins)


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


def test_merged_window_must_still_meet_net_threshold(session, monkeypatch):
    """同向触发之间若慢回撤稀释了首尾净幅度，不合成一个不够阈值的大窗口。"""
    monkeypatch.setattr(config, "ANNOTATION_EVENT_MERGE_GAP_MINUTES", 60)
    now = utc_now_naive()
    _seed(
        session,
        now,
        [
            (40, 100.0),
            (35, 101.0),  # +1.0% 触发
            (30, 100.7),
            (25, 100.4),
            (20, 100.1),
            (15, 99.8),
            (10, 100.4),  # 从 99.8 小涨触发；但 100.0 -> 100.4 只有 +0.4%
        ],
    )

    wins = _call(session)

    assert len(wins) == 2
    assert all(abs(w.change_pct) >= 0.5 for w in wins)
    assert all(w.actual_window_minutes == 5 for w in wins)


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
    assert set(refs) == {"纳指", "日经225", "原油", "黄金", "美债2Y", "美元指数", "BTC"}   # 来自 config 清单
    assert refs["纳指"].pct == pytest.approx(0.5, abs=0.01)
    assert refs["原油"].pct == pytest.approx(1.0, abs=0.01)
    assert refs["黄金"].pct is None                          # 无快照 → 无
    assert refs["美债2Y"].unit == "bp"                       # 收益率类品种 bp 口径
    assert all(not r.is_self for r in wins[0].references)


def test_references_none_when_market_closed(session):
    now = utc_now_naive()
    _seed(session, now, [(20, 100.0), (15, 101.0)])          # 无任何对标快照
    wins = _call(session)
    assert [r.label for r in wins[0].references] == ["纳指", "日经225", "原油", "黄金", "美债2Y", "美元指数", "BTC"]
    assert all(r.pct is None and not r.is_self for r in wins[0].references)


def test_reference_self_for_annotated_symbol(session):
    now = utc_now_naive()
    for m, p in [(20, 20000.0), (15, 20200.0)]:              # 标注 NQ 自身，+1% 触发
        _add_nq(session, now, m, p)
    session.commit()
    wins = load_price_windows(session, "NQ=F", hours=24, threshold_pct=0.5, window_minutes=5)
    assert len(wins) == 1
    refs = {r.label: r for r in wins[0].references}
    assert refs["纳指"].is_self is True and refs["纳指"].pct == pytest.approx(1.0, abs=0.01)
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


# ---- hours<=0 = 全量回溯（2026-07-19：标注页撤掉回溯筛选，前端固定传 0） ----

def _add_btc_segment(session, start, end):
    from models.behavior import BehaviorSegment
    session.add(BehaviorSegment(
        symbol="BTC/USDT", start_dt=start, end_dt=end, direction=1,
        tier_idx=1, tier_max=0.5, net_pct=0.6,
        classification="pure_resonance", class_version="v1",
    ))


def _add_btc_price(session, ts, price):
    session.add(PriceSnapshot(timestamp=ts, asset_class="crypto", symbol="BTC/USDT",
                              name="BTC", price=price, source="test"))


def test_hours_zero_full_lookback_windows(session):
    """hours<=0 = 全量：回溯到最早行为段；同一 40 天前的段在 hours=72 下不可见。"""
    now = utc_now_naive()
    start = now - timedelta(days=40)
    end = start + timedelta(minutes=30)
    _add_btc_segment(session, start, end)
    _add_btc_price(session, start, 100000.0)
    _add_btc_price(session, end, 100600.0)
    session.commit()
    assert load_price_windows(session, "BTC/USDT", hours=72) == []
    wins = load_price_windows(session, "BTC/USDT", hours=0)
    assert len(wins) == 1
    assert wins[0].change_pct == pytest.approx(0.6, abs=0.01)
    assert wins[0].annotatable                                 # 早已走完的段不冻结


def test_hours_zero_no_segments_returns_empty(session):
    """全量模式无任何行为段 → 空列表（不回退到原始扫描）。"""
    now = utc_now_naive()
    _add_btc_price(session, now - timedelta(minutes=20), 100000.0)
    _add_btc_price(session, now - timedelta(minutes=15), 100600.0)
    session.commit()
    assert load_price_windows(session, "BTC/USDT", hours=0) == []


def test_hours_zero_full_lookback_annotations(session):
    from models.news import NewsPriceAnnotation
    now = utc_now_naive()
    ws = now - timedelta(days=40)
    we = ws + timedelta(minutes=15)
    session.add(NewsPriceAnnotation(
        symbol="BTC/USDT", window_start=ws, window_end=we,
        context_start=ws, context_end=we,
        change_pct=1.0, no_clear_news=False, created_at=now, updated_at=now,
    ))
    session.commit()
    assert annotation_service.list_annotations(session, symbol="BTC/USDT", hours=24) == []
    items = annotation_service.list_annotations(session, symbol="BTC/USDT", hours=0)
    assert len(items) == 1


def test_list_annotations_carries_news_briefs_and_s_scores(session):
    """已标注列表行内嵌 driver/同簇冗余新闻摘要（driver 优先），且匹配到当前窗口时带 s_scores。"""
    import json as _json
    from models.behavior import BehaviorSegment
    from models.news import NewsItem, NewsPriceAnnotation

    now = utc_now_naive()
    start = now - timedelta(hours=10)
    end = start + timedelta(minutes=30)
    session.add(BehaviorSegment(
        symbol="BTC/USDT", start_dt=start, end_dt=end, direction=1,
        tier_idx=1, tier_max=0.5, net_pct=0.6,
        classification="pure_resonance", class_version="v1",
        s_scores=_json.dumps({"NQ=F": {"s": 0.77, "ess": 6.3, "coverage": 1.0}}),
    ))
    _add_btc_price(session, start, 100000.0)
    _add_btc_price(session, end, 100600.0)
    n1 = NewsItem(timestamp=start + timedelta(minutes=5), source="jin10", title="美军对伊朗发起打击", content="硬事件", language="zh")
    n2 = NewsItem(timestamp=start + timedelta(minutes=2), source="jin10", title="伊朗遇袭首报", content="同簇首报", language="zh")
    session.add_all([n1, n2])
    session.commit()
    session.add(NewsPriceAnnotation(
        symbol="BTC/USDT", window_start=start, window_end=end,
        context_start=start, context_end=end, change_pct=0.6,
        news_roles=_json.dumps({str(n1.id): "driver", str(n2.id): "redundant"}),
        no_clear_news=False, created_at=now, updated_at=now,
    ))
    session.commit()

    items = annotation_service.list_annotations(session, symbol="BTC/USDT", hours=0)
    assert len(items) == 1
    item = items[0]
    assert [b.role for b in item.news_briefs] == ["driver", "redundant"]   # driver 优先
    assert item.news_briefs[0].title == "美军对伊朗发起打击"
    assert item.news_briefs[1].title == "伊朗遇袭首报"
    assert item.news_briefs[0].time_bj                                     # 北京时间随行
    assert item.s_scores["NQ=F"]["s"] == 0.77                              # 与工作台同数
    assert item.needs_review is False


def test_needs_review_skips_pre_segment_era_annotations(session):
    """行为段时代之前的老标注（当时窗口源还不是行为段）匹配不到任何当前窗口，
    不该被永久打上 needs_review；时代内匹配不上的照旧标。"""
    from models.news import NewsPriceAnnotation

    now = utc_now_naive()
    seg_start = now - timedelta(days=10)
    _add_btc_segment(session, seg_start, seg_start + timedelta(minutes=30))
    _add_btc_price(session, seg_start, 100000.0)
    _add_btc_price(session, seg_start + timedelta(minutes=30), 100600.0)

    def _ann(ws):
        we = ws + timedelta(minutes=15)
        session.add(NewsPriceAnnotation(
            symbol="BTC/USDT", window_start=ws, window_end=we,
            context_start=ws, context_end=we,
            change_pct=1.0, no_clear_news=False, created_at=now, updated_at=now,
        ))

    _ann(now - timedelta(days=40))      # 时代前：不标 needs_review
    _ann(now - timedelta(days=5))       # 时代内、与段无重叠：needs_review
    session.commit()

    items = annotation_service.list_annotations(session, symbol="BTC/USDT", hours=0)
    by_start = {i.window_start.timestamp_utc: i for i in items}
    old = [i for i in items if i.needs_review is False]
    flagged = [i for i in items if i.needs_review is True]
    assert len(items) == 2
    assert len(old) == 1 and len(flagged) == 1
    assert min(by_start) == next(i.window_start.timestamp_utc for i in old)   # 更早那条 = 时代前
