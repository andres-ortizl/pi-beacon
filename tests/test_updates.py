from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from pi_beacon.updates import (
    check_for_update,
    download_installer,
    read_update_status,
    run_installer,
    write_update_status,
)


class ReleaseHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/latest":
            payload = json.dumps(
                {
                    "tag_name": "v1.2.0",
                    "html_url": "https://example.test/releases/v1.2.0",
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path == "/v1.2.0/install.sh":
            payload = b'#!/bin/sh\nprintf "%s\\n" "$@" > "$UPDATE_MARKER"\n'
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_error(404)

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def release_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ReleaseHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = server.server_address[0]
    port = server.server_address[1]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_check_for_update_and_cache_round_trip(tmp_path: Path, release_server: str) -> None:
    status = check_for_update("1.0.0", latest_url=f"{release_server}/latest")

    assert status.current_version == "1.0.0"
    assert status.latest_version == "1.2.0"
    assert status.available is True
    assert status.release_url == "https://example.test/releases/v1.2.0"
    assert status.checked_at

    cache = tmp_path / "update.json"
    write_update_status(status, cache)
    cached = read_update_status(cache, current_version="1.2.0")

    assert cached.current_version == "1.2.0"
    assert cached.latest_version == "1.2.0"
    assert cached.available is False
    assert cache.stat().st_mode & 0o777 == 0o600


def test_downloaded_installer_runs_the_exact_release(
    tmp_path: Path,
    release_server: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = download_installer(
        "v1.2.0",
        tmp_path,
        url_template=f"{release_server}/{{tag}}/install.sh",
    )
    marker = tmp_path / "arguments.txt"
    monkeypatch.setenv("UPDATE_MARKER", str(marker))

    run_installer(installer, "v1.2.0")

    assert installer.read_text().startswith("#!/bin/sh\n")
    assert marker.read_text().splitlines() == ["update", "--yes", "--version", "v1.2.0"]


def test_update_check_rejects_malformed_release_payload(release_server: str) -> None:
    with pytest.raises(RuntimeError, match="release response"):
        check_for_update("1.0.0", latest_url=f"{release_server}/missing")


def test_update_check_rejects_non_http_urls() -> None:
    with pytest.raises(RuntimeError, match="Unsupported release URL"):
        check_for_update("1.0.0", latest_url="file:///etc/passwd")
