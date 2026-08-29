from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pi_beacon import runtime
from pi_beacon.models import LiveSession, SessionState


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def test_live_session_and_subagent_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status_dir = tmp_path / "live"
    session = LiveSession(
        pid=os.getpid(),
        session_id="main-1",
        project="demo",
        state=SessionState.RUNNING,
        model="openai/gpt-5.6-sol",
        started_at="2020-01-01T00:00:00Z",
    )
    write_json(status_dir / f"{os.getpid()}.json", session.model_dump(mode="json", by_alias=True))
    monkeypatch.setattr(runtime, "pi_processes", lambda: [])

    subagent_root = tmp_path / "subagents"
    write_json(
        subagent_root / "run" / "status.json",
        {
            "runId": "run-1",
            "state": "running",
            "startedAt": "2020-01-01T00:00:00Z",
            "steps": [{"agent": "reviewer", "status": "running", "task": "Review bridge"}],
        },
    )

    snapshot = runtime.collect_runtime(status_dir, subagent_root)
    assert snapshot.session_count == 1
    assert snapshot.active_count == 2
    assert snapshot.sessions[0].session_id == "main-1"
    assert snapshot.subagents[0].agent == "reviewer"


def test_stale_status_is_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    status_dir = tmp_path / "live"
    stale = status_dir / "999999999.json"
    write_json(
        stale,
        LiveSession(pid=999999999, session_id="stale", project="old").model_dump(
            mode="json", by_alias=True
        ),
    )
    monkeypatch.setattr(runtime, "pi_processes", lambda: [])

    assert runtime.live_sessions(status_dir) == []
    assert not stale.exists()


def test_elapsed_and_process_helpers(tmp_path: Path) -> None:
    assert runtime.elapsed(None) == ""
    assert runtime.process_kind(os.getpid()) == "session"
    project, cwd = runtime.process_project(os.getpid())
    assert project
    assert cwd is not None
    assert runtime.mapping([]) is None
    assert runtime.subagent_statuses(tmp_path / "missing") == []
