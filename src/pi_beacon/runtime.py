from __future__ import annotations

import os
from pathlib import Path

from pi_beacon.models import AgentStatus, LiveSession, RuntimeStatus, SessionState, SubagentStatus
from pi_beacon.subagent_adapter import PiSubagentsAdapter
from pi_beacon.time_utils import elapsed

ATTENTION_AGENT_STATES = {"paused", "failed", "waiting", "needs_attention"}


def process_arguments(pid: int) -> list[str]:
    try:
        values = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
        return [value.decode(errors="replace") for value in values if value]
    except (OSError, PermissionError):
        return []


def process_start_time(pid: int) -> str:
    try:
        payload = (Path("/proc") / str(pid) / "stat").read_text()
        fields = payload[payload.rfind(")") + 2 :].split()
        return fields[19]
    except (OSError, IndexError):
        return ""


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
                process_start_time=process_start_time(pid),
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
        current_start_time = process_start_time(session.pid)
        if not current_start_time or session.process_start_time != current_start_time:
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


def subagent_statuses(root: Path) -> list[SubagentStatus]:
    return PiSubagentsAdapter().collect(root)


def unique_identity(base: str, seen: set[str]) -> str:
    identity = base
    counter = 2
    while identity in seen:
        identity = f"{base}:{counter}"
        counter += 1
    seen.add(identity)
    return identity


def same_process(status: SubagentStatus, child: LiveSession) -> bool:
    if status.pid != child.pid or not status.process_start_time:
        return False
    if status.process_start_time != child.process_start_time:
        return False
    return not status.instance_id or status.instance_id == child.instance_id


def normalize_agents(
    child_sessions: list[LiveSession],
    subagents: list[SubagentStatus],
) -> list[AgentStatus]:
    agents: list[AgentStatus] = []
    seen_identities: set[str] = set()
    for status in subagents:
        run_identity = status.run_id or "run"
        process_identity = (
            f"{status.pid}:{status.process_start_time}"
            if status.pid is not None and status.process_start_time
            else ""
        )
        item_identity = status.step_id or status.instance_id or process_identity or status.agent
        identity = unique_identity(
            f"{status.adapter}:{run_identity}:{item_identity}", seen_identities
        )
        agents.append(
            AgentStatus(
                identity=identity,
                source=status.adapter,
                source_version=status.adapter_version,
                agent=status.agent,
                state=status.state,
                task=status.task,
                elapsed=status.elapsed,
                started_at=status.started_at,
                run_id=status.run_id,
                pid=status.pid,
                parent_pid=status.parent_pid,
                instance_id=status.instance_id,
                process_start_time=status.process_start_time,
            )
        )
    for child in child_sessions:
        if any(same_process(status, child) for status in subagents):
            continue
        child_identity = child.instance_id or f"{child.pid}:{child.process_start_time}"
        agents.append(
            AgentStatus(
                identity=unique_identity(f"pi-process:{child_identity}", seen_identities),
                source="pi-process",
                agent=child.display_name or child.project or "pi",
                state=child.state.value,
                task=child.detail,
                elapsed=child.elapsed,
                started_at=child.started_at,
                pid=child.pid,
                parent_pid=child.parent_pid,
                instance_id=child.instance_id,
                process_start_time=child.process_start_time,
                project=child.project,
            )
        )
    return agents


def collect_runtime(status_dir: Path, subagent_root: Path) -> RuntimeStatus:
    live = live_sessions(status_dir)
    sessions = [session for session in live if session.kind == "session"]
    child_sessions = [session for session in live if session.kind == "subagent"]
    subagents = subagent_statuses(subagent_root)
    agents = normalize_agents(child_sessions, subagents)
    subagent_count = len(agents)
    active_main = sum(session.state == SessionState.RUNNING for session in sessions)
    attention = any(session.state == SessionState.WAITING for session in sessions) or any(
        status.state in ATTENTION_AGENT_STATES for status in agents
    )
    return RuntimeStatus(
        sessions=sessions,
        child_sessions=child_sessions,
        subagents=subagents,
        agents=agents,
        session_count=len(sessions),
        subagent_count=subagent_count,
        active_count=active_main + subagent_count,
        attention=attention,
    )
