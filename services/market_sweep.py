# -*- coding: utf-8 -*-
"""事件池·找市场提案(spec 2026-08-28 §3):三入口一管线——
素材 → AI①英文搜索词 → Gamma public-search → AI②配对打分(剔价格目标类) → 提案。
提案不落库,勾选走 apply(与 pool_sweep 提案确认制同型);防幻觉=slug 白名单。"""
from __future__ import annotations

import json
import re
import threading

from loguru import logger
from sqlalchemy.orm import Session

import config
from models.event_market import ResearchEventMarket
from models.news import NewsItem
from models.research import ResearchEvent, ResearchEventLink
from models.tracked_market import TrackedMarket
from scanners.sources.polymarket.client import PolymarketGammaClient
from scanners.sources.polymarket.parser import _json_list
from scanners.sources.polymarket.source import PolymarketSource
from services.deepseek_client import call_deepseek_chat
from services.event_linking import MARKET_EVENT_TYPE, VALID_CONFIDENCES, _active_events

MARKET_SWEEP_PROMPT_VERSION = "market-sweep-v1-20260828"


def _yes_probability(markets: list) -> float | None:
    """单市场事件取 Yes 概率;多子市场(降息分桶类)无单一概率,返回 None(spec §4)。"""
    if len(markets) != 1 or not isinstance(markets[0], dict):
        return None
    outcomes = _json_list(markets[0].get("outcomes", ""))
    prices = _json_list(markets[0].get("outcomePrices", ""))
    if (not isinstance(outcomes, list) or not isinstance(prices, list)
            or not outcomes or len(outcomes) != len(prices)):
        return None
    for i, outcome in enumerate(outcomes):
        if str(outcome).strip().lower() == "yes":
            try:
                prob = float(prices[i])
            except (TypeError, ValueError):
                return None
            return prob if 0.0 <= prob <= 1.0 else None
    return None


def _candidate(event: dict, min_volume: float | None = None) -> dict | None:
    """public-search 事件 → 提案候选。剔已关闭/未活跃/低交易量;布尔字段可能是字符串,
    复用 PolymarketSource 的判定。"""
    if not isinstance(event, dict):
        return None
    slug = str(event.get("slug") or "").strip()
    title = str(event.get("title") or "").strip()
    if not slug or not title:
        return None
    if PolymarketSource._is_closed_or_inactive_market(event):
        return None
    try:
        volume = float(event.get("volume") or 0)
    except (TypeError, ValueError):
        volume = 0.0
    floor = float(config.POLYMARKET.get("proposal_min_volume", 10_000)) if min_volume is None else min_volume
    if volume < floor:
        return None
    markets = event.get("markets") if isinstance(event.get("markets"), list) else []
    return {
        "slug": slug,
        "title": title[:200],
        "description": str(event.get("description") or "")[:200],
        "volume": volume,
        "end_date": str(event.get("endDate") or "")[:10],
        "market_count": len(markets) or 1,
        "current_probability": _yes_probability(markets),
    }


SEARCH_TERMS_PROMPT = (
    "你是宏观/加密研究助理。输入是一批研究事件(中文名+免闸关键词+近期新闻标题)。\n"
    "为每个事件生成 1-3 组**英文搜索词**,用于在 Polymarket(英文预测市场)搜索相关市场。\n"
    "要求:每组 2-6 个英文单词;具体优先(实体名、专有名词、政策名);同一事件多组词角度错开。\n"
    "事件与预测市场明显无缘(纯行情波动、公司财报点评)就给空列表。\n"
    '只返回 JSON,不要 Markdown:{"terms": [{"event_id": 1, "queries": ["russia ukraine ceasefire"]}]}\n'
    "event_id 必须来自输入。"
)

PAIR_PROMPT = (
    "你是研究助理。输入是【研究事件】(中文)和每个事件搜到的【候选预测市场】(英文)。\n"
    "判断每个候选是否是对应事件结局的市场定价。规则:\n"
    "- 只挂真正相关的:市场的结算条件必须与事件走向/结局直接相关,主题擦边不算;不相关的候选不要输出。\n"
    "- 剔除纯价格目标类:结算条件为某资产价格达到/越过某数值的(如 'Will BTC reach $150k'、"
    "'oil above $100'),一律标 price_target=true——它们是价格的影子,不是事件概率。\n"
    "- confidence 三档:0.9=明确就是这件事;0.65=大概率相关;0.3=勉强沾边(倾向不挂)。\n"
    '只返回 JSON,不要 Markdown:{"matches": [{"event_id": 1, "slug": "xxx", "confidence": 0.9,'
    ' "price_target": false, "reason": "一句话中文理由"}]}\n'
    "slug 必须来自该事件的候选列表。"
)

_MARKET_SWEEP_LOCK = threading.Lock()


class MarketSweepBusy(RuntimeError):
    """已有一次找市场提案在进行中(路由层译成 409)。"""


def _json_loads_loose(raw: str) -> dict:
    """剥 Markdown 围栏后解析(与 pool_sweep._parse_sweep 同一容错)。"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            raise ValueError(f"AI 返回非 JSON: {text[:200]}")
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("AI 返回不是 JSON 对象")
    return data


def _call_market_ai(system_prompt: str, user_content: str, max_tokens: int) -> tuple[str, float]:
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置,无法找市场提案")
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        # flash 别名默认开思考,思考会吃光 max_tokens 让 content 返空(2026-08-15 实锤)——显式关
        "thinking": {"type": "disabled"},
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    result = call_deepseek_chat(
        payload, api_key=config.DEEPSEEK_API_KEY,
        timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, config.DEEPSEEK_READ_TIMEOUT),
        http_error_prefix="DeepSeek 市场提案返回", error_preview_chars=200,
        normalize_error_newlines=False,
    )
    if not result.content:
        raise RuntimeError("DeepSeek 市场提案返回空 content")
    return result.content, result.duration_seconds


def _parse_terms(raw: str, valid_event_ids: set[int]) -> dict[int, list[str]]:
    """AI① 输出 → {event_id: [英文搜索词]}:id 白名单、词长夹紧、每事件最多 3 组。"""
    data = _json_loads_loose(raw)
    out: dict[int, list[str]] = {}
    for item in (data.get("terms") or []):
        if not isinstance(item, dict):
            continue
        try:
            eid = int(item.get("event_id"))
        except (TypeError, ValueError):
            continue
        if eid not in valid_event_ids:
            continue
        queries: list[str] = []
        for q in (item.get("queries") or []):
            q = str(q).strip()
            if 2 <= len(q) <= 80 and q not in queries:
                queries.append(q)
        if queries:
            out[eid] = queries[:3]
    return out


def _parse_matches(raw: str, candidates: dict[int, dict[str, dict]]) -> tuple[list[dict], int]:
    """AI② 输出 → 合法配对列表 + 被剔的价格目标数。防幻觉:event_id 与 slug 都必须在
    本次候选白名单里;confidence 三档;price_target=true 整条剔除(计数不静默)。"""
    data = _json_loads_loose(raw)
    matches: list[dict] = []
    seen: set[tuple[int, str]] = set()
    dropped_price_targets = 0
    for item in (data.get("matches") or []):
        if not isinstance(item, dict):
            continue
        try:
            eid = int(item.get("event_id"))
        except (TypeError, ValueError):
            continue
        slug = str(item.get("slug") or "").strip()
        if eid not in candidates or slug not in candidates[eid] or (eid, slug) in seen:
            continue
        if item.get("confidence") not in VALID_CONFIDENCES:
            continue
        if bool(item.get("price_target")):
            dropped_price_targets += 1
            continue
        seen.add((eid, slug))
        matches.append({"event_id": eid, "slug": slug,
                        "confidence": float(item["confidence"]),
                        "reason": str(item.get("reason") or "").strip()[:120]})
    return matches, dropped_price_targets


def _recent_titles(session: Session, event_id: int, limit: int = 5) -> list[str]:
    rows = (session.query(NewsItem.title)
            .join(ResearchEventLink, ResearchEventLink.news_id == NewsItem.id)
            .filter(ResearchEventLink.event_id == int(event_id),
                    ResearchEventLink.detached.is_(False))
            .order_by(NewsItem.timestamp.desc())
            .limit(limit).all())
    return [(title or "")[:80] for (title,) in rows]


def _target_events(session: Session, event_type: str, event_id: int | None):
    if event_id is None:
        return _active_events(session, event_type)
    event = (session.query(ResearchEvent)
             .filter(ResearchEvent.id == int(event_id),
                     ResearchEvent.status == "active",
                     ResearchEvent.event_type == event_type)
             .first())
    if event is None:
        raise ValueError("事件不存在、已关闭或不属于该线")
    return [event]


def _drop_already_linked(session: Session, candidates: dict[int, dict[str, dict]]) -> None:
    """已跟踪且已挂到该事件(未摘)的候选没有提案价值,剔除。"""
    slugs = {slug for per_event in candidates.values() for slug in per_event}
    if not slugs:
        return
    tracked = (session.query(TrackedMarket)
               .filter(TrackedMarket.kind == "slug",
                       TrackedMarket.identifier.in_(slugs),
                       TrackedMarket.dismissed.is_(False)).all())
    by_slug = {t.identifier: int(t.id) for t in tracked}
    if not by_slug:
        return
    links = (session.query(ResearchEventMarket)
             .filter(ResearchEventMarket.tracked_id.in_(list(by_slug.values())),
                     ResearchEventMarket.detached.is_(False)).all())
    linked_pairs = {(int(l.event_id), int(l.tracked_id)) for l in links}
    for eid, per_event in candidates.items():
        for slug in list(per_event):
            tid = by_slug.get(slug)
            if tid is not None and (eid, tid) in linked_pairs:
                per_event.pop(slug)


def run_market_sweep(session: Session, event_type: str = "macro",
                     event_id: int | None = None,
                     client: PolymarketGammaClient | None = None) -> dict:
    """找市场提案:素材 → AI①搜索词 → Gamma 搜索 → AI②配对 → 提案(不落库)。
    run 全程零写库,天然就是演练;写入只发生在 apply_market_proposals(人勾选后)。"""
    if event_type not in MARKET_EVENT_TYPE:
        raise ValueError(f"非法 event_type: {event_type!r}")
    if not _MARKET_SWEEP_LOCK.acquire(blocking=False):
        raise MarketSweepBusy("已有一次找市场提案在进行中,等它跑完再点")
    try:
        events = _target_events(session, event_type, event_id)
        base = {"event_type": event_type, "scanned_events": len(events),
                "searched_terms": 0, "candidates": 0, "dropped_price_targets": 0,
                "proposals": [], "duration_seconds": 0.0}
        if not events:
            return base
        materials = [{"id": int(e.id), "name": e.name,
                      "keywords": [k.strip() for k in (e.gate_keywords or "").split("、") if k.strip()],
                      "recent_titles": _recent_titles(session, int(e.id))}
                     for e in events]
        raw_terms, duration1 = _call_market_ai(
            SEARCH_TERMS_PROMPT, json.dumps({"events": materials}, ensure_ascii=False), 1500)
        terms = _parse_terms(raw_terms, {m["id"] for m in materials})
        base["searched_terms"] = sum(len(v) for v in terms.values())
        base["duration_seconds"] = round(duration1, 1)
        if not terms:
            return base
        client = client or PolymarketGammaClient(
            config.POLYMARKET.get("gamma_url", "https://gamma-api.polymarket.com"),
            config.proxies())
        candidates: dict[int, dict[str, dict]] = {}
        for eid, queries in terms.items():
            for query in queries:
                try:
                    found = client.search_events(query, limit_per_type=5)
                except Exception as exc:
                    logger.warning("[MarketSweep] Gamma 搜索失败 q={}: {}", query, exc)
                    continue
                for event_payload in found:
                    c = _candidate(event_payload)
                    if c is not None:
                        candidates.setdefault(eid, {}).setdefault(c["slug"], c)
        _drop_already_linked(session, candidates)
        candidates = {eid: per for eid, per in candidates.items() if per}
        base["candidates"] = sum(len(per) for per in candidates.values())
        if not candidates:
            return base
        name_by_id = {int(e.id): e.name for e in events}
        pair_payload = {
            "events": [{"id": eid, "name": name_by_id[eid]} for eid in candidates],
            "candidates": {str(eid): [{k: c[k] for k in
                                       ("slug", "title", "description", "end_date", "volume")}
                                      for c in per.values()]
                           for eid, per in candidates.items()},
        }
        raw_pairs, duration2 = _call_market_ai(
            PAIR_PROMPT, json.dumps(pair_payload, ensure_ascii=False), 3000)
        matches, dropped = _parse_matches(raw_pairs, candidates)
        base["dropped_price_targets"] = dropped
        proposals = []
        for m in matches:
            c = candidates[m["event_id"]][m["slug"]]
            proposals.append({
                "event_id": m["event_id"], "event_name": name_by_id[m["event_id"]],
                "slug": m["slug"], "title": c["title"],
                "current_probability": c["current_probability"],
                "market_count": c["market_count"], "volume": c["volume"],
                "end_date": c["end_date"], "confidence": m["confidence"],
                "reason": m["reason"],
            })
        proposals.sort(key=lambda p: (-(p["confidence"] or 0), p["event_id"]))
        base["proposals"] = proposals
        base["duration_seconds"] = round(duration1 + duration2, 1)
        logger.info("[MarketSweep] {} 提案完成:事件 {},候选 {},提案 {},剔价格类 {}",
                    event_type, len(events), base["candidates"], len(proposals), dropped)
        return base
    finally:
        _MARKET_SWEEP_LOCK.release()


def apply_market_proposals(session: Session, event_type: str, items: list[dict]) -> dict:
    """采纳勾选的市场提案(签字环节):写 tracked_markets(新建/复活)+ 挂接。
    幂等:已挂跳过;已摘下不挂回(人摘过=否决,与新闻挂接同规矩)。"""
    if event_type not in MARKET_EVENT_TYPE:
        raise ValueError(f"非法 event_type: {event_type!r}")
    if len(items) > 30:
        raise ValueError("一次最多采纳 30 条")
    added: list[str] = []
    revived: list[str] = []
    skipped: list[str] = []
    linked = 0
    for item in items:
        slug = str(item.get("slug") or "").strip()
        if not slug:
            raise ValueError("提案缺 slug")
        try:
            eid = int(item.get("event_id"))
        except (TypeError, ValueError):
            raise ValueError(f"提案「{slug}」缺 event_id")
        event = (session.query(ResearchEvent)
                 .filter(ResearchEvent.id == eid, ResearchEvent.status == "active",
                         ResearchEvent.event_type == event_type).first())
        if event is None:
            skipped.append(f"{slug}(事件 #{eid} 不存在或非进行中)")
            continue
        tracked = session.query(TrackedMarket).filter_by(kind="slug", identifier=slug).first()
        if tracked is None:
            tracked = TrackedMarket(
                kind="slug", identifier=slug, market=event_type, enabled=True,
                display_name=(str(item.get("title") or "").strip()[:255] or None))
            session.add(tracked)
            session.flush()
            added.append(slug)
        elif tracked.dismissed:
            tracked.dismissed = False
            tracked.enabled = True
            tracked.market = event_type
            revived.append(slug)
        link = (session.query(ResearchEventMarket)
                .filter_by(event_id=eid, tracked_id=int(tracked.id)).first())
        if link is not None:
            skipped.append(f"{slug}(已{'摘下' if link.detached else '挂接'})")
            continue
        confidence = item.get("confidence")
        session.add(ResearchEventMarket(
            event_id=eid, tracked_id=int(tracked.id), link_source="auto",
            confidence=float(confidence) if confidence in VALID_CONFIDENCES else None,
            prompt_version=MARKET_SWEEP_PROMPT_VERSION))
        linked += 1
    session.commit()
    logger.info("[MarketSweep] {} 采纳:新建 {},复活 {},挂接 {},跳过 {}",
                event_type, len(added), len(revived), linked, len(skipped))
    return {"event_type": event_type, "added": added, "revived": revived,
            "linked": linked, "skipped": skipped}


def search_markets(query: str) -> list[dict]:
    """手动搜索通道(spec §3):Gamma 搜索代理,不剔价格类、不设交易量门槛(人是有意找的),
    只剔已关闭/未活跃。"""
    query = (query or "").strip()
    if not query:
        return []
    client = PolymarketGammaClient(
        config.POLYMARKET.get("gamma_url", "https://gamma-api.polymarket.com"),
        config.proxies())
    results = []
    for event_payload in client.search_events(query, limit_per_type=10):
        c = _candidate(event_payload, min_volume=0.0)
        if c is not None:
            results.append(c)
    return results[:10]
