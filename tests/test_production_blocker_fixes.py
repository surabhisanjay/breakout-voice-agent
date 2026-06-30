from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import sys
from unittest.mock import MagicMock

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import main as main_module
from main import build_inbound_agent, dispatch
from src.agents.booking_agent import BookingAgent
from src.agents.escalation_agent import EscalationAgent
from src.agents.sentiment_agent import SentimentAgent, SentimentResult
from src.integrations.kreeda.agent_contract_provider import AgentContractProvider
from src.integrations.kreeda.breakout_booking_provider import BreakoutBookingProvider
from src.memory.conversation_memory import ConversationMemory
from src.orchestration.booking_orchestrator import BookingOrchestrator


def _booking_memory(tmp_path: Path) -> ConversationMemory:
    memory = ConversationMemory(tmp_path / "booking.json")
    memory.data.update({
        "intent": "escape_room_inquiry",
        "event_type": "Escape Room",
        "booking_started": True,
        "current_workflow": "booking",
        "participants": 4,
        "age_group": "adults",
        "location": "JP Nagar",
        "preferred_date": "Tomorrow",
        "room": "Murder Mystery",
        "selected_slot": "7:00 PM",
    })
    memory.save()
    return memory


def _booking_agent_with_new_slots(tmp_path: Path) -> BookingAgent:
    memory = _booking_memory(tmp_path)
    agent = BookingAgent(memory)
    agent.response_composer.enabled = False
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._selected_slot = "7:00 PM"
    agent._available_slots = ["7:00 PM"]
    agent._last_availability = {
        "available": True, "verified": True, "slots": ["7:00 PM"],
    }
    agent.availability_tool.check = MagicMock(return_value={
        "available": True,
        "verified": True,
        "capacity_supported": True,
        "slots": ["8:00 PM"],
    })
    return agent


def _inbound(tmp_path: Path):
    memory = ConversationMemory(tmp_path / "session.json")
    return build_inbound_agent(Namespace(model=None, no_openai=True), memory)


# Regression: date modification invalidates all slot/cart state.
def test_date_change_invalidates_selected_slot_and_cart(tmp_path: Path) -> None:
    agent = _booking_agent_with_new_slots(tmp_path)
    agent.memory.data.update({
        "_kreeda_cart_id": "cart-old",
        "_kreeda_cart_signature": "sig-old",
        "_booking_idempotency_key": "key-old",
    })

    agent.handle_message("Change the date to 25 June")

    assert agent.memory.data["selected_slot"] == ""
    assert agent._selected_slot == ""
    assert agent.memory.data["_kreeda_cart_id"] == ""
    assert agent.memory.data["_kreeda_cart_signature"] == ""
    assert agent.memory.data["_booking_idempotency_key"] == ""


# Acceptance A.
def test_acceptance_a_book_room_then_change_date_clears_slot(tmp_path: Path) -> None:
    agent = _booking_agent_with_new_slots(tmp_path)
    result = agent.handle_message("Actually, change the date to 25 June")

    assert agent.memory.data["preferred_date"] == "25 June"
    assert agent.memory.data["selected_slot"] == ""
    assert "8:00 PM" in result.response


# Regression: room modification invalidates all slot/cart state.
def test_room_change_invalidates_selected_slot_and_cart(tmp_path: Path) -> None:
    agent = _booking_agent_with_new_slots(tmp_path)
    agent.memory.data["_kreeda_cart_id"] = "cart-old"

    agent.handle_message("Switch the room to Hostage")

    assert agent.memory.data["selected_slot"] == ""
    assert agent._selected_slot == ""
    assert agent.memory.data["_kreeda_cart_id"] == ""


# Acceptance B.
def test_acceptance_b_book_room_then_change_room_clears_slot(tmp_path: Path) -> None:
    agent = _booking_agent_with_new_slots(tmp_path)
    agent.handle_message("We want Hostage instead")

    assert agent.memory.data["room"] == "Hostage"
    assert agent.memory.data["selected_slot"] == ""


# Regression: complaint clauses are not valid implicit names.
@pytest.mark.parametrize(
    "message",
    ("This is ridiculous and unacceptable", "I am frustrated because this is still not working"),
)
def test_complaint_text_is_not_extracted_as_name(tmp_path: Path, message: str) -> None:
    memory = ConversationMemory(tmp_path / "name.json")
    memory.merge_message(message, "general_faq")
    assert memory.data["customer_name"] == ""


# Acceptance C.
def test_acceptance_c_frustration_does_not_change_customer_name(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "name.json")
    memory.data["customer_name"] = "Riya Patel"
    memory.save()
    memory.merge_message("I'm frustrated", "general_faq")
    assert memory.data["customer_name"] == "Riya Patel"


# Regression: a plain refund action is an escalation trigger.
def test_plain_refund_request_triggers_escalation(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "refund.json")
    sentiment = SentimentAgent(memory).analyze("I want a refund")
    escalation = EscalationAgent(memory).evaluate("I want a refund", sentiment)
    assert escalation.escalate is True
    assert escalation.reason == "Customer requested a refund"


# Acceptance D.
def test_acceptance_d_refund_selects_escalation_agent(tmp_path: Path) -> None:
    result, _, _ = dispatch("I want a refund", _inbound(tmp_path), None, "inbound_agent")
    assert result.next_agent == "escalation_agent"
    assert result.should_handoff is True
    assert result.handoff_summary


# Regression: active injury language is an immediate escalation trigger.
def test_injury_triggers_safety_escalation(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "safety.json")
    sentiment = SentimentAgent(memory).analyze("Someone got injured")
    escalation = EscalationAgent(memory).evaluate("Someone got injured", sentiment)
    assert escalation.escalate is True
    assert "safety concern" in escalation.reason


# Acceptance E.
def test_acceptance_e_injury_selects_escalation_agent(tmp_path: Path) -> None:
    result, _, _ = dispatch("Someone got injured", _inbound(tmp_path), None, "inbound_agent")
    assert result.next_agent == "escalation_agent"
    assert result.should_handoff is True
    assert "urgent" in result.response.lower()


# Regression: direct want-human wording is detected.
def test_want_human_wording_is_detected(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "human.json")
    sentiment = SentimentAgent(memory).analyze("I want a human")
    escalation = EscalationAgent(memory).evaluate("I want a human", sentiment)
    assert escalation.escalate is True


# Acceptance F.
def test_acceptance_f_human_request_creates_complete_handoff(tmp_path: Path) -> None:
    inbound = _inbound(tmp_path)
    inbound.memory.data.update({
        "customer_name": "Riya Patel",
        "phone": "9876543210",
        "intent": "escape_room_inquiry",
        "room": "Murder Mystery",
        "location": "JP Nagar",
        "preferred_date": "Tomorrow",
        "selected_slot": "7:00 PM",
    })
    inbound.memory.save()

    result, _, _ = dispatch("I want a human", inbound, None, "inbound_agent")

    assert result.next_agent == "escalation_agent"
    assert result.handoff_summary
    assert result.handoff_summary["customer_name"] == "Riya Patel"
    assert result.handoff_summary["intent"] == "escape_room_inquiry"
    assert result.handoff_summary["selected_slot"] == "7:00 PM"
    assert result.handoff_summary["escalation_reason"]
    assert result.handoff_summary["summary"]


# Regression: a booking FAQ is answered without consuming the selected slot.
def test_booking_parking_faq_answers_and_preserves_state(tmp_path: Path) -> None:
    agent = _booking_agent_with_new_slots(tmp_path)
    agent._available_slots = ["7:00 PM"]

    result = agent.handle_message("Is parking available?")

    assert "parking" in result.response.lower()
    assert "7:00 PM" in result.response
    assert agent._state == agent._STATE_WAITING_FOR_SLOT
    assert agent.memory.data["selected_slot"] == "7:00 PM"


# Acceptance G.
def test_acceptance_g_booking_faq_answers_then_resumes(tmp_path: Path) -> None:
    memory = _booking_memory(tmp_path)
    memory.data["selected_slot"] = ""
    memory.save()
    inbound = _inbound(tmp_path / "inbound")
    inbound.memory = memory
    inbound.qualification_agent.memory = memory
    booking = BookingAgent(memory)
    booking.response_composer.enabled = False
    booking._state = booking._STATE_WAITING_FOR_SLOT
    booking._available_slots = ["7:00 PM"]

    result, booking, active = dispatch(
        "Is parking available?", inbound, booking, "booking_agent"
    )

    assert active == "booking_agent"
    assert "parking" in result.response.lower()
    assert "which time works best" in result.response.lower()
    assert booking._state == booking._STATE_WAITING_FOR_SLOT


# Regression: DNS timeout never waits for the worker during pool shutdown.
def test_agent_contract_dns_timeout_uses_nonblocking_shutdown(monkeypatch) -> None:
    import src.integrations.kreeda.agent_contract_provider as contract_module

    shutdown_calls: list[tuple[bool, bool]] = []

    class TimeoutFuture:
        def result(self, timeout):
            raise contract_module.concurrent.futures.TimeoutError()

    class FakePool:
        def __init__(self, max_workers):
            pass

        def submit(self, *args, **kwargs):
            return TimeoutFuture()

        def shutdown(self, wait, cancel_futures):
            shutdown_calls.append((wait, cancel_futures))

    monkeypatch.setattr(contract_module.concurrent.futures, "ThreadPoolExecutor", FakePool)
    assert contract_module._dns_check("stalled.example", timeout=0.01) is False
    assert shutdown_calls == [(False, True)]


# Acceptance H: live-mode operational path initializes without discovery and
# completes the provider contract through persisted booking identifiers.
def test_acceptance_h_live_booking_initializes_and_persists_ids(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.delenv("BOOKING_PROVIDER", raising=False)
    monkeypatch.setattr(
        "src.orchestration.booking_orchestrator.AgentContractProvider",
        MagicMock(side_effect=AssertionError("contract discovery must be lazy")),
    )
    provider = MagicMock(spec=BreakoutBookingProvider)
    provider.get_booking_venues.return_value = [{"venueId": "jp-1", "name": "JP Nagar"}]
    provider.get_booking_games.return_value = [{
        "gameId": "murder-1",
        "name": "Murder Mystery",
        "peopleMin": 2,
        "peopleMax": 7,
        "peopleCategories": [{"categoryId": "adult", "categoryName": "Adults", "max": 7}],
    }]
    provider.search_booking_slots.return_value = [{
        "gameId": "murder-1", "date": "2026-06-23", "time": "19:00",
        "available": 7, "isAvailable": True,
    }]
    provider.create_instant_cart.return_value = {"cartId": "cart-live"}
    provider.create_confirmed_booking.return_value = {
        "bookingId": "booking-live",
        "orderId": "reference-live",
        "status": "CONFIRMED",
    }
    orchestrator = BookingOrchestrator(booking_provider=provider)
    assert orchestrator.is_live is True
    assert orchestrator.contract_provider is None

    memory = _booking_memory(tmp_path)
    memory.data.update({
        "preferred_date": "2026-06-23",
        "selected_slot": "",
        "customer_name": "Siddharth Patel",
        "first_name": "Siddharth",
        "last_name": "Patel",
        "phone": "9982254357",
    })
    memory.save()
    agent = BookingAgent(memory, orchestrator=orchestrator)
    agent.response_composer.enabled = False

    availability = agent.handle_message("ready")
    confirmation = agent.handle_message("7 PM")

    assert "7:00 PM" in availability.response
    provider.create_instant_cart.assert_called_once()
    provider.create_confirmed_booking.assert_called_once()
    assert memory.data["booking_id"] == "booking-live"
    assert memory.data["booking_ref"] == "reference-live"
    assert "reference-live" in confirmation.response


def test_robust_escalation_variations(tmp_path: Path) -> None:
    # 1. Representative / human requests
    for msg in ["can I speak to a representative?", "I need to talk to someone", "connect me with support", "put me through to a supervisor", "is there a real person I can talk to?", "speak to an agent"]:
        memory = ConversationMemory(tmp_path / f"human_{hash(msg)}.json")
        sentiment = SentimentResult("neutral", 0.5, False, "", "discovery")
        escalation = EscalationAgent(memory).evaluate(msg, sentiment)
        assert escalation.escalate is True, f"Failed to escalate human request: {msg}"
        assert "human representative" in escalation.reason

    # 2. Safety/emergency requests
    for msg in ["there is an emergency", "call an ambulance", "get me out of here", "my friend fainted inside the room", "I've had a panic attack"]:
        memory = ConversationMemory(tmp_path / f"safety_{hash(msg)}.json")
        sentiment = SentimentResult("neutral", 0.5, False, "", "discovery")
        escalation = EscalationAgent(memory).evaluate(msg, sentiment)
        assert escalation.escalate is True, f"Failed to escalate safety request: {msg}"
        assert "safety concern" in escalation.reason

    # 3. Refund variations
    for msg in ["can I get a refund?", "how to get a refund", "I want my money back", "refund me please", "please refund this", "I am asking for a refund"]:
        memory = ConversationMemory(tmp_path / f"refund_{hash(msg)}.json")
        sentiment = SentimentResult("neutral", 0.5, False, "", "discovery")
        escalation = EscalationAgent(memory).evaluate(msg, sentiment)
        assert escalation.escalate is True, f"Failed to escalate refund request: {msg}"
        assert "refund" in escalation.reason.lower()

    # 4. Complaint / bad experience variations
    for msg in ["I was in an escape room yesterday, and I did not like my experience at all.", "Last time was terrible.", "This was a complete waste of money.", "I was extremely disappointed with the service."]:
        memory = ConversationMemory(tmp_path / f"complaint_{hash(msg)}.json")
        sentiment = SentimentResult("neutral", 0.5, False, "", "discovery")
        escalation = EscalationAgent(memory).evaluate(msg, sentiment)
        assert escalation.escalate is True, f"Failed to escalate complaint request: {msg}"
        assert "frustration" in escalation.reason or "anger" in escalation.reason

    # 5. Empathy cushion in dispatch response for complaints
    result, _, _ = dispatch(
        "I did not like my experience at all yesterday",
        build_inbound_agent(Namespace(model=None, no_openai=True), ConversationMemory(tmp_path / "dispatch_complaint.json")),
        None,
        "inbound_agent"
    )
    assert result.next_agent == "escalation_agent"
    assert result.should_handoff is True
    assert "sorry to hear you had a bad experience" in result.response.lower()
