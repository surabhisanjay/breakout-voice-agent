from argparse import Namespace
from pathlib import Path

from main import _repair_repeated_question, build_inbound_agent
from src.core.agent_response import AgentResponse
from src.memory.conversation_memory import ConversationMemory


def _result(text: str) -> AgentResponse:
    return AgentResponse(
        response=text,
        intent="escape_room_inquiry",
        next_agent="inbound_agent",
        should_handoff=False,
    )


def _inbound(tmp_path: Path):
    memory = ConversationMemory(tmp_path / "session.json")
    return build_inbound_agent(Namespace(model=None, no_openai=True), memory)


def test_answered_question_advances_to_next_missing_field(tmp_path: Path) -> None:
    inbound = _inbound(tmp_path)
    inbound.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "event_type": "Escape Room",
            "participants": 6,
        }
    )
    inbound.memory.add_turn("agent", "How many people are joining?")
    result = _result("How many people are joining?")

    _repair_repeated_question("We are six.", inbound, result)

    assert "how many" not in result.response.lower()
    assert "adults" in result.response.lower()
    assert inbound.qualification_agent._waiting_for == "age_group"


def test_unanswered_question_uses_new_wording_then_stops_looping(tmp_path: Path) -> None:
    inbound = _inbound(tmp_path)
    inbound.memory.data.update(
        {"intent": "escape_room_inquiry", "event_type": "Escape Room", "participants": 4, "age_group": "adults"}
    )
    original = "Which location would you like to visit?"
    inbound.memory.add_turn("agent", original)

    responses = []
    for _ in range(3):
        inbound.memory.add_turn("customer", "I'm not sure.")
        inbound.memory.add_turn("agent", original)
        result = _result(original)
        _repair_repeated_question("I'm not sure.", inbound, result)
        responses.append(result.response)

    assert original not in responses
    assert len(set(responses)) == 3
    assert responses[-1].endswith("Share it when you're ready.")
    assert not responses[-1].endswith("?")


def test_answer_text_is_kept_when_repeated_followup_is_removed(tmp_path: Path) -> None:
    inbound = _inbound(tmp_path)
    inbound.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "event_type": "Escape Room",
            "participants": 4,
            "age_group": "adults",
            "location": "Whitefield",
        }
    )
    current = "Parking is available at Whitefield. Which location would you like to visit?"
    inbound.memory.add_turn("agent", current)
    result = _result(current)

    _repair_repeated_question("Is parking available?", inbound, result)

    assert result.response.startswith("Parking is available at Whitefield.")
    assert "which location" not in result.response.lower()

