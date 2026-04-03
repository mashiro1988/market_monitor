"""
信号注册器 - 管理和发现交易信号
"""
from typing import Optional
from loguru import logger
from signals.base import BaseSignal, SignalContext, SignalResult


class SignalRegistry:
    """交易信号注册和评估中心"""

    def __init__(self):
        self._signals: list[BaseSignal] = []

    def register(self, signal: BaseSignal):
        """注册一个信号"""
        self._signals.append(signal)
        logger.info(f"[SignalRegistry] 注册信号: {signal.name}")

    def evaluate_all(self, context: SignalContext) -> list[SignalResult]:
        """评估所有注册的信号，返回触发的信号列表"""
        results = []
        for signal in self._signals:
            try:
                result = signal.evaluate(context)
                if result is not None:
                    results.append(result)
                    logger.info(
                        f"[Signal] {signal.name} 触发: {result.signal_type} "
                        f"{result.asset} (置信度: {result.confidence:.2f})"
                    )
            except Exception as e:
                logger.error(f"[Signal] {signal.name} 评估失败: {e}")
        return results

    @property
    def signal_count(self) -> int:
        return len(self._signals)

    def list_signals(self) -> list[dict]:
        return [{"name": s.name, "description": s.description} for s in self._signals]
