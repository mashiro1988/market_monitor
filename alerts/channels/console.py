"""
控制台输出通道 - 用于开发调试
"""
from loguru import logger


class ConsoleChannel:
    """控制台日志告警通道"""

    name = "console"

    def send(self, title: str, content: str) -> bool:
        """输出告警到控制台"""
        logger.info(f"\n{'='*60}\n[ALERT] {title}\n{content}\n{'='*60}")
        return True
