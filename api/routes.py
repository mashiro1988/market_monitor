from __future__ import annotations

from datetime import datetime

import config
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.deps import get_db
from api.errors import ApiError
from models.alert_log import AlertLog
from models.news import NewsItem
from models.prediction import PredictionMarket
from models.price import PriceSnapshot
from schemas.alerts import AlertLogSchema, AlertRuleSchema, AlertTestResponse, AlertWebhookStatus
from schemas.annotations import (
    AnnotationCreateRequest,
    AnnotationDetail,
    AnnotationListItem,
    AnnotationResponse,
    AnnotationSymbol,
    AutoAnnotateBatchRequest,
    AutoAnnotateBatchResponse,
    AutoAnnotateRefineRequest,
    AutoAnnotateRequest,
    AutoAnnotateResponse,
    ContextNewsResponse,
    DeleteAnnotationResponse,
    PriceRuleSchema,
    PriceWindowSchema,
)
from schemas.behavior import BehaviorDailyResponse, BehaviorLinkageResponse, BehaviorReviewRequest, BehaviorSegmentsResponse
from schemas.common import Page
from schemas.market import MarketHistoryResponse, MarketLatestResponse, MarketSymbol, MarketTableRow
from schemas.news import NewsItemSchema, NewsResponse, NewsSourceMeta
from schemas.predictions import (
    EventMarketItem,
    EventMarketsResponse,
    MarketSearchResult,
    PredictionFamily,
    PredictionRow,
    PredictionsResponse,
    TrackedMarketCreate,
    TrackedMarketSchema,
    TrackedMarketUpdate,
)
from schemas.research import (
    BackscanRequest,
    BackscanResponse,
    DeleteEventResponse,
    LinkCreateRequest,
    LinkPatchRequest,
    LinkResponse,
    NewsLinksResponse,
    BufferResponse,
    ResearchEventCreateRequest,
    ResearchEventItem,
    ResearchEventPatchRequest,
    ResearchEventsResponse,
    ResearchStats,
    RevivalResponse,
    SuggestKeywordsRequest,
    SuggestKeywordsResponse,
    SweepApplyRequest,
    SweepApplyResponse,
    SweepRequest,
    SweepResponse,
    AttachMarketRequest,
    DetachMarketRequest,
    MarketSweepApplyRequest,
    MarketSweepApplyResponse,
    MarketSweepRequest,
    MarketSweepResponse,
    TimelineResponse,
)
from schemas.sectors import SectorLeaderboardResponse, SectorTokensResponse
from schemas.tasks import TaskStatus
from services import alerts_service, annotation_service, behavior_views, event_linking, event_markets, event_pool, market_service, market_sweep, news_service, pool_sweep, prediction_service, sector_service, task_service
from services.time_utils import parse_datetime, timestamp_pair, utc_now_naive

router = APIRouter(prefix="/api")


def _csv_list(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    result: list[str] = []
    for value in values:
        result.extend([part.strip() for part in value.split(",") if part.strip()])
    return result or None


def _parse_query_datetime(value: str | None) -> datetime | None:
    parsed = parse_datetime(value)
    if value is not None and value.strip() and parsed is None:
        raise ApiError("INVALID_DATETIME", "Invalid datetime query parameter", status_code=400)
    return parsed


@router.get("/health")
def health() -> dict:
    return {"ok": True, "timestamp": timestamp_pair(utc_now_naive())}


@router.get("/status")
def status(db: Session = Depends(get_db)) -> dict:
    return {
        "database": {
            "prices": db.query(func.count(PriceSnapshot.id)).scalar(),
            "news": db.query(func.count(NewsItem.id)).scalar(),
            "predictions": db.query(func.count(PredictionMarket.id)).scalar(),
            "alert_logs": db.query(func.count(AlertLog.id)).scalar(),
        },
        "market": market_service.status_snapshot(db),
        "tasks": task_service.all_tasks()[:10],
    }


@router.post("/tasks/scan", response_model=TaskStatus)
def scan_task() -> TaskStatus:
    return task_service.create_scan_task()


@router.get("/tasks/{task_id}", response_model=TaskStatus)
def task_status(task_id: str) -> TaskStatus:
    task = task_service.get_task(task_id)
    if task is None:
        raise ApiError("TASK_NOT_FOUND", "任务不存在", status_code=404, details={"task_id": task_id})
    return task


@router.get("/market/latest", response_model=MarketLatestResponse)
def market_latest(db: Session = Depends(get_db)) -> MarketLatestResponse:
    return market_service.get_latest_prices(db)


@router.get("/market/symbols", response_model=list[MarketSymbol])
def market_symbols(days: int = 10, db: Session = Depends(get_db)) -> list[MarketSymbol]:
    return market_service.get_symbols(db, days=days)


@router.get("/market/history", response_model=MarketHistoryResponse)
def market_history(
    symbols: list[str] | None = Query(default=None),
    hours: int = 24,
    start_utc: str | None = None,
    end_utc: str | None = None,
    db: Session = Depends(get_db),
) -> MarketHistoryResponse:
    return market_service.get_history(
        db,
        symbols=_csv_list(symbols),
        hours=hours,
        start=_parse_query_datetime(start_utc),
        end=_parse_query_datetime(end_utc),
    )


@router.get("/market/table", response_model=Page[MarketTableRow])
def market_table(
    hours: int = 24,
    asset_classes: list[str] | None = Query(default=None),
    symbols: list[str] | None = Query(default=None),
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
) -> Page[MarketTableRow]:
    return market_service.get_table(db, hours, _csv_list(asset_classes), _csv_list(symbols), page, page_size)


@router.get("/market/table.csv")
def market_table_csv(
    hours: int = 24,
    asset_classes: list[str] | None = Query(default=None),
    symbols: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
) -> Response:
    data = market_service.get_table_csv(db, hours, _csv_list(asset_classes), _csv_list(symbols))
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="price_snapshots_{hours}h.csv"'},
    )


@router.get("/news", response_model=NewsResponse)
def news(
    sources: list[str] | None = Query(default=None),
    min_llm_importance: int = 5,          # 0 = 不限(含未评分)
    hours_back: int = 24,
    jin10_importance: str = "all",
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
    buffer_only: bool = False,            # 仅看未挂事件(= 事件池缓冲区口径)
    db: Session = Depends(get_db),
) -> NewsResponse:
    return news_service.get_news(
        db,
        sources=_csv_list(sources),
        min_llm_importance=min_llm_importance,
        buffer_only=buffer_only,
        hours_back=hours_back,
        jin10_importance=jin10_importance,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.get("/news/sources", response_model=list[NewsSourceMeta])
def news_sources_list() -> list[NewsSourceMeta]:
    return news_service.list_sources()


@router.get("/crypto/news", response_model=NewsResponse)
def crypto_news(
    sources: list[str] | None = Query(default=None),
    hours_back: int = 24,
    min_llm_importance: int = 0,          # 0 = 不限：加密线不按分数拦（design §3）
    affair_only: bool = False,            # 只看币圈事务（滤掉加密源转载的纯宏观）
    coin: str | None = None,
    search: str | None = None,
    unlinked_only: bool = False,          # 只看未挂事件（与宏观页同口径、同判定函数）
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
) -> NewsResponse:
    return news_service.get_crypto_news(
        db, sources=_csv_list(sources), hours_back=hours_back,
        min_llm_importance=min_llm_importance, affair_only=affair_only,
        coin=coin, search=search, unlinked_only=unlinked_only,
        page=page, page_size=page_size)


@router.get("/crypto/news/sources", response_model=list[NewsSourceMeta])
def crypto_news_sources_list() -> list[NewsSourceMeta]:
    return news_service.list_crypto_sources()


@router.get("/predictions", response_model=PredictionsResponse)
def predictions(hours: int = 24, search: str | None = None, market: str | None = None,
                db: Session = Depends(get_db)) -> PredictionsResponse:
    return prediction_service.get_predictions(db, hours=hours, search=search, market=market)


@router.get("/predictions/families", response_model=list[PredictionFamily])
def prediction_families(hours: int = 24, search: str | None = None, market: str | None = None,
                        db: Session = Depends(get_db)) -> list[PredictionFamily]:
    return prediction_service.get_prediction_families(db, hours=hours, search=search, market=market)


@router.get("/predictions/search", response_model=list[MarketSearchResult])
def predictions_search(q: str, db: Session = Depends(get_db)) -> list[MarketSearchResult]:
    """手动搜索通道(spec 2026-08-28 §3):Gamma 搜索代理,不剔价格类。"""
    try:
        return [MarketSearchResult(**c) for c in market_sweep.search_markets(q)]
    except Exception as exc:
        raise ApiError("SEARCH_FAILED", f"Polymarket 搜索失败: {exc}", status_code=502) from exc


@router.get("/predictions/tracked", response_model=list[TrackedMarketSchema])
def list_tracked(market: str | None = None, db: Session = Depends(get_db)) -> list[TrackedMarketSchema]:
    return prediction_service.list_tracked_markets(db, market=market)


@router.post("/predictions/tracked", response_model=TrackedMarketSchema)
def create_tracked(payload: TrackedMarketCreate, db: Session = Depends(get_db)) -> TrackedMarketSchema:
    try:
        return prediction_service.create_tracked_market(db, payload)
    except ValueError as e:
        if str(e) == "duplicate":
            raise ApiError(code="DUPLICATE", message="已存在相同的 kind+identifier", status_code=409)
        raise ApiError(code="INVALID", message=str(e), status_code=400)


@router.patch("/predictions/tracked/{tracked_id}", response_model=TrackedMarketSchema)
def update_tracked(tracked_id: int, payload: TrackedMarketUpdate, db: Session = Depends(get_db)) -> TrackedMarketSchema:
    result = prediction_service.update_tracked_market(db, tracked_id, payload)
    if result is None:
        raise ApiError(code="NOT_FOUND", message="未找到", status_code=404)
    return result


@router.delete("/predictions/tracked/{tracked_id}")
def delete_tracked(tracked_id: int, db: Session = Depends(get_db)) -> dict:
    ok = prediction_service.delete_tracked_market(db, tracked_id)
    if not ok:
        raise ApiError(code="NOT_FOUND", message="未找到", status_code=404)
    return {"ok": True}


@router.get("/predictions/{market_id}/history", response_model=list[PredictionRow])
def prediction_history(market_id: str, hours: int = 24, db: Session = Depends(get_db)) -> list[PredictionRow]:
    return prediction_service.get_market_history(db, market_id=market_id, hours=hours)


@router.get("/alerts/rules", response_model=list[AlertRuleSchema])
def alert_rules() -> list[AlertRuleSchema]:
    return alerts_service.get_rules()


@router.get("/alerts/webhook-status", response_model=AlertWebhookStatus)
def alert_webhook_status() -> AlertWebhookStatus:
    return alerts_service.get_webhook_status()


@router.get("/alerts/logs", response_model=Page[AlertLogSchema])
def alert_logs(hours_back: int = 24, page: int = 1, page_size: int = 50, db: Session = Depends(get_db)) -> Page[AlertLogSchema]:
    return alerts_service.get_logs(db, hours_back, page, page_size)


@router.post("/alerts/test-wechat", response_model=AlertTestResponse)
def test_wechat() -> AlertTestResponse:
    return alerts_service.test_wechat()


@router.get("/annotations/price-rules", response_model=list[PriceRuleSchema])
def annotation_price_rules() -> list[PriceRuleSchema]:
    return annotation_service.load_alert_price_rules()


@router.get("/annotations/symbols", response_model=list[AnnotationSymbol])
def annotation_symbols(hours: int = 72, db: Session = Depends(get_db)) -> list[AnnotationSymbol]:
    return annotation_service.load_symbols(db, hours)


@router.get("/annotations/windows", response_model=list[PriceWindowSchema])
def annotation_windows(
    symbol: str,
    hours: int = 72,
    threshold_pct: float | None = None,
    window_minutes: int | None = None,
    db: Session = Depends(get_db),
) -> list[PriceWindowSchema]:
    return annotation_service.load_price_windows(db, symbol, hours, threshold_pct, window_minutes)


@router.get("/annotations/context-news", response_model=ContextNewsResponse)
def annotation_context_news(
    window_start_utc: str,
    window_end_utc: str,
    pre_minutes: int = 15,
    post_minutes: int = 30,
    db: Session = Depends(get_db),
) -> ContextNewsResponse:
    try:
        return annotation_service.load_context_news_for_window(
            db, window_start_utc, window_end_utc, pre_minutes, post_minutes
        )
    except ValueError as exc:
        raise ApiError("INVALID_DATETIME", str(exc), status_code=400) from exc


# ============================================================
# 价格行为引擎（docs/specs/price-behavior-engine-plan.md Task 6）
# ============================================================

@router.get("/behavior/segments", response_model=BehaviorSegmentsResponse)
def behavior_segments(symbol: str = "BTC/USDT", days: int = Query(2, ge=1, le=30),
                      db: Session = Depends(get_db)) -> BehaviorSegmentsResponse:
    """段明细（含 S 证据/ESS/新闻命中/分类）。0.3 档段 classification=count_only。"""
    return behavior_views.list_segments(db, symbol, days)


@router.get("/behavior/daily", response_model=BehaviorDailyResponse)
def behavior_daily(symbol: str = "BTC/USDT", days: int = Query(14, ge=1, le=90),
                   db: Session = Depends(get_db)) -> BehaviorDailyResponse:
    """日汇总序列：N 个北京日一律 compute-on-read（人工优先，live 恒 true），不读 PIT 快照。"""
    return behavior_views.daily_series(db, symbol, days)


def _parse_utc_naive(value: str | None):
    """ISO 字符串 → naive UTC datetime（DB 时间语义）；带时区先折算，非法返回 None。"""
    if not value:
        return None
    from datetime import datetime as _dt, timezone as _tz
    try:
        dt = _dt.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(_tz.utc).replace(tzinfo=None) if dt.tzinfo else dt


@router.get("/behavior/linkage", response_model=BehaviorLinkageResponse)
def behavior_linkage(symbol: str = "BTC/USDT", hours: int = Query(48, ge=6, le=168),
                     start_utc: str | None = Query(None), end_utc: str | None = Query(None),
                     db: Session = Depends(get_db)) -> BehaviorLinkageResponse:
    """rolling S 联动曲线（逐参照）+ 同步参照数。纯展示层，compute-on-read。
    start_utc/end_utc（ISO，标注页跟随窗口 ±24h）给定时按区间计算，end 超出数据贴最新点。"""
    return behavior_views.linkage(db, symbol, hours,
                                  start=_parse_utc_naive(start_utc), end=_parse_utc_naive(end_utc))


@router.patch("/behavior/segments/{segment_id}", response_model=dict)
def behavior_review(segment_id: int, request: BehaviorReviewRequest,
                    db: Session = Depends(get_db)) -> dict:
    """人工审计段分类：human_class=类别（确认/改判），null=撤销。机器分类保留作对照，构成聚合优先人工。"""
    try:
        row = behavior_views.review_segment(db, segment_id, request.human_class)
    except ValueError as exc:
        raise ApiError("INVALID_CLASS", str(exc), status_code=400) from exc
    if row is None:
        raise ApiError("NOT_FOUND", f"段不存在: {segment_id}", status_code=404)
    return {"id": row.id, "human_class": row.human_class,
            "human_confirmed_at": row.human_confirmed_at.isoformat() if row.human_confirmed_at else None}


# （2026-08-08 切换）GET /annotations/tag-options 与 PATCH /news/{id}/tags 退役：
# 标注页内容标签下拉删除，语义归类走事件池（research_events），方向只读展示不再人工改。


@router.post("/annotations", response_model=AnnotationResponse)
def annotations(request: AnnotationCreateRequest, db: Session = Depends(get_db)) -> AnnotationResponse:
    try:
        return annotation_service.upsert_annotation(db, request)
    except ValueError as exc:
        raise ApiError("ANNOTATION_INVALID", str(exc), status_code=400) from exc


@router.get("/annotations/export")
def annotations_export(days: int = 365, split: str = "train", db: Session = Depends(get_db)) -> Response:
    """标注训练集 JSONL 导出（docs/specs/annotation-v2.md §4）。split=train（默认，排除评估集）/eval/all。"""
    try:
        lines = annotation_service.export_training_jsonl(db, days=days, split=split)
    except ValueError as exc:
        raise ApiError("ANNOTATION_INVALID", str(exc), status_code=400) from exc
    body = "\n".join(lines) + ("\n" if lines else "")
    return Response(
        content=body.encode("utf-8"),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f"attachment; filename=annotations_{split}.jsonl"},
    )


@router.post("/annotations/{annotation_id}/eval-set", response_model=AnnotationResponse)
def annotation_eval_set(annotation_id: int, value: bool = True, db: Session = Depends(get_db)) -> AnnotationResponse:
    """把标注冻结进/移出评估集（训练导出默认排除评估集行）。"""
    try:
        return AnnotationResponse(id=annotation_service.set_eval_set(db, annotation_id, value))
    except ValueError as exc:
        raise ApiError("ANNOTATION_NOT_FOUND", str(exc), status_code=404) from exc


@router.get("/annotations", response_model=Page[AnnotationListItem])
def annotation_list(
    symbol: str | None = None,
    hours: int = 72,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
) -> Page[AnnotationListItem]:
    """已标注分页列表；hours<=0 = 全量回溯。"""
    return annotation_service.list_annotations(db, symbol, hours, page, page_size)


@router.get("/annotations/{annotation_id}", response_model=AnnotationDetail)
def annotation_detail(annotation_id: int, db: Session = Depends(get_db)) -> AnnotationDetail:
    try:
        return annotation_service.get_annotation_detail(db, annotation_id)
    except ValueError as exc:
        raise ApiError("ANNOTATION_NOT_FOUND", str(exc), status_code=404) from exc


@router.delete("/annotations/{annotation_id}", response_model=DeleteAnnotationResponse)
def delete_annotation(annotation_id: int, db: Session = Depends(get_db)) -> DeleteAnnotationResponse:
    try:
        deleted_id = annotation_service.delete_annotation(db, annotation_id)
        return DeleteAnnotationResponse(id=deleted_id, deleted=True)
    except ValueError as exc:
        raise ApiError("ANNOTATION_NOT_FOUND", str(exc), status_code=404) from exc


@router.post("/annotations/auto", response_model=AutoAnnotateResponse)
def annotation_auto(request: AutoAnnotateRequest, db: Session = Depends(get_db)) -> AutoAnnotateResponse:
    try:
        return annotation_service.auto_annotate(db, request)
    except ValueError as exc:
        raise ApiError("ANNOTATION_INVALID", str(exc), status_code=400) from exc
    except RuntimeError as exc:
        raise ApiError("AUTO_ANNOTATE_FAILED", str(exc), status_code=502) from exc


@router.post("/annotations/auto/refine", response_model=AutoAnnotateResponse)
def annotation_auto_refine(request: AutoAnnotateRefineRequest, db: Session = Depends(get_db)) -> AutoAnnotateResponse:
    """互动重标：带上一轮输出 + 用户纠正，多轮对话再调 reasoner。不写库。"""
    try:
        return annotation_service.auto_annotate_refine(db, request)
    except ValueError as exc:
        raise ApiError("ANNOTATION_INVALID", str(exc), status_code=400) from exc
    except RuntimeError as exc:
        raise ApiError("AUTO_ANNOTATE_FAILED", str(exc), status_code=502) from exc


@router.post("/annotations/auto-batch", response_model=AutoAnnotateBatchResponse)
def annotation_auto_batch(request: AutoAnnotateBatchRequest, db: Session = Depends(get_db)) -> AutoAnnotateBatchResponse:
    try:
        return annotation_service.auto_annotate_batch(db, request)
    except ValueError as exc:
        raise ApiError("ANNOTATION_INVALID", str(exc), status_code=400) from exc
    except RuntimeError as exc:
        raise ApiError("AUTO_ANNOTATE_FAILED", str(exc), status_code=502) from exc


# ============================================================
# 板块轮动（Phase 1）
# ============================================================
@router.get("/sectors/leaderboard", response_model=SectorLeaderboardResponse)
def sectors_leaderboard(db: Session = Depends(get_db)) -> SectorLeaderboardResponse:
    """最新一次 sector_scan 的所有板块聚合，按 24h 涨跌降序。"""
    return sector_service.get_leaderboard(db)


@router.get("/sectors/{category}/tokens", response_model=SectorTokensResponse)
def sectors_tokens(category: str, db: Session = Depends(get_db)) -> SectorTokensResponse:
    """某板块下所有 symbol 的当前涨跌（从本地 pivot 缓存现算）。"""
    return sector_service.get_sector_tokens(db, category)


# ============================================================
# 研究事件池(docs/specs/news-research-phase1-event-pool.md §9.3)
# ============================================================

def _event_item(db: Session, event_id: int) -> ResearchEventItem:
    row = next((r for r in event_pool.list_events(db) if r["id"] == event_id), None)
    if row is None:
        raise ApiError("NOT_FOUND", f"事件 #{event_id} 不存在", status_code=404)
    ts = row.pop("last_evidence_at")
    row["last_evidence_at"] = ts.isoformat(timespec="seconds") if ts else None
    return ResearchEventItem(**row)


@router.get("/research/events", response_model=ResearchEventsResponse)
def research_events_list(status: str | None = Query(default=None),
                         q: str | None = Query(default=None),
                         event_type: str | None = Query(default=None),
                         db: Session = Depends(get_db)) -> ResearchEventsResponse:
    rows = event_pool.list_events(db, status=status, q=q, event_type=event_type)
    for r in rows:
        ts = r.pop("last_evidence_at")
        r["last_evidence_at"] = ts.isoformat(timespec="seconds") if ts else None
    return ResearchEventsResponse(items=[ResearchEventItem(**r) for r in rows])


@router.post("/research/events", response_model=ResearchEventItem)
def research_event_create(request: ResearchEventCreateRequest,
                          db: Session = Depends(get_db)) -> ResearchEventItem:
    try:
        e = event_pool.create_event(db, request.name, request.news_ids,
                                    gate_keywords=request.gate_keywords,
                                    created_from=request.created_from,
                                    event_type=request.event_type)
    except ValueError as exc:
        raise ApiError("INVALID_EVENT", str(exc), status_code=400) from exc
    return _event_item(db, e.id)


@router.patch("/research/events/{event_id}", response_model=ResearchEventItem)
def research_event_patch(event_id: int, request: ResearchEventPatchRequest,
                         db: Session = Depends(get_db)) -> ResearchEventItem:
    try:
        if request.name is not None:
            event_pool.rename_event(db, event_id, request.name)
        if request.gate_keywords is not None:
            event_pool.set_keywords(db, event_id, request.gate_keywords,
                                    backscan=request.keywords_backscan)
        if request.merge_into_id is not None:
            event_pool.merge_event(db, source_id=event_id, target_id=request.merge_into_id)
        elif request.status == "closed":
            event_pool.close_event(db, event_id, reason=request.closed_reason)
        elif request.status == "active":
            event_pool.reopen_event(db, event_id)
    except ValueError as exc:
        raise ApiError("INVALID_EVENT_OP", str(exc), status_code=400) from exc
    return _event_item(db, event_id)


@router.delete("/research/events/{event_id}", response_model=DeleteEventResponse)
def research_event_delete(event_id: int, db: Session = Depends(get_db)) -> DeleteEventResponse:
    """软删除(2026-08-13):UI 全消失、账上留痕;证据摘下退回缓冲区,纠错率照审。"""
    try:
        freed = event_pool.delete_event(db, event_id)
    except ValueError as exc:
        raise ApiError("NOT_FOUND", str(exc), status_code=404) from exc
    return DeleteEventResponse(id=event_id, deleted=True, links_freed=freed)


@router.post("/research/events/suggest-keywords", response_model=SuggestKeywordsResponse)
def research_suggest_keywords(request: SuggestKeywordsRequest,
                              db: Session = Depends(get_db)) -> SuggestKeywordsResponse:
    try:
        kws = event_linking.suggest_keywords(db, request.name, request.news_ids)
    except (ValueError, RuntimeError) as exc:
        raise ApiError("SUGGEST_FAILED", str(exc), status_code=400) from exc
    return SuggestKeywordsResponse(keywords=kws)


@router.post("/research/events/{event_id}/backscan", response_model=BackscanResponse)
def research_event_backscan(event_id: int, request: BackscanRequest,
                            db: Session = Depends(get_db)) -> BackscanResponse:
    # event_id 仅作语义定位(回扫是全池行为,spec §6.3);校验事件存在即可
    try:
        event_pool._get_event(db, event_id)
    except ValueError as exc:
        raise ApiError("NOT_FOUND", str(exc), status_code=404) from exc
    cleared = event_linking.clear_link_cursor(db, hours=request.days * 24)
    return BackscanResponse(cleared=cleared)


@router.get("/research/events/{event_id}/timeline", response_model=TimelineResponse)
def research_event_timeline(event_id: int,
                            days: int | None = Query(default=None, ge=1, le=365),
                            min_score: int | None = Query(default=None, ge=1, le=10),
                            min_abs_move: float | None = Query(default=None, ge=0),
                            page: int = Query(default=1, ge=1),
                            page_size: int = Query(default=50, ge=1, le=200),
                            db: Session = Depends(get_db)) -> TimelineResponse:
    # 分页默认值只钉在这一层:服务层不传即全量,replay 脚本(spec §14)要完整时间轴
    try:
        data = event_pool.event_timeline(db, event_id, days=days, min_score=min_score,
                                         min_abs_move=min_abs_move,
                                         page=page, page_size=page_size)
    except ValueError as exc:
        raise ApiError("NOT_FOUND", str(exc), status_code=404) from exc
    return TimelineResponse(**data)


@router.post("/research/links", response_model=LinkResponse)
def research_link_create(request: LinkCreateRequest, db: Session = Depends(get_db)) -> LinkResponse:
    try:
        link = event_pool.attach_news(db, request.event_id, request.news_id)
    except ValueError as exc:
        raise ApiError("INVALID_LINK", str(exc), status_code=400) from exc
    return LinkResponse(id=link.id, event_id=link.event_id, news_id=link.news_id,
                        link_source=link.link_source, detached=link.detached)


@router.patch("/research/links/{link_id}", response_model=LinkResponse)
def research_link_patch(link_id: int, request: LinkPatchRequest,
                        db: Session = Depends(get_db)) -> LinkResponse:
    try:
        if request.event_id is not None:
            link = event_pool.reassign_link(db, link_id, request.event_id)
        elif request.detached:
            link = event_pool.detach_link(db, link_id, request.detach_reason)
        else:
            raise ValueError("PATCH 必须传 event_id(改归属)或 detached=true(摘下)")
    except ValueError as exc:
        raise ApiError("INVALID_LINK_OP", str(exc), status_code=400) from exc
    return LinkResponse(id=link.id, event_id=link.event_id, news_id=link.news_id,
                        link_source=link.link_source, detached=link.detached)


@router.get("/research/news/{news_id}/links", response_model=NewsLinksResponse)
def research_news_links(news_id: int, db: Session = Depends(get_db)) -> NewsLinksResponse:
    return NewsLinksResponse(items=event_pool.news_links(db, news_id))


@router.get("/research/buffer", response_model=BufferResponse)
def research_buffer(days: int = Query(default=3, ge=1, le=30),
                    min_score: int | None = Query(default=None),
                    q: str | None = Query(default=None),
                    drivers_only: bool = Query(default=False),
                    db: Session = Depends(get_db)) -> BufferResponse:
    return BufferResponse(items=event_pool.buffer_news(
        db, days=days, min_score=min_score, q=q, drivers_only=drivers_only))


@router.get("/research/revival", response_model=RevivalResponse)
def research_revival(days: int = Query(default=7, ge=1, le=30),
                     event_type: str | None = Query(default=None),
                     db: Session = Depends(get_db)) -> RevivalResponse:
    return RevivalResponse(items=event_pool.revival_matches(db, days=days,
                                                            event_type=event_type))


@router.get("/research/stats", response_model=ResearchStats)
def research_stats(event_type: str | None = Query(default=None),
                   db: Session = Depends(get_db)) -> ResearchStats:
    # event_type 传了各线各算(两个池子页各看各的挂接率);不传 = 旧混算口径
    return ResearchStats(**event_pool.daily_stats(db, market=event_type))


@router.post("/research/sweep", response_model=SweepResponse)
def research_sweep(request: SweepRequest, db: Session = Depends(get_db)) -> SweepResponse:
    """AI 梳理(2026-08-13 design,2026-08-15 改提案制):同步长调用(思考模型,1-5 分钟),
    新事件只出提案等人采纳,补挂自动落。Nginx proxy_read_timeout 600s 已覆盖此时长。"""
    try:
        return SweepResponse(**pool_sweep.run_sweep(
            db, event_type=request.event_type, dry_run=request.dry_run))
    except pool_sweep.SweepBusy as exc:
        raise ApiError("SWEEP_BUSY", str(exc), status_code=409) from exc
    except ValueError as exc:
        raise ApiError("SWEEP_INVALID", str(exc), status_code=400) from exc
    except RuntimeError as exc:        # DeepSeek 故障/坏输出:上游问题,不是请求错误
        raise ApiError("SWEEP_FAILED", str(exc), status_code=502) from exc


@router.post("/research/sweep/apply", response_model=SweepApplyResponse)
def research_sweep_apply(request: SweepApplyRequest,
                         db: Session = Depends(get_db)) -> SweepApplyResponse:
    """采纳勾选的梳理提案(签字环节,2026-08-15):只有这里会真正立案。"""
    try:
        return SweepApplyResponse(**pool_sweep.apply_proposals(
            db, event_type=request.event_type,
            events=[e.model_dump() for e in request.events]))
    except ValueError as exc:
        raise ApiError("SWEEP_APPLY_INVALID", str(exc), status_code=400) from exc


@router.post("/research/market-sweep", response_model=MarketSweepResponse)
def research_market_sweep(request: MarketSweepRequest, db: Session = Depends(get_db)) -> MarketSweepResponse:
    """找市场提案(spec 2026-08-28 §3):AI 搜索词+Gamma+配对,提案不落库等人勾选。"""
    try:
        return MarketSweepResponse(**market_sweep.run_market_sweep(
            db, event_type=request.event_type, event_id=request.event_id))
    except market_sweep.MarketSweepBusy as exc:
        raise ApiError("MARKET_SWEEP_BUSY", str(exc), status_code=409) from exc
    except ValueError as exc:
        raise ApiError("MARKET_SWEEP_INVALID", str(exc), status_code=400) from exc
    except RuntimeError as exc:
        raise ApiError("MARKET_SWEEP_FAILED", str(exc), status_code=502) from exc


@router.post("/research/market-sweep/apply", response_model=MarketSweepApplyResponse)
def research_market_sweep_apply(request: MarketSweepApplyRequest,
                                db: Session = Depends(get_db)) -> MarketSweepApplyResponse:
    """采纳勾选的市场提案(签字环节):只有这里会写跟踪清单与挂接。"""
    try:
        return MarketSweepApplyResponse(**market_sweep.apply_market_proposals(
            db, request.event_type, [i.model_dump() for i in request.items]))
    except ValueError as exc:
        raise ApiError("MARKET_SWEEP_APPLY_INVALID", str(exc), status_code=400) from exc


@router.get("/research/events/{event_id}/markets", response_model=EventMarketsResponse)
def research_event_markets(event_id: int, db: Session = Depends(get_db)) -> EventMarketsResponse:
    """事件详情市场卡(spec §5):关联跟踪项+旗下市场最新概率摘要+断流判定。"""
    return EventMarketsResponse(items=[EventMarketItem(**it)
                                       for it in event_markets.list_event_markets(db, event_id)])


@router.post("/research/events/{event_id}/markets")
def research_event_market_attach(event_id: int, payload: AttachMarketRequest,
                                 db: Session = Depends(get_db)) -> dict:
    """人工挂接跟踪项到事件(跟踪管理表归属操作;可撤销摘下)。"""
    try:
        link = event_markets.attach_market(db, event_id, payload.tracked_id)
    except ValueError as exc:
        raise ApiError("MARKET_ATTACH_INVALID", str(exc), status_code=400) from exc
    return {"ok": True, "link_id": int(link.id)}


@router.post("/research/event-markets/{link_id}/detach")
def research_event_market_detach(link_id: int, payload: DetachMarketRequest,
                                 db: Session = Depends(get_db)) -> dict:
    """摘下留痕:事件断开、跟踪照旧、行不删。"""
    if not event_markets.detach_market(db, link_id, payload.reason):
        raise ApiError("MARKET_LINK_NOT_FOUND", "挂接不存在", status_code=404)
    return {"ok": True}
