from __future__ import annotations

from pi_beacon.models import LiveSession, WaybarPayload


def test_live_bridge_accepts_camel_case() -> None:
    session = LiveSession.model_validate(
        {
            "pid": 42,
            "sessionId": "session-1",
            "project": "demo",
            "parentPid": 1,
            "displayName": "Release dashboard",
            "titleSource": "prompt",
            "lastMessageAt": "2026-08-29T19:00:00Z",
            "usage": {"totalTokens": 120, "cacheRead": 100},
            "context": {"tokens": 500, "contextWindow": 1000, "percent": 50},
        }
    )
    assert session.session_id == "session-1"
    assert session.display_name == "Release dashboard"
    assert session.title_source == "prompt"
    assert session.last_message_at == "2026-08-29T19:00:00Z"
    assert session.usage.total_tokens == 120
    assert session.model_dump(by_alias=True)["sessionId"] == "session-1"


def test_waybar_class_alias() -> None:
    payload = WaybarPayload(text="π", css_class="idle", tooltip="ready")
    assert payload.model_dump(by_alias=True)["class"] == "idle"
