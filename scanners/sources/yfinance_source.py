"""
yfinance 数据源 - 股指、期货、商品、部分债券
取最近一根已收盘的 5 分钟 K 线收盘价（非即时报价）。
"""
from datetime import datetime, timedelta, timezone
import pandas as pd
import yfinance as yf
from loguru import logger
from scanners.base import BaseSource, PriceRecord
import config


class YFinancePriceSource(BaseSource):
    """通过 yfinance 获取股指/期货/商品价格（5m K 线收盘价口径）"""

    name = "yfinance"

    # K 线粒度：对齐到"最近已收盘的 5 分钟"
    INTERVAL = "5m"
    PERIOD = "7d"  # 覆盖周末/假期后仍能找到最近的有效 K 线

    def __init__(self):
        self.symbol_groups = {
            "stock_index": config.PRICE_SOURCES.get("us_indices", {}),
            "futures": config.PRICE_SOURCES.get("us_futures", {}),
            "asian_index": config.PRICE_SOURCES.get("asian_indices", {}),
            "commodity": config.PRICE_SOURCES.get("commodities", {}),
            "bond": {
                name: info["symbol"]
                for name, info in config.PRICE_SOURCES.get("bonds", {}).items()
                if info.get("source") == "yfinance"
            },
        }

    @staticmethod
    def _to_utc_naive(ts) -> datetime | None:
        """将 pandas Timestamp 统一为 UTC naive datetime"""
        if ts is None:
            return None
        try:
            ts = pd.Timestamp(ts)
            if ts.tz is not None:
                ts = ts.tz_convert("UTC").tz_localize(None)
            return ts.to_pydatetime()
        except Exception:
            return None

    def _iter_closed_bars(self, close_series: pd.Series) -> list[tuple[datetime, float]]:
        """Return closed 5m bars as (bar_end_utc_naive, close), sorted oldest first."""
        close_series = close_series.dropna()
        if close_series.empty:
            return []

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        bars: list[tuple[datetime, float]] = []
        for idx, value in close_series.items():
            end = self._to_utc_naive(idx)
            if end is None:
                continue
            end = end + timedelta(minutes=5)
            if end <= now:
                bars.append((end, float(value)))
        return bars

    def _pick_last_closed(self, close_series: pd.Series) -> tuple[datetime, float, float | None] | None:
        """
        从 5m K 线序列中挑出"最近一根已收盘"的 bar。
        yfinance K 线以 bar 起始时刻为索引，end = start + 5min。
        只接受 end <= now 的 bar；返回 (end_utc_naive, close, prev_close)。
        """
        close_series = close_series.dropna()
        if close_series.empty:
            return None

        valid_items = self._iter_closed_bars(close_series)

        if not valid_items:
            # fallback: 即使 end > now 也取最后一根（盘中某些品种 bar 未完全同步时）
            idx = close_series.index[-1]
            end = self._to_utc_naive(idx)
            if end is None:
                return None
            return end + timedelta(minutes=5), float(close_series.iloc[-1]), (
                float(close_series.iloc[-2]) if len(close_series) >= 2 else None
            )

        last_end, last_close = valid_items[-1]
        prev_close = valid_items[-2][1] if len(valid_items) >= 2 else None
        return last_end, last_close, prev_close

    def _records_from_close_series(
        self,
        asset_class: str,
        symbol: str,
        name: str,
        close_series: pd.Series,
        start_ts: datetime,
        end_ts: datetime,
    ) -> list[PriceRecord]:
        records: list[PriceRecord] = []
        prev_price: float | None = None

        for bar_end, price in self._iter_closed_bars(close_series):
            current_prev = prev_price
            prev_price = price
            if bar_end < start_ts or bar_end > end_ts:
                continue

            change_pct = (
                (price - current_prev) / current_prev * 100
                if current_prev else None
            )
            records.append(PriceRecord(
                asset_class=asset_class,
                symbol=symbol,
                name=name,
                price=price,
                prev_price=current_prev,
                change_pct=change_pct,
                source=self.name,
                timestamp=bar_end,
            ))

        return records

    def fetch(self) -> list[PriceRecord]:
        """批量拉取所有 yfinance 品种的最新 5m K 线收盘价"""
        records = []

        for asset_class, symbols in self.symbol_groups.items():
            if not symbols:
                continue

            name_map = {}
            ticker_list = []
            for name, symbol in symbols.items():
                name_map[symbol] = name
                ticker_list.append(symbol)

            if not ticker_list:
                continue

            try:
                df = yf.download(
                    ticker_list,
                    period=self.PERIOD,
                    interval=self.INTERVAL,
                    prepost=False,
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                )

                if df.empty:
                    logger.warning(f"yfinance {asset_class} 5m 批量下载返回空数据")
                    continue

                for symbol in ticker_list:
                    try:
                        if len(ticker_list) == 1:
                            close_series = df["Close"]
                        else:
                            close_series = df["Close"][symbol]

                        picked = self._pick_last_closed(close_series)
                        if picked is None:
                            logger.warning(f"{name_map[symbol]} ({symbol}) 无已收盘 5m K 线")
                            continue

                        end_ts, price, prev_price = picked
                        change_pct = (
                            (price - prev_price) / prev_price * 100
                            if prev_price else None
                        )

                        records.append(PriceRecord(
                            asset_class=asset_class,
                            symbol=symbol,
                            name=name_map[symbol],
                            price=price,
                            prev_price=prev_price,
                            change_pct=change_pct,
                            source=self.name,
                            timestamp=end_ts,
                        ))
                    except Exception as e:
                        logger.error(f"yfinance 解析 {symbol} 失败: {e}")

            except Exception as e:
                logger.error(f"yfinance 批量下载 {asset_class} 失败: {e}")

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
        for asset_class, symbols in self.symbol_groups.items():
            if not symbols:
                continue

            name_map = {}
            ticker_list = []
            for name, symbol in symbols.items():
                name_map[symbol] = name
                ticker_list.append(symbol)

            if not ticker_list:
                continue

            try:
                df = yf.download(
                    ticker_list,
                    period=self.PERIOD,
                    interval=self.INTERVAL,
                    prepost=False,
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                )

                if df.empty:
                    logger.warning(f"yfinance {asset_class} 历史 5m 批量下载返回空数据")
                    continue

                for symbol in ticker_list:
                    try:
                        if len(ticker_list) == 1:
                            close_series = df["Close"]
                        else:
                            close_series = df["Close"][symbol]

                        records.extend(self._records_from_close_series(
                            asset_class=asset_class,
                            symbol=symbol,
                            name=name_map[symbol],
                            close_series=close_series,
                            start_ts=start_ts,
                            end_ts=end_ts,
                        ))
                    except Exception as e:
                        logger.error(f"yfinance 历史解析 {symbol} 失败: {e}")

            except Exception as e:
                logger.error(f"yfinance 历史批量下载 {asset_class} 失败: {e}")

        return records

    def health_check(self) -> bool:
        try:
            t = yf.Ticker("^GSPC")
            info = t.fast_info
            return info is not None
        except Exception:
            return False
