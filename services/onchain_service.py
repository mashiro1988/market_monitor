from __future__ import annotations

import importlib
import time
from typing import Callable

import pandas as pd

from schemas.onchain import OnchainDataset
from services.time_utils import timestamp_pair, utc_now_naive

DUNE_TTL_SECONDS = 60 * 60
_CACHE: dict[str, tuple[float, list[dict], object]] = {}


def _frame_to_rows(df: pd.DataFrame) -> list[dict]:
    rows = df.copy()
    for column in rows.columns:
        if pd.api.types.is_datetime64_any_dtype(rows[column]):
            rows[column] = rows[column].dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            rows[column] = rows[column].map(lambda value: value.isoformat() if hasattr(value, "isoformat") else value)
    return rows.where(pd.notnull(rows), None).to_dict(orient="records")


def _load_fetcher(name: str) -> Callable[[], pd.DataFrame]:
    module = importlib.import_module("市场监控.dune_queries")
    return getattr(module, name)


def _get_dataset(cache_key: str, fetcher_name: str, force_refresh: bool = False) -> OnchainDataset:
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and not force_refresh and now - cached[0] < DUNE_TTL_SECONDS:
        cached_at = cached[2]
        return OnchainDataset(
            name=cache_key,
            cached_at=timestamp_pair(cached_at),
            ttl_seconds=DUNE_TTL_SECONDS,
            rows=cached[1],
        )

    fetcher = _load_fetcher(fetcher_name)
    df = fetcher()
    rows = _frame_to_rows(df)
    cached_at = utc_now_naive()
    _CACHE[cache_key] = (now, rows, cached_at)
    return OnchainDataset(
        name=cache_key,
        cached_at=timestamp_pair(cached_at),
        ttl_seconds=DUNE_TTL_SECONDS,
        rows=rows,
    )


def top100_netflow(force_refresh: bool = False) -> OnchainDataset:
    return _get_dataset("eth_top100_netflow", "fetch_eth_top100_netflow_last30d", force_refresh)


def daily_stats(force_refresh: bool = False) -> OnchainDataset:
    return _get_dataset("eth_daily_stats", "fetch_eth_daily_stats_last30d", force_refresh)


def cex_flows(force_refresh: bool = False) -> OnchainDataset:
    return _get_dataset("eth_cex_flows", "fetch_eth_cex_daily_inout_last30d", force_refresh)
