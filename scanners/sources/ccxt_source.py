"""
ccxt 数据源 - 通过 Binance 获取加密货币价格
"""
import ccxt
import pandas as pd
from loguru import logger
from scanners.base import BaseSource, PriceRecord
import config


class CcxtPriceSource(BaseSource):
    """通过 Binance (ccxt) 获取加密货币价格"""

    name = "ccxt"

    def __init__(self):
        self.symbols = config.PRICE_SOURCES.get("crypto", {})
        self.proxy = config.PROXY

    def fetch(self) -> list[PriceRecord]:
        """获取所有加密货币最新价格"""
        records = []

        try:
            ccxt_config = {}
            if self.proxy:
                ccxt_config["proxies"] = {"http": self.proxy, "https": self.proxy}
            exchange = ccxt.binance(ccxt_config)
        except Exception as e:
            logger.error(f"初始化 Binance 交易所失败: {e}")
            return records

        for symbol, binance_symbol in self.symbols.items():
            try:
                params = {
                    "symbol": binance_symbol,
                    "interval": "1h",
                    "limit": 2,  # 只需最近2根K线来计算变化
                }
                response = exchange.fapiPublicGetKlines(params=params)

                if not response or len(response) < 2:
                    logger.warning(f"{symbol} K线数据不足")
                    continue

                # 最新K线和前一根
                latest = response[-1]
                previous = response[-2]

                price = float(latest[4])       # close price
                prev_price = float(previous[4])
                volume = float(latest[5])
                change_pct = ((price - prev_price) / prev_price * 100) if prev_price else 0.0

                records.append(PriceRecord(
                    asset_class="crypto",
                    symbol=f"{symbol}/USDT",
                    name=symbol,
                    price=price,
                    prev_price=prev_price,
                    change_pct=change_pct,
                    volume=volume,
                    source=self.name,
                ))

            except Exception as e:
                logger.error(f"采集 {symbol} 数据失败: {e}")

        return records

    def health_check(self) -> bool:
        try:
            ccxt_config = {}
            if self.proxy:
                ccxt_config["proxies"] = {"http": self.proxy, "https": self.proxy}
            exchange = ccxt.binance(ccxt_config)
            exchange.fapiPublicGetKlines(params={
                "symbol": "BTCUSDT", "interval": "1h", "limit": 1,
            })
            return True
        except Exception:
            return False
