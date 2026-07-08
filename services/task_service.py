from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from schemas.tasks import TaskStatus
from services.time_utils import timestamp_pair, utc_now_naive

TASK_TTL = timedelta(hours=24)
_TASKS: dict[str, "TaskRecord"] = {}
_TASK_LOCK = threading.Lock()
_RUNNING_SCAN_ID: str | None = None


@dataclass
class TaskRecord:
    task_id: str
    status: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    message: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    thread: threading.Thread | None = field(default=None, repr=False)


def _cleanup_locked() -> None:
    cutoff = utc_now_naive() - TASK_TTL
    for task_id in list(_TASKS.keys()):
        record = _TASKS[task_id]
        if record.created_at < cutoff and record.status in {"succeeded", "skipped", "failed"}:
            del _TASKS[task_id]


def _schema(record: TaskRecord) -> TaskStatus:
    return TaskStatus(
        task_id=record.task_id,
        status=record.status,
        created_at=timestamp_pair(record.created_at),
        started_at=timestamp_pair(record.started_at) if record.started_at else None,
        finished_at=timestamp_pair(record.finished_at) if record.finished_at else None,
        message=record.message,
        result=record.result,
        error=record.error,
    )


def get_task(task_id: str) -> TaskStatus | None:
    with _TASK_LOCK:
        _cleanup_locked()
        record = _TASKS.get(task_id)
        return _schema(record) if record else None


def all_tasks() -> list[TaskStatus]:
    with _TASK_LOCK:
        _cleanup_locked()
        return [_schema(record) for record in sorted(_TASKS.values(), key=lambda item: item.created_at, reverse=True)]


def create_scan_task() -> TaskStatus:
    global _RUNNING_SCAN_ID
    with _TASK_LOCK:
        _cleanup_locked()
        if _RUNNING_SCAN_ID:
            running = _TASKS.get(_RUNNING_SCAN_ID)
            if running and running.status in {"queued", "running"}:
                record = TaskRecord(
                    task_id=str(uuid.uuid4()),
                    status="skipped",
                    created_at=utc_now_naive(),
                    finished_at=utc_now_naive(),
                    message=f"已有扫描任务正在运行，复用锁语义跳过。本次不排队。running_task={_RUNNING_SCAN_ID}",
                )
                _TASKS[record.task_id] = record
                return _schema(record)

        record = TaskRecord(task_id=str(uuid.uuid4()), status="queued", created_at=utc_now_naive(), message="扫描已创建")
        _TASKS[record.task_id] = record
        _RUNNING_SCAN_ID = record.task_id
        thread = threading.Thread(target=_run_scan_task, args=(record.task_id,), daemon=True)
        record.thread = thread
        thread.start()
        return _schema(record)


def _run_scan_task(task_id: str) -> None:
    global _RUNNING_SCAN_ID
    with _TASK_LOCK:
        record = _TASKS[task_id]
        record.status = "running"
        record.started_at = utc_now_naive()
        record.message = "扫描运行中"

    try:
        from services.scan_runtime import run_scan_once

        price_records, news_records, prediction_records = run_scan_once()
        skipped_by_lock = bool(getattr(run_scan_once, "last_skipped", False))
        result = {
            "price_records": len(price_records),
            "news_records": len(news_records),
            "prediction_records": len(prediction_records),
            "source_statuses": getattr(run_scan_once, "last_source_statuses", {}),
        }
        with _TASK_LOCK:
            record = _TASKS[task_id]
            if skipped_by_lock:
                record.status = "skipped"
                record.message = "扫描锁未获取；本次任务不排队、不并发，按跳过记录"
            else:
                record.status = "succeeded"
                record.message = "扫描完成"
            record.result = result
            record.finished_at = utc_now_naive()
    except Exception as exc:
        logger.exception("[API Task] 扫描任务失败")
        with _TASK_LOCK:
            record = _TASKS[task_id]
            record.status = "failed"
            record.message = "扫描失败"
            record.error = str(exc)
            record.finished_at = utc_now_naive()
    finally:
        with _TASK_LOCK:
            if _RUNNING_SCAN_ID == task_id:
                _RUNNING_SCAN_ID = None
