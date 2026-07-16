# -*- coding: utf-8 -*-
"""新闻内容标签（news-impact-engine Phase 1）：LLM 给每条新闻打 topic/方向/量级。

纯内容判断、**不看价格**——量级是 a-priori 严重度（事件本身多大），不是市场实际反应。
用便宜的 flash 模型批量打；解析层过滤幻觉 id 与非法枚举（对齐 config 三张枚举）。
落库写 news_items.topic / news_direction / magnitude_tier / tagged_at。
"""
from __future__ import annotations

import json
import re

from loguru import logger
from sqlalchemy.orm import Session

import config
from models.news import NewsItem
from services import market_calendar
from services.deepseek_client import call_deepseek_chat
from services.time_utils import utc_now_naive

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
    result = call_deepseek_chat(
        payload,
        api_key=config.DEEPSEEK_API_KEY,
        timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, config.DEEPSEEK_READ_TIMEOUT),
        http_error_prefix="DeepSeek 打标返回",
        error_preview_chars=200,
        normalize_error_newlines=False,
    )
    content = result.content
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


_UNSET = object()


def update_news_tags(session: Session, news_id: int, topic: str | None | object = _UNSET,
                     magnitude_tier: str | None | object = _UNSET,
                     news_direction: str | None | object = _UNSET) -> NewsItem:
    """人工修正一条新闻的内容标签（标注页用）。校验枚举（必须在 config 三张库内）、落库、
    置 tagged_at（人工改过的不会再被自动重打）。没传的字段不动；显式传 None 清空。"""
    n = session.query(NewsItem).filter(NewsItem.id == news_id).first()
    if n is None:
        raise ValueError(f"新闻 #{news_id} 不存在")
    if topic == "":
        topic = None
    if magnitude_tier == "":
        magnitude_tier = None
    if news_direction == "":
        news_direction = None
    if topic is not _UNSET and topic is not None and topic not in config.NEWS_TOPICS:
        raise ValueError(f"非法 topic: {topic!r}")
    if magnitude_tier is not _UNSET and magnitude_tier is not None and magnitude_tier not in config.NEWS_MAGNITUDE_TIERS:
        raise ValueError(f"非法 magnitude: {magnitude_tier!r}")
    if news_direction is not _UNSET and news_direction is not None and news_direction not in config.NEWS_DIRECTIONS:
        raise ValueError(f"非法 direction: {news_direction!r}")
    if topic is not _UNSET:
        n.topic = topic
    if magnitude_tier is not _UNSET:
        n.magnitude_tier = magnitude_tier
    if news_direction is not _UNSET:
        n.news_direction = news_direction
    n.tagged_at = utc_now_naive()
    session.commit()
    return n


def tag_untagged(session: Session, limit: int = 500, batch_size: int | None = None) -> int:
    """给"可打标"的新闻分片打内容标签。"可打标" = 未打标 **且前置条件 traditional_open 已具备**
    （入库即设、backfill 兜底）。内容标签(topic/方向/量级)纯看新闻、**不看价格**，所以**不需要等反应
    窗口走完**——"窗口走完"只约束反应度量(theme_ledger.topic_recent_reactions)与 driver 标注，不约束
    内容打标。回灌脚本与每小时 settle job 共用。"""
    batch_size = int(batch_size or config.DEEPSEEK_BATCH_SIZE)
    todo = (
        session.query(NewsItem)
        .filter(NewsItem.tagged_at.is_(None), NewsItem.traditional_open.isnot(None))
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
