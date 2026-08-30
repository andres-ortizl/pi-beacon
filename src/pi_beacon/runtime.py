from __future__ import annotations

import os
from pathlib import Path

from pi_beacon.models import (
    ActivityNode,
    AgentStatus,
    LiveSession,
    ModelActivityStats,
    RuntimeStatus,
    SessionState,
    SubagentStatus,
)
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


def matching_child(status: SubagentStatus, children: list[LiveSession]) -> LiveSession | None:
    return next((child for child in children if same_process(status, child)), None)


def normalize_agents(
    child_sessions: list[LiveSession],
    subagents: list[SubagentStatus],
) -> list[AgentStatus]:
    """Return a deduplicated, source-labelled collection of live subagents."""
    agents: list[AgentStatus] = []
    seen_identities: set[str] = set()
    for status in subagents:
        child = matching_child(status, child_sessions)
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
                parent_pid=(
                    status.parent_pid
                    if status.parent_pid is not None
                    else child.parent_pid
                    if child
                    else None
                ),
                instance_id=status.instance_id,
                process_start_time=status.process_start_time,
                project=child.project if child else "",
                model=child.model if child else "",
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
                model=child.model,
            )
        )
    return agents


def session_identity(session: LiveSession, seen: set[str]) -> str:
    process_identity = session.instance_id or f"{session.pid}:{session.process_start_time}"
    return unique_identity(f"pi-session:{session.session_id or process_identity}", seen)


def parent_identity_for(
    agent: AgentStatus,
    session_identities: dict[int, str],
    agent_identities: dict[int, list[str]],
) -> str:
    """Resolve only unambiguous direct process-parent relationships."""
    if agent.parent_pid is None:
        return ""
    if parent := session_identities.get(agent.parent_pid):
        return parent
    candidates = [
        identity
        for identity in agent_identities.get(agent.parent_pid, [])
        if identity != agent.identity
    ]
    return candidates[0] if len(candidates) == 1 else ""


def creates_cycle(identity: str, parent: str, parents: dict[str, str]) -> bool:
    current = parent
    visited: set[str] = set()
    while current and current not in visited:
        if current == identity:
            return True
        visited.add(current)
        current = parents.get(current, "")
    return False


def activity_tree(
    sessions: list[LiveSession],
    agents: list[AgentStatus],
) -> tuple[list[ActivityNode], list[AgentStatus]]:
    """Build the frontend execution tree and retain unassociated agents separately."""
    seen_identities: set[str] = {agent.identity for agent in agents}
    session_nodes: dict[str, ActivityNode] = {}
    session_identities: dict[int, str] = {}
    roots: list[ActivityNode] = []
    for session in sessions:
        identity = session_identity(session, seen_identities)
        node = ActivityNode(
            identity=identity,
            kind="session",
            session_id=session.session_id,
            display_name=(
                session.display_name or session.session_name or session.project or "Pi session"
            ),
            state=session.state.value,
            task=session.detail or session.prompt,
            elapsed=session.elapsed,
            model=session.model,
            thinking=session.thinking,
            project=session.project,
            usage=session.usage,
            context=session.context,
            started_at=session.started_at,
            last_message_at=session.last_message_at,
        )
        session_nodes[identity] = node
        session_identities[session.pid] = identity
        roots.append(node)

    agent_identities: dict[int, list[str]] = {}
    for agent in agents:
        if agent.pid is not None:
            agent_identities.setdefault(agent.pid, []).append(agent.identity)

    parent_links: dict[str, str] = {}
    for agent in agents:
        parent = parent_identity_for(agent, session_identities, agent_identities)
        if parent and not creates_cycle(agent.identity, parent, parent_links):
            agent.parent_identity = parent
            parent_links[agent.identity] = parent
        else:
            agent.parent_identity = ""

    root_session_ids = {identity: node.session_id for identity, node in session_nodes.items()}
    unresolved = {agent.identity: agent for agent in agents}
    while unresolved:
        progressed = False
        for identity, agent in list(unresolved.items()):
            parent = agent.parent_identity
            if parent in root_session_ids:
                agent.parent_session_id = root_session_ids[parent]
            elif parent in unresolved:
                continue
            else:
                parent_agent = next((item for item in agents if item.identity == parent), None)
                if parent_agent is not None and parent_agent.parent_session_id:
                    agent.parent_session_id = parent_agent.parent_session_id
            unresolved.pop(identity)
            progressed = True
        if not progressed:
            for agent in unresolved.values():
                agent.parent_identity = ""
                agent.parent_session_id = ""
            break

    nodes: dict[str, ActivityNode] = dict(session_nodes)
    for agent in agents:
        nodes[agent.identity] = ActivityNode(
            identity=agent.identity,
            kind="subagent",
            parent_identity=agent.parent_identity,
            display_name=agent.agent,
            source=agent.source,
            state=agent.state,
            task=agent.task,
            elapsed=agent.elapsed,
            model=agent.model,
            project=agent.project,
            started_at=agent.started_at,
        )

    unattached: list[AgentStatus] = []
    for agent in agents:
        parent = nodes.get(agent.parent_identity)
        child = nodes[agent.identity]
        if parent is None:
            unattached.append(agent)
            continue
        parent.children.append(child)
    return roots, unattached


def model_activity(
    sessions: list[LiveSession], agents: list[AgentStatus]
) -> list[ModelActivityStats]:
    """Aggregate only model values reported by their own live process."""
    grouped: dict[str, ModelActivityStats] = {}
    for session in sessions:
        model = session.model.strip()
        if not model:
            continue
        stats = grouped.setdefault(model, ModelActivityStats(model=model))
        stats.live_count += 1
        stats.session_count += 1
    for agent in agents:
        model = agent.model.strip()
        if not model:
            continue
        stats = grouped.setdefault(model, ModelActivityStats(model=model))
        stats.live_count += 1
        stats.subagent_count += 1
    return sorted(
        grouped.values(),
        key=lambda stats: (-stats.live_count, -stats.session_count, stats.model.lower()),
    )


def attention_count(sessions: list[LiveSession], agents: list[AgentStatus]) -> int:
    waiting_sessions = sum(session.state == SessionState.WAITING for session in sessions)
    attention_agents = sum(agent.state in ATTENTION_AGENT_STATES for agent in agents)
    return waiting_sessions + attention_agents


def collect_runtime(status_dir: Path, subagent_root: Path) -> RuntimeStatus:
    live = live_sessions(status_dir)
    sessions = [session for session in live if session.kind == "session"]
    child_sessions = [session for session in live if session.kind == "subagent"]
    subagents = subagent_statuses(subagent_root)
    agents = normalize_agents(child_sessions, subagents)
    activity, unattached_agents = activity_tree(sessions, agents)
    subagent_count = len(agents)
    active_main = sum(session.state == SessionState.RUNNING for session in sessions)
    attention_items = attention_count(sessions, agents)
    return RuntimeStatus(
        sessions=sessions,
        child_sessions=child_sessions,
        subagents=subagents,
        agents=agents,
        activity=activity,
        unattached_agents=unattached_agents,
        model_activity=model_activity(sessions, agents),
        session_count=len(sessions),
        subagent_count=subagent_count,
        active_count=active_main + subagent_count,
        attention_count=attention_items,
        attention=attention_items > 0,
    )
