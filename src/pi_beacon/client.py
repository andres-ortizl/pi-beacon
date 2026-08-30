from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import sys
import tempfile
import time
import tomllib
from collections.abc import Generator, Iterable, Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class OutputFormat(StrEnum):
    SNAPSHOT = "snapshot"
    WAYBAR = "waybar"


WAYBAR_OFFLINE_PAYLOAD = json.dumps(
    {
        "text": "π off",
        "alt": "pi-beacon-offline",
        "class": "offline",
        "tooltip": "Pi Beacon is stopped\nRight-click for service controls",
    },
    ensure_ascii=False,
    separators=(",", ":"),
)


@dataclass(frozen=True)
class ServerSentEvent:
    event: str
    data: str
    event_id: str
    retry: int | None


@dataclass
class SSEDecoder:
    event_type: str = "message"
    data: list[str] = field(default_factory=list)
    event_id: str = ""
    retry: int | None = None

    def dispatch(self) -> ServerSentEvent | None:
        event = (
            ServerSentEvent(self.event_type, "\n".join(self.data), self.event_id, self.retry)
            if self.data
            else None
        )
        self.event_type = "message"
        self.data = []
        self.retry = None
        return event

    def feed(self, raw_line: bytes) -> ServerSentEvent | None:
        line = raw_line.decode("utf8").rstrip("\r\n")
        if not line:
            return self.dispatch()
        if line.startswith(":"):
            return None
        field_name, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field_name == "event":
            self.event_type = value
        elif field_name == "data":
            self.data.append(value)
        elif field_name == "id" and "\0" not in value:
            self.event_id = value
        elif field_name == "retry":
            with suppress(ValueError):
                self.retry = int(value)
        return None


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, endpoint: Path):
        super().__init__(host="pi-beacon", timeout=None)
        self.endpoint = endpoint

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.connect(str(self.endpoint))
        except BaseException:
            connection.close()
            raise
        self.sock = connection


def iter_sse(lines: Iterable[bytes]) -> Iterator[ServerSentEvent]:
    decoder = SSEDecoder()
    for raw_line in lines:
        if event := decoder.feed(raw_line):
            yield event
    if event := decoder.dispatch():
        yield event


def configured_path(payload: dict[str, Any], name: str) -> Path | None:
    paths = payload.get("paths")
    if not isinstance(paths, dict):
        return None
    value = paths.get(name)
    if not isinstance(value, str) or not value:
        return None
    return Path(value).expanduser()


def resolve_socket(config_path: Path | None = None) -> Path:
    if configured := os.environ.get("PI_BEACON_PATHS__SOCKET"):
        return Path(configured).expanduser()
    if runtime := os.environ.get("PI_BEACON_PATHS__RUNTIME_DIR"):
        return Path(runtime).expanduser() / "api.sock"

    environment_config = os.environ.get("PI_BEACON_CONFIG")
    config_path = config_path or (
        Path(environment_config).expanduser() if environment_config else None
    )
    if config_path is None:
        config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        config_path = config_root / "pi-beacon" / "config.toml"
    if config_path.is_file():
        with config_path.open("rb") as config_file:
            payload = tomllib.load(config_file)
        if endpoint := configured_path(payload, "socket"):
            return endpoint
        if runtime := configured_path(payload, "runtime_dir"):
            return runtime / "api.sock"

    if runtime := os.environ.get("XDG_RUNTIME_DIR"):
        return Path(runtime) / "pi-beacon" / "api.sock"
    return Path(tempfile.gettempdir()) / f"pi-runtime-{os.getuid()}" / "pi-beacon" / "api.sock"


def read_events(
    endpoint: Path,
    output_format: OutputFormat,
    last_event_id: str,
) -> Generator[ServerSentEvent, None, None]:
    event_path = "/v1/events/waybar" if output_format is OutputFormat.WAYBAR else "/v1/events"
    headers = {
        "Accept": "text/event-stream",
        "Cache-Control": "no-store",
    }
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id
    connection = UnixHTTPConnection(endpoint)
    try:
        connection.request("GET", event_path, headers=headers)
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"Pi Beacon API returned HTTP {response.status}")
        content_type = response.getheader("content-type", "").partition(";")[0]
        if content_type != "text/event-stream":
            raise RuntimeError(f"Unexpected Pi Beacon content type: {content_type or 'missing'}")
        yield from iter_sse(response)
    finally:
        connection.close()


def subscribe(endpoint: Path, output_format: OutputFormat, once: bool = False) -> None:
    last_event_id = ""
    reconnect_delay = 0.25
    offline_emitted = False

    def emit_offline() -> None:
        nonlocal offline_emitted
        if output_format is OutputFormat.WAYBAR and not offline_emitted:
            print(WAYBAR_OFFLINE_PAYLOAD, flush=True)
            offline_emitted = True

    while True:
        try:
            for event in read_events(endpoint, output_format, last_event_id):
                if event.event != output_format.value or not event.data:
                    continue
                print(event.data, flush=True)
                offline_emitted = False
                if event.event_id:
                    last_event_id = event.event_id
                if event.retry is not None:
                    reconnect_delay = max(0.05, event.retry / 1000)
                if once:
                    return
            if once:
                return
            emit_offline()
        except (OSError, UnicodeError, http.client.HTTPException, RuntimeError) as error:
            if once:
                raise RuntimeError(f"Pi Beacon API is unavailable at {endpoint}") from error
            emit_offline()
            print(f"Pi Beacon reconnecting: {error}", file=sys.stderr)
        time.sleep(reconnect_delay)
        reconnect_delay = min(5, reconnect_delay * 2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stream Pi Beacon SSE payloads.")
    parser.add_argument(
        "--format",
        choices=OutputFormat,
        type=OutputFormat,
        default=OutputFormat.SNAPSHOT,
    )
    parser.add_argument("--once", action="store_true", help="Exit after the first event.")
    parser.add_argument("--socket", type=Path, help="Unix socket path.")
    parser.add_argument("--config", type=Path, help="Configuration TOML path.")
    arguments = parser.parse_args()
    endpoint = arguments.socket or resolve_socket(arguments.config)
    try:
        subscribe(endpoint, arguments.format, once=arguments.once)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    return 0
