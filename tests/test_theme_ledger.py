# -*- coding: utf-8 -*-
"""主题反应台账（news-impact-engine Phase 1）：
前向反应度量(净+振幅) + 按 主题×品种 聚合最近 N 次 + 同类排名(百分位/档位)。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.news import NewsItem
from models.price import PriceSnapshot
from services import theme_ledger
from services.time_utils import utc_now_naive


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _price(s, symbol, ts, price):
    s.add(PriceSnapshot(timestamp=ts, asset_class="crypto", symbol=symbol, name=symbol, price=price, source="t"))


# ---------- 前向反应度量 ----------

def test_forward_reaction_net_and_range(session):
    """news 时刻起 30 分钟：净 = (末-始)/始；振幅 = (高-低)/低（收盘价口径）。"""
    t0 = datetime(2026, 6, 1, 12, 0)
    # 5m 序列：100 → 102（中途冲到 103 再回落）→ end 101
    for k, p in [(0, 100.0), (5, 103.0), (10, 102.0), (15, 99.0), (20, 101.0), (25, 101.0), (30, 101.0)]:
        _price(session, "BTC/USDT", t0 + timedelta(minutes=k), p)
    session.commit()
    r = theme_ledger.forward_reaction(session, "BTC/USDT", t0, minutes=30)
    assert r is not None
    assert r["net_pct"] == pytest.approx(1.0, abs=0.01)        # 100 → 101
    assert r["range_pct"] == pytest.approx((103 - 99) / 99 * 100, abs=0.01)  # 高103 低99


def test_forward_reaction_none_without_data(session):
    assert theme_ledger.forward_reaction(session, "BTC/USDT", datetime(2026, 6, 1, 12, 0), minutes=30) is None


# ---------- 主题聚合：最近 N 次 ----------

def _news(s, topic, ts, magnitude="大", direction="利空", traditional_open=True):
    n = NewsItem(timestamp=ts, source="jin10", title=f"{topic}事件", content="", language="zh",
                 topic=topic, magnitude_tier=magnitude, news_direction=direction,
                 traditional_open=traditional_open, tagged_at=ts)
    s.add(n)
    return n


def test_topic_recent_reactions_ordered(session):
    """同主题的最近 N 次反应（按时间倒序），每条带净+振幅。"""
    base = datetime(2026, 6, 1, 12, 0)
    # 三条伊朗地缘新闻，各自后 30 分钟价格反应不同
    for i, (day, drop) in enumerate([(0, 0.015), (5, 0.010), (10, 0.002)]):
        nt = base + timedelta(days=day)
        _news(session, "地缘冲突", nt)
        _price(session, "BTC/USDT", nt, 100.0)
        _price(session, "BTC/USDT", nt + timedelta(minutes=30), 100.0 * (1 - drop))
    session.commit()
    recent = theme_ledger.topic_recent_reactions(session, "地缘冲突", "BTC/USDT", n=5)
    assert len(recent) == 3
    # 倒序：最近的在前（drop 0.002）
    assert recent[0]["net_pct"] == pytest.approx(-0.2, abs=0.02)
    assert recent[-1]["net_pct"] == pytest.approx(-1.5, abs=0.02)
    assert all("magnitude" in r and "news_id" in r for r in recent)


def test_topic_recent_reactions_excludes_unelapsed_window(session):
    """反应窗口还没走完(< minutes)的新闻不进——反应不完整、数据可能还没被 gap-repair settle。"""
    now = datetime(2026, 6, 20, 12, 0)
    recent = now - timedelta(minutes=5)        # 5 分钟前发的，30min 窗口未走完
    _news(session, "地缘冲突", recent)
    _price(session, "BTC/USDT", recent, 100.0)
    _price(session, "BTC/USDT", recent + timedelta(minutes=5), 99.0)
    old = now - timedelta(hours=2)             # 2 小时前，已走完
    _news(session, "地缘冲突", old)
    _price(session, "BTC/USDT", old, 100.0)
    _price(session, "BTC/USDT", old + timedelta(minutes=30), 98.0)
    session.commit()
    r = theme_ledger.topic_recent_reactions(session, "地缘冲突", "BTC/USDT", n=5, now=now)
    assert len(r) == 1
    assert r[0]["net_pct"] == pytest.approx(-2.0, abs=0.05)


def test_nq_ledger_filters_closed_period_news(session):
    """NQ 这类传统市场品种：休市时段(traditional_open=False)发的新闻直接被滤掉，
    不进候选；BTC(crypto)则不滤。"""
    base = datetime(2026, 6, 1, 12, 0)
    # 休市发的（不该进 NQ 台账）；给它价格也没用
    _news(session, "地缘冲突", base + timedelta(days=10), traditional_open=False)
    _price(session, "NQ=F", base + timedelta(days=10), 20000.0)
    _price(session, "NQ=F", base + timedelta(days=10, minutes=30), 19800.0)
    # 开市发的（该进）
    _news(session, "地缘冲突", base, traditional_open=True)
    _price(session, "NQ=F", base, 20000.0)
    _price(session, "NQ=F", base + timedelta(minutes=30), 19700.0)
    session.commit()
    nq = theme_ledger.topic_recent_reactions(session, "地缘冲突", "NQ=F", n=5)
    assert len(nq) == 1                                  # 只剩开市那条
    assert nq[0]["net_pct"] == pytest.approx(-1.5, abs=0.05)


def test_nq_forward_reaction_skips_window_crossing_daily_halt(session):
    """16:55 ET 的新闻虽然发布时开市，但 30min 反应窗跨进 CME 日休，应跳过。"""
    news_time = datetime(2026, 6, 15, 20, 55)  # Mon 16:55 ET
    _price(session, "NQ=F", news_time, 20000.0)
    _price(session, "NQ=F", news_time + timedelta(minutes=30), 19800.0)
    session.commit()

    assert theme_ledger.forward_reaction(session, "NQ=F", news_time, minutes=30) is None


def test_crypto_ledger_ignores_traditional_open_flag(session):
    """BTC 24h：即便某条 traditional_open=False（周末发的）也照样进台账。"""
    base = datetime(2026, 6, 1, 12, 0)
    _news(session, "加密生态", base, traditional_open=False)
    _price(session, "BTC/USDT", base, 100.0)
    _price(session, "BTC/USDT", base + timedelta(minutes=30), 103.0)
    session.commit()
    btc = theme_ledger.topic_recent_reactions(session, "加密生态", "BTC/USDT", n=5)
    assert len(btc) == 1


def test_topic_recent_reactions_severity_filter(session):
    """severity 匹配：只取同等量级的实例（大比大）。"""
    base = datetime(2026, 6, 1, 12, 0)
    _news(session, "地缘冲突", base, magnitude="大")
    _price(session, "BTC/USDT", base, 100.0); _price(session, "BTC/USDT", base + timedelta(minutes=30), 98.0)
    _news(session, "地缘冲突", base + timedelta(days=1), magnitude="小")
    _price(session, "BTC/USDT", base + timedelta(days=1), 100.0); _price(session, "BTC/USDT", base + timedelta(days=1, minutes=30), 99.9)
    session.commit()
    big = theme_ledger.topic_recent_reactions(session, "地缘冲突", "BTC/USDT", n=5, magnitude="大")
    assert len(big) == 1 and big[0]["magnitude"] == "大"


# ---------- 排名 ----------

def test_rank_percentile():
    """某值在一串幅度里的百分位（绝对值口径）。"""
    population = [2.0, 1.0, 0.5, 0.3, 0.1]
    assert theme_ledger.rank_percentile(1.5, population) == pytest.approx(0.8, abs=0.01)   # 比 4/5 大
    assert theme_ledger.rank_percentile(0.05, population) == pytest.approx(0.0, abs=0.01)
    assert theme_ledger.rank_percentile(3.0, population) == pytest.approx(1.0, abs=0.01)


def test_rank_percentile_empty():
    assert theme_ledger.rank_percentile(1.0, []) is None


# ---------- 台账总览 ----------

def test_ledger_overview_groups_by_topic(session):
    """每个有反应数据的主题 → 一条：count + 最近反应列表，按 count 倒序。"""
    base = datetime(2026, 6, 1, 12, 0)
    # 地缘 2 条有反应，通胀 1 条
    for i, (topic, day, drop) in enumerate([("地缘冲突", 0, 0.015), ("地缘冲突", 5, 0.005), ("通胀数据", 2, 0.008)]):
        nt = base + timedelta(days=day)
        _news(session, topic, nt)
        _price(session, "BTC/USDT", nt, 100.0)
        _price(session, "BTC/USDT", nt + timedelta(minutes=30), 100.0 * (1 - drop))
    session.commit()
    overview = theme_ledger.ledger_overview(session, "BTC/USDT", n=5)
    by_topic = {o["topic"]: o for o in overview}
    assert by_topic["地缘冲突"]["count"] == 2
    assert by_topic["通胀数据"]["count"] == 1
    assert overview[0]["topic"] == "地缘冲突"          # count 倒序在前
    assert len(by_topic["地缘冲突"]["recent"]) == 2


def test_ledger_overview_skips_topics_without_reactions(session):
    """打了标但价格无反应数据的主题不出现。"""
    base = datetime(2026, 6, 1, 12, 0)
    _news(session, "加密监管", base)   # 没喂价格
    session.commit()
    assert theme_ledger.ledger_overview(session, "BTC/USDT", n=5) == []
