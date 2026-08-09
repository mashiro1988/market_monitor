# -*- coding: utf-8 -*-
"""研究事件池·挂接调用(docs/specs/news-research-phase1-event-pool.md §4-§5)。

模型只有挂接权:把过闸新闻挂到某个进行中事件,或判不挂。闸门/黑名单/关键词免闸
全部在代码里判(可审计)。游标 news_items.event_linked_at 四种结果都盖章
(挂/不挂/不够格/黑名单),回扫=清游标。人工挂接无视本文件所有闸门(在 event_pool.py)。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy.orm import Session

import config
from models.news import NewsItem
from models.research import ResearchEvent, ResearchEventLink
from services.deepseek_client import call_deepseek_chat
from services.time_utils import utc_now_naive


def _is_blacklisted(news: NewsItem) -> bool:
    """固定栏目黑名单(spec §4.4):来源+标题正则都命中才算。"""
    for source, pattern in config.NEWS_EVENT_LINK_BLACKLIST:
        if news.source == source and re.search(pattern, news.title or ""):
            return True
    return False


def _split_keywords(raw: str | None) -> list[str]:
    """顿号分隔(容忍中英文逗号);空白剔除。"""
    if not raw:
        return []
    return [w.strip() for w in re.split(r"[、,，]", raw) if w.strip()]


def _active_events(session: Session, event_type: str = "macro") -> list[ResearchEvent]:
    return (session.query(ResearchEvent)
            .filter(ResearchEvent.status == "active",
                    ResearchEvent.event_type == event_type)
            .order_by(ResearchEvent.id.asc()).all())


def _keyword_pool(events: list[ResearchEvent]) -> list[str]:
    """全部进行中事件关键词的并集(免闸用;已关闭事件的词走沉睡监听,不在此)。"""
    out: list[str] = []
    for e in events:
        out.extend(_split_keywords(e.gate_keywords))
    return out


def _news_text(news: NewsItem) -> str:
    return f"{news.title or ''} {(news.content or '')[:200]}".lower()


def passes_gate(news: NewsItem, keywords: list[str]) -> bool:
    """宏观闸门(spec §4.1):≥6 或未评分 或命中任一进行中事件关键词。
    免闸≠指定归属——挂到哪仍由模型对整个活跃池判断。"""
    if news.llm_importance is None or news.llm_importance >= config.EVENT_LINK_MIN_IMPORTANCE:
        return True
    text = _news_text(news)
    return any(k.lower() in text for k in keywords)


def passes_crypto_gate(news: NewsItem) -> bool:
    """加密线的闸是**语义闸**(web3 二期A design §3):加密源转载的纯宏观新闻
    (美联储/CPI/地缘)不进加密事件池——宏观自有宏观线管。

    分数闸在加密线**不设**:加密线 200-300 条/天量能扛得住全量过模型,而小币新闻
    分数天然低,用分数拦等于把二期B 异动归因的原料掐掉。"""
    return news.is_crypto_affair is True


# 市场 → 事件类型:两条线各看各的池子。人工跨挂不受此限(在 event_pool.py)。
MARKET_EVENT_TYPE = {"macro": "macro", "crypto": "crypto"}


LINK_SYSTEM_PROMPT = (
    "你是宏观新闻研究助理。下面给你一份【活跃事件池】和一批新闻,判断每条新闻是否是池中某个事件的新证据。\n"
    "规则:\n"
    "- 只做归类,不评判新闻重要性;新闻与所有事件都无关 → event_id 给 null(不挂)。\n"
    "- 不确定就不挂:只有主体与事态确实属于该事件才挂,模糊相似不算。\n"
    "- 转载/同一起源的重复报道照挂(时间轴自会显示簇拥,人工把关兜底)。\n"
    "只返回 JSON,不要 Markdown:\n"
    '{"items": [{"id": 新闻id, "event_id": 事件编号或null, "confidence": 0.9}, ...]}\n'
    "confidence 三档:0.9=明确属于;0.65=大概率属于;0.3=勉强(倾向不挂)。\n"
    "每条输入新闻在 items 里有且仅有一项,id 严格对应输入;event_id 必须是池中编号。"
)

# 版本戳:每次实质性修改 LINK_SYSTEM_PROMPT 时更新;随每条 auto 挂接落库。
LINK_PROMPT_VERSION = "link-v1-20260802"

CRYPTO_LINK_SYSTEM_PROMPT = (
    "你是加密市场研究助理。下面给你一份【活跃事件池】和一批加密新闻,判断每条新闻"
    "是否是池中某个事件的新证据。\n"
    "规则:\n"
    "- 只做归类,不评判新闻重要性;新闻与所有事件都无关 → event_id 给 null(不挂)。\n"
    "- 不确定就不挂:只有主体(项目/协议/交易所/资产)与事态确实属于该事件才挂。\n"
    "- 同一个币可能同时有多条事件线(如解锁与生态基金是两件事),按事态归属判断,"
    "别只看币名相同就挂。\n"
    "- 转载/同一起源的重复报道照挂(时间轴自会显示簇拥,人工把关兜底)。\n"
    "只返回 JSON,不要 Markdown:\n"
    '{"items": [{"id": 新闻id, "event_id": 事件编号或null, "confidence": 0.9}, ...]}\n'
    "confidence 三档:0.9=明确属于;0.65=大概率属于;0.3=勉强(倾向不挂)。\n"
    "每条输入新闻在 items 里有且仅有一项,id 严格对应输入;event_id 必须是池中编号。"
)
CRYPTO_LINK_PROMPT_VERSION = "crypto-link-v1-20260809"

VALID_CONFIDENCES = (0.9, 0.65, 0.3)


def _pool_summary(session: Session, events: list[ResearchEvent]) -> str:
    """活跃池摘要:编号+名称+首条证据标题(定义锚)+最近证据日期(spec §4.3)。"""
    lines = []
    for e in events:
        rows = (session.query(NewsItem)
                .join(ResearchEventLink, ResearchEventLink.news_id == NewsItem.id)
                .filter(ResearchEventLink.event_id == e.id,
                        ResearchEventLink.detached.is_(False))
                .order_by(NewsItem.timestamp.asc()).all())
        first_title = (rows[0].title or "")[:60] if rows else "(暂无证据)"
        last_date = rows[-1].timestamp.strftime("%m-%d") if rows else "—"
        lines.append(f"#{e.id} {e.name} | 首条证据: {first_title} | 最近证据: {last_date}")
    return "\n".join(lines)


def _build_link_payload(pool_summary: str, news_list: list[NewsItem]) -> str:
    items = [{"id": n.id, "source": n.source, "title": (n.title or "")[:160],
              "content": (n.content or "")[:200]} for n in news_list]
    return (f"【活跃事件池】\n{pool_summary}\n\n"
            f"【新闻,共 {len(items)} 条】\n{json.dumps({'news': items}, ensure_ascii=False)}")


def _call_linker(user_content: str, system_prompt: str = LINK_SYSTEM_PROMPT) -> str:
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置,无法挂接")
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 2000,
        "temperature": 0,
    }
    result = call_deepseek_chat(
        payload, api_key=config.DEEPSEEK_API_KEY,
        timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, config.DEEPSEEK_READ_TIMEOUT),
        http_error_prefix="DeepSeek 挂接返回", error_preview_chars=200,
        normalize_error_newlines=False,
    )
    if not result.content:
        raise RuntimeError("DeepSeek 挂接返回空 content")
    return result.content


def _parse_link_response(raw: str, valid_news_ids: set[int],
                         valid_event_ids: set[int]) -> dict[int, dict]:
    """防幻觉(spec §4.3):新闻 id 必须在本批、event_id 必须在池内(或 null)、
    confidence 必须三档;非法条目整条丢弃(不盖游标,下轮重试)。"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            raise ValueError(f"挂接返回非 JSON: {text[:200]}")
        data = json.loads(m.group(0))
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("挂接返回缺少 items 列表")
    out: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            nid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if nid not in valid_news_ids:
            continue
        event_id = item.get("event_id")
        if event_id is None:
            out[nid] = {"event_id": None, "confidence": None}
            continue
        try:
            event_id = int(event_id)
        except (TypeError, ValueError):
            continue
        if event_id not in valid_event_ids:
            continue
        if item.get("confidence") not in VALID_CONFIDENCES:
            continue
        out[nid] = {"event_id": event_id, "confidence": float(item["confidence"])}
    return out


def _create_auto_link(session: Session, event_id: int, news_id: int,
                      confidence: float | None,
                      prompt_version: str = LINK_PROMPT_VERSION) -> ResearchEventLink:
    existing = (session.query(ResearchEventLink)
                .filter_by(event_id=event_id, news_id=news_id).first())
    if existing:
        return existing        # 唯一约束:已有挂接(含人工/已摘下)不重复建、不覆盖
    link = ResearchEventLink(event_id=event_id, news_id=news_id, link_source="auto",
                             auto_event_id=event_id, confidence=confidence,
                             prompt_version=prompt_version)
    session.add(link)
    return link


def link_unprocessed(session: Session, limit: int = 200,
                     batch_size: int | None = None, market: str = "macro") -> dict:
    """tick 入口(spec §4.1):处理游标为空的新闻,四种结果都盖章。
    返回 {"processed": 盖章数, "linked": 新增挂接数, "called": 进LLM条数}。

    market="macro" 走分数闸 + 关键词免闸;market="crypto" 走语义闸
    (is_crypto_affair,web3 二期A design §3)。两条线各看各的事件池。"""
    stats = {"processed": 0, "linked": 0, "called": 0}
    is_crypto = market == "crypto"
    events = _active_events(session, MARKET_EVENT_TYPE.get(market, "macro"))
    if not events:
        return stats                     # 池空整段跳过,零调用、游标不动
    keywords = _keyword_pool(events)
    todo = (session.query(NewsItem)
            .filter(NewsItem.tagged_at.isnot(None), NewsItem.event_linked_at.is_(None),
                    NewsItem.market == market)
            .order_by(NewsItem.timestamp.desc())
            .limit(max(1, limit)).all())
    now = utc_now_naive()
    to_llm: list[NewsItem] = []
    for n in todo:
        gate_ok = passes_crypto_gate(n) if is_crypto else passes_gate(n, keywords)
        if _is_blacklisted(n) or not gate_ok:
            n.event_linked_at = now      # 不够格/黑名单:盖章零调用
            stats["processed"] += 1
        else:
            to_llm.append(n)
    session.commit()
    if not to_llm:
        return stats
    system_prompt = CRYPTO_LINK_SYSTEM_PROMPT if is_crypto else LINK_SYSTEM_PROMPT
    prompt_version = CRYPTO_LINK_PROMPT_VERSION if is_crypto else LINK_PROMPT_VERSION
    pool_summary = _pool_summary(session, events)
    valid_event_ids = {int(e.id) for e in events}
    batch_size = int(batch_size or config.DEEPSEEK_BATCH_SIZE)
    for i in range(0, len(to_llm), batch_size):
        chunk = to_llm[i:i + batch_size]
        stats["called"] += len(chunk)
        try:
            raw = _call_linker(_build_link_payload(pool_summary, chunk), system_prompt)
            parsed = _parse_link_response(raw, {int(n.id) for n in chunk}, valid_event_ids)
        except Exception as exc:         # 整批失败:不盖游标,下轮重试
            logger.error(f"[EventLink] 分片挂接失败({len(chunk)} 条): {exc}")
            continue
        now = utc_now_naive()
        by_id = {int(n.id): n for n in chunk}
        for nid, r in parsed.items():
            n = by_id.get(nid)
            if n is None:
                continue
            if r["event_id"] is not None:
                _create_auto_link(session, r["event_id"], nid, r["confidence"], prompt_version)
                stats["linked"] += 1
            n.event_linked_at = now      # 只有合法解析条目盖章(含"不挂")
            stats["processed"] += 1
        session.commit()
    return stats


KEYWORD_SUGGEST_PROMPT = (
    "你是研究助理。给一个宏观研究事件起 3-6 个'免闸关键词',用于从新闻标题+摘要匹配该事件的后续报道。\n"
    "取词规则(spec §5.2):\n"
    "1. 实体词优先,中英别名都要(如:苹果、Apple、iPhone——中文源与英文源都要能命中);\n"
    "2. 每个词单独命中时应大概率与本事件相关('植田'行,'加息'不行——太泛);\n"
    "3. 禁单字与泛词('油''美股''关税'会让闸门虚设);\n"
    "4. 3-6 个。\n"
    '只返回 JSON:{"keywords": ["词1", "词2"]}'
)


def _call_keyword_suggester(user_content: str) -> str:
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": KEYWORD_SUGGEST_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 500,
        "temperature": 0,
    }
    result = call_deepseek_chat(
        payload, api_key=config.DEEPSEEK_API_KEY,
        timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, config.DEEPSEEK_READ_TIMEOUT),
        http_error_prefix="DeepSeek 关键词建议返回", error_preview_chars=200,
        normalize_error_newlines=False,
    )
    if not result.content:
        raise RuntimeError("DeepSeek 关键词建议返回空 content")
    return result.content


def _parse_suggest_response(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError(f"关键词建议返回非 JSON: {raw[:100]}")
    kws = data.get("keywords") if isinstance(data, dict) else None
    if not isinstance(kws, list):
        raise ValueError("关键词建议缺少 keywords 列表")
    out = [str(k).strip() for k in kws if str(k).strip()]
    return out[:6]


def suggest_keywords(session: Session, name: str, news_ids: list[int]) -> list[str]:
    """AI 建议关键词(spec §5.2):即用即弃不留痕;落库的永远是人确认后的版本。
    单发调用没有游标重试兜底,DeepSeek 偶发空返回/坏 JSON 时自动重试一次再抛。"""
    rows = session.query(NewsItem).filter(NewsItem.id.in_(news_ids)).all()
    items = [{"title": (n.title or "")[:160], "content": (n.content or "")[:200]} for n in rows]
    user = f"事件名:{name}\n种子新闻:\n{json.dumps(items, ensure_ascii=False)}"
    last_exc: Exception = RuntimeError("关键词建议未执行")
    for _attempt in range(2):
        try:
            return _parse_suggest_response(_call_keyword_suggester(user))
        except (RuntimeError, ValueError) as exc:
            last_exc = exc
    raise last_exc


def clear_link_cursor(session: Session, hours: float, now: datetime | None = None,
                      market: str = "macro") -> int:
    """回扫=清游标(spec §6.3):范围内**当前够格**(过闸或命中关键词、不在黑名单)
    且**无未摘下挂接**的新闻,游标清空 → 下轮 tick 对着更新后的池子自然重收。
    立案/重开/改关键词勾选时用默认 72h;深回扫按钮传更大的 hours。返回清空条数。

    "当前够格"按 market 各判各的:加密线走语义闸,别把转载宏观又捞回来。"""
    now = now or utc_now_naive()
    is_crypto = market == "crypto"
    events = _active_events(session, MARKET_EVENT_TYPE.get(market, "macro"))
    keywords = _keyword_pool(events)
    cutoff = now - timedelta(hours=hours)
    linked_ids = {row[0] for row in session.query(ResearchEventLink.news_id)
                  .filter(ResearchEventLink.detached.is_(False)).all()}
    rows = (session.query(NewsItem)
            .filter(NewsItem.timestamp >= cutoff,
                    NewsItem.market == market,
                    NewsItem.event_linked_at.isnot(None)).all())
    cleared = 0
    for n in rows:
        if int(n.id) in linked_ids:
            continue
        gate_ok = passes_crypto_gate(n) if is_crypto else passes_gate(n, keywords)
        if _is_blacklisted(n) or not gate_ok:
            continue
        n.event_linked_at = None
        cleared += 1
    session.commit()
    return cleared
