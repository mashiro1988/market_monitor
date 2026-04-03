"""
价格扫描器 - 编排各价格数据源，统一存储到 PriceSnapshot

加密货币采集策略：
  1. 优先使用 Binance (ccxt) — 数据实时、精确
  2. Binance 不可用时（如地区限制 451）自动回退到 CoinGecko
"""
from datetime import datetime, timezone
from loguru import logger
from database import get_session
from models.price import PriceSnapshot
from scanners.base import BaseSource, PriceRecord
from scanners.sources.yfinance_source import YFinancePriceSource
from scanners.sources.ccxt_source import CcxtPriceSource
from scanners.sources.coingecko_source import CoinGeckoPriceSource
from scanners.sources.fred_source import FredBondSource


class PriceScanner:
    """价格扫描器 - 5分钟频率采集所有资产价格"""

    def __init__(self):
        self.yfinance = YFinancePriceSource()
        self.ccxt = CcxtPriceSource()
        self.coingecko = CoinGeckoPriceSource()
        self.fred = FredBondSource()

    def scan(self) -> list[PriceRecord]:
        """执行一次完整的价格扫描"""
        all_records: list[PriceRecord] = []
        scan_time = datetime.now(timezone.utc).replace(tzinfo=None)

        # 1. yfinance: 股指、期货、亚洲指数、商品、部分债券
        all_records.extend(self._fetch_safe(self.yfinance))

        # 2. 加密货币: Binance 优先，失败则回退 CoinGecko
        crypto_records = self._fetch_safe(self.ccxt)
        if not crypto_records:
            logger.info("[PriceScanner] Binance 无数据，回退到 CoinGecko...")
            crypto_records = self._fetch_safe(self.coingecko)
        all_records.extend(crypto_records)

        # 3. FRED: 美债利率
        all_records.extend(self._fetch_safe(self.fred))

        # 写入数据库
        self._save_records(all_records, scan_time)

        logger.info(f"[PriceScanner] 扫描完成，共 {len(all_records)} 条价格记录")
        return all_records

    def _fetch_safe(self, source: BaseSource) -> list[PriceRecord]:
        """安全调用数据源，捕获异常"""
        try:
            logger.info(f"[PriceScanner] 采集 {source.name}...")
            records = source.fetch()
            logger.info(f"[PriceScanner] {source.name} 返回 {len(records)} 条记录")
            return records
        except Exception as e:
            logger.error(f"[PriceScanner] {source.name} 采集失败: {e}")
            return []

    def _save_records(self, records: list[PriceRecord], scan_time: datetime):
        """将价格记录写入数据库"""
        session = get_session()
        try:
            for r in records:
                # 查询上一次该品种的价格（用于计算变化）
                prev = session.query(PriceSnapshot).filter(
                    PriceSnapshot.symbol == r.symbol,
                    PriceSnapshot.timestamp < scan_time,
                ).order_by(PriceSnapshot.timestamp.desc()).first()

                prev_price = r.prev_price
                change_pct = r.change_pct
                if prev and prev_price is None:
                    prev_price = prev.price
                    if prev_price and prev_price != 0:
                        change_pct = ((r.price - prev_price) / abs(prev_price)) * 100

                snapshot = PriceSnapshot(
                    timestamp=scan_time,
                    asset_class=r.asset_class,
                    symbol=r.symbol,
                    name=r.name,
                    price=r.price,
                    prev_price=prev_price,
                    change_pct=change_pct,
                    volume=r.volume,
                    source=r.source,
                )
                session.merge(snapshot)

            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"[PriceScanner] 保存失败: {e}")
        finally:
            session.close()
