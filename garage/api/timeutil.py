"""Europe/London helpers. All instants are timezone-aware; London is a display/calendar zone."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_london(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(LONDON)


def london_date(dt: datetime) -> date:
    return to_london(dt).date()


def london_iso(dt: datetime | None) -> str | None:
    return to_london(dt).isoformat() if dt is not None else None


def london_at(d: date, hour: int = 0, minute: int = 0) -> datetime:
    """Aware datetime for a London wall-clock time on date d (DST-correct)."""
    return datetime.combine(d, time(hour, minute), tzinfo=LONDON)


def next_london_midnight(now: datetime) -> datetime:
    return london_at(london_date(now) + timedelta(days=1), 0, 5)
