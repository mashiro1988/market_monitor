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
            "currency": config.PRICE_SOURCES.get("currencies", {}),
            "bond": {
                name: info["symbol"]
                for name, info in config.PRICE_SOURCES.get("bonds", {}).items()
                if info.get("source") == "yfinance"
            },
        }
        # 浏览器指纹会话：绕过 Yahoo 对数据中心 IP 的 TLS 指纹限流（YFRateLimitError）。
        # 本机住宅 IP 不需要，但部署到云服务器（数据中心 IP）必须，否则 yfinance 全 429。
        self._session = self._build_session()

    @staticmethod
    def _build_session():
        """构造 curl_cffi Chrome 指纹会话；不可用时返回 None（yfinance 退回默认会话）。"""
        try:
            from curl_cffi import requests as curl_requests
            return curl_requests.Session(impersonate="chrome")
        except Exception as exc:  # pragma: no cover - 仅依赖缺失时触发
            logger.warning("curl_cffi 浏览器会话不可用，yfinance 退回默认会话: {}", exc)
            return None

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
            return None

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

    def _all_tickers(self) -> dict[str, tuple[str, str]]:
        """symbol -> (asset_class, name)，把所有资产组拍平、合并为一次 yf.download。

        注意：合并**不减少** HTTP 请求数（yf.download 内部对每 ticker 各发一次 chart
        请求，无批量端点）。合并的真实价值是消除「单品种资产组」落入单 ticker 取列
        坏分支的结构性风险（见 _close_series_for），并让全品种共享同一条失败/解析路径。"""
        out: dict[str, tuple[str, str]] = {}
        for asset_class, symbols in self.symbol_groups.items():
            for name, symbol in symbols.items():
                out[symbol] = (asset_class, name)
        return out

    @staticmethod
    def _close_series_for(df: pd.DataFrame, symbol: str) -> pd.Series:
        """从 yf.download 结果取单品种收盘序列，兼容两种列形态。

        yfinance ≥0.2.51 对**列表输入**恒返回 MultiIndex 列——即使只有 1 个 ticker，
        df["Close"] 也是单列 DataFrame 而非 Series。旧代码按 len(ticker_list)==1 特判
        直接拿 DataFrame 当 Series 用：迭代到的是列名而不是时间戳，fetch_history
        静默返回 0 条、fetch 落入未收盘 fallback——currencies 组只有美元指数一个品种，
        这正是它上线后一直无数据的确定性原因之一。"""
        close = df["Close"]
        if isinstance(close, pd.DataFrame):
            return close[symbol]
        return close

    def fetch(self) -> list[PriceRecord]:
        """一次 yf.download 批量拉取所有 yfinance 品种的最新 5m K 线收盘价"""
        records: list[PriceRecord] = []
        tickers = self._all_tickers()
        if not tickers:
            return records
        ticker_list = list(tickers)

        try:
            df = yf.download(
                ticker_list,
                period=self.PERIOD,
                interval=self.INTERVAL,
                prepost=False,
                auto_adjust=True,
                progress=False,
                threads=True,
                session=self._session,
            )
        except Exception as e:
            logger.error(f"yfinance 批量下载失败: {e}")
            return records

        if df.empty:
            logger.warning("yfinance 5m 批量下载返回空数据")
            return records

        for symbol in ticker_list:
            asset_class, name = tickers[symbol]
            try:
                close_series = self._close_series_for(df, symbol)

                picked = self._pick_last_closed(close_series)
                if picked is None:
                    logger.warning(f"{name} ({symbol}) 无已收盘 5m K 线")
                    continue

                end_ts, price, prev_price = picked
                change_pct = (
                    (price - prev_price) / prev_price * 100
                    if prev_price else None
                )

                records.append(PriceRecord(
                    asset_class=asset_class,
                    symbol=symbol,
                    name=name,
                    price=price,
                    prev_price=prev_price,
                    change_pct=change_pct,
                    source=self.name,
                    timestamp=end_ts,
                ))
            except Exception as e:
                logger.error(f"yfinance 解析 {symbol} 失败: {e}")

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
        tickers = self._all_tickers()
        if not tickers:
            return records
        ticker_list = list(tickers)

        try:
            df = yf.download(
                ticker_list,
                period=self.PERIOD,
                interval=self.INTERVAL,
                prepost=False,
                auto_adjust=True,
                progress=False,
                threads=True,
                session=self._session,
            )
        except Exception as e:
            logger.error(f"yfinance 历史批量下载失败: {e}")
            return records

        if df.empty:
            logger.warning("yfinance 历史 5m 批量下载返回空数据")
            return records

        for symbol in ticker_list:
            asset_class, name = tickers[symbol]
            try:
                close_series = self._close_series_for(df, symbol)

                records.extend(self._records_from_close_series(
                    asset_class=asset_class,
                    symbol=symbol,
                    name=name,
                    close_series=close_series,
                    start_ts=start_ts,
                    end_ts=end_ts,
                ))
            except Exception as e:
                logger.error(f"yfinance 历史解析 {symbol} 失败: {e}")

        return records

    def health_check(self) -> bool:
        try:
            t = yf.Ticker("^GSPC", session=self._session)
            info = t.fast_info
            return info is not None
        except Exception:
            return False
