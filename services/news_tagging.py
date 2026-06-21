# -*- coding: utf-8 -*-
"""新闻内容标签（news-impact-engine Phase 1）：LLM 给每条新闻打 topic/方向/量级。

纯内容判断、**不看价格**——量级是 a-priori 严重度（事件本身多大），不是市场实际反应。
用便宜的 flash 模型批量打；解析层过滤幻觉 id 与非法枚举（对齐 config 三张枚举）。
落库写 news_items.topic / news_direction / magnitude_tier / tagged_at。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta

import requests
from loguru import logger
from sqlalchemy.orm import Session

import config
from models.news import NewsItem
from services import market_calendar
from services.time_utils import utc_now_naive

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

TAGGING_SYSTEM_PROMPT = (
    "你是宏观新闻分类员。给每条新闻打三个**纯内容**标签（只看新闻本身，**不看价格、不猜市场反应**）：\n\n"
    "1. topic（主题，必须严格选下面之一）：\n"
    + "、".join(config.NEWS_TOPICS) + "\n\n"
    "2. direction（相对**风险资产**——BTC/纳指——的应然影响，三选一）：利多 / 利空 / 中性\n\n"
    "3. magnitude（a-priori 量级 rubric，事件本身有多大，三选一）：\n"
    "   - 大 = 直接改宏观/政策/流动性/地缘定价的一级事件（开战、央行决议、CPI 意外、主权违约、海峡封锁、ETF 获批）\n"
    "   - 中 = 有方向但非一级（官员喊话、二线数据、局部摩擦、单个公司事件）\n"
    "   - 小 = 背景 / 重复转述 / 评论 / 行情综述 / 已知信息复述\n\n"
    "只返回 JSON，不要 Markdown：\n"
    '{"items": [{"id": int, "topic": "...", "direction": "...", "magnitude": "..."}, ...]}\n'
    "每条输入新闻在 items 里有且仅有一项，id 严格对应输入。"
)


def _build_tagging_payload(news_list: list[NewsItem]) -> str:
    items = [{
        "id": n.id,
        "source": n.source,
        "title": (n.title or "")[:160],
        "content": (n.content or "")[:200],
    } for n in news_list]
    return f"共 {len(items)} 条新闻。\n{json.dumps({'news': items}, ensure_ascii=False)}"


def _call_deepseek_tagger(user_content: str) -> str:
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，无法打标")
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": TAGGING_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 4000,
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    resp = requests.post(DEEPSEEK_API_URL, json=payload, headers=headers,
                         timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, config.DEEPSEEK_READ_TIMEOUT))
    if resp.status_code >= 400:
        raise RuntimeError(f"DeepSeek 打标返回 {resp.status_code}: {resp.text[:200]}")
    content = (resp.json()["choices"][0].get("message", {}).get("content") or "").strip()
    if not content:
        raise RuntimeError("DeepSeek 打标返回空 content")
    return content


def _parse_tagging_response(raw: str, valid_ids: set[int]) -> dict[int, dict]:
    """解析 items；过滤幻觉 id 与非法枚举（topic/direction/magnitude 必须在 config 枚举内）。"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            raise ValueError(f"打标返回非 JSON: {text[:200]}")
        data = json.loads(m.group(0))
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("打标返回缺少 items 列表")

    out: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            nid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if nid not in valid_ids:
            continue
        topic = item.get("topic")
        direction = item.get("direction")
        magnitude = item.get("magnitude")
        if topic not in config.NEWS_TOPICS:
            continue
        if direction not in config.NEWS_DIRECTIONS:
            continue
        if magnitude not in config.NEWS_MAGNITUDE_TIERS:
            continue
        out[nid] = {"topic": topic, "direction": direction, "magnitude": magnitude}
    return out


def tag_news_batch(session: Session, news_list: list[NewsItem]) -> int:
    """对一批新闻打标并落库，返回成功打标条数。"""
    news_list = [n for n in news_list if n is not None]
    if not news_list:
        return 0
    content = _call_deepseek_tagger(_build_tagging_payload(news_list))
    parsed = _parse_tagging_response(content, {int(n.id) for n in news_list})
    now = utc_now_naive()
    by_id = {int(n.id): n for n in news_list}
    for nid, tags in parsed.items():
        n = by_id.get(nid)
        if n is None:
            continue
        n.topic = tags["topic"]
        n.news_direction = tags["direction"]
        n.magnitude_tier = tags["magnitude"]
        # traditional_open 是**前置条件**，新闻入库时就已设好（news_scanner / backfill_traditional_open），
        # 打标只写内容标签、不碰它。
        n.tagged_at = now
    session.commit()
    return len(parsed)


def backfill_traditional_open(session: Session) -> int:
    """给 traditional_open 为 NULL 的新闻补这个**前置条件**（纯日历、无 LLM，很快）。
    历史新闻（入库时还没这列）一次性补；返回补的行数，幂等。"""
    rows = (
        session.query(NewsItem)
        .filter(NewsItem.traditional_open.is_(None), NewsItem.timestamp.isnot(None))
        .all()
    )
    for n in rows:
        n.traditional_open = market_calendar.is_traditional_open(n.timestamp)
    session.commit()
    return len(rows)


def tag_untagged(session: Session, limit: int = 500, batch_size: int | None = None,
                 reaction_minutes: int = 30, now: datetime | None = None) -> int:
    """给"可打标"的新闻分片打标。"可打标" = 未打标 **且反应窗口已走完**
    （timestamp ≤ now − reaction_minutes）——窗口没走完时反应还测不到，按用户逻辑不放进打标状态。
    traditional_open 是前置条件，入库已设；这里不再写它。回灌脚本与每小时 job 共用。"""
    batch_size = int(batch_size or config.DEEPSEEK_BATCH_SIZE)
    cutoff = (now or utc_now_naive()) - timedelta(minutes=reaction_minutes)
    todo = (
        session.query(NewsItem)
        .filter(NewsItem.tagged_at.is_(None), NewsItem.timestamp <= cutoff)
        .order_by(NewsItem.timestamp.desc())
        .limit(max(1, limit))
        .all()
    )
    total = 0
    for i in range(0, len(todo), batch_size):
        chunk = todo[i:i + batch_size]
        try:
            total += tag_news_batch(session, chunk)
        except Exception as exc:  # 单片失败不阻断后续
            logger.error(f"[NewsTagging] 分片打标失败（{len(chunk)} 条）: {exc}")
    return total
