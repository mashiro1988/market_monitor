"""
新闻扫描器 - 编排多源新闻采集，去重后存储到 NewsItem
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone
from loguru import logger
from database import get_session
from models.news import NewsItem
from scanners.base import BaseSource, NewsRecord
from scanners.sources.wallstreetcn_source import WallStreetCNSource
from scanners.sources.jin10_source import Jin10Source
from scanners.sources.rss_source import create_rss_sources
import config


class NewsScanner:
    """新闻扫描器 - 5分钟频率多源新闻聚合"""

    def __init__(self):
        self.sources: list[BaseSource] = []

        # 添加中文源
        if config.NEWS_SOURCES.get("wallstreetcn", {}).get("enabled", True):
            self.sources.append(WallStreetCNSource())
        if config.NEWS_SOURCES.get("jin10", {}).get("enabled", True):
            self.sources.append(Jin10Source())

        # 添加 RSS 源
        self.sources.extend(create_rss_sources())

    @staticmethod
    def compute_content_hash(title: str) -> str:
        """计算标题的归一化哈希，用于跨源去重"""
        # 去掉空白和标点，转小写
        normalized = re.sub(r"[\s\W]+", "", title.lower())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def scan(self) -> list[NewsRecord]:
        """执行一次完整的新闻扫描"""
        all_records: list[NewsRecord] = []
        scan_time = datetime.now(timezone.utc).replace(tzinfo=None)

        for source in self.sources:
            try:
                logger.info(f"[NewsScanner] 采集 {source.name}...")
                records = source.fetch()
                all_records.extend(records)
                logger.info(f"[NewsScanner] {source.name} 返回 {len(records)} 条新闻")
            except Exception as e:
                logger.error(f"[NewsScanner] {source.name} 采集失败: {e}")

        # 去重并写入数据库
        saved_count = self._save_records(all_records, scan_time)
        logger.info(f"[NewsScanner] 扫描完成，获取 {len(all_records)} 条，入库 {saved_count} 条")
        return all_records

    def _save_records(self, records: list[NewsRecord], scan_time: datetime) -> int:
        """去重后写入数据库，返回实际入库数"""
        session = get_session()
        saved = 0
        try:
            for r in records:
                # 层1: 源内去重 - 检查 (source, source_id) 是否已存在
                if r.source_id:
                    exists = session.query(NewsItem).filter(
                        NewsItem.source == r.source,
                        NewsItem.source_id == r.source_id,
                    ).first()
                    if exists:
                        continue

                # 层2: 跨源去重 - 检查 content_hash 是否在最近24小时内已存在
                content_hash = self.compute_content_hash(r.title)
                cutoff = scan_time - timedelta(hours=24)
                hash_exists = session.query(NewsItem).filter(
                    NewsItem.content_hash == content_hash,
                    NewsItem.timestamp >= cutoff,
                ).first()
                if hash_exists:
                    continue

                item = NewsItem(
                    timestamp=scan_time,
                    source=r.source,
                    source_id=r.source_id,
                    title=r.title[:500],
                    content=r.content,
                    url=r.url,
                    importance=r.importance,
                    language=r.language,
                    categories=r.categories,
                    content_hash=content_hash,
                )
                session.add(item)
                saved += 1

            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"[NewsScanner] 保存失败: {e}")
        finally:
            session.close()

        return saved
