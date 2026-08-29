from __future__ import annotations

import asyncio
from pathlib import Path

from pi_beacon.async_indexer import AsyncSessionIndexer
from pi_beacon.models import TodayStats


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

    async def collect(self) -> TodayStats:
        indexer = AsyncSessionIndexer(
            self.sessions_root,
            self.database_path,
            self.recent_limit,
            self.retention_days,
        )
        await indexer.start()
        try:
            return await indexer.today()
        finally:
            await indexer.close()

    def today(self) -> TodayStats:
        return asyncio.run(self.collect())
