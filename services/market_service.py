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

CLASS_ORDER = ["stock_index", "futures", "asian_index", "bond", "commodity", "crypto"]
CLASS_NAMES = {
    "stock_index": "美股指数",
    "futures": "美股期货",
    "asian_index": "亚洲指数",
    "bond": "债券利率",
    "commodity": "商品",
    "crypto": "加密货币",
}
MARKET_OPEN_NOTES = {
    "stock_index": "北京时间：开盘 21:30，收盘 04:00（T+1，美夏令时）；开盘 22:30，收盘 05:00（T+1，美冬令时）",
    "futures": "北京时间：开盘 06:00，收盘 05:00（T+1，美夏令时）；开盘 07:00，收盘 06:00（T+1，美冬令时）",
    "asian_index": "北京时间：日本/韩国开盘 08:00，收盘 14:30；A股开盘 09:30，收盘 15:00（午休 11:30-13:00）",
    "bond": "北京时间：美债参考开盘 20:00，收盘 05:00（T+1，美夏令时）；开盘 21:00，收盘 06:00（T+1，美冬令时）；日债参考开盘 08:00，收盘 14:30",
    "commodity": "北京时间：开盘 06:00，收盘 05:00（T+1，美夏令时）；开盘 07:00，收盘 06:00（T+1，美冬令时）",
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

    items: list[MarketLatestItem] = []
    for symbol, snaps in by_symbol.items():
        if not snaps:
            continue
        latest = snaps[-1]
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
    cutoff = utc_now_naive() - timedelta(days=max(1, days))
    rows = (
        session.query(PriceSnapshot.symbol, PriceSnapshot.name, PriceSnapshot.asset_class)
        .filter(PriceSnapshot.timestamp >= cutoff)
        .distinct()
        .order_by(PriceSnapshot.asset_class, PriceSnapshot.symbol)
        .all()
    )
    return [MarketSymbol(symbol=row.symbol, name=row.name, asset_class=row.asset_class) for row in rows]


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
