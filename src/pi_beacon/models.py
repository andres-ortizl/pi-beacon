from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel


class BridgeModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="ignore",
        populate_by_name=True,
    )


class SessionState(StrEnum):
    OPEN = "open"
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"


class AgentState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING = "waiting"
    NEEDS_ATTENTION = "needs_attention"


class UsageStats(BridgeModel):
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0
    subagent_cost: float = 0
    total_tokens: int = 0


class ContextStats(BridgeModel):
    tokens: int | None = None
    context_window: int
    percent: float | None = None


class LiveSession(BridgeModel):
    version: int = 1
    pid: int
    parent_pid: int | None = None
    session_id: str
    session_file: Path | None = None
    session_name: str = ""
    display_name: str = ""
    title_source: str = "project"
    cwd: Path | None = None
    project: str
    state: SessionState = SessionState.OPEN
    detail: str = ""
    prompt: str = ""
    model: str = ""
    thinking: str = ""
    usage: UsageStats = Field(default_factory=UsageStats)
    context: ContextStats | None = None
    started_at: int | str | None = None
    last_message_at: int | str | None = None
    updated_at: int | str | None = None
    elapsed: str = ""
    kind: str = "session"


class SubagentStatus(BridgeModel):
    agent: str
    state: str
    task: str = ""
    elapsed: str = ""
    started_at: int | str | None = None
    run_id: str = ""


class RuntimeStatus(BridgeModel):
    sessions: list[LiveSession] = Field(default_factory=list)
    child_sessions: list[LiveSession] = Field(default_factory=list)
    subagents: list[SubagentStatus] = Field(default_factory=list)
    session_count: int = 0
    subagent_count: int = 0
    active_count: int = 0
    attention: bool = False


class RecentSession(BridgeModel):
    session_id: str
    session_file: Path
    project: str
    cwd: Path | None = None
    model: str = ""
    cost: float = 0
    tokens: int = 0
    messages: int = 0
    started_at: str | None = None
    last_activity: str | None = None
    duration_seconds: int = 0


class TodayStats(BridgeModel):
    cost: float = 0
    tokens: int = 0
    messages: int = 0
    sessions: int = 0
    recent: list[RecentSession] = Field(default_factory=list)


class DashboardSnapshot(BridgeModel):
    version: int = 1
    generated_at: str
    runtime: RuntimeStatus
    today: TodayStats


class WaybarPayload(BridgeModel):
    text: str
    alt: str = "pi-beacon"
    css_class: str = Field(serialization_alias="class")
    tooltip: str


class SessionFile(SQLModel, table=True):
    path: str = SQLField(primary_key=True)
    size: int = 0
    mtime_ns: int = 0
    offset: int = 0
    cursor_fingerprint: str = ""
    source_dev: int = 0
    source_inode: int = 0
    day: str = ""
    session_id: str = ""
    cwd: str = ""
    model: str = ""
    cost: float = 0
    tokens: int = 0
    messages: int = 0
    first_activity: str | None = None
    last_activity: str | None = None
