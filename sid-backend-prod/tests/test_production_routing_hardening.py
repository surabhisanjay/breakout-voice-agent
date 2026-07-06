from __future__ import annotations

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
from src.orchestration.conversation_manager import ConversationManager


def _memory(tmp_path: Path, **updates) -> ConversationMemory:
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "event_type": "Escape Room",
            "booking_started": True,
            "current_workflow": "booking",
            "participants": 4,
            "age_group": "adults",
            "location": "Whitefield",
            "preferred_date": "18 June",
            "room": "Murder Mystery",
            "recommended_option": "Murder Mystery",
        }
    )
    memory.data.update(updates)
    memory.save()
    return memory


def _inbound(memory: ConversationMemory) -> InboundAgent:
    return InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


RECOMMENDATION_SCENARIOS = [
    "What would you recommend?",
    "What do you recommend?",
    "Which room is best?",
    "Which one is best?",
    "Which room should we choose?",
    "Which game would you suggest?",
    "What do most people play?",
    "What is your second recommendation?",
    "Second best?",
    "Second option?",
    "Any other option?",
    "Another room?",
    "Other options?",
    "I don't like that one.",
    "I don't want that.",
    "Not that one.",
    "Recommend something harder.",
    "Something harder.",
    "Something easier.",
    "Recommend something for couples.",
    "Recommend something for kids.",
    "Recommend something for adults.",
    "We are beginners.",
    "This is our third time.",
    "We have played before.",
    "We want a challenging one.",
    "We don't like puzzles.",
    "We prefer story and investigation.",
    "Which is more popular?",
    "Your favorite room?",
]

KNOWLEDGE_SCENARIOS = [
    "What rooms do you have?",
    "What themes do you have?",
    "What games do you have?",
    "What options are available at Whitefield?",
    "Tell me about the rooms.",
    "Explain the rooms.",
    "What happens in Hostage?",
    "What happens in Murder Mystery?",
    "What is Murder Mystery?",
    "What is Hostage?",
    "What is Bomb Defusal?",
    "What is Undercover?",
    "What is an escape room?",
    "How does an escape room work?",
    "What are the rules?",
    "How long is the game?",
    "Do you provide hints?",
    "Are we locked in?",
    "Can kids play?",
    "Tell me about Whitefield.",
    "Which location is better?",
    "Compare Koramangala and Whitefield.",
    "Difference between Hostage and Murder Mystery?",
    "Hostage vs Murder Mystery?",
    "Compare Murder Mystery and Hostage.",
]

AVAILABILITY_SCENARIOS = [
    "Do you have 1:30 tomorrow?",
    "Do you have slots at 1:30 tomorrow?",
    "Is 5:20 available?",
    "Any slots tomorrow?",
    "What slots are available?",
    "What slots do you have tomorrow?",
    "Tomorrow evening?",
    "Any openings this Saturday?",
    "Can I book for tomorrow at 3?",
    "Check availability for tomorrow.",
    "Slot availability?",
    "Are there slots available?",
    "Do you have any slots?",
    "Is 7 PM open tomorrow?",
    "What times are open this weekend?",
]

BOOKING_CHANGE_SCENARIOS = [
    "Actually change it to Hostage.",
    "Actually Hostage.",
    "Switch to Bomb Defusal instead.",
    "Make it Undercover.",
    "Change the date to June 25.",
    "Actually tomorrow.",
    "Reschedule to next Saturday.",
    "Change the slot to 7 PM.",
    "Actually 8:30 PM.",
    "Switch to the first slot.",
    "We are actually 6 people.",
    "Make it 5 players.",
    "Actually Whitefield.",
    "Switch location to Koramangala.",
    "Use JP Nagar instead.",
    "My name is Siddharth.",
    "Phone is 9876543210.",
    "Adults.",
    "Yes.",
    "Confirm.",
]

ESCALATION_POLICY_SCENARIOS = [
    "I want a refund.",
    "Please cancel my booking.",
    "I need to reschedule my booking.",
    "Connect me to a human.",
    "I want to speak to someone.",
    "Your team messed up my booking.",
    "I am angry about the refund.",
    "What is the cancellation policy?",
    "What if I cancel?",
    "Can bookings be cancelled?",
]


@pytest.mark.parametrize(
    "message",
    RECOMMENDATION_SCENARIOS,
)
def test_recommendation_and_rejection_preempt_booking(message: str, tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    target, category = ConversationManager(memory).determine_routing(message, active_agent="booking_agent")

    assert (target, category) == ("inbound_agent", "recommendation")


@pytest.mark.parametrize(
    "message",
    KNOWLEDGE_SCENARIOS,
)
def test_knowledge_and_comparison_preempt_booking(message: str, tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    target, category = ConversationManager(memory).determine_routing(message, active_agent="booking_agent")

    assert target == "inbound_agent"
    assert category == "faq"


@pytest.mark.parametrize(
    "message",
    AVAILABILITY_SCENARIOS,
)
def test_availability_routes_to_booking_tools(message: str, tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    target, category = ConversationManager(memory).determine_routing(message, active_agent="inbound_agent")

    assert target == "booking_agent"
    assert category == "continuing_workflow"


@pytest.mark.parametrize(
    "message",
    BOOKING_CHANGE_SCENARIOS,
)
def test_concrete_booking_changes_stay_with_booking(message: str, tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    target, category = ConversationManager(memory).determine_routing(message, active_agent="booking_agent")

    assert target == "booking_agent"
    assert category == "continuing_workflow"


@pytest.mark.parametrize(
    "message",
    ESCALATION_POLICY_SCENARIOS,
)
def test_policy_modification_and_escalation_do_not_become_recommendations(message: str, tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    target, _category = ConversationManager(memory).determine_routing(message, active_agent="booking_agent")

    assert target in {"booking_agent", "inbound_agent"}


def test_live_room_inventory_question_does_not_start_booking_question(tmp_path: Path) -> None:
    memory = _memory(tmp_path, room="", recommended_option="")
    inbound = _inbound(memory)
    booking = BookingAgent(memory)

    result, _booking, active = dispatch("What rooms do you have?", inbound, booking, "booking_agent")

    assert active == "inbound_agent"
    assert result.next_agent == "inbound_agent"
    assert "which room would you like to book" not in result.response.lower()


def test_live_recommendation_question_reaches_inbound_recommendation(tmp_path: Path) -> None:
    memory = _memory(tmp_path, room="", recommended_option="")
    inbound = _inbound(memory)
    booking = BookingAgent(memory)

    result, _booking, active = dispatch("What would you recommend?", inbound, booking, "booking_agent")

    assert active == "inbound_agent"
    assert result.next_agent == "inbound_agent"
    assert result.recommendation["option"] or "recommend" in result.response.lower() or "suggest" in result.response.lower()
    assert "which room would you like to book" not in result.response.lower()


def test_recommendation_rejection_updates_recommended_option_not_room(tmp_path: Path) -> None:
    memory = _memory(tmp_path, room="", recommended_option="Murder Mystery")
    inbound = _inbound(memory)
    memory.data["discussed_options"] = ["Murder Mystery"]
    memory.save()

    result = inbound.handle_message("I don't like that one. Any other option?")

    assert memory.data["recommended_option"]
    assert memory.data["recommended_option"] != "Murder Mystery"
    assert memory.data["room"] == ""
    assert memory.data["recommended_option"] in result.response


def test_room_change_updates_memory_immediately(tmp_path: Path) -> None:
    memory = _memory(tmp_path, room="Murder Mystery", selected_slot="5:00 PM")
    agent = BookingAgent(memory)

    agent.handle_message("Actually change it to Hostage.")

    assert memory.data["room"] == "Hostage"
    assert memory.data["selected_slot"] == ""


def test_date_slot_participant_and_location_changes_update_memory(tmp_path: Path) -> None:
    memory = _memory(tmp_path, selected_slot="5:00 PM")
    agent = BookingAgent(memory)

    agent.handle_message("Actually change the date to June 25.")
    assert memory.data["preferred_date"] == "25 June"
    assert memory.data["selected_slot"] == ""

    agent.handle_message("Actually 7 PM.")
    assert memory.data["preferred_time"] == "7:00 PM"

    agent.handle_message("We are actually 6 people.")
    assert memory.data["participants"] == 6

    agent.handle_message("Switch location to Koramangala.")
    assert memory.data["location"] == "Koramangala"
