from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

import config
from chart_utils import normalize_prices
from models.price import PriceSnapshot
from scanners import market_sessions
from schemas.common import Page, TimeFields
from schemas.market import (
    MarketHistoryPoint,
    MarketHistoryResponse,
    MarketHistorySeries,
    MarketLatestItem,
    MarketLatestResponse,
    MarketSymbol,
    MarketTableRow,
)
from services.pagination import clamp_page, page_count
from services.time_utils import timestamp_pair, utc_now_naive

CLASS_ORDER = ["stock_index", "futures", "perp", "asian_index", "bond", "commodity", "currency", "crypto"]
CLASS_NAMES = {
    "stock_index": "美股指数",
    "futures": "美股期货",
    "perp": "代理永续",
    "asian_index": "亚洲指数",
    "bond": "债券利率",
    "commodity": "商品",
    "currency": "外汇",
    "crypto": "加密货币",
}
MARKET_OPEN_NOTES = {
    "stock_index": "北京时间：开盘 21:30，收盘 04:00（T+1，美夏令时）；开盘 22:30，收盘 05:00（T+1，美冬令时）",
    "futures": "北京时间：开盘 06:00，收盘 05:00（T+1，美夏令时）；开盘 07:00，收盘 06:00（T+1，美冬令时）",
    "perp": "全天 24 小时交易；作为独立行情展示，不用于改写期货价格",
    "asian_index": "北京时间：日本/韩国开盘 08:00，收盘 14:30；A股开盘 09:30，收盘 15:00（午休 11:30-13:00）",
    "bond": "北京时间：美债参考开盘 20:00，收盘 05:00（T+1，美夏令时）；开盘 21:00，收盘 06:00（T+1，美冬令时）；日债参考开盘 08:00，收盘 14:30",
    "commodity": "北京时间：开盘 06:00，收盘 05:00（T+1，美夏令时）；开盘 07:00，收盘 06:00（T+1，美冬令时）",
    "currency": "北京时间：美元指数期货近 23 小时交易，约 05:00-06:00 收盘（随夏/冬令时）",
}


def _change_pct_from_latest(snaps: list[PriceSnapshot], latest_snap: PriceSnapshot, minutes: int) -> float | None:
    tolerances_min = {5: 8, 60: 20, 1440: 240}
    target = latest_snap.timestamp - timedelta(minutes=minutes)
    tolerance = timedelta(minutes=tolerances_min.get(minutes, minutes))
    best = None
    best_delta = None
    for snap in snaps:
        if snap.timestamp >= latest_snap.timestamp or snap.timestamp > target:
            continue
        delta = abs(snap.timestamp - target)
        if delta > tolerance:
            continue
        if best_delta is None or delta < best_delta:
            best = snap
            best_delta = delta
    if best is None or best.price in (None, 0):
        return None
    return (latest_snap.price - best.price) / best.price * 100


def _failed_price_scanner_names() -> set[str]:
    """最近一轮扫描中报错（ok=False）的价格 scanner 名。单 worker 内存态，无扫描历史时为空。"""
    try:
        from services.scan_runtime import run_scan_once
        statuses = getattr(run_scan_once, "last_source_statuses", {}) or {}
        return {s["source"] for s in statuses.get("price", []) if not s.get("ok", True)}
    except Exception:
        return set()


# snapshot.source 前缀 → scanner 状态名（scanners/sources/*.py 的 name 属性）
_SNAPSHOT_SOURCE_TO_SCANNER = (
    ("yfinance", "yfinance"),
    ("okx", "okx"),                      # okx_swap_5m / okx_spot_5m / okx_gapfill*
    ("cnbc_bond_quote", "cnbc_bond_quote"),
)


def _freshness_for(symbol: str, snapshot_source: str, ts: datetime | None,
                   now: datetime, failed_scanners: set[str]) -> tuple[str, int | None]:
    """卡片四态：closed（休市）→ source_down（扫描报错直判）→ live/stale/source_down（按滞后）。"""
    if ts is None:
        return "source_down", None
    if not market_sessions.is_open(symbol, now):
        return "closed", None
    lag_min = max(0, int((now - ts).total_seconds() // 60))
    scanner = next((sc for prefix, sc in _SNAPSHOT_SOURCE_TO_SCANNER
                    if snapshot_source.startswith(prefix)), None)
    if scanner and scanner in failed_scanners:
        return "source_down", lag_min
    if lag_min <= config.FRESHNESS_STALE_MINUTES:
        return "live", None
    if lag_min <= config.FRESHNESS_DOWN_MINUTES:
        return "stale", lag_min
    return "source_down", lag_min


def get_latest_prices(session: Session) -> MarketLatestResponse:
    cutoff = utc_now_naive() - timedelta(days=10)
    snapshots = (
        session.query(PriceSnapshot)
        .filter(PriceSnapshot.timestamp >= cutoff)
        .order_by(PriceSnapshot.timestamp.asc())
        .all()
    )
    by_symbol: dict[str, list[PriceSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        by_symbol[snapshot.symbol].append(snapshot)

    allowed_crypto = {f"{base}/USDT" for base in config.PRICE_SOURCES.get("crypto", {})}
    items: list[MarketLatestItem] = []
    now = utc_now_naive()
    failed_scanners = _failed_price_scanner_names()
    for symbol, snaps in by_symbol.items():
        if not snaps:
            continue
        latest = snaps[-1]
        # 市场概览加密区只显示当前配置的币种（如 BTC/ETH）；已停采的 alt 立刻消失
        if latest.asset_class == "crypto" and symbol not in allowed_crypto:
            continue
        freshness, stale_minutes = _freshness_for(
            symbol, latest.source, latest.timestamp, now, failed_scanners)
        items.append(
            MarketLatestItem(
                name=latest.name,
                symbol=symbol,
                asset_class=latest.asset_class,
                source=latest.source,
                price=latest.price,
                prev_price=latest.prev_price,
                change_pct=latest.change_pct,
                change_5m=_change_pct_from_latest(snaps, latest, 5),
                change_1h=_change_pct_from_latest(snaps, latest, 60),
                change_24h=_change_pct_from_latest(snaps, latest, 1440),
                freshness=freshness,
                stale_minutes=stale_minutes,
                **timestamp_pair(latest.timestamp),
            )
        )
    items.sort(key=lambda item: (CLASS_ORDER.index(item.asset_class) if item.asset_class in CLASS_ORDER else 99, item.symbol))
    latest_ts = max((item.timestamp_utc for item in items if item.timestamp_utc), default=None)
    last_updated: TimeFields | None = None
    if latest_ts:
        last_dt = datetime.fromisoformat(latest_ts)
        last_updated = TimeFields(**timestamp_pair(last_dt))
    return MarketLatestResponse(items=items, last_updated=last_updated)


def get_symbols(session: Session, days: int = 10) -> list[MarketSymbol]:
    """跨资产走势的可选品种，与市场概览（get_latest_prices）同口径：
    近 N 天有快照 + 加密只留当前配置币种（已停采 alt 同步消失）；
    按每个 symbol 最新一条快照取 name/asset_class（历史改名/换源不产生重复选项）；
    排序同概览（CLASS_ORDER 优先级 + symbol）。"""
    cutoff = utc_now_naive() - timedelta(days=max(1, days))
    rows = (
        session.query(PriceSnapshot.symbol, PriceSnapshot.name, PriceSnapshot.asset_class, PriceSnapshot.timestamp)
        .filter(PriceSnapshot.timestamp >= cutoff)
        .order_by(PriceSnapshot.timestamp.asc())
        .all()
    )
    latest_by_symbol: dict[str, tuple[str, str]] = {}
    for row in rows:
        latest_by_symbol[row.symbol] = (row.name, row.asset_class)   # 升序遍历 → 留最新

    allowed_crypto = {f"{base}/USDT" for base in config.PRICE_SOURCES.get("crypto", {})}
    items = [
        MarketSymbol(symbol=symbol, name=name, asset_class=asset_class)
        for symbol, (name, asset_class) in latest_by_symbol.items()
        if not (asset_class == "crypto" and symbol not in allowed_crypto)
    ]
    items.sort(key=lambda s: (CLASS_ORDER.index(s.asset_class) if s.asset_class in CLASS_ORDER else 99, s.symbol))
    return items


def _window_baseline_prices(
    session: Session, symbols: list[str], start: datetime, lookback_days: int
) -> dict[str, float]:
    """每个 symbol 在窗口起点 start 当时的基准价 = timestamp ≤ start 的最后一笔收盘。
    用于「跨资产走势」按窗口起点锚定净值，保留隔夜跳空。无前置数据的 symbol 不入字典。"""
    if not symbols:
        return {}
    lookback_start = start - timedelta(days=lookback_days)
    rows = (
        session.query(PriceSnapshot.symbol, PriceSnapshot.timestamp, PriceSnapshot.price)
        .filter(
            PriceSnapshot.symbol.in_(symbols),
            PriceSnapshot.timestamp >= lookback_start,
            PriceSnapshot.timestamp <= start,
        )
        .order_by(PriceSnapshot.timestamp.asc())
        .all()
    )
    baseline: dict[str, float] = {}
    for row in rows:
        if row.price:
            baseline[row.symbol] = row.price  # asc 遍历，最后写入的是 ≤start 最近一笔
    return baseline


def get_history(
    session: Session,
    symbols: list[str] | None = None,
    hours: int = 24,
    start: datetime | None = None,
    end: datetime | None = None,
) -> MarketHistoryResponse:
    now = utc_now_naive()
    end = end or now
    if start is None:
        hours = max(1, min(int(hours or 24), 24 * 30))
        start = end - timedelta(hours=hours)
    elif end - start > timedelta(days=30):
        start = end - timedelta(days=30)

    query = (
        session.query(
            PriceSnapshot.timestamp,
            PriceSnapshot.symbol,
            PriceSnapshot.name,
            PriceSnapshot.asset_class,
            PriceSnapshot.price,
            PriceSnapshot.source,
        )
        .filter(PriceSnapshot.timestamp >= start, PriceSnapshot.timestamp <= end)
        .order_by(PriceSnapshot.timestamp.asc())
    )
    if symbols:
        query = query.filter(PriceSnapshot.symbol.in_(symbols))
    rows = query.all()

    grouped: dict[str, dict] = {}
    for row in rows:
        bucket = grouped.setdefault(
            row.symbol,
            {"name": row.name, "asset_class": row.asset_class, "rows": []},
        )
        bucket["rows"].append(row)

    baselines = _window_baseline_prices(
        session, list(grouped.keys()), start, config.MARKET_HISTORY_BASELINE_LOOKBACK_DAYS
    )

    series: list[MarketHistorySeries] = []
    for symbol, bucket in grouped.items():
        prices = [row.price for row in bucket["rows"]]
        normalized = normalize_prices(prices, base=baselines.get(symbol)) if len(prices) >= 1 else []
        points = [
            MarketHistoryPoint(
                symbol=row.symbol,
                name=row.name,
                price=row.price,
                normalized_pct=normalized[index] if index < len(normalized) else None,
                source=row.source,
                **timestamp_pair(row.timestamp),
            )
            for index, row in enumerate(bucket["rows"])
        ]
        series.append(
            MarketHistorySeries(
                symbol=symbol,
                name=bucket["name"],
                asset_class=bucket["asset_class"],
                points=points,
            )
        )
    series.sort(key=lambda item: item.symbol)
    return MarketHistoryResponse(
        symbols=[item.symbol for item in series],
        start=timestamp_pair(start),
        end=timestamp_pair(end),
        series=series,
    )


def _table_query(
    session: Session,
    hours: int,
    asset_classes: list[str] | None,
    symbols: list[str] | None,
):
    hours = max(1, min(int(hours or 24), 24 * 30))
    cutoff = utc_now_naive() - timedelta(hours=hours)
    query = session.query(PriceSnapshot).filter(PriceSnapshot.timestamp >= cutoff)
    if asset_classes:
        query = query.filter(PriceSnapshot.asset_class.in_(asset_classes))
    if symbols:
        query = query.filter(PriceSnapshot.symbol.in_(symbols))
    return query.order_by(PriceSnapshot.timestamp.desc(), PriceSnapshot.asset_class.asc(), PriceSnapshot.name.asc())


def _table_row(row: PriceSnapshot) -> MarketTableRow:
    return MarketTableRow(
        asset_class=row.asset_class,
        name=row.name,
        symbol=row.symbol,
        price=row.price,
        prev_price=row.prev_price,
        change_pct=row.change_pct,
        volume=row.volume,
        source=row.source,
        **timestamp_pair(row.timestamp),
    )


def get_table(
    session: Session,
    hours: int = 24,
    asset_classes: list[str] | None = None,
    symbols: list[str] | None = None,
    page: int = 1,
    page_size: int = 50,
) -> Page[MarketTableRow]:
    page, page_size = clamp_page(page, page_size)
    query = _table_query(session, hours, asset_classes, symbols)
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    return Page[MarketTableRow](
        items=[_table_row(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=page_count(total, page_size),
    )


def get_table_csv(
    session: Session,
    hours: int = 24,
    asset_classes: list[str] | None = None,
    symbols: list[str] | None = None,
) -> bytes:
    rows = _table_query(session, hours, asset_classes, symbols).limit(100_000).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["北京时间", "UTC时间", "资产类别", "名称", "品种", "价格", "前值", "涨跌幅", "成交量", "来源"])
    for row in rows:
        times = timestamp_pair(row.timestamp)
        writer.writerow([
            times["timestamp_bj"],
            times["timestamp_utc"],
            row.asset_class,
            row.name,
            row.symbol,
            row.price,
            row.prev_price,
            row.change_pct,
            row.volume,
            row.source,
        ])
    return output.getvalue().encode("utf-8-sig")


def status_snapshot(session: Session) -> dict:
    latest_price = session.query(func.max(PriceSnapshot.timestamp)).scalar()
    counts = session.query(PriceSnapshot.asset_class, func.count(PriceSnapshot.id)).group_by(PriceSnapshot.asset_class).all()
    return {
        "latest_price": timestamp_pair(latest_price),
        "price_counts_by_class": {row[0]: row[1] for row in counts},
        "default_symbols": config.MARKET_OVERVIEW_DEFAULT_SYMBOLS,
        "class_names": CLASS_NAMES,
        "market_open_notes": MARKET_OPEN_NOTES,
    }
