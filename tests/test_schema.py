from __future__ import annotations

import json
from pathlib import Path

from pi_beacon.schema import schema_drift, write_schemas

ROOT = Path(__file__).parents[1]


def test_committed_schemas_are_current() -> None:
    assert schema_drift(ROOT / "schemas") == []


def test_live_schema_exposes_session_metadata() -> None:
    schema = json.loads((ROOT / "schemas" / "live-session-v1.schema.json").read_text())
    properties = schema["properties"]
    assert "displayName" in properties
    assert "sessionFile" in properties
    assert "startedAt" in properties
    assert "lastMessageAt" in properties


def test_dashboard_schema_exposes_activity_and_model_history() -> None:
    schema = json.loads((ROOT / "schemas" / "dashboard-snapshot-v1.schema.json").read_text())
    definitions = schema["$defs"]
    runtime = definitions["RuntimeStatus"]["properties"]
    history = definitions["HistoryStats"]["properties"]
    agent = definitions["AgentStatus"]["properties"]
    update = definitions["UpdateStatus"]["properties"]

    assert "activity" in runtime
    assert "modelActivity" in runtime
    assert "unattachedAgents" in runtime
    assert "modelUsageToday" in history
    assert "modelUsage7d" in history
    assert "parentIdentity" in agent
    assert "model" in agent
    assert "latestVersion" in update
    assert "available" in update
    assert "update" in schema["properties"]


def test_schema_generation_to_new_directory(tmp_path: Path) -> None:
    paths = write_schemas(tmp_path)
    assert len(paths) == 3
    assert schema_drift(tmp_path) == []
