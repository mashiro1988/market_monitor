"""
yfinance 数据源 - 股指、期货、商品、部分债券
"""
import yfinance as yf
from loguru import logger
from scanners.base import BaseSource, PriceRecord
import config


class YFinancePriceSource(BaseSource):
    """通过 yfinance 获取股指/期货/商品价格"""

    name = "yfinance"

    def __init__(self):
        # 从 config 中构建 symbol 列表，按 asset_class 分组
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

    def fetch(self) -> list[PriceRecord]:
        """批量拉取所有 yfinance 品种的最新价格"""
        records = []

        for asset_class, symbols in self.symbol_groups.items():
            if not symbols:
                continue

            # 构建 symbol -> name 映射
            name_map = {}
            ticker_list = []
            for name, symbol in symbols.items():
                name_map[symbol] = name
                ticker_list.append(symbol)

            if not ticker_list:
                continue

            try:
                # 批量下载减少请求次数
                df = yf.download(
                    ticker_list,
                    period="2d",
                    interval="1d",
                    prepost=False,
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                )

                if df.empty:
                    logger.warning(f"yfinance {asset_class} 批量下载返回空数据")
                    continue

                for symbol in ticker_list:
                    try:
                        # 多 symbol 时 df 有 MultiIndex columns
                        if len(ticker_list) == 1:
                            close_series = df["Close"]
                        else:
                            close_series = df["Close"][symbol]

                        close_series = close_series.dropna()
                        if len(close_series) < 1:
                            logger.warning(f"{name_map[symbol]} ({symbol}) 无有效数据")
                            continue

                        price = float(close_series.iloc[-1])
                        prev_price = float(close_series.iloc[-2]) if len(close_series) >= 2 else None
                        change_pct = ((price - prev_price) / prev_price * 100) if prev_price else None

                        records.append(PriceRecord(
                            asset_class=asset_class,
                            symbol=symbol,
                            name=name_map[symbol],
                            price=price,
                            prev_price=prev_price,
                            change_pct=change_pct,
                            source=self.name,
                        ))
                    except Exception as e:
                        logger.error(f"yfinance 解析 {symbol} 失败: {e}")

            except Exception as e:
                logger.error(f"yfinance 批量下载 {asset_class} 失败: {e}")

        return records

    def health_check(self) -> bool:
        try:
            t = yf.Ticker("^GSPC")
            info = t.fast_info
            return info is not None
        except Exception:
            return False
