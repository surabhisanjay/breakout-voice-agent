from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import app as api_app
from src.agents.escalation_agent import EscalationAgent
from src.agents.sentiment_agent import SentimentResult
from src.core.agent_response import AgentResponse
from src.memory.conversation_memory import ConversationMemory


def neutral() -> SentimentResult:
    return SentimentResult("neutral", 0.55, False, "", "discovery")


def response(text: str, *, booking: dict | None = None) -> AgentResponse:
    return AgentResponse(
        response=text,
        intent="general_faq",
        next_agent="inbound_agent",
        should_handoff=False,
        booking=booking,
    )


def test_human_request_persists_complete_escalation_record(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "human.json")
    memory.data.update({
        "call_id": "call-123",
        "customer_name": "Riya Patel",
        "phone": "9876543210",
        "intent": "escape_room_inquiry",
        "booking_id": "BK-42",
        "payment_status": "pending",
    })
    memory.add_turn("customer", "I want to speak to an agent.")

    result = EscalationAgent(memory).evaluate("I want to speak to an agent.", neutral())

    assert result.escalate is True
    assert result.category == "human_request"
    assert result.priority == "high"
    assert result.transfer_required is True
    assert result.support_ticket_id.startswith("ESC-")
    record = memory.data["escalation_requests"][-1]
    assert record["handoff"]["call_id"] == "call-123"
    assert record["handoff"]["phone"] == "9876543210"
    assert record["handoff"]["booking_id"] == "BK-42"
    assert record["handoff"]["payment_status"] == "pending"
    assert record["handoff"]["transcript"]


def test_unresolved_query_is_saved_and_escalated(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "unknown.json")
    result = EscalationAgent(memory).evaluate(
        "Can you arrange a helicopter pickup?",
        neutral(),
        response("I'm not sure about that. Our team would be the best people to help."),
    )

    assert result.reason == "Agent could not answer the customer query"
    assert memory.data["unresolved_queries"][-1]["query"] == "Can you arrange a helicopter pickup?"
    assert memory.data["support_tickets"][-1]["category"] == "knowledge_gap"


def test_repeated_customer_question_escalates_after_three_attempts(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "repeat.json")
    for _ in range(3):
        memory.add_turn("customer", "What is the exact price?")
        memory.add_turn("agent", "The team can confirm the amount.")

    result = EscalationAgent(memory).evaluate("What is the exact price?", neutral())

    assert result.reason == "Repeated conversation loop detected"
    assert result.recommended_action


def test_payment_completed_without_booking_confirmation_escalates(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "payment.json")
    memory.data.update({"payment_status": "successful", "booking_id": "", "booking_ref": ""})

    result = EscalationAgent(memory).evaluate(
        "My payment completed but the booking is not confirmed.", neutral()
    )

    assert result.category == "payment"
    assert result.priority == "high"
    assert "reconcile" in result.recommended_action


def test_multiple_authentication_failures_stop_sensitive_operations(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "auth.json")
    agent = EscalationAgent(memory)

    first = agent.evaluate("The booking details don't match.", neutral())
    second = agent.evaluate("Verification failed again.", neutral())

    assert first.escalate is False
    assert second.reason == "Multiple failed authentication attempts"
    assert second.category == "authentication"
    assert memory.data["authentication_failure_count"] == 2


def test_safety_escalation_is_critical(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "safety.json")
    result = EscalationAgent(memory).evaluate("Someone is hurt and bleeding.", neutral())

    assert result.category == "safety"
    assert result.priority == "critical"
    assert result.transfer_required is True


def test_booking_api_failure_creates_booking_support_ticket(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "booking-failure.json")
    result = EscalationAgent(memory).evaluate(
        "Please book that slot.",
        neutral(),
        response("The booking failed because the API is unavailable.", booking={"status": "failed"}),
    )

    assert result.category == "booking_failure"
    assert memory.data["support_tickets"][-1]["status"] == "open"


def test_vapi_response_exposes_transfer_and_handoff_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("HUMAN_TRANSFER_DESTINATION", raising=False)
    monkeypatch.setattr(api_app, "API_MEMORY_DIR", tmp_path / "api_sessions")
    api_app._sessions.clear()
    client = TestClient(api_app.app)

    result = client.post(
        "/vapi/tool",
        json={"message": "Transfer me to a representative.", "session_id": "call-transfer-1"},
    )

    assert result.status_code == 200
    body = result.json()
    assert body["escalation"]["escalate"] is True
    assert body["transfer"]["required"] is True
    assert body["transfer"]["status"] == "pending_configuration"
    assert body["handoff_summary"]["call_id"] == "call-transfer-1"
    assert body["handoff_summary"]["transcript"]
    assert body["handoff_summary"]["transcript"][-1]["content"] == body["response"]
