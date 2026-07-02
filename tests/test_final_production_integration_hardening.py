from __future__ import annotations

import os
import random
import re
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
def offline_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("BOOKING_PROVIDER", "simulator")
    monkeypatch.setenv("BOOKING_API_KEY", "")
    monkeypatch.setenv("BOOKING_BASE_URL", "")


def _agent(tmp_path: Path, name: str = "session") -> InboundAgent:
    return InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=ConversationMemory(tmp_path / f"{name}.json"),
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


def test_recommendation_date_acceptance_booking_and_wati_payload(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    booking = None
    active = "inbound_agent"
    turns = [
        "Hi. I wanna book an escape room for four people.",
        "This is going to be my first time.",
        "We are all adults.",
        "Whitefield",
        "I would like a recommendation.",
        "Tomorrow.",
        "Yes.",
        "Evening works.",
        "Seven forty PM.",
        "Siddharth Khandelwal",
        "9982151357",
    ]
    responses: list[str] = []

    for turn in turns:
        result, booking, active = dispatch(turn, agent, booking, active)
        responses.append(result.response)

    assert "I'd recommend Murder Mystery" in responses[4]
    assert "What date" in responses[4]
    assert "What time works best" in responses[5]
    assert "morning" in responses[5].lower()
    assert "afternoon" in responses[5].lower()
    assert "evening" in responses[5].lower()
    assert "available evening slots" in responses[7]
    assert "Which room would you like" not in " ".join(responses)
    assert agent.memory.data["room"] == "Murder Mystery"
    assert agent.memory.data["selected_slot"] == "7:40 PM"
    assert agent.memory.data["customer_name"] == "Siddharth Khandelwal"
    assert agent.memory.data["phone"] == "9982151357"
    assert agent.memory.data["booking_id"]
    payload = agent.memory.data["whatsapp_payload"]
    assert payload["provider"] == "wati"
    assert payload["send"] is False
    assert payload["customer_name"] == "Siddharth Khandelwal"
    assert payload["phone"] == "9982151357"
    assert payload["booking_id"] == agent.memory.data["booking_id"]
    assert agent.memory.data["booking_ref"] == agent.memory.data["booking_order_id"]
    assert payload["room"] == "Murder Mystery"
    assert payload["location"] == "Whitefield"
    assert payload["date"] == "Tomorrow"
    assert payload["time"] == "7:40 PM"
    assert payload["payment_link"]


def test_simulator_booking_result_matches_reserved_payment_contract(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    booking = None
    active = "inbound_agent"
    for turn in [
        "Book Murder Mystery for four adults at Whitefield tomorrow evening.",
        "7:40 PM",
        "Siddharth Khandelwal",
        "9982151357",
    ]:
        result, booking, active = dispatch(turn, agent, booking, active)

    booking_result = result.booking_result or {}
    assert booking_result["booking_id"]
    assert booking_result["order_id"]
    assert booking_result["booking_reference"] == booking_result["order_id"]
    assert booking_result["payment_url"]
    assert booking_result["status"] == "RESERVED"
    assert booking_result["payment_deadline"]
    assert agent.memory.data["booking_order_id"] == booking_result["order_id"]
    assert agent.memory.data["bookingStatus"] == "RESERVED"
    assert agent.memory.data["paymentStatus"] == "UNPAID"


def test_compact_couple_opener_extracts_booking_context(tmp_path: Path) -> None:
    agent = _agent(tmp_path)

    result, _booking, active = dispatch(
        "Hi. Couple. Tomorrow. JP Nagar. First timer. Recommend something.",
        agent,
        None,
        "inbound_agent",
    )

    assert active == "inbound_agent"
    assert agent.memory.data["participants"] == 2
    assert agent.memory.data["age_group"] == "adults"
    assert agent.memory.data["experience_level"] == "beginner"
    assert agent.memory.data["location"] == "JP Nagar"
    assert agent.memory.data["preferred_date"] == "Tomorrow"
    assert agent.memory.data["recommended_option"]
    assert "Which location" not in result.response


def test_location_correction_prefers_positive_location_before_not(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    booking = None
    active = "inbound_agent"

    dispatch("Book for four adults in Koramangala.", agent, booking, active)
    result, booking, active = dispatch("Actually no, Whitefield not Koramangala.", agent, booking, active)

    assert agent.memory.data["location"] == "Whitefield"
    assert "Koramangala" not in result.state.get("location", "")


def test_reference_expanded_rejection_does_not_repeat_same_room(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    booking = None
    active = "inbound_agent"

    dispatch("Couple. JP Nagar. First time. Tomorrow evening.", agent, booking, active)
    result, booking, active = dispatch("No not that one.", agent, booking, active)

    assert "Murder Mystery" in agent.memory.data["rejected_options"]
    assert agent.memory.data["recommended_option"] != "Murder Mystery"
    assert "Murder Mystery" not in result.response


def test_rejection_acceptance_does_not_reset_booking_context(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    booking = None
    active = "inbound_agent"

    for turn in [
        "Couple. JP Nagar. First time. Tomorrow evening.",
        "Recommend something.",
        "No not that one.",
    ]:
        result, booking, active = dispatch(turn, agent, booking, active)

    assert agent.memory.data["recommended_option"] == "Hostage"
    result, booking, active = dispatch("Fine book it.", agent, booking, active)

    assert agent.memory.data["participants"] == 2
    assert agent.memory.data["location"] == "JP Nagar"
    assert agent.memory.data["preferred_date"] == "Tomorrow"
    assert agent.memory.data["room"] == "Hostage"
    assert "Which location" not in result.response


def test_challenge_preference_is_not_extracted_as_customer_name(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    booking = None
    active = "inbound_agent"

    for turn in [
        "Four adults, Koramangala, tomorrow evening.",
        "We have played before.",
        "Something hard.",
    ]:
        result, booking, active = dispatch(turn, agent, booking, active)

    assert agent.memory.data["challenge_preference"] == "challenging"
    assert agent.memory.data["customer_name"] == ""
    assert agent.memory.data["first_name"] == ""


def test_second_booking_request_preserves_contact_but_clears_stale_room(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "participants": 2,
            "age_group": "adults",
            "location": "JP Nagar",
            "room": "Hostage",
            "recommended_option": "Hostage",
            "preferred_date": "Tomorrow",
            "selected_slot": "7:40 PM",
            "customer_name": "Siddharth Khandelwal",
            "first_name": "Siddharth",
            "last_name": "Khandelwal",
            "phone": "9982151357",
            "booking_started": True,
            "booking_id": "bk-one",
            "booking_ref": "order-one",
            "payment_link": "https://pay.example/one",
        }
    )
    agent.memory.save()

    result, _booking, active = dispatch(
        "Also want a second booking - 3 people, Whitefield, tomorrow. Recommend something.",
        agent,
        None,
        "booking_agent",
    )

    assert active == "inbound_agent"
    assert agent.memory.data["customer_name"] == "Siddharth Khandelwal"
    assert agent.memory.data["phone"] == "9982151357"
    assert agent.memory.data["participants"] == 3
    assert agent.memory.data["location"] == "Whitefield"
    assert agent.memory.data["preferred_date"] == "Tomorrow"
    assert agent.memory.data["room"] in {"", agent.memory.data["recommended_option"]}
    assert agent.memory.data["relationship"] == ""
    assert "Hostage" not in result.response


def test_couple_context_does_not_override_direct_faq(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    dispatch("Hi. Couple. Tomorrow. JP Nagar. First timer. Recommend something.", agent, None, "inbound_agent")

    result, _booking, _active = dispatch("What happens inside?", agent, None, "inbound_agent")

    lowered = result.response.lower()
    assert "escape room is a themed team game" in lowered
    assert "hostage is the more urgent option" not in lowered


def test_spoken_unavailable_slot_returns_nearest_alternatives(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "event_type": "Escape Room",
            "participants": 4,
            "age_group": "adults",
            "experience_level": "beginner",
            "location": "Whitefield",
            "preferred_date": "Tomorrow",
            "room": "Murder Mystery",
            "recommended_option": "Murder Mystery",
        }
    )
    agent.memory.save()
    booking = BookingAgent(agent.memory)
    first, booking, active = dispatch("ready", agent, booking, "booking_agent")
    assert "what time works best" in first.response.lower()
    first, booking, active = dispatch("Evening works", agent, booking, active)
    assert "available evening slots" in first.response

    result, _booking, _active = dispatch("Eight fifty PM.", agent, booking, active)

    assert "8:50 PM isn't one of the available slots" in result.response
    assert "nearest available slots" in result.response
    assert "7:40 PM" in result.response


def test_generated_booking_conversations_complete_without_recommendation_loop(tmp_path: Path) -> None:
    rng = random.Random(20260627)
    locations = ["Whitefield", "Koramangala", "JP Nagar"]
    people = [2, 3, 4, 5, 6]
    banned = re.compile(r"trouble pulling|hold on|one moment|which room would you like", re.I)

    for index in range(25):
        location = rng.choice(locations)
        count = rng.choice(people)
        agent = _agent(tmp_path / "generated", f"session_{index}")
        booking = None
        active = "inbound_agent"
        turns = [
            f"I want to book an escape room for {count} adults.",
            "First time.",
            location,
            "I would like a recommendation.",
            "Tomorrow.",
            "Yes.",
            "Any time works.",
        ]
        responses: list[str] = []
        for turn in turns:
            result, booking, active = dispatch(turn, agent, booking, active)
            responses.append(result.response)
        slots = re.findall(r"\b\d{1,2}:\d{2} PM\b|\b\d{1,2}:\d{2} AM\b", responses[-1])
        assert slots, responses[-1]
        for turn in [slots[0], "Siddharth Khandelwal", "9982151357"]:
            result, booking, active = dispatch(turn, agent, booking, active)
            responses.append(result.response)

        transcript = " ".join(responses)
        assert not banned.search(transcript)
        assert agent.memory.data.get("booking_id"), transcript
        assert agent.memory.data["room"]
        assert agent.memory.data["selected_slot"]
        assert agent.memory.data.get("whatsapp_payload", {}).get("payment_link")
