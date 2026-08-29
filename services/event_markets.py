# -*- coding: utf-8 -*-
"""事件↔预测市场挂接:读取/挂接/摘下(spec 2026-08-28 §1/§4)。
规矩与新闻挂接一致:摘下留痕不删行;auto 摘过的机器不挂回(apply 层跳过),
人工挂接(human)是明确意图、可撤销摘下。"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

import config
from models.event_market import ResearchEventMarket
from models.prediction import PredictionMarket
from models.research import ResearchEvent
from models.tracked_market import TrackedMarket
from schemas.predictions import PredictionMarketSummary
from services import prediction_service


def links_for_tracked(session: Session, tracked_ids: list[int]) -> dict[int, list[dict]]:
    """跟踪管理表的归属列:tracked_id → [{link_id, event_id, display_no, name}]。"""
    if not tracked_ids:
        return {}
    rows = (session.query(ResearchEventMarket, ResearchEvent)
            .join(ResearchEvent, ResearchEvent.id == ResearchEventMarket.event_id)
            .filter(ResearchEventMarket.tracked_id.in_(tracked_ids),
                    ResearchEventMarket.detached.is_(False))
            .order_by(ResearchEventMarket.id.asc())
            .all())
    out: dict[int, list[dict]] = defaultdict(list)
    for link, event in rows:
        out[int(link.tracked_id)].append({
            "link_id": int(link.id), "event_id": int(event.id),
            "display_no": int(event.display_no or event.id), "name": event.name,
        })
    return dict(out)


def attach_market(session: Session, event_id: int, tracked_id: int,
                  source: str = "human") -> ResearchEventMarket:
    event = (session.query(ResearchEvent)
             .filter(ResearchEvent.id == int(event_id), ResearchEvent.status == "active")
             .first())
    if event is None:
        raise ValueError("事件不存在或非进行中")
    tracked = (session.query(TrackedMarket)
               .filter(TrackedMarket.id == int(tracked_id), TrackedMarket.dismissed.is_(False))
               .first())
    if tracked is None:
        raise ValueError("跟踪项不存在或已删除")
    link = (session.query(ResearchEventMarket)
            .filter_by(event_id=int(event_id), tracked_id=int(tracked_id)).first())
    if link is not None:
        if link.detached and source == "human":
            # 人工复挂是明确意图,撤销摘下;auto 不走此路(apply 层跳过已摘)
            link.detached = False
            link.detach_reason = None
            link.link_source = "human"
        session.commit()
        return link
    link = ResearchEventMarket(event_id=int(event_id), tracked_id=int(tracked_id),
                               link_source=source)
    session.add(link)
    session.commit()
    return link


def detach_market(session: Session, link_id: int, reason: str | None = None) -> bool:
    link = session.query(ResearchEventMarket).filter(ResearchEventMarket.id == int(link_id)).first()
    if link is None:
        return False
    link.detached = True
    link.detach_reason = (reason or "").strip() or None
    session.commit()
    return True


def list_event_markets(session: Session, event_id: int) -> list[dict]:
    """事件详情市场卡:每条未摘挂接 → 跟踪项 + 旗下各市场最新概率摘要 + 断流判定。
    断流(settled)=该跟踪项最新快照落后表内最新超宽限期——结算/停更都长这样,
    曲线定格、卡片打徽章;跟踪项被软删则整卡不显示(挂接行留审计)。"""
    links = (session.query(ResearchEventMarket)
             .filter(ResearchEventMarket.event_id == int(event_id),
                     ResearchEventMarket.detached.is_(False))
             .order_by(ResearchEventMarket.created_at.asc(), ResearchEventMarket.id.asc())
             .all())
    if not links:
        return []
    tracked_rows = {int(t.id): t for t in session.query(TrackedMarket)
                    .filter(TrackedMarket.id.in_([l.tracked_id for l in links])).all()}
    table_latest = session.query(func.max(PredictionMarket.timestamp)).scalar()
    grace = timedelta(minutes=max(1, int(config.PREDICTION_ACTIVE_GRACE_MINUTES)))
    items: list[dict] = []
    for link in links:
        tracked = tracked_rows.get(int(link.tracked_id))
        if tracked is None or tracked.dismissed:
            continue
        origin = f"{tracked.kind}:{tracked.identifier}"
        rows = (session.query(PredictionMarket)
                .filter(PredictionMarket.origin == origin)
                .order_by(PredictionMarket.timestamp.desc())
                .limit(200).all())
        latest = prediction_service.latest_predictions(rows)
        by_market: dict[str, list[PredictionMarket]] = defaultdict(list)
        for row in latest:
            by_market[row.market_id].append(row)
        summaries: list[PredictionMarketSummary] = []
        newest = None
        for market_id, outcomes in by_market.items():
            ordered = sorted(outcomes, key=lambda item: item.outcome)
            summaries.append(PredictionMarketSummary(
                market_id=market_id,
                question=ordered[0].question,
                volume=ordered[0].volume,
                origin=origin,
                outcomes=[prediction_service._row_schema(r) for r in ordered],
                has_shift=any(r.prev_probability is not None
                              and abs(r.probability - r.prev_probability) >= 0.03
                              for r in ordered),
            ))
            market_newest = max(r.timestamp for r in ordered)
            if newest is None or market_newest > newest:
                newest = market_newest
        settled = bool(summaries) and table_latest is not None and newest is not None \
            and newest < table_latest - grace
        items.append({
            "link_id": int(link.id), "tracked_id": int(tracked.id),
            "slug": tracked.identifier, "display_name": tracked.display_name,
            "market": tracked.market or "macro", "enabled": bool(tracked.enabled),
            "link_source": link.link_source, "confidence": link.confidence,
            "settled": settled,
            "waiting_first_scan": not summaries,   # 新挂市场首轮采集前的占位标记
            "markets": summaries,
        })
    return items
