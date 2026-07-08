from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.booking_agent import BookingAgent
from src.agents.escalation_agent import EscalationAgent
from src.agents.inbound_agent import InboundAgent
from src.agents.sentiment_agent import SentimentResult
from src.core.agent_response import AgentResponse
from src.knowledge.knowledge_loader import KnowledgeLoader
from src.memory.conversation_memory import ConversationMemory


def _memory(tmp_path: Path, **updates) -> ConversationMemory:
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update(updates)
    memory.save()
    return memory


def _inbound(tmp_path: Path) -> InboundAgent:
    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    return InboundAgent(
        knowledge_base=knowledge,
        memory=ConversationMemory(tmp_path / "session.json"),
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


def _neutral() -> SentimentResult:
    return SentimentResult("neutral", 0.0, False, "", "booking")


def test_booking_persists_date_while_location_is_missing(tmp_path: Path) -> None:
    memory = _memory(
        tmp_path,
        intent="escape_room_inquiry",
        booking_started=True,
        current_workflow="booking",
        room="Hostage",
        participants=4,
        age_group="adults",
    )
    response = BookingAgent(memory).handle_message("Tomorrow")

    assert memory.data["preferred_date"]
    assert "location" in response.response.lower()


def test_booking_persists_time_while_location_is_missing(tmp_path: Path) -> None:
    memory = _memory(
        tmp_path,
        intent="escape_room_inquiry",
        booking_started=True,
        current_workflow="booking",
        room="Hostage",
        participants=4,
        age_group="adults",
        preferred_date="2026-06-24",
    )
    response = BookingAgent(memory).handle_message("7 PM")

    assert memory.data["preferred_time"] == "7:00 PM"
    assert "location" in response.response.lower()


def test_booking_reuses_persisted_time_after_location_arrives(tmp_path: Path) -> None:
    memory = _memory(
        tmp_path,
        intent="escape_room_inquiry",
        booking_started=True,
        current_workflow="booking",
        room="Murder Mystery",
        participants=4,
        age_group="adults",
        preferred_date="2026-06-24",
        preferred_time="7:00 PM",
    )
    response = BookingAgent(memory).handle_message("JP Nagar")

    assert memory.data["location"] == "JP Nagar"
    assert memory.data["selected_slot"] in {"7:00 PM", ""}
    assert "location" not in response.response.lower() or "jp nagar" in response.response.lower()


@pytest.mark.parametrize(
    ("message", "field", "expected"),
    [
        ("June 24", "preferred_date", "June 24"),
        ("June 25", "preferred_date", "June 25"),
        ("Tomorrow", "preferred_date", "2026-06-24"),
        ("Actually Hostage", "room", "Hostage"),
        ("Actually Murder Mystery", "room", "Murder Mystery"),
        ("Actually Undercover", "room", "Undercover"),
        ("Actually 5 PM", "preferred_time", "5:00 PM"),
        ("Actually 6 PM", "preferred_time", "6:00 PM"),
        ("Actually 7 PM", "preferred_time", "7:00 PM"),
    ],
)
def test_booking_updates_do_not_trigger_loop_escalation(
    tmp_path: Path,
    message: str,
    field: str,
    expected,
) -> None:
    memory = _memory(tmp_path, intent="escape_room_inquiry", booking_started=True)
    memory.data["conversation"] = [
        {"role": "agent", "content": "Which location would you prefer?"},
        {"role": "agent", "content": "Which location would you prefer?"},
        {"role": "agent", "content": "Which location would you prefer?"},
    ]
    memory.save()

    result = EscalationAgent(memory).evaluate(
        message,
        _neutral(),
        AgentResponse(
            response="Which location would you prefer?",
            intent="escape_room_inquiry",
            next_agent="booking_agent",
            should_handoff=False,
        ),
    )

    assert result.escalate is False


@pytest.mark.parametrize(
    "question",
    [
        "What's parking?",
        "What's the cancellation policy?",
        "Do you have food?",
        "When should we arrive?",
        "Can you share directions?",
        "Is there a dress code?",
        "Are there age restrictions?",
        "How long is the game?",
    ],
)
def test_qualification_faq_answers_and_resumes_location_question(tmp_path: Path, question: str) -> None:
    agent = _inbound(tmp_path)
    agent.handle_message("We want an escape room for 4 adults.")
    assert agent.memory.data["participants"] == 4
    assert agent.memory.data["age_group"] == "adults"
    assert agent.qualification_agent._waiting_for == "location"

    response = agent.handle_message(question)["response"]

    assert response
    assert "location" in response.lower() or "koramangala" in response.lower()
    assert agent.qualification_agent._waiting_for == "location"


def test_second_qualification_faq_is_not_ignored(tmp_path: Path) -> None:
    agent = _inbound(tmp_path)
    agent.handle_message("We want an escape room for 4 adults.")

    first = agent.handle_message("What's parking?")["response"]
    second = agent.handle_message("What's cancellation?")["response"]

    assert "parking" in first.lower()
    assert "cancellation" in second.lower()
    assert agent.qualification_agent._waiting_for == "location"


@pytest.mark.parametrize(
    ("spoken", "digits"),
    [
        ("double nine eight double two three five four seven", "998223547"),
        ("triple nine eight two two three five four seven", "9998223547"),
        ("phone is nine eight seven six five four three two one zero", "9876543210"),
        ("mobile oh nine eight seven six five four three two one", "0987654321"),
        ("contact number double nine eight double three four zero three five seven", "9983340357"),
    ],
)
def test_spoken_phone_number_parsing(tmp_path: Path, spoken: str, digits: str) -> None:
    memory = _memory(tmp_path)
    assert memory._extract_phone(spoken) == digits


def test_name_correction_overwrites_existing_name(tmp_path: Path) -> None:
    memory = _memory(tmp_path, customer_name="Siddharth", first_name="Siddharth", last_name="")
    agent = BookingAgent(memory)

    agent.handle_message("Actually use Siddharth Khandelwal")

    assert memory.data["customer_name"] == "Siddharth Khandelwal"
    assert memory.data["first_name"] == "Siddharth"
    assert memory.data["last_name"] == "Khandelwal"


def test_second_name_correction_overwrites_latest_value(tmp_path: Path) -> None:
    memory = _memory(tmp_path, customer_name="Siddharth Khandelwal", first_name="Siddharth", last_name="Khandelwal")
    agent = BookingAgent(memory)

    agent.handle_message("Actually Sidd Khandelwal")

    assert memory.data["customer_name"] == "Sidd Khandelwal"
    assert memory.data["first_name"] == "Sidd"
    assert memory.data["last_name"] == "Khandelwal"


@pytest.mark.parametrize(
    ("message", "field"),
    [
        ("Tomorrow", "preferred_date"),
        ("7 PM", "preferred_time"),
        ("Hostage", "room"),
        ("My name is Siddharth", "customer_name"),
        ("phone is double nine eight double three four zero three five seven", "phone"),
    ],
)
def test_qualification_persists_valid_side_fields_while_location_missing(
    tmp_path: Path,
    message: str,
    field: str,
) -> None:
    agent = _inbound(tmp_path)
    agent.handle_message("We want an escape room for 4 adults.")
    response = agent.handle_message(message)["response"]

    assert agent.memory.data[field]
    assert "location" in response.lower() or "koramangala" in response.lower()
    assert agent.qualification_agent._waiting_for == "location"


def test_date_correction_clears_selected_slot_but_does_not_escalate(tmp_path: Path) -> None:
    memory = _memory(
        tmp_path,
        intent="escape_room_inquiry",
        booking_started=True,
        current_workflow="booking",
        room="Murder Mystery",
        location="JP Nagar",
        preferred_date="June 24",
        selected_slot="7:00 PM",
        participants=4,
        age_group="adults",
    )
    BookingAgent(memory).handle_message("Actually June 25")

    assert memory.data["preferred_date"] == "25 June"
    assert memory.data["selected_slot"] == ""


def test_room_correction_clears_selected_slot_but_does_not_escalate(tmp_path: Path) -> None:
    memory = _memory(
        tmp_path,
        intent="escape_room_inquiry",
        booking_started=True,
        current_workflow="booking",
        room="Murder Mystery",
        location="JP Nagar",
        preferred_date="June 24",
        selected_slot="7:00 PM",
        participants=4,
        age_group="adults",
    )
    BookingAgent(memory).handle_message("Actually Hostage")

    assert memory.data["room"] == "Hostage"
    assert memory.data["selected_slot"] == ""
