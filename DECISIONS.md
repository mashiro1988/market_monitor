# Decision Log - market_monitor

> Newest decisions go first. Keep each entry to date / context / decision / rejected alternatives / impact.

## 2026-05-03 - Replace Streamlit With FastAPI Plus React/Vite

- Context: The dashboard needed a modern frontend while Python scanners, alerts, DB, and Dune wrappers should remain local and single-user.
- Decision: Use FastAPI REST as the only data boundary and React/Vite/TypeScript as the SPA served from `http://localhost:8000`.
- Rejected alternatives: Keep Streamlit alongside React; migrate to Next.js; let React read SQLite or duplicate business calculations.
- Impact: `app.py`, `pages/`, `streamlit`, and `streamlit-autorefresh` are removed; `api/`, `services/`, `schemas/`, and `frontend/` own the new UI path.

## 2026-05-03 - API Owns Dashboard Calculations

- Context: Market deltas, prediction family grouping, news filters, and annotation windows already existed as page logic and would drift if copied into React.
- Decision: Move those calculations into Python services and expose Pydantic schemas with dual UTC/Beijing time fields.
- Rejected alternatives: Recompute deltas/families/windows in TypeScript; return raw DB rows only.
- Impact: React remains a rendering/client-state layer, while `/api/market/*`, `/api/news`, `/api/predictions/*`, and `/api/annotations/*` define the business contract.

## 2026-05-03 - Dune API Uses 60 Minute In-Memory Cache

- Context: Dune data changes slowly enough for local use and live queries can be slow or rate-limited.
- Decision: Cache each Dune dataset in memory for 60 minutes and allow `force_refresh=true`.
- Rejected alternatives: Query Dune on every page load; persist Dune results in SQLite now; remove Dune endpoints until the UI is designed.
- Impact: On-chain UI remains a placeholder, but `/api/onchain/eth/*` endpoints are available with predictable TTL semantics.

## 2026-05-03 - Keep Root Project Maps As The Shared Architecture Contract

- Context: The root map files existed but had become unreadable mojibake while AGENTS.md requires map maintenance.
- Decision: Rewrite `ARCHITECTURE.md`, `DATAFLOW.md`, `DECISIONS.md`, and `PENDING.md` as concise UTF-8 project maps based only on scanned code.
- Rejected alternatives: Trust the previous unreadable maps; move maps under `docs/`; create a new parallel documentation set.
- Impact: Future structural changes should update these root maps in the same change set.

## 2026-04-29 - Startup News Backfill Skips LLM Scoring By Default

- Context: A 72-hour Jin10 backfill can return many items, and serial DeepSeek batches would hold `.scan.lock` long enough to skip multiple normal scans.
- Decision: `NewsScanner.backfill_missing_history()` stores source news by default without scoring; `NEWS_BACKFILL_LLM_ENABLED=1` opts into historical scoring.
- Rejected alternatives: Score all historical news; release scan lock during backfill and allow concurrent DB/API writes; remove news backfill.
- Impact: Backfill finishes faster, but historical backfill rows often have `llm_importance = null`.

## 2026-04-29 - Normal Scans Include Rolling Catch-Up Writes

- Context: Long startup backfills or transient source/API delays can leave recent 5m gaps even after the current scan runs.
- Decision: After current price/news scan, `run_scan_once()` backfills the last `SCAN_ROLLING_BACKFILL_INTERVALS` closed intervals as DB-only catch-up.
- Rejected alternatives: Rely only on startup backfill; include catch-up rows in the current alert evaluation; run a separate catch-up process.
- Impact: Recent gaps are more likely to heal, while current alert evaluation remains tied to the live scan result.

## 2026-04-28 - Prediction Alerts Use Saved `prev_probability`

- Context: `PredictionScanner` saves current rows before `AlertEngine.evaluate_predictions()`, so querying "latest" after save can see the current row.
- Decision: Store `prev_probability` in `PredictionMarket` and compare alerts against that value when the latest DB row is the just-saved row.
- Rejected alternatives: Evaluate prediction alerts before DB save; query history again in the source layer.
- Impact: Prediction shifts can fire without changing the page snapshot flow.

## 2026-04-28 - Startup Backfill Repairs Recent Price And News Gaps

- Context: If Streamlit or the scheduler is stopped, regular scans only resume from the next 5m window and old gaps remain.
- Decision: `run_startup_backfill_once()` runs after app/scheduler startup and backfills up to 72 hours of yfinance/OKX price bars plus visible Jin10/Bloomberg news.
- Rejected alternatives: Only show gaps in the UI; infer gaps from latest DB timestamp only; let normal scans run concurrently with startup backfill.
- Impact: Restarts repair recent missing data, but startup may spend time under `.scan.lock`.

## 2026-04-28 - Hourly Summary Reuses Market Overview Default Symbols

- Context: Hourly WeChat summaries were noisy when they tried to summarize every instrument.
- Decision: Use `MARKET_OVERVIEW_DEFAULT_SYMBOLS` for both market overview defaults and hourly summary.
- Rejected alternatives: Maintain a separate hourly-summary symbol list; parse alert logs to infer active symbols.
- Impact: Summary stays short and aligned with the dashboard's default watchlist.

## 2026-04-27 - News Annotation Reuses Price Alert Windows

- Context: Annotation should explain the same price movements that trigger alerts, not a separate page-only threshold.
- Decision: `pages/6_新闻标注.py` reads `price_change` rules from `config.ALERT_RULES` for BTC/ETH/NQ windows and thresholds.
- Rejected alternatives: Keep an independent 5m threshold in the annotation page; reuse only the threshold number but not the configured window.
- Impact: Annotation samples follow the same path as WeChat price alerts.

## 2026-04-27 - Polymarket Source Split Into Component Package

- Context: Polymarket fetching, retry, parsing, and filtering had separate ownership concerns.
- Decision: Use `scanners/sources/polymarket/` with `client.py`, `filters.py`, `parser.py`, and `source.py`; remove reliance on the old flat `polymarket_source.py` path.
- Rejected alternatives: Keep a compatibility forwarding module; keep all logic in one source file.
- Impact: Prediction source internals are easier to test while `PredictionRecord` and DB shape remain unchanged.

## 2026-04-27 - Automated Scans Start After Closed 5m Boundaries

- Context: Running exactly at process start or before a 5m bar settled could collect incomplete windows.
- Decision: `next_aligned_run_time()` returns the next natural boundary plus `SCAN_START_DELAY_SECONDS`.
- Rejected alternatives: Scan immediately at startup; keep scheduler phase tied to process start time.
- Impact: Automatic scans run around `xx:00:10`, `xx:05:10`, etc.; manual scans still run immediately.

## 2026-04-27 - Price Alerts Use Configured DB Windows

- Context: Source `change_pct` is generally one 5m bar/realtime move, but alerts need configured windows such as 15m.
- Decision: `AlertEngine.evaluate_prices()` computes movement from `price_snapshots` over `params.window_minutes`.
- Rejected alternatives: Continue using source-level `change_pct`; special-case only ETH.
- Impact: Alert text includes actual time interval and price range; missing historical baseline means no alert.

## 2026-04-26 - News Scanner Windows Align To Closed Price Bars

- Context: News scoring/annotation should match stable price windows rather than a moving "now minus 5m" slice.
- Decision: `NewsScanner._filter_scan_window()` keeps the previous closed 5m bucket.
- Rejected alternatives: Use `scan_time - 5m` to `scan_time`; depend on RSS/Jin10 list order.
- Impact: News DB inserts and alerts align with the price interval used later for annotation.

## 2026-04-26 - Main News Path Does Not Use Semantic Deduplication

- Context: LLM semantic dedupe adds latency and can hide candidate news needed for annotation.
- Decision: Keep exact `(source, source_id)` dedupe, but do not dedupe semantically before scoring.
- Rejected alternatives: Keep LLM event dedupe; add title-hash cross-source dedupe.
- Impact: Some near-duplicate cross-source news can appear, but candidate coverage is better.

## 2026-04-25 - DeepSeek V4 Flash Scores News

- Context: News needs short-horizon price-impact scoring, but high-cost/slower models are too heavy for a 5m loop.
- Decision: Use `deepseek-v4-flash` by default through `NewsScorer`, with configurable batch size, timeout, and retry.
- Rejected alternatives: Continue a more expensive Pro model; maintain multiple scoring profiles.
- Impact: Scoring favors speed and stability; missing API key leaves LLM fields empty.

## 2026-04-25 - Jin10 Important Also Triggers News Alerts

- Context: Jin10's source-side `important` flag is not an LLM score but still marks items the user wants pushed.
- Decision: News alerts fire on `llm_importance >= min_importance` or `source == "jin10" and importance == 1`.
- Rejected alternatives: Alert only on LLM score; map Jin10 important to a fake LLM score.
- Impact: Jin10 important items can push even when LLM score is absent or low.

## 2026-04-25 - News Source Runtime Is Jin10 Plus Bloomberg

- Context: More feeds increased noise and scoring cost during the annotation phase.
- Decision: `config.NEWS_SOURCES` keeps Jin10 and Bloomberg RSS enabled.
- Rejected alternatives: Fetch all configured feeds and filter only in UI; keep disabled source configs in active scanner path.
- Impact: New `news_items` are limited to Jin10/Bloomberg unless config changes.

## 2026-04-23 - Eastmoney Quote Replaces FRED For Live Bond Yields

- Context: FRED is slower and less useful for intraday US/Japan yield monitoring.
- Decision: Use Eastmoney structured quote API for US/JP 2Y/10Y yields and compute 10Y-2Y spreads.
- Rejected alternatives: Parse Eastmoney news headlines; keep FRED as the main display source.
- Impact: Bond `PriceSnapshot.source` is `eastmoney_bond_quote`; FRED adapter remains but is not in the current price scanner path.

## 2026-04-23 - Streamlit Background Scheduler Sends Hourly Summary

- Context: Users who only open Streamlit should still receive hourly WeChat summaries.
- Decision: `app.py` starts both scan and hourly summary jobs in the cached background scheduler.
- Rejected alternatives: Require a separate `python run.py schedule` process.
- Impact: One open dashboard can scan and summarize; `.scan.lock` prevents duplicate scans across processes.

## 2026-04-23 - Jin10 Requests Use Beijing Time

- Context: Jin10 `max_time` is interpreted as Beijing time; passing UTC fetches the wrong time slice.
- Decision: Convert request cursors to Beijing local time and convert returned timestamps back to UTC naive before storage.
- Rejected alternatives: Add 8 hours only in UI; omit `max_time`.
- Impact: Jin10 fetch/backfill targets the intended current Beijing-time news window.

## 2026-04-23 - Scan Entry Uses Cross-Process `.scan.lock`

- Context: Streamlit background jobs, manual sidebar scans, and standalone scheduler can overlap.
- Decision: Wrap scan and backfill entry points with a root `.scan.lock` containing PID and stale-lock cleanup.
- Rejected alternatives: Rely only on APScheduler `max_instances`; rely only on DB uniqueness.
- Impact: Only one scan/backfill path runs at a time across processes.

## 2026-04-23 - Crypto Main Source Is OKX, Binance Removed From Runtime Path

- Context: Binance global endpoints often fail from US egress IPs with restricted-location errors.
- Decision: Use OKX raw 5m candles as the main crypto path; fall back to CoinGecko realtime only for missing symbols.
- Rejected alternatives: Keep Binance first; call `load_markets()` before every OKX run.
- Impact: Crypto source is normally `okx_swap_5m` or `okx_spot_5m`; CoinGecko timestamps are realtime collection times.

## 2026-04-22 - Price Collection Uses Closed 5m K-Line Timestamps

- Context: User-facing scans should represent the just-closed 5m bar instead of a current spot tick.
- Decision: `PriceRecord.timestamp` stores closed bar end time where available; DB falls back to scan time only when a source has no timestamp.
- Rejected alternatives: Add a separate `price_timestamp` column; treat all prices as current spot.
- Impact: `(symbol, timestamp)` dedupe and window calculations depend on source timestamp semantics.

## 2026-04-21 - SQLite WAL Instead Of PostgreSQL

- Context: The app is single-machine and single-user, without a real multi-writer deployment requirement.
- Decision: Use SQLite with WAL mode in the project root.
- Rejected alternatives: PostgreSQL deployment.
- Impact: Local setup is simple and read/write concurrency is acceptable for dashboard plus scanner.

## 2026-04-21 - Legacy Tables Are Kept For Compatibility

- Context: Old tables were replaced by unified snapshots but may still contain historical local data.
- Decision: Keep `models/legacy.py` and the legacy `collect` command, but do not extend them for new scanner work.
- Rejected alternatives: Migrate and delete old tables immediately.
- Impact: New runtime should write `PriceSnapshot`, `NewsItem`, and `PredictionMarket`; legacy code remains a separate path.

## 2026-04-21 - Signals Framework Remains Unwired Until Concrete Signals Exist

- Context: `signals/` defines contracts but no concrete signal provides runtime value yet.
- Decision: Keep the framework isolated and do not call it from `run_scan_once()`.
- Rejected alternatives: Wire an empty registry into every scan.
- Impact: No runtime overhead; future signal work needs an explicit integration decision.
