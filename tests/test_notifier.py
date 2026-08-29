from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from pi_beacon.config import NotificationSettings
from pi_beacon.notifier import (
    NotificationEvent,
    default_sound_path,
    event_enabled,
    in_quiet_hours,
    notification_copy,
    notify,
    parse_clock,
    play_sound,
)


def test_notification_copy() -> None:
    title, body, urgency = notification_copy(NotificationEvent.SETTLED, "dotfiles", "")
    assert title == "dotfiles is ready"
    assert "next instruction" in body
    assert urgency == "normal"


def test_quiet_hours_can_cross_midnight() -> None:
    settings = NotificationSettings(quiet_hours_start="22:00", quiet_hours_end="08:00")
    assert in_quiet_hours(settings, datetime.fromisoformat("2026-08-29T23:00:00"))
    assert in_quiet_hours(settings, datetime.fromisoformat("2026-08-29T07:00:00"))
    assert not in_quiet_hours(settings, datetime.fromisoformat("2026-08-29T12:00:00"))


def test_short_settled_run_is_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pi_beacon.notifier.shutil.which", lambda _name: "/bin/true")
    settings = NotificationSettings(minimum_duration_seconds=10)
    assert not notify(settings, NotificationEvent.SETTLED, "demo", duration_seconds=2)


def test_notification_and_optional_sound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    sound_calls: list[NotificationSettings] = []

    def fake_run(arguments, **_kwargs):
        calls.append(arguments)

    sound = tmp_path / "complete.wav"
    sound.write_bytes(b"RIFF")
    monkeypatch.setattr("pi_beacon.notifier.shutil.which", lambda _name: "/usr/bin/notify-send")
    monkeypatch.setattr("pi_beacon.notifier.subprocess.run", fake_run)
    monkeypatch.setattr(
        "pi_beacon.notifier.play_sound", lambda settings: sound_calls.append(settings)
    )
    settings = NotificationSettings(sound=True, sound_file=sound)

    assert notify(settings, NotificationEvent.ERROR, "backend")
    assert calls[0][-2:] == [
        "backend failed",
        "Pi stopped with an error. Open the session for details.",
    ]
    assert sound_calls == [settings]


def test_notification_configuration_helpers() -> None:
    settings = NotificationSettings(waiting=False)
    assert parse_clock("invalid") is None
    assert not event_enabled(settings, NotificationEvent.WAITING)
    assert event_enabled(settings, NotificationEvent.ERROR)
    assert default_sound_path().is_file()


def test_sound_player_uses_first_available_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sound = tmp_path / "complete.wav"
    sound.write_bytes(b"RIFF")
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "pi_beacon.notifier.shutil.which",
        lambda name: "/usr/bin/pw-play" if name == "pw-play" else None,
    )
    monkeypatch.setattr(
        "pi_beacon.notifier.subprocess.Popen",
        lambda arguments, **_kwargs: calls.append(arguments),
    )
    play_sound(NotificationSettings(sound_file=sound))
    assert calls == [["/usr/bin/pw-play", str(sound)]]


def test_notification_requires_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pi_beacon.notifier.shutil.which", lambda _name: None)
    assert not notify(NotificationSettings(), NotificationEvent.ERROR, "demo")
