from __future__ import annotations

from datetime import datetime

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
    AnnotationResponse,
    AnnotationSymbol,
    AutoAnnotateRequest,
    AutoAnnotateResponse,
    ContextNewsResponse,
    DeleteAnnotationResponse,
    PriceRuleSchema,
    PriceWindowSchema,
)
from schemas.common import Page
from schemas.market import MarketHistoryResponse, MarketLatestResponse, MarketSymbol, MarketTableRow
from schemas.news import NewsResponse
from schemas.onchain import OnchainDataset
from schemas.predictions import PredictionFamily, PredictionRow, PredictionsResponse
from schemas.tasks import TaskStatus
from services import alerts_service, annotation_service, market_service, news_service, onchain_service, prediction_service, task_service
from services.time_utils import parse_datetime, timestamp_pair, utc_now_naive

router = APIRouter(prefix="/api")


def _csv_list(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    result: list[str] = []
    for value in values:
        result.extend([part.strip() for part in value.split(",") if part.strip()])
    return result or None


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
        start=parse_datetime(start_utc),
        end=parse_datetime(end_utc),
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


@router.get("/predictions", response_model=PredictionsResponse)
def predictions(hours: int = 24, search: str | None = None, db: Session = Depends(get_db)) -> PredictionsResponse:
    return prediction_service.get_predictions(db, hours=hours, search=search)


@router.get("/predictions/families", response_model=list[PredictionFamily])
def prediction_families(hours: int = 24, search: str | None = None, db: Session = Depends(get_db)) -> list[PredictionFamily]:
    return prediction_service.get_prediction_families(db, hours=hours, search=search)


@router.get("/predictions/{market_id}/history", response_model=list[PredictionRow])
def prediction_history(market_id: str, hours: int = 24, db: Session = Depends(get_db)) -> list[PredictionRow]:
    return prediction_service.get_market_history(db, market_id=market_id, hours=hours)


@router.get("/onchain/eth/top100-netflow", response_model=OnchainDataset)
def onchain_top100(force_refresh: bool = False) -> OnchainDataset:
    return onchain_service.top100_netflow(force_refresh)


@router.get("/onchain/eth/daily-stats", response_model=OnchainDataset)
def onchain_daily(force_refresh: bool = False) -> OnchainDataset:
    return onchain_service.daily_stats(force_refresh)


@router.get("/onchain/eth/cex-flows", response_model=OnchainDataset)
def onchain_cex(force_refresh: bool = False) -> OnchainDataset:
    return onchain_service.cex_flows(force_refresh)


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
    return annotation_service.load_context_news_for_window(
        db, window_start_utc, window_end_utc, pre_minutes, post_minutes
    )


@router.post("/annotations", response_model=AnnotationResponse)
def annotations(request: AnnotationCreateRequest, db: Session = Depends(get_db)) -> AnnotationResponse:
    try:
        return annotation_service.upsert_annotation(db, request)
    except ValueError as exc:
        raise ApiError("ANNOTATION_INVALID", str(exc), status_code=400) from exc


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
