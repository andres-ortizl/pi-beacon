from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"


def executable(path: Path, content: str) -> None:
    path.write_text("#!/bin/sh\nset -eu\n" + content)
    path.chmod(0o755)


def installer_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    home.mkdir()
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    executable(
        fake_bin / "uv",
        'printf \'uv %s\\n\' "$*" >> "$INSTALL_LOG"\n'
        'if [ "${FAIL_COMMAND:-}" = "uv" ]; then exit 7; fi\n'
        'if [ "$1 $2" = "tool install" ]; then\n'
        '  mkdir -p "$HOME/.local/bin"\n'
        '  cp "$FAKE_PI_BEACON" "$HOME/.local/bin/pi-beacon"\n'
        "fi\n",
    )
    executable(
        fake_bin / "pi",
        'printf \'pi %s\\n\' "$*" >> "$INSTALL_LOG"\n'
        'if [ "${FAIL_COMMAND:-}" = "pi" ]; then exit 7; fi\n',
    )
    executable(
        fake_bin / "systemctl",
        'printf \'systemctl %s\\n\' "$*" >> "$INSTALL_LOG"\n'
        'if [ "${FAIL_COMMAND:-}" = "systemctl" ]; then exit 7; fi\n',
    )
    fake_pi_beacon = tmp_path / "fake-pi-beacon"
    executable(
        fake_pi_beacon,
        'printf \'pi-beacon %s\\n\' "$*" >> "$INSTALL_LOG"\n'
        'case "$1" in\n'
        "  init-config)\n"
        '    mkdir -p "$XDG_CONFIG_HOME/pi-beacon"\n'
        '    : > "$XDG_CONFIG_HOME/pi-beacon/config.toml"\n'
        "    ;;\n"
        "  install-systemd)\n"
        '    mkdir -p "$XDG_CONFIG_HOME/systemd/user"\n'
        '    : > "$XDG_CONFIG_HOME/systemd/user/pi-beacon.service"\n'
        "    ;;\n"
        "  install-quickshell)\n"
        '    mkdir -p "$XDG_CONFIG_HOME/quickshell/$2"\n'
        '    : > "$XDG_CONFIG_HOME/quickshell/$2/PiBeaconPanel.qml"\n'
        '    : > "$XDG_CONFIG_HOME/quickshell/$2/PiBeaconTheme.qml"\n'
        '    : > "$XDG_CONFIG_HOME/quickshell/$2/PiBeaconServiceMenu.qml"\n'
        "    ;;\n"
        "esac\n",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "INSTALL_LOG": str(log),
            "FAKE_PI_BEACON": str(fake_pi_beacon),
        }
    )
    return environment, log


def test_installer_sets_up_backend_extension_service_and_optional_quickshell(
    tmp_path: Path,
) -> None:
    environment, log = installer_environment(tmp_path)

    result = subprocess.run(
        [
            "sh",
            str(INSTALLER),
            "install",
            "--yes",
            "--version",
            "v9.9.9",
            "--quickshell",
            "demo",
        ],
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    commands = log.read_text()
    assert (
        "uv tool install --force git+https://github.com/andres-ortizl/pi-beacon.git@v9.9.9"
        in commands
    )
    assert "pi install git:github.com/andres-ortizl/pi-beacon@v9.9.9" in commands
    assert "pi-beacon init-config" in commands
    assert "pi-beacon install-systemd --force" in commands
    assert "pi-beacon install-quickshell demo --force" in commands
    assert "systemctl --user daemon-reload" in commands
    assert "systemctl --user enable --now pi-beacon.service" in commands


def test_uninstall_preserves_config_and_cache_by_default(tmp_path: Path) -> None:
    environment, log = installer_environment(tmp_path)
    installed = subprocess.run(
        ["sh", str(INSTALLER), "install", "--yes", "--quickshell", "demo"],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, installed.stderr
    config_home = Path(environment["XDG_CONFIG_HOME"])
    cache_home = Path(environment["XDG_CACHE_HOME"])
    cache_file = cache_home / "pi-beacon" / "sessions.sqlite3"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text("cache")

    removed = subprocess.run(
        ["sh", str(INSTALLER), "uninstall", "--yes"],
        env=environment,
        capture_output=True,
        text=True,
    )

    assert removed.returncode == 0, removed.stderr
    assert (config_home / "pi-beacon" / "config.toml").is_file()
    assert cache_file.is_file()
    assert not (config_home / "systemd" / "user" / "pi-beacon.service").exists()
    assert (config_home / "quickshell" / "demo" / "PiBeaconServiceMenu.qml").is_file()
    commands = log.read_text()
    assert "systemctl --user disable --now pi-beacon.service" in commands
    assert "pi remove git:github.com/andres-ortizl/pi-beacon" in commands
    assert "uv tool uninstall pi-beacon" in commands
    assert "Config and cache were preserved" in removed.stdout


def test_purge_removes_data_and_only_installer_managed_qml(tmp_path: Path) -> None:
    environment, _log = installer_environment(tmp_path)
    runtime_home = tmp_path / "runtime"
    environment["XDG_RUNTIME_DIR"] = str(runtime_home)
    installed = subprocess.run(
        ["sh", str(INSTALLER), "install", "--yes", "--quickshell", "demo"],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, installed.stderr
    updated = subprocess.run(
        [
            "sh",
            str(INSTALLER),
            "update",
            "--yes",
            "--version",
            "v9.9.10",
            "--quickshell",
            "second",
        ],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert updated.returncode == 0, updated.stderr
    config_home = Path(environment["XDG_CONFIG_HOME"])
    cache_home = Path(environment["XDG_CACHE_HOME"])
    qml_dir = config_home / "quickshell" / "demo"
    second_qml_dir = config_home / "quickshell" / "second"
    unrelated = qml_dir / "shell.qml"
    unrelated.write_text("keep")
    cache_file = cache_home / "pi-beacon" / "sessions.sqlite3"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text("cache")
    runtime_file = runtime_home / "pi-beacon" / "api.sock"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_text("socket")

    removed = subprocess.run(
        ["sh", str(INSTALLER), "uninstall", "--purge", "--yes"],
        env=environment,
        capture_output=True,
        text=True,
    )

    assert removed.returncode == 0, removed.stderr
    assert not (config_home / "pi-beacon").exists()
    assert not (cache_home / "pi-beacon").exists()
    assert not (runtime_home / "pi-beacon").exists()
    assert unrelated.is_file()
    assert not (qml_dir / "PiBeaconPanel.qml").exists()
    assert not (qml_dir / "PiBeaconTheme.qml").exists()
    assert not (qml_dir / "PiBeaconServiceMenu.qml").exists()
    assert not (second_qml_dir / "PiBeaconPanel.qml").exists()
    assert not (second_qml_dir / "PiBeaconTheme.qml").exists()
    assert not (second_qml_dir / "PiBeaconServiceMenu.qml").exists()
    assert "local data were removed" in removed.stdout


def test_installer_supports_the_documented_stdin_pipe(tmp_path: Path) -> None:
    environment, log = installer_environment(tmp_path)

    result = subprocess.run(
        ["sh", "-s", "--", "install", "--yes", "--version", "v9.9.9"],
        input=INSTALLER.read_text(),
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "uv tool install --force" in log.read_text()


def test_installer_bootstraps_uv_with_explicit_consent(tmp_path: Path) -> None:
    environment, log = installer_environment(tmp_path)
    fake_bin = Path(environment["PATH"].split(":", maxsplit=1)[0])
    saved_uv = tmp_path / "uv-after-bootstrap"
    (fake_bin / "uv").replace(saved_uv)
    tool_bin = tmp_path / "tools-without-uv"
    tool_bin.mkdir()
    for command in (
        "cat",
        "chmod",
        "cp",
        "head",
        "id",
        "mkdir",
        "mktemp",
        "rm",
        "sed",
        "sh",
        "uname",
    ):
        resolved = shutil.which(command)
        assert resolved is not None
        (tool_bin / command).symlink_to(resolved)
    environment["PATH"] = f"{fake_bin}:{tool_bin}"
    uv_installer = tmp_path / "uv-installer.sh"
    uv_installer.write_text(
        '#!/bin/sh\nmkdir -p "$HOME/.local/bin"\n'
        'cp "$FAKE_UV_BINARY" "$HOME/.local/bin/uv"\n'
        'chmod 755 "$HOME/.local/bin/uv"\n'
    )
    environment["FAKE_UV_BINARY"] = str(saved_uv)
    environment["FAKE_UV_INSTALLER"] = str(uv_installer)
    executable(
        fake_bin / "curl",
        'printf \'curl %s\\n\' "$*" >> "$INSTALL_LOG"\n'
        'output=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        "    -o) output=$2; shift 2 ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        'cp "$FAKE_UV_INSTALLER" "$output"\n',
    )

    result = subprocess.run(
        ["sh", str(INSTALLER), "install", "--yes", "--version", "v9.9.9"],
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    commands = log.read_text()
    assert "curl -LsSf -o " in commands
    assert "https://astral.sh/uv/install.sh" in commands
    assert "uv tool install --force" in commands


def test_failed_uv_download_never_executes_a_partial_script(tmp_path: Path) -> None:
    environment, log = installer_environment(tmp_path)
    fake_bin = Path(environment["PATH"].split(":", maxsplit=1)[0])
    (fake_bin / "uv").unlink()
    tool_bin = tmp_path / "tools-without-uv"
    tool_bin.mkdir()
    for command in (
        "cat",
        "chmod",
        "cp",
        "head",
        "id",
        "mkdir",
        "mktemp",
        "rm",
        "sed",
        "sh",
        "uname",
    ):
        resolved = shutil.which(command)
        assert resolved is not None
        (tool_bin / command).symlink_to(resolved)
    environment["PATH"] = f"{fake_bin}:{tool_bin}"
    marker = Path(environment["HOME"]) / "partial-script-ran"
    environment["PARTIAL_MARKER"] = str(marker)
    executable(
        fake_bin / "curl",
        'output=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        "    -o) output=$2; shift 2 ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        'printf \'#!/bin/sh\\n: > "$PARTIAL_MARKER"\\n\' > "$output"\n'
        "exit 22\n",
    )

    result = subprocess.run(
        ["sh", str(INSTALLER), "install", "--yes"],
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not marker.exists()
    assert not log.exists() or "uv tool install" not in log.read_text()


@pytest.mark.parametrize("failing_command", ["systemctl", "pi", "uv"])
def test_uninstall_reports_component_removal_failures(
    tmp_path: Path,
    failing_command: str,
) -> None:
    environment, _log = installer_environment(tmp_path)
    installed = subprocess.run(
        ["sh", str(INSTALLER), "install", "--yes"],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, installed.stderr
    environment["FAIL_COMMAND"] = failing_command

    removed = subprocess.run(
        ["sh", str(INSTALLER), "uninstall", "--yes"],
        env=environment,
        capture_output=True,
        text=True,
    )

    assert removed.returncode != 0
    assert "Pi Beacon was removed" not in removed.stdout
    assert "Pi Beacon and its local data were removed" not in removed.stdout
