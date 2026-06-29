from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from main import dispatch
from src.agents.inbound_agent import InboundAgent
from src.knowledge.knowledge_loader import KnowledgeLoader
from src.memory.conversation_memory import ConversationMemory


@pytest.fixture(autouse=True)
def offline_booking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKING_PROVIDER", "simulator")
    monkeypatch.setenv("BOOKING_API_KEY", "")
    monkeypatch.setenv("BOOKING_BASE_URL", "")


def _agent(tmp_path: Path) -> InboundAgent:
    return InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=ConversationMemory(tmp_path / "session.json"),
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


def test_tomorrow_evening_fragment_routes_to_availability_and_returns_evening_slots(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "participants": 4,
            "age_group": "adults",
            "experience_level": "beginner",
            "location": "Whitefield",
            "room": "Murder Mystery",
        }
    )
    agent.memory.save()

    result, booking, active = dispatch("Tomorrow evening.", agent, None, "inbound_agent")

    assert booking is not None
    assert active == "booking_agent"
    assert "5:20 PM" in result.response
    assert "6:30 PM" in result.response
    assert "7:40 PM" in result.response
    assert "AM" not in result.response


def test_first_time_side_channel_does_not_repeat_same_location_question(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    booking = None
    active = "inbound_agent"

    _result, booking, active = dispatch("I'd like to book an escape room.", agent, booking, active)
    first_location_question, booking, active = dispatch("We are 4 adults.", agent, booking, active)
    second_location_question, booking, active = dispatch("It is our first time.", agent, booking, active)

    assert first_location_question.response != second_location_question.response
    assert "first time" in second_location_question.response.lower()
    assert "share the branch" in second_location_question.response.lower()


def test_returning_customer_side_channel_moves_to_participant_question(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    booking = None
    active = "inbound_agent"

    first, booking, active = dispatch("I'd like to book Hostage.", agent, booking, active)
    second, booking, active = dispatch("We are returning customers.", agent, booking, active)

    assert "which location would you prefer" in first.response.lower()
    assert "group size" in second.response.lower()
    assert "welcome back" in second.response.lower()


def test_recent_location_question_is_not_reasked_after_group_size(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    booking = None
    active = "inbound_agent"

    first, booking, active = dispatch("I'd like to book Hostage.", agent, booking, active)
    second, booking, active = dispatch("We are returning customers.", agent, booking, active)
    third, booking, active = dispatch("6 adults.", agent, booking, active)

    assert "which location" in first.response.lower()
    assert not second.response.strip().endswith("?")
    assert not third.response.strip().endswith("?")
    assert "share the branch" in third.response.lower()


def test_existing_booking_complaints_raise_sentiment_and_escalate(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "location": "Whitefield",
            "room": "Hostage",
            "participants": 4,
            "age_group": "adults",
            "preferred_date": "Tomorrow",
            "selected_slot": "6:30 PM",
            "customer_name": "Siddharth Khandelwal",
            "phone": "9982151357",
            "booking_id": "BRK-DEMO123",
            "booking_ref": "BRK-DEMO123",
            "completed_booking": True,
        }
    )
    agent.memory.save()
    booking = None
    active = "inbound_agent"

    for turn in [
        "I've already booked.",
        "My booking is wrong.",
        "You people are not understanding.",
        "I already told you everything.",
    ]:
        result, booking, active = dispatch(turn, agent, booking, active)

    result, booking, active = dispatch("I want to speak to a human.", agent, booking, active)

    sentiment = result.sentiment_analysis
    assert result.should_handoff is True
    assert result.escalation["escalate"] is True
    assert sentiment["current_sentiment"] == "Frustrated"
    assert sentiment["overall_sentiment"] in {"Frustrated", "Ready To Escalate"}
    assert sentiment["escalation_risk"] in {"medium", "high"}
    assert sentiment["frustration_score"] >= 0.4
    assert result.handoff_summary["booking_details"]["slot"] == "6:30 PM"


def test_golden_demo_slot_change_updates_payload_without_restarting_booking(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    booking = None
    active = "inbound_agent"
    for turn in [
        "I'd like to book Hostage.",
        "6 adults.",
        "JP Nagar.",
        "Tomorrow.",
        "3:30 or 4 PM would work.",
        "Actually make it 7 PM.",
        "Siddharth Khandelwal",
    ]:
        result, booking, active = dispatch(turn, agent, booking, active)

    result, booking, active = dispatch("9982151357", agent, booking, active)

    assert active == "booking_agent"
    assert result.booking_result is not None
    assert result.booking_result["confirmed"] is True
    assert result.booking_result["slot"] == "7:00 PM"
    assert agent.memory.data["selected_slot"] == "7:00 PM"
    assert agent.memory.data["customer_name"] == "Siddharth Khandelwal"
