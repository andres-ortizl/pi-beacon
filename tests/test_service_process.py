from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
from pathlib import Path
from threading import Event

import httpx

from pi_beacon.client import OutputFormat, read_events, subscribe


def wait_for_socket(socket_path: Path, process: subprocess.Popen[str]) -> None:
    waiter = Event()
    for _ in range(100):
        if socket_path.is_socket():
            return
        return_code = process.poll()
        if return_code is not None:
            _stdout, stderr = process.communicate()
            raise AssertionError(f"Granian exited with {return_code}: {stderr}")
        waiter.wait(0.05)
    raise AssertionError(f"Unix socket was not created: {socket_path}")


def get_when_ready(client: httpx.Client, process: subprocess.Popen[str]) -> httpx.Response:
    waiter = Event()
    for _ in range(100):
        try:
            return client.get("/v1/snapshot")
        except httpx.ConnectError:
            return_code = process.poll()
            if return_code is not None:
                _stdout, stderr = process.communicate()
                raise AssertionError(f"Granian exited with {return_code}: {stderr}") from None
            waiter.wait(0.05)
    raise AssertionError("Granian did not accept connections")


def stop_process(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def test_granian_uvloop_serves_snapshot_and_sse_over_private_socket(tmp_path: Path, capsys) -> None:
    sessions = tmp_path / "sessions"
    runtime = tmp_path / "runtime"
    sessions.mkdir()
    runtime.mkdir()
    socket_path = runtime / "api.sock"
    config = tmp_path / "config.toml"
    config.write_text(
        f'schema_version = 1\n[paths]\nsessions_dir = "{sessions}"\n'
        f'runtime_dir = "{runtime}"\ndatabase = "{tmp_path / "cache.sqlite3"}"\n'
    )
    environment = os.environ.copy()
    environment["PI_BEACON_CONFIG"] = str(config)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "granian",
            "--interface",
            "asgi",
            "--loop",
            "uvloop",
            "--workers",
            "1",
            "--http",
            "1",
            "--no-ws",
            "--uds",
            str(socket_path),
            "--uds-permissions",
            "0600",
            "pi_beacon.api:app",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
    )
    try:
        wait_for_socket(socket_path, process)
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
        transport = httpx.HTTPTransport(uds=str(socket_path))
        with httpx.Client(transport=transport, base_url="http://pi-beacon") as client:
            response = get_when_ready(client, process)
            assert response.status_code == 200
            assert response.json()["version"] == 1
            assert response.headers["x-pi-beacon-revision"] == "1"
        event_stream = read_events(socket_path, OutputFormat.SNAPSHOT, "")
        event = next(event_stream)
        event_stream.close()
        assert event.event == "snapshot"
        assert event.event_id == "1"
        assert '"version":1' in event.data
        subscribe(socket_path, OutputFormat.WAYBAR, once=True)
        streamed = capsys.readouterr().out
        assert '"alt":"pi-beacon"' in streamed
    finally:
        stop_process(process)


def test_service_stops_with_an_active_sse_subscriber(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    runtime = tmp_path / "runtime"
    sessions.mkdir()
    runtime.mkdir()
    socket_path = runtime / "api.sock"
    config = tmp_path / "config.toml"
    config.write_text(
        f"schema_version = 1\n[service]\nhistory_enabled = false\n[paths]\n"
        f'sessions_dir = "{sessions}"\nruntime_dir = "{runtime}"\n'
        f'database = "{tmp_path / "cache.sqlite3"}"\n'
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "pi_beacon", "serve", "--config", str(config)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    event_stream = None
    try:
        wait_for_socket(socket_path, process)
        transport = httpx.HTTPTransport(uds=str(socket_path))
        with httpx.Client(transport=transport, base_url="http://pi-beacon") as client:
            assert get_when_ready(client, process).status_code == 200
        event_stream = read_events(socket_path, OutputFormat.SNAPSHOT, "")
        assert next(event_stream).event == "snapshot"

        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            raise AssertionError(
                "Service did not stop while an SSE subscriber was active"
            ) from None
        assert process.returncode == 0
    finally:
        if event_stream is not None:
            event_stream.close()
        stop_process(process)
