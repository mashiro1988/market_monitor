"""跟踪的预测市场列表（精确 market/event slug），由 UI 维护."""
import json
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, UniqueConstraint
from database import Base


def market_filter_list(raw: str | None) -> list[str] | None:
    """market_filter 列 → 保留的 market_id 列表;NULL/空/坏 JSON=不过滤。
    宁多显示不误删数据:解析失败一律当"全保留"。采集端与展示端共用此口径。"""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    ids = [str(x).strip() for x in data if str(x).strip()]
    return ids or None


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TrackedMarket(Base):
    __tablename__ = "tracked_markets"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String(16), nullable=False)
    identifier = Column(String(255), nullable=False)
    # 线归属(spec 2026-08-28 §1):宏观/加密两池是独立页面,跟踪项要知道住哪个页面。
    # 存量默认 macro(现存种子全为 Fed/通胀/地缘题材)。
    market = Column(String(8), nullable=False, default="macro")
    # 档位筛选(2026-08-30 用户反馈):event slug 展开的多子市场里只保留哪些 market_id。
    # JSON 数组;NULL/空=全保留。采集端少采、展示端少画——分桶类市场 15 档只看 3 档。
    market_filter = Column(Text, nullable=True)
    display_name = Column(String(255), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    # 软删除墓碑：用户删除时置 True（行保留），让 seed 重启时不会把它当"缺失"补种回来。
    dismissed = Column(Boolean, nullable=False, default=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utc_naive_now)

    __table_args__ = (
        UniqueConstraint("kind", "identifier", name="uq_tracked_kind_identifier"),
    )
