"""
yfinance 数据源 - 股指、期货、商品、部分债券
取最近一根已收盘的 5 分钟 K 线收盘价（非即时报价）。
"""
import random
import time as _time
from datetime import datetime, timedelta, timezone
import pandas as pd
import yfinance as yf
from loguru import logger
from scanners import market_sessions
from scanners.base import BaseSource, PriceRecord
import config

_sleep = _time.sleep          # 测试可注入
_monotonic = _time.monotonic  # 测试可注入


class YFinancePriceSource(BaseSource):
    """通过 yfinance 获取股指/期货/商品价格（5m K 线收盘价口径）"""

    name = "yfinance"

    # K 线粒度：对齐到"最近已收盘的 5 分钟"
    INTERVAL = "5m"
    # 同步窗口封顶（小时）：yahoo 5m 数据可得范围内留裕量；窗口起点由 PriceScanner 按游标公式计算。
    CAP_HOURS = 168

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
        """symbol -> (asset_class, name)，把所有资产组拍平为统一清单。

        2026-07-22 起 fetch_history 逐品种串行下载（治本改造），但清单仍统一维护：
        全品种共享同一条解析路径（_close_series_for 处理单 ticker MultiIndex 列形态）。"""
        out: dict[str, tuple[str, str]] = {}
        for asset_class, symbols in self.symbol_groups.items():
            for name, symbol in symbols.items():
                out[symbol] = (asset_class, name)
        return out

    def active_tickers(self, now_utc: datetime) -> dict[str, tuple[str, str]]:
        """本轮应拉取的 symbol -> (asset_class, name)；供 fetch_history 与 PriceScanner 共用。"""
        return {s: meta for s, meta in self._all_tickers().items()
                if market_sessions.should_fetch(s, now_utc)}

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

    def fetch_history(self, start_ts: datetime, end_ts: datetime) -> list[PriceRecord]:
        """逐品种串行拉取窗口内 5m 收盘价：会话过滤 + 抖动 + 单请求超时 + 阶段软预算。

        2026-07-22 治本改造：原 16 ticker 单次并发批量（threads=True）是 Yahoo 封 IP 的
        直接诱因；改串行后每轮只拉开市品种，全休市轮零请求，超软预算的品种交给
        下一轮游标窗口自愈。"""
        if start_ts.tzinfo is not None:
            start_ts = start_ts.astimezone(timezone.utc).replace(tzinfo=None)
        if end_ts.tzinfo is not None:
            end_ts = end_ts.astimezone(timezone.utc).replace(tzinfo=None)
        if start_ts >= end_ts:
            return []

        tickers = self.active_tickers(end_ts)
        if not tickers:
            return []

        records: list[PriceRecord] = []
        deadline = _monotonic() + config.YF_STAGE_BUDGET_SEC
        skipped: list[str] = []
        items = list(tickers.items())
        for i, (symbol, (asset_class, name)) in enumerate(items):
            if _monotonic() >= deadline:
                skipped = [s for s, _ in items[i:]]
                break
            try:
                # yfinance 对 naive datetime 按本地时区解释，必须传 tz-aware UTC（游标同步 2026-07-14）
                df = yf.download(
                    [symbol],
                    start=start_ts.replace(tzinfo=timezone.utc),
                    end=end_ts.replace(tzinfo=timezone.utc),
                    interval=self.INTERVAL,
                    prepost=False,
                    auto_adjust=True,
                    progress=False,
                    threads=False,
                    session=self._session,
                    timeout=config.YF_REQUEST_TIMEOUT_SEC,
                )
                if df.empty:
                    continue
                close_series = self._close_series_for(df, symbol)
                records.extend(self._records_from_close_series(
                    asset_class=asset_class, symbol=symbol, name=name,
                    close_series=close_series, start_ts=start_ts, end_ts=end_ts))
            except Exception as e:
                logger.error(f"yfinance {symbol} 拉取失败: {type(e).__name__}: {e}")
            if i < len(items) - 1:
                _sleep(random.uniform(config.YF_JITTER_MIN_SEC, config.YF_JITTER_MAX_SEC))

        if skipped:
            logger.warning(f"yfinance 阶段超软预算({config.YF_STAGE_BUDGET_SEC}s)，"
                           f"本轮放弃 {len(skipped)} 品种: {', '.join(skipped)}（下一轮游标窗口自愈）")
        return records

    def health_check(self) -> bool:
        try:
            t = yf.Ticker("^GSPC", session=self._session)
            info = t.fast_info
            return info is not None
        except Exception:
            return False
