# -*- coding: utf-8 -*-
"""分类 job（price-behavior-engine-plan Task 5）：十字格、无对照×新闻、幂等、PIT 日汇总。"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.behavior import BehaviorDailySummary, BehaviorSegment
from models.news import NewsItem
from models.price import PriceSnapshot
from services import behavior_classifier as bc

T0 = datetime(2026, 7, 8, 12, 0)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _seed_prices(session, symbol, prices, start=T0, step_min=5, asset_class="crypto"):
    for i, p in enumerate(prices):
        session.add(PriceSnapshot(
            timestamp=start + timedelta(minutes=step_min * i),
            asset_class=asset_class, symbol=symbol, name=symbol, price=p, source="test",
        ))
    session.commit()


def _btc_with_push(follow_scale=None):
    """BTC：平静 1.5h → 15min 内 +0.6%（0.5 档段）→ 平静 2h。
    follow_scale 不为 None 时返回按比例跟随的参照序列（0.25% 满 NQ 档）。"""
    quiet_pre, push, quiet_post = 18, 3, 24
    btc = [100.0] * quiet_pre + [100.2, 100.4, 100.6] + [100.6] * quiet_post
    if follow_scale is None:
        return btc
    ref = [100.0] * quiet_pre + [100.0 + 0.1 * follow_scale, 100.0 + 0.2 * follow_scale,
                                 100.0 + 0.25 * follow_scale] + [100.0 + 0.25 * follow_scale] * quiet_post
    return btc, ref


def _now_after(prices, margin_min=160):
    return T0 + timedelta(minutes=5 * (len(prices) - 1) + margin_min)


def _one_composed_segment(session):
    rows = session.query(BehaviorSegment).filter(BehaviorSegment.tier_idx >= 1).all()
    assert len(rows) == 1
    return rows[0]


# ---------- 十字格（纯函数全覆盖） ----------

def test_classify_cell_grid():
    hi = 0.5
    assert bc._classify_cell(0.77, True, True, hi) == "macro_news"
    assert bc._classify_cell(0.77, True, False, hi) == "pure_resonance"
    assert bc._classify_cell(0.12, True, True, hi) == "industry_news"
    assert bc._classify_cell(0.12, True, False, hi) == "sentiment"
    assert bc._classify_cell(None, False, True, hi) == "no_ref_news"     # 无对照 ≠ 无新闻
    assert bc._classify_cell(None, False, False, hi) == "no_ref_pending"


# ---------- 集成（合成价格 + 真实检测/评分链路） ----------

def test_pure_resonance_and_macro_news(session, monkeypatch):
    btc, nq = _btc_with_push(follow_scale=1.0)
    _seed_prices(session, "BTC/USDT", btc)
    _seed_prices(session, "NQ=F", nq, asset_class="futures")
    now = _now_after(btc)
    stats = bc.classify(session, "BTC/USDT", now=now)
    assert stats["classified"] == 1
    row = _one_composed_segment(session)
    assert row.classification == "pure_resonance"
    scores = json.loads(row.s_scores)
    assert scores["NQ=F"]["s"] >= 0.5
    # 补一条大新闻 → 重跑（换 class_version 强制重分类）→ macro_news
    session.add(NewsItem(timestamp=row.start_dt + timedelta(minutes=5),
                         source="test", title="CPI 低于预期", magnitude_tier="大"))
    session.commit()
    monkeypatch.setattr(bc, "CLASS_VERSION", "v-test")
    bc.classify(session, "BTC/USDT", now=now)
    row = _one_composed_segment(session)
    assert row.classification == "macro_news"
    assert json.loads(row.news_ids)


def test_sentiment_when_ref_flat(session):
    btc = _btc_with_push()
    nq = [100.0] * len(btc)                      # 参照全程没动
    _seed_prices(session, "BTC/USDT", btc)
    _seed_prices(session, "NQ=F", nq, asset_class="futures")
    bc.classify(session, "BTC/USDT", now=_now_after(btc))
    row = _one_composed_segment(session)
    assert row.classification == "sentiment"
    assert abs(json.loads(row.s_scores)["NQ=F"]["s"]) < 0.3


def test_no_ref_news_when_refs_closed(session):
    btc = _btc_with_push()
    _seed_prices(session, "BTC/USDT", btc)       # 不给任何参照数据 = 宏观休市
    session.add(NewsItem(timestamp=T0 + timedelta(minutes=95),
                         source="test", title="周末地缘冲突升级", magnitude_tier="大"))
    session.commit()
    bc.classify(session, "BTC/USDT", now=_now_after(btc))
    row = _one_composed_segment(session)
    assert row.classification == "no_ref_news"   # 无对照 ≠ 无宏观新闻
    assert row.s_scores == "{}"


def test_count_only_and_idempotent(session):
    btc = _btc_with_push()
    _seed_prices(session, "BTC/USDT", btc)
    now = _now_after(btc)
    bc.classify(session, "BTC/USDT", now=now)
    n1 = session.query(BehaviorSegment).count()
    bc.classify(session, "BTC/USDT", now=now)    # 幂等：不重复建段
    assert session.query(BehaviorSegment).count() == n1
    # 0.3 档段（若检出）标 count_only
    for r in session.query(BehaviorSegment).filter_by(tier_idx=0):
        assert r.classification == "count_only"


def test_daily_summary_pit(session):
    btc = _btc_with_push()
    _seed_prices(session, "BTC/USDT", btc)
    bc.classify(session, "BTC/USDT", now=_now_after(btc))
    d = T0.strftime("%Y-%m-%d")
    bc.write_daily_summary(session, "BTC/USDT", d, now=T0 + timedelta(hours=13))
    bc.write_daily_summary(session, "BTC/USDT", d, now=T0 + timedelta(hours=14))
    rows = session.query(BehaviorDailySummary).filter_by(utc_date=d).all()
    assert len(rows) == 2                        # PIT 追加不覆盖
    counts = json.loads(rows[-1].counts)
    assert counts["0.5"]["up"] == 1
    assert rows[-1].day_type == "weekday"        # 2026-07-08 周三
