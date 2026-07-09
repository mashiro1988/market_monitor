"""
Investment Agent - 运行入口

命令：
  python run.py app        启动 FastAPI + React 仪表板（http://localhost:8000）
  python run.py api-dev    启动 FastAPI 开发服务（不自动构建前端）
  python run.py frontend-build  构建 React 静态产物
  python run.py setup      初始化数据库
  python run.py scan       执行一次扫描（不启动调度器）
"""
import os
import argparse
import sys
import subprocess
import webbrowser
import shutil
from pathlib import Path

from services.logging_config import configure_logging

configure_logging()

from loguru import logger

from services.scan_runtime import (
    configure_proxy_env,
    next_aligned_run_time,
    recent_closed_interval_window,
    run_news_backfill_once,
    run_price_backfill_once,
    run_scan_once,
    run_startup_backfill_once,
)

configure_proxy_env()

ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT_DIR / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"


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


def refresh_sectors_cli():
    """强制刷新 CMC 板块映射缓存（python run.py refresh-sectors）。

    无视 7 天 TTL，直接重新拉一遍白名单内所有板块的成分币。
    ~2 分钟（CMC 限速 + 白名单约 45 个板块 × 2.5s）。
    编辑 config.SECTOR_WHITELIST 后必须跑一次。
    """
    from database import create_tables, SessionLocal
    from services import cmc_client

    create_tables()
    session = SessionLocal()
    try:
        logger.info("强制刷新 CMC 板块映射...")
        result = cmc_client.refresh_categories(force=True, session=session)
        logger.info(f"完成: {result}")
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description="Investment Agent - 宏观市场监控系统")
    parser.add_argument(
        "action",
        nargs="?",
        choices=["app", "api-dev", "frontend-build", "setup", "scan", "refresh-sectors"],
        help="操作: app(仪表板), api-dev(API开发服务), frontend-build(构建前端), setup(初始化DB), "
             "scan(单次扫描), refresh-sectors(强制刷新 CMC 板块映射)",
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
    print("4. 执行单次扫描 (scan)")
    print("5. 初始化数据库 (setup)")
    print("6. 强制刷新 CMC 板块映射 (refresh-sectors)")
    print("7. 退出")
    print("=" * 50)

    while True:
        try:
            choice = input("请选择 (1-7): ").strip()
            actions = {
                "1": "app",
                "2": "api-dev",
                "3": "frontend-build",
                "4": "scan",
                "5": "setup",
                "6": "refresh-sectors",
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
        "scan": run_scan_once,
        "refresh-sectors": refresh_sectors_cli,
    }
    fn = dispatch.get(action)
    if fn:
        fn()
    else:
        logger.error(f"未知操作: {action}")
        sys.exit(1)


if __name__ == "__main__":
    main()
