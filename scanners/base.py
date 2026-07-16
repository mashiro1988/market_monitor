"""
数据源基类 - 所有数据源适配器的抽象基类
"""
from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class PriceRecord:
    """标准化的价格记录"""
    asset_class: str        # stock_index, futures, perp, bond, commodity, crypto
    symbol: str             # ^DJI, ES=F, BTC/USDT, etc.
    name: str               # 道琼斯, S&P500期货, etc.
    price: float
    prev_price: Optional[float] = None
    change_pct: Optional[float] = None
    volume: Optional[float] = None
    source: str = ""
    # 价格对应时间（UTC naive）。5m K 线源使用最近已收盘 bar 的结束时刻；
    # 源端若无法提供精确时间则为 None，入库时回退到 scan_time。
    timestamp: Optional[datetime] = None


@dataclass
class NewsRecord:
    """标准化的新闻记录"""
    source: str             # wallstreetcn, jin10, coindesk_rss
    source_id: str          # 源端原始ID
    title: str
    content: Optional[str] = None
    url: Optional[str] = None
    importance: Optional[int] = None   # 源端重要标志；Jin10 important 映射为 1/0
    llm_importance: Optional[int] = None
    llm_importance_reason: Optional[str] = None
    llm_model: Optional[str] = None
    llm_scored_at: Optional[datetime] = None
    language: str = "zh"
    categories: Optional[str] = None
    published_at: Optional[datetime] = None  # 原始发布时间


@dataclass
class PredictionRecord:
    """标准化的预测市场记录"""
    market_id: str          # Polymarket condition_id
    question: str
    outcome: str            # "Yes", "No"
    probability: float      # 0.0 - 1.0
    volume: Optional[float] = None
    # 来源跟踪项："slug:<identifier>"，由 source 在 fetch 时打标
    origin: Optional[str] = None


@dataclass
class SourceFetchStatus:
    """One source fetch attempt result for scanner diagnostics."""
    source: str
    ok: bool
    record_count: int = 0
    empty: bool = False
    stage: str = "scan"
    error: Optional[str] = None


class SourceHealthMixin:
    """Collect per-source fetch diagnostics for the last scanner operation."""

    source_statuses: list[SourceFetchStatus]

    def _reset_source_statuses(self) -> None:
        self.source_statuses = []

    def _record_source_status(self, source_name: str, records: list, *, stage: str) -> None:
        if not hasattr(self, "source_statuses"):
            self._reset_source_statuses()
        self.source_statuses.append(SourceFetchStatus(
            source=source_name,
            ok=True,
            record_count=len(records),
            empty=len(records) == 0,
            stage=stage,
        ))

    def _record_source_error(self, source_name: str, exc: Exception, *, stage: str) -> None:
        if not hasattr(self, "source_statuses"):
            self._reset_source_statuses()
        self.source_statuses.append(SourceFetchStatus(
            source=source_name,
            ok=False,
            record_count=0,
            empty=False,
            stage=stage,
            error=f"{type(exc).__name__}: {exc}",
        ))


class BaseSource(ABC):
    """数据源抽象基类"""

    name: str = "unknown"

    def fetch(self) -> list:
        """获取"当前"口径数据。仅剩即时报价类源（CNBC 债券）实现；
        K 线类源走 fetch_history 区间语义（游标同步重构 2026-07-14）。"""
        raise NotImplementedError(f"{self.name} 不支持即时 fetch，请用 fetch_history")

    def health_check(self) -> bool:
        """检查数据源是否可用，默认返回 True"""
        return True
