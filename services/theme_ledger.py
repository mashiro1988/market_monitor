# -*- coding: utf-8 -*-
"""主题反应台账（news-impact-engine Phase 1，docs/specs/news-impact-engine-plan.md）。

引擎核心：把"每条新闻的内容标签(主题/方向/量级)" 和 "新闻之后的价格反应" 连起来，
按 (主题 × 品种) 跨时间聚合，给出该主题最近几次的反应——脱敏 / 预判都从这里取数。

故意只做定性/排名层（不做因果量级点估计）：
- forward_reaction：纯观测，news 时刻起 N 分钟价格净变动 + 振幅。
- topic_recent_reactions：同主题最近 N 次反应（可按 a-priori 量级做 severity 匹配）。
- rank_percentile：某幅度在一串同类幅度里的百分位（强/弱判定用，不用绝对阈值）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from models.news import NewsItem
from models.price import PriceSnapshot

DEFAULT_REACTION_MINUTES = 30


def forward_reaction(session: Session, symbol: str, news_time: datetime,
                     minutes: int = DEFAULT_REACTION_MINUTES) -> dict | None:
    """news 时刻起 minutes 分钟内的价格反应（观测，非因果）：
    net_pct = (末-始)/始；range_pct = (高-低)/低（收盘价口径，抓跨 bar 博弈）。
    端点/区间无快照 → None。"""
    rows = (
        session.query(PriceSnapshot.timestamp, PriceSnapshot.price)
        .filter(
            PriceSnapshot.symbol == symbol,
            PriceSnapshot.timestamp >= news_time,
            PriceSnapshot.timestamp <= news_time + timedelta(minutes=minutes),
        )
        .order_by(PriceSnapshot.timestamp.asc())
        .all()
    )
    prices = [p for _, p in rows if p]
    if len(prices) < 2:
        return None
    start, end = prices[0], prices[-1]
    if not start:
        return None
    hi, lo = max(prices), min(prices)
    return {
        "net_pct": (end - start) / abs(start) * 100,
        "range_pct": (hi - lo) / abs(lo) * 100 if lo else None,
        "start": start, "end": end, "high": hi, "low": lo,
    }


# 凑够 n 条合格反应时，最多回扫多少条同主题新闻（防"大量休市时段新闻无价格"把更早合格的饿死，
# 又不至于在超大稀疏历史上无界全表扫）。
MAX_REACTION_SCAN = 1000


def topic_recent_reactions(session: Session, topic: str, symbol: str, n: int = 5,
                           magnitude: str | None = None,
                           minutes: int = DEFAULT_REACTION_MINUTES) -> list[dict]:
    """同主题最近 N 次反应，时间倒序（最近在前）。magnitude 给定则只取同量级实例
    （severity 匹配：大比大，避免拿小事件没反应误判脱敏）。无价格反应的新闻跳过。

    分页回扫直到凑够 n 条或扫满 MAX_REACTION_SCAN —— 不能用固定 limit(n*4)，否则最近一批
    同主题新闻若都落在无价格时段（NQ=F 夜间/周末），会把更早的合格反应静默饿死（返回 < n）。"""
    base = (
        session.query(NewsItem)
        .filter(NewsItem.topic == topic, NewsItem.timestamp.isnot(None))
        .order_by(NewsItem.timestamp.desc())
    )
    if magnitude is not None:
        base = base.filter(NewsItem.magnitude_tier == magnitude)

    out: list[dict] = []
    offset, chunk = 0, max(40, max(1, n) * 4)
    while offset < MAX_REACTION_SCAN:
        batch = base.offset(offset).limit(chunk).all()
        if not batch:
            break
        for news in batch:
            r = forward_reaction(session, symbol, news.timestamp, minutes=minutes)
            if r is None:
                continue
            out.append({
                "news_id": news.id,
                "time": news.timestamp,
                "magnitude": news.magnitude_tier,
                "direction": news.news_direction,
                "net_pct": r["net_pct"],
                "range_pct": r["range_pct"],
            })
            if len(out) >= n:
                return out
        offset += chunk
    return out


def ledger_overview(session: Session, symbol: str, n: int = 5,
                    minutes: int = DEFAULT_REACTION_MINUTES) -> list[dict]:
    """台账总览：对每个有反应数据的主题给出 {topic, count, recent[]}，按 count 倒序。
    Phase 1 的人可见产出——让你直接看"哪些主题历史上动过价、最近反应趋势"。

    **仅供展示**：recent[] 混了大/中/小量级（每条带 magnitude 字段供人眼分辨），
    **不要直接拿它做强弱/脱敏判定**——那必须 severity 匹配（spec §0：拿放话比放话、
    轰炸比轰炸），由 Phase 4 警报层调 `topic_recent_reactions(..., magnitude='大')` 取数。"""
    topics = [
        t[0] for t in
        session.query(NewsItem.topic).filter(NewsItem.topic.isnot(None)).distinct().all()
    ]
    out: list[dict] = []
    for topic in topics:
        recent = topic_recent_reactions(session, topic, symbol, n=n, minutes=minutes)
        if not recent:
            continue
        out.append({"topic": topic, "count": len(recent), "recent": recent})
    out.sort(key=lambda o: o["count"], reverse=True)
    return out


def rank_percentile(value: float, population: list[float]) -> float | None:
    """|value| 在 |population| 里的百分位（0-1）= 比它小的占比。population 空 → None。
    用绝对值：判"反应强弱"看幅度大小，不看方向。"""
    if not population:
        return None
    v = abs(value)
    pop = [abs(x) for x in population]
    smaller = sum(1 for x in pop if x < v)
    return smaller / len(pop)
