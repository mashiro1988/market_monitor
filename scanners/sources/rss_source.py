"""
RSS 数据源 - 通用 RSS/Atom 订阅解析器
支持 CoinDesk, CoinTelegraph, The Block, Bloomberg, FT 等
"""
import hashlib
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
import requests
from loguru import logger
from scanners.base import BaseSource, NewsRecord
import config

try:
    import feedparser
except ImportError:
    feedparser = None
    logger.warning("feedparser 未安装，RSS 新闻源不可用。请运行: pip install feedparser")


class RSSSource(BaseSource):
    """通用 RSS 新闻源"""

    def __init__(self, source_key: str, url: str, name: str, language: str = "en"):
        self.source_key = source_key
        self.url = url
        self.name = name
        self.language = language
        self.proxy = config.PROXY

    def fetch(self) -> list[NewsRecord]:
        """解析 RSS 订阅并返回新闻记录"""
        if feedparser is None:
            logger.warning(f"feedparser 未安装，跳过 {self.name}")
            return []

        records = []
        try:
            # feedparser 不直接支持代理，手动下载后解析
            proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else {}
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            }
            r = requests.get(self.url, headers=headers, timeout=15, proxies=proxies)
            feed = feedparser.parse(r.content)
        except Exception as e:
            logger.error(f"RSS {self.name} 获取失败: {e}")
            return records

        for entry in feed.entries[:50]:  # 限制每次最多50条
            try:
                title = entry.get("title", "").strip()
                if not title:
                    continue

                # 提取内容摘要
                content = ""
                if hasattr(entry, "summary"):
                    content = re.sub(r"<[^>]+>", " ", entry.summary)
                    content = " ".join(content.split())

                # 提取链接
                link = entry.get("link", "")

                # 提取 source_id
                source_id = entry.get("id", link or title)

                # 提取发布时间
                published_at = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        import calendar
                        published_at = datetime.utcfromtimestamp(calendar.timegm(entry.published_parsed))
                    except Exception:
                        pass

                # 提取分类
                categories = ""
                if hasattr(entry, "tags"):
                    categories = ",".join(t.get("term", "") for t in entry.tags if t.get("term"))

                records.append(NewsRecord(
                    source=self.source_key,
                    source_id=str(hashlib.md5(source_id.encode()).hexdigest()),
                    title=title,
                    content=content[:2000] if content else None,
                    url=link,
                    importance=None,
                    language=self.language,
                    categories=categories if categories else None,
                    published_at=published_at,
                ))
            except Exception:
                continue

        logger.info(f"RSS {self.name} 获取 {len(records)} 条新闻")
        return records

    def fetch_backfill(self, start_time: datetime, end_time: datetime) -> list[NewsRecord]:
        """RSS 无历史翻页接口；从当前 feed 中补齐仍可见的时间段内条目。"""
        records = [
            record for record in self.fetch()
            if record.published_at is not None
            and start_time <= record.published_at < end_time
        ]
        logger.info(f"RSS {self.name} 回补 {len(records)} 条新闻")
        return records

    def health_check(self) -> bool:
        try:
            proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else {}
            r = requests.head(self.url, timeout=10, proxies=proxies)
            return r.status_code < 400
        except Exception:
            return False


def create_rss_sources() -> list[RSSSource]:
    """根据 config.NEWS_SOURCES 创建所有启用的 RSS 源"""
    sources = []
    for key, cfg in config.NEWS_SOURCES.items():
        if not cfg.get("enabled", False):
            continue
        if cfg.get("type") != "rss":
            continue
        url = cfg.get("url", "")
        if not url:
            continue
        sources.append(RSSSource(
            source_key=key,
            url=url,
            name=cfg.get("name", key),
            language=cfg.get("language", "en"),
        ))
    return sources
