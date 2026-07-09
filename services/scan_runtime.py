from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

import config

ROOT_DIR = Path(__file__).resolve().parents[1]
SCAN_LOCK_PATH = str(ROOT_DIR / ".scan.lock")
SCAN_LOCK_STALE_SECONDS = 6 * 60
SCAN_START_DELAY_SECONDS = 10

_PROXY_ENV_CONFIGURED = False


def configure_proxy_env() -> None:
    """Set process-wide proxy environment once for libraries that read env vars."""
    global _PROXY_ENV_CONFIGURED
    if _PROXY_ENV_CONFIGURED:
        return
    if config.PROXY:
        os.environ.setdefault("HTTP_PROXY", config.PROXY)
        os.environ.setdefault("HTTPS_PROXY", config.PROXY)
        logger.info(f"代理已启用: {config.PROXY}")
    else:
        logger.info("代理不可用，使用直连模式")
    _PROXY_ENV_CONFIGURED = True


def next_aligned_run_time(
    interval_minutes: int,
    now: datetime | None = None,
    delay_seconds: int = SCAN_START_DELAY_SECONDS,
) -> datetime:
    """Return the next interval boundary plus a small delay after the bar closes."""
    interval_minutes = max(1, int(interval_minutes))
    delay_seconds = max(0, int(delay_seconds))
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    current_minute = now.hour * 60 + now.minute
    boundary = now.replace(second=0, microsecond=0)
    if current_minute % interval_minutes:
        next_minute = ((current_minute // interval_minutes) + 1) * interval_minutes
        day_offset, minute_of_day = divmod(next_minute, 24 * 60)
        hour, minute = divmod(minute_of_day, 60)
        boundary = (boundary + timedelta(days=day_offset)).replace(hour=hour, minute=minute)

    candidate = boundary + timedelta(seconds=delay_seconds)
    if candidate <= now:
        candidate += timedelta(minutes=interval_minutes)
    return candidate


def recent_closed_interval_window(
    interval_minutes: int,
    intervals: int,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Return the last N fully closed intervals ending at the current interval boundary."""
    interval_minutes = max(1, int(interval_minutes))
    intervals = max(1, int(intervals))
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)

    minute_of_day = now.hour * 60 + now.minute
    boundary_minute = (minute_of_day // interval_minutes) * interval_minutes
    boundary_hour, boundary_minute_of_hour = divmod(boundary_minute, 60)
    window_end = now.replace(
        hour=boundary_hour,
        minute=boundary_minute_of_hour,
        second=0,
        microsecond=0,
    )
    window_start = window_end - timedelta(minutes=interval_minutes * intervals)
    return window_start, window_end


def _process_exists(pid: int) -> bool:
    """Return whether pid appears to be alive on Windows."""
    if pid <= 0:
        return False
    try:
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return True


def _read_lock_pid() -> int | None:
    try:
        with open(SCAN_LOCK_PATH, "r", encoding="utf-8") as f:
            content = f.read(200)
    except OSError:
        return None
    match = re.search(r"pid=(\d+)", content)
    return int(match.group(1)) if match else None


@contextmanager
def _scan_lock():
    """Cross-process scan lock. If another scan is running, skip this trigger."""
    fd = None
    acquired = False

    def _try_create_lock() -> int | None:
        try:
            new_fd = os.open(SCAN_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            return None
        os.write(
            new_fd,
            f"pid={os.getpid()} started={time.time()} started_iso={time.strftime('%Y-%m-%dT%H:%M:%S')}\n".encode("utf-8"),
        )
        return new_fd

    try:
        try:
            fd = _try_create_lock()
            acquired = fd is not None
            if not acquired:
                raise FileExistsError(SCAN_LOCK_PATH)
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(SCAN_LOCK_PATH)
                lock_pid = _read_lock_pid()
                if lock_pid is not None:
                    if not _process_exists(lock_pid):
                        logger.warning(f"[Scan] 发现残留扫描锁（pid={lock_pid} 不存在），清理: {SCAN_LOCK_PATH}")
                        os.unlink(SCAN_LOCK_PATH)
                        fd = _try_create_lock()
                        acquired = fd is not None
                        if not acquired:
                            logger.warning("[Scan] 残留锁清理后被其他扫描抢先获取，本次触发跳过")
                    else:
                        logger.warning(f"[Scan] 已有扫描正在运行，本次触发跳过 (pid={lock_pid}, age={age:.0f}s)")
                elif age > SCAN_LOCK_STALE_SECONDS:
                    logger.warning(f"[Scan] 发现过期扫描锁，清理: {SCAN_LOCK_PATH}")
                    os.unlink(SCAN_LOCK_PATH)
                    fd = _try_create_lock()
                    acquired = fd is not None
                    if not acquired:
                        logger.warning("[Scan] 过期锁清理后被其他扫描抢先获取，本次触发跳过")
                else:
                    logger.warning(f"[Scan] 已有扫描正在运行，本次触发跳过 (age={age:.0f}s)")
            except FileNotFoundError:
                fd = _try_create_lock()
                acquired = fd is not None

        yield acquired
    finally:
        if fd is not None:
            os.close(fd)
        if acquired:
            try:
                os.unlink(SCAN_LOCK_PATH)
            except FileNotFoundError:
                pass


def run_scan_once():
    """执行一次完整的扫描周期（价格 + 新闻 + 预测市场 + 告警评估）"""
    configure_proxy_env()
    with _scan_lock() as acquired:
        if not acquired:
            run_scan_once.last_skipped = True
            run_scan_once.last_source_statuses = {}
            return [], [], []
        run_scan_once.last_skipped = False
        scan_started_at = datetime.now(timezone.utc).replace(tzinfo=None)

        from database import create_tables

        create_tables(run_migrations=False, seed_defaults=False)

        from alerts.engine import AlertEngine
        from scanners.news_scanner import NewsScanner
        from scanners.prediction_scanner import PredictionScanner
        from scanners.price_scanner import PriceScanner

        alert_engine = AlertEngine()

        logger.info("=" * 50)
        logger.info("[Scan] 开始价格扫描...")
        price_scanner = PriceScanner()
        price_records = price_scanner.scan()

        logger.info("[Scan] 开始新闻扫描...")
        news_scanner = NewsScanner()
        news_records = news_scanner.scan()

        try:
            _run_rolling_backfill(price_scanner, news_scanner, scan_started_at)
        except Exception as exc:
            logger.exception(f"[ScanCatchup] rolling backfill failed, continuing scan: {exc}")

        logger.info("[Scan] 开始预测市场扫描...")
        pred_scanner = PredictionScanner()
        pred_records = pred_scanner.scan()

        source_statuses = {
            "price": _source_status_payload(price_scanner),
            "news": _source_status_payload(news_scanner),
            "prediction": _source_status_payload(pred_scanner),
        }
        run_scan_once.last_source_statuses = source_statuses
        _log_source_statuses(source_statuses)

        logger.info("[Scan] 评估告警规则...")
        alert_engine.evaluate_all(
            price_records=price_records,
            news_records=news_records,
            prediction_records=pred_records,
        )

        logger.info(
            f"[Scan] 扫描完成: 价格 {len(price_records)} | "
            f"新闻 {len(news_records)} | 预测 {len(pred_records)}"
        )
        return price_records, news_records, pred_records


def _source_status_payload(scanner) -> list[dict]:
    return [asdict(status) for status in getattr(scanner, "source_statuses", [])]


def _log_source_statuses(source_statuses: dict[str, list[dict]]) -> None:
    for group, statuses in source_statuses.items():
        failed = [s for s in statuses if not s["ok"]]
        empty = [s for s in statuses if s["ok"] and s["empty"]]
        if failed:
            names = ", ".join(f"{s['source']} ({s['error']})" for s in failed)
            logger.warning("[ScanSource] {} failed: {}", group, names)
        if empty:
            names = ", ".join(s["source"] for s in empty)
            logger.info("[ScanSource] {} returned 0 rows: {}", group, names)


def _run_rolling_backfill(price_scanner, news_scanner, now: datetime):
    """每轮扫描后补最近几个已收盘 interval，只写库，不参与本轮告警评估。"""
    intervals = max(0, int(getattr(config, "SCAN_ROLLING_BACKFILL_INTERVALS", 2)))
    if intervals <= 0:
        return

    price_interval = max(1, int(config.SCAN_INTERVALS.get("price", 5)))
    price_start, price_end = recent_closed_interval_window(price_interval, intervals, now)
    logger.info(
        f"[ScanCatchup] 回补最近 {intervals} 个价格 interval: "
        f"{price_start.isoformat()} - {price_end.isoformat()} UTC"
    )
    price_scanner.backfill_range(price_start, price_end)

    news_interval = max(1, int(config.SCAN_INTERVALS.get("news", 5)))
    news_start, news_end = recent_closed_interval_window(news_interval, intervals, now)
    logger.info(
        f"[ScanCatchup] 回补最近 {intervals} 个新闻 interval: "
        f"{news_start.isoformat()} - {news_end.isoformat()} UTC"
    )
    news_scanner.backfill_range(news_start, news_end, score_records=False)


def run_price_backfill_once(max_hours: int | None = None):
    """执行一次价格历史回补；与常规扫描共用 `.scan.lock` 防止并发。"""
    configure_proxy_env()
    with _scan_lock() as acquired:
        if not acquired:
            return []

        from database import create_tables

        create_tables()

        from scanners.price_scanner import PriceScanner

        price_scanner = PriceScanner()
        return price_scanner.backfill_missing_history(max_hours=max_hours)


def run_news_backfill_once(max_hours: int | None = None):
    """执行一次新闻历史回补；与常规扫描共用 `.scan.lock` 防止并发。"""
    configure_proxy_env()
    with _scan_lock() as acquired:
        if not acquired:
            return []

        from database import create_tables

        create_tables()

        from scanners.news_scanner import NewsScanner

        news_scanner = NewsScanner()
        return news_scanner.backfill_missing_history(max_hours=max_hours)


def run_startup_backfill_once():
    """启动后回补价格与新闻；单次持有扫描锁，避免长回补期间并发扫描。"""
    configure_proxy_env()
    with _scan_lock() as acquired:
        if not acquired:
            return [], []

        from database import create_tables

        create_tables()

        from scanners.news_scanner import NewsScanner
        from scanners.price_scanner import PriceScanner

        started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        price_scanner = PriceScanner()
        news_scanner = NewsScanner()

        price_records = price_scanner.backfill_missing_history()
        news_records = news_scanner.backfill_missing_history()

        finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        scan_interval = max(1, int(config.SCAN_INTERVALS.get("price", 5)))
        elapsed_seconds = (finished_at - started_at).total_seconds()
        if elapsed_seconds >= scan_interval * 60:
            catchup_hours = max(1, int((elapsed_seconds + 3599) // 3600))
            logger.info(
                f"[StartupBackfill] 启动回补耗时 {elapsed_seconds / 60:.1f} 分钟，"
                f"追加回补最近 {catchup_hours} 小时价格缺口"
            )
            price_records.extend(
                price_scanner.backfill_missing_history(max_hours=catchup_hours, end_time=finished_at)
            )
        return price_records, news_records
