from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import app as api_app  # noqa: E402
from src.core.agent_response import AgentResponse  # noqa: E402
from src.integrations.closiro.client import ClosiroClient  # noqa: E402
from src.integrations.closiro.models import ClosiroSyncResult, ClosiroWebhookPayload  # noqa: E402
from src.integrations.closiro.payload_builder import build_call_ended_payload, build_escalation_payload  # noqa: E402
from src.integrations.closiro.signer import sign_payload  # noqa: E402


def make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("BREAKOUT_GPT_REASONER", "false")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("BOOKING_PROVIDER", "simulator")
    monkeypatch.setattr(api_app, "API_MEMORY_DIR", tmp_path / "api_sessions")
    api_app._sessions.clear()
    return TestClient(api_app.app)


def _booking_result(inbound, message: str) -> AgentResponse:
    inbound.memory.data.update(
        {
            "customer_name": "Siddharth Khandelwal",
            "phone": "9982151357",
            "booking_id": "bk_test",
            "booking_ref": "or_test",
            "orderId": "or_test",
            "bookingStatus": "RESERVED",
            "paymentStatus": "UNPAID",
            "paymentUrl": "https://pay.example/order?pr=true",
            "room": "Murder Mystery",
            "location": "Whitefield",
            "preferred_date": "Tomorrow",
            "selected_slot": "7:00 PM",
            "participants": 4,
            "price_breakdown": {
                "base_price": 3000,
                "discount": 0,
                "final_price": 3000,
                "currency": "INR",
            },
        }
    )
    inbound.memory.add_turn("customer", message)
    inbound.memory.add_turn("agent", "I've reserved your slot.")
    return AgentResponse(
        response="I've reserved your slot.",
        intent="escape_room_inquiry",
        next_agent="booking_agent",
        should_handoff=False,
        state=dict(inbound.memory.data),
        recommendation={"option": "Murder Mystery", "reason": "Best fit for first-time groups."},
        ai_summary={"summary": "Customer completed a booking."},
        transcript=[
            {"sequence": 1, "speaker_type": "customer", "text": message, "spoken_at_second": 1.0},
            {"sequence": 2, "speaker_type": "agent", "text": "I've reserved your slot.", "spoken_at_second": 2.0},
        ],
    )


def _escalation_result(inbound, message: str) -> AgentResponse:
    inbound.memory.add_turn("customer", message)
    inbound.memory.add_turn("agent", "I'll connect you with our team.")
    return AgentResponse(
        response="I'll connect you with our team.",
        intent="human_request",
        next_agent="escalation_agent",
        should_handoff=True,
        state=dict(inbound.memory.data),
        escalation={"escalate": True, "reason": "Customer explicitly requested a human", "priority": "medium"},
        handoff_summary={"summary": "Customer requested a human.", "action_items": ["Call customer"]},
        ai_summary={"summary": "Customer requested a human."},
        transcript=[
            {"sequence": 1, "speaker_type": "customer", "text": message, "spoken_at_second": 1.0},
            {"sequence": 2, "speaker_type": "agent", "text": "I'll connect you with our team.", "spoken_at_second": 2.0},
        ],
    )


def test_hmac_signature_generation_uses_exact_body_bytes() -> None:
    body = b'{"session_id":"abc","outcome":"booking_reserved"}'
    secret = "test-secret"

    assert sign_payload(body, secret) == hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_call_ended_payload_contains_frontend_contract_objects() -> None:
    chat_payload = {
        "booking": {"booking_id": "bk_test", "reference": "or_test"},
        "payment": {"payment_url": "https://pay.example/order"},
        "price_breakdown": {"final_price": 3000},
        "conversation_summary": {"conversation_id": "s1", "outcome": "booking_reserved", "summary": "Booked."},
        "handoff_summary": None,
        "evaluation": {"score": 93},
        "metrics": {"turn_count": 4},
        "csat": {"score": 4.7},
        "sentiment": {"sentiment": "positive"},
        "sentiment_graph": [{"turn": 1, "score": 0.75}],
        "learning": {"drop_off_stage": "payment_pending"},
        "follow_up": {"recommendations": ["Send confirmation"]},
        "conversation_history": [{"role": "customer", "text": "Book"}],
        "recommendation": {"option": "Murder Mystery"},
        "media": [],
    }

    payload = build_call_ended_payload("s1", chat_payload)

    assert payload.endpoint == "/api/v1/webhooks/vapi/call-ended"
    assert payload.body["message"]["type"] == "end-of-call-report"
    assert payload.body["booking"]["booking_id"] == "bk_test"
    assert payload.body["payment"]["payment_url"] == "https://pay.example/order"
    assert payload.body["recommendation"]["option"] == "Murder Mystery"
    assert payload.body["evaluation"]["score"] == 93
    assert payload.body["conversation_history"][0]["text"] == "Book"


def test_escalation_payload_uses_tool_call_shape_and_handoff_data() -> None:
    payload = build_escalation_payload(
        "s2",
        {
            "conversation_summary": {"conversation_id": "s2", "intent": "human_request", "summary": "Needs a human."},
            "escalation": {"reason": "Customer requested human", "priority": "high"},
            "handoff_summary": {"summary": "Needs a human", "customer_name": "Krati", "phone": "9982151357"},
            "conversation_history": [{"role": "customer", "text": "Human please"}],
            "recording": {"url": "https://recordings.example/call-2.mp3"},
            "booking": {"status": "not_started"},
            "payment": {"status": "not_started"},
            "customer_intent": "human_request",
            "confidence_score": 0.91,
            "preferred_contact_method": "whatsapp",
            "timestamp": "2026-07-05T10:00:00+00:00",
        },
    )

    assert payload.endpoint == "/api/v1/webhooks/vapi/escalation"
    assert payload.body["message"]["type"] == "tool-calls"
    assert payload.body["message"]["toolCalls"][0]["function"]["name"] == "escalate"
    assert payload.body["reason"] == "Customer requested human"
    assert payload.body["handoff_summary"]["summary"] == "Needs a human"
    required = {
        "customer_details",
        "conversation_summary",
        "transcript",
        "call_recording_reference",
        "escalation_reason",
        "priority",
        "customer_intent",
        "confidence_score",
        "booking_status",
        "payment_status",
        "preferred_contact_method",
        "timestamp",
    }
    assert required.issubset(payload.body)
    assert payload.body["customer_details"]["name"] == "Krati"
    assert payload.body["transcript"][0]["text"] == "Human please"
    assert payload.body["call_recording_reference"] == "https://recordings.example/call-2.mp3"
    assert payload.body["customer_intent"] == "human_request"
    assert payload.body["confidence_score"] == 0.91
    assert payload.body["preferred_contact_method"] == "whatsapp"


class _Response:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> Any:
        return self._body


class _Session:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, *, data: bytes, headers: dict[str, str], timeout: float):
        self.calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_client_sends_signed_payload_successfully() -> None:
    session = _Session([_Response(200, {"ok": True})])
    client = ClosiroClient(base_url="https://closiro.example", webhook_secret="secret", session=session)
    payload = ClosiroWebhookPayload("call-ended", "/api/v1/webhooks/vapi/call-ended", {"session_id": "s1"}, "k1")

    result = client.send(payload)

    assert result.sent is True
    assert session.calls[0]["url"] == "https://closiro.example/api/v1/webhooks/vapi/call-ended"
    assert session.calls[0]["headers"]["X-Vapi-Signature"] == sign_payload(session.calls[0]["data"], "secret")
    assert "Authorization" not in session.calls[0]["headers"]


def test_client_retries_transient_failures() -> None:
    session = _Session([_Response(503, {"error": "busy"}), _Response(200, {"ok": True})])
    client = ClosiroClient(base_url="https://closiro.example/api/v1", webhook_secret="secret", session=session)

    result = client.send(ClosiroWebhookPayload("escalation", "/api/v1/webhooks/vapi/escalation", {"session_id": "s1"}, "k1"))

    assert result.sent is True
    assert result.retry_count == 1
    assert len(session.calls) == 2
    assert session.calls[0]["url"] == "https://closiro.example/api/v1/webhooks/vapi/escalation"


def test_timeout_is_reported_without_raising() -> None:
    session = _Session([requests.Timeout("slow"), requests.Timeout("slow"), requests.Timeout("slow")])
    client = ClosiroClient(base_url="https://closiro.example", webhook_secret="secret", session=session, max_retries=2)

    result = client.send(ClosiroWebhookPayload("call-ended", "/api/v1/webhooks/vapi/call-ended", {"session_id": "s1"}, "k1"))

    assert result.sent is False
    assert result.reason == "timeout"
    assert result.retry_count == 2


def test_malformed_payload_is_rejected_before_http() -> None:
    session = _Session([_Response(200, {"ok": True})])
    client = ClosiroClient(base_url="https://closiro.example", webhook_secret="secret", session=session)

    result = client.send(ClosiroWebhookPayload("call-ended", "/api/v1/webhooks/vapi/call-ended", ["bad"], "k1"))  # type: ignore[arg-type]

    assert result.attempted is False
    assert result.reason == "invalid_payload_body"
    assert session.calls == []


def test_booking_webhook_failure_does_not_fail_chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    def fake_dispatch(message, inbound, booking, active_agent):
        return _booking_result(inbound, message), booking, active_agent

    class RaisingClient:
        def send(self, payload):
            raise requests.ConnectionError("down")

    monkeypatch.setattr(api_app, "dispatch", fake_dispatch)
    monkeypatch.setattr(api_app, "get_default_closiro_client", lambda: RaisingClient())

    response = client.post("/chat", json={"session_id": "failure-safe", "message": "book"})

    assert response.status_code == 200
    assert response.json()["booking"]["booking_id"] == "bk_test"
    assert response.json()["payment"]["payment_url"] == "https://pay.example/order?pr=true"


def test_call_ended_sync_is_sent_once_after_booking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    sent: list[ClosiroWebhookPayload] = []

    def fake_dispatch(message, inbound, booking, active_agent):
        return _booking_result(inbound, message), booking, active_agent

    class FakeClosiroClient:
        def send(self, payload):
            sent.append(payload)
            return ClosiroSyncResult(
                attempted=True,
                sent=True,
                status_code=200,
                endpoint=payload.endpoint,
                event_type=payload.event_type,
            )

    monkeypatch.setattr(api_app, "dispatch", fake_dispatch)
    monkeypatch.setattr(api_app, "get_default_closiro_client", lambda: FakeClosiroClient())

    first = client.post("/chat", json={"session_id": "dedupe-booking", "message": "book"})
    second = client.post("/chat", json={"session_id": "dedupe-booking", "message": "thanks"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert [payload.event_type for payload in sent] == ["call-ended"]
    assert sent[0].body["booking"]["booking_id"] == "bk_test"


def test_escalation_sync_is_sent_immediately_when_next_agent_escalates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    sent: list[ClosiroWebhookPayload] = []

    def fake_dispatch(message, inbound, booking, active_agent):
        return _escalation_result(inbound, message), booking, "escalation_agent"

    class FakeClosiroClient:
        def send(self, payload):
            sent.append(payload)
            return ClosiroSyncResult(
                attempted=True,
                sent=True,
                status_code=200,
                endpoint=payload.endpoint,
                event_type=payload.event_type,
            )

    monkeypatch.setattr(api_app, "dispatch", fake_dispatch)
    monkeypatch.setattr(api_app, "get_default_closiro_client", lambda: FakeClosiroClient())

    response = client.post("/chat", json={"session_id": "escalation-sync", "message": "human please"})

    assert response.status_code == 200
    assert [payload.event_type for payload in sent] == ["escalation"]
    assert sent[0].body["reason"] == "Customer explicitly requested a human"
    assert sent[0].body["handoff_summary"]["summary"] == "Customer requested a human."
