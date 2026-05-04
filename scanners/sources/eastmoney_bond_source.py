"""
Eastmoney bond quote source.

Uses Eastmoney's structured quote endpoint for intraday US/Japan government
bond yields. This source reads quote data directly; it does not parse news.
"""
from datetime import datetime, timezone

import requests
from loguru import logger

import config
from scanners.base import BaseSource, PriceRecord


class EastmoneyBondQuoteSource(BaseSource):
    """Fetch US/Japan government bond yields from Eastmoney quote API."""

    name = "eastmoney_bond_quote"
    URL = "https://push2.eastmoney.com/api/qt/stock/get"
    FIELDS = "f43,f57,f58,f60,f169,f170,f86,f152"
    VALUE_DIVISOR = 10000.0
    CHANGE_PCT_DIVISOR = 100.0
    SPREAD_PAIRS = (
        ("US_SPREAD", "美债利差(10Y-2Y)", "US_10Y", "US_2Y"),
        ("JP_SPREAD", "日债利差(10Y-2Y)", "JP_10Y", "JP_2Y"),
    )

    def __init__(self):
        self.bond_quotes = {
            symbol: info
            for symbol, info in config.PRICE_SOURCES.get("bonds", {}).items()
            if info.get("source") == "eastmoney"
        }
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
        }

    @classmethod
    def _scale_yield(cls, value) -> float | None:
        if value in (None, "-", ""):
            return None
        try:
            return float(value) / cls.VALUE_DIVISOR
        except (TypeError, ValueError):
            return None

    @classmethod
    def _scale_change_pct(cls, value) -> float | None:
        if value in (None, "-", ""):
            return None
        try:
            return float(value) / cls.CHANGE_PCT_DIVISOR
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_timestamp(value) -> datetime | None:
        if value in (None, "-", "", 0):
            return None
        try:
            return datetime.fromtimestamp(int(value), timezone.utc).replace(tzinfo=None)
        except (OSError, TypeError, ValueError):
            return None

    def _parse_quote(self, symbol: str, info: dict, data: dict) -> PriceRecord | None:
        price = self._scale_yield(data.get("f43"))
        if price is None:
            return None

        prev_price = self._scale_yield(data.get("f60"))
        change_pct = self._scale_change_pct(data.get("f170"))
        quote_name = data.get("f58") or info.get("name") or symbol

        return PriceRecord(
            asset_class="bond",
            symbol=symbol,
            name=info.get("name") or quote_name,
            price=price,
            prev_price=prev_price,
            change_pct=change_pct,
            source=self.name,
            timestamp=self._parse_timestamp(data.get("f86")),
        )

    @staticmethod
    def _spread_timestamp(long_record: PriceRecord, short_record: PriceRecord) -> datetime | None:
        if long_record.timestamp and short_record.timestamp:
            return max(long_record.timestamp, short_record.timestamp)
        return long_record.timestamp or short_record.timestamp

    def _build_spread_records(self, records: list[PriceRecord]) -> list[PriceRecord]:
        values = {record.symbol: record for record in records}
        spread_records: list[PriceRecord] = []

        for spread_symbol, spread_name, long_symbol, short_symbol in self.SPREAD_PAIRS:
            if long_symbol not in values or short_symbol not in values:
                continue

            long_record = values[long_symbol]
            short_record = values[short_symbol]
            spread_records.append(PriceRecord(
                asset_class="bond",
                symbol=spread_symbol,
                name=spread_name,
                price=long_record.price - short_record.price,
                source=self.name,
                timestamp=self._spread_timestamp(long_record, short_record),
            ))

        return spread_records

    def fetch(self) -> list[PriceRecord]:
        records: list[PriceRecord] = []
        if not self.bond_quotes:
            logger.warning("[EastmoneyBond] no eastmoney bond quotes configured")
            return records

        proxies = config.proxies()
        for symbol, info in self.bond_quotes.items():
            secid = info.get("secid")
            if not secid:
                continue

            try:
                response = requests.get(
                    self.URL,
                    params={"secid": secid, "fields": self.FIELDS},
                    headers=self.headers,
                    timeout=15,
                    proxies=proxies,
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") or {}
                record = self._parse_quote(symbol, info, data)
                if record is None:
                    logger.warning(f"[EastmoneyBond] {symbol} ({secid}) returned no quote")
                    continue
                records.append(record)
                logger.info(
                    f"[EastmoneyBond] {symbol} ({secid}): {record.price:.4f}%"
                )
            except Exception as e:
                logger.error(f"[EastmoneyBond] fetch {symbol} ({secid}) failed: {e}")

        records.extend(self._build_spread_records(records))
        return records

    def health_check(self) -> bool:
        try:
            probes = list(self.bond_quotes.values())
            if not probes:
                return False
            proxies = config.proxies()
            response = requests.get(
                self.URL,
                params={"secid": probes[0].get("secid"), "fields": self.FIELDS},
                headers=self.headers,
                timeout=10,
                proxies=proxies,
            )
            return response.status_code == 200 and bool(response.json().get("data"))
        except Exception:
            return False
