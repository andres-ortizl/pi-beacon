from __future__ import annotations

import asyncio
from pathlib import Path

from pi_beacon.async_indexer import AsyncSessionIndexer
from pi_beacon.models import HistoryStats, TodayStats


class SessionIndexer:
    """Synchronous compatibility adapter for one-shot CLI commands."""

    def __init__(
        self,
        sessions_root: Path,
        database_path: Path,
        recent_limit: int = 5,
        retention_days: int = 7,
    ):
        self.sessions_root = sessions_root
        self.database_path = database_path
        self.recent_limit = recent_limit
        self.retention_days = retention_days

    async def collect(self) -> tuple[TodayStats, HistoryStats]:
        indexer = AsyncSessionIndexer(
            self.sessions_root,
            self.database_path,
            self.recent_limit,
            self.retention_days,
        )
        await indexer.start()
        try:
            return await indexer.summary()
        finally:
            await indexer.close()

    def summary(self) -> tuple[TodayStats, HistoryStats]:
        return asyncio.run(self.collect())

    def today(self) -> TodayStats:
        today, _history = self.summary()
        return today
