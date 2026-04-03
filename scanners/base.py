"""
数据源基类 - 所有数据源适配器的抽象基类
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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


@dataclass
class NewsRecord:
    """标准化的新闻记录"""
    source: str             # wallstreetcn, jin10, coindesk_rss
    source_id: str          # 源端原始ID
    title: str
    content: Optional[str] = None
    url: Optional[str] = None
    importance: Optional[int] = None   # 0-10
    language: str = "zh"
    categories: Optional[str] = None


@dataclass
class PredictionRecord:
    """标准化的预测市场记录"""
    market_id: str          # Polymarket condition_id
    question: str
    outcome: str            # "Yes", "No"
    probability: float      # 0.0 - 1.0
    volume: Optional[float] = None


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
