from __future__ import annotations

import html
from datetime import datetime

from pi_beacon.config import Settings
from pi_beacon.indexer import SessionIndexer
from pi_beacon.models import DashboardSnapshot, RuntimeStatus, SessionState, WaybarPayload
from pi_beacon.paths import database_path, live_sessions_dir, sessions_dir, subagent_runs_dir
from pi_beacon.runtime import collect_runtime

SUPERSCRIPT_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def superscript_count(value: int) -> str:
    return str(max(0, value)).translate(SUPERSCRIPT_DIGITS)


class DashboardService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def runtime(self) -> RuntimeStatus:
        return collect_runtime(live_sessions_dir(self.settings), subagent_runs_dir())

    def snapshot(self) -> DashboardSnapshot:
        runtime = self.runtime()
        today = SessionIndexer(
            sessions_dir(self.settings),
            database_path(self.settings),
            self.settings.recent_session_limit,
        ).today()
        return DashboardSnapshot(
            generated_at=datetime.now().astimezone().isoformat(),
            runtime=runtime,
            today=today,
        )

    @staticmethod
    def waybar_payload(runtime: RuntimeStatus) -> WaybarPayload:
        visible_count = runtime.session_count + runtime.subagent_count
        text = f"π{superscript_count(visible_count)}" if visible_count else "π"
        css_class = (
            "attention" if runtime.attention else "active" if runtime.active_count else "idle"
        )
        tooltip = [
            f"Pi Beacon • {runtime.session_count} session(s) • {runtime.subagent_count} subagent(s)"
        ]
        for session in runtime.sessions[:6]:
            elapsed = f" • {session.elapsed}" if session.elapsed else ""
            tooltip.append(f"{session.project} • {session.state.value}{elapsed}")
            if session.detail or session.model:
                tooltip.append(f"  {(session.detail or session.model)[:90]}")
        for agent in runtime.subagents[:6]:
            elapsed = f" • {agent.elapsed}" if agent.elapsed else ""
            tooltip.append(f"↳ {agent.agent} • {agent.state}{elapsed}")
        tooltip.append("Click for dashboard • Ctrl+Alt+F in Pi for FleetView")
        return WaybarPayload(
            text=text,
            css_class=css_class,
            tooltip=html.escape("\n".join(tooltip)),
        )

    def waybar(self) -> WaybarPayload:
        return self.waybar_payload(self.runtime())


def state_color(state: SessionState) -> str:
    return {
        SessionState.RUNNING: "active",
        SessionState.WAITING: "attention",
        SessionState.IDLE: "idle",
        SessionState.OPEN: "idle",
    }[state]
