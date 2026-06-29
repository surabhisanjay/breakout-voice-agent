from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.inbound_agent import InboundAgent  # noqa: E402
from src.knowledge.knowledge_loader import KnowledgeLoader  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402


def make_agent(tmp_path: Path) -> InboundAgent:
    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = ConversationMemory(tmp_path / "session.json")
    return InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
        use_ollama=False,
    )


def test_confusion_question_explains_escape_rooms_before_qualification(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    response = agent.handle_message("I am confused. What exactly happens inside an escape room?")["response"]

    assert "themed team game" in response
    assert "search for clues" in response
    assert "How many people" not in response


def test_frustration_repair_does_not_ask_for_contact_first(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    response = agent.handle_message("No no no, that is not what I asked. You are not understanding me.")["response"]

    assert "missed what you were asking" in response
    assert "name and number" not in response.lower()


def test_horror_preference_answers_before_participant_question(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    response = agent.handle_message("Do you have any scary or horror escape room? I want something spooky.")["response"]

    assert "horror" in response.lower()
    assert "jump-scare" in response.lower()
    assert "How many people" not in response


def test_thrill_preference_uses_location_context(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.memory.data.update({"intent": "escape_room_inquiry", "location": "Whitefield"})
    agent.memory.save()

    response = agent.handle_message("Which room has more thrill and pressure?")["response"]

    assert "Bomb Defusal" in response
    assert "Whitefield" in response


def test_budget_concern_answers_before_birthday_qualification(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    response = agent.handle_message("We are 14 kids for a birthday but I am worried about budget. What are the options?")["response"]

    assert "watch the budget" in response
    assert "escape-room only" in response
    assert "Which location" not in response


def test_discount_question_does_not_collect_name_or_promise_offer(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    response = agent.handle_message("It is a little expensive. Is there any discount available?")["response"]

    assert "confirmed discount information" in response
    assert "don't want to promise" in response
    assert "name" not in response.lower()


def test_price_objection_answers_without_booking_handoff(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    result = agent.handle_message("What is the price for four people?")

    assert "Pricing depends" in result["response"]
    assert result["route"]["next_agent"] == "inbound_agent"
    assert result["route"]["should_handoff"] is False


def test_slot_comparison_answers_timing_tradeoff(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    response = agent.handle_message("For Murder Mystery at Koramangala, should I take 3:15 or 5:30? We might be late.")["response"]

    assert "Choose 5:30" in response
    assert "buffer" in response
    assert "Murder Mystery is an investigation-style" not in response


def test_late_arrival_duration_is_not_slot_comparison(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    response = agent.handle_message("We have a 7 PM booking but we are running 30 minutes late. Can you help?")["response"]

    assert "scheduled time" in response
    assert "Choose 30" not in response


def test_room_comparison_answers_comparison_not_room_monologue(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    response = agent.handle_message("What is better for first timers, Murder Mystery or Hostage?")["response"]

    assert "Murder Mystery" in response
    assert "Hostage" in response
    assert "first visit" in response or "first timers" in response
    assert "offered at Koramangala" not in response


def test_location_comparison_recommends_whitefield_from_marathahalli(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    response = agent.handle_message("I am near Marathahalli. Which location should I choose?")["response"]

    assert "Whitefield" in response
    assert "first" in response.lower()


def test_direct_answer_resumes_pending_qualification(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("I want to book an escape room")
    assert agent.qualification_agent._waiting_for == "participants"

    response = agent.handle_message("What exactly happens inside an escape room?")["response"]

    assert "search for clues" in response
    assert "How many" in response or "players" in response
    assert agent.qualification_agent._waiting_for == "participants"
