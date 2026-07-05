from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.booking_agent import BookingAgent  # noqa: E402
from src.agents.inbound_agent import InboundAgent  # noqa: E402
from src.knowledge.knowledge_loader import KnowledgeLoader  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402


def make_inbound(tmp_path: Path) -> InboundAgent:
    return InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=ConversationMemory(tmp_path / "session.json"),
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
        use_ollama=False,
    )


def booking_memory(tmp_path: Path) -> ConversationMemory:
    memory = ConversationMemory(tmp_path / "booking.json")
    memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "booking_started": True,
            "current_workflow": "booking",
            "participants": 4,
            "age_group": "adults",
            "location": "Whitefield",
            "preferred_date": "Tomorrow",
            "room": "Murder Mystery",
            "selected_slot": "3:00 PM",
            "preferred_time": "3:00 PM",
        }
    )
    memory.save()
    return memory


def test_room_change_is_acknowledged_and_clears_selected_slot(tmp_path: Path) -> None:
    memory = booking_memory(tmp_path)
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["3:00 PM", "5:00 PM"]

    response = agent.handle_message("Actually change to Hostage.").response

    assert "switched the room to Hostage" in response
    assert "same date and time" in response
    assert memory.data["room"] == "Hostage"
    assert memory.data["selected_slot"] == ""
    assert agent._state == agent._STATE_WAITING_FOR_SLOT


def test_date_change_is_acknowledged_and_clears_selected_slot(tmp_path: Path) -> None:
    memory = booking_memory(tmp_path)
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["3:00 PM", "5:00 PM"]

    response = agent.handle_message("Actually make it June 26.").response

    assert "check 26 June instead" in response
    assert "same room" in response
    assert memory.data["preferred_date"] == "26 June"
    assert memory.data["selected_slot"] == ""
    assert agent._state == agent._STATE_WAITING_FOR_SLOT


def test_location_specific_parking_answer_for_inbound(tmp_path: Path) -> None:
    response = make_inbound(tmp_path).handle_message("What's parking like at Whitefield?").response

    assert response == "Whitefield has basement parking available."
    assert "Koramangala" not in response
    assert "JP Nagar" not in response


def test_location_specific_parking_answer_for_active_booking(tmp_path: Path) -> None:
    memory = booking_memory(tmp_path)
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["3:00 PM", "5:00 PM"]

    response = agent.handle_message("What's parking like?").response

    assert "Whitefield has basement parking available" in response
    assert "available slots" in response.lower()


def test_food_answer_uses_birthday_context(tmp_path: Path) -> None:
    agent = make_inbound(tmp_path)

    response = agent.handle_message("Do you have food options for birthday parties?").response

    assert "birthday parties" in response
    assert "guest count" in response
    assert "corporate events" not in response


def test_recommendation_stays_consistent_after_location_selection(tmp_path: Path) -> None:
    agent = make_inbound(tmp_path)

    first = agent.handle_message("We are first timers but want something thrilling and adventurous.").response
    agent.handle_message("JP Nagar")
    final = agent.handle_message("Four adults").response

    assert "suspense" in first or "pressure" in first
    assert "date" in final.lower()
    assert "Classified" not in final
    assert "Bomb Defusal" not in final
