from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

from pi_beacon.api import create_app
from pi_beacon.config import PathSettings, Settings
from pi_beacon.runtime import process_start_time


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def settings_for(tmp_path: Path) -> Settings:
    sessions = tmp_path / "sessions"
    runtime = tmp_path / "runtime"
    sessions.mkdir()
    runtime.mkdir()
    return Settings(
        paths=PathSettings(
            sessions_dir=sessions,
            runtime_dir=runtime,
            database=tmp_path / "cache.sqlite3",
        )
    )


@pytest.mark.anyio
async def test_health_reports_api_version_and_collector_revision(
    tmp_path: Path, anyio_backend: str
) -> None:
    assert anyio_backend == "asyncio"
    app = create_app(settings_for(tmp_path))
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": 1, "revision": 1}


@pytest.mark.anyio
async def test_snapshot_endpoint_returns_versioned_dashboard(
    tmp_path: Path, anyio_backend: str
) -> None:
    assert anyio_backend == "asyncio"
    app = create_app(settings_for(tmp_path))
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.get("/v1/snapshot")

    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert response.headers["x-pi-beacon-revision"] == "1"


@pytest.mark.anyio
async def test_collector_publishes_a_new_revision_when_live_state_changes(
    tmp_path: Path, anyio_backend: str
) -> None:
    assert anyio_backend == "asyncio"
    settings = settings_for(tmp_path)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.1)
        runtime_dir = settings.paths.runtime_dir
        assert runtime_dir is not None
        status_dir = runtime_dir / "sessions"
        status_dir.joinpath(f"{os.getpid()}.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "pid": os.getpid(),
                    "instanceId": "event-test-instance",
                    "processStartTime": process_start_time(os.getpid()),
                    "sessionId": "event-test",
                    "project": "demo",
                    "state": "running",
                }
            )
        )
        events = app.state.collector.events(after_revision=1)
        event = await asyncio.wait_for(anext(events), timeout=3)
        await events.aclose()

    assert "event: snapshot" in event
    assert "id: 2" in event
    assert '"sessionId":"event-test"' in event


@pytest.mark.anyio
async def test_live_only_service_does_not_create_the_history_database(
    tmp_path: Path, anyio_backend: str
) -> None:
    assert anyio_backend == "asyncio"
    settings = settings_for(tmp_path)
    settings.service.history_enabled = False
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        assert app.state.collector.snapshot.today.sessions == 0

    database = settings.paths.database
    assert database is not None
    assert not database.exists()
