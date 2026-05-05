"""跟踪的预测市场列表（slug 或 tag），由 UI 维护."""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, UniqueConstraint
from database import Base


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TrackedMarket(Base):
    __tablename__ = "tracked_markets"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String(16), nullable=False)
    identifier = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utc_naive_now)

    __table_args__ = (
        UniqueConstraint("kind", "identifier", name="uq_tracked_kind_identifier"),
    )
