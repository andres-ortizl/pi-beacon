from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from pi_beacon.models import DashboardSnapshot, LiveSession, WaybarPayload

SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "live-session-v1.schema.json": LiveSession,
    "dashboard-snapshot-v1.schema.json": DashboardSnapshot,
    "waybar-payload-v1.schema.json": WaybarPayload,
}


def render_schema(model: type[BaseModel]) -> str:
    payload = model.model_json_schema(by_alias=True, mode="serialization")
    payload["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def write_schemas(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for file_name, model in SCHEMA_MODELS.items():
        path = output_dir / file_name
        path.write_text(render_schema(model))
        paths.append(path)
    return paths


def schema_drift(output_dir: Path) -> list[str]:
    drift: list[str] = []
    for file_name, model in SCHEMA_MODELS.items():
        path = output_dir / file_name
        expected = render_schema(model)
        if not path.is_file():
            drift.append(f"missing: {path}")
        elif path.read_text() != expected:
            drift.append(f"outdated: {path}")
    return drift
