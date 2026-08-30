from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import pi_beacon.client as client_module
from pi_beacon.client import OutputFormat, iter_sse, main, resolve_socket, subscribe


def test_stream_client_does_not_import_database_or_api_stacks() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import pi_beacon.client; "
                "assert 'sqlmodel' not in sys.modules; "
                "assert 'fastapi' not in sys.modules"
            ),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_sse_decoder_handles_comments_multiline_data_and_retry() -> None:
    events = list(
        iter_sse(
            [
                b": keep-alive\n",
                b"id: 7\n",
                b"event: snapshot\n",
                b"retry: 1500\n",
                b'data: {"part":\n',
                b"data: true}\n",
                b"\n",
            ]
        )
    )

    assert len(events) == 1
    assert events[0].event == "snapshot"
    assert events[0].event_id == "7"
    assert events[0].retry == 1500
    assert events[0].data == '{"part":\ntrue}'


def test_resolve_socket_uses_environment_then_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "configured.sock"
    monkeypatch.setenv("PI_BEACON_PATHS__SOCKET", str(configured))
    assert resolve_socket() == configured

    monkeypatch.delenv("PI_BEACON_PATHS__SOCKET")
    runtime = tmp_path / "runtime"
    config = tmp_path / "config.toml"
    config.write_text(f'[paths]\nruntime_dir = "{runtime}"\n')
    assert resolve_socket(config) == runtime / "api.sock"


def test_one_shot_subscriber_reports_an_unavailable_socket(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="API is unavailable"):
        subscribe(tmp_path / "missing.sock", OutputFormat.SNAPSHOT, once=True)


def test_stream_entrypoint_reports_an_unavailable_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing.sock"
    monkeypatch.setattr(
        sys,
        "argv",
        ["pi-beacon-stream", "--once", "--socket", str(missing)],
    )

    assert main() == 1
    assert str(missing) in capsys.readouterr().err


def test_waybar_subscriber_emits_an_offline_control_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unavailable(*_args: object, **_kwargs: object):
        raise OSError("service stopped")

    def stop_after_first_retry(_seconds: float) -> None:
        raise StopIteration

    monkeypatch.setattr(client_module, "read_events", unavailable)
    monkeypatch.setattr(client_module.time, "sleep", stop_after_first_retry)

    with pytest.raises(StopIteration):
        subscribe(tmp_path / "missing.sock", OutputFormat.WAYBAR)

    payload = json.loads(capsys.readouterr().out)
    assert payload["alt"] == "pi-beacon-offline"
    assert payload["class"] == "offline"
    assert "Right-click" in payload["tooltip"]
