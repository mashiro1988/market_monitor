from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
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

    def sector_scan_cycle() -> None:
        """读本地 cache + DB 板块映射 -> 算板块涨跌入库。
        不进 fast_scan,因为节奏不一样(板块 1h, fast_scan 5min)且锁分离避免阻塞。
        """
        try:
            from scanners.sector_scanner import SectorScanner

            stats = SectorScanner().scan()
            logger.info("[FastAPI Scheduler] sector_scan finished: {}", stats)
        except Exception as exc:
            logger.exception("[FastAPI Scheduler] sector_scan failed: {}", exc)

    def cmc_bootstrap() -> None:
        """启动后异步检查 CMC 板块映射是否过期(7 天 TTL),过期了就刷新。
        作为 date trigger 一次性 job,不阻塞 lifespan。首次启动大概 2 分钟。
        """
        try:
            from database import SessionLocal
            from services import cmc_client

            session = SessionLocal()
            try:
                if cmc_client.needs_refresh(session):
                    logger.info("[FastAPI Scheduler] cmc_bootstrap: 板块映射过期/为空,开始刷新...")
                    result = cmc_client.refresh_categories(session=session)
                    logger.info("[FastAPI Scheduler] cmc_bootstrap done: {}", result)
                else:
                    logger.info("[FastAPI Scheduler] cmc_bootstrap: 板块映射仍在 TTL 内,跳过")
            finally:
                session.close()
        except Exception as exc:
            logger.exception("[FastAPI Scheduler] cmc_bootstrap failed: {}", exc)

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
    # 板块扫描:每小时 :32 跑(BMAC 30m 偏移在 :30 落 .ready,留 2 min 给 remote_puller 拉完)。
    # 自己的 max_instances=1 -> 锁分离,跟 scan_cycle 并行也 OK。
    scheduler.add_job(
        sector_scan_cycle,
        CronTrigger(minute=32),
        id="sector_scan_cycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    # CMC 板块映射启动检查:lifespan 起来后 10s 跑一次。如果 TTL 过期会触发刷新(~2min)。
    scheduler.add_job(
        cmc_bootstrap,
        "date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=10),
        id="cmc_bootstrap",
        replace_existing=True,
        max_instances=1,
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
            # 远程数据 puller 守护线程,独立于 APScheduler,有自己的 60s 轮询节奏。
            try:
                from services.remote_puller import start_puller
                start_puller()
            except Exception as exc:
                logger.warning("[FastAPI] remote_puller 启动失败({}); "
                               "板块数据将停留在上一次缓存", exc)
        try:
            yield
        finally:
            try:
                from services.remote_puller import stop_puller
                stop_puller()
            except Exception:
                pass
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
