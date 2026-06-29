from __future__ import annotations

import random
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.booking_agent import BookingAgent
from src.agents.conversation_intelligence_agent import ConversationIntelligenceAgent
from src.agents.escalation_agent import EscalationAgent
from src.agents.handoff_summary_agent import HandoffSummaryAgent
from src.agents.sentiment_agent import SentimentAgent
from src.integrations.kreeda.agent_contract_provider import AgentContractProvider
from src.integrations.kreeda.breakout_booking_provider import BreakoutBookingProvider
from src.memory.conversation_memory import ConversationMemory
from src.orchestration.booking_orchestrator import BookingOrchestrator


def _memory(tmp_path: Path, **overrides) -> ConversationMemory:
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update({
        "intent": "escape_room_inquiry",
        "event_type": "Escape Room",
        "participants": 4,
        "age_group": "adults",
        "location": "Whitefield",
        "preferred_date": "25 June",
        "room": "Murder Mystery",
        "recommended_option": "Murder Mystery",
        "selected_slot": "7:00 PM",
        "booking_started": True,
    })
    memory.data.update(overrides)
    memory.save()
    return memory


def _name_agent(tmp_path: Path, **overrides) -> tuple[ConversationMemory, BookingAgent]:
    defaults = {"customer_name": "", "first_name": "", "last_name": "", "phone": ""}
    defaults.update(overrides)
    memory = _memory(tmp_path, **defaults)
    agent = BookingAgent(memory)
    agent._state = agent._STATE_WAITING_FOR_FIRST_NAME
    agent._selected_slot = "7:00 PM"
    agent._available_slots = ["7:00 PM"]
    agent._last_availability = {
        "available": True,
        "verified": True,
        "slots": ["7:00 PM"],
    }
    agent.booking_tool.create = MagicMock()
    return memory, agent


@pytest.mark.parametrize(
    ("message", "customer_name", "first_name", "last_name"),
    [
        ("My name is Siddharth", "Siddharth", "Siddharth", ""),
        ("My name is Siddharth Khandelwal", "Siddharth Khandelwal", "Siddharth", "Khandelwal"),
        ("Sidd", "Sidd", "Sidd", ""),
        ("Book under Siddharth Khandelwal", "Siddharth Khandelwal", "Siddharth", "Khandelwal"),
    ],
)
def test_natural_name_collection_accepts_single_or_full_name(
    tmp_path: Path,
    message: str,
    customer_name: str,
    first_name: str,
    last_name: str,
) -> None:
    memory, agent = _name_agent(tmp_path)

    response = agent.handle_message(message)

    assert memory.data["customer_name"] == customer_name
    assert memory.data["first_name"] == first_name
    assert memory.data["last_name"] == last_name
    assert agent._state == agent._STATE_WAITING_FOR_PHONE
    assert "phone" in response.response.lower()
    assert "last name" not in response.response.lower()
    agent.booking_tool.create.assert_not_called()


def test_last_name_refusal_does_not_repeat_last_name_prompt(tmp_path: Path) -> None:
    memory, agent = _name_agent(
        tmp_path,
        customer_name="Siddharth",
        first_name="Siddharth",
        last_name="",
    )
    agent._state = agent._STATE_WAITING_FOR_LAST_NAME

    response = agent.handle_message("I don't want to share my last name.")

    assert memory.data["customer_name"] == "Siddharth"
    assert memory.data["last_name"] == ""
    assert agent._state == agent._STATE_WAITING_FOR_PHONE
    assert "phone" in response.response.lower()
    assert "last name" not in response.response.lower()


def test_single_word_name_can_complete_booking_with_phone(tmp_path: Path) -> None:
    memory, agent = _name_agent(tmp_path)
    agent.booking_tool.create = MagicMock(return_value={
        "booking_id": "booking-single-word",
        "booking_reference": "reference-single-word",
        "confirmed": True,
        "location": "Whitefield",
        "date": "25 June",
        "slot": "7:00 PM",
        "participants": 4,
        "event_type": "Escape Room",
        "customer_name": "Sidd",
        "phone": "9982254357",
    })

    name_response = agent.handle_message("Sidd")
    booking_response = agent.handle_message("9982254357")

    assert "phone" in name_response.response.lower()
    assert memory.data["customer_name"] == "Sidd"
    assert memory.data["last_name"] == ""
    agent.booking_tool.create.assert_called_once_with(memory.data, "7:00 PM")
    assert memory.data["booking_id"] == "booking-single-word"
    assert memory.data["booking_ref"] == "reference-single-word"
    assert "reference-single-word" in booking_response.response


def test_provider_payload_uses_blank_last_name_for_single_word_name(monkeypatch) -> None:
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.delenv("BOOKING_PROVIDER", raising=False)
    provider = MagicMock(spec=BreakoutBookingProvider)
    provider.create_instant_cart.return_value = {"cartId": "cart-name"}
    provider.create_confirmed_booking.return_value = {
        "bookingId": "booking-name",
        "orderId": "reference-name",
        "status": "CONFIRMED",
    }
    orchestrator = BookingOrchestrator(
        booking_provider=provider,
        contract_provider=MagicMock(spec=AgentContractProvider),
    )
    orchestrator._location = {"venueId": "wf-1", "name": "Whitefield"}
    orchestrator._game = {
        "gameId": "murder-1",
        "name": "Murder Mystery",
        "peopleCategories": [{"categoryId": "adult", "categoryName": "Adults", "max": 7}],
    }
    orchestrator._slot_lookup = {
        "19:00": {"gameId": "murder-1", "date": "2026-06-25", "time": "19:00"}
    }

    result = orchestrator.prepare_booking({
        "intent": "escape_room_inquiry",
        "event_type": "Escape Room",
        "participants": 4,
        "age_group": "adults",
        "location": "Whitefield",
        "preferred_date": "25 June",
        "room": "Murder Mystery",
        "customer_name": "Sidd",
        "first_name": "Sidd",
        "phone": "9982254357",
    }, "7:00 PM")

    payload = provider.create_confirmed_booking.call_args.args[0]
    assert result["confirmed"] is True
    assert payload["customer"]["firstName"] == "Sidd"
    # P0 fix: empty last_name is now substituted with 'NA' so Kreeda API accepts single-name bookings
    assert payload["customer"]["lastName"] == "NA"


def test_sentiment_journey_covers_positive_ready_and_escalation_paths(tmp_path: Path) -> None:
    positive_memory = _memory(tmp_path / "positive")
    positive_agent = SentimentAgent(positive_memory)
    positive_agent.analyze("What rooms do you have?", stage="recommendation")
    positive_agent.analyze("Sounds good, thank you so much.", stage="recommendation")
    positive = positive_agent.analyze("Let's book it.", stage="booking").to_dict()

    assert positive["customer_mood"] == "Ready To Book"
    assert positive["sentiment_journey"][-1]["sentiment"] == "Ready To Book"
    assert positive["frustration_score"] == 0.0

    frustrated_memory = _memory(tmp_path / "frustrated")
    frustrated_agent = SentimentAgent(frustrated_memory)
    frustrated_agent.analyze("This looks interesting.", stage="recommendation")
    frustrated_agent.analyze("Actually change it to Hostage.", stage="booking")
    frustrated = frustrated_agent.analyze(
        "No no no, that's not what I asked. I want a human.",
        stage="booking",
    )
    escalation = EscalationAgent(frustrated_memory).evaluate("I want a human.", frustrated)

    assert frustrated.to_dict()["escalation_risk"] in {"medium", "high"}
    assert frustrated.to_dict()["frustration_score"] > 0
    assert escalation.to_dict()["escalate"] is True


@pytest.mark.parametrize(
    ("message", "trigger"),
    [
        ("I want a refund.", "refund_request"),
        ("I want a human.", "human_request"),
        ("Someone got injured.", "safety_concern"),
    ],
)
def test_escalation_requests_generate_handoff_summary(
    tmp_path: Path,
    message: str,
    trigger: str,
) -> None:
    memory = _memory(tmp_path, customer_name="Sidd", phone="9982254357")
    sentiment = SentimentAgent(memory).analyze(message, stage="booking")
    escalation = EscalationAgent(memory).evaluate(message, sentiment).to_dict()
    handoff = HandoffSummaryAgent().generate(memory.data, escalation)

    assert escalation["escalate"] is True
    assert escalation["trigger"] == trigger
    assert handoff["customer_name"] == "Sidd"
    assert handoff["phone_available"] is True
    assert handoff["booking_details"]["room"] == "Murder Mystery"
    assert handoff["sentiment"]
    assert handoff["key_takeaways"]
    assert handoff["action_items"]
    assert handoff["follow_up_recommendations"]


def test_handoff_and_conversation_intelligence_cover_booking_states(tmp_path: Path) -> None:
    cases = [
        {
            "name": "successful",
            "booking_id": "booking-1",
            "booking_ref": "reference-1",
            "customer_name": "Sidd",
            "phone": "9982254357",
        },
        {"name": "faq", "customer_name": "Sidd", "phone": "9982254357"},
        {"name": "modified", "room": "Hostage", "preferred_date": "26 June", "selected_slot": ""},
    ]
    for case in cases:
        memory = _memory(tmp_path / case["name"], **{k: v for k, v in case.items() if k != "name"})
        memory.add_turn("customer", "I want to book an escape room.")
        memory.add_turn("agent", "Sure.")
        sentiment = SentimentAgent(memory).analyze("Please continue.", stage="booking")
        escalation = EscalationAgent(memory).evaluate("Please continue.", sentiment).to_dict()
        handoff = HandoffSummaryAgent().generate(memory.data, escalation)
        intelligence = ConversationIntelligenceAgent().analyze(memory.data, escalation)

        assert handoff["customer_name"] == memory.data.get("customer_name", "")
        assert handoff["intent"] == "escape_room_inquiry"
        assert "room" in handoff["booking_details"]
        assert handoff["sentiment"]
        assert handoff["key_takeaways"]
        assert handoff["action_items"]
        assert handoff["follow_up_recommendations"]
        assert intelligence["ai_summary"]["summary"]
        assert intelligence["timeline_events"]
        assert intelligence["key_takeaways"]
        assert intelligence["follow_up_recommendations"]


def test_seeded_red_team_conversation_simulations_preserve_name_and_state(tmp_path: Path) -> None:
    rng = random.Random(20260624)
    name_messages = [
        "My name is Sidd.",
        "My name is Siddharth Khandelwal.",
        "Sidd",
        "Book under Siddharth",
        "Actually use Sidd Khandelwal",
    ]
    correction_messages = [
        "Actually change it to Hostage.",
        "No, Whitefield.",
        "Wait, make it tomorrow.",
        "Actually 6 PM.",
        "We are actually 7 adults.",
    ]
    faq_messages = [
        "Is parking available?",
        "What is the cancellation policy?",
        "Can we get food?",
        "What time should we arrive?",
        "Any age restrictions?",
    ]
    escalation_messages = [
        "No no no, that's not what I asked.",
        "I want a refund.",
        "I want a human.",
        "Someone got injured.",
        "You are not understanding me.",
    ]
    package_messages = [
        "This is for a corporate booking.",
        "I want a birthday party.",
        "We are first-time players.",
        "Actually change the date.",
        "My name is Sidddharth.",
    ]
    scenario_pool = (
        [("name", message) for message in name_messages]
        + [("correction", message) for message in correction_messages]
        + [("faq", message) for message in faq_messages]
        + [("escalation", message) for message in escalation_messages]
        + [("package", message) for message in package_messages]
    )
    scenarios = [rng.choice(scenario_pool) for _ in range(50)]
    required_kinds = {kind for kind, _ in scenario_pool}
    scenarios[:len(required_kinds)] = [(kind, next(message for item_kind, message in scenario_pool if item_kind == kind)) for kind in required_kinds]

    results: list[bool] = []
    for index, (kind, message) in enumerate(scenarios):
        memory = _memory(tmp_path / f"red-team-{index}", customer_name="", first_name="", last_name="", phone="")
        agent = BookingAgent(memory)
        agent._selected_slot = "7:00 PM"
        agent._available_slots = ["6:00 PM", "7:00 PM"]
        agent._last_availability = {"available": True, "verified": True, "slots": ["6:00 PM", "7:00 PM"]}
        agent.booking_tool.create = MagicMock()

        if kind == "name":
            agent._state = agent._STATE_WAITING_FOR_FIRST_NAME
            response = agent.handle_message(message)
            assert memory.data["customer_name"]
            assert "last name" not in response.response.lower()
        elif kind == "correction":
            old_slot = memory.data["selected_slot"]
            agent._state = agent._STATE_WAITING_FOR_SLOT
            response = agent.handle_message(message)
            assert response.response
            if "hostage" in message.lower() or "tomorrow" in message.lower():
                assert memory.data["selected_slot"] != old_slot
        elif kind == "faq":
            agent._state = agent._STATE_WAITING_FOR_SLOT
            response = agent.handle_message(message)
            assert response.response
            assert memory.data["booking_started"] is True
        elif kind == "escalation":
            sentiment = SentimentAgent(memory).analyze(message, stage="booking")
            escalation = EscalationAgent(memory).evaluate(message, sentiment).to_dict()
            if any(term in message.lower() for term in ("refund", "human", "injured")):
                assert escalation["escalate"] is True
            else:
                assert sentiment.to_dict()["frustration_score"] > 0
        else:
            memory.merge_message(message, "escape_room_inquiry")
            intelligence = ConversationIntelligenceAgent().analyze(memory.data)
            assert intelligence["ai_summary"]["summary"]
        results.append(True)

    assert len(results) == 50
    assert all(results)
