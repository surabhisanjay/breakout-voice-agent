from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import app as api_app  # noqa: E402


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
