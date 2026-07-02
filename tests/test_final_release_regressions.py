from __future__ import annotations

from argparse import Namespace
from datetime import date
from pathlib import Path
import sys
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import app as api_app
import main as main_module
from main import build_inbound_agent, dispatch
from src.agents.booking_agent import BookingAgent
from src.core.agent_response import AgentResponse
from src.integrations.kreeda.agent_contract_provider import AgentContractProvider
from src.integrations.kreeda.breakout_booking_provider import BreakoutBookingProvider
from src.memory.conversation_memory import ConversationMemory
from src.orchestration.booking_orchestrator import BookingOrchestrator
from src.response_composer import ResponseComposer
from src.services.recommendation_engine import RecommendationEngine


def _memory(tmp_path: Path, **updates) -> ConversationMemory:
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update({
        "intent": "escape_room_inquiry",
        "event_type": "Escape Room",
        "booking_started": True,
        "current_workflow": "booking",
        "participants": 4,
        "age_group": "adults",
        "location": "JP Nagar",
        "preferred_date": "2026-06-25",
        "room": "Murder Mystery",
        "time_preference": "any",
    })
    memory.data.update(updates)
    memory.save()
    return memory


def _provider() -> MagicMock:
    provider = MagicMock(spec=BreakoutBookingProvider)
    provider.get_booking_venues.return_value = [{"venueId": "jp", "name": "JP Nagar"}]
    provider.get_booking_games.return_value = [{
        "gameId": "mm", "name": "Murder Mystery", "peopleMin": 2, "peopleMax": 7,
        "peopleCategories": [{"categoryId": "adult", "categoryName": "Adults", "max": 7}],
    }]
    provider.search_booking_slots.return_value = [{
        "gameId": "mm", "date": "2026-06-25", "time": "19:00",
        "available": 7, "isAvailable": True,
    }]
    provider.create_instant_cart.return_value = {"cartId": "cart-1"}
    return provider


def test_confirmed_provider_response_requires_independent_reference(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    provider = _provider()
    provider.create_confirmed_booking.return_value = {
        "bookingId": "booking-without-ref", "status": "CONFIRMED",
    }
    orchestrator = BookingOrchestrator(
        booking_provider=provider,
        contract_provider=MagicMock(spec=AgentContractProvider),
    )
    memory = _memory(
        tmp_path,
        selected_slot="7:00 PM",
        customer_name="Riya Patel",
        first_name="Riya",
        last_name="Patel",
        phone="9876543210",
    )
    availability = orchestrator.check_availability("JP Nagar", "2026-06-25", 4, "Murder Mystery")
    agent = BookingAgent(memory, orchestrator=orchestrator)
    agent.response_composer.enabled = False
    agent._state = agent._STATE_READY_FOR_BOOKING
    agent._selected_slot = "7:00 PM"
    agent._available_slots = availability["slots"]
    agent._last_availability = availability

    result = agent.handle_message("continue")

    assert result.booking_result is None or not result.booking_result.get("confirmed")
    assert memory.data.get("booking_id", "") == ""
    assert memory.data.get("booking_ref", "") == ""
    assert "confirmed" not in result.response.lower() or "couldn't confirm" in result.response.lower()


def test_response_composer_requires_both_booking_identifiers() -> None:
    assert ResponseComposer._has_unverified_booking_claim(
        "Your booking is confirmed.", {"booking_id": "id-only", "booking_ref": ""}
    )
    assert not ResponseComposer._has_unverified_booking_claim(
        "Your booking is confirmed.", {"booking_id": "id", "booking_ref": "ref"}
    )


def test_day_after_tomorrow_normalizes_to_plus_two() -> None:
    reference = date(2026, 6, 22)
    assert BookingOrchestrator._normalise_date("Day After Tomorrow", reference) == "2026-06-24"


def test_past_month_day_is_rejected_instead_of_silently_rolling_year() -> None:
    assert BookingOrchestrator._normalise_date("1 January", date(2026, 6, 22)) == ""


def test_ambiguous_location_requires_clarification(tmp_path) -> None:
    memory = ConversationMemory(tmp_path / "location.json")
    assert memory._extract_location("whitefield or koramangala") == ""


def test_booking_agent_restart_revalidates_and_completes_persisted_slot(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    provider = _provider()
    provider.create_confirmed_booking.return_value = {
        "bookingId": "booking-restart", "orderId": "reference-restart", "status": "CONFIRMED",
    }
    memory = _memory(
        tmp_path,
        selected_slot="7:00 PM",
        customer_name="Riya Patel",
        first_name="Riya",
        last_name="Patel",
        phone="9876543210",
    )
    reloaded = ConversationMemory(memory.session_path)
    agent = BookingAgent(
        reloaded,
        orchestrator=BookingOrchestrator(
            booking_provider=provider,
            contract_provider=MagicMock(spec=AgentContractProvider),
        ),
    )
    agent.response_composer.enabled = False

    result = agent.handle_message("continue")

    assert provider.search_booking_slots.called
    assert provider.create_confirmed_booking.called
    assert reloaded.data["booking_id"] == "booking-restart"
    assert reloaded.data["booking_ref"] == "reference-restart"
    assert "reference-restart" in result.response


def test_handoff_summary_failure_never_suppresses_escalation(tmp_path, monkeypatch) -> None:
    memory = ConversationMemory(tmp_path / "handoff.json")
    inbound = build_inbound_agent(Namespace(model=None, no_openai=True), memory)
    monkeypatch.setattr(
        main_module.HandoffSummaryAgent,
        "generate",
        MagicMock(side_effect=RuntimeError("summary unavailable")),
    )

    result, _, _ = dispatch("Someone got injured", inbound, None, "inbound_agent")

    assert result.escalation["escalate"] is True
    assert result.next_agent == "escalation_agent"
    assert result.handoff_summary["summary"]


def test_short_human_transfer_requests_escalate_through_dispatch(tmp_path) -> None:
    for idx, message in enumerate(("transfer me", "I don't want AI")):
        memory = ConversationMemory(tmp_path / f"handoff-short-{idx}.json")
        inbound = build_inbound_agent(Namespace(model=None, no_openai=True), memory)

        result, _, _ = dispatch(message, inbound, None, "inbound_agent")

        assert result.should_handoff is True
        assert result.next_agent == "escalation_agent"
        assert result.escalation["escalate"] is True
        assert result.escalation["trigger"] == "human_request"
        assert result.handoff_summary["summary"]


def test_frustration_escalation_does_not_capture_complaint_as_name(tmp_path) -> None:
    memory = ConversationMemory(tmp_path / "frustration.json")
    inbound = build_inbound_agent(Namespace(model=None, no_openai=True), memory)

    result, _, _ = dispatch("This is useless", inbound, None, "inbound_agent")

    assert result.should_handoff is True
    assert result.next_agent == "escalation_agent"
    assert result.escalation["escalate"] is True
    assert "connect you" in result.response.lower()
    assert not memory.data.get("customer_name")
    assert "useless" not in result.response.lower()


def test_escalation_state_remains_active_after_handoff(tmp_path) -> None:
    memory = ConversationMemory(tmp_path / "sticky-escalation.json")
    inbound = build_inbound_agent(Namespace(model=None, no_openai=True), memory)

    first, booking, active = dispatch("I want a human", inbound, None, "inbound_agent")
    second, _, _ = dispatch("I also need parking details", inbound, booking, active)

    assert first.escalation["escalate"] is True
    assert second.escalation["escalate"] is True
    assert second.next_agent == "escalation_agent"
    assert "connect you" in second.response.lower()
    assert memory.data["escalation_state"]["escalate"] is True


def test_chat_response_exposes_handoff_metadata(monkeypatch) -> None:
    api_app._sessions.clear()
    fake = AgentResponse(
        response="Connecting you now.",
        intent="general_faq",
        next_agent="escalation_agent",
        should_handoff=True,
        escalation={"escalate": True, "reason": "human request"},
        handoff_summary={"summary": "Customer requested a human."},
    )
    monkeypatch.setattr(api_app, "dispatch", MagicMock(return_value=(fake, None, "inbound_agent")))
    response = TestClient(api_app.app).post(
        "/chat", json={"session_id": "handoff-api", "message": "I want a human"}
    )
    payload = response.json()
    assert payload["next_agent"] == "escalation_agent"
    assert payload["escalation"]["escalate"] is True
    assert payload["handoff_summary"]["summary"]


def test_large_group_enters_coordination_and_collects_contact(tmp_path) -> None:
    memory = _memory(tmp_path, participants=20, customer_name="", phone="")
    agent = BookingAgent(memory)
    agent.response_composer.enabled = False
    agent.availability_tool.check = MagicMock(return_value={
        "available": True,
        "verified": True,
        "slots": ["7:00 PM"],
        "capacity_supported": False,
        "max_available_capacity": 8,
    })

    first = agent.handle_message("ready")
    second = agent.handle_message("Riya")
    final = agent.handle_message("9876543210")

    assert "events team" in first.response.lower()
    assert "phone" in second.response.lower()
    assert memory.data["coordination_ready"] is True
    assert final.next_agent == "events_team"
    assert final.should_handoff is True
    assert not memory.data.get("booking_id")


def test_corporate_event_never_calls_single_room_availability(tmp_path) -> None:
    memory = _memory(
        tmp_path,
        intent="corporate_event",
        event_type="Corporate Event",
        room="",
        company_size=30,
        customer_name="Riya",
        phone="9876543210",
    )
    agent = BookingAgent(memory)
    agent.response_composer.enabled = False
    agent.availability_tool.check = MagicMock()

    result = agent.handle_message("ready")

    agent.availability_tool.check.assert_not_called()
    assert result.next_agent == "events_team"
    assert "not a single-room booking" in result.response


def test_recommendation_engine_routes_corporate_and_large_groups_separately() -> None:
    engine = RecommendationEngine()
    corporate = engine.recommend("team outing", {
        "intent": "corporate_event", "company_size": 30, "participants": 30,
        "location": "Whitefield", "preferred_date": "25 June",
    })
    large = engine.recommend("escape room", {
        "intent": "escape_room_inquiry", "participants": 15,
        "location": "Whitefield", "age_group": "adults",
    })
    assert corporate.option == "Corporate event coordination"
    assert large.option == "Multi-room event coordination"


def test_slot_summary_contracts(tmp_path) -> None:
    memory = _memory(tmp_path, selected_slot="")
    agent = BookingAgent(memory)
    agent.response_composer.enabled = False
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["8:00 PM", "10:00 AM", "6:00 PM", "3:00 PM"]

    all_slots = agent.handle_message("show all slots").response
    evening = agent.handle_message("only evening slots").response
    earliest = agent.handle_message("what is the first available slot?").response

    assert all(slot in all_slots for slot in agent._available_slots)
    assert "6:00 PM" in evening and "8:00 PM" in evening and "10:00 AM" not in evening
    assert "10:00 AM" in earliest and "earliest" in earliest.lower()


def test_cancel_without_reference_waits_for_reference_and_validates_result(tmp_path) -> None:
    memory = _memory(tmp_path, booking_ref="")
    agent = BookingAgent(memory)
    agent.response_composer.enabled = False
    agent.orchestrator.cancel_booking = MagicMock(return_value={"status": "cancelled"})

    first = agent.handle_message("Cancel my reservation")
    second = agent.handle_message("BRK-12345")

    assert "share your booking id" in first.response.lower()
    assert "cancelled" not in first.response.lower()
    agent.orchestrator.cancel_booking.assert_called_once_with("BRK-12345", reason="customer request")
    assert "successfully cancelled" in second.response.lower()


def test_repeated_unclassified_input_does_not_escalate_without_customer_complaint(tmp_path) -> None:
    memory = ConversationMemory(tmp_path / "loop.json")
    inbound = build_inbound_agent(Namespace(model=None, no_openai=True), memory)
    booking = None
    active = "inbound_agent"
    final = None
    for _ in range(3):
        final, booking, active = dispatch("blah blah blah xyz", inbound, booking, active)
    assert final is not None
    assert final.next_agent != "escalation_agent"
    assert not final.escalation["escalate"]

    complained, booking, active = dispatch("You keep repeating yourself.", inbound, booking, active)

    assert complained.next_agent == "escalation_agent"
    assert complained.escalation["reason"] == "Repeated misunderstanding reported by customer"
