from __future__ import annotations

import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.inbound_agent import InboundAgent  # noqa: E402
from src.knowledge.knowledge_loader import KnowledgeLoader  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402


def make_agent(tmp_path: Path) -> InboundAgent:
    return InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=ConversationMemory(tmp_path / "session.json"),
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


def test_first_time_group_guides_before_qualifying(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    response = agent.handle_message("We are four friends and want to book an escape room.").response

    assert "escape room before" in response.lower() or "first one" in response.lower()
    assert response.count("?") <= 1


def test_first_time_friends_then_which_would_you_choose_resolves_rooms(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We are four friends and it is our first time.")
    response = agent.handle_message("Which would you choose?").response

    assert "Murder Mystery" in response
    assert "location" not in response.lower()
    assert response.count("?") <= 1


def test_couple_recommendation_is_personal_and_not_form_like(tmp_path: Path) -> None:
    response = make_agent(tmp_path).handle_message("We are a couple. What would you recommend?").response

    assert "Murder Mystery" in response
    assert "teamwork" in response.lower() or "story" in response.lower()
    assert "To finalize" not in response


def test_room_comparison_answers_current_rooms(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We have never done an escape room before.")
    response = agent.handle_message("Can you compare these two?").response

    assert "Murder Mystery" in response
    assert "Hostage" in response
    assert "phone" not in response.lower()


def test_which_one_would_you_recommend_uses_discussed_rooms(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We have never done an escape room before.")
    response = agent.handle_message("Which one would you recommend?").response

    assert "Murder Mystery" in response
    assert "first visit" in response.lower() or "first-time" in response.lower()


def test_room_explanation_answers_before_guiding(tmp_path: Path) -> None:
    response = make_agent(tmp_path).handle_message("What is Hostage like?").response

    assert "Hostage" in response
    assert "urgency" in response.lower() or "rescue" in response.lower()
    assert response.count("?") <= 1


def test_food_recommendation_uses_last_food_topic(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("What food options do you have?")
    response = agent.handle_message("Which is best?").response

    assert "Indian buffet" in response
    assert "lighter" in response.lower()
    assert response.count("?") <= 1


def test_location_recommendation_uses_last_location_topic(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("What locations do you have?")
    response = agent.handle_message("Which would you choose?").response

    assert "Koramangala" in response
    assert "Whitefield" in response
    assert "JP Nagar" in response


def test_faq_interruption_answers_then_resumes_without_robotic_transition(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We need a corporate event for 30 employees.")
    response = agent.handle_message("What food options do you have?").response

    assert "Food options" in response
    assert "To finalize" not in response
    assert "location" in response.lower() or "koramangala" in response.lower()


def test_booking_continuation_after_recommendation_preserves_context(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We are four adults visiting Whitefield.")
    agent.handle_message("It is our first time.")
    response = agent.handle_message("Book that.").response

    assert "room in mind" in response or "recommendation" in response
    assert "captured all the information" not in response
