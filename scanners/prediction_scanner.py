"""
预测市场扫描器 - 跟踪 Polymarket 等预测市场的赔率变化
"""
from datetime import datetime, timedelta, timezone
from loguru import logger
from sqlalchemy import func
from database import get_session
from models.prediction import PredictionMarket
from scanners.base import PredictionRecord, SourceHealthMixin
from scanners.sources.polymarket.source import PolymarketSource
import config


class PredictionScanner(SourceHealthMixin):
    """预测市场扫描器 - 5分钟频率跟踪宏观相关预测市场"""

    def __init__(self):
        self.sources = []
        if config.POLYMARKET.get("enabled", True):
            self.sources.append(PolymarketSource())
        self._reset_source_statuses()

    def scan(self) -> list[PredictionRecord]:
        """执行一次完整的预测市场扫描"""
        all_records: list[PredictionRecord] = []
        scan_time = datetime.now(timezone.utc).replace(tzinfo=None)
        self._reset_source_statuses()

        for source in self.sources:
            try:
                logger.info(f"[PredictionScanner] 采集 {source.name}...")
                records = source.fetch()
                self._record_source_status(source.name, records, stage="scan")
                all_records.extend(records)
                logger.info(f"[PredictionScanner] {source.name} 返回 {len(records)} 条记录")
            except Exception as e:
                self._record_source_error(source.name, e, stage="scan")
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
                    origin=getattr(r, "origin", None),
                )
                session.add(pm)

            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"[PredictionScanner] 保存失败: {e}")
        finally:
            session.close()


def prediction_scan_due(session, now: datetime | None = None) -> bool:
    """小时门控(spec 2026-08-28 §2):表内最新快照距 now ≥ SCAN_INTERVALS['prediction']
    分钟才到点。基准取 DB 不取内存——重启不丢节拍;Gamma 全挂那轮没写快照,
    下轮 5 分钟 scan_cycle 自动重试(自愈,优于独立小时 job)。"""
    interval = max(1, int(config.SCAN_INTERVALS.get("prediction", 60)))
    latest = session.query(func.max(PredictionMarket.timestamp)).scalar()
    if latest is None:
        return True
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return now - latest >= timedelta(minutes=interval)
