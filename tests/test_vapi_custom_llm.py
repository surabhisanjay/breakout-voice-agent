from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app as api_app


def configure(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(api_app, "API_MEMORY_DIR", tmp_path / "api_sessions")
    api_app._sessions.clear()
    return TestClient(api_app.app)


def test_custom_llm_returns_grounded_location_completion(tmp_path: Path, monkeypatch) -> None:
    client = configure(tmp_path, monkeypatch)

    response = client.post(
        "/vapi/llm/chat/completions",
        json={
            "model": "breakout-inbound-agent",
            "stream": False,
            "messages": [{"role": "user", "content": "What locations do you have?"}],
            "call": {"id": "vapi-llm-location"},
        },
    )

    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"]
    assert content == "Breakout has locations in Koramangala, Whitefield, and JP Nagar."
    assert "Mumbai" not in content
    assert "Pune" not in content


def test_custom_llm_streams_openai_compatible_sse(tmp_path: Path, monkeypatch) -> None:
    client = configure(tmp_path, monkeypatch)

    response = client.post(
        "/vapi/llm/chat/completions",
        json={
            "model": "breakout-inbound-agent",
            "stream": True,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "What locations do you have?"}]}],
            "metadata": {"call": {"id": "vapi-llm-stream"}},
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "Koramangala, Whitefield, and JP Nagar" in response.text
    assert response.text.rstrip().endswith("data: [DONE]")


def test_custom_llm_uses_call_id_for_conversation_memory(tmp_path: Path, monkeypatch) -> None:
    client = configure(tmp_path, monkeypatch)
    payload = {
        "model": "breakout-inbound-agent",
        "stream": False,
        "call": {"id": "vapi-llm-memory"},
    }

    first = client.post(
        "/vapi/llm/chat/completions",
        json={**payload, "messages": [{"role": "user", "content": "I want to book."}]},
    )
    second = client.post(
        "/vapi/llm/chat/completions",
        json={**payload, "messages": [{"role": "user", "content": "Just an escape room."}]},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert "How many people" in second.json()["choices"][0]["message"]["content"]
    data = json.loads((tmp_path / "api_sessions" / "vapi-llm-memory.json").read_text())
    assert data["call_status_source"] == "tool"
