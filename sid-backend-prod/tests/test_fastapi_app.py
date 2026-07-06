from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import app as api_app  # noqa: E402
from src.services.wati_client import WatiSendResult  # noqa: E402


def make_client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("BREAKOUT_GPT_REASONER", "false")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setattr(api_app, "API_MEMORY_DIR", tmp_path / "api_sessions")
    api_app._sessions.clear()
    return TestClient(api_app.app)


def test_health_endpoint(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "booking_provider": "simulator",
        "version": api_app.API_VERSION,
    }


def test_debug_endpoint_echoes_arbitrary_json(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    payload = {"message": {"type": "tool-calls"}, "items": [1, None, True]}

    response = client.post("/debug", json=payload)

    assert response.status_code == 200
    assert response.json() == {"received": payload}


def test_chat_accepts_exact_valid_payload(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    response = client.post("/chat", json={"session_id": "test", "message": "hello"})

    assert response.status_code == 200
    assert isinstance(response.json()["response"], str)


def test_chat_rejects_missing_session_id(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    response = client.post("/chat", json={"message": "hello"})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "session_id"]
    assert response.json()["detail"][0]["type"] == "missing"


def test_chat_rejects_missing_message(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    response = client.post("/chat", json={"session_id": "test"})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "message"]
    assert response.json()["detail"][0]["type"] == "missing"


def test_chat_rejects_null_values(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    response = client.post("/chat", json={"session_id": None, "message": None})

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert [(error["loc"], error["type"]) for error in errors] == [
        (["body", "session_id"], "string_type"),
        (["body", "message"], "string_type"),
    ]


def test_chat_ignores_unexpected_fields(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/chat",
        json={"session_id": "test", "message": "hello", "unexpected": "value"},
    )

    assert response.status_code == 200
    assert isinstance(response.json()["response"], str)


def test_chat_logs_raw_validation_parsed_and_response(tmp_path: Path, monkeypatch, caplog) -> None:
    client = make_client(tmp_path, monkeypatch)
    caplog.set_level("INFO", logger=api_app.__name__)

    invalid = client.post("/chat", json={"message": "hello"})
    valid = client.post("/chat", json={"session_id": "logging-test", "message": "hello"})

    assert invalid.status_code == 422
    assert valid.status_code == 200
    messages = [record.getMessage() for record in caplog.records]
    assert any(message.startswith("RAW_REQUEST=") for message in messages)
    assert any(message.startswith("VALIDATION_ERROR=") for message in messages)
    assert any(message.startswith("PARSED_REQUEST=") for message in messages)
    assert any(message.startswith("CHAT_RESPONSE=") for message in messages)


def test_chat_persists_memory_for_session(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    first = client.post("/chat", json={"session_id": "alpha", "message": "We are six adults."})
    memory = client.get("/memory/alpha")

    assert first.status_code == 200
    assert isinstance(first.json()["response"], str)
    assert memory.status_code == 200
    assert memory.json()["memory"]["participants"] == 6
    assert memory.json()["memory"]["age_group"] == "adults"


def test_multiple_sessions_are_independent(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    client.post("/chat", json={"session_id": "alpha", "message": "We are six adults."})
    client.post("/chat", json={"session_id": "beta", "message": "We are four kids."})

    alpha = client.get("/memory/alpha").json()["memory"]
    beta = client.get("/memory/beta").json()["memory"]

    assert alpha["participants"] == 6
    assert alpha["age_group"] == "adults"
    assert beta["participants"] == 4
    assert beta["age_group"] == "kids"


def test_reset_clears_session_memory_and_runtime(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    client.post("/chat", json={"session_id": "alpha", "message": "We are six adults."})
    reset = client.post("/reset", json={"session_id": "alpha"})
    memory = client.get("/memory/alpha").json()["memory"]

    assert reset.status_code == 200
    assert reset.json() == {"success": True}
    assert memory["participants"] == ""
    assert memory["age_group"] == ""


def test_invalid_session_id_is_rejected(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    response = client.post("/chat", json={"session_id": "../bad", "message": "Hi"})

    assert response.status_code == 400


def test_wati_webhook_routes_every_message_through_dispatch_and_replies(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    sent: list[dict[str, str]] = []

    def fake_send(self, phone: str, message_text: str, *, context=None):
        sent.append({"phone": phone, "message": message_text, "session_id": context["session_id"]})
        return WatiSendResult(
            attempted=True,
            sent=True,
            status_code=200,
            response={"result": True, "messageId": "wamid-test"},
            message_id="wamid-test",
        )

    monkeypatch.setattr("src.services.wati_client.WatiClient.send_session_message", fake_send)

    response = client.post(
        "/webhooks/wati",
        json={"waId": "9982151357", "text": "We are a couple, first escape room."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["session_id"] == "whatsapp:919982151357"
    assert body["wati"]["sent"] is True
    assert body["wati"]["message_id"] == "wamid-test"
    assert sent and sent[0]["session_id"] == "whatsapp:919982151357"

    memory = client.get("/memory/whatsapp:919982151357").json()["memory"]
    assert memory["channel"] == "whatsapp"
    assert memory["relationship"] == "couple"
    assert memory["participants"] == 2
    assert memory["experience_level"] == "beginner"
    assert memory["last_whatsapp_reply"]["delivery"]["sent"] is True


def test_wati_webhook_uses_phone_number_as_conversation_identity(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "src.services.wati_client.WatiClient.send_session_message",
        lambda self, phone, message_text, *, context=None: WatiSendResult(
            attempted=True,
            sent=True,
            status_code=200,
            response={"result": True},
        ),
    )

    first = client.post("/api/v1/whatsapp/wati/webhook", json={"whatsappNumber": "9845012367", "text": "Whitefield"})
    second = client.post("/api/v1/whatsapp/wati/webhook", json={"whatsappNumber": "9845012367", "text": "Tomorrow evening"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["session_id"] == second.json()["session_id"] == "whatsapp:919845012367"
    memory = client.get("/memory/whatsapp:919845012367").json()["memory"]
    assert memory["location"] == "Whitefield"
    assert memory["preferred_period"] == "evening"


def test_wati_webhook_does_not_fail_conversation_when_reply_delivery_fails(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "src.services.wati_client.WatiClient.send_session_message",
        lambda self, phone, message_text, *, context=None: WatiSendResult(
            attempted=True,
            sent=False,
            status_code=503,
            reason="http_503: unavailable",
            response={"result": False},
        ),
    )

    response = client.post("/webhooks/wati", json={"waId": "9982151357", "text": "Hi, is this Breakout?"})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["wati"]["attempted"] is True
    assert body["wati"]["sent"] is False
    assert body["wati"]["status_code"] == 503
    assert "response" in body and body["response"]


def test_wati_status_callbacks_are_ignored_without_dispatch(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    def fail_send(*args, **kwargs):
        raise AssertionError("status callbacks must not send replies")

    monkeypatch.setattr("src.services.wati_client.WatiClient.send_session_message", fail_send)

    response = client.post("/webhooks/wati", json={"eventType": "message_status", "waId": "9982151357"})

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "ignored": True,
        "session_id": "",
        "response": "",
        "next_agent": "inbound_agent",
        "wati": {},
    }
