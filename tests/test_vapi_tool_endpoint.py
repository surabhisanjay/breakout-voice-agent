from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import app as api_app  # noqa: E402
from src.core.agent_response import AgentResponse  # noqa: E402


def test_vapi_tool_endpoint_handles_tool_calls(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setattr(api_app, "API_MEMORY_DIR", tmp_path / "api_sessions")
    api_app._sessions.clear()
    fake = AgentResponse(
        response="Awesome! We'd love to host you. Is this for an escape room with friends, or are you planning something special like a birthday or team outing?",
        intent="general_faq",
        next_agent="inbound_agent",
        should_handoff=False,
    )
    monkeypatch.setattr(api_app, "_dispatch_chat_message", MagicMock(return_value=fake))

    response = TestClient(api_app.app).post(
        "/vapi/tool",
        json={
            "message": {
                "call": {"id": "call-123"},
                "toolCalls": [
                    {
                        "id": "tool-call-1",
                        "function": {
                            "arguments": {
                                "message": "I want to book",
                                "session_id": "vapi-session",
                            }
                        },
                    }
                ],
            }
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "vapi-session"
    assert body["results"][0]["toolCallId"] == "tool-call-1"
    assert "escape room" in body["results"][0]["result"]
    api_app._dispatch_chat_message.assert_called_once_with("vapi-session", "I want to book")


def test_vapi_tool_endpoint_handles_plain_text_payload(monkeypatch) -> None:
    fake = AgentResponse(
        response="Got it. How many people are joining?",
        intent="escape_room_inquiry",
        next_agent="qualification_agent",
        should_handoff=False,
    )
    monkeypatch.setattr(api_app, "_dispatch_chat_message", MagicMock(return_value=fake))

    response = TestClient(api_app.app).post(
        "/vapi/tool",
        json={"callId": "call-plain", "text": "We are six adults."},
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == "call-plain"
    assert response.json()["response"] == "Got it. How many people are joining?"


def test_vapi_tool_endpoint_handles_simple_schema(monkeypatch) -> None:
    fake = AgentResponse(
        response="Awesome! We'd love to host you. Is this for an escape room with friends, or are you planning something special like a birthday or team outing?",
        intent="general_faq",
        next_agent="inbound_agent",
        should_handoff=False,
    )
    monkeypatch.setattr(api_app, "_dispatch_chat_message", MagicMock(return_value=fake))

    response = TestClient(api_app.app).post(
        "/vapi/tool",
        json={"message": "I want to book", "session_id": "vapi-simple"},
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == "vapi-simple"
    assert "escape room" in response.json()["response"]
    api_app._dispatch_chat_message.assert_called_once_with("vapi-simple", "I want to book")
