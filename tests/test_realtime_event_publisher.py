from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import app as api_app  # noqa: E402
from src.core.agent_response import AgentResponse  # noqa: E402
from src.services.realtime.configuration import RealtimeConfig  # noqa: E402
from src.services.realtime.event_models import SUPPORTED_REALTIME_EVENTS, RealtimeEventEnvelope  # noqa: E402
from src.services.realtime.publisher import RealtimeEventPublisher, transcript_chunk_from_turn  # noqa: E402
from src.services.realtime.redis import RedisEventTransport  # noqa: E402
from src.services.phase1_state import PHASE1_BUSINESS_STATE_FIELDS  # noqa: E402
from src.services.wati_client import WatiSendResult  # noqa: E402


class FakeRedis:
    def __init__(self, outcomes: list[Any] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.calls: list[tuple[str, str]] = []
        self.close_count = 0

    def publish(self, channel: str, message: str) -> int:
        self.calls.append((channel, message))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return int(outcome)
        return 1

    def close(self) -> None:
        self.close_count += 1


class FakeRealtimePublisher:
    def __init__(self, *, raise_on_publish: bool = False) -> None:
        self.raise_on_publish = raise_on_publish
        self.events: list[dict[str, Any]] = []

    def publish(self, event: str, call_id: str, payload: dict[str, Any]):
        if self.raise_on_publish:
            raise RuntimeError("redis down")
        self.events.append({"event": event, "call_id": call_id, "payload": payload})
        return SimpleNamespace(
            attempted=True,
            sent=True,
            channel=f"live_call:{call_id}",
            subscriber_count=1,
            latency_ms=1.0,
            retry_count=0,
            reason="",
        )


def make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, publisher: FakeRealtimePublisher) -> TestClient:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("BREAKOUT_GPT_REASONER", "false")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("BOOKING_PROVIDER", "simulator")
    monkeypatch.setattr(api_app, "API_MEMORY_DIR", tmp_path / "api_sessions")
    monkeypatch.setattr(api_app, "get_default_realtime_publisher", lambda: publisher)
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
            "price_breakdown": {"base_price": 3000, "discount": 0, "final_price": 3000, "currency": "INR"},
        }
    )
    inbound.memory.add_turn("customer", message)
    inbound.memory.add_turn("agent", "I've reserved your slot.")
    booking_result = {
        "booking_id": "bk_test",
        "booking_ref": "or_test",
        "status": "RESERVED",
        "payment_url": "https://pay.example/order?pr=true",
        "payment_status": "UNPAID",
        "room": "Murder Mystery",
        "location": "Whitefield",
        "date": "Tomorrow",
        "time": "7:00 PM",
        "participants": 4,
        "price_breakdown": {"base_price": 3000, "discount": 0, "final_price": 3000, "currency": "INR"},
    }
    return AgentResponse(
        response="I've reserved your slot.",
        intent="escape_room_inquiry",
        next_agent="booking_agent",
        should_handoff=False,
        state=dict(inbound.memory.data),
        recommendation={"option": "Murder Mystery", "reason": "Best fit for first-time groups."},
        booking_result=booking_result,
        sentiment_analysis={"sentiment": "positive", "confidence": 0.81},
        ai_summary={"summary": "Customer completed a booking."},
        timeline_events=[{"time": "00:30", "event": "Booking Confirmed"}],
        transcript=[
            {"sequence": 1, "speaker_type": "customer", "text": message, "spoken_at_second": 1.0},
            {"sequence": 2, "speaker_type": "agent", "text": "I've reserved your slot.", "spoken_at_second": 2.0},
        ],
    )


def test_standard_envelope_supports_required_events() -> None:
    assert {"transcript.chunk", "booking.updated", "summary.ready", "call.ended"}.issubset(SUPPORTED_REALTIME_EVENTS)

    envelope = RealtimeEventEnvelope(
        event="transcript.chunk",
        call_id="abc123",
        timestamp="2026-07-04T10:00:00+00:00",
        payload={"speaker": "customer", "text": "I need a room"},
    )

    assert envelope.to_dict() == {
        "version": 1,
        "event": "transcript.chunk",
        "call_id": "abc123",
        "timestamp": "2026-07-04T10:00:00+00:00",
        "payload": {"speaker": "customer", "text": "I need a room"},
    }


def test_redis_publish_uses_single_live_call_channel_and_schema() -> None:
    redis = FakeRedis()
    publisher = RealtimeEventPublisher(
        RedisEventTransport(
            RealtimeConfig(redis_url="redis://localhost:6379/0", enabled=True),
            client=redis,
        )
    )

    result = publisher.publish("booking.updated", "abc123", {"booking_id": "bk_test"})

    assert result.sent is True
    assert redis.calls[0][0] == "live_call:abc123"
    body = json.loads(redis.calls[0][1])
    assert body["version"] == 1
    assert body["event"] == "booking.updated"
    assert body["call_id"] == "abc123"
    assert body["payload"]["booking_id"] == "bk_test"


def test_redis_transport_reconnects_and_retries_transient_failure() -> None:
    redis = FakeRedis([ConnectionError("down"), 2])
    transport = RedisEventTransport(
        RealtimeConfig(redis_url="redis://localhost:6379/0", enabled=True, retry_attempts=1),
        client=redis,
    )

    result = transport.publish("live_call:abc123", '{"ok":true}')

    assert result.sent is True
    assert result.retry_count == 1
    assert len(redis.calls) == 2
    assert redis.close_count == 1


def test_redis_unavailable_returns_failure_without_raising() -> None:
    redis = FakeRedis([TimeoutError("slow"), TimeoutError("slow")])
    transport = RedisEventTransport(
        RealtimeConfig(redis_url="redis://localhost:6379/0", enabled=True, retry_attempts=1),
        client=redis,
    )

    result = transport.publish("live_call:abc123", '{"ok":true}')

    assert result.sent is False
    assert result.attempted is True
    assert result.reason == "TimeoutError"


def test_turn_mapping_reuses_conversation_memory_shape() -> None:
    chunk = transcript_chunk_from_turn({"role": "agent", "content": "Sure, how many people?"})

    assert chunk is not None
    assert chunk.speaker == "assistant"
    assert chunk.text == "Sure, how many people?"
    assert chunk.event_type == "final"


def test_chat_publishes_ordered_realtime_events_and_keeps_frontend_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    publisher = FakeRealtimePublisher()
    client = make_client(tmp_path, monkeypatch, publisher)

    monkeypatch.setattr(api_app, "dispatch", lambda message, inbound, booking, active_agent: (_booking_result(inbound, message), booking, active_agent))

    response = client.post("/chat", json={"session_id": "redis-chat", "message": "book"})

    assert response.status_code == 200
    body = response.json()
    assert body["booking"]["booking_id"] == "bk_test"
    for key in {"booking", "payment", "conversation_history", "sentiment", "csat"}:
        assert key in body
    events = [item["event"] for item in publisher.events]
    assert events[:5] == ["call.started", "agent.state", "call.updated", "transcript.chunk", "transcript.chunk"]
    assert "sentiment.updated" in events
    assert "timeline.event" in events
    assert "booking.created" in events
    assert "booking.updated" in events
    assert events[-2:] == ["summary.ready", "call.ended"]
    assert {item["call_id"] for item in publisher.events} == {"redis-chat"}
    for item in publisher.events:
        assert set(PHASE1_BUSINESS_STATE_FIELDS).issubset(item["payload"])
        assert item["payload"]["business_state"]["customer_intent"] == "escape_room_inquiry"
    chunks = [item["payload"] for item in publisher.events if item["event"] == "transcript.chunk"]
    assert [(item["speaker"], item["text"]) for item in chunks] == [
        ("customer", "book"),
        ("assistant", "I've reserved your slot."),
    ]


def test_realtime_failure_does_not_fail_booking_or_conversation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    publisher = FakeRealtimePublisher(raise_on_publish=True)
    client = make_client(tmp_path, monkeypatch, publisher)
    monkeypatch.setattr(api_app, "dispatch", lambda message, inbound, booking, active_agent: (_booking_result(inbound, message), booking, active_agent))

    response = client.post("/chat", json={"session_id": "redis-failure-safe", "message": "book"})

    assert response.status_code == 200
    assert response.json()["booking"]["booking_id"] == "bk_test"
    assert response.json()["payment"]["payment_url"] == "https://pay.example/order?pr=true"


def test_negative_sentiment_publishes_high_priority_escalation_business_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = FakeRealtimePublisher()
    client = make_client(tmp_path, monkeypatch, publisher)

    response = client.post(
        "/chat",
        json={"session_id": "negative-phase1", "message": "This service is terrible."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["next_agent"] == "escalation_agent"
    assert body["escalation"]["priority"] == "high"
    escalation_event = next(item for item in publisher.events if item["event"] == "escalation.created")
    state = escalation_event["payload"]["business_state"]
    assert state["negative_sentiment"] is True
    assert state["follow_up_eligible"] is False
    assert state["priority"] == "high"
    escalation_payload = escalation_event["payload"]["escalation_payload"]
    assert escalation_payload["escalation_reason"] == "Customer anger detected"
    assert escalation_payload["confidence_score"] > 0.0


def test_wati_inbound_publishes_transcript_chunks_to_realtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    publisher = FakeRealtimePublisher()
    client = make_client(tmp_path, monkeypatch, publisher)

    def fake_dispatch(message, inbound, booking, active_agent):
        inbound.memory.add_turn("customer", message)
        inbound.memory.add_turn("agent", "Yes, parking is available.")
        result = AgentResponse(
            response="Yes, parking is available.",
            intent="general_faq",
            next_agent="inbound_agent",
            should_handoff=False,
            state=dict(inbound.memory.data),
            transcript=[
                {"role": "customer", "text": message},
                {"role": "agent", "text": "Yes, parking is available."},
            ],
        )
        return result, booking, active_agent

    monkeypatch.setattr(api_app, "dispatch", fake_dispatch)
    monkeypatch.setattr(
        "src.services.wati_client.WatiClient.send_session_message",
        lambda self, phone, message_text, *, context=None: WatiSendResult(
            attempted=False,
            sent=False,
            reason="missing_configuration",
        ),
    )

    response = client.post("/webhooks/wati", json={"waId": "9982151357", "text": "Is parking available?"})

    assert response.status_code == 200
    chunks = [item for item in publisher.events if item["event"] == "transcript.chunk"]
    assert [(item["call_id"], item["payload"]["speaker"], item["payload"]["text"]) for item in chunks] == [
        ("whatsapp:919982151357", "customer", "Is parking available?"),
        ("whatsapp:919982151357", "assistant", "Yes, parking is available."),
    ]


def test_wati_negative_sentiment_publishes_escalation_state_without_scheduler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = FakeRealtimePublisher()
    client = make_client(tmp_path, monkeypatch, publisher)
    monkeypatch.setattr(
        "src.services.wati_client.WatiClient.send_session_message",
        lambda self, phone, message_text, *, context=None: WatiSendResult(
            attempted=False,
            sent=False,
            reason="missing_configuration",
        ),
    )

    response = client.post(
        "/webhooks/wati",
        json={"waId": "9982151357", "text": "This service is terrible."},
    )

    assert response.status_code == 200
    escalation_event = next(item for item in publisher.events if item["event"] == "escalation.created")
    assert escalation_event["payload"]["escalation_payload"]["preferred_contact_method"] == "whatsapp"
    assert escalation_event["payload"]["negative_sentiment"] is True
    assert escalation_event["payload"]["follow_up_eligible"] is False


def test_backend_no_longer_exposes_transcript_sse_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(tmp_path, monkeypatch, FakeRealtimePublisher())

    openapi = client.get("/openapi.json").json()

    assert "/transcript/stream/{session_id}" not in openapi["paths"]
    assert client.get("/transcript/stream/s1").status_code == 404
