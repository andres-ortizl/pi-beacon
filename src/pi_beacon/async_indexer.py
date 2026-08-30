from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.sql import delete
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from pi_beacon.database import protect_database_files, upgrade_database
from pi_beacon.history import JsonObject, mapping, numeric_float, numeric_int, parse_time
from pi_beacon.models import RecentSession, SessionFile, TodayStats

READ_BATCH_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ReadBatch:
    events: tuple[JsonObject, ...]
    offset: int
    size: int
    mtime_ns: int
    source_dev: int
    source_inode: int


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

    def session_files_for_today(self, now: datetime) -> list[Path]:
        files: list[Path] = []
        if not self.sessions_root.is_dir():
            return files
        for path in self.sessions_root.rglob("*.jsonl"):
            if "subagent-artifacts" in path.parts:
                continue
            try:
                if datetime.fromtimestamp(path.stat().st_mtime).date() == now.date():
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
    def add_usage(record: SessionFile, usage: JsonObject) -> None:
        cost = mapping(usage.get("cost")) or {}
        record.cost += numeric_float(cost.get("total"))
        total_tokens = numeric_int(usage.get("totalTokens"))
        if total_tokens == 0:
            total_tokens = sum(
                numeric_int(usage.get(key))
                for key in ("input", "output", "cacheRead", "cacheWrite")
            )
        record.tokens += total_tokens

    def update_message_usage(self, record: SessionFile, message: JsonObject) -> None:
        role = message.get("role")
        if role == "assistant":
            record.messages += 1
            record.model = str(message.get("responseModel") or message.get("model") or record.model)
        if role not in {"assistant", "toolResult"}:
            return
        if usage := mapping(message.get("usage")):
            self.add_usage(record, usage)

    def apply_event(self, record: SessionFile, payload: JsonObject, now: datetime) -> None:
        event_type = payload.get("type")
        if event_type == "session":
            record.session_id = str(payload.get("id") or record.session_id)
            record.cwd = str(payload.get("cwd") or record.cwd)
        elif event_type == "model_change":
            record.model = str(payload.get("modelId") or record.model)

        timestamp = parse_time(payload.get("timestamp"))
        if timestamp is None or timestamp.date() != now.date():
            return
        self.update_activity(record, timestamp)
        if event_type == "message" and (message := mapping(payload.get("message"))):
            self.update_message_usage(record, message)
        elif event_type in {"compaction", "branch_summary"} and (
            usage := mapping(payload.get("usage"))
        ):
            self.add_usage(record, usage)

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

    async def update_record_from_file(self, record: SessionFile, path: Path, now: datetime) -> None:
        while True:
            previous_offset = record.offset
            batch = await asyncio.to_thread(self.read_appended_events, path, record.offset)
            for payload in batch.events:
                self.apply_event(record, payload, now)
            record.offset = batch.offset
            if record.offset >= batch.size or record.offset == previous_offset:
                break
        record.size = batch.size
        record.mtime_ns = batch.mtime_ns
        record.source_dev = batch.source_dev
        record.source_inode = batch.source_inode
        record.cursor_fingerprint = await asyncio.to_thread(
            self.cursor_fingerprint, path, record.offset
        )

    async def index_file(self, session: AsyncSession, path: Path, now: datetime) -> None:
        day = now.date().isoformat()
        record = await session.get(SessionFile, str(path)) or SessionFile(
            path=str(path), day=day, session_id=path.stem
        )
        self.reset_for_day(record, day)
        try:
            stat = await asyncio.to_thread(path.stat)
            if stat.st_size == record.size and stat.st_mtime_ns == record.mtime_ns:
                return
            if await self.source_was_rewritten(record, path, stat):
                self.reset_record(record, day)
            await self.update_record_from_file(record, path, now)
        except OSError:
            return
        session.add(record)

    @staticmethod
    async def reconcile_missing(session: AsyncSession, day: str, paths: list[Path]) -> None:
        current_paths = {str(path) for path in paths}
        result = await session.exec(select(SessionFile).where(SessionFile.day == day))
        for record in result.all():
            if record.path not in current_paths:
                await session.delete(record)

    @staticmethod
    def duration(record: SessionFile) -> int:
        first = parse_time(record.first_activity)
        last = parse_time(record.last_activity)
        if first is None or last is None:
            return 0
        try:
            return max(0, int((last - first).total_seconds()))
        except (OverflowError, ValueError):
            return 0

    async def today(self) -> TodayStats:
        now = datetime.now().astimezone()
        day = now.date().isoformat()
        paths = await asyncio.to_thread(self.session_files_for_today, now)
        async with AsyncSession(self.engine) as session:
            for path in paths:
                await self.index_file(session, path, now)
            await self.reconcile_missing(session, day, paths)
            retention_day = (now.date() - timedelta(days=self.retention_days)).isoformat()
            await session.exec(delete(SessionFile).where(col(SessionFile.day) < retention_day))
            await session.commit()
            result = await session.exec(
                select(SessionFile)
                .where(SessionFile.day == day)
                .where(col(SessionFile.last_activity).is_not(None))
                .order_by(col(SessionFile.last_activity).desc())
            )
            records = list(result.all())
        await self.protect_database_files()

        recent = [
            RecentSession(
                session_id=record.session_id,
                session_file=Path(record.path),
                project=Path(record.cwd).name or record.cwd or Path(record.path).parent.name,
                cwd=Path(record.cwd) if record.cwd else None,
                model=record.model,
                cost=round(record.cost, 4),
                tokens=record.tokens,
                messages=record.messages,
                started_at=record.first_activity,
                last_activity=record.last_activity,
                duration_seconds=self.duration(record),
            )
            for record in records[: self.recent_limit]
        ]
        return TodayStats(
            cost=round(sum(record.cost for record in records), 4),
            tokens=sum(record.tokens for record in records),
            messages=sum(record.messages for record in records),
            sessions=len(records),
            recent=recent,
        )
