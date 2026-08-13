# -*- coding: utf-8 -*-
"""事件池·AI 梳理(docs/specs/2026-08-13-pool-sweep-design.md)。

与挂接器(event_linking)的分工:挂接器逐条问"这条新闻挂到哪个进行中事件";
梳理是**集合层盘点**——把近 N 天未挂接的过闸快讯全量摊开,让思考模型一次看全局:
1) 该立而未立的事件聚出来(new_events);2) 属于现有事件的漏网证据补挂(attach);
3) 其余日常流水不动。按钮触发、同步返回,不进定时任务(用户拍板 2026-08-13)。

铁律照旧:
- 立案落库走 create_event(created_from="sweep"),种子挂接记 link_source="auto" +
  prompt_version——纠错率审计对梳理产物同样生效,人随时摘下/改归属/关闭。
- 防幻觉与挂接器同一套:新闻 id 必须在本批、事件 id 必须在活跃池,非法条目整条丢弃。
- 有上限就要说出来:截断/超额丢弃都写进返回值,不静默。
"""
from __future__ import annotations

import json
import re
import threading
from datetime import timedelta

from loguru import logger
from sqlalchemy.orm import Session

import config
from models.news import NewsItem
from models.research import ResearchEventLink
from services.deepseek_client import call_deepseek_chat
from services.event_linking import (
    MARKET_EVENT_TYPE, VALID_CONFIDENCES, _active_events, _create_auto_link,
)
from services.event_pool import buffer_predicate, create_event
from services.time_utils import utc_now_naive

SWEEP_PROMPT_VERSION = "sweep-v1-20260813"

SWEEP_SYSTEM_PROMPT = (
    "你是研究事件池的盘点员。输入是【现有事件池】(进行中事件)和一批【未挂接快讯】。\n"
    "三类产出:\n"
    "1) new_events:池里缺、但值得跟踪的事件。硬门槛(不满足就不立):同一主题 ≥3 条快讯,"
    "或单一主体的重大事态(被盗/暴雷/监管处罚/停摆)≥2 条。事件名 ≤30 字,必须含主体+事态;"
    "keywords 给 3-6 个免闸关键词——每个词单独命中时应大概率与本事件相关,禁单字与泛词"
    "(如'美股''关税'会让闸门虚设),实体词优先、中英别名都要;news_ids 列出属于该事件的"
    "全部输入快讯 id。\n"
    "2) attach:未挂快讯里属于【现有事件池】某事件新证据的,逐条给 news_id、event_id 和"
    " confidence(0.9=明确属于;0.65=大概率;0.3=勉强)。只有主体与事态确实属于才挂,"
    "模糊相似不算;转载/重复报道照挂。\n"
    "3) 其余(日常行情流水、巨鲸盘口、孤立信号)不要输出。\n"
    "宁缺勿滥:不确定就不立、不挂;new_events 最多 8 个。\n"
    "只返回 JSON,不要 Markdown:\n"
    '{"new_events": [{"name": "...", "keywords": ["..."], "news_ids": [1, 2], "why": "一句话"}],\n'
    ' "attach": [{"news_id": 3, "event_id": 5, "confidence": 0.9}]}\n'
    "所有 news_id 必须来自输入、event_id 必须来自现有事件池编号。"
)

# 单进程内防连点:同一时刻只允许一次梳理在跑(部署是单 worker,进程锁即全局锁)
_SWEEP_LOCK = threading.Lock()


class SweepBusy(RuntimeError):
    """已有一次梳理在进行中(路由层译成 409)。"""


def _gather(session: Session, market: str, days: int, max_news: int):
    """取本线近 N 天、过闸、无未摘下挂接的快讯(=缓冲区口径,复用唯一判定源)。"""
    now = utc_now_naive()
    is_buffer = buffer_predicate(session, market=market)
    rows = (session.query(NewsItem)
            .filter(NewsItem.market == market,
                    NewsItem.tagged_at.isnot(None),
                    NewsItem.timestamp >= now - timedelta(days=days))
            .order_by(NewsItem.timestamp.desc()).all())
    picked = [n for n in rows if is_buffer(n)]
    return picked[:max_news], len(picked) > max_news


def _pool_lines(events) -> str:
    return "\n".join(f"#{e.id} {e.name} | 免闸词:{e.gate_keywords or '—'}"
                     for e in events) or "(池空)"


def _build_payload(events, news_list) -> str:
    items = [{"id": int(n.id),
              "t": n.timestamp.strftime("%m-%d %H:%M") if n.timestamp else "",
              "source": n.source,
              "title": (n.title or "")[:160],
              "content": (n.content or "")[:120]} for n in news_list]
    return (f"【现有事件池】\n{_pool_lines(events)}\n\n"
            f"【未挂接快讯,共 {len(items)} 条】\n"
            f"{json.dumps({'news': items}, ensure_ascii=False)}")


def _call_sweep(user_content: str) -> tuple[str, float]:
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置,无法梳理")
    payload = {
        "model": config.DEEPSEEK_REASONER_MODEL,
        "messages": [
            {"role": "system", "content": SWEEP_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        # 与自动标注同一套 thinking 口径(annotation_service._call_deepseek_reasoner_batch)
        "thinking": {"type": "enabled", "reasoning_effort": config.DEEPSEEK_REASONER_EFFORT},
        "response_format": {"type": "json_object"},
        # max_tokens 同时覆盖 reasoning_content + content:全池盘点思考量大,给足预算
        "max_tokens": config.RESEARCH_SWEEP_MAX_TOKENS,
    }
    result = call_deepseek_chat(
        payload, api_key=config.DEEPSEEK_API_KEY,
        timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, config.DEEPSEEK_REASONER_BATCH_READ_TIMEOUT),
        http_error_prefix="DeepSeek 梳理返回", error_preview_chars=200,
        normalize_error_newlines=False,
    )
    if not result.content:
        raise RuntimeError(
            f"DeepSeek 梳理返回空 content(reasoning 预览: {result.reasoning_content[:200]})")
    return result.content, result.duration_seconds


def _parse_sweep(raw: str, valid_news_ids: set[int], valid_event_ids: set[int]) -> dict:
    """防幻觉(与 event_linking._parse_link_response 同一铁律):id 白名单校验、
    confidence 三档校验,非法条目整条丢弃;名字/关键词做长度与数量夹紧。"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            raise ValueError(f"梳理返回非 JSON: {text[:200]}")
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("梳理返回不是 JSON 对象")
    new_events: list[dict] = []
    for item in (data.get("new_events") or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:80]
        raw_ids = item.get("news_ids") if isinstance(item.get("news_ids"), list) else []
        ids: list[int] = []
        for nid in raw_ids:
            try:
                nid = int(nid)
            except (TypeError, ValueError):
                continue
            if nid in valid_news_ids and nid not in ids:
                ids.append(nid)
        kws = [str(k).strip()[:24] for k in (item.get("keywords") or [])
               if len(str(k).strip()) >= 2][:8]         # 禁单字(spec §5.2),数量夹紧
        if not name or len(ids) < 2:                    # 兜底门槛:至少 2 条真实成员
            continue
        new_events.append({"name": name, "keywords": kws, "news_ids": ids,
                           "why": str(item.get("why") or "").strip()[:120]})
    attach: list[dict] = []
    for item in (data.get("attach") or []):
        if not isinstance(item, dict):
            continue
        try:
            nid, eid = int(item.get("news_id")), int(item.get("event_id"))
        except (TypeError, ValueError):
            continue
        if nid not in valid_news_ids or eid not in valid_event_ids:
            continue
        if item.get("confidence") not in VALID_CONFIDENCES:
            continue
        attach.append({"news_id": nid, "event_id": eid,
                       "confidence": float(item["confidence"])})
    return {"new_events": new_events, "attach": attach}


def run_sweep(session: Session, event_type: str = "macro", days: int | None = None,
              dry_run: bool = False) -> dict:
    """一次全量梳理:未挂快讯 → 思考模型盘点 → 立案 + 补挂 → 摘要。"""
    if event_type not in MARKET_EVENT_TYPE:
        raise ValueError(f"非法 event_type: {event_type!r}")
    if not _SWEEP_LOCK.acquire(blocking=False):
        raise SweepBusy("已有一次梳理在进行中,等它跑完再点")
    try:
        days = days or config.RESEARCH_SWEEP_DAYS
        events = _active_events(session, event_type)
        news, truncated = _gather(session, event_type, days, config.RESEARCH_SWEEP_MAX_NEWS)
        base = {"event_type": event_type, "scanned": len(news), "truncated": truncated,
                "created": [], "attached": 0, "skipped_new_events": 0,
                "duration_seconds": 0.0, "dry_run": dry_run}
        if not news:
            return base
        raw, duration = _call_sweep(_build_payload(events, news))
        base["duration_seconds"] = round(duration, 1)
        try:
            parsed = _parse_sweep(raw, {int(n.id) for n in news},
                                  {int(e.id) for e in events})
        except ValueError as exc:      # LLM 输出坏形状 = 上游故障,不是请求错误
            raise RuntimeError(str(exc)) from exc

        # 模型把现有事件"重新发明"了 → 降级为向那个事件补挂(冗余变成有用证据)
        by_name = {e.name.casefold(): e for e in events}
        attaches = list(parsed["attach"])
        creates = []
        for ne in parsed["new_events"]:
            hit = by_name.get(ne["name"].casefold())
            if hit is not None:
                attaches.extend({"news_id": nid, "event_id": int(hit.id), "confidence": 0.65}
                                for nid in ne["news_ids"])
                continue
            creates.append(ne)
        over = len(creates) - config.RESEARCH_SWEEP_MAX_NEW_EVENTS
        if over > 0:
            base["skipped_new_events"] = over
            creates = creates[:config.RESEARCH_SWEEP_MAX_NEW_EVENTS]

        if dry_run:
            base["created"] = [{"id": 0, "display_no": 0, "name": ne["name"],
                                "news_count": len(ne["news_ids"]), "why": ne["why"]}
                               for ne in creates]
            base["attached"] = len(attaches)
            return base

        for ne in creates:
            e = create_event(session, ne["name"], ne["news_ids"],
                             gate_keywords="、".join(ne["keywords"]) or None,
                             created_from="sweep", event_type=event_type,
                             link_source="auto", prompt_version=SWEEP_PROMPT_VERSION)
            base["created"].append({"id": int(e.id), "display_no": int(e.display_no or 0),
                                    "name": e.name, "news_count": len(ne["news_ids"]),
                                    "why": ne["why"]})
        applied = 0
        for a in attaches:
            dup = (session.query(ResearchEventLink)
                   .filter_by(event_id=a["event_id"], news_id=a["news_id"]).first())
            if dup is not None:
                continue               # 含已摘下的:人摘过的不悄悄挂回去
            _create_auto_link(session, a["event_id"], a["news_id"], a["confidence"],
                              prompt_version=SWEEP_PROMPT_VERSION)
            applied += 1
        session.commit()
        base["attached"] = applied
        logger.info("[PoolSweep] {} 梳理完成:扫描 {} 条,新立 {},补挂 {},llm {}s",
                    event_type, len(news), len(base["created"]), applied,
                    base["duration_seconds"])
        return base
    finally:
        _SWEEP_LOCK.release()
