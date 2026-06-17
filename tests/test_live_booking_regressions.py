from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.booking_agent import BookingAgent  # noqa: E402
from src.orchestration.booking_orchestrator import BookingOrchestrator  # noqa: E402
from src.core.booking_provider import BreakoutAPIProvider, SimulatorProvider  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402


def make_memory(tmp_path: Path, *, with_date: bool = True) -> ConversationMemory:
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "event_type": "Escape Room",
            "participants": 4,
            "age_group": "adults",
            "location": "Whitefield",
            "preferred_date": "18 June" if with_date else "",
            "room": "Bomb Defusal",
            "recommended_option": "Bomb Defusal",
            "customer_name": "Surabhi",
            "phone": "8217008407",
        }
    )
    memory.save()
    return memory


def test_date_is_required_before_availability_or_time_selection(tmp_path: Path) -> None:
    memory = make_memory(tmp_path, with_date=False)
    agent = BookingAgent(memory)
    agent.availability_tool.check = MagicMock()

    first = agent.handle_message("Whitefield")

    assert "what date" in first.response.lower()
    agent.availability_tool.check.assert_not_called()
    assert agent._state == agent._STATE_CHECKING_AVAILABILITY

    agent.availability_tool.check.return_value = {
        "available": True,
        "slots": ["3:00 PM"],
        "location": "Whitefield",
        "date": "18 June",
        "participants": 4,
    }
    second = agent.handle_message("18 June")
    assert memory.data["preferred_date"] == "18 June"
    agent.availability_tool.check.assert_called_once_with("Whitefield", "18 June", 4)
    assert "3:00 PM" in second.response


def test_food_faq_answers_then_resumes_booking(tmp_path: Path) -> None:
    agent = BookingAgent(make_memory(tmp_path))
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["3:00 PM", "5:00 PM"]
    agent.booking_tool.create = MagicMock()

    result = agent.handle_message("about food")

    assert "Food options include" in result.response
    assert "3:00 PM" in result.response
    assert "Which time works best" in result.response
    assert agent._state == agent._STATE_WAITING_FOR_SLOT
    agent.booking_tool.create.assert_not_called()


def test_booking_cannot_be_prepared_without_date(tmp_path: Path) -> None:
    memory = make_memory(tmp_path, with_date=False)
    simulator = SimulatorProvider()
    simulator.booking_tool.create = MagicMock()

    result = simulator.prepare_booking(memory.data, "3:00 PM")

    assert result["prepared"] is False
    assert "preferred_date" in result["error"]
    simulator.booking_tool.create.assert_not_called()

    orchestrator = BookingOrchestrator(simulator_provider=simulator)
    result = orchestrator.prepare_booking(memory.data, "3:00 PM")
    assert result["prepared"] is False
    assert "preferred_date" in result["error"]


def test_cancellation_policy_never_cancels_booking(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["3:00 PM"]
    agent.orchestrator.cancel_booking = MagicMock()

    result = agent.handle_message("What is your cancellation policy?")

    assert "Cancellation charges" in result.response
    assert "3:00 PM" in result.response
    assert "cancelled" not in result.response.lower()
    agent.orchestrator.cancel_booking.assert_not_called()
    assert agent._state == agent._STATE_WAITING_FOR_SLOT


def test_explicit_cancel_booking_still_cancels(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data["booking_ref"] = "BRK-123"
    agent = BookingAgent(memory)
    agent.orchestrator.cancel_booking = MagicMock(return_value={"status": "cancelled"})

    result = agent.handle_message("I want to cancel my booking")

    agent.orchestrator.cancel_booking.assert_called_once_with("BRK-123", reason="customer request")
    assert "cancelled" in result.response.lower()


def test_time_normalization_variants() -> None:
    variants = ("3pm", "3:pm", "3 pm", "03:00 pm")
    for value in variants:
        assert BookingAgent._extract_slot(value) == "3:00 PM"

    assert BreakoutAPIProvider._normalise_time("3pm") == "15:00"
    assert BreakoutAPIProvider._normalise_time("3:pm") == "15:00"
    assert BreakoutAPIProvider._normalise_time("3 pm") == "15:00"
    assert BreakoutAPIProvider._normalise_time("03:00 pm") == "15:00"


def test_contextual_best_resolves_against_food_during_booking(tmp_path: Path) -> None:
    agent = BookingAgent(make_memory(tmp_path))
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["3:00 PM", "5:00 PM"]

    agent.handle_message("What food options do you have?")
    result = agent.handle_message("Which is best?")

    assert "Indian buffet" in result.response
    assert "lighter" in result.response
    assert "3:00 PM" in result.response


def test_policy_offer_yes_provides_full_policy_and_resumes_booking(tmp_path: Path) -> None:
    agent = BookingAgent(make_memory(tmp_path))
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["3:00 PM"]

    offer = agent.handle_message("What is the cancellation policy?")
    explanation = agent.handle_message("Yes please")

    assert "I can explain the policy" in offer.response
    assert "3 days or more" in explanation.response
    assert "not refundable" in explanation.response
    assert "3:00 PM" in explanation.response
    assert agent._state == agent._STATE_WAITING_FOR_SLOT


def test_booking_completion_uses_concierge_follow_up(tmp_path: Path) -> None:
    agent = BookingAgent(make_memory(tmp_path))
    agent.handle_message("ready")

    result = agent.handle_message(agent._available_slots[0])

    lowered = result.response.lower()
    assert "food options" in lowered
    assert "parking" in lowered
    assert "cancellation policy" in lowered
    assert "arrival guidance" in lowered
    assert "anything else" not in lowered


def test_slot_selection_collects_contact_before_preparing_booking(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data.update({"participants": 7, "age_group": "adults", "location": "JP Nagar", "preferred_date": "20 June"})
    memory.data["customer_name"] = ""
    memory.data["phone"] = ""
    memory.save()
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["8:30 PM"]
    agent.booking_tool.create = MagicMock(return_value={
        "booking_id": "BRK-DEMO123",
        "confirmed": True,
        "location": "JP Nagar",
        "date": "20 June",
        "slot": "8:30 PM",
        "participants": 7,
        "event_type": "Escape Room",
        "customer_name": "Surabhi",
        "phone": "8217008407",
    })

    slot = agent.handle_message("8:30 PM")
    assert "available slot at 8:30 PM" in slot.response
    assert "name" in slot.response.lower()
    assert slot.booking_result is None
    agent.booking_tool.create.assert_not_called()

    name = agent.handle_message("Surabhi")
    assert memory.data["customer_name"] == "Surabhi"
    assert "phone" in name.response.lower()
    assert name.booking_result is None
    agent.booking_tool.create.assert_not_called()

    phone = agent.handle_message("8217008407")
    assert memory.data["phone"] == "8217008407"
    agent.booking_tool.create.assert_called_once_with(memory.data, "8:30 PM")
    assert phone.booking_result is not None
    assert phone.booking_result["booking_id"] == "BRK-DEMO123"


def test_booking_readiness_requires_contact_and_age_group(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    assert memory.booking_ready()

    for field in ("age_group", "customer_name", "phone"):
        original = memory.data[field]
        memory.data[field] = ""
        assert not memory.booking_ready()
        memory.data[field] = original


def test_name_lookup_before_and_after_capture(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data["customer_name"] = ""
    memory.save()
    agent = BookingAgent(memory)

    missing = agent.handle_message("my name?")
    assert missing.response == "I don't have your name yet."

    memory.data["customer_name"] = "Surabhi"
    memory.save()
    stored = agent.handle_message("my name?")
    assert stored.response == "You're booked under Surabhi."
