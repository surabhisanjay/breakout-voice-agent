from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from main import dispatch
from src.agents.booking_agent import BookingAgent
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


@pytest.mark.parametrize(
    "message",
    [
        "We are a couple and want to book tomorrow.",
        "Two adults want to book tomorrow.",
        "My husband and I want to book tomorrow.",
        "My girlfriend and I want to book tomorrow.",
    ],
)
def test_couple_language_stays_in_standard_escape_room_booking(tmp_path: Path, message: str) -> None:
    agent = _agent(tmp_path)

    result, _booking, active = dispatch(message, agent, None, "inbound_agent")

    assert result.intent == "escape_room_inquiry"
    assert active != "escalation_agent"
    assert agent.memory.data["intent"] == "escape_room_inquiry"
    if "couple" in message.lower() or "husband" in message.lower() or "girlfriend" in message.lower():
        assert agent.memory.data["relationship"] == "couple"
        assert agent.memory.data["participants"] == 2
    assert "events team" not in result.response.lower()


def test_explicit_couple_package_remains_a_package_inquiry(tmp_path: Path) -> None:
    agent = _agent(tmp_path)

    result, _booking, _active = dispatch("I want to book an anniversary package.", agent, None, "inbound_agent")

    assert result.intent == "couple_event"
    assert agent.memory.data["intent"] == "couple_event"


@pytest.mark.parametrize(
    "acceptance",
    ["Let's book that.", "Book it.", "Yes.", "I'll take that one.", "Go ahead.", "Let's continue."],
)
def test_recommendation_acceptance_commits_ranked_room(tmp_path: Path, acceptance: str) -> None:
    agent = _agent(tmp_path)
    agent.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "event_type": "Escape Room",
            "participants": 2,
            "age_group": "adults",
            "location": "Whitefield",
            "recommended_option": "Murder Mystery or Hostage",
        }
    )
    agent.memory.save()

    result, _booking, _active = dispatch(acceptance, agent, None, "inbound_agent")

    assert agent.memory.data["room"] == "Murder Mystery"
    assert "which specific room" not in result.response.lower()
    assert "date" in result.response.lower()


@pytest.mark.parametrize("message", ["Siddharth", "It's Siddharth", "Book under Siddharth", "S I D D H A R T H", "S I Double D H A R T H"])
def test_name_capture_is_deterministic_for_natural_and_spelled_names(tmp_path: Path, message: str) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "participants": 2,
            "age_group": "adults",
            "location": "Whitefield",
            "preferred_date": "Tomorrow",
            "room": "Murder Mystery",
            "booking_started": True,
        }
    )
    memory.save()
    booking = BookingAgent(memory)
    booking._state = booking._STATE_WAITING_FOR_FIRST_NAME
    booking._selected_slot = "6:30 PM"
    booking._available_slots = ["6:30 PM"]
    booking._last_availability = {"available": True, "verified": True, "slots": ["6:30 PM"]}

    response = booking.handle_message(message)

    assert memory.data["customer_name"] == "Siddharth"
    assert booking._state == booking._STATE_WAITING_FOR_PHONE
    assert "phone" in response.response.lower()


def test_dispatch_uses_evening_slots_and_preserves_booking_memory(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "participants": 2,
            "age_group": "adults",
            "location": "Whitefield",
            "room": "Murder Mystery",
        }
    )
    agent.memory.save()

    result, booking, active = dispatch("Tomorrow evening.", agent, None, "inbound_agent")

    assert active == "booking_agent"
    assert booking is not None
    assert agent.memory.data["location"] == "Whitefield"
    assert agent.memory.data["participants"] == 2
    assert all("PM" in slot for slot in booking._available_slots)
    assert "5:20 PM" in result.response and "7:40 PM" in result.response


def test_slot_change_and_booking_creation_keep_forward_progress(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    booking = None
    active = "inbound_agent"
    for turn in [
        "Book Hostage for 2 adults at JP Nagar tomorrow.",
        "3:30 or 4 PM would work.",
        "Actually make it 7 PM.",
        "Siddharth",
        "9982151357",
    ]:
        result, booking, active = dispatch(turn, agent, booking, active)

    assert active == "booking_agent"
    assert result.booking_result is not None
    assert result.booking_result["confirmed"] is True
    assert result.booking_result["slot"] == "7:00 PM"
    assert agent.memory.data["selected_slot"] == "7:00 PM"
    assert agent.memory.data["room"] == "Hostage"
    assert agent.memory.data["customer_name"] == "Siddharth"
