from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, time
from enum import StrEnum
from importlib.resources import files
from pathlib import Path

from pi_beacon.config import NotificationSettings


class NotificationEvent(StrEnum):
    SETTLED = "settled"
    WAITING = "waiting"
    ERROR = "error"


def parse_clock(value: str | None) -> time | None:
    if not value:
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def in_quiet_hours(settings: NotificationSettings, now: datetime | None = None) -> bool:
    start = parse_clock(settings.quiet_hours_start)
    end = parse_clock(settings.quiet_hours_end)
    if start is None or end is None:
        return False
    current = (now or datetime.now().astimezone()).time().replace(tzinfo=None)
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def event_enabled(settings: NotificationSettings, event: NotificationEvent) -> bool:
    return {
        NotificationEvent.SETTLED: settings.settled,
        NotificationEvent.WAITING: settings.waiting,
        NotificationEvent.ERROR: settings.errors,
    }[event]


def notification_copy(event: NotificationEvent, project: str, detail: str) -> tuple[str, str, str]:
    name = project or "Pi session"
    if event == NotificationEvent.SETTLED:
        return (
            f"{name} is ready",
            detail or "Pi finished and is waiting for your next instruction.",
            "normal",
        )
    if event == NotificationEvent.WAITING:
        return f"{name} needs input", detail or "Pi is waiting for a decision.", "normal"
    return (
        f"{name} failed",
        detail or "Pi stopped with an error. Open the session for details.",
        "critical",
    )


def default_sound_path() -> Path:
    return Path(str(files("pi_beacon").joinpath("assets", "sounds", "complete.wav")))


def play_sound(settings: NotificationSettings) -> None:
    sound = settings.sound_file.expanduser() if settings.sound_file else default_sound_path()
    if not sound.is_file():
        return
    players = (
        ("pw-play", [str(sound)]),
        ("paplay", [str(sound)]),
        ("canberra-gtk-play", ["--file", str(sound)]),
    )
    for executable, arguments in players:
        command = shutil.which(executable)
        if command is None:
            continue
        subprocess.Popen(
            [command, *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return


def notify(
    settings: NotificationSettings,
    event: NotificationEvent,
    project: str,
    detail: str = "",
    duration_seconds: float = 0,
) -> bool:
    if not settings.enabled or not event_enabled(settings, event):
        return False
    if event == NotificationEvent.SETTLED and duration_seconds < settings.minimum_duration_seconds:
        return False
    if in_quiet_hours(settings):
        return False
    command = shutil.which("notify-send")
    if command is None:
        return False

    title, body, urgency = notification_copy(event, project, detail)
    environment = os.environ.copy()
    environment.setdefault("XDG_ACTIVATION_TOKEN", "")
    subprocess.run(
        [
            command,
            "--app-name",
            "Pi Beacon",
            "--urgency",
            urgency,
            "--icon",
            "utilities-terminal",
            title,
            body,
        ],
        check=False,
        timeout=5,
        env=environment,
    )
    if settings.sound and event in {NotificationEvent.SETTLED, NotificationEvent.ERROR}:
        play_sound(settings)
    return True
