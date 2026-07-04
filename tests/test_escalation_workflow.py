from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import app as api_app
from src.agents.escalation_agent import EscalationAgent
from src.agents.sentiment_agent import SentimentResult
from src.core.agent_response import AgentResponse
from src.memory.conversation_memory import ConversationMemory
from src.analytics import db as analytics_db


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


def test_repeated_customer_question_is_detected_even_when_agent_answers_change(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "repeat-customer.json")
    memory.add_turn("customer", "What is the exact cancellation policy?")
    memory.add_turn("agent", "It depends on when you cancel.")
    memory.add_turn("customer", "Please tell me, what is the exact cancellation policy?")
    memory.add_turn("agent", "The team can provide the details.")
    memory.add_turn("customer", "Just tell me the exact cancellation policy again.")

    result = EscalationAgent(memory).evaluate(
        "Just tell me the exact cancellation policy again.", neutral()
    )

    assert result.escalate is True
    assert result.category == "conversation_failure"


def test_direct_frustration_escalates_without_sentiment_dependency(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "frustration.json")

    result = EscalationAgent(memory).evaluate("You're not helping me.", neutral())

    assert result.escalate is True
    assert result.category == "frustration"


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
    
    # Pre-populate session with phone number to satisfy stateful escalation requirements
    session_dir = tmp_path / "api_sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    memory = ConversationMemory(session_dir / "call-transfer-1.json")
    memory.data["phone"] = "9876543210"
    memory.save()

    monkeypatch.setattr(api_app, "API_MEMORY_DIR", session_dir)
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


def test_vapi_keeps_escalation_classified_while_collecting_contact_details(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    session_dir = tmp_path / "api_sessions"
    monkeypatch.setattr(analytics_db, "DB_PATH", tmp_path / "analytics.db")
    analytics_db.init_db()
    monkeypatch.setattr(api_app, "API_MEMORY_DIR", session_dir)
    api_app._sessions.clear()

    result = TestClient(api_app.app).post(
        "/vapi/tool",
        json={"message": "I want to speak to a human.", "session_id": "missing-phone"},
    )

    assert result.status_code == 200
    body = result.json()
    assert body["escalation"]["escalate"] is True
    assert body["escalation"]["trigger"] == "human_request"
    assert body["transfer"] == {
        "required": False,
        "status": "awaiting_customer_name",
        "support_ticket_id": body["escalation"]["support_ticket_id"],
    }
    with analytics_db.get_db_connection() as conn:
        escalation = conn.execute(
            "SELECT reason, priority, status, handoff_to FROM escalations WHERE session_id = ?",
            ("missing-phone",),
        ).fetchone()
        call = conn.execute(
            "SELECT status FROM calls WHERE session_id = ?", ("missing-phone",)
        ).fetchone()
        activity_count = conn.execute(
            "SELECT COUNT(*) FROM activities WHERE session_id = ? AND type = 'escalation'",
            ("missing-phone",),
        ).fetchone()[0]

    assert dict(escalation) == {
        "reason": "Customer explicitly requested a human representative",
        "priority": "high",
        "status": "pending",
        "handoff_to": "human_request",
    }
    assert call["status"] == "escalated"
    assert activity_count == 1


def test_vapi_escalation_flow_without_phone_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    session_dir = tmp_path / "split_phone_sessions"
    monkeypatch.setattr(analytics_db, "DB_PATH", tmp_path / "split-phone-analytics.db")
    analytics_db.init_db()
    monkeypatch.setattr(api_app, "API_MEMORY_DIR", session_dir)
    api_app._sessions.clear()
    client = TestClient(api_app.app)
    session_id = "split-spoken-phone"

    first = client.post(
        "/vapi/tool",
        json={"message": "Can you make me talk to a human first?", "session_id": session_id},
    ).json()
    named = client.post(
        "/vapi/tool",
        json={"message": "Riya", "session_id": session_id},
    ).json()
    completed = client.post(
        "/vapi/tool",
        json={"message": "I want a refund", "session_id": session_id},
    ).json()

    assert "name" in first["response"].lower()
    assert "phone" not in first["response"].lower()
    assert "anything else" in named["response"].lower()
    assert "is your name riya" in completed["response"].lower()

    consent_prompt = client.post(
        "/vapi/tool",
        json={"message": "Yes, that's right", "session_id": session_id},
    ).json()
    assert "whatsapp" in consent_prompt["response"].lower()

    consent_completed = client.post(
        "/vapi/tool",
        json={"message": "Yes, this number is fine", "session_id": session_id},
    ).json()

    assert "reach out to you shortly" in consent_completed["response"].lower()
    assert consent_completed["escalation"]["reason"] == "Customer explicitly requested a human representative"
    assert consent_completed["escalation"]["priority"] == "high"
    assert consent_completed["escalation"]["trigger"] == "human_request"
    assert consent_completed["escalation"]["support_ticket_id"]
    memory = ConversationMemory(session_dir / f"{session_id}.json")
    assert memory.data["phone"] == "current_number"
    assert memory.data["whatsapp_followup_phone"] == "8217008407"
    assert memory.data["customer_name"] == "Riya"
    assert memory.data["escalation_notes"] == "I want a refund"
    assert memory.data["escalation_state"]["trigger"] == "human_request"
    assert memory.data["escalation_state"]["priority"] == "high"


def test_vapi_uses_inbound_caller_id_and_only_requests_name(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    session_dir = tmp_path / "caller_id_sessions"
    monkeypatch.setattr(analytics_db, "DB_PATH", tmp_path / "caller-id-analytics.db")
    analytics_db.init_db()
    monkeypatch.setattr(api_app, "API_MEMORY_DIR", session_dir)
    api_app._sessions.clear()

    body = TestClient(api_app.app).post(
        "/vapi/tool",
        json={
            "message": "I want to speak to a human.",
            "session_id": "caller-id-session",
            "call": {
                "id": "caller-id-session",
                "customer": {"number": "+91 82170 08407"},
            },
        },
    ).json()

    assert "name" in body["response"].lower()
    assert "phone" not in body["response"].lower()
    assert body["transfer"]["status"] == "awaiting_customer_name"
    memory = ConversationMemory(session_dir / "caller-id-session.json")
    assert memory.data["phone"] == "8217008407"
    assert memory.data["phone_source"] == "vapi_caller_id"
