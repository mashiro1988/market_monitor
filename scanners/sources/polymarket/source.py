"""Polymarket prediction market source orchestration."""

from loguru import logger

import config
from scanners.base import BaseSource, PredictionRecord
from scanners.sources.polymarket.client import PolymarketGammaClient
from scanners.sources.polymarket.filters import PolymarketMarketFilter
from scanners.sources.polymarket.parser import parse_market


class PolymarketSource(BaseSource):
    """Polymarket 预测市场数据源."""

    name = "polymarket"

    def __init__(
        self,
        client: PolymarketGammaClient | None = None,
        market_filter: PolymarketMarketFilter | None = None,
    ):
        self.gamma_url = config.POLYMARKET.get("gamma_url", "https://gamma-api.polymarket.com")
        self.tracked_tags = config.POLYMARKET.get("tracked_tags", [])
        self.tracked_slugs = config.POLYMARKET.get("tracked_slugs", [])
        self.discovery_limit = int(config.POLYMARKET.get("discovery_limit", 5))
        self.proxy = config.PROXY
        self.client = client or PolymarketGammaClient(self.gamma_url, self.proxy)
        self.market_filter = market_filter or PolymarketMarketFilter(
            min_volume=float(config.POLYMARKET.get("min_volume", 100_000)),
        )

    def _is_noise_market(self, market: dict) -> bool:
        return self.market_filter.is_noise_market(market)

    def _search_markets(self, tag: str, limit: int = 10) -> list[dict]:
        """通过 Gamma API 搜索相关市场."""
        try:
            return self.client.search_markets(tag, limit=limit)
        except Exception as e:
            logger.debug(f"Polymarket 搜索 tag={tag} 失败: {e}")
        return []

    def _get_markets_by_slug(self, slug: str) -> list[dict]:
        """通过 market slug 或 event slug 获取市场。"""
        try:
            return self.client.get_markets_by_slug(slug)
        except Exception as e:
            logger.debug(f"Polymarket 获取 slug={slug} 失败: {e}")
        return []

    def _append_market_records(
        self,
        records: list[PredictionRecord],
        seen_ids: set[str],
        market: dict,
    ):
        for r in self._parse_market(market):
            key = f"{r.market_id}:{r.outcome}"
            if key in seen_ids:
                continue
            records.append(r)
            seen_ids.add(key)

    def fetch(self) -> list[PredictionRecord]:
        """获取所有跟踪的预测市场最新赔率."""
        records = []
        seen_ids: set[str] = set()  # key: "market_id:outcome"

        # 1. 手动指定 slug（核心，不受过滤）
        for slug in self.tracked_slugs:
            for market in self._get_markets_by_slug(slug):
                self._append_market_records(records, seen_ids, market)

        # 2. tag 搜索（补充，严格过滤）
        for tag in self.tracked_tags:
            for market in self._search_markets(tag, limit=self.discovery_limit):
                if self._is_noise_market(market):
                    continue
                self._append_market_records(records, seen_ids, market)

        logger.info(f"[Polymarket] 获取 {len(records)} 条预测市场记录")
        return records

    def _parse_market(self, market: dict) -> list[PredictionRecord]:
        return parse_market(market)

    def health_check(self) -> bool:
        return self.client.health_check()
