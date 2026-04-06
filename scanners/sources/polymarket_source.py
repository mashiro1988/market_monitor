"""
Polymarket 数据源 - 预测市场赔率追踪

使用 Polymarket Gamma API（公开、无需认证）获取市场数据。
不依赖 py-clob-client，直接调用 REST API 更可靠。
"""
import requests
from loguru import logger
from scanners.base import BaseSource, PredictionRecord
import config


class PolymarketSource(BaseSource):
    """Polymarket 预测市场数据源"""

    name = "polymarket"

    def __init__(self):
        self.gamma_url = config.POLYMARKET.get("gamma_url", "https://gamma-api.polymarket.com")
        self.tracked_tags = config.POLYMARKET.get("tracked_tags", [])
        self.tracked_slugs = config.POLYMARKET.get("tracked_slugs", [])
        self.proxy = config.PROXY

    _NOISE_KEYWORDS = [
        # 足球/体育联赛
        " fc", "fc ", " sc ", "orlando city", "los angeles fc",
        "over/under", "o/u", "total kills", "total goals",
        "finish in the top", "make the cut",
        "uefa", "champions league", "qualify for the league",
        "serie a", "premier league", "bundesliga", "la liga", "ligue 1",
        "nba", "nfl", "mlb", "nhl", "mls",
        "world cup", "euro 2024", "euro 2025", "euro 2026",
        # 电竞
        "baron nashor", "game 1", "game 2", "kills in",
        # 天气
        "temperature", "degrees", "fahrenheit", "celsius",
        "highest temp", "lowest temp",
        # 娱乐/名人
        "grammy", "oscar", "box office",
        # 低质量赛事押注
        "who will win the match", "which team will win",
    ]

    _MIN_VOLUME = 10_000  # USD

    def _is_noise_market(self, market: dict) -> bool:
        """Return True if this market should be filtered out."""
        try:
            volume = float(market.get("volume", 0) or 0)
        except (ValueError, TypeError):
            volume = 0.0

        if volume < self._MIN_VOLUME:
            return True

        question = (market.get("question", "") or "").lower()
        return any(kw in question for kw in self._NOISE_KEYWORDS)

    def _get_proxies(self):
        return {"http": self.proxy, "https": self.proxy} if self.proxy else {}

    def _search_markets(self, tag: str, limit: int = 10) -> list[dict]:
        """通过 Gamma API 搜索相关市场"""
        try:
            url = f"{self.gamma_url}/markets"
            params = {
                "tag": tag,
                "closed": "false",
                "limit": limit,
                "order": "volume",
                "ascending": "false",
            }
            r = requests.get(url, params=params, timeout=15, proxies=self._get_proxies())
            if r.status_code == 200:
                return r.json() if isinstance(r.json(), list) else []
        except Exception as e:
            logger.debug(f"Polymarket 搜索 tag={tag} 失败: {e}")
        return []

    def _get_market_by_slug(self, slug: str) -> dict | None:
        """通过 slug 获取特定市场"""
        try:
            url = f"{self.gamma_url}/markets"
            params = {"slug": slug}
            r = requests.get(url, params=params, timeout=15, proxies=self._get_proxies())
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and data:
                    return data[0]
        except Exception as e:
            logger.debug(f"Polymarket 获取 slug={slug} 失败: {e}")
        return None

    def fetch(self) -> list[PredictionRecord]:
        """获取所有跟踪的预测市场最新赔率"""
        records = []
        seen_ids: set[str] = set()  # key: "market_id:outcome"

        # 1. 手动指定 slug（核心，不受过滤）
        for slug in self.tracked_slugs:
            market = self._get_market_by_slug(slug)
            if market:
                for r in self._parse_market(market):
                    key = f"{r.market_id}:{r.outcome}"
                    if key not in seen_ids:
                        records.append(r)
                        seen_ids.add(key)

        # 2. tag 搜索（补充，严格过滤）
        for tag in self.tracked_tags:
            for market in self._search_markets(tag, limit=5):
                if self._is_noise_market(market):
                    continue
                for r in self._parse_market(market):
                    key = f"{r.market_id}:{r.outcome}"
                    if key not in seen_ids:
                        records.append(r)
                        seen_ids.add(key)

        logger.info(f"[Polymarket] 获取 {len(records)} 条预测市场记录")
        return records

    def _parse_market(self, market: dict) -> list[PredictionRecord]:
        """解析单个市场的数据"""
        records = []
        try:
            condition_id = market.get("conditionId", market.get("id", ""))
            question = market.get("question", "")

            if not condition_id or not question:
                return records

            # Gamma API 返回的 outcomePrices 是 JSON 字符串
            outcome_prices = market.get("outcomePrices", "")
            outcomes = market.get("outcomes", "")

            if isinstance(outcome_prices, str):
                import json
                try:
                    outcome_prices = json.loads(outcome_prices)
                except (json.JSONDecodeError, TypeError):
                    outcome_prices = []

            if isinstance(outcomes, str):
                import json
                try:
                    outcomes = json.loads(outcomes)
                except (json.JSONDecodeError, TypeError):
                    outcomes = []

            volume = float(market.get("volume", 0) or 0)

            for i, outcome in enumerate(outcomes):
                prob = float(outcome_prices[i]) if i < len(outcome_prices) else 0.0
                records.append(PredictionRecord(
                    market_id=str(condition_id),
                    question=question[:500],
                    outcome=str(outcome),
                    probability=prob,
                    volume=volume,
                ))

        except Exception as e:
            logger.error(f"解析 Polymarket 市场数据失败: {e}")

        return records

    def health_check(self) -> bool:
        try:
            url = f"{self.gamma_url}/markets"
            params = {"limit": 1}
            r = requests.get(url, params=params, timeout=10, proxies=self._get_proxies())
            return r.status_code == 200
        except Exception:
            return False
