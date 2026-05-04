"""
金十数据数据源 - 财经快讯
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone
import requests
from loguru import logger
from scanners.base import BaseSource, NewsRecord
import config


class Jin10Source(BaseSource):
    """金十数据快讯 API"""

    name = "jin10"
    BEIJING_OFFSET = timedelta(hours=8)

    def __init__(self):
        self.url = "https://flash-api.jin10.com/get_flash_list"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "application/json",
            "x-app-id": "bVBF4FyRTn5NJF5n",
            "x-version": "1.0.0",
        }

    @classmethod
    def _now_beijing_naive(cls) -> datetime:
        """Jin10 API expects max_time in Beijing local time, not UTC."""
        return datetime.now(timezone.utc).replace(tzinfo=None) + cls.BEIJING_OFFSET

    @classmethod
    def _parse_beijing_time(cls, time_str: str) -> datetime | None:
        """Parse Jin10 Beijing time string into UTC naive datetime for storage."""
        if not time_str:
            return None
        try:
            bj_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            return bj_time - cls.BEIJING_OFFSET
        except ValueError:
            return None

    @classmethod
    def _to_beijing_naive(cls, dt: datetime) -> datetime:
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt + cls.BEIJING_OFFSET

    def fetch(self, max_time: datetime | None = None) -> list[NewsRecord]:
        """获取金十数据快讯；max_time 为 UTC naive/aware，传给 Jin10 前转北京时间。"""
        records = []

        try:
            query_time = self._to_beijing_naive(max_time) if max_time else self._now_beijing_naive()
            params = {
                "max_time": query_time.strftime("%Y-%m-%d %H:%M:%S"),
                "channel": "-8200",
            }
            proxies = config.proxies()
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

                # 金十源端只提供 important 标志；这里只保留是/否，不把它当评分。
                is_important = it.get("important", 0)
                importance = 1 if is_important else 0

                source_id = str(it.get("id", ""))
                time_str = it.get("time", "")

                # 金十的 time 字段为北京时间字符串 "YYYY-MM-DD HH:MM:SS"，转为 UTC naive
                published_at = self._parse_beijing_time(time_str)

                records.append(NewsRecord(
                    source=self.name,
                    source_id=source_id,
                    title=title,
                    content=clean_content if clean_content else None,
                    importance=importance,
                    language="zh",
                    categories="financial",
                    published_at=published_at,
                ))
            except Exception:
                continue

        logger.info(f"金十数据获取 {len(records)} 条快讯")
        return records

    def fetch_backfill(
        self,
        start_time: datetime,
        end_time: datetime,
        max_pages: int = 200,
    ) -> list[NewsRecord]:
        """向前翻页回补指定 UTC naive 时间段内的金十快讯。"""
        records: list[NewsRecord] = []
        seen_ids: set[str] = set()
        cursor = end_time
        pages = 0

        while cursor > start_time and pages < max_pages:
            page_records = self.fetch(max_time=cursor)
            pages += 1
            dated_records = [r for r in page_records if r.published_at is not None]
            if not dated_records:
                break

            oldest = min(r.published_at for r in dated_records if r.published_at is not None)
            for record in dated_records:
                if not (start_time <= record.published_at < end_time):
                    continue
                if record.source_id in seen_ids:
                    continue
                seen_ids.add(record.source_id)
                records.append(record)

            if oldest <= start_time:
                break
            if oldest >= cursor:
                break
            cursor = oldest - timedelta(seconds=1)

        if pages >= max_pages:
            logger.warning(f"金十数据回补达到最大页数 {max_pages}，可能仍有更早新闻未拉完")
        logger.info(f"金十数据回补 {len(records)} 条快讯，页数 {pages}")
        return records

    def health_check(self) -> bool:
        try:
            proxies = config.proxies()
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
