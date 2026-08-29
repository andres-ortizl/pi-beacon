from __future__ import annotations

from datetime import datetime
from typing import Any

JsonObject = dict[str, Any]


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def numeric_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float):
        try:
            return int(value)
        except (OverflowError, ValueError):
            return 0
    return 0


def numeric_float(value: object) -> float:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float):
        try:
            return float(value)
        except (OverflowError, ValueError):
            return 0
    return 0


def mapping(value: object) -> JsonObject | None:
    if not isinstance(value, dict):
        return None
    return value
