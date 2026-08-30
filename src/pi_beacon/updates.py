from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from platformdirs import user_cache_path

from pi_beacon.models import UpdateStatus

LATEST_RELEASE_URL = "https://api.github.com/repos/andres-ortizl/pi-beacon/releases/latest"
INSTALLER_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/andres-ortizl/pi-beacon/{tag}/install.sh"
)
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
MAX_RESPONSE_BYTES = 1024 * 1024


def installed_version() -> str:
    return version("pi-beacon")


def update_cache_path() -> Path:
    return user_cache_path("pi-beacon") / "update.json"


def release_version(value: str) -> str:
    match = VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        raise RuntimeError(f"Unsupported release version: {value}")
    return ".".join(match.groups())


def version_key(value: str) -> tuple[int, int, int]:
    major, minor, patch = release_version(value).split(".")
    return int(major), int(minor), int(patch)


def validate_release_url(url: str) -> None:
    parsed = urlsplit(url)
    loopback_http = parsed.scheme == "http" and parsed.hostname in {
        "127.0.0.1",
        "::1",
        "localhost",
    }
    if not parsed.netloc or parsed.username or parsed.password:
        raise RuntimeError(f"Unsupported release URL: {url}")
    if parsed.scheme != "https" and not loopback_http:
        raise RuntimeError(f"Unsupported release URL: {url}")


def request_bytes(url: str) -> bytes:
    validate_release_url(url)
    request = Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "pi-beacon"}
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, OSError) as error:
        raise RuntimeError(f"Could not read release response: {error}") from error
    if len(payload) > MAX_RESPONSE_BYTES:
        raise RuntimeError("Release response is too large")
    return payload


def check_for_update(
    current_version: str | None = None,
    *,
    latest_url: str = LATEST_RELEASE_URL,
) -> UpdateStatus:
    current = release_version(current_version or installed_version())
    try:
        payload = json.loads(request_bytes(latest_url))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("Invalid release response") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Invalid release response")
    tag = payload.get("tag_name")
    release_url = payload.get("html_url")
    if not isinstance(tag, str) or not isinstance(release_url, str):
        raise RuntimeError("Invalid release response")
    latest = release_version(tag)
    return UpdateStatus(
        current_version=current,
        latest_version=latest,
        available=version_key(latest) > version_key(current),
        release_url=release_url,
        checked_at=datetime.now(UTC).isoformat(),
    )


def write_update_status(status: UpdateStatus, path: Path | None = None) -> Path:
    destination = path or update_cache_path()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf8") as cache_file:
            cache_file.write(status.model_dump_json(by_alias=True))
            cache_file.write("\n")
        os.chmod(temporary, 0o600)
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def read_update_status(
    path: Path | None = None,
    *,
    current_version: str | None = None,
) -> UpdateStatus:
    current = release_version(current_version or installed_version())
    source = path or update_cache_path()
    try:
        cached = UpdateStatus.model_validate_json(source.read_text(encoding="utf8"))
        latest = release_version(cached.latest_version)
    except (OSError, ValueError, RuntimeError):
        return UpdateStatus(current_version=current)
    return cached.model_copy(
        update={
            "current_version": current,
            "latest_version": latest,
            "available": version_key(latest) > version_key(current),
        }
    )


def download_installer(
    tag: str,
    directory: Path,
    *,
    url_template: str = INSTALLER_URL_TEMPLATE,
) -> Path:
    release_tag = f"v{release_version(tag)}"
    payload = request_bytes(url_template.format(tag=release_tag))
    if not payload.startswith(b"#!/bin/sh\n"):
        raise RuntimeError("Downloaded installer is not a POSIX shell script")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = directory / f"pi-beacon-{release_tag}-install.sh"
    destination.write_bytes(payload)
    os.chmod(destination, 0o600)
    return destination


def run_installer(installer: Path, tag: str) -> None:
    release_tag = f"v{release_version(tag)}"
    try:
        subprocess.run(
            ["sh", str(installer), "update", "--yes", "--version", release_tag],
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"Pi Beacon update failed: {error}") from error
