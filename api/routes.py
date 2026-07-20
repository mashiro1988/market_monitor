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
from schemas.news import NewsItemSchema, NewsResponse, NewsSourceMeta, NewsTagUpdateRequest
from schemas.predictions import (
    PredictionFamily,
    PredictionRow,
    PredictionsResponse,
    TrackedMarketCreate,
    TrackedMarketSchema,
    TrackedMarketUpdate,
)
from schemas.sectors import SectorLeaderboardResponse, SectorTokensResponse
from schemas.tasks import TaskStatus
from services import alerts_service, annotation_service, behavior_views, market_service, news_service, news_tagging, prediction_service, sector_service, task_service
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
    min_llm_importance: int = 5,
    hours_back: int = 24,
    jin10_importance: str = "all",
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
) -> NewsResponse:
    return news_service.get_news(
        db,
        sources=_csv_list(sources),
        min_llm_importance=min_llm_importance,
        hours_back=hours_back,
        jin10_importance=jin10_importance,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.get("/news/sources", response_model=list[NewsSourceMeta])
def news_sources_list() -> list[NewsSourceMeta]:
    return news_service.list_sources()


@router.get("/predictions", response_model=PredictionsResponse)
def predictions(hours: int = 24, search: str | None = None, db: Session = Depends(get_db)) -> PredictionsResponse:
    return prediction_service.get_predictions(db, hours=hours, search=search)


@router.get("/predictions/families", response_model=list[PredictionFamily])
def prediction_families(hours: int = 24, search: str | None = None, db: Session = Depends(get_db)) -> list[PredictionFamily]:
    return prediction_service.get_prediction_families(db, hours=hours, search=search)


@router.get("/predictions/tracked", response_model=list[TrackedMarketSchema])
def list_tracked(db: Session = Depends(get_db)) -> list[TrackedMarketSchema]:
    return prediction_service.list_tracked_markets(db)


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
    """日汇总序列：每日最新 PIT 行；当日盘中/缺口按同口径现算（live=true）。"""
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


@router.get("/annotations/tag-options")
def annotation_tag_options() -> dict[str, list[str]]:
    """内容标签三张「库」（标注页人工改标签的下拉用）：topic / 量级 / 方向。"""
    return {
        "topics": list(config.NEWS_TOPICS),
        "magnitudes": list(config.NEWS_MAGNITUDE_TIERS),
        "directions": list(config.NEWS_DIRECTIONS),
    }


@router.patch("/news/{news_id}/tags", response_model=NewsItemSchema)
def news_tags_update(news_id: int, request: NewsTagUpdateRequest, db: Session = Depends(get_db)) -> NewsItemSchema:
    """人工修正一条新闻的内容标签（topic/量级/方向），校验枚举后落库（置 tagged_at，不再被自动重打）。"""
    try:
        item = news_tagging.update_news_tags(db, news_id, **request.model_dump(exclude_unset=True))
        return news_service.to_news_schema(item)
    except ValueError as exc:
        raise ApiError("NEWS_TAG_INVALID", str(exc), status_code=400) from exc


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
