"""Pi Beacon public API."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pi_beacon.models import DashboardSnapshot

__all__ = ["DashboardSnapshot"]


def __getattr__(name: str) -> object:
    if name == "DashboardSnapshot":
        from pi_beacon.models import DashboardSnapshot

        return DashboardSnapshot
    raise AttributeError(name)
