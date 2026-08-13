# -*- coding: utf-8 -*-
"""研究事件池·生命周期与读取(docs/specs/news-research-phase1-event-pool.md §6-§10)。

全部写操作只走人工入口(API),模型无立案/关闭/重开/合并权。
人工挂接无视闸门与黑名单(spec §1)。留痕规则见 spec §6.2 表。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

import config
from models.crypto import NewsCoin
from models.news import NewsItem, NewsPriceAnnotation
from models.price import PriceSnapshot
from models.research import ResearchEvent, ResearchEventLink
from services.event_linking import (
    _active_events, _is_blacklisted, _keyword_pool, _split_keywords,
    clear_link_cursor, passes_crypto_gate, passes_gate,
)
from services.news_service import to_news_schema
from services.theme_ledger import (
    OBS_BASELINE_TOLERANCE_MINUTES, observed_reaction_from_rows,
)
from services.time_utils import bj_date_of, bj_day_bounds, format_bj, utc_now_naive


def _get_event(session: Session, event_id: int) -> ResearchEvent:
    e = session.query(ResearchEvent).filter_by(id=event_id).first()
    if e is None:
        raise ValueError(f"事件 #{event_id} 不存在")
    return e


EVENT_TYPES = ("macro", "crypto")


def create_event(session: Session, name: str, news_ids: list[int],
                 gate_keywords: str | None = None, created_from: str = "manual",
                 backscan_hours: float | None = None,
                 now: datetime | None = None,
                 event_type: str = "macro",
                 link_source: str = "human",
                 prompt_version: str | None = None) -> ResearchEvent:
    """立案(spec §6.1):强制 ≥1 条新闻;出生即 active;自动回扫 72h。
    event_type 决定这个事件收哪条线的新闻(web3 二期A design §4)。
    created_from="sweep" 是 AI 梳理按钮落的草稿(2026-08-13 design):立案权仍不给
    定时任务,只在人点按钮时行使;梳理种子挂接传 link_source="auto"+prompt_version,
    auto_event_id 记本事件——纠错率审计对梳理产物同样生效。"""
    if not news_ids:
        raise ValueError("立案必须至少挂一条新闻")
    if created_from not in ("annotation", "manual", "sweep"):
        raise ValueError(f"非法 created_from: {created_from!r}")
    if link_source not in ("human", "auto"):
        raise ValueError(f"非法 link_source: {link_source!r}")
    if event_type not in EVENT_TYPES:
        raise ValueError(f"非法 event_type: {event_type!r}")
    found = {int(i) for (i,) in session.query(NewsItem.id)
             .filter(NewsItem.id.in_(news_ids)).all()}
    missing = {int(i) for i in news_ids} - found
    if missing:
        raise ValueError(f"新闻不存在: {sorted(missing)}")
    now = now or utc_now_naive()
    # 人看的序号:同类型内 max+1,只增不补(中间的事件没了也不重排,免得你记的 #3 变成别人)
    max_no = (session.query(func.max(ResearchEvent.display_no))
              .filter(ResearchEvent.event_type == event_type).scalar() or 0)
    event = ResearchEvent(name=name, status="active", event_type=event_type,
                          display_no=int(max_no) + 1,
                          gate_keywords=(gate_keywords or None),
                          created_from=created_from, status_changed_at=now)
    session.add(event)
    session.flush()
    for nid in news_ids:
        session.add(ResearchEventLink(
            event_id=event.id, news_id=int(nid), link_source=link_source,
            auto_event_id=(event.id if link_source == "auto" else None),
            prompt_version=prompt_version))
    session.commit()
    # 回扫只清同线新闻的游标:立个加密事件不该把宏观新闻整批重排队
    clear_link_cursor(session, backscan_hours or config.EVENT_BACKSCAN_DEFAULT_HOURS,
                      now=now, market=event_type)
    return event


def rename_event(session: Session, event_id: int, name: str) -> ResearchEvent:
    e = _get_event(session, event_id)
    e.name = name
    session.commit()
    return e


def set_keywords(session: Session, event_id: int, gate_keywords: str | None,
                 backscan: bool = False) -> ResearchEvent:
    """改关键词只对之后的新闻自动生效;backscan=True 追溯最近 72h(spec §5.1)。"""
    e = _get_event(session, event_id)
    e.gate_keywords = gate_keywords or None
    session.commit()
    if backscan:
        clear_link_cursor(session, config.EVENT_BACKSCAN_DEFAULT_HOURS)
    return e


def close_event(session: Session, event_id: int, reason: str | None) -> ResearchEvent:
    e = _get_event(session, event_id)
    e.status = "closed"
    e.closed_reason = reason
    e.status_changed_at = utc_now_naive()
    session.commit()
    return e


def reopen_event(session: Session, event_id: int) -> ResearchEvent:
    """重开(spec §6.2/§7):closed→active,免闸恢复,自动回扫 72h。时间轴同一条。"""
    e = _get_event(session, event_id)
    e.status = "active"
    e.status_changed_at = utc_now_naive()
    session.commit()
    clear_link_cursor(session, config.EVENT_BACKSCAN_DEFAULT_HOURS)
    return e


def merge_event(session: Session, source_id: int, target_id: int) -> int:
    """合并 A→B(spec §6.2):未摘下挂接迁移(撞唯一索引跳过保 B 现有),关键词并入去重,
    A 关闭并记 merged_into;A 的已摘下记录留在 A(审计痕迹不迁移)。返回迁移条数。"""
    if source_id == target_id:
        raise ValueError("不能合并到自身")
    src = _get_event(session, source_id)
    dst = _get_event(session, target_id)
    dst_news = {row[0] for row in session.query(ResearchEventLink.news_id)
                .filter_by(event_id=target_id).all()}
    moved = 0
    for link in (session.query(ResearchEventLink)
                 .filter_by(event_id=source_id, detached=False).all()):
        if link.news_id in dst_news:
            continue
        link.event_id = target_id
        moved += 1
    merged_kw = _split_keywords(dst.gate_keywords)
    for k in _split_keywords(src.gate_keywords):
        if k not in merged_kw:
            merged_kw.append(k)
    dst.gate_keywords = "、".join(merged_kw) or None
    src.status = "closed"
    src.merged_into_id = target_id
    src.closed_reason = f"合并入 #{target_id}"
    src.status_changed_at = utc_now_naive()
    session.commit()
    return moved


def attach_news(session: Session, event_id: int, news_id: int) -> ResearchEventLink:
    """人工挂接:无视闸门/黑名单;同 (event,news) 已有记录则复活(detached→False)。"""
    _get_event(session, event_id)
    existing = (session.query(ResearchEventLink)
                .filter_by(event_id=event_id, news_id=news_id).first())
    if existing:
        existing.detached = False
        existing.detach_reason = None
        existing.link_source = "human"
        session.commit()
        return existing
    link = ResearchEventLink(event_id=event_id, news_id=news_id, link_source="human")
    session.add(link)
    session.commit()
    return link


def reassign_link(session: Session, link_id: int, new_event_id: int) -> ResearchEventLink:
    """改归属:auto_event_id 保模型原判,link_source 变 human(spec §6.2)。"""
    link = session.query(ResearchEventLink).filter_by(id=link_id).first()
    if link is None:
        raise ValueError(f"挂接 #{link_id} 不存在")
    _get_event(session, new_event_id)
    dup = (session.query(ResearchEventLink)
           .filter_by(event_id=new_event_id, news_id=link.news_id).first())
    if dup is not None and dup.id != link.id:
        raise ValueError(f"目标事件已有这条新闻的挂接(#{dup.id})")
    link.event_id = new_event_id
    link.link_source = "human"
    session.commit()
    return link


def detach_link(session: Session, link_id: int, reason: str | None) -> ResearchEventLink:
    """摘下=标记不删行(留痕,spec §6.2)。"""
    link = session.query(ResearchEventLink).filter_by(id=link_id).first()
    if link is None:
        raise ValueError(f"挂接 #{link_id} 不存在")
    link.detached = True
    link.detach_reason = reason
    link.link_source = "human"
    session.commit()
    return link


# ---- 读取层(spec §8-§10):全部读时派生,不落库 ----

def _driver_badge_map(session: Session) -> dict[int, dict]:
    """确认层徽章(spec §8.2):news_id → {symbol, change_pct}(标注 news_roles 反查 driver)。"""
    out: dict[int, dict] = {}
    rows = (session.query(NewsPriceAnnotation)
            .filter(NewsPriceAnnotation.news_roles.isnot(None)).all())
    for a in rows:
        try:
            roles = json.loads(a.news_roles or "{}")
        except json.JSONDecodeError:
            continue
        for nid, role in (roles or {}).items():
            if role == "driver":
                out[int(nid)] = {"symbol": a.symbol, "change_pct": a.change_pct}
    return out


def _event_links(session: Session, event_id: int, include_detached: bool = False):
    q = (session.query(ResearchEventLink, NewsItem)
         .join(NewsItem, NewsItem.id == ResearchEventLink.news_id)
         .filter(ResearchEventLink.event_id == event_id))
    if not include_detached:
        q = q.filter(ResearchEventLink.detached.is_(False))
    return q.order_by(NewsItem.timestamp.desc()).all()


def _event_coins(session: Session, event_id: int) -> list[str]:
    """事件涉及哪些币 = 时间轴新闻提及币种的并集(web3 二期A design §4)。

    读时派生、不落库:与一期"挂接表不存业务数值"同一铁律——摘下证据、重打标、
    补挂新闻,币种清单都应自动跟着变,存一份就会立刻过期。"""
    rows = (session.query(NewsCoin.coin)
            .join(ResearchEventLink, ResearchEventLink.news_id == NewsCoin.news_id)
            .filter(ResearchEventLink.event_id == event_id,
                    ResearchEventLink.detached.is_(False))
            .distinct().all())
    return sorted({r[0] for r in rows})


def list_events(session: Session, status: str | None = None, q: str | None = None,
                now: datetime | None = None,
                event_type: str | None = None) -> list[dict]:
    """事件列表(spec §9.1):最新证据倒序 + 派生徽章;搜索覆盖名称+关键词(含已关闭)。
    event_type 不传 = 两条线都要(工作台"全部"档)。"""
    now = now or utc_now_naive()
    badge_map = _driver_badge_map(session)
    today_start, today_end = bj_day_bounds(bj_date_of(now))
    yday_start = today_start - timedelta(days=1)
    query = session.query(ResearchEvent)
    if status:
        query = query.filter(ResearchEvent.status == status)
    if event_type:
        query = query.filter(ResearchEvent.event_type == event_type)
    if q:
        like = f"%{q}%"
        query = query.filter((ResearchEvent.name.like(like)) |
                             (ResearchEvent.gate_keywords.like(like)))
    out = []
    for e in query.all():
        rows = _event_links(session, e.id)
        last_ts = rows[0][1].timestamp if rows else None
        out.append({
            "id": e.id, "name": e.name, "status": e.status,
            # display_no 是人看的号(各类型自排);id 仍是程序内部唯一标识
            "display_no": e.display_no or e.id,
            "event_type": e.event_type or "macro",
            # 币种只对加密事件有意义;宏观事件恒空,免得宏观卡片挂一串无关代码
            "coins": _event_coins(session, e.id) if e.event_type == "crypto" else [],
            "gate_keywords": e.gate_keywords, "created_from": e.created_from,
            "merged_into_id": e.merged_into_id, "closed_reason": e.closed_reason,
            "evidence_count": len(rows),
            "today_new": sum(1 for l, _ in rows
                             if l.created_at and today_start <= l.created_at < today_end),
            "yesterday_new": sum(1 for l, _ in rows
                                 if l.created_at and yday_start <= l.created_at < today_start),
            "badge_count": sum(1 for _, n in rows if int(n.id) in badge_map),
            "last_evidence_at": last_ts,
            # 卡片显示用北京时间(ui-redesign §6.1):last_evidence_at 是 naive UTC,直接切会差 8 小时
            "last_evidence_bj": format_bj(last_ts),
            "days_since_last": (now - last_ts).days if last_ts else None,
        })
    out.sort(key=lambda r: (r["last_evidence_at"] is None,
                            -(r["last_evidence_at"].timestamp() if r["last_evidence_at"] else 0)))
    return out


def event_timeline(session: Session, event_id: int, now: datetime | None = None,
                   days: int | None = None, min_score: int | None = None,
                   min_abs_move: float | None = None,
                   page: int | None = None, page_size: int | None = None) -> dict:
    """时间轴(spec §8 + ui-redesign §6.2):每条证据 = 新闻 + 观测值(现算)+ 确认徽章 +
    评分失手 + 挂接留痕。筛选/分页全可选;**不传分页 = 全量返回**(replay 脚本直连服务层,
    默认 50 条会截断 spec §14 的完整时间轴;分页默认值只钉在路由层)。"""
    now = now or utc_now_naive()
    e = _get_event(session, event_id)
    rows = _event_links(session, event_id)
    if days is not None:
        cutoff = now - timedelta(days=days)
        rows = [(l, n) for l, n in rows if n.timestamp and n.timestamp >= cutoff]
    badge_map = _driver_badge_map(session)
    obs_symbol = config.EVENT_OBS_SYMBOLS[0]
    snaps: list = []
    if rows:
        times = [n.timestamp for _, n in rows]
        # spec §8.1:一次批量捞时间范围快照。跨年老事件此范围会变大,届时换 per-news 小查询即可。
        snaps = (session.query(PriceSnapshot.timestamp, PriceSnapshot.price)
                 .filter(PriceSnapshot.symbol == obs_symbol,
                         PriceSnapshot.timestamp >= min(times) - timedelta(minutes=OBS_BASELINE_TOLERANCE_MINUTES),
                         PriceSnapshot.timestamp <= max(times) + timedelta(minutes=config.EVENT_OBS_REACTION_MINUTES))
                 .order_by(PriceSnapshot.timestamp.asc()).all())
    items = []
    for link, n in rows:
        # 未评分(NULL)在设了分数门槛时出局:它是"评分失败"不是"0 分",不该混进筛选结果
        if min_score is not None and (n.llm_importance is None or n.llm_importance < min_score):
            continue
        obs = observed_reaction_from_rows(snaps, n.timestamp, now=now)
        if min_abs_move is not None:
            if obs.get("status") != "ok" or obs.get("net_pct") is None:
                continue                                   # pending/no_data 一律排除
            if abs(obs["net_pct"]) < min_abs_move:
                continue
        items.append({
            "news": to_news_schema(n).model_dump(),
            "obs": obs,
            "obs_symbol": obs_symbol,
            "driver_badge": badge_map.get(int(n.id)),
            "score_miss": (n.llm_importance is not None
                           and n.llm_importance < config.EVENT_LINK_MIN_IMPORTANCE),
            "link": {"id": link.id, "link_source": link.link_source,
                     "auto_event_id": link.auto_event_id, "confidence": link.confidence,
                     "prompt_version": link.prompt_version, "detached": link.detached},
        })
    total = len(items)
    page = max(1, int(page or 1))
    if page_size is not None:
        page_size = max(1, int(page_size))
        items = items[(page - 1) * page_size: page * page_size]
    # 回扫队列按本事件所属的线统计:加密页不该显示宏观的积压(两条线各跑各的挂接)
    pending_relink = (session.query(NewsItem)
                      .filter(NewsItem.tagged_at.isnot(None),
                              NewsItem.event_linked_at.is_(None),
                              NewsItem.market == (e.event_type or "macro")).count())
    return {"event": {"id": e.id, "display_no": e.display_no or e.id,
                      "name": e.name, "status": e.status,
                      "gate_keywords": e.gate_keywords, "created_from": e.created_from,
                      "closed_reason": e.closed_reason, "merged_into_id": e.merged_into_id},
            "items": items, "pending_relink": pending_relink,
            "total": total, "page": page, "page_size": page_size or total}


def news_links(session: Session, news_id: int) -> list[dict]:
    """某条新闻挂在哪些事件上(标注页只读徽章用,spec §9.2)。"""
    rows = (session.query(ResearchEventLink, ResearchEvent)
            .join(ResearchEvent, ResearchEvent.id == ResearchEventLink.event_id)
            .filter(ResearchEventLink.news_id == news_id,
                    ResearchEventLink.detached.is_(False)).all())
    return [{"link_id": l.id, "event_id": e.id, "event_name": e.name,
             "event_status": e.status} for l, e in rows]


def buffer_predicate(session: Session, market: str = "macro"):
    """返回"这条新闻算不算缓冲区"的判定函数(spec §6.4:过闸 + 非黑名单 + 无未摘下挂接)。

    口径的唯一来源:缓冲区页签与两个快讯页的"只看未挂事件"共用它,
    防各处各写一遍而慢慢漂移(buffer-into-news-page design §1.1)。
    活跃事件关键词与已挂 news_id 集合在这里查一次,判定本身不再碰库。

    market="crypto" 时闸门换语义闸(web3 二期A design §3):加密线不设分数闸,
    转载宏观的新闻本来就不进加密事件池,自然也不算它的待研究缓冲。
    """
    from services.event_linking import MARKET_EVENT_TYPE, passes_crypto_gate

    is_crypto = market == "crypto"
    keywords = _keyword_pool(_active_events(session, MARKET_EVENT_TYPE.get(market, "macro")))
    linked = {row[0] for row in session.query(ResearchEventLink.news_id)
              .filter(ResearchEventLink.detached.is_(False)).all()}

    def _is_buffer(news: NewsItem) -> bool:
        if int(news.id) in linked:
            return False
        gate_ok = passes_crypto_gate(news) if is_crypto else passes_gate(news, keywords)
        return not _is_blacklisted(news) and gate_ok

    return _is_buffer


def buffer_news(session: Session, days: int = 3, min_score: int | None = None,
                q: str | None = None, drivers_only: bool = False,
                now: datetime | None = None, limit: int = 200) -> list[dict]:
    """缓冲区(spec §6.4):过闸 + 不在黑名单 + 无未摘下挂接。"""
    now = now or utc_now_naive()
    is_buffer = buffer_predicate(session)
    badge_map = _driver_badge_map(session) if drivers_only else {}
    query = (session.query(NewsItem)
             .filter(NewsItem.tagged_at.isnot(None),
                     NewsItem.timestamp >= now - timedelta(days=days))
             .order_by(NewsItem.timestamp.desc()))
    if min_score is not None:
        query = query.filter(NewsItem.llm_importance >= min_score)
    if q:
        query = query.filter(NewsItem.title.like(f"%{q}%"))
    out = []
    for n in query.all():
        if not is_buffer(n):
            continue
        if drivers_only and int(n.id) not in badge_map:
            continue
        out.append(to_news_schema(n).model_dump())
        if len(out) >= limit:
            break
    return out


def revival_matches(session: Session, days: int = 7, now: datetime | None = None,
                    event_type: str | None = None) -> list[dict]:
    """沉睡监听(spec §7):近 N 天新闻命中**已关闭**事件关键词;纯文本现算,零 LLM。
    event_type 让两个事件池页各看各的旧事重提(web3 二期A)。"""
    now = now or utc_now_naive()
    query = (session.query(ResearchEvent)
             .filter(ResearchEvent.status == "closed",
                     ResearchEvent.gate_keywords.isnot(None)))
    if event_type:
        query = query.filter(ResearchEvent.event_type == event_type)
    closed = query.all()
    watchlist = [(e, [k.lower() for k in _split_keywords(e.gate_keywords)])
                 for e in closed]
    watchlist = [(e, ks) for e, ks in watchlist if ks]
    if not watchlist:
        return []
    out = []
    news_q = (session.query(NewsItem)
              .filter(NewsItem.timestamp >= now - timedelta(days=days)))
    if event_type:
        # 各线只拿自己的新闻:加密事件的沉睡词不该被宏观新闻唤醒,反之亦然
        news_q = news_q.filter(NewsItem.market == event_type)
    rows = news_q.order_by(NewsItem.timestamp.desc()).all()
    for n in rows:
        if _is_blacklisted(n):
            continue
        text = f"{n.title or ''} {(n.content or '')[:200]}".lower()
        for e, ks in watchlist:
            if any(k in text for k in ks):
                out.append({"news": to_news_schema(n).model_dump(),
                            "event_id": e.id, "event_name": e.name})
                break
    return out


def daily_stats(session: Session, now: datetime | None = None,
                market: str | None = None) -> dict:
    """并行期观察数字(spec §9.1/§13.3):当日(北京日)挂接率/纠错率,近似口径。
    market 传了只算那条线(两个池子页各看各的,2026-08-13 sweep design):加密新闻
    按语义闸判"过闸",宏观按分数闸;不传 = 两线混算(历史口径,兼容旧调用)。"""
    now = now or utc_now_naive()
    start, end = bj_day_bounds(bj_date_of(now))
    events = _active_events(session, market or "macro")
    keywords = _keyword_pool(events)
    processed_q = (session.query(NewsItem)
                   .filter(NewsItem.event_linked_at >= start,
                           NewsItem.event_linked_at < end))
    if market:
        processed_q = processed_q.filter(NewsItem.market == market)

    def _gate(n: NewsItem) -> bool:
        # 各线各自的闸:老 market=NULL 行按宏观闸走(二期A 前的历史数据)
        return passes_crypto_gate(n) if n.market == "crypto" else passes_gate(n, keywords)

    gated = [n for n in processed_q.all() if not _is_blacklisted(n) and _gate(n)]
    auto_q = (session.query(ResearchEventLink)
              .filter(ResearchEventLink.auto_event_id.isnot(None),
                      ResearchEventLink.created_at >= start,
                      ResearchEventLink.created_at < end))
    if market:
        auto_q = (auto_q.join(NewsItem, NewsItem.id == ResearchEventLink.news_id)
                  .filter(NewsItem.market == market))
    auto_today = auto_q.all()
    corrected = [l for l in auto_today if l.detached or l.event_id != l.auto_event_id]
    pending_q = (session.query(NewsItem)
                 .filter(NewsItem.tagged_at.isnot(None),
                         NewsItem.event_linked_at.is_(None)))
    if market:
        pending_q = pending_q.filter(NewsItem.market == market)
    pending_relink = pending_q.count()
    return {
        "gated_processed_today": len(gated),
        "auto_linked_today": len(auto_today),
        "link_rate": round(len(auto_today) / len(gated), 3) if gated else None,
        "corrected_today": len(corrected),
        "correction_rate": round(len(corrected) / len(auto_today), 3) if auto_today else None,
        "pending_relink": pending_relink,
    }


def daily_brief_text(session: Session, now: datetime | None = None) -> tuple[str, str]:
    """每日 WeCom 清单(spec §10):昨日北京日,纯查库拼文本。返回 (title, markdown)。"""
    now = now or utc_now_naive()
    y_date = (datetime.strptime(bj_date_of(now), "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    start, end = bj_day_bounds(y_date)
    badge_map = _driver_badge_map(session)
    lines: list[str] = []
    total_new = 0
    total_badge = 0
    for e in session.query(ResearchEvent).filter(ResearchEvent.status == "active").all():
        rows = [(l, n) for l, n in _event_links(session, e.id)
                if l.created_at and start <= l.created_at < end]
        if not rows:
            continue
        badges = sum(1 for _, n in rows if int(n.id) in badge_map)
        total_new += len(rows)
        total_badge += badges
        lines.append(f"- {e.name} +{len(rows)}" + (f"(徽章{badges})" if badges else ""))
    linked = {row[0] for row in session.query(ResearchEventLink.news_id)
              .filter(ResearchEventLink.detached.is_(False)).all()}
    hot = [n for n in session.query(NewsItem)
           .filter(NewsItem.timestamp >= start, NewsItem.timestamp < end,
                   NewsItem.llm_importance >= 8).all()
           if int(n.id) not in linked and not _is_blacklisted(n)]
    revival = [r for r in revival_matches(session, days=1, now=end)
               if start <= datetime.fromisoformat(r["news"]["timestamp_utc"]) < end]
    title = f"事件池日报 {y_date}"
    if not lines and not hot and not revival:
        return title, "事件池无动静"
    parts = []
    if lines:
        parts.append(f"进行中事件昨日新增证据 {total_new} 条(带确认徽章 {total_badge})")
        parts.extend(lines)
    parts.append(f"缓冲区昨日 ≥8 分未挂 {len(hot)} 条")
    for r in revival:
        parts.append(f"旧事重提:『{r['event_name']}』命中 {r['news']['title'][:40]}")
    return title, "\n".join(parts)
