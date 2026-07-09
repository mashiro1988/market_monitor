"""Filtering rules retained for Polymarket market quality checks."""

NOISE_KEYWORDS = [
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
    # 数字货币预测市场
    "bitcoin", "btc", "ethereum", "eth ", "crypto", "solana", "xrp",
    "doge", "memecoin", "token",
    # 低质量赛事押注
    "who will win the match", "which team will win",
]

MIN_VOLUME = 100_000  # USD

INCLUDE_KEYWORDS = [
    "fed", "fomc", "federal reserve", "interest rate", "rate cut", "rate cuts",
    "bps", "inflation", "cpi", "consumer price index",
    "hormuz", "strait", "shipping", "transit", "portwatch",
    "oil", "crude", "brent", "wti", "opec",
    "iran", "israel", "middle east", "red sea", "houthi", "gaza",
    "war", "ceasefire", "sanction", "geopolitical",
]


class PolymarketMarketFilter:
    """Classifies Gamma markets as relevant or noise."""

    def __init__(
        self,
        min_volume: float = MIN_VOLUME,
        include_keywords: list[str] | None = None,
        noise_keywords: list[str] | None = None,
    ):
        self.min_volume = min_volume
        self.include_keywords = INCLUDE_KEYWORDS if include_keywords is None else include_keywords
        self.noise_keywords = NOISE_KEYWORDS if noise_keywords is None else noise_keywords

    def is_noise_market(self, market: dict) -> bool:
        try:
            volume = float(market.get("volume", 0) or 0)
        except (ValueError, TypeError):
            volume = 0.0

        if volume < self.min_volume:
            return True

        question = (market.get("question", "") or "").lower()
        if any(kw in question for kw in self.noise_keywords):
            return True

        if self.include_keywords and not any(kw in question for kw in self.include_keywords):
            return True

        return False


DEFAULT_MARKET_FILTER = PolymarketMarketFilter()


def is_noise_market(market: dict) -> bool:
    return DEFAULT_MARKET_FILTER.is_noise_market(market)
