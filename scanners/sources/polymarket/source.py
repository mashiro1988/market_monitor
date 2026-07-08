"""Polymarket prediction market source orchestration."""

from loguru import logger

import config
from database import get_session
from models.tracked_market import TrackedMarket
from scanners.base import BaseSource, PredictionRecord
from scanners.sources.polymarket.client import PolymarketGammaClient
from scanners.sources.polymarket.parser import parse_market


class PolymarketSource(BaseSource):
    """Polymarket 预测市场数据源."""

    name = "polymarket"

    def __init__(
        self,
        client: PolymarketGammaClient | None = None,
    ):
        self.gamma_url = config.POLYMARKET.get("gamma_url", "https://gamma-api.polymarket.com")
        # None 表示 "未指定，运行时查 DB"；测试可以直接赋值 list 来覆盖
        self.tracked_slugs: list[str] | None = None
        self.client = client or PolymarketGammaClient(self.gamma_url, config.proxies())

    def _load_tracked_from_db(self) -> list[str]:
        session = get_session()
        try:
            rows = session.query(TrackedMarket).filter(
                TrackedMarket.enabled.is_(True),
                TrackedMarket.dismissed.is_(False),
                TrackedMarket.kind == "slug",
            ).all()
            slugs = [r.identifier for r in rows if r.kind == "slug"]
            return slugs
        finally:
            session.close()

    def _resolve_tracked(self) -> list[str]:
        if self.tracked_slugs is None:
            return self._load_tracked_from_db()
        return self.tracked_slugs or []

    @staticmethod
    def _boolish(value) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes"}:
                return True
            if normalized in {"false", "0", "no"}:
                return False
        return None

    @classmethod
    def _is_closed_or_inactive_market(cls, market: dict) -> bool:
        for field in ("closed", "archived"):
            if cls._boolish(market.get(field)) is True:
                return True
        if cls._boolish(market.get("active")) is False:
            return True
        return False

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
        if self._is_closed_or_inactive_market(market):
            return
        for r in self._parse_market(market):
            key = f"{r.market_id}:{r.outcome}"
            if key in seen_ids:
                continue
            r.origin = origin
            records.append(r)
            seen_ids.add(key)

    def fetch(self) -> list[PredictionRecord]:
        """获取所有跟踪的预测市场最新赔率."""
        slugs = self._resolve_tracked()

        records: list[PredictionRecord] = []
        seen_ids: set[str] = set()

        for slug in slugs:
            for market in self._get_markets_by_slug(slug):
                self._append_market_records(records, seen_ids, market, origin=f"slug:{slug}")

        logger.info(f"[Polymarket] 获取 {len(records)} 条预测市场记录")
        return records

    def _parse_market(self, market: dict) -> list[PredictionRecord]:
        return parse_market(market)

    def health_check(self) -> bool:
        return self.client.health_check()
