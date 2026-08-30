from __future__ import annotations

from pi_beacon.models import DashboardSnapshot, LiveSession, WaybarPayload


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


def test_snapshot_serializes_additive_dashboard_data() -> None:
    snapshot = DashboardSnapshot.model_validate(
        {
            "generatedAt": "2026-08-30T12:00:00Z",
            "runtime": {
                "activity": [
                    {
                        "identity": "pi-session:parent",
                        "kind": "session",
                        "sessionId": "parent",
                        "displayName": "Parent",
                        "children": [
                            {
                                "identity": "agent:child",
                                "kind": "subagent",
                                "parentIdentity": "pi-session:parent",
                                "displayName": "reviewer",
                                "model": "openai/gpt-5.6",
                            }
                        ],
                    }
                ],
                "modelActivity": [
                    {
                        "model": "openai/gpt-5.6",
                        "liveCount": 1,
                        "sessionCount": 1,
                    }
                ],
            },
            "today": {},
            "update": {
                "currentVersion": "1.0.0",
                "latestVersion": "1.1.0",
                "available": True,
                "releaseUrl": "https://example.test/releases/v1.1.0",
            },
            "history": {
                "dailyCost": [{"day": "2026-08-30", "cost": 0.25}],
                "modelUsageToday": [
                    {
                        "model": "openai/gpt-5.6",
                        "cost": 0.25,
                        "tokens": 100,
                        "responses": 1,
                        "sessions": 1,
                    }
                ],
            },
        }
    )

    payload = snapshot.model_dump(by_alias=True)
    assert payload["runtime"]["activity"][0]["children"][0]["parentIdentity"] == "pi-session:parent"
    assert payload["runtime"]["modelActivity"][0]["liveCount"] == 1
    assert payload["history"]["dailyCost"][0]["cost"] == 0.25
    assert payload["update"]["latestVersion"] == "1.1.0"
    assert payload["update"]["available"] is True
