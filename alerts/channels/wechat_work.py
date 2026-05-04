"""
企业微信机器人 Webhook 推送通道
"""
import requests
from loguru import logger
import config


class WeChatWorkChannel:
    """企业微信 Webhook 推送"""

    name = "wechat_work"

    def __init__(self):
        self.webhook_url = config.WECHAT_WORK_WEBHOOK

    def send(self, title: str, content: str) -> bool:
        """
        发送 markdown 格式消息到企业微信群

        企业微信 Webhook 支持的 markdown 特殊标记：
        - <font color="warning">橙色文字</font>  用于告警
        - <font color="info">绿色文字</font>      用于正常
        - <font color="comment">灰色文字</font>    用于备注
        - 支持 **加粗**、> 引用、[链接](url)
        """
        if not self.webhook_url:
            logger.warning("企业微信 Webhook URL 未配置")
            return False

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"## {title}\n{content}",
            },
        }

        try:
            proxies = config.proxies()
            r = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
                proxies=proxies,
            )
            result = r.json()
            if result.get("errcode") == 0:
                logger.info(f"[WeChat Work] 消息发送成功: {title}")
                return True
            else:
                logger.error(f"[WeChat Work] 发送失败: {result}")
                return False
        except Exception as e:
            logger.error(f"[WeChat Work] 发送异常: {e}")
            return False

    def send_text(self, content: str) -> bool:
        """发送纯文本消息"""
        if not self.webhook_url:
            return False

        payload = {
            "msgtype": "text",
            "text": {"content": content},
        }

        try:
            proxies = config.proxies()
            r = requests.post(self.webhook_url, json=payload, timeout=10, proxies=proxies)
            return r.json().get("errcode") == 0
        except Exception:
            return False
