# Dataflow Map - market_monitor

> Last verified from code on 2026-05-03. Update this file when runtime data shapes, persistence semantics, producer/consumer relationships, or external integrations change.

## Disk Layout

```text
D:\market_monitor\
|-- market_monitor.db           SQLite database, created by `create_tables()`
|-- market_monitor.db-wal       SQLite WAL runtime file
|-- market_monitor.db-shm       SQLite shared-memory runtime file
|-- .env                        API keys and optional PROXY_URL; not owned by code
|-- .scan.lock                  cross-process scan lock, created during scan/backfill
|-- api/                        FastAPI app, routes, dependencies, error handlers
|-- services/                   DB query, calculation, cache, task, and integration services
|-- schemas/                    Pydantic response/request schemas and OpenAPI source
|-- frontend/                   React/Vite/TypeScript SPA source
|-- frontend/dist/              generated static frontend; ignored by git
|-- frontend/node_modules/      installed frontend deps; ignored by git
|-- ARCHITECTURE.md             structure map
|-- DATAFLOW.md                 this runtime data map
|-- DECISIONS.md                architecture decision log
|-- PENDING.md                  cross-session handoff
```

Logs currently go to loguru stdout; no file log sink is configured in code.

## Persistent Tables

| Table | ORM | Producer | Consumers |
|---|---|---|---|
| `price_snapshots` | `PriceSnapshot` (`models/price.py:9`) | `PriceScanner._save_records()` (`scanners/price_scanner.py:139`) | market API/cards/charts/table, alert price windows, hourly summary, annotation API |
| `news_items` | `NewsItem` (`models/news.py:10`) | `NewsScanner._save_records()` (`scanners/news_scanner.py:283`) | news API, news alerts, hourly summary count, annotation context-news API |
| `news_price_annotations` | `NewsPriceAnnotation` (`models/news.py:36`) | `annotation_service.upsert_annotation()` (`services/annotation_service.py:155`) | future training/export work; no current scanner consumes it |
| `prediction_markets` | `PredictionMarket` (`models/prediction.py:10`) | `PredictionScanner._save_records()` (`scanners/prediction_scanner.py:40`) | prediction API/family charts, prediction alerts |
| `alert_logs` | `AlertLog` (`models/alert_log.py:10`) | `AlertEngine._log_alert()` (`alerts/engine.py:79`) | cooldown checks, news dedupe, alerts API/history page |
| `stock_indices`, `bond_rates`, `economic_data`, `crypto_data`, `market_news` | legacy models (`models/legacy.py:9`) | only legacy `DataCollector` path (`data_collector.py:16`) | kept for compatibility; new API/frontend do not use them |

SQLite details:

- WAL is enabled on each DB connection (`database.py:15`).
- `PriceSnapshot` has unique `(timestamp, symbol)` via `ix_price_snapshot_ts_symbol` (`models/price.py:26`).
- `NewsItem` has a non-unique `(source, source_id)` index and dedupe is enforced in application code (`models/news.py:30`, `scanners/news_scanner.py:283`).
- `database._ensure_sqlite_schema()` adds missing LLM columns to old `news_items` tables and recreates the source-id index (`database.py:34`).

## API Contracts

| Contract | Shape | Producer | Consumer |
|---|---|---|---|
| unified error | `{code, message, details}` (`api/errors.py:15`) | FastAPI exception handlers | React error state and API clients |
| API time fields | `{timestamp_utc, timestamp_bj}` (`services/time_utils.py:29`) | service serializers | every React page and CSV display |
| paginated result | `{items,total,page,page_size,pages}` (`schemas/common.py:20`) | table/log/news services | React DataTable/pager |
| scan task | `queued/running/succeeded/skipped/failed` plus 24h in-memory retention (`services/task_service.py:12`, `services/task_service.py:67`) | `/api/tasks/scan` | market page manual scan banner |
| Dune cache | rows plus `cached_at` and `ttl_seconds=3600` (`services/onchain_service.py:12`) | on-chain API endpoints | on-chain placeholder/future pages |

## Runtime Contracts

| Object | Shape | Producer | Consumer |
|---|---|---|---|
| `PriceRecord` | `asset_class`, `symbol`, `name`, `price`, optional `prev_price`, `change_pct`, `volume`, `source`, optional UTC-naive `timestamp` (`scanners/base.py:11`) | price sources | `PriceScanner._save_records()`, `AlertEngine.evaluate_prices()` |
| `NewsRecord` | `source`, `source_id`, `title`, optional `content`, `url`, `importance`, LLM fields, `language`, `categories`, optional `published_at` (`scanners/base.py:27`) | Jin10/RSS sources and `NewsScorer` | `NewsScanner._save_records()`, `AlertEngine.evaluate_news()` |
| `PredictionRecord` | `market_id`, `question`, `outcome`, `probability`, optional `volume` (`scanners/base.py:45`) | Polymarket source | `PredictionScanner._save_records()`, `AlertEngine.evaluate_predictions()` |
| `AlertRule` | `name`, `rule_type`, `params`, `channels`, `cooldown_minutes`, `enabled` (`alerts/rules.py:8`) | `config.ALERT_RULES` | `AlertEngine._load_rules()`, `alerts_service.get_rules()` |
| market latest API item | latest price plus `change_5m`, `change_1h`, `change_24h`, and dual time fields (`services/market_service.py:65`) | `market_service.get_latest_prices()` | React market asset cards |
| prediction family | grouped Yes-market series by macro theme (`services/prediction_service.py:111`) | prediction service | React prediction family charts |
| annotation price window | price rule window plus start/end prices, change %, dual time fields (`services/annotation_service.py:69`) | annotation service | React annotation workbench |

## Price Flow

1. `PriceScanner.scan()` starts with UTC-naive `scan_time` (`scanners/price_scanner.py:26`).
2. `YFinancePriceSource.fetch()` downloads 7d of 5m bars for stock indices, futures, Asian indices, commodities, and any yfinance bonds; it stores bar-end UTC-naive timestamps (`scanners/sources/yfinance_source.py:13`, `scanners/sources/yfinance_source.py:65`, `scanners/sources/yfinance_source.py:126`).
3. `OkxPriceSource.fetch()` tries `*-USDT-SWAP` 5m candles first, then `*-USDT` spot candles, and stores bar-end timestamps (`scanners/sources/okx_source.py:17`, `scanners/sources/okx_source.py:119`, `scanners/sources/okx_source.py:132`).
4. If OKX misses configured crypto names, `CoinGeckoPriceSource.fetch_symbols()` fills only those missing names with realtime USD prices and collection-time timestamps (`scanners/price_scanner.py:34`, `scanners/sources/coingecko_source.py:54`).
5. `EastmoneyBondQuoteSource.fetch()` fetches US/Japan 2Y/10Y yields from structured quote API and computes `US_SPREAD`/`JP_SPREAD` (`scanners/sources/eastmoney_bond_source.py:20`, `scanners/sources/eastmoney_bond_source.py:24`, `scanners/sources/eastmoney_bond_source.py:98`, `scanners/sources/eastmoney_bond_source.py:119`).
6. `PriceScanner._save_records()` groups by symbol, skips existing `(symbol, timestamp)`, fills missing previous price from the previous DB row, and commits `PriceSnapshot` rows (`scanners/price_scanner.py:139`).
7. `/api/market/latest` reads the last 10 days and computes 5m/1h/24h deltas server-side (`services/market_service.py:65`).
8. `/api/market/history`, `/table`, and `/table.csv` query DB snapshots and return normalized chart series, paginated rows, or UTF-8-SIG CSV (`services/market_service.py:118`, `services/market_service.py:217`, `services/market_service.py:238`).

Backfill:

- `PriceScanner.backfill_missing_history()` caps the request to 72 hours (`scanners/price_scanner.py:53`).
- Only yfinance and OKX implement history backfill; CoinGecko and Eastmoney are explicitly current-quote only (`scanners/price_scanner.py:61`, `scanners/price_scanner.py:83`).
- Rolling catch-up after each scan writes recent closed intervals but does not feed those historical records into this scan's alert price evaluation (`run.py:289`).

## News Flow

1. `NewsScanner.__init__()` registers Jin10 when enabled and all enabled RSS configs; current config enables Jin10 and Bloomberg RSS (`scanners/news_scanner.py:21`, `config.py:163`).
2. `Jin10Source.fetch()` sends `max_time` in Beijing local time, parses Jin10 Beijing timestamps back to UTC naive, and stores `important` as `importance` 1/0 (`scanners/sources/jin10_source.py:31`, `scanners/sources/jin10_source.py:36`, `scanners/sources/jin10_source.py:52`, `scanners/sources/jin10_source.py:103`).
3. `RSSSource.fetch()` downloads the configured feed manually, parses up to 50 entries with feedparser, hashes entry id/link/title into `source_id`, and uses feed published time when available (`scanners/sources/rss_source.py:31`, `scanners/sources/rss_source.py:51`).
4. `NewsScanner._filter_scan_window()` keeps only the previous closed 5m news window, e.g. a 15:49 scan handles 15:40-15:45 (`scanners/news_scanner.py:250`).
5. `NewsScorer.enrich_batch()` adds DeepSeek LLM fields when `DEEPSEEK_API_KEY` is configured; no key means records remain unscored (`scanners/scorer.py:53`, `scanners/scorer.py:61`, `scanners/scorer.py:80`).
6. `NewsScanner._save_records()` writes `NewsItem.timestamp = published_at` when available, otherwise `scan_time`; with `skip_existing=True`, duplicate `(source, source_id)` rows are skipped (`scanners/news_scanner.py:283`).
7. `/api/news` applies source, LLM score, Jin10 important, keyword, hour-window, and pagination filters server-side (`services/news_service.py:41`).

## Prediction Flow

1. `PredictionScanner` registers `PolymarketSource` only when `config.POLYMARKET.enabled` is true (`scanners/prediction_scanner.py:13`, `config.py:180`).
2. `PolymarketSource.fetch()` first expands configured market/event slugs, then searches configured tags by Gamma volume and applies macro/noise filters (`scanners/sources/polymarket/source.py:23`, `scanners/sources/polymarket/source.py:64`, `scanners/sources/polymarket/filters.py:3`).
3. `parse_market()` converts Gamma outcomes/prices to one `PredictionRecord` per outcome (`scanners/sources/polymarket/parser.py:13`).
4. `PredictionScanner._save_records()` stores current rows and fills `prev_probability` from the previous DB snapshot for the same `(market_id, outcome)` (`scanners/prediction_scanner.py:40`).
5. `/api/predictions` returns latest outcome summaries and single-market history endpoint; `/api/predictions/families` groups related Yes markets by theme (`services/prediction_service.py:111`, `services/prediction_service.py:147`).

## Alert And Task Flow

| Flow | Producer | Consumer |
|---|---|---|
| manual scan | `POST /api/tasks/scan` creates a daemon-thread task; task states stay in memory for 24h (`services/task_service.py:12`, `services/task_service.py:67`) | market page scan banner and `/api/tasks/{task_id}` |
| scan conflict | active API task or `.scan.lock` conflict returns `skipped`; tasks are not queued and scans do not run concurrently (`services/task_service.py:67`, `run.py:239`) | API clients and scheduler logs |
| rule view | `alerts_service.get_rules()` serializes `config.ALERT_RULES` read-only (`services/alerts_service.py:15`) | alerts page |
| webhook test | `alerts_service.test_wechat()` sends one WeChat Work test message (`services/alerts_service.py:33`) | alerts page button |
| alert logs | `alerts_service.get_logs()` paginates `alert_logs` (`services/alerts_service.py:41`) | alerts page table |

## Annotation Flow

1. `/api/annotations/price-rules` reads enabled price-change rules for `BTC/USDT`, `ETH/USDT`, and `NQ=F` (`services/annotation_service.py:18`).
2. `/api/annotations/symbols` lists available target symbols in the selected lookback (`services/annotation_service.py:42`).
3. `/api/annotations/windows` computes price windows using alert thresholds and DB snapshots (`services/annotation_service.py:69`).
4. `/api/annotations/context-news` loads Jin10/Bloomberg news from ±30 minutes around the selected window (`services/annotation_service.py:136`).
5. `POST /api/annotations` upserts a unique `(symbol, window_start, window_end)` annotation row with selected news IDs, no-clear-news flag, notes, and labeler (`services/annotation_service.py:155`).

## Frontend Flow

| Page | API inputs | UI output |
|---|---|---|
| Market | `/api/market/latest`, `/history`, `/table`, `/table.csv`, `/tasks/scan` | asset cards, default 24h cross-asset chart, paginated table, CSV, manual scan banner |
| News | `/api/news` | bilingual filterable feeds with inline detail expansion |
| Predictions | `/api/predictions`, `/predictions/families`, `/predictions/{market_id}/history` | topic charts first, selected single-market detail |
| Alerts | `/api/alerts/rules`, `/alerts/logs`, `/alerts/webhook-status`, `/alerts/test-wechat` | read-only rule table, test button, history |
| Annotations | `/api/annotations/*` | symbol/window selector, price summary, candidate news checklist, save form |
| Onchain | static placeholder plus documented Dune API endpoints | navigation and backend endpoint visibility only |

## High-Risk Fields

| Field | Why it is easy to break |
|---|---|
| `PriceSnapshot.timestamp` | yfinance/OKX use closed 5m bar end; CoinGecko uses collection time; Eastmoney uses quote update time when available (`scanners/base.py:18`). |
| `PriceSnapshot.change_pct` | Source-level 5m/realtime change; price alerts and market API deltas use DB window calculations instead (`alerts/engine.py:158`, `services/market_service.py:65`). |
| `(PriceSnapshot.symbol, PriceSnapshot.timestamp)` | Dedupe and annotation window upsert depend on exact UTC-naive timestamps (`scanners/price_scanner.py:146`, `services/annotation_service.py:155`). |
| `NewsItem.timestamp` | Means source published time, not DB insert time; `created_at` is the insert time (`scanners/news_scanner.py:307`, `models/news.py:27`). |
| `NewsItem.importance` | Jin10 source flag only, stored as 1/0; not the LLM score (`scanners/sources/jin10_source.py:95`). |
| `NewsItem.llm_importance` | Only filled when scoring ran for that scan/backfill; historical backfill defaults to null (`config.py:77`, `scanners/scorer.py:80`). |
| `PredictionMarket.prev_probability` | Filled before alert evaluation and used to avoid comparing a new row to itself (`scanners/prediction_scanner.py:48`, `alerts/engine.py:381`). |
| `MARKET_OVERVIEW_DEFAULT_SYMBOLS` | Shared by market chart defaults and hourly summary (`config.py:86`, `alerts/engine.py:531`, `frontend/src/pages/MarketPage.tsx`). |

## External Integrations

| Service | Module | Auth | Persistence | Failure behavior |
|---|---|---|---|---|
| Yahoo Finance | `scanners/sources/yfinance_source.py` | none | `PriceSnapshot` | logs error/warning and returns `[]` |
| OKX candles via ccxt raw API | `scanners/sources/okx_source.py` | none | `PriceSnapshot` | missing swap falls back to spot; scanner sends missing symbols to CoinGecko |
| CoinGecko simple price | `scanners/sources/coingecko_source.py` | none | `PriceSnapshot` | returns realtime fallback rows or `[]`; 429 is skipped |
| Eastmoney bond quote | `scanners/sources/eastmoney_bond_source.py` | none | `PriceSnapshot` | logs and skips missing quote; spreads require both legs |
| Jin10 flash API | `scanners/sources/jin10_source.py` | custom headers only | `NewsItem` | returns `[]` on request/parse failure |
| Bloomberg RSS | `scanners/sources/rss_source.py` via config | none | `NewsItem` | returns `[]`; no historical paging |
| Polymarket Gamma | `scanners/sources/polymarket/*` | none | `PredictionMarket` | retry once on TLS/connection; invalid slugs yield no rows |
| DeepSeek chat completions | `scanners/scorer.py` | `DEEPSEEK_API_KEY` | LLM fields on `NewsItem` | no key disables scoring; API errors return null scores |
| WeChat Work webhook | `alerts/channels/wechat_work.py` | webhook URL | `AlertLog` | failed/missing send returns `False` and is logged |
| Dune Analytics | `市场监控/dune_queries.py` | `DUNE_API_KEY` and query IDs | in-memory 60m API cache only | API raises unified error through FastAPI |
