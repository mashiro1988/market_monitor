# Architecture Map - market_monitor

> Last verified from code on 2026-05-03. Update this file in the same change set as any module move, entry-point change, or dependency-direction change.

## Module Responsibilities

| Module | Responsibility |
|---|---|
| `run.py` | CLI entry point for FastAPI app/dev, frontend build, collect/setup/schedule/scan; owns scan lock, startup backfill, rolling catch-up, and the full scan/alert sequence (`run.py:40`, `run.py:175`, `run.py:198`, `run.py:239`, `run.py:472`). |
| `api/` | FastAPI boundary: app factory/lifespan scheduler/static SPA serving, unified error handlers, DB dependency, and REST routes under `/api` (`api/app.py:32`, `api/app.py:97`, `api/app.py:124`, `api/app.py:145`, `api/routes.py:34`). |
| `services/` | Business/query layer for market data, news, predictions, alerts, annotations, on-chain Dune cache, task registry, pagination, and time formatting (`services/market_service.py:65`, `services/news_service.py:41`, `services/prediction_service.py:147`, `services/annotation_service.py:69`, `services/task_service.py:67`). |
| `schemas/` | Pydantic API contracts used to generate OpenAPI and keep React from reading DB shapes directly (`schemas/common.py:10`, `schemas/market.py:7`, `schemas/news.py:8`, `schemas/tasks.py:7`). |
| `frontend/` | React/Vite/TypeScript single-page app with left navigation, TanStack Query API client, DataTable/state/chart wrappers, and migrated pages (`frontend/src/main.tsx`, `frontend/src/api/client.ts`, `frontend/src/components/AppShell.tsx`, `frontend/src/pages/MarketPage.tsx`). |
| `config.py` | Central configuration for proxy detection, API keys, scan/backfill windows, source lists, alert rules, Dune query IDs, and retention constants (`config.py:12`, `config.py:44`, `config.py:63`, `config.py:100`, `config.py:163`, `config.py:180`, `config.py:215`, `config.py:299`). |
| `database.py` | SQLAlchemy engine/session factory; enables SQLite WAL, creates tables, and applies lightweight SQLite schema fixes for `news_items` (`database.py:15`, `database.py:26`, `database.py:34`). |
| `models/` | ORM layer: price snapshots, news items and annotations, prediction snapshots, alert logs, plus legacy tables kept for compatibility (`models/price.py:9`, `models/news.py:10`, `models/news.py:36`, `models/prediction.py:10`, `models/alert_log.py:10`, `models/legacy.py:9`). |
| `scanners/` | Runtime data collection pipeline and source adapters for price/news/prediction records (`scanners/base.py:11`, `scanners/price_scanner.py:26`, `scanners/news_scanner.py:36`, `scanners/prediction_scanner.py:18`, `scanners/sources/polymarket/source.py:12`). |
| `alerts/` | Loads alert rules, evaluates price/news/prediction/hourly-summary rules, dispatches through WeChat Work or console, and records `AlertLog` rows (`alerts/engine.py:48`, `alerts/rules.py:8`, `alerts/channels/wechat_work.py:9`). |
| `市场监控/dune_queries.py` | Dune client wrapper for saved ETH queries; now consumed through cached service/API endpoints instead of a Streamlit page (`市场监控/dune_queries.py:27`, `services/onchain_service.py:12`). |
| `signals/` | Reserved signal framework; defines contracts and registry but is not invoked by `run_scan_once()` (`signals/base.py:11`, `signals/base.py:28`, `signals/registry.py:9`, `run.py:239`). |
| `data_collector.py` | Legacy collector for old tables, still reachable via `python run.py collect`; not part of the scanner/API/frontend path (`run.py:219`, `data_collector.py:16`, `data_collector.py:309`). |
| `chart_utils.py` | Shared chart/time helpers still used by scanner/alert tests and non-React runtime formatting (`chart_utils.py:7`, `chart_utils.py:17`, `chart_utils.py:27`). |

## Dependency Direction

```text
run.py
  -> api.app through uvicorn, database.py, config.py
  -> scanners/{price,news,prediction}_scanner.py
  -> alerts/engine.py

api/routes.py
  -> services/*, schemas/*, database dependency

services/*
  -> database sessions supplied by API
  -> models/*, config.py, chart_utils.py where needed
  -> alerts/channels/wechat_work.py only for webhook test
  -> 市场监控/dune_queries.py only through onchain_service dynamic import

frontend/*
  -> REST API only through frontend/src/api/client.ts
  -> no database/model/scanner imports

scanners/*
  -> scanners/sources/*, scanners/scorer.py, database.py, models/*, config.py

alerts/*
  -> alerts/rules.py, alerts/channels/*, database.py, models/*, config.py

models/*
  -> database.Base
```

Observed dependency check: no lower-level source/model module imports `api/`, `services/`, `frontend/`, or `run.py`; React has no direct Python/DB dependency.

## Entry Points

| Command | Runtime path |
|---|---|
| `python run.py app` | Creates tables, builds missing `frontend/dist`, opens `http://localhost:8000`, then runs `api.app:app` with FastAPI scheduler lifespan (`run.py:198`, `api/app.py:97`). |
| `python run.py api-dev` | Runs FastAPI in reload/factory mode on `127.0.0.1:8000`; Vite dev server can proxy `/api` (`run.py:211`, `frontend/vite.config.ts`). |
| `python run.py frontend-build` | Runs `npm run build` in `frontend/` and expects Vite to produce `frontend/dist` (`run.py:175`). |
| `python run.py schedule` | Starts blocking APScheduler: startup backfill after 1 second, scan every aligned 5 minutes, hourly summary every aligned hour (`run.py:376`). |
| `python run.py scan` | Runs one full scan under `.scan.lock`, then exits (`run.py:239`). |
| `python run.py setup` | Creates all current and legacy tables (`run.py:231`, `database.py:26`). |
| `python run.py collect` | Runs legacy `DataCollector.collect_all_data()` into legacy tables (`run.py:219`, `data_collector.py:309`). |

## Main Call Chain - `python run.py app`

1. `run.py:main()` dispatches to `run_fastapi_app()` (`run.py:472`, `run.py:515`).
2. `run_fastapi_app()` creates DB tables, ensures `frontend/dist/index.html`, opens `http://localhost:8000`, and starts Uvicorn (`run.py:198`).
3. FastAPI lifespan creates tables and starts the background scheduler when enabled (`api/app.py:97`).
4. `api.app._start_background_scheduler()` schedules startup backfill, aligned scan, and hourly summary (`api/app.py:32`).
5. REST requests enter `api/routes.py`, which delegates calculations/queries to `services/*` and returns Pydantic schemas (`api/routes.py:34`, `services/market_service.py:65`).
6. React pages call only `/api/*` through `frontend/src/api/client.ts` and render data through reusable controls, tables, state views, and charts.

## Main Call Chain - Scan Runtime

1. `run_scan_once()` acquires `.scan.lock`; conflicts set `run_scan_once.last_skipped = True` and return empty result lists (`run.py:239`).
2. `PriceScanner.scan()` collects yfinance -> OKX -> CoinGecko missing crypto -> Eastmoney bonds, then saves `PriceSnapshot` rows (`scanners/price_scanner.py:26`, `scanners/price_scanner.py:139`).
3. `NewsScanner.scan()` collects Jin10/RSS, filters to the target closed 5m window, enriches via DeepSeek if enabled, then saves `NewsItem` rows (`scanners/news_scanner.py:36`, `scanners/news_scanner.py:250`, `scanners/scorer.py:80`).
4. `run.py` executes rolling catch-up for recent closed price/news intervals; those writes do not enter this scan's price alert evaluation (`run.py:289`).
5. `PredictionScanner.scan()` collects Polymarket markets and saves `PredictionMarket` snapshots (`scanners/prediction_scanner.py:18`).
6. `AlertEngine.evaluate_all()` evaluates price, news, and prediction rules and logs deliveries (`alerts/engine.py:595`).

## Known Structural Issues

- `AlertEngine` still owns rule loading, price/news/prediction evaluation, hourly summary aggregation, dispatch, and alert logging in one class (`alerts/engine.py:48`).
- `AlertRule.rule_type` is a raw string and engine matching is string-based, so typos fail silently unless tests cover the path (`alerts/rules.py:11`, `alerts/engine.py:114`, `alerts/engine.py:278`, `alerts/engine.py:361`, `alerts/engine.py:519`).
- `run.py` imports frontend build tooling and scan/runtime orchestration in one file; this is acceptable for a local CLI but should stay small (`run.py:175`, `run.py:239`).
- Source failure and "no data" both surface as empty lists in scanner orchestration, making health diagnosis hard (`scanners/price_scanner.py:119`, `scanners/news_scanner.py:49`, `scanners/prediction_scanner.py:27`).
- `signals/` is a framework only; it has no concrete signal and no call from `run_scan_once()` (`signals/base.py:28`, `signals/registry.py:9`, `run.py:239`).
- `data_collector.py` still imports Binance/FRED/legacy tables and remains callable via `collect`, so new work should avoid extending the legacy path (`run.py:219`, `data_collector.py:16`).
- Frontend dependency installation currently requires network access; `frontend/node_modules/` and `frontend/dist/` are intentionally ignored.
