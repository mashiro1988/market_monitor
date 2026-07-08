"""
OKX 数据源 - 通过 ccxt raw API 获取加密货币价格

不调用 load_markets()，直接使用 OKX instId 拉 5 分钟 K 线：
1. 优先 USDT 永续合约：BTC-USDT-SWAP
2. 合约不存在时补 OKX 现货：BTC-USDT
"""
from datetime import datetime, timezone
from typing import NamedTuple
import time

import ccxt
from loguru import logger

from scanners.base import BaseSource, PriceRecord
import config


class PerpBar(NamedTuple):
    bar_end: datetime   # UTC naive，5m bar 收盘时刻
    close: float


class OkxPriceSource(BaseSource):
    """通过 OKX 获取加密货币价格（5m K 线收盘价口径）"""

    name = "okx"
    INTERVAL_MS = 5 * 60 * 1000
    TIMEOUT_MS = 15_000

    def __init__(self):
        self.symbols = config.PRICE_SOURCES.get("crypto", {})
        self.proxy = config.proxy_url()

    def _make_exchange(self):
        exchange = ccxt.okx({
            "enableRateLimit": True,
            "timeout": self.TIMEOUT_MS,
        })
        if self.proxy:
            exchange.httpsProxy = self.proxy
            logger.info(f"[OKX] 使用代理(httpsProxy): {self.proxy}")
        else:
            logger.info("[OKX] 未配置代理（config.PROXY 为空 / 探测未通）")
        return exchange

    @staticmethod
    def _bases(display_symbol: str, configured_symbol: str) -> list[str]:
        base = (
            configured_symbol[:-4]
            if configured_symbol.endswith("USDT")
            else display_symbol
        )
        bases = [base]
        if display_symbol != base:
            bases.append(display_symbol)
        return bases

    def _candidate_inst_ids(self, display_symbol: str, configured_symbol: str, market_type: str) -> list[str]:
        suffix = "-USDT-SWAP" if market_type == "swap" else "-USDT"
        return [f"{base}{suffix}" for base in self._bases(display_symbol, configured_symbol)]

    @staticmethod
    def _is_missing_instrument(exc: Exception) -> bool:
        msg = str(exc)
        return isinstance(exc, ccxt.BadSymbol) or "51001" in msg or "doesn't exist" in msg

    def _fetch_candles(self, exchange, inst_id: str, limit: int = 5, after_ms: int | None = None) -> list:
        params = {
            "instId": inst_id,
            "bar": "5m",
            "limit": str(limit),
        }
        if after_ms is not None:
            params["after"] = str(after_ms)
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = exchange.publicGetMarketCandles(params)
                return response.get("data", []) if isinstance(response, dict) else []
            except (
                ccxt.RequestTimeout,
                ccxt.NetworkError,
                ccxt.ExchangeNotAvailable,
                ccxt.DDoSProtection,
            ) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(1)
                    continue
                raise
        if last_error:
            raise last_error
        return []

    def _closed_candle_points(self, candles: list) -> list[tuple[int, datetime, float, float | None]]:
        """
        Return closed OKX candles as (start_ms, bar_end_utc_naive, close, volume).
        The API returns newest first; this helper preserves that order.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        points: list[tuple[int, datetime, float, float | None]] = []
        for candle in candles:
            if len(candle) < 6:
                continue
            start_ms = int(candle[0])
            confirm = str(candle[8]) if len(candle) > 8 else ""
            end_ms = start_ms + self.INTERVAL_MS
            if confirm != "1" and end_ms > now_ms:
                continue

            bar_end = datetime.fromtimestamp(
                end_ms / 1000,
                timezone.utc,
            ).replace(tzinfo=None)
            price = float(candle[4])
            volume = float(candle[5]) if candle[5] not in (None, "") else None
            points.append((start_ms, bar_end, price, volume))
        return points

    def _pick_last_closed(self, candles: list) -> tuple[datetime, float, float | None, float | None] | None:
        """
        OKX candles are newest first:
        [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm].
        ts is bar start; timestamp stored in DB is bar end.
        """
        closed = self._closed_candle_points(candles)
        if not closed:
            return None

        latest = closed[0]
        previous = closed[1] if len(closed) > 1 else None

        bar_end = latest[1]
        price = latest[2]
        prev_price = previous[2] if previous else None
        volume = latest[3]
        return bar_end, price, prev_price, volume

    def _fetch_one(self, exchange, display_symbol: str, configured_symbol: str) -> PriceRecord | None:
        for inst_id in self._candidate_inst_ids(display_symbol, configured_symbol, "swap"):
            try:
                picked = self._pick_last_closed(self._fetch_candles(exchange, inst_id))
                if picked:
                    return self._make_record(display_symbol, picked, "okx_swap_5m")
                logger.warning(f"[OKX] {display_symbol}({inst_id}) 合约 5m K线数据为空")
            except Exception as e:
                if self._is_missing_instrument(e):
                    logger.info(f"[OKX] {display_symbol} 无合约 {inst_id}，尝试现货")
                    continue
                logger.error(f"[OKX] 采集合约 {display_symbol}({inst_id}) 失败: {type(e).__name__}: {e}")
                continue

        for inst_id in self._candidate_inst_ids(display_symbol, configured_symbol, "spot"):
            try:
                picked = self._pick_last_closed(self._fetch_candles(exchange, inst_id))
                if picked:
                    return self._make_record(display_symbol, picked, "okx_spot_5m")
                logger.warning(f"[OKX] {display_symbol}({inst_id}) 现货 5m K线数据为空")
            except Exception as e:
                if self._is_missing_instrument(e):
                    logger.info(f"[OKX] {display_symbol} 无现货 {inst_id}")
                    continue
                logger.error(f"[OKX] 采集现货 {display_symbol}({inst_id}) 失败: {type(e).__name__}: {e}")
                return None

        logger.warning(f"[OKX] {display_symbol} 合约/现货均不可用")
        return None

    def _fetch_history_for_inst(
        self,
        exchange,
        display_symbol: str,
        inst_id: str,
        source: str,
        start_ts: datetime,
        end_ts: datetime,
    ) -> list[PriceRecord]:
        """Fetch closed 5m candles for one OKX instrument within [start_ts, end_ts]."""
        points_by_start: dict[int, tuple[datetime, float, float | None]] = {}
        cursor_after: int | None = None
        start_floor_ms = int((start_ts.replace(tzinfo=timezone.utc).timestamp() * 1000) - self.INTERVAL_MS)

        for _ in range(8):  # 8 * 300 * 5m = 100h，覆盖 72h 并留出余量
            candles = self._fetch_candles(exchange, inst_id, limit=300, after_ms=cursor_after)
            points = self._closed_candle_points(candles)
            if not points:
                break

            for start_ms, bar_end, price, volume in points:
                if start_ts <= bar_end <= end_ts:
                    points_by_start[start_ms] = (bar_end, price, volume)

            oldest_start = min(point[0] for point in points)
            if oldest_start <= start_floor_ms:
                break
            if cursor_after is not None and oldest_start >= cursor_after:
                logger.warning(f"[OKX] {display_symbol}({inst_id}) 历史分页未前进，停止回补")
                break
            cursor_after = oldest_start

        ordered = sorted(points_by_start.values(), key=lambda item: item[0])
        records: list[PriceRecord] = []
        prev_price: float | None = None
        for bar_end, price, volume in ordered:
            current_prev = prev_price
            records.append(self._make_record(
                display_symbol,
                (bar_end, price, current_prev, volume),
                source,
            ))
            prev_price = price
        return records

    def _fetch_history_one(
        self,
        exchange,
        display_symbol: str,
        configured_symbol: str,
        start_ts: datetime,
        end_ts: datetime,
    ) -> list[PriceRecord]:
        for inst_id in self._candidate_inst_ids(display_symbol, configured_symbol, "swap"):
            try:
                records = self._fetch_history_for_inst(
                    exchange, display_symbol, inst_id, "okx_swap_5m", start_ts, end_ts
                )
                if records:
                    return records
                logger.warning(f"[OKX] {display_symbol}({inst_id}) 合约历史 5m K线数据为空")
            except Exception as e:
                if self._is_missing_instrument(e):
                    logger.info(f"[OKX] {display_symbol} 无合约 {inst_id}，尝试现货历史")
                    continue
                logger.error(f"[OKX] 采集合约历史 {display_symbol}({inst_id}) 失败: {type(e).__name__}: {e}")
                continue

        for inst_id in self._candidate_inst_ids(display_symbol, configured_symbol, "spot"):
            try:
                records = self._fetch_history_for_inst(
                    exchange, display_symbol, inst_id, "okx_spot_5m", start_ts, end_ts
                )
                if records:
                    return records
                logger.warning(f"[OKX] {display_symbol}({inst_id}) 现货历史 5m K线数据为空")
            except Exception as e:
                if self._is_missing_instrument(e):
                    logger.info(f"[OKX] {display_symbol} 无现货 {inst_id}")
                    continue
                logger.error(f"[OKX] 采集现货历史 {display_symbol}({inst_id}) 失败: {type(e).__name__}: {e}")
                return []

        logger.warning(f"[OKX] {display_symbol} 合约/现货历史均不可用")
        return []

    def _make_record(self, symbol: str, picked: tuple[datetime, float, float | None, float | None], source: str) -> PriceRecord:
        bar_end, price, prev_price, volume = picked
        change_pct = ((price - prev_price) / prev_price * 100) if prev_price else None
        return PriceRecord(
            asset_class="crypto",
            symbol=f"{symbol}/USDT",
            name=symbol,
            price=price,
            prev_price=prev_price,
            change_pct=change_pct,
            volume=volume,
            source=source,
            timestamp=bar_end,
        )

    def fetch(self) -> list[PriceRecord]:
        records = []
        try:
            exchange = self._make_exchange()
        except Exception as e:
            logger.error(f"[OKX] 初始化交易所失败: {type(e).__name__}: {e}")
            return records

        for symbol, configured_symbol in self.symbols.items():
            record = self._fetch_one(exchange, symbol, configured_symbol)
            if record:
                records.append(record)

        if not records:
            logger.warning("[OKX] 本轮未产出任何记录")
        return records

    def fetch_history(self, start_ts: datetime, end_ts: datetime) -> list[PriceRecord]:
        """批量拉取时间窗内的历史 5m K 线收盘价，用于中断后回补。"""
        if start_ts.tzinfo is not None:
            start_ts = start_ts.astimezone(timezone.utc).replace(tzinfo=None)
        if end_ts.tzinfo is not None:
            end_ts = end_ts.astimezone(timezone.utc).replace(tzinfo=None)
        if start_ts >= end_ts:
            return []

        records: list[PriceRecord] = []
        try:
            exchange = self._make_exchange()
        except Exception as e:
            logger.error(f"[OKX] 初始化交易所失败: {type(e).__name__}: {e}")
            return records

        for symbol, configured_symbol in self.symbols.items():
            records.extend(self._fetch_history_one(exchange, symbol, configured_symbol, start_ts, end_ts))

        if not records:
            logger.warning("[OKX] 历史回补未产出任何记录")
        return records

    def fetch_instrument_bars(self, inst_ids: list[str], limit: int = 12) -> dict[str, list[PerpBar]]:
        """取若干 instId 的已收盘 5m bar（升序）。供 GapFiller 用；返回原始 (bar_end, close)，
        不构造 crypto PriceRecord。一次建 exchange、循环复用。"""
        out: dict[str, list[PerpBar]] = {inst: [] for inst in inst_ids}
        try:
            exchange = self._make_exchange()
        except Exception as e:
            logger.error(f"[OKX] gapfill 初始化交易所失败: {type(e).__name__}: {e}")
            return out
        for inst_id in inst_ids:
            try:
                candles = self._fetch_candles(exchange, inst_id, limit=limit)
                pts = self._closed_candle_points(candles)   # (start_ms, bar_end, close, vol)，newest-first
                out[inst_id] = sorted(
                    (PerpBar(bar_end=p[1], close=p[2]) for p in pts),
                    key=lambda b: b.bar_end,
                )
            except Exception as e:
                logger.error(f"[OKX] gapfill 取 {inst_id} 失败: {type(e).__name__}: {e}")
                out[inst_id] = []
        return out

    def health_check(self) -> bool:
        try:
            exchange = self._make_exchange()
            exchange.publicGetPublicTime()
            return True
        except Exception as e:
            logger.warning(f"[OKX] health_check 失败: {type(e).__name__}: {e}")
            return False
