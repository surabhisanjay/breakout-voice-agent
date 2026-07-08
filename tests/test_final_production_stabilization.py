from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from main import dispatch
from src.agents.inbound_agent import InboundAgent
from src.knowledge.knowledge_loader import KnowledgeLoader
from src.memory.conversation_memory import ConversationMemory
from src.orchestration.conversation_manager import ConversationManager


def _agent(tmp_path: Path, name: str = "session") -> InboundAgent:
    return InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=ConversationMemory(tmp_path / f"{name}.json"),
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


def _run_conversation(agent: InboundAgent, turns: list[str]) -> tuple[list[str], str]:
    booking = None
    active = "inbound_agent"
    responses: list[str] = []
    for turn in turns:
        result, booking, active = dispatch(turn, agent, booking, active)
        responses.append(result.response)
        assert result.response
        assert "traceback" not in result.response.lower()
        assert "api error" not in result.response.lower()
        assert len(result.response.split(". ")) <= 5
    return responses, active


@pytest.mark.parametrize(
    ("spoken", "field", "expected"),
    [
        ("White food", "location", "Whitefield"),
        ("JP Nogger", "location", "JP Nagar"),
        ("Murder history", "room", "Murder Mystery"),
        ("Hostages", "room", "Hostage"),
        ("Bomb diffusion", "room", "Bomb Defusal"),
    ],
)
def test_asr_entity_resolution_before_routing(tmp_path: Path, spoken: str, field: str, expected: str) -> None:
    agent = _agent(tmp_path)
    agent.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "participants": 4,
            "age_group": "adults",
            "preferred_date": "18 June",
            "booking_started": True,
            "current_workflow": "booking",
        }
    )
    agent.memory.save()

    responses, _active = _run_conversation(agent, [spoken])

    assert agent.memory.data[field] == expected
    if spoken == "White food":
        assert "food options" not in responses[-1].lower()


def test_same_phone_same_room_and_same_booking_reuse_memory(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "participants": 4,
            "age_group": "adults",
            "location": "Whitefield",
            "room": "Hostage",
            "preferred_date": "18 June",
            "phone": "9876543210",
            "booking_ref": "BRK-123",
            "booking_started": True,
            "current_workflow": "booking",
        }
    )
    agent.memory.save()

    normalized = agent.memory.apply_reference_aliases("Use same phone, same room, and same booking")

    assert "9876543210" in normalized
    assert "Hostage" in normalized
    assert "BRK-123" in normalized


def test_additional_booking_resets_booking_state_without_losing_contact(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.memory.data.update(
        {
            "completed_booking": True,
            "booking_started": True,
            "current_workflow": "booking",
            "booking_id": "booking-a",
            "booking_ref": "ref-a",
            "customer_name": "Surabhi Rao",
            "phone": "9876543210",
            "location": "Whitefield",
            "room": "Murder Mystery",
            "preferred_date": "18 June",
            "selected_slot": "5:00 PM",
            "participants": 4,
        }
    )
    agent.memory.save()

    _run_conversation(agent, ["I also want another booking for same phone"])

    assert agent.memory.data["completed_booking"] is False
    assert agent.memory.data["booking_id"] == ""
    assert agent.memory.data["booking_ref"] == ""
    assert agent.memory.data["phone"] == "9876543210"
    assert agent.memory.data["customer_name"] == "Surabhi Rao"
    assert agent.memory.data["previous_bookings"][0]["booking_ref"] == "ref-a"


def test_recommendation_rejection_remembers_rejected_room(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "participants": 4,
            "age_group": "adults",
            "location": "Whitefield",
            "recommended_option": "Murder Mystery",
            "discussed_options": ["Murder Mystery"],
        }
    )
    agent.memory.save()

    first = agent.handle_message("I don't like that one. Any other option?")
    second = agent.handle_message("Already played that. Something harder.")

    assert "Murder Mystery" in agent.memory.data["rejected_options"]
    assert agent.memory.data["recommended_option"] != "Murder Mystery"
    assert agent.memory.data["recommended_option"] in second.response
    assert first.response != second.response


def test_availability_question_does_not_become_recommendation(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "participants": 4,
            "age_group": "adults",
            "location": "Whitefield",
            "room": "Hostage",
            "preferred_date": "18 June",
        }
    )
    memory.save()

    target, category = ConversationManager(memory).determine_routing("Do you have 1:30 tomorrow?", "inbound_agent")

    assert (target, category) == ("booking_agent", "continuing_workflow")


def test_guard_answers_direct_question_during_active_booking_without_losing_state(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "participants": 4,
            "age_group": "adults",
            "location": "Whitefield",
            "room": "Hostage",
            "preferred_date": "18 June",
            "booking_started": True,
            "current_workflow": "booking",
        }
    )
    agent.memory.save()

    booking = None
    result, booking, active = dispatch("What is the price?", agent, booking, "booking_agent")

    assert "price" in result.response.lower() or "pricing" in result.response.lower()
    assert "phone" not in result.response.lower()
    assert active == "booking_agent"
    assert agent.memory.data["room"] == "Hostage"


def test_guard_room_list_lists_rooms_before_next_question(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.memory.data.update({"location": "Whitefield", "participants": 4})
    agent.memory.save()

    result, _booking, _active = dispatch("What rooms do you have?", agent, None, "inbound_agent")

    assert "Murder Mystery" in result.response
    assert "Hostage" in result.response
    assert "Which style" in result.response
    assert "brief summary" not in result.response.lower()
    assert result.response.count("?") <= 1


def test_guard_recommendation_uses_selected_location_inventory(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "participants": 4,
            "age_group": "adults",
            "location": "JP Nagar",
        }
    )
    agent.memory.save()

    result, _booking, _active = dispatch("Recommend something difficult", agent, None, "inbound_agent")

    assert "Bomb Defusal" not in result.response
    assert agent.memory.data["recommended_option"] in {"Missile Attack", "Murder Mystery", "Hostage"}


VOICE_CONVERSATIONS: list[list[str]] = []

FIRST_TURNS = [
    "We are first time players, four adults.",
    "This is our third time and we want something harder.",
    "We are a couple looking for a room.",
    "Family of five with two kids.",
    "Corporate team of 18 people.",
    "Birthday for ten kids.",
    "Large group of 12 friends.",
    "What rooms do you have?",
    "Which location is better?",
    "Do you have slots tomorrow evening?",
]
SECOND_TURNS = [
    "White food",
    "JP Nogger",
    "Koramangala",
    "What would you recommend?",
    "What is the price?",
    "Any other option?",
    "Murder history",
    "Hostages",
    "Bomb diffusion",
    "What about parking?",
]
THIRD_TURNS = [
    "Actually change it to Hostage.",
    "Tomorrow evening?",
    "same phone",
    "My phone is 9876543210.",
    "My name is Surabhi.",
    "What is cancellation policy?",
    "I want to speak to a human.",
    "Already played that.",
    "Something easier.",
    "Use same room.",
]
FOURTH_TURNS = [
    "18 June",
    "7 PM",
    "Adults.",
    "same location",
    "another booking",
    "What food do you have?",
    "Can I get a refund?",
    "Switch location to Whitefield.",
    "Change the date to June 25.",
    "Confirm.",
]

for idx in range(300):
    VOICE_CONVERSATIONS.append(
        [
            FIRST_TURNS[idx % len(FIRST_TURNS)],
            SECOND_TURNS[(idx // 2) % len(SECOND_TURNS)],
            THIRD_TURNS[(idx // 3) % len(THIRD_TURNS)],
            FOURTH_TURNS[(idx // 5) % len(FOURTH_TURNS)],
        ]
    )


@pytest.mark.parametrize("turns", VOICE_CONVERSATIONS)
def test_300_realistic_voice_conversations_use_real_dispatch(tmp_path: Path, turns: list[str]) -> None:
    agent = _agent(tmp_path, name=str(abs(hash(tuple(turns)))))

    responses, _active = _run_conversation(agent, turns)

    combined = " ".join(responses).lower()
    assert "which room would you like to book" not in combined or any(
        room.lower() in " ".join(turns).lower()
        for room in ("murder", "hostage", "classified", "undercover", "bomb")
    )
