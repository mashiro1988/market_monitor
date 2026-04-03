"""
旧表模型 - 从 database.py 迁移，保持向后兼容
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from datetime import datetime
from database import Base


class StockIndex(Base):
    """股票指数数据表"""
    __tablename__ = "stock_indices"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, nullable=False)
    symbol = Column(String(10), nullable=False)
    name = Column(String(50), nullable=False)
    prev_close = Column(Float, nullable=True)
    close = Column(Float, nullable=False)
    change_pct = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class BondRate(Base):
    """债券利率数据表"""
    __tablename__ = "bond_rates"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, nullable=False)
    rate_type = Column(String(20), nullable=False)
    value = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class EconomicData(Base):
    """经济数据表"""
    __tablename__ = "economic_data"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, nullable=False)
    indicator = Column(String(50), nullable=False)
    actual = Column(Float, nullable=True)
    forecast = Column(Float, nullable=True)
    previous = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CryptoData(Base):
    """加密货币数据表"""
    __tablename__ = "crypto_data"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, nullable=False)
    symbol = Column(String(10), nullable=False)
    price = Column(Float, nullable=False)
    change_pct = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class MarketNews(Base):
    """市场新闻表"""
    __tablename__ = "market_news"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, nullable=False)
    category = Column(String(50), nullable=False)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=True)
    source = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
