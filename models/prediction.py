"""
预测市场模型 - 存储 Polymarket 等预测市场的概率快照
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, Index
from datetime import datetime
from database import Base


class PredictionMarket(Base):
    """预测市场快照表"""
    __tablename__ = "prediction_markets"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False)
    market_id = Column(String(100), nullable=False)     # Polymarket condition_id 或 slug
    question = Column(String(500), nullable=False)      # 市场问题描述
    outcome = Column(String(100), nullable=False)       # "Yes", "No", etc.
    probability = Column(Float, nullable=False)          # 0.0 - 1.0
    prev_probability = Column(Float, nullable=True)     # 上次快照的概率
    volume = Column(Float, nullable=True)                # 交易量
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_prediction_market_ts", "market_id", "outcome", "timestamp"),
    )
