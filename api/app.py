from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

import config
from api.errors import (
    ApiError,
    api_error_handler,
    http_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from api.routes import router
from database import create_tables
from run import next_aligned_run_time, run_scan_once, run_startup_backfill_once

ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"


def _start_background_scheduler() -> BackgroundScheduler:
    create_tables()
    scheduler = BackgroundScheduler(timezone="UTC")

    def scan_cycle() -> None:
        try:
            logger.info("[FastAPI Scheduler] scan cycle started")
            price_records, news_records, prediction_records = run_scan_once()
            logger.info(
                "[FastAPI Scheduler] scan cycle finished: price={} news={} prediction={}",
                len(price_records),
                len(news_records),
                len(prediction_records),
            )
        except Exception as exc:
            logger.exception("[FastAPI Scheduler] scan cycle failed: {}", exc)

    def hourly_summary() -> None:
        try:
            from alerts.engine import AlertEngine

            AlertEngine().send_hourly_summary()
        except Exception as exc:
            logger.exception("[FastAPI Scheduler] hourly summary failed: {}", exc)

    def startup_backfill() -> None:
        try:
            price_records, news_records = run_startup_backfill_once()
            logger.info(
                "[FastAPI Scheduler] startup backfill finished: price={} news={}",
                len(price_records),
                len(news_records),
            )
        except Exception as exc:
            logger.exception("[FastAPI Scheduler] startup backfill failed: {}", exc)

    price_interval = max(1, int(config.SCAN_INTERVALS.get("price", 5)))
    scheduler.add_job(
        scan_cycle,
        IntervalTrigger(minutes=price_interval, start_date=next_aligned_run_time(price_interval)),
        id="scan_cycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        startup_backfill,
        "date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=1),
        id="startup_backfill",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        hourly_summary,
        IntervalTrigger(hours=1, start_date=next_aligned_run_time(60)),
        id="hourly_summary",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    return scheduler


def create_app(enable_scheduler: bool = True) -> FastAPI:
    scheduler_holder: dict[str, BackgroundScheduler | None] = {"scheduler": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        create_tables()
        if enable_scheduler:
            scheduler_holder["scheduler"] = _start_background_scheduler()
            logger.info("[FastAPI] background scheduler started")
        try:
            yield
        finally:
            scheduler = scheduler_holder.get("scheduler")
            if scheduler:
                scheduler.shutdown(wait=False)
                logger.info("[FastAPI] background scheduler stopped")

    app = FastAPI(
        title="Market Monitor API",
        version="1.0.0",
        lifespan=lifespan,
        responses={400: {"description": "Unified error response"}},
    )
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(router)

    @app.get("/api/openapi.json", include_in_schema=False)
    def api_openapi():
        return app.openapi()

    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/")
    def spa_root():
        index = FRONTEND_DIST / "index.html"
        if index.exists():
            return FileResponse(index)
        return JSONResponse(
            status_code=503,
            content={
                "code": "FRONTEND_NOT_BUILT",
                "message": "frontend/dist 不存在，请先运行 python run.py frontend-build",
                "details": {},
            },
        )

    @app.get("/{path:path}", include_in_schema=False)
    def spa_fallback(path: str):
        if path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"code": "NOT_FOUND", "message": "API 路径不存在", "details": {"path": path}},
            )
        target = FRONTEND_DIST / path
        if target.exists() and target.is_file():
            return FileResponse(target)
        index = FRONTEND_DIST / "index.html"
        if index.exists():
            return FileResponse(index)
        return JSONResponse(
            status_code=503,
            content={
                "code": "FRONTEND_NOT_BUILT",
                "message": "frontend/dist 不存在，请先运行 python run.py frontend-build",
                "details": {},
            },
        )

    return app


app = create_app()
dev_app = create_app(enable_scheduler=False)
