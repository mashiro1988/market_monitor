"""
价格快照模型 - 统一存储所有资产类别的5分钟价格数据
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, Index
from datetime import datetime
from database import Base


class PriceSnapshot(Base):
    """统一价格快照表 - 5分钟频率"""
    __tablename__ = "price_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False)
    asset_class = Column(String(20), nullable=False)  # stock_index, futures, bond, commodity, crypto
    symbol = Column(String(30), nullable=False)        # ^DJI, ES=F, BTC/USDT, etc.
    name = Column(String(50), nullable=False)           # 道琼斯, S&P500期货, etc.
    price = Column(Float, nullable=False)
    prev_price = Column(Float, nullable=True)
    change_pct = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    source = Column(String(30), nullable=False)         # yfinance, okx_swap_5m, okx_spot_5m, coingecko_realtime, eastmoney_bond_quote, fred
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_price_snapshot_ts_symbol", "timestamp", "symbol", unique=True),
        Index("ix_price_snapshot_class_ts", "asset_class", "timestamp"),
    )
