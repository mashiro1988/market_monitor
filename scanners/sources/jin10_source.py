"""
金十数据数据源 - 财经快讯
"""
import hashlib
import html
import re
from datetime import datetime, timedelta, timezone
import requests
from loguru import logger
from scanners.base import BaseSource, NewsRecord
import config


class Jin10ApiError(RuntimeError):
    """flash-api 请求失败 / 非 200 / 返回体异常。

    2026-07-10 事故：金十把海外机房 IP 封在 flash-api（403 Access denied），
    旧实现把它静默当 0 条返回，源健康记为"正常但 0 行"，18 小时无告警。
    现在这类情况一律抛错，由 news_scanner 记 source error；fetch() 会先用
    flash.jin10.com SSR 页兜底（该页未被封）。
    """


# flash.jin10.com SSR 页解析（兜底）：每条容器带 API 同款 20 位 id（"flash" 前缀），
# 前 14 位即北京时间戳，source_id 去重与 API 完全兼容，无跨零点问题。
_FLASH_ID_RE = re.compile(r'id="flash(\d{20})"')
_FLASH_TITLE_RE = re.compile(r'right-common-title[^>]*>(.*?)</b>', re.S)
_FLASH_TEXT_RE = re.compile(r'class="flash-text"[^>]*>(.*?)</div>', re.S)


class Jin10Source(BaseSource):
    """金十数据快讯 API"""

    name = "jin10"
    BEIJING_OFFSET = timedelta(hours=8)
    FLASH_PAGE_URL = "https://flash.jin10.com/"

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
        """获取金十数据快讯；max_time 为 UTC naive/aware，传给 Jin10 前转北京时间。

        flash-api 失败时（Jin10ApiError）用 flash 页 SSR 快照兜底——快照没有
        max_time 语义，所以只有无 max_time 的实时抓取会兜底，翻页调用直接上抛。
        兜底也失败 → 上抛，让 news_scanner 把源记成 error 而不是"正常 0 行"。
        """
        try:
            return self._fetch_api(max_time=max_time)
        except Jin10ApiError as api_err:
            if max_time is not None:
                raise
            try:
                records = self._fetch_flash_page()
            except Exception as page_err:
                raise RuntimeError(
                    f"金十 flash-api 失败（{api_err}），flash 页兜底也失败（{page_err}）"
                ) from api_err
            logger.warning(f"金十 flash-api 不可用（{api_err}），flash 页兜底获取 {len(records)} 条快讯")
            return records

    def _fetch_api(self, max_time: datetime | None = None) -> list[NewsRecord]:
        """flash-api 正常路径；请求/结构异常抛 Jin10ApiError（不再静默吞成 0 条）。"""
        records = []

        query_time = self._to_beijing_naive(max_time) if max_time else self._now_beijing_naive()
        params = {
            "max_time": query_time.strftime("%Y-%m-%d %H:%M:%S"),
            "channel": "-8200",
        }
        try:
            r = requests.get(
                self.url,
                headers=self.headers,
                params=params,
                timeout=15,
                proxies=config.proxies(),
            )
        except Exception as e:
            raise Jin10ApiError(f"请求异常: {e}") from e
        if r.status_code != 200:
            raise Jin10ApiError(f"HTTP {r.status_code}: {str(getattr(r, 'text', ''))[:120]}")
        try:
            data = r.json()
        except Exception as e:
            raise Jin10ApiError(f"响应非 JSON: {e}") from e
        if not isinstance(data, dict):
            raise Jin10ApiError(f"响应结构异常: {type(data).__name__}")
        status = data.get("status")
        if status is not None and status != 200:
            raise Jin10ApiError(f"业务状态 {status}: {data.get('message')}")
        items = data.get("data") or []
        if not isinstance(items, list):
            return records

        for it in items:
            try:
                if not isinstance(it, dict):
                    continue
                content_data = it.get("data") or {}
                if not isinstance(content_data, dict):
                    content_data = {}
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

    def _fetch_flash_page(self) -> list[NewsRecord]:
        """拉 flash.jin10.com SSR 页并解析（兜底路径，只有最新一页 ~25 条）。"""
        r = requests.get(
            self.FLASH_PAGE_URL,
            headers={"User-Agent": self.headers["User-Agent"]},
            timeout=15,
            proxies=config.proxies(),
        )
        if r.status_code != 200:
            raise RuntimeError(f"flash 页 HTTP {r.status_code}")
        return self._parse_flash_html(r.text)

    @classmethod
    def _parse_flash_html(cls, page_html: str) -> list[NewsRecord]:
        """解析 flash 页 SSR HTML：id="flash<20位>" 分条，标题在 right-common-title，
        正文在 flash-text；VIP 锁定条目无正文 → 跳过。无标题条目沿用 API 路径规则
        （正文前 100 字），importance 用容器上的 is-important 类。"""
        records: list[NewsRecord] = []
        marks = list(_FLASH_ID_RE.finditer(page_html))
        for idx, m in enumerate(marks):
            end = marks[idx + 1].start() if idx + 1 < len(marks) else len(page_html)
            seg = page_html[m.start():end]
            fid = m.group(1)
            try:
                published_bj = datetime.strptime(fid[:14], "%Y%m%d%H%M%S")
            except ValueError:
                continue
            text_m = _FLASH_TEXT_RE.search(seg)
            if not text_m:
                continue
            content = html.unescape(re.sub(r"<[^>]+>", " ", text_m.group(1)))
            content = " ".join(content.split())
            title = ""
            title_m = _FLASH_TITLE_RE.search(seg)
            if title_m:
                title = html.unescape(re.sub(r"<[^>]+>", " ", title_m.group(1))).strip()
            if not title and content:
                title = content[:100]
            if not title:
                continue
            records.append(NewsRecord(
                source=cls.name,
                source_id=fid,
                title=title,
                content=content if content else None,
                importance=1 if "is-important" in seg else 0,
                language="zh",
                categories="financial",
                published_at=published_bj - cls.BEIJING_OFFSET,
            ))
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

        try:
            while cursor > start_time and pages < max_pages:
                seen_before_page = len(seen_ids)
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
                    if len(seen_ids) == seen_before_page:
                        cursor = cursor - timedelta(seconds=1)
                    else:
                        cursor = oldest
                else:
                    cursor = oldest
        except Jin10ApiError as api_err:
            # API 挂（如 403 封 IP）→ 回补退化为 flash 页单页快照：没有翻页语义，
            # 只能覆盖页面上最新 ~25 条；页面也挂则上抛给 scanner 记 error。
            if records:
                logger.warning(f"金十回补中途 API 失败（{api_err}），返回已取得 {len(records)} 条")
            else:
                for record in self._fetch_flash_page():
                    if record.published_at is None:
                        continue
                    if not (start_time <= record.published_at < end_time):
                        continue
                    if record.source_id in seen_ids:
                        continue
                    seen_ids.add(record.source_id)
                    records.append(record)
                logger.warning(f"金十回补降级为 flash 页单页快照（{api_err}）：{len(records)} 条")
            return records

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
