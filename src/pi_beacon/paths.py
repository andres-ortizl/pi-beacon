from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from platformdirs import user_cache_path

from pi_beacon.config import Settings


def runtime_root(settings: Settings) -> Path:
    configured = settings.paths.runtime_dir
    if configured is not None:
        return configured.expanduser()
    if runtime_dir := os.environ.get("XDG_RUNTIME_DIR"):
        return Path(runtime_dir) / "pi-beacon"
    return Path(tempfile.gettempdir()) / f"pi-runtime-{os.getuid()}" / "pi-beacon"


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"Private runtime path is a symbolic link: {path}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"Private runtime path is not a directory: {path}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeError(f"Cannot securely open private runtime path: {path}") from error
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_uid != os.getuid():
            raise RuntimeError(f"Private runtime path has a foreign owner: {path}")
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)


def ensure_runtime_root(settings: Settings) -> Path:
    root = runtime_root(settings)
    if settings.paths.runtime_dir is None:
        ensure_private_directory(root.parent)
    ensure_private_directory(root)
    return root


def live_sessions_dir(settings: Settings) -> Path:
    sessions = ensure_runtime_root(settings) / "sessions"
    ensure_private_directory(sessions)
    return sessions


def socket_path(settings: Settings) -> Path:
    configured = settings.paths.socket
    if configured is not None:
        return configured.expanduser()
    return runtime_root(settings) / "api.sock"


def sessions_dir(settings: Settings) -> Path:
    configured = settings.paths.sessions_dir
    if configured is not None:
        return configured.expanduser()
    return Path.home() / ".pi" / "agent" / "sessions"


def database_path(settings: Settings) -> Path:
    configured = settings.paths.database
    if configured is not None:
        return configured.expanduser()
    return user_cache_path("pi-beacon") / "sessions.sqlite3"


def subagent_runs_dir(settings: Settings) -> Path:
    configured = settings.paths.subagent_runs_dir
    if configured is not None:
        return configured.expanduser()
    return Path(tempfile.gettempdir()) / f"pi-subagents-uid-{os.getuid()}" / "async-subagent-runs"
