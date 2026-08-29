from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pi_beacon import runtime
from pi_beacon.models import LiveSession, SessionState, SubagentStatus


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
        process_start_time=runtime.process_start_time(os.getpid()),
    )
    write_json(status_dir / f"{os.getpid()}.json", session.model_dump(mode="json", by_alias=True))
    monkeypatch.setattr(runtime, "pi_processes", lambda: [])

    subagent_root = tmp_path / "subagents"
    write_json(
        subagent_root / "run" / "status.json",
        {
            "lifecycleArtifactVersion": 3,
            "runId": "run-1",
            "state": "running",
            "startedAt": "2020-01-01T00:00:00Z",
            "steps": [
                {
                    "id": "step-1",
                    "agent": "reviewer",
                    "status": "running",
                    "task": "Review bridge",
                }
            ],
        },
    )

    snapshot = runtime.collect_runtime(status_dir, subagent_root)
    assert snapshot.session_count == 1
    assert snapshot.active_count == 2
    assert snapshot.sessions[0].session_id == "main-1"
    assert snapshot.subagents[0].agent == "reviewer"
    assert snapshot.agents[0].identity == "pi-subagents:run-1:step-1"


def test_agent_normalization_preserves_sources_without_durable_correlation() -> None:
    child = LiveSession(
        pid=42,
        parent_pid=1,
        session_id="child",
        project="demo",
        state=SessionState.RUNNING,
        detail="Review code",
        process_start_time="new-process",
        kind="subagent",
    )
    external = SubagentStatus(
        agent="reviewer",
        state="running",
        task="Review code",
        run_id="run-1",
        pid=42,
    )
    independent = SubagentStatus(
        agent="scout",
        state="waiting",
        task="Inspect API",
        run_id="run-2",
    )

    agents = runtime.normalize_agents([child], [external, independent])

    assert [agent.agent for agent in agents] == ["reviewer", "scout", "demo"]
    assert agents[0].source == "pi-subagents"
    assert agents[0].pid == 42
    assert agents[1].identity == "pi-subagents:run-2:scout"
    assert agents[2].identity == "pi-process:42:new-process"


def test_agent_normalization_deduplicates_only_matching_process_identity() -> None:
    child = LiveSession(
        pid=42,
        instance_id="child-instance",
        process_start_time="100",
        session_id="child",
        project="demo",
        kind="subagent",
    )
    matching = SubagentStatus(
        agent="reviewer",
        state="running",
        run_id="run-1",
        pid=42,
        process_start_time="100",
    )
    reused = matching.model_copy(update={"run_id": "run-2", "process_start_time": "99"})

    matching_agents = runtime.normalize_agents([child], [matching])
    reused_agents = runtime.normalize_agents([child], [reused])

    assert len(matching_agents) == 1
    assert len(reused_agents) == 2
    assert reused_agents[1].source == "pi-process"

    duplicate = matching.model_copy(update={"process_start_time": "", "step_id": ""})
    duplicate_identities = [
        agent.identity for agent in runtime.normalize_agents([], [duplicate, duplicate])
    ]
    assert duplicate_identities == [
        "pi-subagents:run-1:reviewer",
        "pi-subagents:run-1:reviewer:2",
    ]


def test_reused_pid_status_is_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    status_dir = tmp_path / "live"
    reused = status_dir / f"{os.getpid()}.json"
    write_json(
        reused,
        LiveSession(
            pid=os.getpid(),
            session_id="stale",
            project="old",
            process_start_time="wrong",
        ).model_dump(mode="json", by_alias=True),
    )
    monkeypatch.setattr(runtime, "pi_processes", lambda: [])

    assert runtime.live_sessions(status_dir) == []
    assert not reused.exists()


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


def test_unknown_subagent_artifact_version_is_ignored(tmp_path: Path) -> None:
    write_json(
        tmp_path / "run" / "status.json",
        {
            "lifecycleArtifactVersion": 99,
            "runId": "future",
            "state": "running",
            "steps": [{"agent": "reviewer", "status": "running"}],
        },
    )

    assert runtime.subagent_statuses(tmp_path) == []


def test_malformed_subagent_artifacts_do_not_hide_valid_siblings(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt" / "status.json"
    corrupt.parent.mkdir()
    corrupt.write_bytes(b"\xff")
    write_json(
        tmp_path / "invalid" / "status.json",
        {
            "lifecycleArtifactVersion": 3,
            "runId": "invalid",
            "state": "running",
            "startedAt": {},
        },
    )
    write_json(
        tmp_path / "valid" / "status.json",
        {
            "lifecycleArtifactVersion": 3,
            "runId": "valid",
            "state": "running",
            "steps": [{"id": "step-1", "agent": "reviewer", "status": "running"}],
        },
    )

    statuses = runtime.subagent_statuses(tmp_path)

    assert [status.run_id for status in statuses] == ["valid"]


def test_elapsed_and_process_helpers(tmp_path: Path) -> None:
    assert runtime.elapsed(None) == ""
    assert runtime.process_kind(os.getpid()) == "session"
    project, cwd = runtime.process_project(os.getpid())
    assert project
    assert cwd is not None
    assert runtime.subagent_statuses(tmp_path / "missing") == []
