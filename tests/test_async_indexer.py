from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta
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


def assistant_event(
    timestamp: str,
    cost: float,
    tokens: int,
    model: str = "gpt-5.6-sol",
    response_model: str | None = None,
) -> dict[str, object]:
    message: dict[str, object] = {
        "role": "assistant",
        "model": model,
        "stopReason": "stop",
        "usage": usage_payload(cost, tokens),
    }
    if response_model is not None:
        message["responseModel"] = response_model
    return {"type": "message", "timestamp": timestamp, "message": message}


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

    assert version == ("0003_dashboard_history",)
    assert any(index[1] == "ix_sessionfile_day_last_activity" for index in indexes)
    assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'sessionday'").fetchone()
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE name = 'sessionmodelusage'"
    ).fetchone()


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


@pytest.mark.anyio
async def test_async_indexer_attributes_model_usage_per_response_and_append(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "switch.jsonl"
    now = datetime.now().astimezone()
    timestamp = now.isoformat()
    append_event(path, {"type": "session", "id": "switch", "cwd": "/code/demo"})
    append_event(path, assistant_event(timestamp, 0.25, 100, "fallback", "openai/gpt-5.6"))
    append_event(path, assistant_event(timestamp, 0.75, 300, "anthropic/claude-sonnet-4.6"))
    append_event(
        path,
        {
            "type": "message",
            "timestamp": timestamp,
            "message": {"role": "toolResult", "usage": usage_payload(0.5, 50)},
        },
    )

    indexer = AsyncSessionIndexer(sessions, tmp_path / "cache.sqlite3")
    await indexer.start()
    try:
        today, history = await indexer.summary(now)
        append_event(path, assistant_event(timestamp, 0.4, 200, "openai/gpt-5.6"))
        _today, appended_history = await indexer.summary(now)
    finally:
        await indexer.close()

    assert today.cost == 1.5
    assert {
        stats.model: (stats.cost, stats.tokens, stats.responses, stats.sessions)
        for stats in history.model_usage_today
    } == {
        "openai/gpt-5.6": (0.25, 100, 1, 1),
        "anthropic/claude-sonnet-4.6": (0.75, 300, 1, 1),
    }
    assert {stats.model: stats.cost for stats in appended_history.model_usage_today} == {
        "openai/gpt-5.6": 0.65,
        "anthropic/claude-sonnet-4.6": 0.75,
    }


@pytest.mark.anyio
async def test_async_indexer_rebuilds_model_usage_after_rewrite(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "rewrite.jsonl"
    now = datetime.now().astimezone()
    header = {"type": "session", "id": "rewrite", "cwd": "/code/demo"}
    path.write_text(
        json.dumps(header)
        + "\n"
        + json.dumps(assistant_event(now.isoformat(), 0.25, 100, "openai/gpt-5.6"))
        + "\n"
    )

    indexer = AsyncSessionIndexer(sessions, tmp_path / "cache.sqlite3")
    await indexer.start()
    try:
        _today, initial_history = await indexer.summary(now)
        path.write_text(
            json.dumps(header)
            + "\n"
            + json.dumps(assistant_event(now.isoformat(), 0.75, 300, "anthropic/claude-sonnet-4.6"))
            + "\n"
        )
        _today, rewritten_history = await indexer.summary(now)
    finally:
        await indexer.close()

    assert [stats.model for stats in initial_history.model_usage_today] == ["openai/gpt-5.6"]
    assert [(stats.model, stats.cost) for stats in rewritten_history.model_usage_today] == [
        ("anthropic/claude-sonnet-4.6", 0.75)
    ]


@pytest.mark.anyio
async def test_async_indexer_keeps_daily_history_and_prunes_retention(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "history.jsonl"
    first_day = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    second_day = first_day + timedelta(days=1)
    append_event(path, {"type": "session", "id": "history", "cwd": "/code/demo"})
    append_event(path, assistant_event(first_day.isoformat(), 0.25, 100, "openai/gpt-5.6"))

    indexer = AsyncSessionIndexer(sessions, tmp_path / "cache.sqlite3", retention_days=2)
    await indexer.start()
    try:
        first_today, _first_history = await indexer.summary(first_day)
        append_event(
            path, assistant_event(second_day.isoformat(), 0.75, 300, "anthropic/claude-sonnet-4.6")
        )
        second_today, second_history = await indexer.summary(second_day)
        _expired_today, expired_history = await indexer.summary(second_day + timedelta(days=2))
    finally:
        await indexer.close()

    assert first_today.cost == 0.25
    assert second_today.cost == 0.75
    assert [point.cost for point in second_history.daily_cost[-2:]] == [0.25, 0.75]
    assert {stats.model for stats in second_history.model_usage_7d} == {
        "openai/gpt-5.6",
        "anthropic/claude-sonnet-4.6",
    }
    assert expired_history.model_usage_7d == []
    assert all(point.cost == 0 for point in expired_history.daily_cost)


@pytest.mark.anyio
async def test_model_usage_7d_excludes_older_retained_usage(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "long-retention.jsonl"
    now = datetime.now().astimezone()
    append_event(path, {"type": "session", "id": "long-retention", "cwd": "/code/demo"})
    append_event(
        path,
        assistant_event(
            (now - timedelta(days=8)).isoformat(),
            8.0,
            800,
            "anthropic/claude-sonnet-4.6",
        ),
    )
    append_event(path, assistant_event(now.isoformat(), 1.0, 100, "openai/gpt-5.6"))

    indexer = AsyncSessionIndexer(
        sessions,
        tmp_path / "cache.sqlite3",
        retention_days=10,
    )
    await indexer.start()
    try:
        _today, history = await indexer.summary(now)
    finally:
        await indexer.close()

    assert [(item.model, item.cost) for item in history.model_usage_7d] == [("openai/gpt-5.6", 1.0)]


@pytest.mark.anyio
async def test_retention_change_rebuilds_source_aggregates(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "retention-change.jsonl"
    now = datetime.now().astimezone()
    append_event(path, {"type": "session", "id": "retention-change", "cwd": "/code/demo"})
    append_event(
        path,
        assistant_event(
            (now - timedelta(days=1)).isoformat(),
            0.25,
            100,
            "anthropic/claude-sonnet-4.6",
        ),
    )
    append_event(path, assistant_event(now.isoformat(), 0.75, 300, "openai/gpt-5.6"))
    database = tmp_path / "cache.sqlite3"

    one_day = AsyncSessionIndexer(sessions, database, retention_days=1)
    await one_day.start()
    try:
        _today, initial = await one_day.summary(now)
    finally:
        await one_day.close()

    two_days = AsyncSessionIndexer(sessions, database, retention_days=2)
    await two_days.start()
    try:
        _today, rebuilt = await two_days.summary(now)
    finally:
        await two_days.close()

    assert [item.model for item in initial.model_usage_7d] == ["openai/gpt-5.6"]
    assert {item.model for item in rebuilt.model_usage_7d} == {
        "anthropic/claude-sonnet-4.6",
        "openai/gpt-5.6",
    }


@pytest.mark.anyio
async def test_async_indexer_removes_model_history_with_deleted_source(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "deleted.jsonl"
    now = datetime.now().astimezone()
    append_event(path, {"type": "session", "id": "deleted", "cwd": "/code/demo"})
    append_event(path, assistant_event(now.isoformat(), 0.25, 100, "openai/gpt-5.6"))

    indexer = AsyncSessionIndexer(sessions, tmp_path / "cache.sqlite3")
    await indexer.start()
    try:
        _today, history = await indexer.summary(now)
        path.unlink()
        today, deleted_history = await indexer.summary(now)
    finally:
        await indexer.close()

    assert history.model_usage_today
    assert today.sessions == 0
    assert deleted_history.model_usage_today == []
