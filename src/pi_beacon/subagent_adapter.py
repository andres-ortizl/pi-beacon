from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pi_beacon.models import SubagentStatus
from pi_beacon.time_utils import elapsed

ADAPTER_NAME = "pi-subagents"
ADAPTER_VERSION = 1
SUPPORTED_ARTIFACT_VERSIONS = frozenset({3})
ACTIVE_STATES = frozenset({"queued", "running", "paused", "waiting", "needs_attention"})

JsonObject = dict[str, Any]


def mapping(value: object) -> JsonObject | None:
    return value if isinstance(value, dict) else None


def optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def optional_str(value: object) -> str:
    return value if isinstance(value, str) else ""


class PiSubagentsAdapter:
    name = ADAPTER_NAME
    version = ADAPTER_VERSION
    supported_artifact_versions = SUPPORTED_ARTIFACT_VERSIONS

    def collect(self, root: Path) -> list[SubagentStatus]:
        statuses: list[SubagentStatus] = []
        if not root.is_dir():
            return statuses
        for path in root.rglob("status.json"):
            statuses.extend(self.read_status(path))
        return statuses

    def read_status(self, path: Path) -> list[SubagentStatus]:
        try:
            payload = mapping(json.loads(path.read_text(encoding="utf8")))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []
        if payload is None:
            return []
        artifact_version = optional_int(payload.get("lifecycleArtifactVersion"))
        if artifact_version not in self.supported_artifact_versions:
            return []
        run_state = str(payload.get("state", "")).lower()
        if run_state not in ACTIVE_STATES:
            return []
        statuses: list[SubagentStatus] = []
        for step in self.candidates(payload, run_state):
            try:
                status = self.parse_status(payload, step, run_state, artifact_version)
            except (TypeError, ValueError):
                continue
            statuses.append(status)
        return statuses

    @staticmethod
    def candidates(payload: JsonObject, run_state: str) -> list[JsonObject]:
        step_values = payload.get("steps")
        steps = [mapping(step) for step in step_values] if isinstance(step_values, list) else []
        active_steps = [
            step
            for step in steps
            if step is not None
            and str(step.get("status", step.get("state", ""))).lower() in ACTIVE_STATES
        ]
        return active_steps or [payload]

    @staticmethod
    def parse_status(
        payload: JsonObject,
        step: JsonObject,
        run_state: str,
        artifact_version: int,
    ) -> SubagentStatus:
        started_at = step.get("startedAt") or payload.get("startedAt")
        return SubagentStatus.model_validate(
            {
                "adapter": ADAPTER_NAME,
                "adapter_version": ADAPTER_VERSION,
                "artifact_version": artifact_version,
                "agent": str(
                    step.get("agent") or step.get("label") or payload.get("mode") or "agent"
                ),
                "state": str(step.get("status", step.get("state", run_state))).lower(),
                "task": str(
                    step.get("task")
                    or step.get("description")
                    or payload.get("goal")
                    or payload.get("task")
                    or ""
                ),
                "elapsed": elapsed(started_at),
                "started_at": started_at,
                "run_id": optional_str(payload.get("runId") or payload.get("id")),
                "step_id": optional_str(step.get("stepId") or step.get("id")),
                "pid": optional_int(step.get("pid") or payload.get("pid")),
                "parent_pid": optional_int(step.get("parentPid") or payload.get("parentPid")),
                "instance_id": optional_str(step.get("instanceId") or payload.get("instanceId")),
                "process_start_time": optional_str(
                    step.get("processStartTime") or payload.get("processStartTime")
                ),
            }
        )
