"""
告警日志模型 - 记录已发送的告警，用于冷却去重
"""
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, Index
from datetime import datetime
from database import Base


class AlertLog(Base):
    """告警发送记录表"""
    __tablename__ = "alert_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    rule_name = Column(String(100), nullable=False)
    message = Column(Text, nullable=False)
    channel = Column(String(50), nullable=False)        # wechat_work, console
    delivered = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_alert_rule_ts", "rule_name", "timestamp"),
    )
