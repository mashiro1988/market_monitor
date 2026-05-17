"""
Investment Agent - 运行入口

命令：
  python run.py app        启动 FastAPI + React 仪表板（http://localhost:8000）
  python run.py api-dev    启动 FastAPI 开发服务（不自动构建前端）
  python run.py frontend-build  构建 React 静态产物
  python run.py setup      初始化数据库
  python run.py schedule   启动 5 分钟频率定时扫描 + 告警
  python run.py scan       执行一次扫描（不启动调度器）
"""
import os
import argparse
import sys
import subprocess
import time
import re
import webbrowser
import shutil
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from loguru import logger

# 加载配置（config 会自动检测代理并设置/清除环境变量）
import config
if config.PROXY:
    os.environ.setdefault("HTTP_PROXY", config.PROXY)
    os.environ.setdefault("HTTPS_PROXY", config.PROXY)
    logger.info(f"代理已启用: {config.PROXY}")
else:
    logger.info("代理不可用，使用直连模式")


SCAN_LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".scan.lock")
SCAN_LOCK_STALE_SECONDS = 6 * 60
SCAN_START_DELAY_SECONDS = 10
ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT_DIR / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"


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
    try:
        try:
            fd = os.open(SCAN_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, f"pid={os.getpid()} started={time.time()}\n".encode("utf-8"))
            acquired = True
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(SCAN_LOCK_PATH)
                lock_pid = _read_lock_pid()
                if lock_pid is not None:
                    if not _process_exists(lock_pid):
                        logger.warning(f"[Scan] 发现残留扫描锁（pid={lock_pid} 不存在），清理: {SCAN_LOCK_PATH}")
                        os.unlink(SCAN_LOCK_PATH)
                        fd = os.open(SCAN_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                        os.write(fd, f"pid={os.getpid()} started={time.time()} started_iso={time.strftime('%Y-%m-%dT%H:%M:%S')}\n".encode("utf-8"))
                        acquired = True
                    else:
                        logger.warning(f"[Scan] 已有扫描正在运行，本次触发跳过 (pid={lock_pid}, age={age:.0f}s)")
                elif age > SCAN_LOCK_STALE_SECONDS:
                    logger.warning(f"[Scan] 发现过期扫描锁，清理: {SCAN_LOCK_PATH}")
                    os.unlink(SCAN_LOCK_PATH)
                    fd = os.open(SCAN_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                    os.write(fd, f"pid={os.getpid()} started={time.time()} started_iso={time.strftime('%Y-%m-%dT%H:%M:%S')}\n".encode("utf-8"))
                    acquired = True
                else:
                    logger.warning(f"[Scan] 已有扫描正在运行，本次触发跳过 (age={age:.0f}s)")
            except FileNotFoundError:
                fd = os.open(SCAN_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(fd, f"pid={os.getpid()} started={time.time()} started_iso={time.strftime('%Y-%m-%dT%H:%M:%S')}\n".encode("utf-8"))
                acquired = True

        yield acquired
    finally:
        if fd is not None:
            os.close(fd)
        if acquired:
            try:
                os.unlink(SCAN_LOCK_PATH)
            except FileNotFoundError:
                pass


def _npm_cmd() -> str:
    if os.name != "nt":
        npm = shutil.which("npm")
        if npm:
            return npm
        raise RuntimeError("找不到 npm，请先安装 Node.js 并确认 npm 在 PATH 中")

    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm:
        return npm

    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "npm.cmd",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "nodejs" / "npm.cmd",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    raise RuntimeError(
        "找不到 npm.cmd。请安装 Node.js，或使用完整路径运行："
        r'"C:\Program Files\nodejs\npm.cmd" install'
    )


def _npm_env() -> dict[str, str]:
    env = os.environ.copy()
    if os.name == "nt":
        node_dir = Path(_npm_cmd()).resolve().parent
        env["PATH"] = f"{node_dir};{env.get('PATH', '')}"
    return env


def build_frontend():
    """构建 React/Vite 前端静态产物。"""
    package_json = FRONTEND_DIR / "package.json"
    if not package_json.exists():
        raise RuntimeError("frontend/package.json 不存在，无法构建前端")
    logger.info("构建 React 前端...")
    subprocess.run([_npm_cmd(), "run", "build"], cwd=str(FRONTEND_DIR), check=True, env=_npm_env())
    logger.info("前端构建完成")


def ensure_frontend_dist():
    """如果缺少 frontend/dist，则自动构建；构建失败时明确退出。"""
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return
    logger.warning("frontend/dist 不存在，尝试自动构建前端")
    try:
        build_frontend()
    except Exception as exc:
        logger.error(f"前端自动构建失败: {exc}")
        raise SystemExit(1) from exc


def run_fastapi_app():
    """启动 FastAPI + React 静态仪表板。"""
    from database import create_tables

    logger.info("启动 Market Monitor 仪表板: http://localhost:8000")
    create_tables()
    ensure_frontend_dist()
    webbrowser.open("http://localhost:8000")
    import uvicorn

    uvicorn.run("api.app:app", host="127.0.0.1", port=8000, reload=False)


def run_api_dev():
    """启动 FastAPI 开发服务，不自动构建前端。"""
    from database import create_tables
    import uvicorn

    create_tables()
    uvicorn.run("api.app:dev_app", host="127.0.0.1", port=8000, reload=True)


def setup_database():
    """初始化数据库"""
    from database import create_tables
    logger.info("初始化数据库...")
    create_tables()
    logger.info("数据库初始化完成")


def run_scan_once():
    """执行一次完整的扫描周期（价格 + 新闻 + 预测市场 + 告警评估）"""
    with _scan_lock() as acquired:
        if not acquired:
            run_scan_once.last_skipped = True
            return [], [], []
        run_scan_once.last_skipped = False
        scan_started_at = datetime.now(timezone.utc).replace(tzinfo=None)

        from database import create_tables
        create_tables()

        from scanners.price_scanner import PriceScanner
        from scanners.news_scanner import NewsScanner
        from scanners.prediction_scanner import PredictionScanner
        from alerts.engine import AlertEngine

        alert_engine = AlertEngine()

        # 1. 价格扫描
        logger.info("=" * 50)
        logger.info("[Scan] 开始价格扫描...")
        price_scanner = PriceScanner()
        price_records = price_scanner.scan()

        # 2. 新闻扫描
        logger.info("[Scan] 开始新闻扫描...")
        news_scanner = NewsScanner()
        news_records = news_scanner.scan()

        _run_rolling_backfill(price_scanner, news_scanner, scan_started_at)

        # 3. 预测市场扫描
        logger.info("[Scan] 开始预测市场扫描...")
        pred_scanner = PredictionScanner()
        pred_records = pred_scanner.scan()

        # 4. 告警评估
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
    with _scan_lock() as acquired:
        if not acquired:
            return [], []

        from database import create_tables
        create_tables()

        from scanners.price_scanner import PriceScanner
        from scanners.news_scanner import NewsScanner

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


def start_scheduler():
    """启动 5 分钟频率定时扫描 + 每小时摘要 + 板块管道（puller + sector_scan + cmc_bootstrap）"""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    from database import create_tables

    create_tables()

    logger.info("启动 Investment Agent 定时扫描器...")

    scheduler = BackgroundScheduler(timezone="UTC")

    from alerts.engine import AlertEngine

    alert_engine = AlertEngine()

    def scan_cycle():
        """一次完整的扫描周期"""
        try:
            logger.info(f"[Scheduler] 扫描周期开始 {time.strftime('%Y-%m-%d %H:%M:%S')}")
            price_records, news_records, pred_records = run_scan_once()

            logger.info(
                f"[Scheduler] 周期完成: 价格 {len(price_records)} | "
                f"新闻 {len(news_records)} | 预测 {len(pred_records)}"
            )
        except Exception as e:
            logger.error(f"[Scheduler] 扫描周期异常: {e}")

    def hourly_summary():
        """每小时市场摘要"""
        try:
            alert_engine.send_hourly_summary()
        except Exception as e:
            logger.error(f"[Scheduler] 每小时摘要失败: {e}")

    def startup_backfill():
        """启动后回补最近最多 72 小时价格与新闻历史。"""
        try:
            price_records, news_records = run_startup_backfill_once()
            logger.info(
                f"[Scheduler] 启动回补完成，价格源端返回 {len(price_records)} 条，"
                f"新闻源端返回 {len(news_records)} 条"
            )
        except Exception as e:
            logger.error(f"[Scheduler] 启动回补失败: {e}")

    def sector_scan_cycle():
        """板块扫描周期：读本地 BMAC 缓存 + DB 板块映射 → 算板块涨跌入库。
        独立 max_instances=1 锁，跟 5min 主扫描并行也不互斥。"""
        try:
            from scanners.sector_scanner import SectorScanner
            stats = SectorScanner().scan()
            logger.info(f"[Scheduler] 板块扫描完成: {stats}")
        except Exception as e:
            logger.error(f"[Scheduler] 板块扫描异常: {e}")

    def cmc_bootstrap():
        """启动 10s 后检查 CMC 板块映射是否过期，过期则刷新（~2min）。"""
        try:
            from database import SessionLocal
            from services import cmc_client
            session = SessionLocal()
            try:
                if cmc_client.needs_refresh(session):
                    logger.info("[Scheduler] CMC 板块映射过期/为空，开始刷新...")
                    result = cmc_client.refresh_categories(session=session)
                    logger.info(f"[Scheduler] CMC 板块刷新完成: {result}")
                else:
                    logger.info("[Scheduler] CMC 板块映射仍在 TTL 内，跳过")
            finally:
                session.close()
        except Exception as e:
            logger.error(f"[Scheduler] CMC bootstrap 失败: {e}")

    # 添加5分钟扫描任务
    price_interval = max(1, int(config.SCAN_INTERVALS.get("price", 5)))
    first_scan_time = next_aligned_run_time(price_interval)
    scheduler.add_job(
        scan_cycle,
        IntervalTrigger(minutes=price_interval, start_date=first_scan_time),
        id="scan_cycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        startup_backfill,
        "date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=1),
        id="startup_backfill",
        replace_existing=True,
        max_instances=1,
    )

    # 添加每小时摘要任务
    first_summary_time = next_aligned_run_time(60)
    scheduler.add_job(
        hourly_summary,
        IntervalTrigger(hours=1, start_date=first_summary_time),
        id="hourly_summary",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # 板块扫描：每小时 :32 跑（BMAC 30m 偏移在 :30 落 .ready，留 2 min 给 remote_puller 拉完）
    scheduler.add_job(
        sector_scan_cycle,
        CronTrigger(minute=32),
        id="sector_scan_cycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # CMC 板块映射启动检查（10s 后跑一次，过期会触发刷新 ~2min）
    scheduler.add_job(
        cmc_bootstrap,
        "date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=10),
        id="cmc_bootstrap",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.start()

    # 启动 remote_puller 守护线程（不在 scheduler 里，自己 60s 轮询）
    try:
        from services.remote_puller import start_puller
        start_puller()
    except Exception as e:
        logger.warning(f"[Scheduler] remote_puller 启动失败: {e}；板块数据将停留在缓存")

    logger.info(
        f"[Scheduler] 定时扫描已启动（每 {price_interval} 分钟），"
        f"首次扫描: {first_scan_time.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')} 北京时间；"
        f"首次摘要: {first_summary_time.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')} 北京时间。Ctrl+C 退出"
    )
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("停止定时扫描器...")
        try:
            from services.remote_puller import stop_puller
            stop_puller()
        except Exception:
            pass
        scheduler.shutdown()


def main():
    parser = argparse.ArgumentParser(description="Investment Agent - 宏观市场监控系统")
    parser.add_argument(
        "action",
        nargs="?",
        choices=["app", "api-dev", "frontend-build", "setup", "schedule", "scan"],
        help="操作: app(仪表板), api-dev(API开发服务), frontend-build(构建前端), setup(初始化DB), schedule(定时扫描), scan(单次扫描)",
    )
    args = parser.parse_args()

    if args.action is None:
        show_menu()
    else:
        execute(args.action)


def show_menu():
    """交互式菜单"""
    print("Investment Agent - 宏观市场监控系统")
    print("=" * 50)
    print("1. 启动仪表板 (app)")
    print("2. 启动 API 开发服务 (api-dev)")
    print("3. 构建前端 (frontend-build)")
    print("4. 启动定时扫描 (schedule)")
    print("5. 执行单次扫描 (scan)")
    print("6. 初始化数据库 (setup)")
    print("7. 退出")
    print("=" * 50)

    while True:
        try:
            choice = input("请选择 (1-7): ").strip()
            actions = {
                "1": "app",
                "2": "api-dev",
                "3": "frontend-build",
                "4": "schedule",
                "5": "scan",
                "6": "setup",
            }
            if choice == "7":
                break
            elif choice in actions:
                execute(actions[choice])
                break
            else:
                print("无效选项")
        except (KeyboardInterrupt, EOFError):
            break


def execute(action: str):
    """执行指定操作"""
    dispatch = {
        "app": run_fastapi_app,
        "api-dev": run_api_dev,
        "frontend-build": build_frontend,
        "setup": setup_database,
        "schedule": start_scheduler,
        "scan": run_scan_once,
    }
    fn = dispatch.get(action)
    if fn:
        fn()
    else:
        logger.error(f"未知操作: {action}")
        sys.exit(1)


if __name__ == "__main__":
    main()
