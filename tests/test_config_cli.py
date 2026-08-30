from __future__ import annotations

import json
import os
import socket
import stat
import sys
from pathlib import Path
from typing import TypedDict

import pytest
from typer.testing import CliRunner

import pi_beacon.cli as cli_module
from pi_beacon.cli import app
from pi_beacon.config import PathSettings, Settings, load_settings
from pi_beacon.models import UpdateStatus
from pi_beacon.paths import ensure_runtime_root, live_sessions_dir

runner = CliRunner()


class ExecutedCommand(TypedDict, total=False):
    file: str
    args: list[str]
    environment: dict[str, str]


def test_load_settings_from_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
schema_version = 1
recent_session_limit = 2
[service]
runtime_interval_seconds = 7
[display]
show_cost = false
[paths]
sessions_dir = "/tmp/sessions"
""".strip()
    )
    monkeypatch.setenv("PI_BEACON_PATHS__SESSIONS_DIR", "/tmp/environment-sessions")
    settings = load_settings(config)
    assert settings.service.runtime_interval_seconds == 7
    assert settings.recent_session_limit == 2
    assert not settings.display.show_cost
    assert settings.paths.sessions_dir == Path("/tmp/environment-sessions")


def test_runtime_root_secures_a_normal_directory(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o755)
    settings = Settings(paths=PathSettings(runtime_dir=runtime))

    ensure_runtime_root(settings)

    assert stat.S_IMODE(runtime.stat().st_mode) == 0o700


def test_runtime_root_rejects_a_symbolic_link(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "runtime"
    linked.symlink_to(target, target_is_directory=True)
    settings = Settings(paths=PathSettings(runtime_dir=linked))

    with pytest.raises(RuntimeError, match="symbolic link"):
        ensure_runtime_root(settings)


def test_live_sessions_directory_rejects_a_symbolic_link(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (runtime / "sessions").symlink_to(target, target_is_directory=True)
    settings = Settings(paths=PathSettings(runtime_dir=runtime))

    with pytest.raises(RuntimeError, match="symbolic link"):
        live_sessions_dir(settings)


def test_waybar_and_snapshot_commands(tmp_path: Path, monkeypatch) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("PI_BEACON_PATHS__SESSIONS_DIR", str(sessions))
    monkeypatch.setenv("PI_BEACON_PATHS__RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("PI_BEACON_PATHS__DATABASE", str(tmp_path / "cache.sqlite3"))

    waybar = runner.invoke(app, ["waybar"])
    assert waybar.exit_code == 0
    assert json.loads(waybar.stdout)["alt"] == "pi-beacon"

    snapshot = runner.invoke(app, ["snapshot"])
    assert snapshot.exit_code == 0
    assert json.loads(snapshot.stdout)["version"] == 1

    version = runner.invoke(app, ["version"])
    assert version.exit_code == 0
    assert version.stdout.strip() == "1.1.0"


def test_update_check_command_writes_dashboard_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "cache" / "sessions.sqlite3"
    monkeypatch.setenv("PI_BEACON_PATHS__DATABASE", str(database))
    monkeypatch.setattr(
        cli_module,
        "check_for_update",
        lambda: UpdateStatus(
            current_version="1.0.0",
            latest_version="1.1.0",
            available=True,
            release_url="https://example.test/releases/v1.1.0",
        ),
    )

    result = runner.invoke(app, ["update", "--check"])

    assert result.exit_code == 0
    assert "Update available" in result.stdout
    payload = json.loads(database.with_name("update.json").read_text())
    assert payload["latestVersion"] == "1.1.0"


def test_asset_installers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = runner.invoke(app, ["init-config"])
    assert config.exit_code == 0
    assert (tmp_path / "pi-beacon" / "config.toml").is_file()

    qml = runner.invoke(app, ["install-quickshell", "demo"])
    assert qml.exit_code == 0
    assert (tmp_path / "quickshell" / "demo" / "PiBeaconPanel.qml").is_file()
    assert (tmp_path / "quickshell" / "demo" / "PiBeaconOverview.qml").is_file()
    assert (tmp_path / "quickshell" / "demo" / "PiBeaconActivity.qml").is_file()
    assert (tmp_path / "quickshell" / "demo" / "PiBeaconActivityNode.qml").is_file()
    assert (tmp_path / "quickshell" / "demo" / "PiBeaconModels.qml").is_file()
    assert (tmp_path / "quickshell" / "demo" / "PiBeaconTheme.qml").is_file()
    assert (tmp_path / "quickshell" / "demo" / "PiBeaconServiceMenu.qml").is_file()

    duplicate = runner.invoke(app, ["install-quickshell", "demo"])
    assert duplicate.exit_code != 0

    service_target = tmp_path / "systemd" / "pi-beacon.service"
    service = runner.invoke(
        app,
        ["install-systemd", "--target", str(service_target)],
    )
    assert service.exit_code == 0
    assert "pi-beacon serve" in service_target.read_text()

    update_timer = runner.invoke(app, ["install-update-timer"])
    assert update_timer.exit_code == 0
    update_service_path = tmp_path / "systemd" / "user" / "pi-beacon-update-check.service"
    update_timer_path = tmp_path / "systemd" / "user" / "pi-beacon-update-check.timer"
    assert "pi-beacon update --check" in update_service_path.read_text()
    assert "OnUnitActiveSec=24h" in update_timer_path.read_text()


@pytest.mark.parametrize("config_name", [".", "..", "../outside"])
def test_quickshell_installer_rejects_path_escape_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_name: str,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    result = runner.invoke(app, ["install-quickshell", config_name, "--force"])

    assert result.exit_code != 0
    assert "Invalid Quickshell config name" in result.output
    assert not (tmp_path / "config" / "outside" / "PiBeaconPanel.qml").exists()


def test_quickshell_installer_rejects_an_absolute_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    outside = tmp_path / "absolute"

    result = runner.invoke(app, ["install-quickshell", str(outside), "--force"])

    assert result.exit_code != 0
    assert "Invalid Quickshell config name" in result.output
    assert not (outside / "PiBeaconPanel.qml").exists()


def test_doctor_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PI_BEACON_PATHS__RUNTIME_DIR", str(tmp_path / "runtime"))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Pi Beacon doctor" in result.stdout


def test_schema_command(tmp_path: Path) -> None:
    output = tmp_path / "schemas"
    generated = runner.invoke(app, ["schemas", "--output-dir", str(output)])
    assert generated.exit_code == 0
    checked = runner.invoke(app, ["schemas", "--output-dir", str(output), "--check"])
    assert checked.exit_code == 0
    assert "schemas are current" in checked.stdout


def test_serve_uses_granian_uvloop_and_the_configured_unix_socket(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "config.toml"
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    stale_socket = runtime / "api.sock"
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(stale_socket))
    config.write_text(f'[paths]\nruntime_dir = "{runtime}"\n')
    executed: ExecutedCommand = {}

    def capture_exec(file: str, args: list[str], environment: dict[str, str]) -> None:
        executed.update(file=file, args=args, environment=environment)

    monkeypatch.setattr(os, "execvpe", capture_exec)
    result = runner.invoke(app, ["serve", "--config", str(config)])

    assert result.exit_code == 0
    assert "file" in executed
    assert "args" in executed
    assert "environment" in executed
    assert executed["file"] == sys.executable
    assert "--loop" in executed["args"]
    assert "uvloop" in executed["args"]
    assert str(stale_socket) in executed["args"]
    assert not stale_socket.exists()
    assert executed["environment"]["PI_BEACON_CONFIG"] == str(config)


def test_notify_command_can_be_disabled(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("[notifications]\nenabled = false\n")
    result = runner.invoke(
        app,
        [
            "notify",
            "settled",
            "--project",
            "demo",
            "--duration",
            "12",
            "--config",
            str(config),
        ],
    )
    assert result.exit_code == 0
