from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

import pytest

from pi_beacon.async_indexer import AsyncSessionIndexer


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def append_event(path: Path, payload: Mapping[str, object]) -> None:
    with path.open("a", encoding="utf8") as session_file:
        session_file.write(json.dumps(payload) + "\n")


def usage_payload(cost: float, tokens: int) -> dict[str, object]:
    return {
        "input": tokens,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
        "totalTokens": tokens,
        "cost": {"total": cost},
    }


def assistant_event(timestamp: str, cost: float, tokens: int) -> dict[str, object]:
    return {
        "type": "message",
        "timestamp": timestamp,
        "message": {
            "role": "assistant",
            "model": "gpt-5.6-sol",
            "stopReason": "stop",
            "usage": usage_payload(cost, tokens),
        },
    }


@pytest.mark.anyio
async def test_async_indexer_reads_new_and_appended_events(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "session.jsonl"
    now = datetime.now().astimezone().isoformat()
    append_event(path, {"type": "session", "id": "session-1", "cwd": "/code/demo"})
    append_event(path, assistant_event(now, 0.25, 100))

    indexer = AsyncSessionIndexer(sessions, tmp_path / "cache.sqlite3")
    await indexer.start()
    try:
        first = await indexer.today()
        append_event(path, assistant_event(now, 0.75, 300))
        second = await indexer.today()
    finally:
        await indexer.close()

    assert first.cost == 0.25
    assert first.tokens == 100
    assert second.cost == 1.0
    assert second.tokens == 400
    assert second.messages == 2


@pytest.mark.anyio
async def test_async_indexer_migrates_an_existing_unversioned_database(tmp_path: Path) -> None:
    database = tmp_path / "cache.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE sessionfile (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                offset INTEGER NOT NULL,
                day TEXT NOT NULL,
                session_id TEXT NOT NULL,
                cwd TEXT NOT NULL,
                model TEXT NOT NULL,
                cost FLOAT NOT NULL,
                tokens INTEGER NOT NULL,
                messages INTEGER NOT NULL,
                first_activity TEXT,
                last_activity TEXT
            )
            """
        )

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    indexer = AsyncSessionIndexer(sessions, database)
    await indexer.start()
    await indexer.close()

    with sqlite3.connect(database) as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        indexes = connection.execute("PRAGMA index_list(sessionfile)").fetchall()

    assert version is not None
    assert any(index[1] == "ix_sessionfile_day_last_activity" for index in indexes)


@pytest.mark.anyio
async def test_async_indexer_recomputes_a_same_size_rewrite(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "session.jsonl"
    now = datetime.now().astimezone().isoformat()
    header = {"type": "session", "id": "session-1", "cwd": "/code/demo"}
    append_event(path, header)
    append_event(path, assistant_event(now, 0.25, 100))

    indexer = AsyncSessionIndexer(sessions, tmp_path / "cache.sqlite3")
    await indexer.start()
    try:
        first = await indexer.today()
        original_size = path.stat().st_size
        path.write_text(
            json.dumps(header) + "\n" + json.dumps(assistant_event(now, 0.75, 300)) + "\n"
        )
        assert path.stat().st_size == original_size
        second = await indexer.today()
    finally:
        await indexer.close()

    assert first.cost == 0.25
    assert second.cost == 0.75
    assert second.tokens == 300


@pytest.mark.anyio
async def test_async_indexer_removes_deleted_sessions_from_today(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "session.jsonl"
    now = datetime.now().astimezone().isoformat()
    append_event(path, {"type": "session", "id": "session-1", "cwd": "/code/demo"})
    append_event(path, assistant_event(now, 0.25, 100))

    indexer = AsyncSessionIndexer(sessions, tmp_path / "cache.sqlite3")
    await indexer.start()
    try:
        first = await indexer.today()
        path.unlink()
        second = await indexer.today()
    finally:
        await indexer.close()

    assert first.sessions == 1
    assert second.sessions == 0
    assert second.cost == 0


@pytest.mark.anyio
async def test_async_indexer_matches_pi_billable_usage_entry_types(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "session.jsonl"
    now = datetime.now().astimezone().isoformat()
    append_event(path, {"type": "session", "id": "session-1", "cwd": "/code/demo"})
    for payload in (
        {
            "type": "message",
            "timestamp": now,
            "message": {
                "role": "assistant",
                "model": "gpt-5.6-sol",
                "stopReason": "aborted",
                "usage": usage_payload(0.1, 10),
            },
        },
        {
            "type": "message",
            "timestamp": now,
            "message": {
                "role": "toolResult",
                "usage": usage_payload(0.2, 20),
            },
        },
        {
            "type": "compaction",
            "timestamp": now,
            "usage": usage_payload(0.3, 30),
        },
        {
            "type": "branch_summary",
            "timestamp": now,
            "usage": usage_payload(0.4, 40),
        },
    ):
        append_event(path, payload)

    indexer = AsyncSessionIndexer(sessions, tmp_path / "cache.sqlite3")
    await indexer.start()
    try:
        stats = await indexer.today()
    finally:
        await indexer.close()

    assert stats.cost == 1.0
    assert stats.tokens == 100
    assert stats.messages == 1


def test_jsonl_reader_returns_a_bounded_complete_line_batch(tmp_path: Path) -> None:
    path = tmp_path / "large.jsonl"
    line = json.dumps({"type": "message", "content": "x" * 16_384}) + "\n"
    path.write_text(line * 128)

    batch = AsyncSessionIndexer.read_appended_events(path, 0)

    assert 0 < batch.offset < path.stat().st_size
    assert batch.offset <= 1024 * 1024 + len(line)
    assert batch.events
    assert all(event["type"] == "message" for event in batch.events)


@pytest.mark.anyio
async def test_async_indexer_consumes_every_bounded_batch(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "large-session.jsonl"
    now = datetime.now().astimezone().isoformat()
    append_event(path, {"type": "session", "id": "large-session", "cwd": "/code/demo"})
    for _ in range(96):
        payload = assistant_event(now, 0.01, 10)
        message = payload["message"]
        assert isinstance(message, dict)
        message["content"] = "x" * 16_384
        append_event(path, payload)

    indexer = AsyncSessionIndexer(sessions, tmp_path / "cache.sqlite3")
    await indexer.start()
    try:
        stats = await indexer.today()
    finally:
        await indexer.close()

    assert stats.sessions == 1
    assert stats.messages == 96
    assert stats.tokens == 960
    assert stats.cost == pytest.approx(0.96)
