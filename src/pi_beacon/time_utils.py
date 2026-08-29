from __future__ import annotations

from datetime import UTC, datetime


def parse_time(value: object) -> datetime | None:
    try:
        if isinstance(value, int | float):
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        if not isinstance(value, str) or not value:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def elapsed(value: object) -> str:
    started = parse_time(value)
    if started is None:
        return ""
    try:
        seconds = max(0, int((datetime.now(UTC) - started).total_seconds()))
    except (OverflowError, TypeError, ValueError):
        return ""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {seconds % 3600 // 60}m"
