from __future__ import annotations

import errno
import json
import os
import re
import socket
import stat
import sys
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Annotated

import typer
from click import ClickException
from rich.console import Console
from rich.table import Table

from pi_beacon.client import OutputFormat, subscribe
from pi_beacon.config import default_config_path, load_settings
from pi_beacon.database import upgrade_database
from pi_beacon.notifier import NotificationEvent, notify
from pi_beacon.paths import (
    database_path,
    ensure_runtime_root,
    live_sessions_dir,
    sessions_dir,
    socket_path,
    subagent_runs_dir,
)
from pi_beacon.schema import schema_drift, write_schemas
from pi_beacon.service import DashboardService

app = typer.Typer(no_args_is_help=True, help="Pi session observability for Linux desktops.")
console = Console()


def service(config: Path | None) -> DashboardService:
    return DashboardService(load_settings(config))


@app.command()
def snapshot(
    config: Annotated[Path | None, typer.Option(help="Configuration TOML path.")] = None,
    pretty: Annotated[bool, typer.Option(help="Pretty-print JSON.")] = False,
) -> None:
    """Emit the versioned dashboard snapshot as JSON."""
    payload = service(config).snapshot().model_dump(mode="json", by_alias=True)
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))


@app.command()
def waybar(
    config: Annotated[Path | None, typer.Option(help="Configuration TOML path.")] = None,
) -> None:
    """Emit a Waybar custom-module payload."""
    payload = service(config).waybar().model_dump(mode="json", by_alias=True)
    typer.echo(json.dumps(payload, ensure_ascii=False))


@app.command("subscribe")
def subscribe_events(
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Stream payload format."),
    ] = OutputFormat.SNAPSHOT,
    config: Annotated[Path | None, typer.Option(help="Configuration TOML path.")] = None,
    once: Annotated[bool, typer.Option(help="Exit after the first event.")] = False,
) -> None:
    """Subscribe to versioned snapshots from the local API."""
    settings = load_settings(config)
    try:
        subscribe(socket_path(settings), output_format, once=once)
    except RuntimeError as error:
        raise ClickException(str(error)) from error


def prepare_socket_path(endpoint: Path) -> None:
    if not endpoint.exists():
        return
    if not endpoint.is_socket():
        raise typer.BadParameter(f"Socket path is not a Unix socket: {endpoint}")
    with socket.socket(socket.AF_UNIX) as probe:
        try:
            probe.connect(str(endpoint))
        except OSError as error:
            if error.errno in {errno.ECONNREFUSED, errno.ENOENT}:
                endpoint.unlink(missing_ok=True)
                return
            raise typer.BadParameter(f"Cannot inspect Unix socket: {endpoint}") from error
    raise typer.BadParameter(f"Pi Beacon is already listening on {endpoint}")


@app.command()
def serve(
    config: Annotated[Path | None, typer.Option(help="Configuration TOML path.")] = None,
) -> None:
    """Run the persistent local API on a private Unix socket."""
    settings = load_settings(config)
    ensure_runtime_root(settings)
    endpoint = socket_path(settings)
    parent_exists = endpoint.parent.exists()
    endpoint.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not parent_exists or settings.paths.socket is None:
        os.chmod(endpoint.parent, 0o700)
    elif stat.S_IMODE(endpoint.parent.stat().st_mode) & 0o077:
        raise typer.BadParameter(f"Socket directory must be private: {endpoint.parent}")
    prepare_socket_path(endpoint)
    environment = os.environ.copy()
    if config is not None:
        environment["PI_BEACON_CONFIG"] = str(config.resolve())
    arguments = [
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
        str(endpoint),
        "--uds-permissions",
        "0600",
        "--workers-kill-timeout",
        "5",
        "pi_beacon.api:app",
    ]
    os.execvpe(sys.executable, arguments, environment)


@app.command("database-upgrade")
def database_upgrade(
    config: Annotated[Path | None, typer.Option(help="Configuration TOML path.")] = None,
) -> None:
    """Apply all pending Alembic migrations to the local cache."""
    path = database_path(load_settings(config))
    upgrade_database(path)
    typer.echo(path)


@app.command()
def doctor(
    config: Annotated[Path | None, typer.Option(help="Configuration TOML path.")] = None,
) -> None:
    """Check Pi Beacon inputs and show their resolved paths."""
    settings = load_settings(config)
    checks = [
        (
            "Config",
            config or default_config_path(),
            (config or default_config_path()).is_file(),
        ),
        ("Live bridge", live_sessions_dir(settings), live_sessions_dir(settings).is_dir()),
        ("API socket", socket_path(settings), socket_path(settings).is_socket()),
        ("Pi sessions", sessions_dir(settings), sessions_dir(settings).is_dir()),
        (
            "Subagents",
            subagent_runs_dir(settings),
            subagent_runs_dir(settings).is_dir(),
        ),
        ("SQLite index", database_path(settings), database_path(settings).exists()),
    ]
    table = Table(title="Pi Beacon doctor")
    table.add_column("Input")
    table.add_column("Status")
    table.add_column("Path")
    for label, path, available in checks:
        table.add_row(label, "ok" if available else "not found", str(path))
    console.print(table)


@app.command("notify")
def notify_event(
    event: Annotated[NotificationEvent, typer.Argument(help="Lifecycle event.")],
    project: Annotated[str, typer.Option(help="Session display name.")] = "Pi session",
    detail: Annotated[str, typer.Option(help="Notification detail.")] = "",
    duration: Annotated[float, typer.Option(help="Run duration in seconds.", min=0)] = 0,
    config: Annotated[Path | None, typer.Option(help="Configuration TOML path.")] = None,
) -> None:
    """Emit a configured Freedesktop notification."""
    settings = load_settings(config)
    notify(settings.notifications, event, project, detail, duration)


def write_asset(resource: Traversable, target: Path, force: bool) -> None:
    if target.exists() and not force:
        raise typer.BadParameter(f"Target exists: {target}. Pass --force to replace it.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(resource.read_bytes())


@app.command("init-config")
def init_config(
    target: Annotated[Path | None, typer.Option(help="Destination TOML path.")] = None,
    force: Annotated[bool, typer.Option(help="Replace an existing file.")] = False,
) -> None:
    """Install the documented configuration template."""
    destination = target or default_config_path()
    resource = files("pi_beacon").joinpath("assets", "config.toml")
    write_asset(resource, destination, force)
    typer.echo(destination)


@app.command("install-systemd")
def install_systemd(
    target: Annotated[Path | None, typer.Option(help="Destination user unit path.")] = None,
    force: Annotated[bool, typer.Option(help="Replace an existing unit.")] = False,
) -> None:
    """Install the supervised user service unit without enabling it."""
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    destination = target or xdg_config / "systemd" / "user" / "pi-beacon.service"
    resource = files("pi_beacon").joinpath("assets", "systemd", "pi-beacon.service")
    write_asset(resource, destination, force)
    typer.echo(destination)


@app.command("install-quickshell")
def install_quickshell(
    config_name: Annotated[str, typer.Argument(help="Quickshell configuration name.")],
    force: Annotated[bool, typer.Option(help="Replace an existing component.")] = False,
) -> None:
    """Copy the maintained panel and service menu into a Quickshell configuration."""
    if config_name in {".", ".."} or re.fullmatch(r"[A-Za-z0-9._-]+", config_name) is None:
        raise typer.BadParameter(f"Invalid Quickshell config name: {config_name}")
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    destination_dir = xdg_config / "quickshell" / config_name
    resources = files("pi_beacon").joinpath("assets", "quickshell")
    for file_name in (
        "PiBeaconPanel.qml",
        "PiBeaconTheme.qml",
        "PiBeaconServiceMenu.qml",
    ):
        destination = destination_dir / file_name
        write_asset(resources.joinpath(file_name), destination, force)
        typer.echo(destination)


@app.command("schemas")
def schemas(
    output_dir: Annotated[Path, typer.Option(help="Schema output directory.")] = Path("schemas"),
    check: Annotated[bool, typer.Option(help="Fail when committed schemas drift.")] = False,
) -> None:
    """Generate or verify the public JSON Schema contract."""
    if check:
        drift = schema_drift(output_dir)
        if drift:
            for message in drift:
                typer.echo(message, err=True)
            raise typer.Exit(1)
        typer.echo("schemas are current")
        return
    for path in write_schemas(output_dir):
        typer.echo(path)


if __name__ == "__main__":
    app()
