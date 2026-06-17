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
    assert response.json() == {"status": "ok"}


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
