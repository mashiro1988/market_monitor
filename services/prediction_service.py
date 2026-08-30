from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

import config
from models.prediction import PredictionMarket
from models.tracked_market import TrackedMarket, market_filter_list
from schemas.predictions import (
    PredictionFamily,
    PredictionFamilySeries,
    PredictionMarketSummary,
    PredictionRow,
    PredictionsResponse,
    TrackedEventBrief,
    TrackedMarketCreate,
    TrackedMarketSchema,
    TrackedMarketUpdate,
)
from services.time_utils import timestamp_pair, utc_now_naive


def _row_schema(row: PredictionMarket) -> PredictionRow:
    delta_pct = None
    if row.prev_probability is not None:
        delta_pct = (row.probability - row.prev_probability) * 100
    return PredictionRow(
        market_id=row.market_id,
        question=row.question,
        outcome=row.outcome,
        probability=row.probability,
        prev_probability=row.prev_probability,
        probability_pct=row.probability * 100,
        delta_pct=delta_pct,
        volume=row.volume,
        **timestamp_pair(row.timestamp),
    )


def load_prediction_rows(session: Session, hours: int = 24, market: str | None = None) -> list[PredictionMarket]:
    hours = max(1, min(int(hours or 24), 24 * 366))
    cutoff = utc_now_naive() - timedelta(hours=hours)
    rows = (
        session.query(PredictionMarket)
        .filter(PredictionMarket.timestamp >= cutoff)
        .order_by(PredictionMarket.timestamp.asc())
        .all()
    )
    if not rows:
        return rows
    # 图表只显示「仍在跟踪」的市场，按市场粒度整体保留/剔除：
    # 1) 快照带 origin（"slug:x"）→ 按 tracked_markets 软删状态精确判定：
    #    删除跟踪立即清图；市场结算/接口抖动导致的断流不误伤。
    # 2) 旧快照（origin 为 NULL，无法关联跟踪项）→ 断流启发式兜底：最后一笔快照落后
    #    表内最新快照超过宽限期视为已停跟踪。基准取表内最新时间而非墙钟，调度器宕机不误杀。
    tracked = (
        session.query(TrackedMarket.kind, TrackedMarket.identifier, TrackedMarket.market)
        .filter(
            TrackedMarket.dismissed.is_(False),
            TrackedMarket.kind == "slug",
        )
        .all()
    )
    active_keys = {f"{t.kind}:{t.identifier}" for t in tracked}
    # 线过滤(2026-08-28):market 传入时只留该线跟踪项的市场;旧无 origin 快照算宏观
    line_keys = ({f"{t.kind}:{t.identifier}" for t in tracked if (t.market or "macro") == market}
                 if market else None)
    origins_by_market: dict[str, set[str]] = defaultdict(set)
    latest_by_market: dict[str, datetime] = {}
    for row in rows:
        if row.origin:
            origins_by_market[row.market_id].add(row.origin)
        prev_latest = latest_by_market.get(row.market_id)
        if prev_latest is None or row.timestamp > prev_latest:
            latest_by_market[row.market_id] = row.timestamp
    latest_ts = max(latest_by_market.values())
    grace = timedelta(minutes=max(1, int(config.PREDICTION_ACTIVE_GRACE_MINUTES)))

    def _visible(market_id: str) -> bool:
        origins = origins_by_market.get(market_id)
        if origins:
            if not (origins & active_keys):
                return False
            return line_keys is None or bool(origins & line_keys)
        if line_keys is not None and market != "macro":
            return False
        return latest_by_market[market_id] >= latest_ts - grace

    visible = {market_id for market_id in latest_by_market if _visible(market_id)}
    return [row for row in rows if row.market_id in visible]


def latest_predictions(rows: list[PredictionMarket]) -> list[PredictionMarket]:
    seen: dict[str, PredictionMarket] = {}
    for row in sorted(rows, key=lambda item: item.timestamp, reverse=True):
        key = f"{row.market_id}:{row.outcome}"
        if key not in seen:
            seen[key] = row
    return list(seen.values())


def classify_market_family(question: str) -> dict | None:
    q = (question or "").lower()

    if "fed rate cuts happen in 2026" in q:
        if "will no fed rate cuts happen" in q:
            return {"id": "fed_cuts_2026", "name": "2026 年 Fed 降息次数", "label": "0 cuts", "order": 0}
        if "will 12 or more fed rate cuts happen" in q:
            return {"id": "fed_cuts_2026", "name": "2026 年 Fed 降息次数", "label": "12+ cuts", "order": 12}
        match = re.search(r"will (\d+) fed rate cuts happen in 2026", q)
        if match:
            count = int(match.group(1))
            return {"id": "fed_cuts_2026", "name": "2026 年 Fed 降息次数", "label": f"{count} cuts", "order": count}

    if "after the june 2026 meeting" in q and "fed" in q:
        options = [
            ("decrease interest rates by 50+", "Cut 50+ bps", -50),
            ("decrease interest rates by 25 bps", "Cut 25 bps", -25),
            ("no change", "No change", 0),
            ("increase interest rates by 25 bps", "Hike 25 bps", 25),
            ("increase interest rates by 50+", "Hike 50+ bps", 50),
        ]
        for needle, label, order in options:
            if needle in q:
                return {"id": "fed_june_2026", "name": "2026 年 6 月 FOMC 利率决定", "label": label, "order": order}

    match = re.search(r"fed rate cut by ([a-z]+) 2026 meeting", q)
    if match:
        month = match.group(1).title()
        order = {"January": 1, "March": 3, "April": 4, "June": 6, "July": 7, "September": 9, "October": 10, "December": 12}
        return {"id": "fed_cut_by_meeting_2026", "name": "Fed 首次降息截止会议", "label": month, "order": order.get(month, 99)}

    if "upper bound of the target federal funds rate" in q and "end of 2026" in q:
        match = re.search(r"be (.+?) at the end of 2026", question or "", flags=re.IGNORECASE)
        label = match.group(1).strip() if match else question[:40]
        number = re.search(r"(\d+(?:\.\d+)?)", label)
        return {"id": "fed_funds_upper_bound_eoy_2026", "name": "2026 年底 Fed Funds 上限", "label": label, "order": float(number.group(1)) if number else 999.0}

    match = re.search(r"inflation reach more than ([0-9.]+)% in 2026", q)
    if match:
        threshold = float(match.group(1))
        return {"id": "inflation_threshold_2026", "name": "2026 年美国通胀阈值", "label": f">{threshold:g}%", "order": threshold}

    # 核心 CPI 月环比：同一个月的所有区间桶（如 0.1% / 0.2% / ≤-0.3% / ≥0.6%）聚成一族。
    # 真实问法："Will Core CPI MoM be 0.3% in May?" / "...be -0.3% or less in May?"
    match = re.search(r"core cpi mom be (-?[0-9.]+)%(?: (or less|or more))? in (\w+)", q)
    if match:
        value = float(match.group(1))
        bound = match.group(2)
        month = match.group(3).title()
        label = f"≤{value:g}%" if bound == "or less" else f"≥{value:g}%" if bound == "or more" else f"{value:g}%"
        return {"id": f"core_cpi_mom_{month.lower()}", "name": f"{month} 核心CPI月环比", "label": label, "order": value}

    # 月度通胀（headline CPI 月环比）："Will monthly inflation increase by 0.3% in May?"
    match = re.search(r"monthly inflation increase by (-?[0-9.]+)%(?: (or less|or more))? in (\w+)", q)
    if match:
        value = float(match.group(1))
        bound = match.group(2)
        month = match.group(3).title()
        label = f"≤{value:g}%" if bound == "or less" else f"≥{value:g}%" if bound == "or more" else f"{value:g}%"
        return {"id": f"inflation_mom_{month.lower()}", "name": f"{month} 月度通胀", "label": label, "order": value}

    if "strait of hormuz traffic returns to normal by" in q:
        label_match = re.search(r"by (.+?)\?", question or "", flags=re.IGNORECASE)
        label = label_match.group(1).strip() if label_match else question[:40]
        order_map = {"end of april": 4.9, "may 15": 5.5, "end of may": 5.9, "end of june": 6.9, "april 30": 4.3}
        return {"id": "hormuz_normalization", "name": "霍尔木兹海峡通行恢复", "label": label, "order": order_map.get(label.lower(), 99)}

    if "unrestricted shipping through hormuz in april" in q:
        return {"id": "hormuz_normalization", "name": "霍尔木兹海峡通行恢复", "label": "Unrestricted in April", "order": 4.1}

    match = re.search(r"will wti crude oil.*?hit \((high|low)\) \$([0-9]+) in ([a-z]+)", q)
    if match:
        side, price_str, month = match.group(1), match.group(2), match.group(3).title()
        price = int(price_str)
        if side == "high":
            return {
                "id": f"wti_high_{month.lower()}",
                "name": f"WTI 原油 {month} 触及上沿",
                "label": f"≥${price}",
                "order": float(price),
            }
        return {
            "id": f"wti_low_{month.lower()}",
            "name": f"WTI 原油 {month} 触及下沿",
            "label": f"≤${price}",
            "order": float(price),
        }

    return None


def get_prediction_families(session: Session, hours: int = 24, search: str | None = None,
                            market: str | None = None) -> list[PredictionFamily]:
    rows = load_prediction_rows(session, hours, market=market)
    groups: dict[str, dict] = {}
    for row in rows:
        if str(row.outcome).lower() != "yes":
            continue
        family = classify_market_family(row.question)
        if not family:
            continue
        if search and search.lower() not in row.question.lower() and search.lower() not in family["name"].lower():
            continue
        group = groups.setdefault(family["id"], {"name": family["name"], "series": {}})
        series = group["series"].setdefault(
            row.market_id,
            {"label": family["label"], "order": family["order"], "question": row.question, "rows": []},
        )
        series["rows"].append(row)

    result: list[PredictionFamily] = []
    for group_id, group in groups.items():
        if len(group["series"]) < 2:
            continue
        series_items = [
            PredictionFamilySeries(
                market_id=market_id,
                question=series["question"],
                label=series["label"],
                order=series["order"],
                points=[_row_schema(row) for row in sorted(series["rows"], key=lambda item: item.timestamp)],
            )
            for market_id, series in sorted(group["series"].items(), key=lambda item: (item[1]["order"], item[1]["label"]))
        ]
        result.append(PredictionFamily(id=group_id, name=group["name"], series=series_items))
    return sorted(result, key=lambda item: item.name)


def get_predictions(session: Session, hours: int = 24, search: str | None = None,
                    market: str | None = None) -> PredictionsResponse:
    rows = load_prediction_rows(session, hours, market=market)
    latest = latest_predictions(rows)
    by_market: dict[str, list[PredictionMarket]] = defaultdict(list)
    for row in latest:
        if search and search.lower() not in row.question.lower() and search.lower() not in row.market_id.lower():
            continue
        by_market[row.market_id].append(row)

    markets: list[PredictionMarketSummary] = []
    for market_id, outcomes in by_market.items():
        ordered = sorted(outcomes, key=lambda item: item.outcome)
        has_shift = any(
            row.prev_probability is not None and abs(row.probability - row.prev_probability) >= 0.03
            for row in ordered
        )
        markets.append(
            PredictionMarketSummary(
                market_id=market_id,
                question=ordered[0].question,
                volume=ordered[0].volume,
                origin=ordered[0].origin,
                outcomes=[_row_schema(row) for row in ordered],
                has_shift=has_shift,
            )
        )
    markets.sort(key=lambda item: (not item.has_shift, item.question))
    latest_ts = max((row.timestamp for row in latest), default=None)
    return PredictionsResponse(markets=markets, latest_timestamp=timestamp_pair(latest_ts) if latest_ts else None)


def get_market_history(session: Session, market_id: str, hours: int = 24) -> list[PredictionRow]:
    """单市场历史:按 market_id 走索引取数(2026-08-29 查询下推——旧实现复用
    load_prediction_rows 整窗全捞再挑一个市场,30 天×5min 存量下每张图表都是几十万行
    全表扫,单核服务器上事件详情曲线会转半天)。可见性判定与 load_prediction_rows
    同规则、范围缩到单市场:有 origin → 按跟踪项软删精确判(tag 等非 slug 不算活);
    无 origin(旧数据)→ 断流启发式,基准取窗口内全表最新快照。"""
    hours = max(1, min(int(hours or 24), 24 * 366))
    cutoff = utc_now_naive() - timedelta(hours=hours)
    rows = (
        session.query(PredictionMarket)
        .filter(
            PredictionMarket.market_id == market_id,
            PredictionMarket.timestamp >= cutoff,
        )
        .order_by(PredictionMarket.timestamp.asc())
        .all()
    )
    if not rows:
        return []
    origins = {row.origin for row in rows if row.origin}
    if origins:
        active_keys = {
            f"{t.kind}:{t.identifier}"
            for t in session.query(TrackedMarket.kind, TrackedMarket.identifier)
            .filter(TrackedMarket.dismissed.is_(False), TrackedMarket.kind == "slug")
            .all()
        }
        if not (origins & active_keys):
            return []
    else:
        latest_ts = (
            session.query(func.max(PredictionMarket.timestamp))
            .filter(PredictionMarket.timestamp >= cutoff)
            .scalar()
        )
        grace = timedelta(minutes=max(1, int(config.PREDICTION_ACTIVE_GRACE_MINUTES)))
        if latest_ts is not None and rows[-1].timestamp < latest_ts - grace:
            return []
    return [_row_schema(row) for row in rows]


def _tracked_to_schema(row: TrackedMarket, events: list[dict] | None = None) -> TrackedMarketSchema:
    return TrackedMarketSchema(
        id=row.id,
        kind=row.kind,
        identifier=row.identifier,
        display_name=row.display_name,
        enabled=row.enabled,
        notes=row.notes,
        market=row.market or "macro",
        events=[TrackedEventBrief(**e) for e in (events or [])],
        market_filter=market_filter_list(row.market_filter),
    )


def list_tracked_markets(session: Session, market: str | None = None) -> list[TrackedMarketSchema]:
    query = session.query(TrackedMarket).filter(
        TrackedMarket.dismissed.is_(False),
        TrackedMarket.kind == "slug",
    )
    if market:
        query = query.filter(TrackedMarket.market == market)
    rows = query.order_by(TrackedMarket.kind, TrackedMarket.identifier).all()
    from services import event_markets
    briefs = event_markets.links_for_tracked(session, [int(r.id) for r in rows])
    return [_tracked_to_schema(r, briefs.get(int(r.id))) for r in rows]


def create_tracked_market(session: Session, payload: TrackedMarketCreate) -> TrackedMarketSchema:
    identifier = (payload.identifier or "").strip()
    if not identifier:
        raise ValueError("identifier empty")

    exists = (
        session.query(TrackedMarket)
        .filter(TrackedMarket.kind == payload.kind, TrackedMarket.identifier == identifier)
        .first()
    )
    if exists is not None and not exists.dismissed:
        raise ValueError("duplicate")

    if exists is not None:
        # 之前被软删的同名项 → 复活而不是报重复;线归属按本次添加意图覆盖。
        exists.dismissed = False
        exists.enabled = True
        exists.market = payload.market
        new_name = (payload.display_name or "").strip()
        if new_name:
            exists.display_name = new_name
        new_notes = (payload.notes or "").strip()
        if new_notes:
            exists.notes = new_notes
        row = exists
    else:
        row = TrackedMarket(
            kind=payload.kind,
            identifier=identifier,
            market=payload.market,
            display_name=(payload.display_name or "").strip() or None,
            notes=(payload.notes or "").strip() or None,
            enabled=True,
        )
        session.add(row)
    session.commit()
    session.refresh(row)
    # 添加即挂接(spec §4):人工通道,link_source=human;事件非法抛 ValueError → 路由译 400
    from services import event_markets
    if payload.event_id:
        event_markets.attach_market(session, int(payload.event_id), int(row.id), source="human")
    briefs = event_markets.links_for_tracked(session, [int(row.id)])
    return _tracked_to_schema(row, briefs.get(int(row.id)))


def update_tracked_market(session: Session, tracked_id: int, payload: TrackedMarketUpdate) -> TrackedMarketSchema | None:
    row = session.query(TrackedMarket).filter(TrackedMarket.id == tracked_id).first()
    if row is None:
        return None
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.display_name is not None:
        row.display_name = payload.display_name.strip() or None
    if payload.notes is not None:
        row.notes = payload.notes.strip() or None
    if payload.market_filter is not None:
        # 档位筛选(2026-08-30):空列表=清除(全保留),非空=只保留这些子市场
        cleaned = [str(x).strip() for x in payload.market_filter if str(x).strip()]
        row.market_filter = json.dumps(cleaned) if cleaned else None
    session.commit()
    session.refresh(row)
    return _tracked_to_schema(row)


def delete_tracked_market(session: Session, tracked_id: int) -> bool:
    row = session.query(TrackedMarket).filter(TrackedMarket.id == tracked_id).first()
    if row is None or row.dismissed:
        return False        # 不存在或已软删 → 调用方返回 404（删第二次幂等）
    # 软删除：打墓碑、留行。seed 的 existing 查全表，行还在→(kind,identifier) 仍命中→重启不补种。
    row.dismissed = True
    session.commit()
    return True
