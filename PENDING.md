# Pending Handoff - market_monitor

> New sessions should read this file first. Update it when task state, risk tier, or known facts change.

## Quick Snapshot

- Primary CLI: `python run.py app` for FastAPI + React at `http://localhost:8000`, `python run.py api-dev` for API reload mode, `python run.py frontend-build` for Vite build, `python run.py scan` for one cycle, and `python run.py schedule` for the standalone scheduler (`run.py:175`, `run.py:198`, `run.py:211`, `run.py:239`, `run.py:472`).
- Dashboard runtime: FastAPI lifespan starts startup backfill, aligned 5m scans, and hourly summary; React is served as static SPA from `frontend/dist` when built (`api/app.py:32`, `api/app.py:97`, `api/app.py:145`).
- API boundary: React calls only `/api/*`; service layer reads DB and owns market deltas, news filters, prediction families, alert views, annotation windows, Dune cache, and scan tasks (`api/routes.py:34`, `services/market_service.py:65`, `services/prediction_service.py:111`, `services/annotation_service.py:69`).
- Scan lock: all normal scans and startup backfills share `.scan.lock`; overlapping triggers skip rather than run concurrently (`run.py:31`, `run.py:118`).
- Scan order: price -> news -> rolling catch-up -> prediction -> alert evaluation (`run.py:239`, `run.py:289`).
- Price pipeline: yfinance closed 5m bars, OKX closed 5m crypto candles, CoinGecko realtime fallback for missing crypto, Eastmoney bond quote and spreads (`scanners/price_scanner.py:26`).
- News pipeline: Jin10 plus Bloomberg RSS, filtered to previous closed 5m news window, then DeepSeek scoring if configured (`scanners/news_scanner.py:36`, `scanners/news_scanner.py:250`, `scanners/scorer.py:53`).
- Prediction pipeline: Polymarket Gamma configured slugs plus filtered tag discovery; rows store `prev_probability` for alert comparison (`scanners/sources/polymarket/source.py:64`, `scanners/prediction_scanner.py:40`).
- Core DB tables: `price_snapshots`, `news_items`, `news_price_annotations`, `prediction_markets`, `alert_logs` (`models/price.py:11`, `models/news.py:11`, `models/news.py:37`, `models/prediction.py:11`, `models/alert_log.py:11`).
- Time semantics: DB times are UTC naive; UI and WeChat format Beijing time through `chart_utils.py` (`chart_utils.py:17`, `chart_utils.py:27`).
- Price snapshot timestamp: yfinance/OKX use closed 5m bar end; CoinGecko uses collection time; Eastmoney uses quote update time when provided (`scanners/base.py:18`).
- News timestamp: `NewsItem.timestamp` is source publish time when available, not insert time (`scanners/news_scanner.py:307`).
- Startup backfill: capped to 72 hours for prices/news; historical news LLM scoring defaults off (`scanners/price_scanner.py:53`, `scanners/news_scanner.py:78`, `config.py:77`).
- Market overview default symbols are shared with hourly summary and the React default chart (`config.py:86`, `alerts/engine.py:531`, `frontend/src/pages/MarketPage.tsx`).
- News annotation only targets `BTC/USDT`, `ETH/USDT`, and `NQ=F`, using configured price alert windows (`services/annotation_service.py:18`).
- `signals/` is a reserved framework and is not connected to scan runtime (`signals/base.py:28`, `run.py:239`).
- `data_collector.py` and `python run.py collect` are legacy paths writing legacy tables; avoid extending them for new scanner work (`run.py:219`, `data_collector.py:16`).
- Root maps are readable UTF-8 and are part of the working contract: `ARCHITECTURE.md`, `DATAFLOW.md`, `DECISIONS.md`, `PENDING.md`.

## Task Tiers

### A Tier - Low Risk, Any Session Can Do

- [ ] Add an `AlertRuleType` enum or constants and replace raw `rule_type` comparisons in `alerts/engine.py` (`alerts/rules.py:11`, `alerts/engine.py:114`, `alerts/engine.py:278`, `alerts/engine.py:361`, `alerts/engine.py:519`).
- [ ] Add shared proxy helper on `BaseSource` or a small utility and migrate repeated `{"http": proxy, "https": proxy}` code (`scanners/base.py:54`, `scanners/sources/coingecko_source.py:49`, `scanners/sources/eastmoney_bond_source.py:128`, `alerts/channels/wechat_work.py:39`).
- [ ] Add optional loguru file logging if persistent logs are needed; current runtime only logs to stdout.
- [ ] Generate OpenAPI-derived TypeScript types once frontend dependencies can be installed and codegen tooling is chosen.
- [ ] Add broader frontend component tests after `node_modules` is available; current migration has source files but dependency install was blocked by approval service/network.

### B Tier - Core Logic, Needs A Separate Plan/PR

- [ ] Split `alerts/engine.py` into smaller responsibilities such as rule loading, evaluators, summary builder, dispatcher, and log repository (`alerts/engine.py:48`).
- [ ] Design a source health model that distinguishes "no data" from "source failed" across price/news/prediction scanners (`scanners/price_scanner.py:119`, `scanners/news_scanner.py:49`, `scanners/prediction_scanner.py:27`).
- [ ] Wire `signals/` into the scan loop only after at least one concrete `BaseSignal` implementation exists (`signals/base.py:28`, `signals/registry.py:9`).
- [ ] Build annotation export/training-set generation from `news_price_annotations` plus `news_items`, including positive samples, same-window negatives, and no-clear-news windows (`models/news.py:36`, `services/annotation_service.py:155`).
- [ ] Decide whether legacy tables should be migrated, archived, or permanently retained; `data_collector.py` still writes them (`models/legacy.py:9`, `data_collector.py:309`).
- [ ] Consider a first-class retention cleanup job for `DATA_RETENTION`; config exists but no cleanup path was found (`config.py:310`).

### C Tier - Observe Only For Now

- [ ] Dune API depends on configured saved query IDs and live Dune API; local persistence is intentionally not added yet (`services/onchain_service.py:12`, `市场监控/dune_queries.py:27`).
- [ ] `test.py`, `test_cex_inout.py`, and `strategy_test.py` look like ad hoc/root scripts, not normal pytest files; do not refactor without checking intent.
- [ ] Polymarket tracked slugs are hard-coded in config; relevance may drift as 2026 markets close (`config.py:198`).
- [ ] CoinGecko fallback remains realtime, not closed 5m K-line data; dashboards and alerts should continue to treat its timestamp accordingly (`scanners/sources/coingecko_source.py:54`).

## Known Facts For B-Tier Work

| Topic | Facts |
|---|---|
| Alert rule strings | `AlertRule.rule_type` is a string; branches exist in `evaluate_prices`, `evaluate_news`, `evaluate_predictions`, and `send_hourly_summary` (`alerts/rules.py:11`, `alerts/engine.py:114`, `alerts/engine.py:278`, `alerts/engine.py:361`, `alerts/engine.py:519`). |
| Price alert windows | Price alerts query `price_snapshots` around `current_ts - window_minutes` with tolerance `max(SCAN_INTERVALS.price * 2, 1)` (`alerts/engine.py:196`). |
| News alert dedupe | News alert logs include `news:<source>:<source_id>` markers and `_already_alerted()` searches for marker substrings (`alerts/engine.py:262`, `alerts/engine.py:328`). |
| News scan window | Normal news scan targets the previous closed bucket, not the current in-progress bucket (`scanners/news_scanner.py:250`). |
| Startup locking | Startup backfill and normal scans use the same `_scan_lock()`, so long backfills skip overlapping scheduled triggers (`run.py:118`, `run.py:294`). |
| Price backfill scope | Only yfinance and OKX are backfilled; Eastmoney and CoinGecko do not fake historical 5m rows (`scanners/price_scanner.py:83`, `scanners/price_scanner.py:114`). |
| Prediction alert baseline | `PredictionScanner` saves current row first and alert evaluation uses `prev_probability` from that saved row when it matches the current record (`scanners/prediction_scanner.py:48`, `alerts/engine.py:375`). |
| Annotation uniqueness | `news_price_annotations` is unique on `(symbol, window_start, window_end)` and service upsert reuses an existing row (`models/news.py:58`, `services/annotation_service.py:155`). |
| Task retention | API scan task records are in-memory only and completed/skipped/failed tasks older than 24h are cleaned on access (`services/task_service.py:12`, `services/task_service.py:31`). |

## Recently Completed

- [x] Rebuilt root project maps from current code and restored readable UTF-8 documentation on 2026-05-03.
- [x] Replaced Streamlit with FastAPI + React/Vite source, removed Streamlit code/dependencies, and moved dashboard calculations into services on 2026-05-03.
- [x] Backend validation passed with `python -m pytest` (61 tests) and Streamlit cleanup grep returned no code/dependency matches on 2026-05-03.
- [x] Frontend dependency install/build is unblocked; npm needed `C:\Program Files\nodejs` prepended to `PATH` so esbuild postinstall can find `node`.
