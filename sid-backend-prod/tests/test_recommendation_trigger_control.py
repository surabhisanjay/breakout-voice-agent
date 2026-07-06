from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.inbound_agent import InboundAgent
from src.knowledge.knowledge_loader import KnowledgeLoader
from src.memory.conversation_memory import ConversationMemory


def make_agent(tmp_path: Path) -> InboundAgent:
    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = ConversationMemory(tmp_path / "session.json")
    return InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
        use_ollama=False,
        use_reasoner=False,
    )


def test_no_recommendation_without_request(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    first = agent.handle_message("Book for five people.")
    assert "Have you done an escape room before" in first.response

    second = agent.handle_message("First time.")
    assert "adults, kids, or a mix" in second.response
    assert "Murder Mystery" not in second.response
    assert "Hostage" not in second.response

    third = agent.handle_message("We are all adults.")
    assert third.response == "Which location would you like to visit?"
    assert "Murder Mystery" not in third.response
    assert agent.memory.data["recommended_option"] == ""


def test_locations_not_listed_unprompted(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    agent.handle_message("Book for five people.")
    agent.handle_message("First time.")
    response = agent.handle_message("Adults.").response

    assert response == "Which location would you like to visit?"
    assert "Koramangala" not in response
    assert "Whitefield" not in response
    assert "JP Nagar" not in response


def test_location_list_only_after_explicit_request(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    response = agent.handle_message("What locations do you have?").response

    assert "Koramangala" in response
    assert "Whitefield" in response
    assert "JP Nagar" in response


def test_recommendation_only_after_request(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    agent.handle_message("Book for five people.")
    agent.handle_message("First time.")
    agent.handle_message("Adults.")
    no_recommendation = agent.handle_message("Whitefield.").response

    assert no_recommendation == "Do you already have a room in mind, or would you like a recommendation?"
    assert agent.memory.data["recommended_option"] == ""

    recommendation = agent.handle_message("Recommend one.").response

    assert "Murder Mystery" in recommendation
    assert agent.memory.data["recommended_option"]


def test_no_duplicate_recommendation_after_location(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    agent.handle_message("Book for five people.")
    agent.handle_message("First time.")
    agent.handle_message("Adults.")
    recommendation = agent.handle_message("What do you recommend?").response
    after_location = agent.handle_message("Whitefield.").response

    assert "Murder Mystery" in recommendation
    assert "Murder Mystery" not in after_location
    assert "Hostage" not in after_location


def test_best_selling_room_question(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    response = agent.handle_message("Which is the best selling room at Whitefield?").response

    assert response == "Murder Mystery is usually the most popular choice at Whitefield."


def test_tomorrow_is_accepted(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    agent.handle_message("Book Murder Mystery at Whitefield for five adults.")
    response = agent.handle_message("Tomorrow").response

    assert agent.memory.data["preferred_date"] == "Tomorrow"
    assert "didn't quite catch" not in response


def test_booking_intent_stable_after_date(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    agent.handle_message("Book Murder Mystery at Whitefield for five adults.")
    agent.handle_message("Tomorrow")

    assert agent.memory.data["intent"] == "escape_room_inquiry"


def test_booking_intent_stable_after_location(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    agent.handle_message("Book for five people.")
    agent.handle_message("First time.")
    agent.handle_message("Adults.")
    agent.handle_message("Whitefield.")

    assert agent.memory.data["intent"] == "escape_room_inquiry"


def test_booking_intent_stable_after_recommendation(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    agent.handle_message("Book for five people.")
    agent.handle_message("First time.")
    agent.handle_message("Adults.")
    agent.handle_message("Whitefield.")
    agent.handle_message("Recommend one.")

    assert agent.memory.data["intent"] == "escape_room_inquiry"
