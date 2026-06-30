"""休市补点锚点：每品种一行，记录最后一根真实 bar 与同刻 perp 价，用于比率锚定。"""
from sqlalchemy import Column, String, Float, DateTime
from datetime import datetime
from database import Base


class GapfillAnchor(Base):
    __tablename__ = "gapfill_anchor"

    symbol = Column(String(30), primary_key=True)
    real_ts = Column(DateTime, nullable=False)
    real_close = Column(Float, nullable=False)
    perp_price = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
