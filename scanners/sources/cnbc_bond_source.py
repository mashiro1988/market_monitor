"""
CNBC 债券收益率源 —— 美债/日债 2Y & 10Y 盘中收益率。

一个批量请求覆盖 US2Y/US10Y/JP2Y/JP10Y，盘中实时、无需 key、海外（东京）可达，
替代从境外抓不稳的东方财富。10Y-2Y 利差客户端相减得到。

要点：
- 必须带浏览器 User-Agent，否则 Akamai 返回 200 "Access Denied"。
- 响应路径 FormattedQuoteResult.FormattedQuote[]；每项 last 是带 % 的收益率字符串。
- code==0 才有效（code==1 是无效 symbol）。
- timestamp 留空 → price_scanner 用扫描时间落库，保证每 5m 一行、收盘期也不留空洞
  （与原东方财富源的连续性策略一致）。
"""
from datetime import datetime

import requests
from loguru import logger

import config
from scanners.base import BaseSource, PriceRecord


class CnbcBondQuoteSource(BaseSource):
    """从 CNBC 行情 API 取美/日国债收益率。"""

    name = "cnbc_bond_quote"
    URL = "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
    SPREAD_PAIRS = (
        ("US_SPREAD", "美债利差(10Y-2Y)", "US_10Y", "US_2Y"),
        ("JP_SPREAD", "日债利差(10Y-2Y)", "JP_10Y", "JP_2Y"),
    )

    def __init__(self):
        # 只取 config 里 source=="cnbc" 的债券，建立 CNBC symbol -> (本地 symbol, info) 映射。
        self.bond_quotes = {
            symbol: info
            for symbol, info in config.PRICE_SOURCES.get("bonds", {}).items()
            if info.get("source") == "cnbc" and info.get("cnbc")
        }
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        }

    @staticmethod
    def _parse_yield(value) -> float | None:
        """'4.554%' -> 4.554"""
        if value in (None, "", "-"):
            return None
        try:
            return float(str(value).rstrip("%").strip())
        except (TypeError, ValueError):
            return None

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

    def _request(self, symbols_param: str, timeout: int = 15) -> dict:
        response = requests.get(
            self.URL,
            params={
                "symbols": symbols_param,
                "requestMethod": "itv",
                "noform": "1",
                "output": "json",
            },
            headers=self.headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    def fetch(self) -> list[PriceRecord]:
        records: list[PriceRecord] = []
        if not self.bond_quotes:
            logger.warning("[CnbcBond] 未配置任何 cnbc 债券品种")
            return records

        cnbc_to_symbol = {info["cnbc"]: (symbol, info) for symbol, info in self.bond_quotes.items()}
        symbols_param = "|".join(cnbc_to_symbol.keys())

        try:
            payload = self._request(symbols_param)
        except Exception as e:
            logger.error(f"[CnbcBond] 请求失败: {e}")
            return records

        quotes = (payload.get("FormattedQuoteResult") or {}).get("FormattedQuote") or []
        if isinstance(quotes, dict):  # 单 symbol 时 CNBC 可能返回对象而非数组
            quotes = [quotes]

        for quote in quotes:
            if not isinstance(quote, dict):
                continue
            try:
                if int(quote.get("code", 0)) != 0:  # code==1 是无效 symbol
                    continue
            except (TypeError, ValueError):
                pass
            mapped = cnbc_to_symbol.get(quote.get("symbol"))
            if not mapped:
                continue
            symbol, info = mapped
            price = self._parse_yield(quote.get("last"))
            if price is None:
                logger.warning(f"[CnbcBond] {symbol} ({quote.get('symbol')}) 无 last 收益率")
                continue
            records.append(PriceRecord(
                asset_class="bond",
                symbol=symbol,
                name=info.get("name") or symbol,
                price=price,
                prev_price=None,    # 留空 → price_scanner 用上一条快照算 5m 收益率变化（与其它资产一致）
                change_pct=None,
                source=self.name,
                timestamp=None,     # 留空 → price_scanner 用扫描时间，保证每 5m 一行、收盘期不留空洞
            ))
            logger.info(f"[CnbcBond] {symbol} ({quote.get('symbol')}): {price:.4f}%")

        records.extend(self._build_spread_records(records))
        return records

    def health_check(self) -> bool:
        try:
            if not self.bond_quotes:
                return False
            probe = next(iter(self.bond_quotes.values())).get("cnbc")
            payload = self._request(probe, timeout=10)
            return bool((payload.get("FormattedQuoteResult") or {}).get("FormattedQuote"))
        except Exception:
            return False
