"""
华尔街见闻数据源 - 重要财经快讯
"""
import re
import hashlib
from datetime import datetime, timedelta, timezone
import requests
from loguru import logger
from scanners.base import BaseSource, NewsRecord
import config


class WallStreetCNSource(BaseSource):
    """华尔街见闻 JSON API 新闻源"""

    name = "wallstreetcn"

    def __init__(self):
        self.url = "https://api.wallstreetcn.com/apiv1/content/lives"
        self.params = {
            "channel": "global",
            "client": "pc",
            "limit": 100,
            "important": "true",
        }
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "application/json",
        }
        self.proxy = config.PROXY

    def _compute_hash(self, title: str) -> str:
        """计算标题的归一化哈希，用于跨源去重"""
        normalized = re.sub(r"[\s\W]+", "", title.lower())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def fetch(self) -> list[NewsRecord]:
        """获取最近的重要财经快讯"""
        records = []

        try:
            proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else {}
            r = requests.get(
                self.url,
                headers=self.headers,
                params=self.params,
                timeout=15,
                proxies=proxies,
            )
            j = r.json()
            items = j.get("data", {}).get("items", [])
        except Exception as e:
            logger.error(f"华尔街见闻请求失败: {e}")
            return records

        for it in items:
            try:
                ts = int(it.get("display_time", 0))
                if ts == 0:
                    continue

                raw_title = (it.get("title") or "").strip()
                if not raw_title:
                    continue

                raw_content = it.get("content_text", "")
                content_text = re.sub(r"<[^>]+>", " ", str(raw_content))
                content_text = " ".join(content_text.split())

                channel = it.get("channels")
                if isinstance(channel, list):
                    channel = ",".join(map(str, channel))

                # 判断重要性
                is_important = it.get("important", False)
                importance = 8 if is_important else 5

                records.append(NewsRecord(
                    source=self.name,
                    source_id=str(it.get("id", ts)),
                    title=raw_title,
                    content=content_text if content_text else None,
                    url=it.get("uri"),
                    importance=importance,
                    language="zh",
                    categories=str(channel) if channel else "global",
                ))
            except Exception:
                continue

        logger.info(f"华尔街见闻获取 {len(records)} 条快讯")
        return records

    def health_check(self) -> bool:
        try:
            proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else {}
            r = requests.get(
                self.url,
                headers=self.headers,
                params={"channel": "global", "client": "pc", "limit": 1},
                timeout=10,
                proxies=proxies,
            )
            return r.status_code == 200
        except Exception:
            return False
