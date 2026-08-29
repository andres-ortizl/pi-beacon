from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pi_beacon.indexer import SessionIndexer


def append_event(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf8") as session_file:
        session_file.write(json.dumps(payload) + "\n")


def assistant_event(timestamp: str, cost: float, tokens: int) -> dict[str, object]:
    return {
        "type": "message",
        "timestamp": timestamp,
        "message": {
            "role": "assistant",
            "model": "gpt-5.6-sol",
            "stopReason": "stop",
            "usage": {
                "input": tokens,
                "output": 0,
                "cacheRead": 0,
                "cacheWrite": 0,
                "totalTokens": tokens,
                "cost": {"total": cost},
            },
        },
    }


def test_indexer_reads_appends_without_double_counting(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "session.jsonl"
    now = datetime.now().astimezone().isoformat()
    append_event(path, {"type": "session", "id": "session-1", "cwd": "/code/demo"})
    append_event(path, assistant_event(now, 0.25, 100))

    indexer = SessionIndexer(sessions, tmp_path / "cache.sqlite3")
    first = indexer.today()
    assert first.cost == 0.25
    assert first.tokens == 100
    assert first.sessions == 1
    assert first.recent[0].project == "demo"

    append_event(path, assistant_event(now, 0.75, 300))
    second = indexer.today()
    assert second.cost == 1.0
    assert second.tokens == 400
    assert second.messages == 2

    unchanged = indexer.today()
    assert unchanged == second


def test_indexer_ignores_transcripts_and_old_messages(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    transcript_dir = sessions / "subagent-artifacts"
    transcript_dir.mkdir(parents=True)
    old = "2020-01-01T00:00:00+00:00"
    append_event(transcript_dir / "copy.jsonl", assistant_event(old, 9.0, 900))
    live = sessions / "live.jsonl"
    append_event(live, {"type": "session", "id": "live", "cwd": "/code/live"})
    append_event(live, assistant_event(old, 2.0, 200))

    stats = SessionIndexer(sessions, tmp_path / "cache.sqlite3").today()
    assert stats.cost == 0
    assert stats.sessions == 0
