from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

import app as api_app
from backend.app.main import app as unified_app
from crm_app import get_live_calls, lead_from_file


def configure_paths(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(api_app, "API_MEMORY_DIR", tmp_path / "api_sessions")
    monkeypatch.setattr(api_app, "VAPI_EVENT_LOG_DIR", tmp_path / "vapi_events")
    monkeypatch.delenv("VAPI_WEBHOOK_TOKEN", raising=False)
    monkeypatch.setenv("VAPI_ENVIRONMENT", "staging")
    monkeypatch.setenv("VAPI_REGION", "ap-south")
    api_app._sessions.clear()
    return TestClient(api_app.app)


def status_payload(call_id: str, status: str) -> dict:
    return {
        "message": {
            "type": "status-update",
            "status": status,
            "call": {
                "id": call_id,
                "status": status,
                "assistantId": "assistant-1",
                "phoneNumberId": "phone-1",
                "monitor": {"listenUrl": "wss://listen.example/call", "controlUrl": "https://control.example/call"},
                "customer": {"number": "+919876543210"},
            },
        }
    }


def test_status_update_creates_real_live_call(tmp_path: Path, monkeypatch) -> None:
    client = configure_paths(tmp_path, monkeypatch)

    response = client.post("/vapi/webhook", json=status_payload("call-live-1", "in-progress"))

    assert response.status_code == 200
    data = json.loads((tmp_path / "api_sessions" / "call-live-1.json").read_text())
    assert data["call_status"] == "in-progress"
    assert data["call_status_source"] == "webhook"
    assert data["vapi_assistant_id"] == "assistant-1"
    assert data["vapi_phone_number_id"] == "phone-1"
    assert data["vapi_monitor_listen_url"].startswith("wss://")
    assert data["vapi_monitor_control_url"].startswith("https://")
    assert data["vapi_environment"] == "staging"
    assert data["vapi_region"] == "ap-south"
    assert data["vapi_event_count"] == 1
    live = client.get("/vapi/calls/live").json()
    assert live["count"] == 1
    assert live["calls"][0]["call_id"] == "call-live-1"
    assert live["calls"][0]["listen_websocket_available"] is True


def test_transcript_and_end_report_are_persisted(tmp_path: Path, monkeypatch) -> None:
    client = configure_paths(tmp_path, monkeypatch)
    client.post("/vapi/webhook", json=status_payload("call-lifecycle", "in-progress"))
    transcript = {
        "message": {
            "type": "transcript",
            "role": "user",
            "transcriptType": "final",
            "transcript": "I want to book.",
            "call": {"id": "call-lifecycle"},
        }
    }
    ended = {
        "message": {
            "type": "end-of-call-report",
            "endedReason": "customer-ended-call",
            "call": {"id": "call-lifecycle", "status": "ended"},
        }
    }

    assert client.post("/vapi/webhook", json=transcript).status_code == 200
    assert client.post("/vapi/webhook", json=ended).status_code == 200

    data = json.loads((tmp_path / "api_sessions" / "call-lifecycle.json").read_text())
    assert data["vapi_transcript_events"][-1]["text"] == "I want to book."
    assert data["call_status"] == "ended"
    assert data["call_ended_reason"] == "customer-ended-call"
    assert data["call_ended_at"]
    assert client.get("/vapi/calls/live").json()["count"] == 0


def test_assistant_request_resolves_inbound_assistant(tmp_path: Path, monkeypatch) -> None:
    client = configure_paths(tmp_path, monkeypatch)
    monkeypatch.setenv("VAPI_ASSISTANT_ID", "assistant-inbound")
    payload = {"message": {"type": "assistant-request", "call": {"id": "call-inbound"}}}

    response = client.post("/vapi/webhook", json=payload)

    assert response.status_code == 200
    assert response.json() == {"assistantId": "assistant-inbound"}


def test_webhook_token_rejects_unauthorized_request(tmp_path: Path, monkeypatch) -> None:
    client = configure_paths(tmp_path, monkeypatch)
    monkeypatch.setenv("VAPI_WEBHOOK_TOKEN", "expected-secret")
    payload = status_payload("call-secure", "ringing")

    denied = client.post("/vapi/webhook", json=payload)
    allowed = client.post("/vapi/webhook", json=payload, headers={"x-vapi-secret": "expected-secret"})

    assert denied.status_code == 401
    assert allowed.status_code == 200


def test_crm_live_calls_uses_lifecycle_status_not_unfinished_sessions(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    active_path.write_text(json.dumps({
        "call_id": "active",
        "call_status": "in-progress",
        "call_status_source": "webhook",
        "call_started_at": "2099-01-01T00:00:00+00:00",
        "last_call_activity_at": "2099-01-01T00:00:00+00:00",
        "conversation": [],
    }))
    inactive_path = tmp_path / "inactive.json"
    inactive_path.write_text(json.dumps({
        "call_id": "inactive",
        "call_status": "ended",
        "conversation": [{"role": "customer", "content": "hello"}],
    }))

    live = get_live_calls([lead_from_file(active_path), lead_from_file(inactive_path)])

    assert [call["session_id"] for call in live] == ["active"]


def test_unified_websocket_receives_vapi_call_started(tmp_path: Path, monkeypatch) -> None:
    call_id = f"call-websocket-{uuid4()}"
    monkeypatch.setattr(api_app, "API_MEMORY_DIR", tmp_path / "api_sessions")
    monkeypatch.setattr(api_app, "VAPI_EVENT_LOG_DIR", tmp_path / "vapi_events")
    monkeypatch.delenv("VAPI_WEBHOOK_TOKEN", raising=False)
    api_app._sessions.clear()

    with TestClient(unified_app) as client:
        with client.websocket_connect("/ws") as websocket:
            response = client.post(
                "/vapi/webhook",
                json=status_payload(call_id, "in-progress"),
            )
            event = websocket.receive_json()

    assert response.status_code == 200
    assert event["event_type"] == "call_started"
    assert event["payload"]["session_id"] == call_id
    assert event["payload"]["status"] == "connected"
