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
from src.services.conversation_guard import ConversationGuard  # noqa: E402
from src.agents.sentiment_agent import SentimentAgent  # noqa: E402
from main import _start_additional_booking, dispatch  # noqa: E402
from scripts.demo_runner import _discover_evening_slot  # noqa: E402


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


def test_policy_question_provides_full_policy_and_resumes_booking(tmp_path: Path) -> None:
    agent = BookingAgent(make_memory(tmp_path))
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["3:00 PM"]

    explanation = agent.handle_message("What is the cancellation policy?")

    assert "3 days" in explanation.response
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
    assert "cancellation" not in lowered
    assert "arrival guidance" in lowered
    assert "anything else" not in lowered


def test_booking_confirmation_does_not_include_policy_text(tmp_path: Path) -> None:
    """
    A standard booking confirmation response must NOT contain policy text
    unless the customer explicitly asked about it.
    """
    agent = BookingAgent(make_memory(tmp_path))
    agent.handle_message("ready")

    result = agent.handle_message(agent._available_slots[0])

    lowered = result.response.lower()
    assert "no refund" not in lowered
    assert "no reschedule policy" not in lowered
    assert "cancellation" not in lowered


def test_cancellation_policy_uses_venue_policy_endpoint(monkeypatch, tmp_path: Path) -> None:
    """
    When customer asks about cancellation policy, backend must call
    get_venue_policy() and return the actual graduated policy text.
    """
    api = MagicMock()
    api.get_booking_venues.return_value = [
        {"venueId": "venue-whitefield", "venueName": "Whitefield"}
    ]
    api.call_agent_tool.return_value = {
        "venueId": "venue-whitefield",
        "cancellationPolicy": "0% at 3 days, 25% under 3 days, 50% under 2 days, and 75% under 1 day.",
        "reschedulePolicy": "No fee at least 2 days ahead.",
    }
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://booking.example")
    monkeypatch.setattr("src.services.venue_policy.BreakoutAPI", lambda timeout: api)
    agent = BookingAgent(make_memory(tmp_path))
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["3:00 PM"]

    result = agent.handle_message("What is your cancellation policy?")

    api.call_agent_tool.assert_called_once_with(
        "get_venue_policy", {"venueId": "venue-whitefield"}
    )
    assert "25% under 3 days" in result.response
    assert "3:00 PM" in result.response


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
        "booking_reference": "REF-DEMO123",
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
    assert memory.data["first_name"] == "Surabhi"
    assert memory.data["last_name"] == ""
    assert "phone" in name.response.lower()
    assert "last name" not in name.response.lower()
    assert name.booking_result is None
    agent.booking_tool.create.assert_not_called()

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


def test_live_availability_exposes_only_capacity_valid_slots(monkeypatch) -> None:
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.delenv("BOOKING_PROVIDER", raising=False)
    provider = MagicMock()
    provider.get_booking_venues.return_value = [{"venueId": "venue-whitefield", "venueName": "Whitefield"}]
    provider.get_booking_games.return_value = [{
        "gameId": "game-mm",
        "gameName": "Murder Mystery",
        "peopleMin": 2,
        "peopleMax": 7,
    }]
    provider.search_booking_slots.return_value = [
        {"eventId": "slot-630", "time": "18:30", "available": 7, "isAvailable": True},
        {"eventId": "slot-735", "time": "19:35", "available": 3, "isAvailable": True},
        {"eventId": "slot-850", "time": "20:50", "available": 7, "isAvailable": True},
    ]
    orchestrator = BookingOrchestrator(booking_provider=provider)

    availability = orchestrator.check_availability("Whitefield", "Tomorrow", 4, "Murder Mystery")

    assert availability["slots"] == ["6:30 PM", "8:50 PM"]
    assert availability["all_available_slots"] == ["6:30 PM", "7:35 PM", "8:50 PM"]
    assert availability["bookable_slots"] == ["6:30 PM", "8:50 PM"]


def test_evening_request_uses_bookable_slots_only_and_limits_choices(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data["preferred_period"] = "evening"
    memory.save()
    agent = BookingAgent(memory)
    agent.availability_tool.check = MagicMock(return_value={
        "available": True,
        "verified": True,
        "slots": ["5:20 PM", "6:30 PM", "7:35 PM", "8:50 PM", "10:00 PM"],
        "bookable_slots": ["6:30 PM", "8:50 PM", "10:00 PM"],
        "location": "Whitefield",
        "date": "Tomorrow",
        "participants": 4,
    })

    result = agent.handle_message("ready")

    assert "evening slots" in result.response
    assert "6:30 PM" in result.response
    assert "8:50 PM" in result.response
    assert "10:00 PM" in result.response
    assert "7:35 PM" not in result.response


def test_evening_slot_resolution_is_slot_agnostic(tmp_path: Path) -> None:
    """
    Backend must return only evening slots (17:00+) when customer says
    'tomorrow evening' or 'around 7'. Must not assume a fixed fallback slot.
    Must complete booking with whatever slot Kreeda returns.
    """
    memory = make_memory(tmp_path)
    memory.data.update({"preferred_date": "Tomorrow", "preferred_period": "evening"})
    memory.save()
    agent = BookingAgent(memory)
    returned_slots = [
        f"{hour if hour <= 12 else hour - 12}:15 {'AM' if hour < 12 else 'PM'}"
        for hour in (11, 18, 19, 20, 23)
    ]
    agent.availability_tool.check = MagicMock(
        return_value={
            "available": True,
            "verified": True,
            "slots": returned_slots,
            "bookable_slots": returned_slots,
            "location": "Whitefield",
            "date": "Tomorrow",
            "participants": 4,
        }
    )
    selected_slot = BookingAgent.select_evening_slot(returned_slots)
    agent.booking_tool.create = MagicMock(
        return_value={
            "booking_id": "bk_dynamic",
            "booking_reference": "or_dynamic",
            "order_id": "or_dynamic",
            "payment_url": None,
            "status": "PAYMENT_PENDING",
            "confirmed": True,
            "location": "Whitefield",
            "date": "Tomorrow",
            "slot": selected_slot,
            "participants": 4,
        }
    )

    availability = agent.handle_message("Tomorrow evening")
    final = agent.handle_message(selected_slot)

    assert agent._available_slots
    assert all(17 <= BookingAgent._slot_hour(slot) <= 22 for slot in agent._available_slots)
    assert "AM" not in availability.response
    assert selected_slot in agent._available_slots
    assert final.booking_result["booking_id"] == "bk_dynamic"
    assert final.booking_result["order_id"] == "or_dynamic"
    assert "payment_url" in final.booking_result
    assert final.booking_result["status"] == "PAYMENT_PENDING"
    assert final.booking_result["slot"] == selected_slot
    assert memory.data["selected_slot"] == selected_slot


def test_evening_slot_resolution_uses_first_returned_when_target_is_absent() -> None:
    slots = ["6:20 PM", "8:10 PM", "10:30 PM", "11:00 AM"]

    assert BookingAgent.select_evening_slot(slots) == slots[0]
    assert BookingAgent.evening_slots(slots) == slots[:2]


def test_evening_slot_resolution_asks_for_different_day_when_none_exist(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data.update({"preferred_date": "Tomorrow", "preferred_period": "evening"})
    memory.save()
    agent = BookingAgent(memory)
    agent.availability_tool.check = MagicMock(
        return_value={
            "available": True,
            "verified": True,
            "slots": ["11:00 AM", "3:30 PM"],
            "bookable_slots": ["11:00 AM", "3:30 PM"],
        }
    )

    result = agent.handle_message("Tomorrow evening")

    assert "no verified evening slots" in result.response.lower()
    assert "different day" in result.response.lower()
    assert "AM" not in result.response


def test_demo_slot_discovery_advances_to_first_day_with_evening_inventory() -> None:
    api = MagicMock()
    api.get_booking_venues.return_value = [
        {"venueId": "venue-koramangala", "venueName": "Koramangala"}
    ]
    api.get_booking_games.return_value = [
        {"gameId": "game-hostage", "gameName": "Hostage"}
    ]
    start_date = "2026-07-01"
    api.search_booking_slots.side_effect = [
        [{"time": "18:30", "available": 0, "isAvailable": False}],
        [{"time": "19:10", "available": 7, "isAvailable": True}],
    ]

    venue_id, game_id, selected_date, selected_slot = _discover_evening_slot(
        api,
        location="Koramangala",
        room="Hostage",
        participants=2,
        booking_date=start_date,
        search_days=2,
    )

    assert venue_id == "venue-koramangala"
    assert game_id == "game-hostage"
    assert selected_date == "2026-07-02"
    assert selected_slot == "7:10 PM"


def test_calendar_date_with_evening_is_not_parsed_as_morning_slot(tmp_path: Path) -> None:
    memory = make_memory(tmp_path, with_date=False)
    memory.data["room"] = "Hostage"
    memory.save()
    agent = BookingAgent(memory)
    agent.availability_tool.check = MagicMock(
        return_value={
            "available": True,
            "verified": True,
            "slots": ["5:10 PM", "6:30 PM", "7:50 PM"],
            "bookable_slots": ["5:10 PM", "6:30 PM", "7:50 PM"],
        }
    )

    result = agent.handle_message("1 July evening")

    assert memory.data["preferred_date"] == "1 July"
    assert memory.data.get("preferred_time") in ("", None)
    assert memory.data.get("selected_slot") in ("", None)
    assert "AM" not in result.response
    assert "5:10 PM" in result.response


def test_location_correction_uses_last_mentioned_branch(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")

    memory.merge_message("JP Nagar I think. Actually no, wait - Koramangala. Yeah Koramangala.", "escape_room_inquiry")

    assert memory.data["location"] == "Koramangala"


def test_never_done_triggers_beginner_recommendation_guard(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update({"participants": 4, "age_group": "adults", "location": "Whitefield", "intent": "escape_room_inquiry"})
    memory.save()

    result = ConversationGuard(memory).evaluate("We've never done one before so beginner friendly would be good.", "inbound_agent")

    assert result is not None
    assert "Murder Mystery" in result.response
    assert memory.data["recommended_option"] == "Murder Mystery"


def test_bare_evening_time_selects_matching_available_slot(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data.update({"room": "Murder Mystery", "preferred_period": "evening", "selected_slot": "", "customer_name": "", "phone": ""})
    memory.save()
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["5:20 PM", "6:30 PM", "7:35 PM", "8:50 PM"]
    agent._last_availability = {"available": True, "verified": True, "slots": agent._available_slots}

    result = agent.handle_message("Okay, 7:35 then.")

    assert memory.data["selected_slot"] == "7:35 PM"
    assert "name" in result.response.lower()


def test_unavailable_around_time_remembers_nearest_for_that_works(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data.update({"room": "Hostage", "selected_slot": "", "customer_name": "", "phone": ""})
    memory.save()
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["6:30 PM", "7:35 PM", "8:50 PM"]
    agent._last_availability = {"available": True, "verified": True, "slots": agent._available_slots}

    first = agent.handle_message("Like around 7?")
    second = agent.handle_message("That works. Let's do it.")

    assert "nearest available slots" in first.response.lower()
    assert memory.data["selected_slot"] == "6:30 PM"
    assert "name" in second.response.lower()


def test_human_transfer_and_complaint_repair_do_not_use_banned_opener(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update({"participants": 3, "age_group": "adults", "location": "Whitefield", "recommended_option": "Murder Mystery"})
    memory.save()

    complaint = ConversationGuard(memory).evaluate("I don't like any of these. You keep suggesting the same things.", "inbound_agent")
    handoff = ConversationGuard(memory).evaluate("I want to speak to a human.", "inbound_agent")

    assert complaint is not None
    assert "Murder Mystery" not in complaint.response
    assert handoff is not None
    assert not handoff.response.startswith("Of course")


def test_do_not_want_rejection_never_repeats_same_recommendation(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update(
        {
            "participants": 2,
            "age_group": "adults",
            "experience_level": "beginner",
            "location": "Koramangala",
            "recommended_option": "Murder Mystery",
            "discussed_options": ["Murder Mystery"],
        }
    )
    memory.save()

    result = ConversationGuard(memory).evaluate(
        "I do not want that one. Something else.", "inbound_agent"
    )

    assert result is not None
    assert "Murder Mystery" not in result.response
    assert memory.data["rejected_options"] == ["Murder Mystery"]


def test_rejected_reference_is_not_committed_as_selected_room(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "participants": 2,
            "age_group": "adults",
            "experience_level": "beginner",
            "location": "Koramangala",
            "recommended_option": "Murder Mystery",
            "discussed_options": ["Murder Mystery"],
        }
    )
    memory.save()
    inbound = make_inbound(tmp_path, memory)

    rejected, booking, active_agent = dispatch(
        "Nah, I do not want that one. Something else.",
        inbound,
        None,
        "inbound_agent",
    )
    accepted, booking, active_agent = dispatch(
        "Hostage then. Let's book it.",
        inbound,
        booking,
        active_agent,
    )

    assert "Murder Mystery" not in rejected.response
    assert memory.data["rejected_options"] == ["Murder Mystery"]
    assert memory.data["room"] == "Hostage"
    assert memory.data["recommended_option"] == "Hostage"
    assert active_agent == "booking_agent"
    assert "date" in accepted.response.lower()


def test_generic_to_booking_transition_preserves_couple_context_and_accepts_asr_confirmation(
    tmp_path: Path,
) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    inbound = make_inbound(tmp_path, memory)
    booking = None
    active_agent = "inbound_agent"

    for message in (
        "Hi, is this Breakout?",
        "We're a couple.",
        "It's our first escape room.",
    ):
        _result, booking, active_agent = dispatch(
            message, inbound, booking, active_agent
        )

    assert memory.data["participants"] == 2
    assert memory.data["relationship"] == "couple"
    assert memory.data["age_group"] == "adults"
    assert memory.data["experience_level"] == "beginner"

    for message in (
        "Whitefield works for us.",
        "What would you recommend?",
        "Okay let's do that.",
    ):
        result, booking, active_agent = dispatch(
            message, inbound, booking, active_agent
        )

    assert memory.data["room"] == "Murder Mystery"
    assert memory.data["recommended_option"] == "Murder Mystery"
    assert "date" in result.response.lower()


def test_short_asr_corrections_and_difficulty_preferences_are_persisted(
    tmp_path: Path,
) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data["intent"] = "escape_room_inquiry"
    memory.merge_message("6 people", "escape_room_inquiry")
    memory.merge_message("Actually make it 4", "escape_room_inquiry")
    memory.merge_message("We have played before", "escape_room_inquiry")
    memory.merge_message("I want something difficult", "escape_room_inquiry")

    assert memory.data["participants"] == 4
    assert memory.data["experience_level"] == "experienced"
    assert memory.data["challenge_preference"] == "challenging"

    inbound = make_inbound(tmp_path, memory)
    first, booking, active_agent = dispatch(
        "What about Whitefield?", inbound, None, "inbound_agent"
    )
    second, _booking, _active_agent = dispatch(
        "No, let us do Koramangala.", inbound, booking, active_agent
    )

    assert "Undercover" in first.response or "Bomb Defusal" in first.response
    assert memory.data["location"] == "Koramangala"
    assert memory.data["recommended_option"] == "Classified"
    assert memory.data["rejected_options"] == ["Undercover"]
    assert "Classified" in second.response


def test_existing_booking_confirmation_issue_escalates_on_talk_to_someone(
    tmp_path: Path,
) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    inbound = make_inbound(tmp_path, memory)
    booking = None
    active_agent = "inbound_agent"
    responses: list[str] = []

    for message in (
        "I booked yesterday.",
        "I think something is wrong.",
        "I never received my confirmation.",
        "I already paid.",
        "This is frustrating.",
        "Can I talk to someone?",
    ):
        result, booking, active_agent = dispatch(
            message, inbound, booking, active_agent
        )
        responses.append(result.response)

    assert len(set(responses[:4])) == 4
    assert memory.data["support_context"] == {
        "existing_booking": True,
        "booked_when": "yesterday",
        "issue_reported": True,
        "confirmation_received": False,
        "payment_reported": "paid",
    }
    assert result.should_handoff is True
    assert result.escalation["escalate"] is True
    assert result.escalation["trigger"] == "human_request"
    assert result.handoff_summary["support_context"]["payment_reported"] == "paid"
    assert result.handoff_summary["customer_concerns"]
    assert result.handoff_summary["action_items"]
    assert result.timeline_events
    assert result.customer_profile
    assert "confirmation" in result.ai_summary["summary"].lower()


def test_participant_correction_is_not_misread_as_customer_name(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data.update({"participants": 6, "customer_name": "", "first_name": "", "last_name": ""})
    memory.save()
    agent = BookingAgent(memory)

    agent.handle_message("Actually make it four.")

    assert memory.data["participants"] == 4
    assert memory.data["customer_name"] == ""
    assert memory.data["first_name"] == ""


def test_all_adults_stays_with_booking_agent_waiting_for_age(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data.update(
        {
            "age_group": "",
            "selected_slot": "6:30 PM",
            "booking_started": True,
            "current_workflow": "booking",
            "customer_name": "",
            "phone": "",
        }
    )
    memory.save()
    inbound = make_inbound(tmp_path, memory)
    booking = BookingAgent(memory)
    booking._state = booking._STATE_WAITING_FOR_AGE
    booking._selected_slot = "6:30 PM"

    result, booking, active_agent = dispatch(
        "All adults.", inbound, booking, "booking_agent"
    )

    assert active_agent == "booking_agent"
    assert memory.data["age_group"] == "adults"
    assert booking._state == booking._STATE_WAITING_FOR_FIRST_NAME
    assert "name" in result.response.lower()


def test_closest_to_time_selects_nearest_live_option(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data.update(
        {
            "age_group": "adults",
            "selected_slot": "",
            "customer_name": "",
            "phone": "",
        }
    )
    memory.save()
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["5:15 PM", "6:30 PM", "7:45 PM", "9:00 PM"]
    agent._last_availability = {
        "available": True,
        "verified": True,
        "slots": agent._available_slots,
    }

    result = agent.handle_message("I'll take whichever is closest to seven.")

    assert memory.data["selected_slot"] == "6:30 PM"
    assert agent._state == agent._STATE_WAITING_FOR_FIRST_NAME
    assert "name" in result.response.lower()


def test_unavailable_slot_hypothetical_answers_without_losing_selection(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data.update({"age_group": "", "selected_slot": "6:30 PM"})
    memory.save()
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_AGE
    agent._selected_slot = "6:30 PM"
    agent._available_slots = ["5:15 PM", "6:30 PM", "7:45 PM", "9:00 PM"]

    result = agent.handle_message("What if that slot is unavailable?")

    assert "won't switch you silently" in result.response
    assert "nearest verified alternatives" in result.response
    assert "adults, kids, or a mix" in result.response
    assert memory.data["selected_slot"] == "6:30 PM"
    assert agent._state == agent._STATE_WAITING_FOR_AGE


def test_second_booking_preserves_customer_contact_and_first_timer_context(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update({
        "customer_name": "Siddharth Khandelwal",
        "first_name": "Siddharth",
        "last_name": "Khandelwal",
        "phone": "9982151357",
        "age_group": "adults",
        "experience_level": "beginner",
        "room": "Hostage",
        "preferred_date": "Tomorrow",
        "participants": 2,
        "booking_started": True,
    })
    inbound = make_inbound(tmp_path, memory)

    _start_additional_booking(inbound)

    assert memory.data["customer_name"] == "Siddharth Khandelwal"
    assert memory.data["phone"] == "9982151357"
    assert memory.data["age_group"] == "adults"
    assert memory.data["experience_level"] == "beginner"
    assert memory.data["room"] == ""
    assert memory.data["previous_bookings"][0]["room"] == "Hostage"


def test_second_booking_something_for_people_triggers_fresh_room_recommendation(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update({
        "intent": "escape_room_inquiry",
        "participants": 3,
        "age_group": "adults",
        "experience_level": "beginner",
        "location": "Whitefield",
        "preferred_date": "Tomorrow",
        "last_discussed_topic": "locations",
    })
    memory.save()

    result = ConversationGuard(memory).evaluate("Whitefield, tomorrow. Something for 3 people this time.", "inbound_agent")

    assert result is not None
    assert "Koramangala" not in result.response
    assert memory.data["recommended_option"] in {"Murder Mystery", "Hostage", "Undercover"}


def test_frustration_trajectory_detects_recommendation_loop_complaints(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    agent = SentimentAgent(memory)

    first = agent.analyze("I don't like any of these. You keep suggesting the same things.", stage="sales")
    second = agent.analyze("This is really frustrating. I've been going in circles.", stage="sales")

    assert first.sentiment == "frustrated"
    assert second.sentiment == "frustrated"
    assert second.to_dict()["frustration_score"] >= first.to_dict()["frustration_score"]
    assert "Repeated recommendation loop" in second.to_dict()["frustration_reasons"]


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
