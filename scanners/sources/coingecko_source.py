"""
CoinGecko 数据源 - 加密货币实时价格备选方案

当 OKX 合约/现货 K 线不可用时，使用 CoinGecko 公开 API 获取实时价格。
无需 API key，支持中国直连。
"""
from datetime import datetime, timezone

import requests
from loguru import logger
from scanners.base import BaseSource, PriceRecord
import config

# symbol -> CoinGecko ID 映射
SYMBOL_TO_COINGECKO = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "FET": "fetch-ai",
    "TAO": "bittensor",
    "RNDR": "render-token",
    "WLD": "worldcoin-wld",
    "UNI": "uniswap",
    "ONDO": "ondo-finance",
    "PENDLE": "pendle",
    "1INCH": "1inch",
    "DOGE": "dogecoin",
    "XRP": "ripple",
    "SOL": "solana",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "CFX": "conflux-token",
    "ENS": "ethereum-name-service",
    "AR": "arweave",
    "FIL": "filecoin",
    "ARB": "arbitrum",
    "OP": "optimism",
}


class CoinGeckoPriceSource(BaseSource):
    """CoinGecko 公开 API 加密货币实时价格源"""

    name = "coingecko_realtime"

    def __init__(self):
        self.base_url = "https://api.coingecko.com/api/v3"
        self.proxy = config.PROXY
        # 从 config 中获取要监控的 symbol 列表
        self.symbols = list(config.PRICE_SOURCES.get("crypto", {}).keys())

    def _get_proxies(self):
        return {"http": self.proxy, "https": self.proxy} if self.proxy else {}

    def fetch_symbols(self, symbols: list[str]) -> list[PriceRecord]:
        """批量获取指定加密货币实时价格；timestamp 为本次采集时点。"""
        records = []

        # 构建 CoinGecko IDs 列表
        ids_map = {}  # coingecko_id -> symbol
        for symbol in symbols:
            cg_id = SYMBOL_TO_COINGECKO.get(symbol)
            if cg_id:
                ids_map[cg_id] = symbol

        if not ids_map:
            return records

        # CoinGecko 支持批量查询（一次请求）
        ids_str = ",".join(ids_map.keys())
        url = f"{self.base_url}/simple/price"
        params = {
            "ids": ids_str,
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_24hr_vol": "true",
        }

        try:
            r = requests.get(
                url,
                params=params,
                timeout=15,
                proxies=self._get_proxies(),
                headers={"Accept": "application/json"},
            )

            if r.status_code == 429:
                logger.warning("[CoinGecko] 触发速率限制 (429)，跳过本次采集")
                return records

            r.raise_for_status()
            data = r.json()
            collected_at = datetime.now(timezone.utc).replace(tzinfo=None)
        except Exception as e:
            logger.error(f"[CoinGecko] 请求失败: {e}")
            return records

        for cg_id, symbol in ids_map.items():
            coin_data = data.get(cg_id)
            if not coin_data:
                continue

            price = coin_data.get("usd")
            change_pct = coin_data.get("usd_24h_change")
            volume = coin_data.get("usd_24h_vol")

            if price is None:
                continue

            # 从24h变化百分比反推昨日价格
            prev_price = None
            if change_pct is not None and change_pct != -100:
                prev_price = price / (1 + change_pct / 100)

            records.append(PriceRecord(
                asset_class="crypto",
                symbol=f"{symbol}/USDT",
                name=symbol,
                price=float(price),
                prev_price=float(prev_price) if prev_price else None,
                change_pct=float(change_pct) if change_pct is not None else None,
                volume=float(volume) if volume else None,
                source=self.name,
                timestamp=collected_at,
            ))

        logger.info(f"[CoinGecko] 获取 {len(records)} 个加密货币实时价格，timestamp={collected_at}")
        return records

    def fetch(self) -> list[PriceRecord]:
        """批量获取所有加密货币实时价格"""
        return self.fetch_symbols(self.symbols)

    def health_check(self) -> bool:
        try:
            r = requests.get(
                f"{self.base_url}/ping",
                timeout=10,
                proxies=self._get_proxies(),
            )
            return r.status_code == 200
        except Exception:
            return False
