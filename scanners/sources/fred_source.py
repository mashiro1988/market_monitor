"""
FRED 数据源 - 美国国债利率和经济数据
"""
from loguru import logger
from fredapi import Fred
from scanners.base import BaseSource, PriceRecord
import config


class FredBondSource(BaseSource):
    """通过 FRED API 获取美国国债利率"""

    name = "fred"

    def __init__(self):
        self.api_key = config.FRED_API_KEY
        self.bond_series = {
            name: info
            for name, info in config.PRICE_SOURCES.get("bonds", {}).items()
            if info.get("source") == "fred"
        }

    def fetch(self) -> list[PriceRecord]:
        """获取美债利率最新值"""
        records = []

        if not self.api_key:
            logger.warning("未配置 FRED API 密钥，跳过美债数据")
            return records

        try:
            fred = Fred(api_key=self.api_key)
        except Exception as e:
            logger.error(f"初始化 FRED 失败: {e}")
            return records

        rate_values = {}

        for name, info in self.bond_series.items():
            series_id = info.get("series", "")
            try:
                s = fred.get_series(series_id).dropna()
                if s.empty:
                    continue
                value = float(s.iloc[-1])
                prev_value = float(s.iloc[-2]) if len(s) >= 2 else None

                rate_values[name] = value

                records.append(PriceRecord(
                    asset_class="bond",
                    symbol=name,
                    name=name,
                    price=value,
                    prev_price=prev_value,
                    change_pct=((value - prev_value) / abs(prev_value) * 100) if prev_value else None,
                    source=self.name,
                ))
                logger.info(f"FRED {name} ({series_id}): {value:.3f}%")
            except Exception as e:
                logger.error(f"FRED 获取 {series_id} 失败: {e}")

        # 计算利差
        if "US_10Y" in rate_values and "US_2Y" in rate_values:
            spread = rate_values["US_10Y"] - rate_values["US_2Y"]
            records.append(PriceRecord(
                asset_class="bond",
                symbol="US_SPREAD",
                name="美债利差(10Y-2Y)",
                price=spread,
                source=self.name,
            ))

        return records

    def health_check(self) -> bool:
        if not self.api_key:
            return False
        try:
            fred = Fred(api_key=self.api_key)
            s = fred.get_series("DGS10")
            return not s.empty
        except Exception:
            return False
