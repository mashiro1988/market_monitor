"""图表数据只含「仍在跟踪」的市场。

快照带 origin（"slug:<identifier>"）时按 tracked_markets 的软删状态
**精确过滤**：删除跟踪立即清图；市场结算 / 接口抖动导致的断流不误伤。
旧数据（origin 为 NULL）退回断流启发式：最后一笔快照落后表内最新快照超过
config.PREDICTION_ACTIVE_GRACE_MINUTES（相对表内最新时间而非墙钟）即视为已停跟踪。
三个端点（/predictions、families、history）共用该过滤。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.prediction import PredictionMarket
from models.tracked_market import TrackedMarket
from services import prediction_service
from services.time_utils import utc_now_naive


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _snap(session, market_id, question, ts, prob=0.5, outcome="Yes", origin=None):
    session.add(PredictionMarket(
        timestamp=ts,
        market_id=market_id,
        question=question,
        outcome=outcome,
        probability=prob,
        volume=1000.0,
        origin=origin,
    ))


def _track(session, kind, identifier, dismissed=False, enabled=True):
    session.add(TrackedMarket(kind=kind, identifier=identifier, dismissed=dismissed, enabled=enabled))


def test_get_predictions_excludes_markets_no_longer_scanned(session):
    now = utc_now_naive()
    for k in range(3):
        _snap(session, "live", "Will it rain tomorrow?", now - timedelta(minutes=5 * k))
    # 已停止跟踪：最后一笔快照在 2 小时前，远超宽限期
    for k in range(3):
        _snap(session, "dead", "Old dismissed market?", now - timedelta(hours=2, minutes=5 * k))
    session.commit()

    res = prediction_service.get_predictions(session, hours=24)
    assert {m.market_id for m in res.markets} == {"live"}


def test_history_of_stale_market_is_empty(session):
    now = utc_now_naive()
    for k in range(3):
        _snap(session, "live", "Will it rain tomorrow?", now - timedelta(minutes=5 * k))
    for k in range(3):
        _snap(session, "dead", "Old dismissed market?", now - timedelta(hours=2, minutes=5 * k))
    session.commit()

    assert prediction_service.get_market_history(session, "dead", hours=24) == []


def test_history_of_live_market_keeps_full_lookback(session):
    """活跃市场不能被误伤：它 2 小时前的旧点位仍要完整保留在历史里。"""
    now = utc_now_naive()
    _snap(session, "live", "Will it rain tomorrow?", now - timedelta(hours=2))
    _snap(session, "live", "Will it rain tomorrow?", now - timedelta(minutes=5))
    session.commit()

    history = prediction_service.get_market_history(session, "live", hours=24)
    assert len(history) == 2


def test_families_exclude_stale_series(session):
    now = utc_now_naive()
    for n, mid in [(2, "m2"), (3, "m3")]:
        for k in range(2):
            _snap(session, mid, f"Will {n} Fed rate cuts happen in 2026?", now - timedelta(minutes=5 * k))
    # 4 cuts 桶已停止跟踪 3 小时
    for k in range(2):
        _snap(session, "m4", "Will 4 Fed rate cuts happen in 2026?", now - timedelta(hours=3, minutes=5 * k))
    session.commit()

    fams = prediction_service.get_prediction_families(session, hours=24)
    fam = next(f for f in fams if f.id == "fed_cuts_2026")
    assert {s.market_id for s in fam.series} == {"m2", "m3"}


def test_scheduler_down_keeps_markets_relative_to_latest(session):
    """调度器宕机时全表都停更：宽限期以表内最新时间为基准，不和墙钟比 → 全部保留。"""
    now = utc_now_naive()
    for mid in ("a", "b"):
        for k in range(2):
            _snap(session, mid, f"Question {mid}?", now - timedelta(hours=3, minutes=5 * k))
    session.commit()

    res = prediction_service.get_predictions(session, hours=24)
    assert {m.market_id for m in res.markets} == {"a", "b"}


def test_dismissed_origin_market_hidden_even_when_fresh(session):
    """带 origin 的快照按跟踪项软删状态精确过滤：刚软删、快照还很新 → 立即清出图表。"""
    now = utc_now_naive()
    _track(session, "slug", "old-market", dismissed=True)
    _track(session, "slug", "live-market")
    _snap(session, "dead", "Old dismissed market?", now, origin="slug:old-market")
    _snap(session, "live", "Live market?", now, origin="slug:live-market")
    session.commit()

    res = prediction_service.get_predictions(session, hours=24)
    assert {m.market_id for m in res.markets} == {"live"}
    assert prediction_service.get_market_history(session, "dead", hours=24) == []


def test_tag_origin_history_is_not_preserved_after_discovery_removed(session):
    """tag 自动发现已退场；旧 tag-origin 快照不再因为 tag 行存在而保持活跃。"""
    now = utc_now_naive()
    _track(session, "tag", "cpi")
    _track(session, "slug", "live-market")
    # CPI 桶 3 小时前断流（已结算），远超宽限期
    for k in range(3):
        _snap(session, "cpi03", "Will monthly inflation increase by 0.3% in May?",
              now - timedelta(hours=3, minutes=5 * k), origin="tag:cpi")
    # 其它市场仍在产出快照，latest_ts 持续推进
    _snap(session, "live", "Live market?", now, origin="slug:live-market")
    session.commit()

    res = prediction_service.get_predictions(session, hours=24)
    assert {m.market_id for m in res.markets} == {"live"}
    assert prediction_service.get_market_history(session, "cpi03", hours=24) == []


def test_mixed_legacy_and_origin_rows_follow_origin_verdict(session):
    """新旧数据混合：市场有 NULL origin 的历史行 + 带 origin 的新行 → 按 origin 判定，整段历史一起保留/剔除。"""
    now = utc_now_naive()
    _track(session, "slug", "kept-market")
    _track(session, "slug", "gone-market", dismissed=True)
    _track(session, "slug", "anchor")
    # kept：旧行无 origin + 新行有 origin，跟踪项活跃 → 全部保留
    _snap(session, "kept", "Kept market?", now - timedelta(hours=2))
    _snap(session, "kept", "Kept market?", now, origin="slug:kept-market")
    # gone：同样新旧混合，但跟踪项已软删 → 全部隐藏（包括 NULL 旧行）
    _snap(session, "gone", "Gone market?", now - timedelta(hours=2))
    _snap(session, "gone", "Gone market?", now, origin="slug:gone-market")
    _snap(session, "anchor", "Anchor?", now, origin="slug:anchor")
    session.commit()

    res = prediction_service.get_predictions(session, hours=24)
    assert {m.market_id for m in res.markets} == {"kept", "anchor"}
    assert len(prediction_service.get_market_history(session, "kept", hours=24)) == 2
    assert prediction_service.get_market_history(session, "gone", hours=24) == []
