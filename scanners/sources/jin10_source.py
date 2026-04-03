"""
金十数据数据源 - 财经快讯
"""
import hashlib
import re
from datetime import datetime, timezone
import requests
from loguru import logger
from scanners.base import BaseSource, NewsRecord
import config


class Jin10Source(BaseSource):
    """金十数据快讯 API"""

    name = "jin10"

    def __init__(self):
        self.url = "https://flash-api.jin10.com/get_flash_list"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "application/json",
            "x-app-id": "bVBF4FyRTn5NJF5n",
            "x-version": "1.0.0",
        }
        self.proxy = config.PROXY

    def fetch(self) -> list[NewsRecord]:
        """获取金十数据最新快讯"""
        records = []

        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            params = {
                "max_time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "channel": "-8200",
            }
            proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else {}
            r = requests.get(
                self.url,
                headers=self.headers,
                params=params,
                timeout=15,
                proxies=proxies,
            )
            data = r.json()
            items = data.get("data", [])
        except Exception as e:
            logger.error(f"金十数据请求失败: {e}")
            return records

        for it in items:
            try:
                content_data = it.get("data", {})
                title = content_data.get("title", "").strip()
                content = content_data.get("content", "").strip()

                # 金十很多条目没有 title，用 content 替代
                if not title and content:
                    # 取内容前100字作为标题
                    clean = re.sub(r"<[^>]+>", "", content)
                    title = clean[:100]

                if not title:
                    continue

                # 清理 HTML 标签
                clean_content = re.sub(r"<[^>]+>", " ", content)
                clean_content = " ".join(clean_content.split())

                # 重要性：金十有 important 字段
                is_important = it.get("important", 0)
                importance = 8 if is_important else 4

                source_id = str(it.get("id", ""))
                time_str = it.get("time", "")

                records.append(NewsRecord(
                    source=self.name,
                    source_id=source_id,
                    title=title,
                    content=clean_content if clean_content else None,
                    importance=importance,
                    language="zh",
                    categories="financial",
                ))
            except Exception:
                continue

        logger.info(f"金十数据获取 {len(records)} 条快讯")
        return records

    def health_check(self) -> bool:
        try:
            proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else {}
            r = requests.get(
                self.url,
                headers=self.headers,
                params={"channel": "-8200"},
                timeout=10,
                proxies=proxies,
            )
            return r.status_code == 200
        except Exception:
            return False
