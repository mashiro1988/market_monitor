"""HTTP client for Polymarket Gamma market data."""

import time

import requests


class PolymarketGammaClient:
    """Small Gamma API wrapper used by the Polymarket source."""

    def __init__(self, gamma_url: str, proxy: str = ""):
        self.gamma_url = gamma_url.rstrip("/")
        self.proxy = proxy

    def get_proxies(self) -> dict[str, str]:
        return {"http": self.proxy, "https": self.proxy} if self.proxy else {}

    def request_with_retry(self, url: str, params: dict, attempts: int = 2):
        """GET with one retry on transient network/TLS errors."""
        last_err: Exception | None = None
        for i in range(attempts):
            try:
                return requests.get(
                    url,
                    params=params,
                    timeout=15,
                    proxies=self.get_proxies(),
                )
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
                last_err = e
                if i < attempts - 1:
                    time.sleep(1.5)
        if last_err:
            raise last_err
        return None

    def _markets_from_response(self, response) -> list[dict]:
        if response and response.status_code == 200:
            data = response.json()
            return data if isinstance(data, list) else []
        return []

    def search_markets(self, tag: str, limit: int = 10) -> list[dict]:
        url = f"{self.gamma_url}/markets"
        params = {
            "tag": tag,
            "closed": "false",
            "limit": limit,
            "order": "volume",
            "ascending": "false",
        }
        return self._markets_from_response(self.request_with_retry(url, params))

    def get_market_by_slug(self, slug: str) -> dict | None:
        url = f"{self.gamma_url}/markets"
        params = {"slug": slug}
        markets = self._markets_from_response(self.request_with_retry(url, params))
        return markets[0] if markets else None

    def get_event_markets_by_slug(self, slug: str) -> list[dict]:
        url = f"{self.gamma_url}/events/slug/{slug}"
        response = self.request_with_retry(url, {})
        if response and response.status_code == 200:
            data = response.json()
            markets = data.get("markets", []) if isinstance(data, dict) else []
            return markets if isinstance(markets, list) else []
        return []

    def get_markets_by_slug(self, slug: str) -> list[dict]:
        market = self.get_market_by_slug(slug)
        if market:
            return [market]
        return self.get_event_markets_by_slug(slug)

    def health_check(self) -> bool:
        try:
            url = f"{self.gamma_url}/markets"
            params = {"limit": 1}
            r = requests.get(
                url,
                params=params,
                timeout=10,
                proxies=self.get_proxies(),
            )
            return r.status_code == 200
        except Exception:
            return False
