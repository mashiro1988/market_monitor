"""Tests for scheduler scan window helpers."""
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.scan_runtime import recent_closed_interval_window

ROOT = Path(__file__).resolve().parents[1]


def test_recent_closed_interval_window_matches_2225_example():
    start, end = recent_closed_interval_window(5, 2, datetime(2026, 4, 29, 14, 25, 10))

    assert start == datetime(2026, 4, 29, 14, 15)
    assert end == datetime(2026, 4, 29, 14, 25)


def test_recent_closed_interval_window_matches_2220_example():
    start, end = recent_closed_interval_window(5, 2, datetime(2026, 4, 29, 14, 20, 10))

    assert start == datetime(2026, 4, 29, 14, 10)
    assert end == datetime(2026, 4, 29, 14, 20)


def test_recent_closed_interval_window_crosses_midnight():
    start, end = recent_closed_interval_window(5, 2, datetime(2026, 4, 29, 0, 2, 10))

    assert start == datetime(2026, 4, 28, 23, 50)
    assert end == datetime(2026, 4, 29, 0, 0)


def test_api_and_task_service_do_not_import_cli_run_module():
    app_source = (ROOT / "api/app.py").read_text(encoding="utf-8")
    task_source = (ROOT / "services/task_service.py").read_text(encoding="utf-8")

    forbidden = ("from run import", "import run")
    assert all(not line.strip().startswith(forbidden) for line in app_source.splitlines())
    assert all(not line.strip().startswith(forbidden) for line in task_source.splitlines())
