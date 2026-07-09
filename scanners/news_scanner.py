"""
新闻扫描器 - 编排 Jin10/Bloomberg 新闻采集，按扫描窗口原样存储到 NewsItem。
"""
from datetime import datetime, timedelta, timezone
from loguru import logger
from database import get_session
from models.news import NewsItem
from services import market_calendar
from scanners.base import BaseSource, NewsRecord, SourceHealthMixin
from scanners.sources.jin10_source import Jin10Source
from scanners.sources.rss_source import create_rss_sources
from scanners.scorer import NewsScorer
from chart_utils import format_beijing_time
import config


def _chunks(items: list[str], size: int = 500):
    for idx in range(0, len(items), size):
        yield items[idx:idx + size]


class NewsScanner(SourceHealthMixin):
    """新闻扫描器 - 5分钟频率多源新闻聚合"""

    def __init__(self):
        self.sources: list[BaseSource] = []

        # 当前只保留 Jin10 与 Bloomberg 两个新闻源。
        if config.NEWS_SOURCES.get("jin10", {}).get("enabled", True):
            self.sources.append(Jin10Source())

        # 添加 Bloomberg RSS 源
        self.sources.extend(create_rss_sources())

        self.scorer = NewsScorer()
        self._reset_source_statuses()

    def scan(self) -> list[NewsRecord]:
        """执行一次完整的新闻扫描"""
        all_records: list[NewsRecord] = []
        scan_time = datetime.now(timezone.utc).replace(tzinfo=None)
        self._reset_source_statuses()

        for source in self.sources:
            try:
                logger.info(f"[NewsScanner] 采集 {source.name}...")
                records = source.fetch()
                self._record_source_status(source.name, records, stage="scan")
                all_records.extend(records)
                logger.info(f"[NewsScanner] {source.name} 返回 {len(records)} 条新闻")
            except Exception as e:
                self._record_source_error(source.name, e, stage="scan")
                logger.error(f"[NewsScanner] {source.name} 采集失败: {e}")

        if not all_records:
            logger.info("[NewsScanner] 本轮没有新闻需要打分")
            return []

        all_records = self._filter_scan_window(all_records, scan_time)
        if not all_records:
            logger.info("[NewsScanner] 本轮 5min window 内没有新闻需要打分")
            return []

        all_records.sort(
            key=lambda r: r.published_at or datetime.min,
            reverse=True,
        )

        # 对所有保留新闻补充 DeepSeek V4 价格波动重要性评分；不覆盖源端 importance。
        if self.scorer.enabled:
            all_records = self.scorer.enrich_batch(all_records)

        # 按发布时间降序排列（无 published_at 的条目排在最后）
        all_records.sort(
            key=lambda r: r.published_at or datetime.min,
            reverse=True,
        )

        saved_count = self._save_records(all_records, scan_time, skip_existing=True)
        logger.info(f"[NewsScanner] 扫描完成，获取 {len(all_records)} 条，入库 {saved_count} 条")
        return all_records

    def backfill_missing_history(
        self,
        max_hours: int | None = None,
        end_time: datetime | None = None,
        score_records: bool | None = None,
    ) -> list[NewsRecord]:
        """
        回补停机期间缺失的新闻，最多 72 小时。

        回补任务只补齐 DB 中最新新闻之后的时间段；如果任务本身跑得超过一个扫描周期，
        会继续补到任务完成时附近，避免回补期间的定时扫描被锁跳过后留下新缺口。
        历史回补默认跳过 LLM 打分，避免几千条历史新闻串行调用外部 API 阻塞扫描锁。
        """
        requested_hours = int(config.NEWS_BACKFILL_MAX_HOURS if max_hours is None else max_hours)
        window_hours = min(max(requested_hours, 0), 72)
        self._reset_source_statuses()
        if window_hours <= 0:
            logger.info("[NewsBackfill] 已禁用（window_hours <= 0）")
            return []
        should_score = bool(
            getattr(config, "NEWS_BACKFILL_LLM_ENABLED", False)
            if score_records is None
            else score_records
        )

        if end_time is None:
            end_time = datetime.now(timezone.utc).replace(tzinfo=None)
            dynamic_end_time = True
        else:
            dynamic_end_time = False
            if end_time.tzinfo is not None:
                end_time = end_time.astimezone(timezone.utc).replace(tzinfo=None)

        start_time = end_time - timedelta(hours=window_hours)

        logger.info(
            f"[NewsBackfill] 开始回补 {format_beijing_time(start_time)} ~ "
            f"{format_beijing_time(end_time)} 北京时间（最多 {window_hours} 小时）"
        )
        if self.scorer.enabled and not should_score:
            logger.info("[NewsBackfill] 历史 LLM 打分已关闭，仅保存源端新闻；常规扫描仍会打分当前窗口")

        all_records: list[NewsRecord] = []
        range_start = start_time
        range_end = end_time
        scan_interval = max(1, int(config.SCAN_INTERVALS.get("news", 5)))
        catchup_rounds = max(1, int(getattr(config, "NEWS_BACKFILL_CATCHUP_ROUNDS", 4)))

        for round_idx in range(catchup_rounds):
            range_records = self.backfill_range(
                range_start,
                range_end,
                score_records=should_score,
            )
            all_records.extend(range_records)

            if not dynamic_end_time:
                break

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if (now - range_end).total_seconds() < scan_interval * 60:
                break
            if round_idx >= catchup_rounds - 1:
                logger.warning(
                    f"[NewsBackfill] 追赶轮数已用尽（{catchup_rounds} 轮），"
                    f"仍落后 {(now - range_end).total_seconds() / 60:.1f} 分钟；"
                    "下次启动或手动新闻回补会继续补齐"
                )
                break
            range_start = range_end
            range_end = now

        logger.info(f"[NewsBackfill] 回补完成，源端返回 {len(all_records)} 条")
        return all_records

    def backfill_range(
        self,
        start_time: datetime,
        end_time: datetime,
        score_records: bool | None = None,
    ) -> list[NewsRecord]:
        """回补指定 UTC 时间段内可见新闻；默认不做历史 LLM 打分。"""
        if start_time.tzinfo is not None:
            start_time = start_time.astimezone(timezone.utc).replace(tzinfo=None)
        if end_time.tzinfo is not None:
            end_time = end_time.astimezone(timezone.utc).replace(tzinfo=None)
        if end_time <= start_time:
            logger.info("[NewsBackfill] 回补区间为空，跳过")
            return []

        should_score = bool(
            getattr(config, "NEWS_BACKFILL_LLM_ENABLED", False)
            if score_records is None
            else score_records
        )
        if self.scorer.enabled and not should_score:
            logger.info("[NewsBackfill] 历史 LLM 打分已关闭，仅保存源端新闻")

        range_records = self._fetch_backfill_range(start_time, end_time)
        range_records = self._filter_existing_records(range_records)
        if not range_records:
            logger.info(
                f"[NewsBackfill] 区间 {format_beijing_time(start_time)} ~ "
                f"{format_beijing_time(end_time)} 无可回补新闻"
            )
            return []

        range_records.sort(key=lambda r: r.published_at or datetime.min, reverse=True)
        if should_score and self.scorer.enabled:
            range_records = self.scorer.enrich_batch(range_records)
        range_records.sort(key=lambda r: r.published_at or datetime.min, reverse=True)
        inserted = self._save_records(range_records, end_time, skip_existing=True)
        logger.info(
            f"[NewsBackfill] 区间 {format_beijing_time(start_time)} ~ "
            f"{format_beijing_time(end_time)} 返回 {len(range_records)} 条，新增 {inserted} 条"
        )
        return range_records

    def _fetch_backfill_range(self, start_time: datetime, end_time: datetime) -> list[NewsRecord]:
        records: list[NewsRecord] = []
        for source in self.sources:
            try:
                logger.info(
                    f"[NewsBackfill] 回补 {source.name}: "
                    f"{format_beijing_time(start_time)} ~ {format_beijing_time(end_time)} 北京时间"
                )
                fetch_backfill = getattr(source, "fetch_backfill", None)
                if callable(fetch_backfill):
                    source_records = fetch_backfill(start_time, end_time)
                else:
                    source_records = source.fetch()
                    source_records = [
                        r for r in source_records
                        if r.published_at is not None and start_time <= r.published_at < end_time
                    ]
                self._record_source_status(source.name, source_records, stage="backfill")
                records.extend(source_records)
                logger.info(f"[NewsBackfill] {source.name} 返回 {len(source_records)} 条")
            except Exception as e:
                self._record_source_error(source.name, e, stage="backfill")
                logger.error(f"[NewsBackfill] {source.name} 回补失败: {e}")
        return records

    @staticmethod
    def _filter_existing_records(records: list[NewsRecord]) -> list[NewsRecord]:
        if not records:
            return []

        source_ids = sorted({r.source_id for r in records if r.source_id})
        existing_keys: set[tuple[str, str]] = set()
        if source_ids:
            session = get_session()
            try:
                for chunk in _chunks(source_ids):
                    rows = session.query(NewsItem.source, NewsItem.source_id).filter(
                        NewsItem.source_id.in_(chunk),
                    ).all()
                    existing_keys.update((source, source_id) for source, source_id in rows)
            finally:
                session.close()

        filtered: list[NewsRecord] = []
        seen_keys: set[tuple[str, str]] = set()
        for record in records:
            if not record.source_id:
                filtered.append(record)
                continue
            key = (record.source, record.source_id)
            if key in existing_keys or key in seen_keys:
                continue
            seen_keys.add(key)
            filtered.append(record)
        return filtered

    @staticmethod
    def _filter_scan_window(records: list[NewsRecord], scan_time: datetime) -> list[NewsRecord]:
        """只保留目标 5min 发布时间窗口内的新闻；RSS 返回的旧列表项不进入打分/告警。

        目标窗口采用当前 bucket 的上一根已收盘 5min bar：
        例如 15:49 扫描时处理 15:40-15:45。
        这样新闻窗口与价格窗口都使用已经稳定落地的已收盘区间。
        """
        window_minutes = max(1, int(config.SCAN_INTERVALS.get("news", 5)))
        window_start, window_end = NewsScanner._target_scan_window(scan_time, window_minutes)
        filtered = [
            r for r in records
            if r.published_at is None or (window_start <= r.published_at < window_end)
        ]
        skipped = len(records) - len(filtered)
        if skipped:
            logger.info(
                f"[NewsScanner] 过滤扫描窗口外新闻 {skipped} 条，"
                f"目标窗口: {format_beijing_time(window_start)} ~ {format_beijing_time(window_end)} 北京时间"
            )
        return filtered

    @staticmethod
    def _target_scan_window(scan_time: datetime, window_minutes: int) -> tuple[datetime, datetime]:
        """返回当前 bucket 的上一根已收盘 5min 新闻窗口。

        示例：15:49 -> floor 到 15:45，目标窗口是上一根已收盘 bar：15:40-15:45。
        """
        minute = (scan_time.minute // window_minutes) * window_minutes
        current_bucket_start = scan_time.replace(minute=minute, second=0, microsecond=0)
        window_end = current_bucket_start
        window_start = current_bucket_start - timedelta(minutes=window_minutes)
        return window_start, window_end

    def _save_records(self, records: list[NewsRecord], scan_time: datetime, skip_existing: bool = False) -> int:
        """写入数据库，返回实际入库数。每个 5min window 的新闻独立保存。"""
        if not records:
            return 0

        session = get_session()
        saved = 0
        try:
            existing_keys: set[tuple[str, str]] = set()
            if skip_existing:
                source_ids = sorted({r.source_id for r in records if r.source_id})
                if source_ids:
                    for chunk in _chunks(source_ids):
                        existing_rows = session.query(NewsItem.source, NewsItem.source_id).filter(
                            NewsItem.source_id.in_(chunk),
                        ).all()
                        existing_keys.update((source, source_id) for source, source_id in existing_rows)

            seen_keys: set[tuple[str, str]] = set()
            for r in records:
                key = (r.source, r.source_id)
                if skip_existing and r.source_id:
                    if key in existing_keys or key in seen_keys:
                        continue
                    seen_keys.add(key)

                # timestamp 优先使用源端发布时间，回退到扫描时间
                item_ts = r.published_at if r.published_at else scan_time
                if item_ts.tzinfo is not None:
                    item_ts = item_ts.astimezone(timezone.utc).replace(tzinfo=None)
                item = NewsItem(
                    timestamp=item_ts,
                    source=r.source,
                    source_id=r.source_id,
                    title=r.title[:500],
                    content=r.content,
                    url=r.url,
                    importance=r.importance,
                    llm_importance=r.llm_importance,
                    llm_importance_reason=r.llm_importance_reason,
                    llm_model=r.llm_model,
                    llm_scored_at=r.llm_scored_at,
                    language=r.language,
                    categories=r.categories,
                    # 出生即定：传统市场开没开，纯日历，不依赖打标（news-impact-engine Phase 1，
                    # 是后续"可打标"判断与台账滤休市的前置条件）。
                    traditional_open=market_calendar.is_traditional_open(item_ts),
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
