# -*- coding: utf-8 -*-
"""新闻内容标签：LLM 给每条新闻打**方向**（利多/利空/中性），纯内容判断、不看价格。

【2026-08-08 切换】topic 与量级(magnitude)停判停写，只判 direction：
- topic：事件池并行期验收通过，按 news-research-phase1-event-pool.md §13.4 既定步骤退役，
  语义归类由 research_events 挂接接替（标注页位置 = 事件徽章，spec §9.2）；
- 量级：用户拍板一并退役——spec §4.2 校准实测它是较差的重要性信号（"≥6 且量级非小"
  driver 召回 77% vs 分数闸门 96%），行为分类的新闻命中信号改用事件池闸门口径。
历史 news_items.topic / magnitude_tier 数据原地保留，不清洗。
tagged_at 继续盖章——它是事件挂接游标的前置（event_linking 捞 tagged_at 非空的新闻）。
用便宜的 flash 模型批量打；解析层过滤幻觉 id 与非法枚举。
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
    "你是宏观新闻分类员。给每条新闻打一个**纯内容**标签（只看新闻本身，**不看价格、不猜市场反应**）：\n\n"
    "direction（相对**风险资产**——BTC/纳指——的应然影响，三选一）：利多 / 利空 / 中性\n\n"
    "只返回 JSON，不要 Markdown：\n"
    '{"items": [{"id": int, "direction": "..."}, ...]}\n'
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
    """解析 items；过滤幻觉 id 与非法枚举（direction 必须在 config.NEWS_DIRECTIONS 内）。"""
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
        direction = item.get("direction")
        if direction not in config.NEWS_DIRECTIONS:
            continue
        out[nid] = {"direction": direction}
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
        # 2026-08-08 切换后只写方向；topic/magnitude_tier 不再写入（历史值原地保留）。
        n.news_direction = tags["direction"]
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
