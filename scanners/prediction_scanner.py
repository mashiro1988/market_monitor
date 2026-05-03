"""
预测市场扫描器 - 跟踪 Polymarket 等预测市场的赔率变化
"""
from datetime import datetime, timezone
from loguru import logger
from database import get_session
from models.prediction import PredictionMarket
from scanners.base import PredictionRecord
from scanners.sources.polymarket.source import PolymarketSource
import config


class PredictionScanner:
    """预测市场扫描器 - 5分钟频率跟踪宏观相关预测市场"""

    def __init__(self):
        self.sources = []
        if config.POLYMARKET.get("enabled", True):
            self.sources.append(PolymarketSource())

    def scan(self) -> list[PredictionRecord]:
        """执行一次完整的预测市场扫描"""
        all_records: list[PredictionRecord] = []
        scan_time = datetime.now(timezone.utc).replace(tzinfo=None)

        for source in self.sources:
            try:
                logger.info(f"[PredictionScanner] 采集 {source.name}...")
                records = source.fetch()
                all_records.extend(records)
                logger.info(f"[PredictionScanner] {source.name} 返回 {len(records)} 条记录")
            except Exception as e:
                logger.error(f"[PredictionScanner] {source.name} 采集失败: {e}")

        # 写入数据库
        self._save_records(all_records, scan_time)

        logger.info(f"[PredictionScanner] 扫描完成，共 {len(all_records)} 条记录")
        return all_records

    def _save_records(self, records: list[PredictionRecord], scan_time: datetime):
        """将预测市场记录写入数据库，并与前一次快照比较"""
        session = get_session()
        try:
            for r in records:
                # 查找该市场+outcome的上一次记录
                prev = session.query(PredictionMarket).filter(
                    PredictionMarket.market_id == r.market_id,
                    PredictionMarket.outcome == r.outcome,
                    PredictionMarket.timestamp < scan_time,
                ).order_by(PredictionMarket.timestamp.desc()).first()

                prev_probability = prev.probability if prev else None

                pm = PredictionMarket(
                    timestamp=scan_time,
                    market_id=r.market_id,
                    question=r.question,
                    outcome=r.outcome,
                    probability=r.probability,
                    prev_probability=prev_probability,
                    volume=r.volume,
                )
                session.add(pm)

            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"[PredictionScanner] 保存失败: {e}")
        finally:
            session.close()
