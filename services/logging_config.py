"""Shared loguru setup for CLI and FastAPI entrypoints."""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

import config


_CONFIGURED = False
ROOT_DIR = Path(__file__).resolve().parents[1]


def configure_logging(*, force: bool = False) -> Path | None:
    """Configure console + optional rotating file logs once per process."""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return _log_path()

    logger.remove()
    logger.add(
        sys.stderr,
        level=config.LOG_LEVEL,
        backtrace=False,
        diagnose=False,
    )

    log_path = _log_path()
    if config.LOG_FILE_ENABLED and log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(log_path),
            level=config.LOG_LEVEL,
            rotation=config.LOG_ROTATION,
            retention=config.LOG_RETENTION,
            compression=config.LOG_COMPRESSION or None,
            encoding="utf-8",
            enqueue=True,
            backtrace=False,
            diagnose=False,
        )

    _CONFIGURED = True
    return log_path if config.LOG_FILE_ENABLED else None


def _log_path() -> Path | None:
    if not config.LOG_FILE_ENABLED:
        return None
    log_dir = Path(config.LOG_DIR)
    if not log_dir.is_absolute():
        log_dir = ROOT_DIR / log_dir
    return log_dir / config.LOG_FILE_NAME
