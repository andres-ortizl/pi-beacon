from __future__ import annotations

import asyncio
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.sql import delete
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from pi_beacon.database import protect_database_files, upgrade_database
from pi_beacon.history import JsonObject, mapping, numeric_float, numeric_int, parse_time
from pi_beacon.models import (
    DailyCost,
    HistoryStats,
    ModelUsageStats,
    RecentSession,
    SessionDay,
    SessionFile,
    SessionModelUsage,
    TodayStats,
)

READ_BATCH_BYTES = 1024 * 1024
HISTORY_AGGREGATION_VERSION = 1
MODEL_USAGE_DAYS = 7


@dataclass(frozen=True)
class ReadBatch:
    events: tuple[JsonObject, ...]
    offset: int
    size: int
    mtime_ns: int
    source_dev: int
    source_inode: int


@dataclass
class DailyUsageDelta:
    cost: float = 0
    tokens: int = 0
    messages: int = 0
    first_activity: datetime | None = None
    last_activity: datetime | None = None

    def add(self, timestamp: datetime, cost: float, tokens: int, messages: int) -> None:
        self.cost += cost
        self.tokens += tokens
        self.messages += messages
        if self.first_activity is None or timestamp < self.first_activity:
            self.first_activity = timestamp
        if self.last_activity is None or timestamp > self.last_activity:
            self.last_activity = timestamp


@dataclass
class ModelUsageDelta:
    cost: float = 0
    tokens: int = 0
    responses: int = 0


@dataclass
class UsageAccumulator:
    daily: dict[str, DailyUsageDelta] = field(default_factory=dict)
    models: dict[tuple[str, str], ModelUsageDelta] = field(default_factory=dict)

    def add_daily(
        self,
        timestamp: datetime,
        cost: float,
        tokens: int,
        messages: int,
    ) -> None:
        day = timestamp.date().isoformat()
        delta = self.daily.setdefault(day, DailyUsageDelta())
        delta.add(timestamp, cost, tokens, messages)

    def add_model(
        self,
        timestamp: datetime,
        model: str,
        cost: float,
        tokens: int,
        responses: int,
    ) -> None:
        if not model:
            return
        key = (timestamp.date().isoformat(), model)
        delta = self.models.setdefault(key, ModelUsageDelta())
        delta.cost += cost
        delta.tokens += tokens
        delta.responses += responses


class AsyncSessionIndexer:
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
        self.engine: AsyncEngine = create_async_engine(
            f"sqlite+aiosqlite:///{database_path}",
            connect_args={"timeout": 5},
        )
        event.listen(self.engine.sync_engine, "connect", self.configure_connection)

    @property
    def aggregation_signature(self) -> int:
        return HISTORY_AGGREGATION_VERSION * 1000 + self.retention_days

    @staticmethod
    def configure_connection(connection: Any, _record: object) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async def start(self) -> None:
        await asyncio.to_thread(upgrade_database, self.database_path)
        await self.protect_database_files()

    async def close(self) -> None:
        await self.engine.dispose()

    async def protect_database_files(self) -> None:
        await asyncio.to_thread(protect_database_files, self.database_path)

    def session_files_for_history(self, now: datetime) -> list[Path]:
        """Return JSONL sources changed inside the retained history window."""
        if not self.sessions_root.is_dir():
            return []
        oldest = now - timedelta(days=self.retention_days)
        files: list[Path] = []
        for path in self.sessions_root.rglob("*.jsonl"):
            if "subagent-artifacts" in path.parts:
                continue
            try:
                if datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo) >= oldest:
                    files.append(path)
            except OSError:
                continue
        return files

    @staticmethod
    def reset_for_day(record: SessionFile, day: str) -> None:
        if record.day == day:
            return
        record.day = day
        record.cost = 0
        record.tokens = 0
        record.messages = 0
        record.first_activity = None
        record.last_activity = None

    @staticmethod
    def reset_record(record: SessionFile, day: str) -> None:
        record.size = 0
        record.mtime_ns = 0
        record.offset = 0
        record.cursor_fingerprint = ""
        record.source_dev = 0
        record.source_inode = 0
        record.aggregation_version = 0
        record.day = day
        record.session_id = Path(record.path).stem
        record.cwd = ""
        record.model = ""
        record.cost = 0
        record.tokens = 0
        record.messages = 0
        record.first_activity = None
        record.last_activity = None

    @staticmethod
    def cursor_fingerprint(path: Path, offset: int) -> str:
        if offset <= 0:
            return ""
        start = max(0, offset - 4096)
        with path.open("rb") as session_file:
            session_file.seek(start)
            content = session_file.read(offset - start)
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def update_activity(record: SessionFile, timestamp: datetime) -> None:
        timestamp_text = timestamp.isoformat()
        first = parse_time(record.first_activity)
        last = parse_time(record.last_activity)
        if first is None or timestamp < first:
            record.first_activity = timestamp_text
        if last is None or timestamp > last:
            record.last_activity = timestamp_text

    @staticmethod
    def usage_values(usage: JsonObject) -> tuple[float, int]:
        cost = mapping(usage.get("cost")) or {}
        total_tokens = numeric_int(usage.get("totalTokens"))
        if total_tokens == 0:
            total_tokens = sum(
                numeric_int(usage.get(key))
                for key in ("input", "output", "cacheRead", "cacheWrite")
            )
        return numeric_float(cost.get("total")), total_tokens

    @staticmethod
    def response_model(message: JsonObject) -> str:
        model = message.get("responseModel") or message.get("model")
        return model.strip() if isinstance(model, str) else ""

    def apply_event(
        self,
        record: SessionFile,
        payload: JsonObject,
        now: datetime,
        oldest_day: date,
        accumulator: UsageAccumulator,
    ) -> None:
        event_type = payload.get("type")
        if event_type == "session":
            record.session_id = str(payload.get("id") or record.session_id)
            record.cwd = str(payload.get("cwd") or record.cwd)
        elif event_type == "model_change":
            model = payload.get("modelId")
            if isinstance(model, str) and model:
                record.model = model

        timestamp = parse_time(payload.get("timestamp"))
        if timestamp is None or timestamp.date() < oldest_day:
            return

        cost = 0.0
        tokens = 0
        messages = 0
        model = ""
        responses = 0
        message = mapping(payload.get("message")) if event_type == "message" else None
        if message is not None:
            role = message.get("role")
            if role == "assistant":
                messages = 1
                responses = 1
                model = self.response_model(message)
                if model:
                    record.model = model
            if role in {"assistant", "toolResult"}:
                usage = mapping(message.get("usage"))
                if usage is not None:
                    cost, tokens = self.usage_values(usage)
        elif event_type in {"compaction", "branch_summary"}:
            usage = mapping(payload.get("usage"))
            if usage is not None:
                cost, tokens = self.usage_values(usage)

        accumulator.add_daily(timestamp, cost, tokens, messages)
        if responses:
            accumulator.add_model(timestamp, model, cost, tokens, responses)

        if timestamp.date() != now.date():
            return
        self.update_activity(record, timestamp)
        record.cost += cost
        record.tokens += tokens
        record.messages += messages

    @staticmethod
    def read_appended_events(path: Path, offset: int) -> ReadBatch:
        events: list[JsonObject] = []
        next_offset = offset
        with path.open("rb") as session_file:
            session_file.seek(offset)
            while True:
                line_start = session_file.tell()
                raw_line = session_file.readline()
                if not raw_line:
                    break
                if not raw_line.endswith(b"\n"):
                    next_offset = line_start
                    break
                next_offset = session_file.tell()
                try:
                    payload = mapping(json.loads(raw_line))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = None
                if payload is not None:
                    events.append(payload)
                if next_offset - offset >= READ_BATCH_BYTES:
                    break
        stat = path.stat()
        return ReadBatch(
            events=tuple(events),
            offset=next_offset,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            source_dev=stat.st_dev,
            source_inode=stat.st_ino,
        )

    async def source_was_rewritten(self, record: SessionFile, path: Path, stat: Any) -> bool:
        if stat.st_size < record.offset:
            return True
        if record.source_inode and (
            record.source_dev != stat.st_dev or record.source_inode != stat.st_ino
        ):
            return True
        if not record.offset:
            return False
        fingerprint = await asyncio.to_thread(self.cursor_fingerprint, path, record.offset)
        return fingerprint != record.cursor_fingerprint

    @staticmethod
    async def delete_source_aggregates(session: AsyncSession, source_path: str) -> None:
        await session.exec(delete(SessionDay).where(col(SessionDay.source_path) == source_path))
        await session.exec(
            delete(SessionModelUsage).where(col(SessionModelUsage.source_path) == source_path)
        )

    @staticmethod
    def merge_activity(
        first: str | None,
        last: str | None,
        delta: DailyUsageDelta,
    ) -> tuple[str | None, str | None]:
        existing_first = parse_time(first)
        existing_last = parse_time(last)
        first_time = delta.first_activity
        last_time = delta.last_activity
        if existing_first is not None and (first_time is None or existing_first < first_time):
            first_time = existing_first
        if existing_last is not None and (last_time is None or existing_last > last_time):
            last_time = existing_last
        return (
            first_time.isoformat() if first_time is not None else None,
            last_time.isoformat() if last_time is not None else None,
        )

    async def persist_aggregates(
        self,
        session: AsyncSession,
        source_path: str,
        accumulator: UsageAccumulator,
    ) -> None:
        for day, delta in accumulator.daily.items():
            record = await session.get(SessionDay, (source_path, day))
            if record is None:
                record = SessionDay(source_path=source_path, day=day)
            record.cost += delta.cost
            record.tokens += delta.tokens
            record.messages += delta.messages
            record.first_activity, record.last_activity = self.merge_activity(
                record.first_activity,
                record.last_activity,
                delta,
            )
            session.add(record)
        for (day, model), delta in accumulator.models.items():
            record = await session.get(SessionModelUsage, (source_path, day, model))
            if record is None:
                record = SessionModelUsage(source_path=source_path, day=day, model=model)
            record.cost += delta.cost
            record.tokens += delta.tokens
            record.responses += delta.responses
            session.add(record)

    async def update_record_from_file(
        self,
        session: AsyncSession,
        record: SessionFile,
        path: Path,
        now: datetime,
        oldest_day: date,
    ) -> None:
        accumulator = UsageAccumulator()
        while True:
            previous_offset = record.offset
            batch = await asyncio.to_thread(self.read_appended_events, path, record.offset)
            for payload in batch.events:
                self.apply_event(record, payload, now, oldest_day, accumulator)
            record.offset = batch.offset
            if record.offset >= batch.size or record.offset == previous_offset:
                break
        await self.persist_aggregates(session, record.path, accumulator)
        record.size = batch.size
        record.mtime_ns = batch.mtime_ns
        record.source_dev = batch.source_dev
        record.source_inode = batch.source_inode
        record.cursor_fingerprint = await asyncio.to_thread(
            self.cursor_fingerprint, path, record.offset
        )

    async def index_file(self, session: AsyncSession, path: Path, now: datetime) -> None:
        day = now.date().isoformat()
        oldest_day = now.date() - timedelta(days=self.retention_days - 1)
        record = await session.get(SessionFile, str(path)) or SessionFile(
            path=str(path), day=day, session_id=path.stem
        )
        self.reset_for_day(record, day)
        try:
            stat = await asyncio.to_thread(path.stat)
            if (
                record.aggregation_version != self.aggregation_signature
                or await self.source_was_rewritten(record, path, stat)
            ):
                await self.delete_source_aggregates(session, record.path)
                self.reset_record(record, day)
            if stat.st_size == record.size and stat.st_mtime_ns == record.mtime_ns:
                session.add(record)
                return
            session.add(record)
            await session.flush()
            await self.update_record_from_file(session, record, path, now, oldest_day)
        except OSError:
            return
        record.aggregation_version = self.aggregation_signature
        session.add(record)

    @staticmethod
    async def reconcile_missing(session: AsyncSession, paths: list[Path]) -> None:
        current_paths = {str(path) for path in paths}
        result = await session.exec(select(SessionFile))
        for record in result.all():
            if record.path not in current_paths:
                await session.delete(record)

    async def prune_history(self, session: AsyncSession, oldest_day: str) -> None:
        await session.exec(delete(SessionDay).where(col(SessionDay.day) < oldest_day))
        await session.exec(delete(SessionModelUsage).where(col(SessionModelUsage.day) < oldest_day))

    @staticmethod
    def duration(record: SessionDay) -> int:
        first = parse_time(record.first_activity)
        last = parse_time(record.last_activity)
        if first is None or last is None:
            return 0
        try:
            return max(0, int((last - first).total_seconds()))
        except (OverflowError, ValueError):
            return 0

    @staticmethod
    def model_usage(records: list[SessionModelUsage]) -> list[ModelUsageStats]:
        grouped: dict[str, ModelUsageStats] = {}
        sources: dict[str, set[str]] = defaultdict(set)
        for record in records:
            stats = grouped.setdefault(record.model, ModelUsageStats(model=record.model))
            stats.cost += record.cost
            stats.tokens += record.tokens
            stats.responses += record.responses
            sources[record.model].add(record.source_path)
        for model, stats in grouped.items():
            stats.cost = round(stats.cost, 4)
            stats.sessions = len(sources[model])
        return sorted(
            grouped.values(),
            key=lambda stats: (-stats.cost, -stats.tokens, -stats.responses, stats.model.lower()),
        )

    async def summary(self, now: datetime | None = None) -> tuple[TodayStats, HistoryStats]:
        now = now or datetime.now().astimezone()
        today = now.date()
        oldest_day = today - timedelta(days=self.retention_days - 1)
        paths = await asyncio.to_thread(self.session_files_for_history, now)
        async with AsyncSession(self.engine) as session:
            for path in paths:
                await self.index_file(session, path, now)
            await self.reconcile_missing(session, paths)
            await self.prune_history(session, oldest_day.isoformat())
            await session.commit()

            today_result = await session.exec(
                select(SessionDay)
                .where(SessionDay.day == today.isoformat())
                .where(col(SessionDay.last_activity).is_not(None))
                .order_by(col(SessionDay.last_activity).desc())
            )
            today_records = list(today_result.all())
            model_result = await session.exec(
                select(SessionModelUsage).where(
                    col(SessionModelUsage.day) >= oldest_day.isoformat()
                )
            )
            model_records = list(model_result.all())

            recent: list[RecentSession] = []
            for record in today_records[: self.recent_limit]:
                source = await session.get(SessionFile, record.source_path)
                if source is None:
                    continue
                recent.append(
                    RecentSession(
                        session_id=source.session_id,
                        session_file=Path(source.path),
                        project=Path(source.cwd).name
                        or source.cwd
                        or Path(source.path).parent.name,
                        cwd=Path(source.cwd) if source.cwd else None,
                        model=source.model,
                        cost=round(record.cost, 4),
                        tokens=record.tokens,
                        messages=record.messages,
                        started_at=record.first_activity,
                        last_activity=record.last_activity,
                        duration_seconds=self.duration(record),
                    )
                )

            cost_result = await session.exec(
                select(SessionDay).where(col(SessionDay.day) >= oldest_day.isoformat())
            )
            cost_records = list(cost_result.all())
        await self.protect_database_files()

        daily_costs = {record.day: 0.0 for record in cost_records}
        for record in cost_records:
            daily_costs[record.day] = daily_costs.get(record.day, 0.0) + record.cost
        trend_days = [
            today - timedelta(days=offset) for offset in range(MODEL_USAGE_DAYS - 1, -1, -1)
        ]
        daily_cost = [
            DailyCost(day=day.isoformat(), cost=round(daily_costs.get(day.isoformat(), 0.0), 4))
            for day in trend_days
        ]
        today_models = [record for record in model_records if record.day == today.isoformat()]
        seven_day_start = today - timedelta(days=MODEL_USAGE_DAYS - 1)
        seven_day_models = [
            record for record in model_records if record.day >= seven_day_start.isoformat()
        ]
        return (
            TodayStats(
                cost=round(sum(record.cost for record in today_records), 4),
                tokens=sum(record.tokens for record in today_records),
                messages=sum(record.messages for record in today_records),
                sessions=len(today_records),
                recent=recent,
            ),
            HistoryStats(
                daily_cost=daily_cost,
                model_usage_today=self.model_usage(today_models),
                modelUsage7d=self.model_usage(seven_day_models),
            ),
        )

    async def today(self) -> TodayStats:
        today, _history = await self.summary()
        return today
