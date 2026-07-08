"""Time helpers shared by API services."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

BJ_OFFSET = timedelta(hours=8)


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def to_bj_naive(value: datetime | None) -> datetime | None:
    value = ensure_utc_naive(value)
    return value + BJ_OFFSET if value else None


def format_bj(value: datetime | None) -> str | None:
    bj = to_bj_naive(value)
    return bj.strftime("%Y-%m-%d %H:%M:%S") if bj else None


def timestamp_pair(value: datetime | None) -> dict[str, str | None]:
    value = ensure_utc_naive(value)
    return {
        "timestamp_utc": value.isoformat(timespec="seconds") if value else None,
        "timestamp_bj": format_bj(value),
    }


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return ensure_utc_naive(parsed)
