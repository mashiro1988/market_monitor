"""Health alerts for the remote sector data pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy.engine import make_url

import config
from alerts.channels.wechat_work import WeChatWorkChannel
from database import SessionLocal
from models.alert_log import AlertLog
from services import remote_fs


RULE_NAME = "remote_data_monitor"


@dataclass
class RemoteHealthFinding:
    kind: str
    title: str
    content: str
    marker: str


def check_remote_data_health(
    *,
    stats: dict | None = None,
    exception: Exception | None = None,
    session=None,
    channel=None,
    now: datetime | None = None,
) -> list[dict]:
    """Inspect remote pipeline health and push WeCom alerts with cooldown."""
    if not getattr(config, "REMOTE_MONITORING_ENABLED", True):
        return []

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    findings = collect_remote_health_findings(stats=stats, exception=exception)
    if not findings:
        return []

    own_session = session is None
    session = session or SessionLocal()
    channel = channel or WeChatWorkChannel()
    sent: list[dict] = []
    try:
        for finding in findings:
            if _recently_delivered(session, finding.marker, now):
                continue
            message = f"{finding.marker}\n{finding.content}"
            delivered = channel.send(finding.title, finding.content)
            session.add(AlertLog(
                timestamp=now,
                rule_name=RULE_NAME,
                message=message[:8000],
                channel=getattr(channel, "name", "wechat_work"),
                delivered=delivered,
            ))
            sent.append({**asdict(finding), "delivered": delivered})
        if own_session:
            session.commit()
        else:
            session.flush()
    except Exception:
        if own_session:
            session.rollback()
        logger.exception("remote health alert failed")
        raise
    finally:
        if own_session:
            session.close()
    return sent


def collect_remote_health_findings(
    *,
    stats: dict | None = None,
    exception: Exception | None = None,
) -> list[RemoteHealthFinding]:
    findings: list[RemoteHealthFinding] = []
    if exception is not None:
        findings.append(_finding(
            "remote_data_cycle_failed",
            "远程数据周期失败",
            f"remote_data_cycle 直接异常：{type(exception).__name__}: {exception}",
        ))

    if stats:
        errors = stats.get("errors") or []
        if errors:
            findings.append(_finding(
                "remote_dataset_errors",
                "远程数据拉取失败",
                "以下 dataset 本轮拉取失败：" + ", ".join(map(str, errors)),
            ))
        sector_scan = stats.get("sector_scan")
        if isinstance(sector_scan, dict) and sector_scan.get("error"):
            findings.append(_finding(
                "sector_scan_failed",
                "板块扫描失败",
                f"pivot 已拉到，但 sector_scan 没写出新板块：{sector_scan['error']}",
            ))

    sftp_status = remote_fs.get_session_status()
    failures = int(sftp_status.get("consecutive_failures") or 0)
    threshold = max(1, int(getattr(config, "REMOTE_MONITOR_SFTP_FAILURE_THRESHOLD", 3)))
    if failures >= threshold:
        findings.append(_finding(
            "sftp_consecutive_failures",
            "SFTP 连续失败",
            f"SFTP 已连续失败 {failures} 次，阈值 {threshold}。最近错误：{sftp_status.get('last_error') or 'unknown'}",
        ))

    wal_path, wal_size = _sqlite_wal_size_bytes()
    max_bytes = max(1, int(getattr(config, "REMOTE_MONITOR_WAL_MAX_MB", 512))) * 1024 * 1024
    if wal_path is not None and wal_size is not None and wal_size > max_bytes:
        findings.append(_finding(
            "sqlite_wal_large",
            "SQLite WAL 过大",
            f"{wal_path} 当前 {wal_size / 1024 / 1024:.1f} MB，超过阈值 {max_bytes / 1024 / 1024:.0f} MB。",
        ))

    return findings


def _finding(kind: str, title: str, content: str) -> RemoteHealthFinding:
    return RemoteHealthFinding(
        kind=kind,
        title=title,
        content=content,
        marker=f"remote-monitor:{kind}",
    )


def _recently_delivered(session, marker: str, now: datetime) -> bool:
    cooldown = max(1, int(getattr(config, "REMOTE_MONITOR_ALERT_COOLDOWN_MINUTES", 60)))
    cutoff = now - timedelta(minutes=cooldown)
    rows = (
        session.query(AlertLog.message)
        .filter(
            AlertLog.rule_name == RULE_NAME,
            AlertLog.timestamp >= cutoff,
            AlertLog.delivered == True,
        )
        .all()
    )
    return any(marker in (message or "").splitlines() for (message,) in rows)


def _sqlite_wal_size_bytes() -> tuple[Path | None, int | None]:
    try:
        url = make_url(config.DATABASE_URL)
    except Exception:
        return None, None
    if url.get_backend_name() != "sqlite":
        return None, None
    database = url.database
    if not database or database == ":memory:":
        return None, None
    db_path = Path(database)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    wal_path = db_path.with_name(db_path.name + "-wal")
    if not wal_path.exists():
        return wal_path, 0
    try:
        return wal_path, wal_path.stat().st_size
    except OSError:
        return wal_path, None

