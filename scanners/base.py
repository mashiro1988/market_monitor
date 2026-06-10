"""
数据源基类 - 所有数据源适配器的抽象基类
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class PriceRecord:
    """标准化的价格记录"""
    asset_class: str        # stock_index, futures, bond, commodity, crypto
    symbol: str             # ^DJI, ES=F, BTC/USDT, etc.
    name: str               # 道琼斯, S&P500期货, etc.
    price: float
    prev_price: Optional[float] = None
    change_pct: Optional[float] = None
    volume: Optional[float] = None
    source: str = ""
    # 价格对应时间（UTC naive）。5m K 线源使用最近已收盘 bar 的结束时刻；
    # CoinGecko 实时价使用采集时点；FRED 等源端若无法提供则为 None，入库时回退到 scan_time。
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
    # 来源跟踪项："slug:<identifier>" / "tag:<identifier>"，由 source 在 fetch 时打标
    origin: Optional[str] = None


class BaseSource(ABC):
    """数据源抽象基类"""

    name: str = "unknown"

    @abstractmethod
    def fetch(self) -> list:
        """获取数据，返回标准化记录列表（PriceRecord / NewsRecord / PredictionRecord）"""
        ...

    def health_check(self) -> bool:
        """检查数据源是否可用，默认返回 True"""
        return True
