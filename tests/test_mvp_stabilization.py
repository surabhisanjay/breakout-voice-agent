from __future__ import annotations

import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.booking_agent import BookingAgent
from src.agents.inbound_agent import InboundAgent
from src.knowledge.knowledge_loader import KnowledgeLoader
from src.memory.conversation_memory import ConversationMemory
from src.integrations.kreeda.agent_contract_provider import AgentContractProvider
from src.integrations.kreeda.breakout_api import BreakoutAPIError
from src.integrations.kreeda.breakout_booking_provider import BreakoutBookingProvider
from src.orchestration.booking_orchestrator import BookingOrchestrator
from src.orchestration.conversation_manager import ConversationManager
from src.response_composer import ResponseComposer
from src.services.recommendation_engine import RecommendationEngine


def make_inbound(tmp_path: Path) -> InboundAgent:
    return InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=ConversationMemory(tmp_path / "session.json"),
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


def test_named_session_replay_fixes_name_faq_options_and_date(tmp_path: Path) -> None:
    agent = make_inbound(tmp_path)
    turns = [
        "The four friends want to book an escape room.",
        "Whitefield location.",
        "This is the first time.",
        "Tell me more options.",
        "What are all available?",
        "We're all adults.",
        "Twenty fourth of June.",
    ]
    responses = [agent.handle_message(turn).response for turn in turns]

    assert agent.memory.data["customer_name"] == ""
    assert agent.memory.data["experience_level"] == "beginner"
    assert agent.memory.data["preferred_date"] == "24 June"
    assert "Undercover" in responses[3] and "Hostage" in responses[3]
    assert "rooms currently matching" in responses[4].lower()
    assert "age group" not in responses[4].lower()


def test_date_variants_are_extracted_persisted_and_logged(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    for index, phrase in enumerate(("24 June", "Twenty fourth of June", "Tomorrow", "Next Friday")):
        memory = ConversationMemory(tmp_path / f"date-{index}.json")
        memory.merge_message(phrase, "escape_room_inquiry", expected_field="preferred_date")
        assert memory.data["preferred_date"]

    messages = [record.getMessage() for record in caplog.records]
    assert any(message.startswith("DATE_EXTRACTED=") for message in messages)
    assert any(message.startswith("DATE_NORMALIZED=") for message in messages)
    assert any(message.startswith("DATE_PERSISTED=") for message in messages)


def test_name_extraction_rejects_first_time_and_accepts_explicit_name(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    memory = ConversationMemory(tmp_path / "name.json")

    memory.merge_message("This is my first time", "escape_room_inquiry")
    assert memory.data["customer_name"] == ""
    memory.merge_message("My name is Sadart", "escape_room_inquiry")
    assert memory.data["customer_name"] == "Sadart"

    messages = [record.getMessage() for record in caplog.records]
    assert "NAME_REJECTED=My First Time" in messages
    assert "NAME_ACCEPTED=Sadart" in messages


def test_booking_does_not_start_from_participant_count_only(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "state.json")
    memory.merge_message("Book for 2 people", "escape_room_inquiry")

    assert memory.handoff_ready("escape_room_inquiry") is False
    assert memory.data["room"] == ""
    assert memory.data["preferred_date"] == ""


def test_room_and_slot_remain_in_booking_agent(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "routing.json")
    target, category = ConversationManager(memory).determine_routing(
        "Book Undercover at 8:20 PM", "booking_agent"
    )
    assert (target, category) == ("booking_agent", "continuing_workflow")


def test_acceptance_a_first_time_recommendation(tmp_path: Path) -> None:
    agent = make_inbound(tmp_path)
    response = agent.handle_message("We are 4 adults and it is our first time.").response
    assert response == "Which location would you like to visit?"
    assert agent.memory.data["participants"] == 4
    assert agent.memory.data["experience_level"] == "beginner"


def test_acceptance_c_booking_persists_real_provider_reference(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "booking.json")
    memory.data.update({
        "intent": "escape_room_inquiry", "event_type": "Escape Room",
        "participants": 4, "age_group": "adults", "location": "Whitefield",
        "preferred_date": "24 June", "room": "Murder Mystery",
        "customer_name": "Sadart Rao", "phone": "9983340357",
    })
    memory.save()
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["8:20 PM"]
    agent._last_availability = {"available": True, "verified": True, "slots": ["8:20 PM"]}
    agent.booking_tool.create = MagicMock(return_value={
        "booking_id": "bk-mvp-123", "booking_reference": "ref-mvp-123",
        "confirmed": True, "location": "Whitefield", "date": "24 June",
        "slot": "8:20 PM", "participants": 4, "event_type": "Escape Room",
        "customer_name": "Sadart Rao", "phone": "9983340357",
    })

    result = agent.handle_message("8:20 PM")

    assert result.booking_result["booking_id"] == "bk-mvp-123"
    assert memory.data["booking_id"] == "bk-mvp-123"
    assert memory.data["booking_ref"] == "ref-mvp-123"


def test_acceptance_d_faq_interrupt_preserves_slot_state(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "faq.json")
    memory.data.update({
        "participants": 4, "age_group": "adults", "location": "Whitefield",
        "preferred_date": "24 June", "room": "Murder Mystery",
    })
    memory.save()
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["3:00 PM"]

    response = agent.handle_message("What is the cancellation policy?").response

    assert "cancellation" in response.lower()
    assert "3:00 PM" in response
    assert agent._state == agent._STATE_WAITING_FOR_SLOT


def test_acceptance_e_and_f_explicit_date_and_room_changes_are_persisted(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "changes.json")
    memory.data.update({"preferred_date": "20 June", "room": "Murder Mystery"})

    assert memory.set_field("preferred_date", "23 June", "Change the date to 23 June")
    assert memory.set_field("room", "Hostage", "Switch the room to Hostage")
    assert memory.data["preferred_date"] == "23 June"
    assert memory.data["room"] == "Hostage"
    assert memory.data["pending_confirmation"] is None


def test_acceptance_g_large_group_warns_before_room_recommendation() -> None:
    recommendation = RecommendationEngine().recommend("What do you recommend?", {
        "intent": "escape_room_inquiry", "participants": 15,
        "age_group": "adults", "location": "Whitefield",
    })
    assert recommendation.option == "Multi-room event coordination"
    assert "Bomb Defusal" not in recommendation.option


def test_acceptance_h_birthday_package_does_not_call_kreeda(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "birthday.json")
    memory.data.update({
        "intent": "birthday_party", "event_type": "Birthday Package",
        "participants": 15, "location": "Whitefield", "preferred_date": "24 June",
        "recommended_option": "Escape Rooms and Scavenger Hunt",
    })
    memory.save()
    agent = BookingAgent(memory)
    agent.availability_tool.check = MagicMock()

    result = agent.handle_message("ready")

    assert "package inquiry" in result.response.lower()
    assert "confirmed" not in result.response.lower()
    agent.availability_tool.check.assert_not_called()


def test_unverified_confirmation_is_blocked() -> None:
    assert ResponseComposer._has_unverified_booking_claim(
        "Your booking is confirmed and all set.", {"booking_id": "", "booking_ref": ""}
    )


def booking_memory(tmp_path: Path, participants: int = 4) -> ConversationMemory:
    memory = ConversationMemory(tmp_path / "active-booking.json")
    memory.data.update({
        "intent": "escape_room_inquiry", "event_type": "Escape Room",
        "participants": participants, "age_group": "adults", "location": "Whitefield",
        "preferred_date": "24 June", "room": "Murder Mystery",
        "current_workflow": "booking",
        "time_preference": "any",
    })
    memory.save()
    return memory


def test_participant_correction_overwrites_and_clears_stale_range(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "participants.json")
    memory.merge_message("10 people", "escape_room_inquiry")
    assert memory.data["participants"] == 10

    memory.merge_message("We are actually 7", "escape_room_inquiry")

    assert memory.data["participants"] == 7
    assert memory.data["participants_min"] == ""
    assert memory.data["participants_max"] == ""
    assert ConversationMemory(tmp_path / "participants.json").data["participants"] == 7


def test_price_question_stays_in_active_booking(tmp_path: Path) -> None:
    memory = booking_memory(tmp_path)
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["3:00 PM"]

    response = agent.handle_message("What is the price?").response

    assert "estimated total" in response.lower()
    assert agent._state == agent._STATE_WAITING_FOR_SLOT


def test_cached_slot_availability_question_does_not_select_slot(tmp_path: Path) -> None:
    memory = booking_memory(tmp_path)
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["3:00 PM", "5:00 PM"]
    agent._last_availability = {
        "available": True, "verified": True, "slots": ["3:00 PM", "5:00 PM"],
    }
    agent.booking_tool.create = MagicMock()

    response = agent.handle_message("Is 3 PM available?").response

    assert response == "Yes, 3:00 PM is available for Murder Mystery at Whitefield on 24 June."
    assert memory.data.get("selected_slot", "") == ""
    assert agent._state == agent._STATE_WAITING_FOR_SLOT
    agent.booking_tool.create.assert_not_called()


def test_booking_state_lock_ignores_faq_and_resumes_on_price(tmp_path: Path) -> None:
    import main as main_module

    memory = booking_memory(tmp_path)
    inbound = make_inbound(tmp_path)
    inbound.memory = memory
    inbound.qualification_agent.memory = memory
    booking = BookingAgent(memory)
    booking._state = booking._STATE_WAITING_FOR_SLOT
    booking._available_slots = ["3:00 PM", "5:00 PM"]
    booking._last_availability = {
        "available": True, "verified": True, "slots": ["3:00 PM", "5:00 PM"],
    }

    faq, booking, active = main_module.dispatch(
        "Where do we park?", inbound, booking, "booking_agent"
    )
    assert active == "booking_agent"
    assert "available slots" in faq.response.lower()
    assert "parking" in faq.response.lower()
    assert booking._state == booking._STATE_WAITING_FOR_SLOT
    assert booking._available_slots == ["3:00 PM", "5:00 PM"]

    price, booking, active = main_module.dispatch(
        "What is the price?", inbound, booking, active
    )
    assert active == "booking_agent"
    assert "estimated total" in price.response.lower()
    assert booking._available_slots == ["3:00 PM", "5:00 PM"]


def test_capacity_is_rechecked_after_participant_change(tmp_path: Path) -> None:
    memory = booking_memory(tmp_path, participants=10)
    assert ConversationManager(memory).determine_routing(
        "We are actually 7", "inbound_agent"
    ) == ("booking_agent", "continuing_workflow")
    agent = BookingAgent(memory)
    agent._state = agent._STATE_CLOSED
    agent._last_availability = {
        "available": True, "slots": ["3:00 PM"], "capacity_supported": False,
    }
    agent.availability_tool.check = MagicMock(return_value={
        "available": True, "verified": True, "slots": ["3:00 PM"],
        "capacity_supported": True, "bookable_slots": ["3:00 PM"],
    })

    response = agent.handle_message("We are actually 7").response

    assert memory.data["participants"] == 7
    agent.availability_tool.check.assert_called_once_with("Whitefield", "24 June", 7)
    assert agent._state == agent._STATE_WAITING_FOR_SLOT
    assert "for 7 players" in response


def test_labeled_name_and_contact_are_persisted_immediately(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    memory = ConversationMemory(tmp_path / "labelled-contact.json")

    memory.merge_message(
        "Name: Siddad Khanilwal. Contact number: 998223547. Please confirm booking.",
        "escape_room_inquiry",
    )

    assert memory.data["customer_name"] == "Siddad Khanilwal"
    assert memory.data["phone"] == "998223547"
    messages = [record.getMessage() for record in caplog.records]
    assert "CUSTOMER_NAME_EXTRACTED=Siddad Khanilwal" in messages
    assert "PHONE_EXTRACTED=998223547" in messages


def test_discount_question_never_returns_cached_slot(tmp_path: Path) -> None:
    memory = booking_memory(tmp_path)
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["7:00 PM"]
    agent._last_availability = {
        "available": True, "verified": True, "slots": ["7:00 PM"],
    }

    response = agent.handle_message("Is there any discount available?").response

    assert "10% off for 4 or more players" in response
    assert "7:00 PM is available" not in response
    assert agent._state == agent._STATE_WAITING_FOR_SLOT


def test_spoken_participant_override_is_logged_and_rechecked(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    memory = booking_memory(tmp_path, participants=10)
    agent = BookingAgent(memory)
    agent._state = agent._STATE_CLOSED
    agent.availability_tool.check = MagicMock(return_value={
        "available": True, "verified": True, "slots": ["7:00 PM"],
        "capacity_supported": True, "bookable_slots": ["7:00 PM"],
    })

    agent.handle_message("We are seven adults.")

    assert memory.data["participants"] == 7
    agent.availability_tool.check.assert_called_once_with("Whitefield", "24 June", 7)
    messages = [record.getMessage() for record in caplog.records]
    assert "PARTICIPANT_OVERRIDE=true" in messages
    assert "OLD_PARTICIPANTS=10" in messages
    assert "NEW_PARTICIPANTS=7" in messages


def test_jp_nagar_booking_executes_cart_and_booking_end_to_end(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.delenv("BOOKING_PROVIDER", raising=False)
    provider = MagicMock(spec=BreakoutBookingProvider)
    provider.get_booking_venues.return_value = [{"venueId": "jp-1", "name": "JP Nagar"}]
    provider.get_booking_games.return_value = [{
        "gameId": "murder-1", "name": "Murder Mystery", "peopleMin": 2, "peopleMax": 7,
        "peopleCategories": [{"categoryId": "adult", "categoryName": "Adults", "max": 7}],
    }]
    provider.search_booking_slots.return_value = [{
            "eventId": "event-7pm", "gameId": "murder-1", "date": "2026-06-25",
        "time": "19:00", "available": 7, "isAvailable": True,
    }]
    provider.create_instant_cart.return_value = {"cartId": "cart-e2e"}
    provider.create_confirmed_booking.return_value = {
        "bookingId": "booking-e2e", "orderId": "reference-e2e", "status": "CONFIRMED",
    }
    orchestrator = BookingOrchestrator(
        booking_provider=provider,
        contract_provider=MagicMock(spec=AgentContractProvider),
    )
    memory = ConversationMemory(tmp_path / "jp-e2e.json")
    memory.data.update({
        "intent": "escape_room_inquiry", "event_type": "Escape Room",
        "participants": 4, "age_group": "adults", "location": "JP Nagar",
        "preferred_date": "25 June", "room": "Murder Mystery",
        "preferred_time": "7:00 PM", "time_preference": "specific",
    })
    memory.save()
    agent = BookingAgent(memory, orchestrator=orchestrator)

    availability = agent.handle_message("ready")
    slot = agent.handle_message("7 PM")
    confirmation = agent.handle_message(
        "Name: Siddad Khanilwal. Phone: 9982235470. Please confirm booking."
    )

    assert "7:00 PM" in availability.response
    assert "name" in slot.response.lower()
    assert memory.data["selected_slot"] == "7:00 PM"
    assert memory.data["customer_name"] == "Siddad Khanilwal"
    assert memory.data["phone"] == "9982235470"
    provider.create_instant_cart.assert_called_once()
    provider.create_confirmed_booking.assert_called_once()
    assert memory.data["booking_id"] == "booking-e2e"
    assert memory.data["booking_ref"] == "reference-e2e"
    assert "reference-e2e" in confirmation.response
    messages = [record.getMessage() for record in caplog.records]
    assert any(message.startswith("BOOKING_GATE_CHECK") for message in messages)
    assert any(message.startswith("BOOKING_GATE_PASSED") for message in messages)
    assert not any(message.startswith("BOOKING_GATE_MISSING_FIELDS") for message in messages)


def test_single_word_name_proceeds_to_phone_without_last_name(tmp_path: Path) -> None:
    memory = booking_memory(tmp_path)
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["7:00 PM"]
    agent._last_availability = {
        "available": True, "verified": True, "slots": ["7:00 PM"],
    }
    agent.booking_tool.create = MagicMock()

    agent.handle_message("7 PM")
    phone_prompt = agent.handle_message("Sadad")

    assert memory.data["first_name"] == "Sadad"
    assert memory.data["last_name"] == ""
    assert memory.data["customer_name"] == "Sadad"
    assert agent._state == agent._STATE_WAITING_FOR_PHONE
    assert "phone" in phone_prompt.response.lower()
    assert "last name" not in phone_prompt.response.lower()
    agent.booking_tool.create.assert_not_called()


def test_orchestrator_allows_single_word_name_with_blank_last_name(monkeypatch) -> None:
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.delenv("BOOKING_PROVIDER", raising=False)
    provider = MagicMock(spec=BreakoutBookingProvider)
    provider.create_instant_cart.return_value = {"cartId": "cart-single-name"}
    provider.create_confirmed_booking.return_value = {
        "bookingId": "booking-single-name",
        "orderId": "reference-single-name",
        "status": "CONFIRMED",
    }
    orchestrator = BookingOrchestrator(
        booking_provider=provider,
        contract_provider=MagicMock(spec=AgentContractProvider),
    )
    orchestrator._location = {"venueId": "jp-1", "name": "JP Nagar"}
    orchestrator._game = {
        "gameId": "murder-1", "name": "Murder Mystery",
        "peopleCategories": [{"categoryId": "adult", "categoryName": "Adults", "max": 7}],
    }
    orchestrator._slot_lookup = {
        "19:00": {"gameId": "murder-1", "date": "2026-06-25", "time": "19:00"}
    }

    result = orchestrator.prepare_booking({
        "participants": 4, "age_group": "adults", "location": "JP Nagar",
        "room": "Murder Mystery",
        "preferred_date": "25 June", "customer_name": "Sadad",
        "first_name": "Sadad", "phone": "9982235470",
    }, "7:00 PM")

    assert result["confirmed"] is True
    assert result["booking_id"] == "booking-single-name"
    assert result["booking_reference"] == "reference-single-name"
    provider.create_instant_cart.assert_called_once()
    provider.create_confirmed_booking.assert_called_once()
    payload = provider.create_confirmed_booking.call_args.args[0]
    assert payload["customer"]["firstName"] == "Sadad"
    # P0 fix: empty last_name is now substituted with 'NA' so Kreeda API accepts it
    assert payload["customer"]["lastName"] == "NA"


def test_booking_failure_recovers_missing_last_name_and_retries(tmp_path: Path) -> None:
    memory = booking_memory(tmp_path)
    memory.data.update({
        "selected_slot": "7:00 PM", "customer_name": "Sadad Khanilwal",
        "first_name": "Sadad", "last_name": "Khanilwal", "phone": "9982235470",
        "_kreeda_cart_id": "cart-existing", "_kreeda_cart_signature": "signature-existing",
    })
    memory.save()
    agent = BookingAgent(memory)
    agent._state = agent._STATE_READY_FOR_BOOKING
    agent._selected_slot = "7:00 PM"
    agent._available_slots = ["7:00 PM"]
    agent._last_availability = {
        "available": True, "verified": True, "slots": ["7:00 PM"],
    }
    agent.booking_tool.create = MagicMock(side_effect=[
        {
            "booking_id": "", "confirmed": False,
            "error": "Missing required parameter: customer.lastName",
            "missing_field": "last_name",
        },
        {
            "booking_id": "booking-retry", "booking_reference": "reference-retry",
            "confirmed": True, "location": "Whitefield", "date": "24 June",
            "slot": "7:00 PM", "participants": 4, "event_type": "Escape Room",
            "customer_name": "Sadad Patel", "phone": "9982235470",
        },
    ])

    # P0 fix: the agent now auto-fills 'NA' for last_name and retries immediately.
    # The customer is NOT asked for a last name. The booking should succeed on retry.
    result = agent.handle_message("retry")
    assert "last name" not in result.response.lower(), (
        f"Agent should auto-retry with NA, not ask for last name. Got: {result.response!r}"
    )
    # Agent must not enter WAITING_FOR_LAST_NAME state
    assert agent._state != agent._STATE_WAITING_FOR_LAST_NAME, (
        "Agent must not enter WAITING_FOR_LAST_NAME state"
    )
    # last_name in memory should be 'NA' after the auto-fill
    assert memory.data.get("last_name") == "NA"


def test_cart_is_reused_after_last_name_api_failure(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.delenv("BOOKING_PROVIDER", raising=False)
    provider = MagicMock(spec=BreakoutBookingProvider)
    provider.get_booking_venues.return_value = [{"venueId": "jp-1", "name": "JP Nagar"}]
    provider.get_booking_games.return_value = [{
        "gameId": "murder-1", "name": "Murder Mystery", "peopleMin": 2, "peopleMax": 7,
        "peopleCategories": [{"categoryId": "adult", "categoryName": "Adults", "max": 7}],
    }]
    provider.search_booking_slots.return_value = [{
            "eventId": "event-7pm", "gameId": "murder-1", "date": "2026-06-25",
        "time": "19:00", "available": 7, "isAvailable": True,
    }]
    provider.create_instant_cart.return_value = {"cartId": "cart-reused"}
    provider.create_confirmed_booking.side_effect = [
        BreakoutAPIError(
            "Missing required parameter: customer.lastName", code="MISSING_PARAM", status=400
        ),
        {"bookingId": "booking-after-retry", "orderId": "reference-after-retry", "status": "CONFIRMED"},
    ]
    orchestrator = BookingOrchestrator(
        booking_provider=provider,
        contract_provider=MagicMock(spec=AgentContractProvider),
    )
    memory = ConversationMemory(tmp_path / "cart-retry.json")
    memory.data.update({
        "intent": "escape_room_inquiry", "event_type": "Escape Room",
        "participants": 4, "age_group": "adults", "location": "JP Nagar",
        "preferred_date": "25 June", "room": "Murder Mystery",
        "selected_slot": "7:00 PM", "customer_name": "Sadad Khanilwal",
        "first_name": "Sadad", "last_name": "Khanilwal", "phone": "9982235470",
    })
    memory.save()
    availability = orchestrator.check_availability("JP Nagar", "25 June", 4, "Murder Mystery")
    agent = BookingAgent(memory, orchestrator=orchestrator)
    agent._state = agent._STATE_READY_FOR_BOOKING
    agent._selected_slot = "7:00 PM"
    agent._available_slots = availability["slots"]
    agent._last_availability = availability

    # P0 fix: instead of asking customer for last name, agent auto-fills 'NA' and retries.
    # The 'failure' response should be the booking CONFIRMATION (retry succeeded).
    failure = agent.handle_message("retry")
    assert "last name" not in failure.response.lower(), (
        f"Agent should auto-retry with NA, not ask for last name. Got: {failure.response!r}"
    )
    assert memory.data["selected_slot"] == "7:00 PM"
    assert memory.data["_kreeda_cart_id"] == "cart-reused"
    # After auto-retry, booking should be confirmed
    assert memory.data.get("booking_ref") == "reference-after-retry"
    assert "reference-after-retry" in failure.response


def test_booking_routing_acceptance_a_through_f(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    import main as main_module

    caplog.set_level(logging.INFO)
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.delenv("BOOKING_PROVIDER", raising=False)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    provider = MagicMock(spec=BreakoutBookingProvider)
    provider.get_booking_venues.return_value = [{"venueId": "jp-1", "name": "JP Nagar"}]
    provider.get_booking_games.return_value = [{
        "gameId": "murder-1", "name": "Murder Mystery", "peopleMin": 2, "peopleMax": 7,
        "peopleCategories": [{"categoryId": "adult", "categoryName": "Adults", "max": 7}],
    }]
    provider.search_booking_slots.return_value = [{
        "eventId": "event-7pm", "gameId": "murder-1", "date": tomorrow,
        "time": "19:00", "available": 7, "isAvailable": True,
    }]
    provider.create_instant_cart.return_value = {"cartId": "cart-routing"}
    provider.create_confirmed_booking.return_value = {
        "bookingId": "booking-routing", "orderId": "reference-routing", "status": "CONFIRMED",
    }
    orchestrator = BookingOrchestrator(
        booking_provider=provider,
        contract_provider=MagicMock(spec=AgentContractProvider),
    )

    def booking_factory(memory: ConversationMemory) -> BookingAgent:
        agent = BookingAgent(memory, orchestrator=orchestrator)
        agent.response_composer.enabled = False
        return agent

    monkeypatch.setattr(main_module, "BookingAgent", booking_factory)

    # Acceptance A: booking signal with room/location asks only for date.
    inbound_a = make_inbound(tmp_path / "case-a")
    result_a, _, active_a = main_module.dispatch(
        "Book Murder Mystery at JP Nagar.", inbound_a, None, "inbound_agent"
    )
    assert active_a == "booking_agent"
    assert "what date" in result_a.response.lower()
    assert "investigation-style" not in result_a.response.lower()

    # Acceptance B: a supplied date now asks for time preference before availability.
    inbound_b = make_inbound(tmp_path / "case-b")
    result_b, _, active_b = main_module.dispatch(
        "Book Murder Mystery at JP Nagar tomorrow.", inbound_b, None, "inbound_agent"
    )
    assert active_b == "booking_agent"
    assert "what time works best" in result_b.response.lower()

    # Acceptance C-F: slot -> name -> phone -> confirmation.
    inbound = make_inbound(tmp_path / "case-c-f")
    result_c, booking, active = main_module.dispatch(
        "Book Murder Mystery at JP Nagar for 4 adults tomorrow at 7 PM.",
        inbound,
        None,
        "inbound_agent",
    )
    assert active == "booking_agent"
    assert "name" in result_c.response.lower()
    assert "last name" not in result_c.response.lower()
    assert inbound.memory.data["selected_slot"] == "7:00 PM"

    result_d, booking, active = main_module.dispatch(
        "My first name is Siddharth", inbound, booking, active
    )
    assert "phone" in result_d.response.lower()
    assert "last name" not in result_d.response.lower()
    assert inbound.memory.data["first_name"] == "Siddharth"
    assert inbound.memory.data["customer_name"] == "Siddharth"

    result_f, booking, active = main_module.dispatch("9982254357", inbound, booking, active)
    assert inbound.memory.data["phone"] == "9982254357"
    assert inbound.memory.data["booking_id"] == "booking-routing"
    assert inbound.memory.data["booking_ref"] == "reference-routing"
    assert "reference-routing" in result_f.response

    messages = [record.getMessage() for record in caplog.records]
    required_logs = (
        "BOOKING_AGENT_SELECTED", "BOOKING_STARTED=true", "NAME_PERSISTED",
        "PHONE_PERSISTED", "KREEDA_CREATE_CART_SUCCESS", "KREEDA_CREATE_BOOKING_SUCCESS",
        "BOOKING_ID=booking-routing", "BOOKING_REF=reference-routing",
    )
    for marker in required_logs:
        assert any(marker in message for message in messages), marker
