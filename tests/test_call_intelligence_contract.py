from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import sys

from fastapi.testclient import TestClient

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import app as app_module
from main import build_inbound_agent, dispatch
from src.agents.booking_agent import BookingAgent
from src.agents.conversation_intelligence_agent import ConversationIntelligenceAgent
from src.agents.escalation_agent import EscalationAgent
from src.agents.handoff_summary_agent import HandoffSummaryAgent
from src.agents.sentiment_agent import SentimentAgent
from src.memory.conversation_memory import ConversationMemory


def _memory(tmp_path: Path, **updates) -> ConversationMemory:
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update(updates)
    memory.save()
    return memory


def _inbound(memory: ConversationMemory):
    return build_inbound_agent(Namespace(model=None, no_openai=True), memory)


def test_sentiment_agent_tracks_conversation_wide_frustration(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    agent = SentimentAgent(memory)

    agent.analyze("This looks exciting.", stage="recommendation")
    agent.analyze("Actually change it to Hostage.", stage="booking")
    result = agent.analyze("No no no, that's not what I asked. You are not understanding.", stage="booking")

    payload = result.to_dict()
    assert payload["current_sentiment"] == "Frustrated"
    assert payload["overall_sentiment"] in {"Frustrated", "Ready To Escalate"}
    assert payload["frustration_score"] > 0
    assert payload["escalation_risk"] in {"medium", "high"}
    assert payload["sentiment_journey"]
    assert "Customer interruption or rejection" in payload["frustration_reasons"]
    assert payload["conversation_health"] in {"watch", "poor"}


def test_conversation_intelligence_for_successful_booking(tmp_path: Path) -> None:
    memory = _memory(
        tmp_path,
        intent="escape_room_inquiry",
        event_type="Escape Room",
        participants=4,
        age_group="adults",
        experience_level="beginner",
        location="Whitefield",
        preferred_date="tomorrow",
        room="Murder Mystery",
        selected_slot="7:00 PM",
        customer_name="Riya Patel",
        phone="9876543210",
        booking_id="BK-123",
        booking_ref="BR-123",
        booking_started=True,
    )
    memory.add_turn("customer", "This is my first time.")
    memory.add_turn("agent", "That's exciting.")
    memory.add_turn("customer", "Book Murder Mystery at Whitefield tomorrow at 7 PM.")

    SentimentAgent(memory).analyze("Please confirm booking.", stage="booking")
    intelligence = ConversationIntelligenceAgent().analyze(memory.data)

    assert intelligence["ai_summary"]["summary"]
    assert intelligence["customer_profile"]["booking_status"] == "confirmed"
    assert "First-time player" in intelligence["key_takeaways"]
    assert any(event["event"] == "Booking Confirmed" for event in intelligence["timeline_events"])
    assert "Send booking confirmation" in intelligence["follow_up_recommendations"]
    assert intelligence["recording"]["available"] is False
    assert intelligence["transcript"]


def test_handoff_summary_contains_manager_ready_fields(tmp_path: Path) -> None:
    memory = _memory(
        tmp_path,
        intent="escape_room_inquiry",
        customer_name="Riya Patel",
        phone="9876543210",
        participants=4,
        location="JP Nagar",
        preferred_date="25 June",
        selected_slot="7:00 PM",
        room="Murder Mystery",
        sentiment_analysis={
            "overall_sentiment": "Frustrated",
            "current_sentiment": "Frustrated",
            "sentiment_score": 0.2,
            "frustration_score": 0.7,
            "escalation_risk": "high",
            "customer_mood": "Ready To Escalate",
            "sentiment_journey": [],
            "frustration_reasons": ["Repeated corrections"],
            "conversation_health": "poor",
        },
    )

    summary = HandoffSummaryAgent().generate(
        memory.data,
        {"escalate": True, "reason": "Customer requested a human representative", "priority": "medium"},
    )

    assert summary["phone_available"] is True
    assert summary["booking_details"]["room"] == "Murder Mystery"
    assert summary["sentiment"]["escalation_risk"] == "high"
    assert summary["escalation"]["required"] is True
    assert summary["action_items"]
    assert summary["follow_up_recommendations"]
    assert summary["conversation_summary"]
    assert summary["summary"]


def test_escalation_consumes_sentiment_and_sets_priority(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    sentiment_agent = SentimentAgent(memory)
    sentiment_agent.analyze("I am frustrated.", stage="booking")
    sentiment = sentiment_agent.analyze("You keep asking me the same thing.", stage="booking")

    escalation = EscalationAgent(memory).evaluate("You keep asking me the same thing.", sentiment)
    payload = escalation.to_dict()

    assert payload["escalate"] is True
    assert payload["priority"] in {"medium", "high"}
    assert payload["status"] == "unresolved"
    assert payload["trigger"] in {"frustration", "manual_review"}
    assert payload["recommended_human_action"]


def test_refund_and_human_requests_have_frontend_escalation_contract(tmp_path: Path) -> None:
    for message, expected_trigger in [
        ("I want a refund", "refund_request"),
        ("I want a human", "human_request"),
    ]:
        memory = _memory(tmp_path / expected_trigger)
        sentiment = SentimentAgent(memory).analyze(message, stage="booking")
        escalation = EscalationAgent(memory).evaluate(message, sentiment)
        payload = escalation.to_dict()
        assert payload["escalate"] is True
        assert payload["trigger"] == expected_trigger
        assert payload["priority"] in {"medium", "high"}


def test_dispatch_returns_frontend_ready_intelligence_for_faq_interruption(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    memory = _memory(
        tmp_path,
        intent="escape_room_inquiry",
        participants=4,
        age_group="adults",
        location="Whitefield",
        preferred_date="tomorrow",
        room="Murder Mystery",
        booking_started=True,
        current_workflow="booking",
    )
    inbound = _inbound(memory)

    result, _, _ = dispatch("Is parking available?", inbound, None, "booking_agent")

    assert result.ai_summary["summary"]
    assert result.customer_profile["intent"] == "escape_room_inquiry"
    assert isinstance(result.timeline_events, list)
    assert result.follow_up_recommendations
    assert result.transcript
    assert result.recording["available"] is False
    assert result.call_intelligence["timeline_events"] == result.timeline_events


def test_dispatch_tracks_booking_modification_room_and_date_change(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    memory = _memory(
        tmp_path,
        intent="escape_room_inquiry",
        participants=4,
        age_group="adults",
        location="JP Nagar",
        preferred_date="25 June",
        room="Murder Mystery",
        selected_slot="7:00 PM",
        booking_started=True,
        current_workflow="booking",
    )
    booking = BookingAgent(memory)
    booking.handle_message("Actually Hostage")
    booking.handle_message("Actually 26 June")
    intelligence = ConversationIntelligenceAgent().analyze(memory.data)

    assert memory.data["room"] == "Hostage"
    assert memory.data["preferred_date"] == "26 June"
    assert memory.data["selected_slot"] == ""
    assert any("Room Selected" in event["event"] for event in intelligence["timeline_events"])


def test_chat_api_returns_complete_frontend_contract(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    app_module._sessions.clear()
    client = TestClient(app_module.app)

    response = client.post(
        "/chat",
        json={"session_id": "intelligence-api", "message": "We are four adults and this is our first time."},
    )

    assert response.status_code == 200
    payload = response.json()
    for key in [
        "sentiment_analysis",
        "call_intelligence",
        "ai_summary",
        "customer_profile",
        "timeline_events",
        "follow_up_recommendations",
        "transcript",
        "recording",
    ]:
        assert key in payload
    assert payload["ai_summary"]["summary"]
    assert isinstance(payload["transcript"], list)

    intelligence = client.get("/intelligence/intelligence-api")
    assert intelligence.status_code == 200
    intelligence_payload = intelligence.json()
    assert intelligence_payload["ai_summary"]["summary"]
    assert "customer_profile" in intelligence_payload


def test_booking_abandonment_adds_risk_and_follow_up(tmp_path: Path) -> None:
    memory = _memory(
        tmp_path,
        intent="escape_room_inquiry",
        participants=4,
        location="Whitefield",
        booking_started=True,
        current_workflow="booking",
    )
    SentimentAgent(memory).analyze("Forget it, don't book anymore.", stage="booking")

    intelligence = ConversationIntelligenceAgent().analyze(memory.data)

    assert any("Booking abandonment" in risk for risk in intelligence["conversation_risks"])
    assert "Manager should review conversation risks" in intelligence["follow_up_recommendations"]
