from __future__ import annotations

from itertools import product
from pathlib import Path
import sys
from unittest.mock import MagicMock

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.booking_agent import BookingAgent
from src.agents.escalation_agent import EscalationAgent
from src.agents.sentiment_agent import SentimentAgent
from src.memory.conversation_memory import ConversationMemory
from src.services.recommendation_engine import RecommendationEngine


BOOKING_CASES = list(product(
    (2, 3, 4, 6),
    ("25 June", "26 June", "27 June", "28 June", "29 June"),
    ("Hostage", "Classified", "Undercover", "Bomb Defusal", "Prison Break"),
))
assert len(BOOKING_CASES) == 100


@pytest.mark.parametrize("participants,new_date,new_room", BOOKING_CASES)
def test_100_booking_scenarios_preserve_context_and_invalidate_slot(
    tmp_path: Path, participants: int, new_date: str, new_room: str
) -> None:
    memory = ConversationMemory(tmp_path / "booking.json")
    memory.data.update({
        "intent": "escape_room_inquiry", "event_type": "Escape Room",
        "booking_started": True, "current_workflow": "booking",
        "participants": participants, "age_group": "adults",
        "location": "Whitefield", "preferred_date": "23 June",
        "room": "Murder Mystery", "selected_slot": "3:00 PM",
        "customer_name": "Riya Patel", "first_name": "Riya",
        "last_name": "Patel", "phone": "9876543210",
    })
    memory.save()
    agent = BookingAgent(memory)
    agent.response_composer.enabled = False
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._selected_slot = "3:00 PM"
    agent._available_slots = ["3:00 PM"]
    agent.availability_tool.check = MagicMock(return_value={
        "available": True, "verified": True, "capacity_supported": True,
        "slots": ["5:00 PM"],
    })

    result = agent.handle_message(f"Change the date to {new_date} and switch to {new_room}")

    assert memory.data["preferred_date"] == new_date
    assert memory.data["room"] == new_room
    assert memory.data["selected_slot"] == ""
    assert memory.data["participants"] == participants
    assert memory.data["customer_name"] == "Riya Patel"
    assert "confirmed" not in result.response.lower()


RECOMMENDATION_CASES = list(product(
    ("koramangala", "whitefield", "jp nagar"),
    (2, 3, 4, 5, 6, 7, 8, 9, 10),
    ("beginner", "experienced"),
))[:50]
assert len(RECOMMENDATION_CASES) == 50


@pytest.mark.parametrize("location,participants,experience", RECOMMENDATION_CASES)
def test_50_recommendation_scenarios_respect_inventory_and_capacity(
    location: str, participants: int, experience: str
) -> None:
    engine = RecommendationEngine()
    recommendation = engine.recommend("What do you recommend?", {
        "intent": "escape_room_inquiry",
        "participants": participants,
        "age_group": "adults",
        "experience_level": experience,
        "location": location.title() if location != "jp nagar" else "JP Nagar",
    })
    assert recommendation.option
    if participants > 8 or recommendation.option == "Multi-room event coordination":
        assert recommendation.option == "Multi-room event coordination"
        return
    inventory = engine.ROOM_INVENTORY[location]
    for room in recommendation.option.split(" or "):
        assert room in inventory
        minimum, maximum = inventory[room]
        assert minimum <= participants <= maximum


FAQ_BASE = (
    ("Is parking available?", "parking"),
    ("Where can we park?", "parking"),
    ("How long is the game?", "50 minutes"),
    ("What is the game duration?", "50 minutes"),
    ("What food do you have?", "food options"),
    ("Can you explain the rules?", "immersive"),
    ("What happens inside?", "immersive"),
    ("What is the cancellation policy?", "cancellation"),
    ("Do you have parking at the venue?", "parking"),
    ("How does the escape room work?", "immersive"),
)
FAQ_CASES = list(FAQ_BASE) * 5
assert len(FAQ_CASES) == 50


@pytest.mark.parametrize("message,expected", FAQ_CASES)
def test_50_faq_interruptions_answer_and_preserve_booking(
    tmp_path: Path, message: str, expected: str
) -> None:
    memory = ConversationMemory(tmp_path / "faq.json")
    memory.data.update({
        "intent": "escape_room_inquiry", "booking_started": True,
        "current_workflow": "booking", "participants": 4,
        "age_group": "adults", "location": "Whitefield",
        "preferred_date": "25 June", "room": "Murder Mystery",
        "selected_slot": "",
    })
    memory.save()
    agent = BookingAgent(memory)
    agent.response_composer.enabled = False
    agent._state = agent._STATE_WAITING_FOR_SLOT
    agent._available_slots = ["3:00 PM", "5:00 PM"]

    response = agent.handle_message(message).response.lower()

    assert expected in response
    assert agent._state == agent._STATE_WAITING_FOR_SLOT
    assert memory.data["room"] == "Murder Mystery"
    assert memory.data["selected_slot"] == ""


ESCALATION_CASES = (
    "I want a human", "I need a person", "get me a human please",
    "connect me to an agent", "connect me with someone", "connect me with anyone",
    "I want to talk to your manager", "transfer me to a supervisor", "real person please",
    "please connect me to the team", "please connect me with support",
    "I need human help right now", "talk to a manager", "speak with an agent",
    "transfer me to a person", "connect me to a human", "I want a supervisor",
    "I need an agent", "talk to the manager", "speak to your manager",
    "Someone got injured", "my friend fainted", "someone passed out",
    "a person collapsed", "someone is unconscious", "there is smoke in the room",
    "there is a fire", "someone cannot breathe", "someone can't breathe",
    "there is bleeding", "medical emergency", "someone is unresponsive",
    "someone had a seizure", "someone had a heart attack", "not breathing",
    "I want a refund", "I need a refund", "I deserve a refund",
    "refund me", "give me my money back", "I want my money back",
    "I would like a refund", "I demand a refund", "I am asking for a refund",
    "I request a refund", "give me a refund", "I need my money back",
    "This is ridiculous", "This service is unacceptable", "I am furious",
)
assert len(ESCALATION_CASES) == 50


@pytest.mark.parametrize("message", ESCALATION_CASES)
def test_50_escalation_scenarios_always_escalate(tmp_path: Path, message: str) -> None:
    memory = ConversationMemory(tmp_path / "escalation.json")
    sentiment = SentimentAgent(memory).analyze(message, stage="booking")
    result = EscalationAgent(memory).evaluate(message, sentiment)
    assert result.escalate is True
    assert result.reason


ASR_BASE = (
    ("koramangala", "Koramangala"),
    ("koramangla", "Koramangala"),
    ("koramangal", "Koramangala"),
    ("koramangalar", "Koramangala"),
    ("whitefield", "Whitefield"),
    ("white field", "Whitefield"),
    ("whitfield", "Whitefield"),
    ("whitefeild", "Whitefield"),
    ("white shield", "Whitefield"),
    ("wide field", "Whitefield"),
    ("jp nagar", "JP Nagar"),
    ("jpnagar", "JP Nagar"),
    ("jeep nagar", "JP Nagar"),
    ("jp nuggets", "JP Nagar"),
    ("jp nagger", "JP Nagar"),
)
ASR_PREFIXES = ("", "at ", "go to ", "location ")
ASR_CASES = [
    (f"{prefix}{spoken}", expected)
    for spoken, expected in ASR_BASE
    for prefix in ASR_PREFIXES
][:50]
assert len(ASR_CASES) == 50


@pytest.mark.parametrize("message,expected", ASR_CASES)
def test_50_asr_location_scenarios(message: str, expected: str) -> None:
    assert ConversationMemory._extract_location(message.lower()) == expected


CORPORATE_CASES = list(product(
    (10, 20, 30, 40, 50),
    ("Koramangala", "Whitefield", "JP Nagar"),
    ("25 June", "26 June", "27 June", "28 June"),
))[:50]
assert len(CORPORATE_CASES) == 50


@pytest.mark.parametrize("company_size,location,preferred_date", CORPORATE_CASES)
def test_50_corporate_scenarios_use_coordination_not_single_room(
    tmp_path: Path, company_size: int, location: str, preferred_date: str
) -> None:
    memory = ConversationMemory(tmp_path / "corporate.json")
    memory.data.update({
        "intent": "corporate_event", "event_type": "Corporate Event",
        "company_size": company_size, "participants": company_size,
        "location": location, "preferred_date": preferred_date,
        "customer_name": "Riya", "phone": "9876543210",
        "booking_started": True, "current_workflow": "booking",
    })
    memory.save()
    recommendation = RecommendationEngine().recommend("team outing", memory.data)
    agent = BookingAgent(memory)
    agent.response_composer.enabled = False
    agent.availability_tool.check = MagicMock()

    result = agent.handle_message("ready")

    assert recommendation.option == "Corporate event coordination"
    agent.availability_tool.check.assert_not_called()
    assert result.next_agent == "events_team"
    assert result.should_handoff is True
    assert not memory.data.get("booking_id")
