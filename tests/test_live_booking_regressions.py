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
from src.agents.inbound_agent import InboundAgent  # noqa: E402
from src.knowledge.knowledge_loader import KnowledgeLoader  # noqa: E402
from src.orchestration.conversation_manager import ConversationManager  # noqa: E402
from src.services.recommendation_engine import RecommendationEngine  # noqa: E402


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
            "customer_name": "Surabhi Rao",
            "phone": "8217008407",
        }
    )
    memory.save()
    return memory


def make_inbound(tmp_path: Path, memory: ConversationMemory) -> InboundAgent:
    return InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


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
    agent._last_availability = {"available": True, "slots": ["8:30 PM"]}
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
    assert "last name" in name.response.lower()
    assert name.booking_result is None
    agent.booking_tool.create.assert_not_called()

    last_name = agent.handle_message("Rao")
    assert memory.data["customer_name"] == "Surabhi Rao"
    assert "phone" in last_name.response.lower()
    phone = agent.handle_message("8217008407")
    assert memory.data["phone"] == "8217008407"
    agent.booking_tool.create.assert_called_once_with(memory.data, "8:30 PM")
    assert phone.booking_result is not None
    assert phone.booking_result["booking_id"] == "BRK-DEMO123"


def test_booking_readiness_requires_contact_and_age_group(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data["selected_slot"] = "3:00 PM"
    assert memory.booking_ready()

    for field in ("age_group", "room", "selected_slot", "customer_name", "phone"):
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


def test_booking_reuses_participants_age_and_location_from_memory(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data.update({"customer_name": "", "phone": ""})
    memory.save()
    agent = BookingAgent(memory)
    agent.availability_tool.check = MagicMock(return_value={
        "available": True,
        "verified": True,
        "slots": ["3:00 PM"],
        "location": "Whitefield",
        "date": "18 June",
        "participants": 4,
    })

    availability = agent.handle_message("ready")
    contact = agent.handle_message("3 PM")

    assert "3:00 PM" in availability.response
    assert "name" in contact.response.lower()
    combined = f"{availability.response} {contact.response}".lower()
    assert "how many" not in combined
    assert "age group" not in combined
    assert "which location" not in combined


def test_package_inquiry_never_calls_kreeda_availability(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data.update({
        "intent": "birthday_party",
        "event_type": "Birthday Package",
        "recommended_option": "Escape Rooms and Scavenger Hunt",
        "room": "",
    })
    memory.save()
    agent = BookingAgent(memory)
    agent.availability_tool.check = MagicMock()

    result = agent.handle_message("ready")

    assert "package inquiry" in result.response.lower()
    agent.availability_tool.check.assert_not_called()


def test_booking_never_confirms_without_verified_availability(tmp_path: Path) -> None:
    agent = BookingAgent(make_memory(tmp_path))
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["3:00 PM"]
    agent.booking_tool.create = MagicMock()

    result = agent.handle_message("3 PM")

    assert "verify" in result.response.lower()
    assert "confirmed" not in result.response.lower()
    agent.booking_tool.create.assert_not_called()


def test_booking_never_confirms_without_booking_id(tmp_path: Path) -> None:
    agent = BookingAgent(make_memory(tmp_path))
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["3:00 PM"]
    agent._last_availability = {"available": True, "slots": ["3:00 PM"]}
    agent.booking_tool.create = MagicMock(return_value={
        "booking_id": "",
        "confirmed": True,
        "prepared": True,
    })

    result = agent.handle_message("3 PM")

    assert "couldn't confirm" in result.response.lower()
    assert "still saved for retry" in result.response.lower()
    assert result.should_handoff is False
    assert agent._state == agent._STATE_READY_FOR_BOOKING
    assert not agent.memory.data.get("booking_ref")


def test_time_and_group_ranges_do_not_corrupt_age_group(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)

    assert memory._extract_age_group("10 to 15 people") == ("", "")
    assert memory._extract_age_group("between 3 to 6 pm") == ("", "")
    assert memory._extract_age_group("players aged 10 to 15 years") == ("teens", "10-15")


def test_explicit_adults_replaces_incorrect_teen_state_without_confirmation(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data.update({
        "age_group": "teens",
        "age_detail": "10-15",
        "location": "JP Nagar",
        "room": "Murder Mystery",
        "recommended_option": "Murder Mystery",
    })
    memory.save()
    agent = make_inbound(tmp_path, memory)

    agent.handle_message(
        "We are all adults. Is Murder Mystery at JP Nagar available between 3 to 6 PM?"
    )

    assert memory.data["age_group"] == "adults"
    assert memory.data["age_detail"] == ""
    assert not memory.data.get("pending_confirmation")


def test_long_affirmative_date_restatement_applies_pending_change(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data["preferred_date"] = "20 June"
    memory.data["pending_confirmation"] = {
        "field": "preferred_date",
        "old_value": "20 June",
        "new_value": "23 June",
        "type": "correction",
    }
    memory.save()
    agent = make_inbound(tmp_path, memory)

    agent.handle_message(
        "Yes, please proceed with booking Murder Mystery for adults at JP Nagar on 23rd June."
    )

    assert memory.data["preferred_date"] == "23 June"
    assert memory.data["pending_confirmation"] is None


def test_spoken_compound_ordinal_date_is_extracted_as_twenty_third(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    normalized = memory.normalize_number_words("Yes, I want June twenty third for my booking")

    assert normalized == "Yes, I want June 23 for my booking"
    assert memory._extract_preferred_date(normalized) == "23 June"


def test_booking_agent_explains_capacity_without_offering_unbookable_slot(tmp_path: Path) -> None:
    agent = BookingAgent(make_memory(tmp_path))
    agent.availability_tool.check = MagicMock(return_value={
        "available": True,
        "slots": ["3:00 PM"],
        "capacity_supported": False,
        "bookable_slots": [],
        "max_available_capacity": 8,
    })

    result = agent.handle_message("ready")

    assert "returned time slots" in result.response.lower()
    assert "none can fit" in result.response.lower()
    assert agent._available_slots == []


def test_transcript_participant_range_is_persisted_and_reused(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")

    memory.merge_message("We are 10 to 15 people", "escape_room_inquiry")

    assert memory.data["participants_min"] == 10
    assert memory.data["participants_max"] == 15
    assert memory.data["participants"] == 15
    assert "participants" not in memory.missing_fields("escape_room_inquiry")


def test_room_and_slot_message_keeps_booking_workflow_ownership(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    manager = ConversationManager(memory)

    target, category = manager.determine_routing(
        "Please book Undercover at 8:20 PM", "booking_agent"
    )

    assert (target, category) == ("booking_agent", "continuing_workflow")


def test_slot_message_with_room_name_executes_booking_instead_of_room_faq(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data["room"] = "Undercover"
    memory.data["recommended_option"] = "Undercover"
    memory.save()
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["8:20 PM"]
    agent._last_availability = {"available": True, "verified": True, "slots": ["8:20 PM"]}
    agent.booking_tool.create = MagicMock(return_value={
        "booking_id": "bk-live-transcript",
        "booking_reference": "ref-live-transcript",
        "confirmed": True,
        "location": "Whitefield",
        "date": "18 June",
        "slot": "8:20 PM",
        "participants": 4,
        "event_type": "Escape Room",
        "customer_name": "Surabhi",
        "phone": "8217008407",
    })

    result = agent.handle_message("Please book Undercover at 8:20 PM")

    agent.booking_tool.create.assert_called_once_with(memory.data, "8:20 PM")
    assert result.booking_result["booking_id"] == "bk-live-transcript"
    assert memory.data["booking_id"] == "bk-live-transcript"
    assert memory.data["booking_ref"] == "ref-live-transcript"


def test_recommendations_respect_live_location_inventory_and_capacity() -> None:
    engine = RecommendationEngine()

    too_large = engine.recommend("What do you recommend?", {
        "intent": "escape_room_inquiry", "participants": 15,
        "age_group": "adults", "location": "Whitefield",
    })
    couple = engine.recommend("Something challenging", {
        "intent": "escape_room_inquiry", "participants": 2,
        "age_group": "adults", "location": "Whitefield",
    })
    jp_nagar = engine.recommend("Something challenging", {
        "intent": "escape_room_inquiry", "participants": 6,
        "age_group": "adults", "location": "JP Nagar",
    })

    assert too_large.option == "Multi-room event coordination"
    assert "Bomb Defusal" not in couple.option
    assert "Undercover" not in couple.option
    assert "Prison Break" not in jp_nagar.option
    assert "Missile Attack" in jp_nagar.option
