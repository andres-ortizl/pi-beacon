from __future__ import annotations

from pathlib import Path

import pytest

import pi_beacon.service as service_module
from pi_beacon.config import PathSettings, Settings
from pi_beacon.models import LiveSession, RuntimeStatus, SessionState, SubagentStatus
from pi_beacon.service import DashboardService, state_color


def test_waybar_payload_contains_main_sessions_and_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DashboardService(Settings())
    runtime = RuntimeStatus(
        sessions=[
            LiveSession(
                pid=1,
                session_id="main",
                project="dotfiles",
                state=SessionState.RUNNING,
                detail="Tool: edit",
            )
        ],
        subagents=[SubagentStatus(agent="scout", state="running")],
        session_count=1,
        subagent_count=1,
        active_count=2,
    )
    monkeypatch.setattr(service, "runtime", lambda: runtime)

    payload = service.waybar()
    assert payload.text == "π²"
    assert payload.css_class == "active"
    assert "dotfiles" in payload.tooltip
    assert "scout" in payload.tooltip


def test_snapshot_uses_configured_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service_module, "collect_runtime", lambda _status, _subagents: RuntimeStatus()
    )
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    settings = Settings(
        paths=PathSettings(
            sessions_dir=sessions,
            runtime_dir=tmp_path / "runtime",
            database=tmp_path / "cache.sqlite3",
        )
    )
    snapshot = DashboardService(settings).snapshot()
    assert snapshot.version == 1
    assert snapshot.today.sessions == 0
    assert snapshot.runtime.sessions == []


def test_state_colors() -> None:
    assert state_color(SessionState.RUNNING) == "active"
    assert state_color(SessionState.WAITING) == "attention"
    assert state_color(SessionState.IDLE) == "idle"
    assert state_color(SessionState.OPEN) == "idle"
