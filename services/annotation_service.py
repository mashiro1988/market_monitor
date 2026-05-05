from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta

import requests
from loguru import logger
from sqlalchemy.orm import Session

import config
from models.news import NewsItem, NewsPriceAnnotation
from models.price import PriceSnapshot
from schemas.annotations import (
    AnnotationCreateRequest,
    AnnotationDetail,
    AnnotationListItem,
    AnnotationResponse,
    AnnotationSymbol,
    AutoAnnotateBatchItem,
    AutoAnnotateBatchRequest,
    AutoAnnotateBatchResponse,
    AutoAnnotateRequest,
    AutoAnnotateResponse,
    ContextNewsResponse,
    PriceRuleSchema,
    PriceWindowSchema,
)
from services.news_service import to_news_schema
from services.time_utils import parse_datetime, timestamp_pair, utc_now_naive

TARGET_PRICE_SYMBOLS = ["BTC/USDT", "NQ=F"]

# 每个标注窗口前后取的候选新闻范围：向前 15 分钟，向后 30 分钟。
# 与 upsert_annotation 写入 context_start / context_end 的偏移保持一致。
CONTEXT_PRE_MINUTES_DEFAULT = 15
CONTEXT_POST_MINUTES_DEFAULT = 30


def _annotation_news_sources() -> list[str]:
    """标注上下文 / 自动标注 候选新闻的源白名单——只读取在 `config.NEWS_SOURCES` 里启用的。
    切换 / 增减英文源（CNBC / Reuters 等）时只改 config，不动业务代码。"""
    return [k for k, v in config.NEWS_SOURCES.items() if v.get("enabled")]

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

AUTO_ANNOTATE_SYSTEM_PROMPT = """你是一名买方量化研究员，专门分析单一资产在短时间窗口内的价格异动是否由特定新闻事件触发。

你将收到：
1. 一段价格异动窗口的元数据（symbol、起止时间、价格变化百分比、阈值）。
2. 该窗口前后一段时间内的候选新闻条目（含 id、北京时间、来源、LLM 评分、标题、内容片段）。

**关于新闻时间戳的关键说明**：候选新闻的 time_bj 是新闻**发布时间**，不是事件发生时间。中文财经源（华尔街见闻、jin10）和翻译类英文源经常把欧美时段的事件延迟 5-30 分钟才推送到国内。所以：
- 窗口结束后 0-30 分钟内出现的高分新闻，如果其内容描述的事件**明显发生在窗口期间或之前**（如 FOMC 决议、CPI 公布、美股盘中事件），仍视为价格异动的有效触发，**优先选中**。
- 只有当新闻内容明显描述窗口结束之后才发生的事件（如另一场会议、次日数据、当晚才出的声明），才把它当作"窗口后新闻"忽略。
- 当不确定事件何时发生时，倾向于把它视为发布延迟、可作为触发。

请基于以下原则判断哪些新闻是这次价格异动的因果触发：
- 优先选择窗口内、窗口开始前 0-15 分钟、以及上述"发布延迟"判定为有效的窗口后新闻。
- 优先选择 LLM 评分 ≥7 或源端 jin10 标注重要的新闻。
- 选中的新闻必须与该 symbol 或其驱动因素（宏观、监管、流动性、地缘风险、行业事件）直接相关。
- 仅做新闻 → 价格的归因，不要做"价格 → 新闻"的反推。
- 如果候选新闻里没有任何明显的因果触发，返回 no_clear_news=true 并把 selected_news_ids 留空。

只返回 JSON，不要 Markdown 标记，不要解释性正文。格式：
{
  "selected_news_ids": [int, ...],   // 必须是候选新闻列表里实际存在的 id
  "no_clear_news": bool,
  "summary": "不超过 80 字的因果归因结论"
}
"""


AUTO_ANNOTATE_BATCH_SYSTEM_PROMPT = """你是一名买方量化研究员。你将一次性收到一组价格异动窗口及其各自的候选新闻列表，
对**每个**窗口分别判断哪些新闻是因果触发。

输入结构：
{
  "windows": [
    {
      "id": int,                                  // 窗口编号，必须在返回中按相同 id 引用
      "symbol", "start_utc", "end_utc",
      "threshold_pct", "price_start", "price_end", "change_pct",
      "candidates": [{ "id": int, "time_bj", "source", "llm_score", ... }, ...]
    },
    ...
  ]
}

**关于新闻时间戳的关键说明**：候选新闻的 time_bj 是新闻**发布时间**，不是事件发生时间。中文财经源（华尔街见闻、jin10）和翻译类英文源经常把欧美时段的事件延迟 5-30 分钟才推送到国内。所以：
- 窗口结束后 0-30 分钟内出现的高分新闻，如果其内容描述的事件**明显发生在窗口期间或之前**（如 FOMC 决议、CPI 公布、美股盘中事件），仍视为价格异动的有效触发，**优先选中**。
- 只有当新闻内容明显描述窗口结束之后才发生的事件（如另一场会议、次日数据、当晚才出的声明），才把它当作"窗口后新闻"忽略。
- 当不确定事件何时发生时，倾向于把它视为发布延迟、可作为触发。

判断原则与单窗口模式一致：
- 优先选择窗口内、窗口开始前 0-15 分钟、以及上述"发布延迟"判定为有效的窗口后新闻。
- 优先选择 LLM 评分 ≥7 或源端 jin10 标注重要的新闻。
- 选中的新闻必须与该 symbol 或其驱动因素（宏观、监管、流动性、地缘风险、行业事件）直接相关。
- 仅做新闻 → 价格的归因。
- 候选里没有明显因果时返回 no_clear_news=true，selected_news_ids=[]。

每个 window 必须在输出 items 中**有且只有一项**，window_id 与输入的 id 严格对应。每个 item 必须独立给出 reasoning，不要写"同上"或跨窗口共用。

只返回 JSON：
{
  "items": [
    {
      "window_id": int,
      "selected_news_ids": [int, ...],   // 只能引用对应 window 自己 candidates 列表里的 id
      "no_clear_news": bool,
      "summary": "不超过 80 字的归因结论",
      "reasoning": "150-250 字解释为什么选这些新闻、为什么排除其他高分候选、或为什么 no_clear_news；该字段只属于这个 window，禁止跨窗口复用。"
    },
    ...
  ]
}
"""

# 一次批量调用最多塞 N 个窗口，避免上下文过长把 reasoning 时间和成本拉爆。
AUTO_ANNOTATE_BATCH_LIMIT = 10


def load_alert_price_rules() -> list[PriceRuleSchema]:
    rules: list[PriceRuleSchema] = []
    for rule in config.ALERT_RULES:
        if not rule.get("enabled", True) or rule.get("rule_type") != "price_change":
            continue
        params = rule.get("params", {})
        symbol = params.get("symbol")
        threshold = params.get("threshold_pct")
        window_minutes = params.get("window_minutes")
        if symbol in TARGET_PRICE_SYMBOLS and threshold is not None:
            rules.append(
                PriceRuleSchema(
                    symbol=symbol,
                    threshold_pct=float(threshold),
                    window_minutes=int(window_minutes or config.SCAN_INTERVALS["price"]),
                )
            )
    return rules


def load_symbols(session: Session, hours: int = 72) -> list[AnnotationSymbol]:
    cutoff = utc_now_naive() - timedelta(hours=max(1, min(int(hours or 72), 24 * 30)))
    rule_symbols = {rule.symbol for rule in load_alert_price_rules()}
    rows = (
        session.query(PriceSnapshot.symbol, PriceSnapshot.name, PriceSnapshot.asset_class)
        .filter(PriceSnapshot.timestamp >= cutoff, PriceSnapshot.symbol.in_(list(rule_symbols or TARGET_PRICE_SYMBOLS)))
        .distinct()
        .order_by(PriceSnapshot.asset_class, PriceSnapshot.symbol)
        .all()
    )
    return [AnnotationSymbol(symbol=row.symbol, name=row.name, asset_class=row.asset_class) for row in rows]


def _nearest_snapshot(rows: list[PriceSnapshot], target_time: datetime, before_time: datetime, tolerance_minutes: int) -> PriceSnapshot | None:
    candidates = [
        row for row in rows
        if row.timestamp < before_time
        if abs((row.timestamp - target_time).total_seconds()) <= tolerance_minutes * 60
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda row: abs((row.timestamp - target_time).total_seconds()))


def load_price_windows(
    session: Session,
    symbol: str,
    hours: int,
    threshold_pct: float | None = None,
    window_minutes: int | None = None,
) -> list[PriceWindowSchema]:
    rule_map = {rule.symbol: rule for rule in load_alert_price_rules()}
    rule = rule_map.get(symbol)
    if rule is None and (threshold_pct is None or window_minutes is None):
        return []
    threshold_pct = float(threshold_pct if threshold_pct is not None else rule.threshold_pct)
    window_minutes = int(window_minutes if window_minutes is not None else rule.window_minutes)
    hours = max(1, min(int(hours or 72), 24 * 30))
    cutoff = utc_now_naive() - timedelta(hours=hours, minutes=window_minutes + 10)
    rows = (
        session.query(PriceSnapshot)
        .filter(PriceSnapshot.symbol == symbol, PriceSnapshot.timestamp >= cutoff)
        .order_by(PriceSnapshot.timestamp.asc())
        .all()
    )
    display_cutoff = utc_now_naive() - timedelta(hours=hours)
    tolerance_minutes = max(config.SCAN_INTERVALS["price"] * 2, 1)

    # 一次性把这个 symbol 在显示窗口里的已有标注全部捞出来，按 (window_start, window_end) 建查找表。
    annotation_rows = (
        session.query(NewsPriceAnnotation.id, NewsPriceAnnotation.window_start, NewsPriceAnnotation.window_end)
        .filter(
            NewsPriceAnnotation.symbol == symbol,
            NewsPriceAnnotation.window_end >= display_cutoff,
        )
        .all()
    )
    annotation_index: dict[tuple[datetime, datetime], int] = {
        (row.window_start, row.window_end): row.id for row in annotation_rows
    }

    # Step 1：扫所有快照，找出全部超阈值的滚动窗口（"原始触发"）。
    triggers: list[tuple[datetime, dict]] = []  # (window_end_dt, schema_kwargs)
    for current in rows:
        if current.timestamp < display_cutoff:
            continue
        baseline_time = current.timestamp - timedelta(minutes=window_minutes)
        baseline = _nearest_snapshot(rows, baseline_time, current.timestamp, tolerance_minutes)
        if baseline is None or not baseline.price:
            continue
        change_pct = ((current.price - baseline.price) / abs(baseline.price)) * 100
        if abs(change_pct) < threshold_pct:
            continue
        annotation_id = annotation_index.get((baseline.timestamp, current.timestamp))
        triggers.append((
            current.timestamp,
            {
                "symbol": current.symbol,
                "asset_class": current.asset_class,
                "name": current.name,
                "window_start": timestamp_pair(baseline.timestamp),
                "window_end": timestamp_pair(current.timestamp),
                "configured_window_minutes": window_minutes,
                "actual_window_minutes": round((current.timestamp - baseline.timestamp).total_seconds() / 60, 1),
                "price_start": baseline.price,
                "price_end": current.price,
                "change_pct": change_pct,
                "annotation_id": annotation_id,
            },
        ))

    # Step 2：按时间正序聚合连续 run。两个相邻触发同号且 window_end 间隔 ≤ window_minutes
    # 时视为同一连续异动；run 的第一个为 primary，其余为 secondary（is_primary=False）。
    triggers.sort(key=lambda t: t[0])
    enriched: list[tuple[datetime, datetime, PriceWindowSchema]] = []  # (run_anchor_dt, end_dt, schema)
    last_end_dt: datetime | None = None
    last_sign: int | None = None
    run_anchor_dt: datetime | None = None
    for end_dt, kwargs in triggers:
        sign = 1 if kwargs["change_pct"] >= 0 else -1
        is_primary = (
            last_end_dt is None
            or sign != last_sign
            or (end_dt - last_end_dt).total_seconds() / 60 > window_minutes
        )
        if is_primary:
            run_anchor_dt = end_dt
        kwargs["is_primary"] = is_primary
        last_end_dt = end_dt
        last_sign = sign
        enriched.append((run_anchor_dt, end_dt, PriceWindowSchema(**kwargs)))

    # Step 3：排序——最新的 run 排前面（按 anchor DESC），run 内部按 end ASC（primary 在前）。
    # 用稳定排序两步走：先按 end ASC，再按 anchor DESC，得到所需顺序。
    enriched.sort(key=lambda t: t[1])
    enriched.sort(key=lambda t: t[0], reverse=True)
    return [t[2] for t in enriched][:200]


def load_context_news(session: Session, context_start: datetime, context_end: datetime) -> ContextNewsResponse:
    rows = (
        session.query(NewsItem)
        .filter(
            NewsItem.source.in_(_annotation_news_sources()),
            NewsItem.timestamp >= context_start,
            NewsItem.timestamp <= context_end,
        )
        .order_by(NewsItem.timestamp.asc())
        .all()
    )
    return ContextNewsResponse(items=[to_news_schema(row) for row in rows])


def load_context_news_for_window(
    session: Session,
    window_start_utc: str,
    window_end_utc: str,
    pre_minutes: int = CONTEXT_PRE_MINUTES_DEFAULT,
    post_minutes: int = CONTEXT_POST_MINUTES_DEFAULT,
) -> ContextNewsResponse:
    start = parse_datetime(window_start_utc)
    end = parse_datetime(window_end_utc)
    if start is None or end is None:
        return ContextNewsResponse(items=[])
    return load_context_news(
        session,
        start - timedelta(minutes=pre_minutes),
        end + timedelta(minutes=post_minutes),
    )


def _find_window_snapshot(session: Session, symbol: str, timestamp_value: datetime) -> PriceSnapshot | None:
    return (
        session.query(PriceSnapshot)
        .filter(PriceSnapshot.symbol == symbol, PriceSnapshot.timestamp == timestamp_value)
        .first()
    )


def upsert_annotation(session: Session, request: AnnotationCreateRequest) -> AnnotationResponse:
    window_start = parse_datetime(request.window_start_utc)
    window_end = parse_datetime(request.window_end_utc)
    if window_start is None or window_end is None:
        raise ValueError("window_start_utc/window_end_utc 不能为空")

    start_snapshot = _find_window_snapshot(session, request.symbol, window_start)
    end_snapshot = _find_window_snapshot(session, request.symbol, window_end)
    if end_snapshot is None:
        raise ValueError("找不到窗口终点价格快照")
    if start_snapshot is None:
        raise ValueError("找不到窗口起点价格快照")

    existing = (
        session.query(NewsPriceAnnotation)
        .filter(
            NewsPriceAnnotation.symbol == request.symbol,
            NewsPriceAnnotation.window_start == window_start,
            NewsPriceAnnotation.window_end == window_end,
        )
        .first()
    )
    if existing is None:
        existing = NewsPriceAnnotation(
            symbol=request.symbol,
            window_start=window_start,
            window_end=window_end,
        )
        session.add(existing)

    existing.asset_class = end_snapshot.asset_class
    existing.context_start = window_start - timedelta(minutes=CONTEXT_PRE_MINUTES_DEFAULT)
    existing.context_end = window_end + timedelta(minutes=CONTEXT_POST_MINUTES_DEFAULT)
    existing.threshold_pct = request.threshold_pct
    existing.price_start = start_snapshot.price
    existing.price_end = end_snapshot.price
    existing.change_pct = ((end_snapshot.price - start_snapshot.price) / abs(start_snapshot.price)) * 100 if start_snapshot.price else None
    existing.causal_news_ids = json.dumps(request.selected_news_ids, ensure_ascii=False)
    if request.candidate_news_ids is not None:
        existing.candidate_news_ids = json.dumps(request.candidate_news_ids, ensure_ascii=False)
    existing.no_clear_news = request.no_clear_news
    existing.notes = (request.notes or "").strip() or None
    existing.labeler = (request.labeler or "").strip() or None
    if request.auto_reasoning is not None:
        existing.auto_reasoning = request.auto_reasoning.strip() or None
    if request.auto_summary is not None:
        existing.auto_summary = request.auto_summary.strip() or None
    existing.updated_at = utc_now_naive()
    session.commit()
    return AnnotationResponse(id=existing.id)


def _parse_news_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(value, list):
        return []
    return [int(item) for item in value if isinstance(item, (int, float, str)) and str(item).strip().lstrip("-").isdigit()]


def get_annotation_detail(session: Session, annotation_id: int) -> AnnotationDetail:
    row = session.query(NewsPriceAnnotation).filter(NewsPriceAnnotation.id == annotation_id).first()
    if row is None:
        raise ValueError(f"标注 #{annotation_id} 不存在")
    selected_ids = _parse_news_ids(row.causal_news_ids)
    selected_news: list = []
    if selected_ids:
        news_rows = session.query(NewsItem).filter(NewsItem.id.in_(selected_ids)).all()
        # 保持前端展示顺序与原始 selected_ids 一致。
        by_id = {item.id: item for item in news_rows}
        ordered = [by_id[i] for i in selected_ids if i in by_id]
        selected_news = [to_news_schema(item) for item in ordered]
    return AnnotationDetail(
        id=row.id,
        symbol=row.symbol,
        asset_class=row.asset_class,
        window_start=timestamp_pair(row.window_start),
        window_end=timestamp_pair(row.window_end),
        context_start=timestamp_pair(row.context_start),
        context_end=timestamp_pair(row.context_end),
        threshold_pct=row.threshold_pct,
        price_start=row.price_start,
        price_end=row.price_end,
        change_pct=row.change_pct,
        selected_news_ids=selected_ids,
        selected_news=selected_news,
        candidate_news_ids=_parse_news_ids(row.candidate_news_ids),
        no_clear_news=bool(row.no_clear_news),
        notes=row.notes,
        labeler=row.labeler,
        auto_reasoning=row.auto_reasoning,
        auto_summary=row.auto_summary,
        created_at=timestamp_pair(row.created_at),
        updated_at=timestamp_pair(row.updated_at),
    )


def list_annotations(session: Session, symbol: str | None, hours: int) -> list[AnnotationListItem]:
    """已标注的轻量列表，按 window_end 倒序。symbol 为空则不过滤。"""
    hours = max(1, min(int(hours or 72), 24 * 30))
    cutoff = utc_now_naive() - timedelta(hours=hours)
    query = session.query(NewsPriceAnnotation).filter(NewsPriceAnnotation.window_end >= cutoff)
    if symbol:
        query = query.filter(NewsPriceAnnotation.symbol == symbol)
    rows = query.order_by(NewsPriceAnnotation.window_end.desc()).limit(500).all()
    items: list[AnnotationListItem] = []
    for row in rows:
        selected_ids = _parse_news_ids(row.causal_news_ids)
        items.append(AnnotationListItem(
            id=row.id,
            symbol=row.symbol,
            asset_class=row.asset_class,
            window_start=timestamp_pair(row.window_start),
            window_end=timestamp_pair(row.window_end),
            change_pct=row.change_pct,
            no_clear_news=bool(row.no_clear_news),
            selected_count=len(selected_ids),
            labeler=row.labeler,
            notes=row.notes,
            created_at=timestamp_pair(row.created_at),
            updated_at=timestamp_pair(row.updated_at),
        ))
    return items


def delete_annotation(session: Session, annotation_id: int) -> int:
    row = session.query(NewsPriceAnnotation).filter(NewsPriceAnnotation.id == annotation_id).first()
    if row is None:
        raise ValueError(f"标注 #{annotation_id} 不存在")
    session.delete(row)
    session.commit()
    return annotation_id


def _build_auto_annotate_user_payload(
    request: AutoAnnotateRequest,
    candidate_news,
    price_start: float | None,
    price_end: float | None,
    change_pct: float | None,
) -> str:
    """组装喂给 reasoner 的用户消息。控制总长度，避免 token 超标。"""
    items = []
    for row in candidate_news:
        items.append({
            "id": row.id,
            "time_bj": (row.timestamp + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") if row.timestamp else None,
            "source": row.source,
            "llm_score": row.llm_importance,
            "jin10_important": bool(row.importance) if row.source == "jin10" else None,
            "title": (row.title or "")[:160],
            "content": (row.content or "")[:300],
        })

    body = {
        "window": {
            "symbol": request.symbol,
            "start_utc": request.window_start_utc,
            "end_utc": request.window_end_utc,
            "threshold_pct": request.threshold_pct,
            "price_start": price_start,
            "price_end": price_end,
            "change_pct": change_pct,
        },
        "candidate_news": items,
    }
    return f"共 {len(items)} 条候选新闻。\n{json.dumps(body, ensure_ascii=False)}"


def _call_deepseek_reasoner(user_content: str) -> tuple[str, str, float]:
    """调 DeepSeek v4-pro thinking 模式，返回 (content, reasoning_content, duration_seconds)。"""
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，无法调用自动标注")

    payload = {
        "model": config.DEEPSEEK_REASONER_MODEL,
        "messages": [
            {"role": "system", "content": AUTO_ANNOTATE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "thinking": {
            "type": "enabled",
            "reasoning_effort": config.DEEPSEEK_REASONER_EFFORT,
        },
        "response_format": {"type": "json_object"},
        "max_tokens": 4000,
    }
    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    started = time.monotonic()
    resp = requests.post(
        DEEPSEEK_API_URL,
        json=payload,
        headers=headers,
        timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, config.DEEPSEEK_REASONER_READ_TIMEOUT),
    )
    duration = time.monotonic() - started

    if resp.status_code >= 400:
        preview = resp.text[:300].replace("\n", " ")
        raise RuntimeError(f"DeepSeek 返回 {resp.status_code}: {preview}")

    body = resp.json()
    message = body["choices"][0].get("message", {})
    content = (message.get("content") or "").strip()
    reasoning = (message.get("reasoning_content") or "").strip()
    if not content:
        raise RuntimeError(f"DeepSeek 返回空 content（reasoning 预览: {reasoning[:200]}）")
    return content, reasoning, duration


def _parse_auto_annotate_response(raw: str, valid_ids: set[int]) -> tuple[list[int], bool, str]:
    """从 reasoner 的 JSON 输出解析 selected_news_ids / no_clear_news / summary。
    selected_news_ids 必须是 valid_ids 子集，过滤幻觉。"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"reasoner 返回非 JSON: {text[:200]}")
        data = json.loads(match.group(0))

    if not isinstance(data, dict):
        raise ValueError(f"reasoner 返回顶层不是对象: {type(data)}")

    raw_ids = data.get("selected_news_ids") or []
    selected: list[int] = []
    if isinstance(raw_ids, list):
        seen: set[int] = set()
        for item in raw_ids:
            try:
                value = int(item)
            except (TypeError, ValueError):
                continue
            if value in valid_ids and value not in seen:
                selected.append(value)
                seen.add(value)

    no_clear_news = bool(data.get("no_clear_news"))
    summary = (data.get("summary") or "")
    if not isinstance(summary, str):
        summary = str(summary)
    summary = summary.strip()[:240]

    return selected, no_clear_news, summary


def auto_annotate(session: Session, request: AutoAnnotateRequest) -> AutoAnnotateResponse:
    """**只调用模型，不写库**。前端拿到结果后由用户 review，再调 POST /api/annotations 保存。"""
    window_start = parse_datetime(request.window_start_utc)
    window_end = parse_datetime(request.window_end_utc)
    if window_start is None or window_end is None:
        raise ValueError("window_start_utc/window_end_utc 不能为空")

    start_snapshot = _find_window_snapshot(session, request.symbol, window_start)
    end_snapshot = _find_window_snapshot(session, request.symbol, window_end)

    context_start = window_start - timedelta(minutes=CONTEXT_PRE_MINUTES_DEFAULT)
    context_end = window_end + timedelta(minutes=CONTEXT_POST_MINUTES_DEFAULT)
    candidate_news = (
        session.query(NewsItem)
        .filter(
            NewsItem.source.in_(_annotation_news_sources()),
            NewsItem.timestamp >= context_start,
            NewsItem.timestamp <= context_end,
        )
        .order_by(NewsItem.timestamp.asc())
        .all()
    )

    if not candidate_news:
        # 没有候选新闻就直接返回 no_clear_news，省掉一次 API 调用。
        return AutoAnnotateResponse(
            selected_news_ids=[],
            no_clear_news=True,
            summary="窗口前后没有可见的候选新闻，无法归因。",
            reasoning="",
            model=config.DEEPSEEK_REASONER_MODEL,
            duration_seconds=0.0,
            candidate_count=0,
        )

    user_payload = _build_auto_annotate_user_payload(
        request,
        candidate_news,
        start_snapshot.price if start_snapshot else None,
        end_snapshot.price if end_snapshot else None,
        ((end_snapshot.price - start_snapshot.price) / abs(start_snapshot.price) * 100)
        if start_snapshot and end_snapshot and start_snapshot.price else None,
    )

    logger.info(
        f"[AutoAnnotate] 调 DeepSeek {config.DEEPSEEK_REASONER_MODEL}，"
        f"effort={config.DEEPSEEK_REASONER_EFFORT}，候选 {len(candidate_news)} 条新闻"
    )
    content, reasoning, duration = _call_deepseek_reasoner(user_payload)
    valid_ids = {int(row.id) for row in candidate_news}
    selected, no_clear_news, summary = _parse_auto_annotate_response(content, valid_ids)
    logger.info(
        f"[AutoAnnotate] 完成，耗时 {duration:.1f}s，选中 {len(selected)} 条，"
        f"no_clear_news={no_clear_news}"
    )

    return AutoAnnotateResponse(
        selected_news_ids=selected,
        no_clear_news=no_clear_news,
        summary=summary,
        reasoning=reasoning,
        model=config.DEEPSEEK_REASONER_MODEL,
        duration_seconds=round(duration, 2),
        candidate_count=len(candidate_news),
    )


def _build_auto_annotate_batch_user_payload(
    session: Session,
    windows: list[AutoAnnotateRequest],
) -> tuple[str, list[dict], dict[int, list[int]]]:
    """组装批量请求的 user 消息；同时返回每个 window 的 candidate id 列表（落库用）。

    返回 (user_content, window_metas, candidate_ids_by_window_idx)。
    window_metas 与 windows 索引对齐，记录 symbol / 时间 / 价格元数据，用于响应阶段回填。
    """
    payload_windows: list[dict] = []
    window_metas: list[dict] = []
    candidate_ids_by_window: dict[int, list[int]] = {}

    for idx, w in enumerate(windows):
        window_start = parse_datetime(w.window_start_utc)
        window_end = parse_datetime(w.window_end_utc)
        if window_start is None or window_end is None:
            raise ValueError(f"窗口 {idx} 的 window_start_utc / window_end_utc 不能为空")

        start_snapshot = _find_window_snapshot(session, w.symbol, window_start)
        end_snapshot = _find_window_snapshot(session, w.symbol, window_end)

        context_start = window_start - timedelta(minutes=CONTEXT_PRE_MINUTES_DEFAULT)
        context_end = window_end + timedelta(minutes=CONTEXT_POST_MINUTES_DEFAULT)
        candidate_news = (
            session.query(NewsItem)
            .filter(
                NewsItem.source.in_(_annotation_news_sources()),
                NewsItem.timestamp >= context_start,
                NewsItem.timestamp <= context_end,
            )
            .order_by(NewsItem.timestamp.asc())
            .all()
        )
        candidate_ids_by_window[idx] = [int(row.id) for row in candidate_news]

        candidates_payload = [
            {
                "id": row.id,
                "time_bj": (row.timestamp + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") if row.timestamp else None,
                "source": row.source,
                "llm_score": row.llm_importance,
                "jin10_important": bool(row.importance) if row.source == "jin10" else None,
                "title": (row.title or "")[:160],
                "content": (row.content or "")[:300],
            }
            for row in candidate_news
        ]

        change_pct = None
        if start_snapshot and end_snapshot and start_snapshot.price:
            change_pct = (end_snapshot.price - start_snapshot.price) / abs(start_snapshot.price) * 100

        payload_windows.append({
            "id": idx,
            "symbol": w.symbol,
            "start_utc": w.window_start_utc,
            "end_utc": w.window_end_utc,
            "threshold_pct": w.threshold_pct,
            "price_start": start_snapshot.price if start_snapshot else None,
            "price_end": end_snapshot.price if end_snapshot else None,
            "change_pct": change_pct,
            "candidates": candidates_payload,
        })
        window_metas.append({
            "symbol": w.symbol,
            "window_start_utc": w.window_start_utc,
            "window_end_utc": w.window_end_utc,
            "candidate_count": len(candidate_news),
        })

    user_content = f"共 {len(payload_windows)} 个窗口。\n{json.dumps({'windows': payload_windows}, ensure_ascii=False)}"
    return user_content, window_metas, candidate_ids_by_window


def _parse_auto_annotate_batch_response(
    raw: str,
    valid_ids_by_window: dict[int, set[int]],
) -> dict[int, tuple[list[int], bool, str, str]]:
    """从 reasoner 返回的 JSON 解析 items，按 window_id 映射回去；过滤幻觉 id。

    返回 (selected_news_ids, no_clear_news, summary, reasoning) 四元组。
    reasoning 是模型在结构化 JSON 里给出的**该窗口专属**解释，
    与 message.reasoning_content（整批共享的 thinking trace）不同。
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"reasoner 批量返回非 JSON: {text[:200]}")
        data = json.loads(match.group(0))

    if not isinstance(data, dict):
        raise ValueError(f"reasoner 批量返回顶层不是对象: {type(data)}")
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("reasoner 批量返回缺少 items 列表")

    by_window: dict[int, tuple[list[int], bool, str, str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            wid = int(item.get("window_id"))
        except (TypeError, ValueError):
            continue
        valid_ids = valid_ids_by_window.get(wid)
        if valid_ids is None:
            continue
        raw_ids = item.get("selected_news_ids") or []
        selected: list[int] = []
        if isinstance(raw_ids, list):
            seen: set[int] = set()
            for entry in raw_ids:
                try:
                    value = int(entry)
                except (TypeError, ValueError):
                    continue
                if value in valid_ids and value not in seen:
                    selected.append(value)
                    seen.add(value)
        no_clear_news = bool(item.get("no_clear_news"))
        summary = item.get("summary") or ""
        if not isinstance(summary, str):
            summary = str(summary)
        reasoning = item.get("reasoning") or ""
        if not isinstance(reasoning, str):
            reasoning = str(reasoning)
        by_window[wid] = (selected, no_clear_news, summary.strip()[:240], reasoning.strip())

    return by_window


def auto_annotate_batch(session: Session, request: AutoAnnotateBatchRequest) -> AutoAnnotateBatchResponse:
    """**只调模型，不写库**。一次喂多个窗口给 reasoner，复用同一份 system prompt。
    超过 AUTO_ANNOTATE_BATCH_LIMIT 个窗口时报错（前端应分片调用）。"""
    windows = request.windows or []
    if not windows:
        raise ValueError("windows 不能为空")
    if len(windows) > AUTO_ANNOTATE_BATCH_LIMIT:
        raise ValueError(
            f"批量自动标注一次最多 {AUTO_ANNOTATE_BATCH_LIMIT} 个窗口，收到 {len(windows)}；"
            f"请前端分片调用"
        )

    user_content, window_metas, candidate_ids_by_window = _build_auto_annotate_batch_user_payload(session, windows)

    logger.info(
        f"[AutoAnnotateBatch] 调 DeepSeek {config.DEEPSEEK_REASONER_MODEL}，"
        f"effort={config.DEEPSEEK_REASONER_EFFORT}，{len(windows)} 个窗口，"
        f"总候选 {sum(m['candidate_count'] for m in window_metas)} 条"
    )
    content, reasoning, duration = _call_deepseek_reasoner_batch(user_content)

    valid_ids_by_window = {idx: set(ids) for idx, ids in candidate_ids_by_window.items()}
    parsed = _parse_auto_annotate_batch_response(content, valid_ids_by_window)

    results: list[AutoAnnotateBatchItem] = []
    for idx, meta in enumerate(window_metas):
        # 模型可能漏给某个窗口；漏的视为 no_clear_news=true，summary 留空，
        # 让前端 UI 提示用户检查/重新单独跑该窗口。
        selected, no_clear_news, summary, item_reasoning = parsed.get(idx, ([], True, "", ""))
        results.append(AutoAnnotateBatchItem(
            symbol=meta["symbol"],
            window_start_utc=meta["window_start_utc"],
            window_end_utc=meta["window_end_utc"],
            selected_news_ids=selected,
            no_clear_news=no_clear_news,
            summary=summary,
            reasoning=item_reasoning,
            candidate_count=meta["candidate_count"],
            candidate_news_ids=candidate_ids_by_window[idx],
        ))

    logger.info(
        f"[AutoAnnotateBatch] 完成，耗时 {duration:.1f}s，命中 {len(parsed)}/{len(windows)} 个窗口"
    )

    return AutoAnnotateBatchResponse(
        results=results,
        reasoning=reasoning,
        model=config.DEEPSEEK_REASONER_MODEL,
        duration_seconds=round(duration, 2),
        requested_count=len(windows),
        answered_count=len(parsed),
    )


def _call_deepseek_reasoner_batch(user_content: str) -> tuple[str, str, float]:
    """与 _call_deepseek_reasoner 同样的 thinking 模式调用，但用批量 system prompt + 更大 max_tokens。"""
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，无法调用批量自动标注")

    payload = {
        "model": config.DEEPSEEK_REASONER_MODEL,
        "messages": [
            {"role": "system", "content": AUTO_ANNOTATE_BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "thinking": {
            "type": "enabled",
            "reasoning_effort": config.DEEPSEEK_REASONER_EFFORT,
        },
        "response_format": {"type": "json_object"},
        # max_tokens 同时覆盖 reasoning_content + content；批量场景里 thinking 容易吃掉
        "max_tokens": 16000,
    }
    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    started = time.monotonic()
    resp = requests.post(
        DEEPSEEK_API_URL,
        json=payload,
        headers=headers,
        timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, config.DEEPSEEK_REASONER_BATCH_READ_TIMEOUT),
    )
    duration = time.monotonic() - started

    if resp.status_code >= 400:
        preview = resp.text[:300].replace("\n", " ")
        raise RuntimeError(f"DeepSeek 返回 {resp.status_code}: {preview}")

    body = resp.json()
    message = body["choices"][0].get("message", {})
    content = (message.get("content") or "").strip()
    reasoning = (message.get("reasoning_content") or "").strip()
    if not content:
        raise RuntimeError(f"DeepSeek 批量返回空 content（reasoning 预览: {reasoning[:200]}）")
    return content, reasoning, duration
