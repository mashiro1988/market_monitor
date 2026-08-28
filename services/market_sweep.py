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
