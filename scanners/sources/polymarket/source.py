"""Polymarket prediction market source orchestration."""

from loguru import logger

import config
from database import get_session
from models.tracked_market import TrackedMarket
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
        # None 表示 "未指定，运行时查 DB"；测试可以直接赋值 list 来覆盖
        self.tracked_tags: list[str] | None = None
        self.tracked_slugs: list[str] | None = None
        self.discovery_limit = int(config.POLYMARKET.get("discovery_limit", 5))
        self.proxy = config.PROXY
        self.client = client or PolymarketGammaClient(self.gamma_url, self.proxy)
        self.market_filter = market_filter or PolymarketMarketFilter(
            min_volume=float(config.POLYMARKET.get("min_volume", 100_000)),
        )

    def _load_tracked_from_db(self) -> tuple[list[str], list[str]]:
        session = get_session()
        try:
            rows = session.query(TrackedMarket).filter(
                TrackedMarket.enabled.is_(True),
                TrackedMarket.dismissed.is_(False),
            ).all()
            slugs = [r.identifier for r in rows if r.kind == "slug"]
            tags = [r.identifier for r in rows if r.kind == "tag"]
            return slugs, tags
        finally:
            session.close()

    def _resolve_tracked(self) -> tuple[list[str], list[str]]:
        if self.tracked_slugs is None and self.tracked_tags is None:
            return self._load_tracked_from_db()
        return self.tracked_slugs or [], self.tracked_tags or []

    def _is_noise_market(self, market: dict) -> bool:
        return self.market_filter.is_noise_market(market)

    def _search_markets(self, tag: str, limit: int = 10) -> list[dict]:
        try:
            return self.client.search_markets(tag, limit=limit)
        except Exception as e:
            logger.debug(f"Polymarket 搜索 tag={tag} 失败: {e}")
        return []

    def _get_markets_by_slug(self, slug: str) -> list[dict]:
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
        origin: str | None = None,
    ):
        for r in self._parse_market(market):
            key = f"{r.market_id}:{r.outcome}"
            if key in seen_ids:
                continue
            r.origin = origin
            records.append(r)
            seen_ids.add(key)

    def fetch(self) -> list[PredictionRecord]:
        """获取所有跟踪的预测市场最新赔率."""
        slugs, tags = self._resolve_tracked()

        records: list[PredictionRecord] = []
        seen_ids: set[str] = set()

        for slug in slugs:
            for market in self._get_markets_by_slug(slug):
                self._append_market_records(records, seen_ids, market, origin=f"slug:{slug}")

        for tag in tags:
            for market in self._search_markets(tag, limit=self.discovery_limit):
                if self._is_noise_market(market):
                    continue
                self._append_market_records(records, seen_ids, market, origin=f"tag:{tag}")

        logger.info(f"[Polymarket] 获取 {len(records)} 条预测市场记录")
        return records

    def _parse_market(self, market: dict) -> list[PredictionRecord]:
        return parse_market(market)

    def health_check(self) -> bool:
        return self.client.health_check()
