"""
交易信号基类 - 预留接口，后续扩展
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from scanners.base import PriceRecord, NewsRecord, PredictionRecord


@dataclass
class SignalContext:
    """信号评估上下文 - 包含当前市场状态"""
    prices: dict[str, PriceRecord] = field(default_factory=dict)       # symbol -> latest price
    news: list[NewsRecord] = field(default_factory=list)               # recent news
    predictions: list[PredictionRecord] = field(default_factory=list)  # prediction market data


@dataclass
class SignalResult:
    """信号评估结果"""
    signal_type: str        # "long", "short", "neutral"
    asset: str              # 目标资产，如 "BTC/USDT"
    confidence: float       # 0.0 - 1.0
    reasoning: str          # 信号理由
    metadata: dict = field(default_factory=dict)  # 附加信息


class BaseSignal(ABC):
    """交易信号抽象基类"""

    name: str = "unknown"
    description: str = ""

    @abstractmethod
    def evaluate(self, context: SignalContext) -> Optional[SignalResult]:
        """
        给定当前市场状态，返回一个信号或 None。

        实现示例：
        - 当 VIX 处于低位且预测市场对某事件概率骤变时，生成信号
        - 当多个资产类别出现相关性断裂时，生成信号
        - 当重要新闻出现但价格未反应时，生成信号
        """
        ...
