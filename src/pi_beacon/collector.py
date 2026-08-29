from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Literal

from watchfiles import awatch

from pi_beacon.async_indexer import AsyncSessionIndexer
from pi_beacon.config import Settings
from pi_beacon.models import DashboardSnapshot, RuntimeStatus, TodayStats
from pi_beacon.paths import database_path, ensure_runtime_root, live_sessions_dir, sessions_dir
from pi_beacon.service import DashboardService


class Collector:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.service = DashboardService(settings)
        self.indexer = AsyncSessionIndexer(
            sessions_dir(settings),
            database_path(settings),
            settings.recent_session_limit,
            settings.service.history_retention_days,
        )
        self.revision = 0
        self.runtime = RuntimeStatus()
        self.today = TodayStats()
        self.snapshot = DashboardSnapshot(
            generated_at=datetime.now().astimezone().isoformat(),
            runtime=self.runtime,
            today=self.today,
        )
        self.waybar = self.service.waybar_payload(self.runtime)
        self.stop_event = asyncio.Event()
        self.refresh_lock = asyncio.Lock()
        self.history_lock = asyncio.Lock()
        self.changed = asyncio.Condition()

    async def start(self) -> None:
        await asyncio.to_thread(ensure_runtime_root, self.settings)
        status_dir = live_sessions_dir(self.settings)
        await asyncio.to_thread(status_dir.mkdir, parents=True, exist_ok=True, mode=0o700)
        await asyncio.to_thread(os.chmod, status_dir, 0o700)
        if self.settings.service.history_enabled:
            await self.indexer.start()
            runtime, today = await asyncio.gather(
                asyncio.to_thread(self.service.runtime),
                self.indexer.today(),
            )
            await self.publish(runtime, today)
            return
        runtime = await asyncio.to_thread(self.service.runtime)
        await self.publish(runtime=runtime)

    async def publish(
        self,
        runtime: RuntimeStatus | None = None,
        today: TodayStats | None = None,
    ) -> None:
        async with self.refresh_lock:
            if runtime is not None:
                self.runtime = runtime
            if today is not None:
                self.today = today
            self.snapshot = DashboardSnapshot(
                generated_at=datetime.now().astimezone().isoformat(),
                runtime=self.runtime,
                today=self.today,
            )
            self.waybar = self.service.waybar_payload(self.runtime)
            async with self.changed:
                self.revision += 1
                self.changed.notify_all()

    async def refresh_runtime(self) -> None:
        runtime = await asyncio.to_thread(self.service.runtime)
        await self.publish(runtime=runtime)

    async def refresh_history(self) -> None:
        if not self.settings.service.history_enabled:
            return
        async with self.history_lock:
            today = await self.indexer.today()
        await self.publish(today=today)

    async def events(
        self,
        after_revision: int = 0,
        event_type: Literal["snapshot", "waybar"] = "snapshot",
    ) -> AsyncIterator[str]:
        if after_revision > self.revision:
            after_revision = 0
        while not self.stop_event.is_set():
            try:
                async with asyncio.timeout(15):
                    async with self.changed:
                        await self.changed.wait_for(
                            lambda revision=after_revision: (
                                self.revision > revision or self.stop_event.is_set()
                            )
                        )
                        if self.stop_event.is_set():
                            return
                        revision = self.revision
                        payload = (
                            self.snapshot.model_dump_json(by_alias=True)
                            if event_type == "snapshot"
                            else self.waybar.model_dump_json(by_alias=True)
                        )
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield self.format_event(revision, event_type, payload)
            after_revision = revision

    @staticmethod
    def format_event(revision: int, event_type: str, payload: str) -> str:
        return f"id: {revision}\nevent: {event_type}\nretry: 1000\ndata: {payload}\n\n"

    async def wait_interval(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=seconds)
        except TimeoutError:
            return False
        return True

    async def refresh_runtime_periodically(self) -> None:
        while not await self.wait_interval(self.settings.service.runtime_interval_seconds):
            await self.refresh_runtime()

    async def refresh_history_periodically(self) -> None:
        while not await self.wait_interval(self.settings.service.history_interval_seconds):
            await self.refresh_history()

    async def watch_runtime(self) -> None:
        async for changes in awatch(
            live_sessions_dir(self.settings),
            stop_event=self.stop_event,
            debounce=self.settings.service.watch_debounce_ms,
            recursive=False,
        ):
            if any(path.endswith(".json") for _change, path in changes):
                await self.refresh_runtime()

    async def watch_history(self) -> None:
        root = sessions_dir(self.settings)
        if not root.is_dir():
            return
        async for changes in awatch(
            root,
            stop_event=self.stop_event,
            debounce=self.settings.service.watch_debounce_ms,
            recursive=True,
        ):
            if any(path.endswith(".jsonl") for _change, path in changes):
                await self.refresh_history()

    async def run(self) -> None:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self.refresh_runtime_periodically())
            tasks.create_task(self.watch_runtime())
            if self.settings.service.history_enabled:
                tasks.create_task(self.refresh_history_periodically())
                tasks.create_task(self.watch_history())
            await self.stop_event.wait()

    def stop(self) -> None:
        self.stop_event.set()

    async def close(self) -> None:
        self.stop()
        await self.indexer.close()
