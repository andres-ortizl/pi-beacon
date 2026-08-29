from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pi_beacon.models import LiveSession, RuntimeStatus, SessionState, SubagentStatus

ACTIVE_AGENT_STATES = {"queued", "running", "paused", "waiting", "needs_attention"}
ATTENTION_AGENT_STATES = {"paused", "failed", "waiting", "needs_attention"}
JsonObject = dict[str, Any]


def parse_time(value: object) -> datetime | None:
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        if not isinstance(value, str) or not value:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def elapsed(value: object) -> str:
    started = parse_time(value)
    if started is None:
        return ""
    try:
        seconds = max(0, int((datetime.now(UTC) - started).total_seconds()))
    except (OverflowError, TypeError, ValueError):
        return ""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {seconds % 3600 // 60}m"


def process_arguments(pid: int) -> list[str]:
    try:
        values = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
        return [value.decode(errors="replace") for value in values if value]
    except (OSError, PermissionError):
        return []


def process_kind(pid: int) -> str:
    arguments = process_arguments(pid)
    return (
        "subagent"
        if "--mode" in arguments and "json" in arguments and "-p" in arguments
        else "session"
    )


def process_project(pid: int) -> tuple[str, Path | None]:
    try:
        cwd = Path(os.readlink(Path("/proc") / str(pid) / "cwd"))
    except OSError:
        return "Pi", None
    return cwd.name or str(cwd), cwd


def pi_processes() -> list[LiveSession]:
    sessions: list[LiveSession] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
            command = (entry / "comm").read_text().strip()
            arguments = process_arguments(pid)
            if command != "pi" and not any("pi-coding-agent" in arg for arg in arguments):
                continue
            project, cwd = process_project(pid)
        except (OSError, PermissionError, ValueError):
            continue
        sessions.append(
            LiveSession(
                pid=pid,
                session_id=f"pid-{pid}",
                project=project,
                cwd=cwd,
                state=SessionState.OPEN,
                detail="Reload Pi to enable live status",
                kind=process_kind(pid),
            )
        )
    return sessions


def live_sessions(status_dir: Path) -> list[LiveSession]:
    sessions: list[LiveSession] = []
    known_pids: set[int] = set()
    status_dir.mkdir(parents=True, exist_ok=True)
    for path in status_dir.glob("*.json"):
        try:
            session = LiveSession.model_validate_json(path.read_text())
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
            continue
        if not (Path("/proc") / str(session.pid)).exists():
            path.unlink(missing_ok=True)
            continue
        session.kind = process_kind(session.pid)
        session.elapsed = elapsed(session.started_at)
        sessions.append(session)
        known_pids.add(session.pid)

    sessions.extend(session for session in pi_processes() if session.pid not in known_pids)
    return sorted(
        sessions,
        key=lambda session: (session.kind != "session", session.project.lower()),
    )


def mapping(value: object) -> JsonObject | None:
    return value if isinstance(value, dict) else None


def subagent_statuses(root: Path) -> list[SubagentStatus]:
    statuses: list[SubagentStatus] = []
    if not root.is_dir():
        return statuses
    for path in root.rglob("status.json"):
        try:
            payload = mapping(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
        if payload is None:
            continue
        run_state = str(payload.get("state", "")).lower()
        if run_state not in ACTIVE_AGENT_STATES:
            continue
        step_values = payload.get("steps")
        steps = [mapping(step) for step in step_values] if isinstance(step_values, list) else []
        active_steps = [
            step
            for step in steps
            if step is not None
            and str(step.get("status", step.get("state", ""))).lower() in ACTIVE_AGENT_STATES
        ]
        candidates = active_steps or [payload]
        for step in candidates:
            state = str(step.get("status", step.get("state", run_state))).lower()
            statuses.append(
                SubagentStatus(
                    agent=str(
                        step.get("agent") or step.get("label") or payload.get("mode") or "agent"
                    ),
                    state=state,
                    task=str(
                        step.get("task")
                        or step.get("description")
                        or payload.get("goal")
                        or payload.get("task")
                        or ""
                    ),
                    elapsed=elapsed(step.get("startedAt") or payload.get("startedAt")),
                    started_at=step.get("startedAt") or payload.get("startedAt"),
                    run_id=str(payload.get("runId") or payload.get("id") or ""),
                )
            )
    return statuses


def collect_runtime(status_dir: Path, subagent_root: Path) -> RuntimeStatus:
    live = live_sessions(status_dir)
    sessions = [session for session in live if session.kind == "session"]
    child_sessions = [session for session in live if session.kind == "subagent"]
    subagents = subagent_statuses(subagent_root)
    subagent_count = max(len(child_sessions), len(subagents))
    active_main = sum(session.state == SessionState.RUNNING for session in sessions)
    attention = any(session.state == SessionState.WAITING for session in sessions) or any(
        status.state in ATTENTION_AGENT_STATES for status in subagents
    )
    return RuntimeStatus(
        sessions=sessions,
        child_sessions=child_sessions,
        subagents=subagents,
        session_count=len(sessions),
        subagent_count=subagent_count,
        active_count=active_main + subagent_count,
        attention=attention,
    )
