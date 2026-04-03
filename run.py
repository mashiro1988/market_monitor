"""
Investment Agent - 运行入口

命令：
  python run.py app        启动 Streamlit 仪表板
  python run.py collect    执行一次全量数据采集
  python run.py setup      初始化数据库
  python run.py schedule   启动 5 分钟频率定时扫描 + 告警
  python run.py scan       执行一次扫描（不启动调度器）
"""
import os
import argparse
import sys
import subprocess
import time
from loguru import logger

# 加载配置（config 会自动检测代理并设置/清除环境变量）
import config
if config.PROXY:
    os.environ.setdefault("HTTP_PROXY", config.PROXY)
    os.environ.setdefault("HTTPS_PROXY", config.PROXY)
    logger.info(f"代理已启用: {config.PROXY}")
else:
    logger.info("代理不可用，使用直连模式")


def run_streamlit():
    """启动 Streamlit 应用"""
    from database import create_tables
    logger.info("启动 Investment Agent 仪表板...")
    create_tables()
    subprocess.run([sys.executable, "-m", "streamlit", "run", "app.py"])


def collect_data():
    """兼容旧版：执行一次全量数据采集（旧 DataCollector）"""
    from database import create_tables
    from data_collector import DataCollector
    logger.info("执行旧版全量数据采集...")
    create_tables()
    collector = DataCollector()
    collector.collect_all_data()
    logger.info("旧版数据采集完成")


def setup_database():
    """初始化数据库（包括新表）"""
    from database import create_tables
    logger.info("初始化数据库...")
    create_tables()
    logger.info("数据库初始化完成（包含所有新旧表）")


def run_scan_once():
    """执行一次完整的扫描周期（价格 + 新闻 + 预测市场 + 告警评估）"""
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


def start_scheduler():
    """启动 5 分钟频率定时扫描 + 每小时摘要"""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from database import create_tables

    create_tables()

    logger.info("启动 Investment Agent 定时扫描器...")

    scheduler = BackgroundScheduler()

    from scanners.price_scanner import PriceScanner
    from scanners.news_scanner import NewsScanner
    from scanners.prediction_scanner import PredictionScanner
    from alerts.engine import AlertEngine

    alert_engine = AlertEngine()

    def scan_cycle():
        """一次完整的扫描周期"""
        try:
            logger.info("=" * 50)
            logger.info(f"[Scheduler] 扫描周期开始 {time.strftime('%Y-%m-%d %H:%M:%S')}")

            # 价格扫描
            price_records = []
            try:
                price_scanner = PriceScanner()
                price_records = price_scanner.scan()
            except Exception as e:
                logger.error(f"[Scheduler] 价格扫描失败: {e}")

            # 新闻扫描
            news_records = []
            try:
                news_scanner = NewsScanner()
                news_records = news_scanner.scan()
            except Exception as e:
                logger.error(f"[Scheduler] 新闻扫描失败: {e}")

            # 预测市场扫描
            pred_records = []
            try:
                pred_scanner = PredictionScanner()
                pred_records = pred_scanner.scan()
            except Exception as e:
                logger.error(f"[Scheduler] 预测市场扫描失败: {e}")

            # 告警评估
            try:
                alert_engine.evaluate_all(
                    price_records=price_records,
                    news_records=news_records,
                    prediction_records=pred_records,
                )
            except Exception as e:
                logger.error(f"[Scheduler] 告警评估失败: {e}")

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

    # 添加5分钟扫描任务
    price_interval = config.SCAN_INTERVALS.get("price", 5)
    scheduler.add_job(
        scan_cycle,
        IntervalTrigger(minutes=price_interval),
        id="scan_cycle",
        replace_existing=True,
        next_run_time=None,  # 先手动跑一次
    )

    # 添加每小时摘要任务
    scheduler.add_job(
        hourly_summary,
        IntervalTrigger(hours=1),
        id="hourly_summary",
        replace_existing=True,
    )

    scheduler.start()

    # 立即执行一次扫描
    logger.info("[Scheduler] 立即执行首次扫描...")
    scan_cycle()

    logger.info(f"[Scheduler] 定时扫描已启动（每 {price_interval} 分钟）。Ctrl+C 退出")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("停止定时扫描器...")
        scheduler.shutdown()


def main():
    parser = argparse.ArgumentParser(description="Investment Agent - 宏观市场监控系统")
    parser.add_argument(
        "action",
        nargs="?",
        choices=["app", "collect", "setup", "schedule", "scan"],
        help="操作: app(仪表板), collect(旧版采集), setup(初始化DB), schedule(定时扫描), scan(单次扫描)",
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
    print("2. 启动定时扫描 (schedule)")
    print("3. 执行单次扫描 (scan)")
    print("4. 初始化数据库 (setup)")
    print("5. 旧版数据采集 (collect)")
    print("6. 退出")
    print("=" * 50)

    while True:
        try:
            choice = input("请选择 (1-6): ").strip()
            actions = {"1": "app", "2": "schedule", "3": "scan", "4": "setup", "5": "collect"}
            if choice == "6":
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
        "app": run_streamlit,
        "collect": collect_data,
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
