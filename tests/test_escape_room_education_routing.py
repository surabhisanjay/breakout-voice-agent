from __future__ import annotations

import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.inbound_agent import InboundAgent  # noqa: E402
from src.knowledge.knowledge_loader import KnowledgeLoader  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402
from src.services.question_classifier import QuestionClassifier  # noqa: E402


def make_agent(tmp_path: Path) -> InboundAgent:
    return InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=ConversationMemory(tmp_path / "session.json"),
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
        use_ollama=False,
        use_reasoner=False,
    )


def assert_education_only(agent: InboundAgent, message: str) -> str:
    result = agent.handle_message(message)
    lowered = result.response.lower()

    assert "themed" in lowered
    assert "clues" in lowered
    assert "puzzles" in lowered
    assert "how many people" not in lowered
    assert "how many players" not in lowered
    assert result.recommendation["option"] == ""
    assert result.next_agent == "inbound_agent"
    assert agent.qualification_agent._waiting_for == ""
    return result.response


def test_tell_me_about_escape_rooms_educates_without_qualification(tmp_path: Path) -> None:
    response = assert_education_only(make_agent(tmp_path), "Tell me about escape rooms")

    assert "recommendation or just learning" in response.lower()


def test_how_do_escape_rooms_work_uses_canonical_education_response(tmp_path: Path) -> None:
    assert_education_only(make_agent(tmp_path), "How do escape rooms work?")


def test_tell_me_more_does_not_recommend_without_context(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    response = assert_education_only(agent, "Tell me more about your escape rooms")

    assert "game master" in response.lower()
    assert "murder mystery" not in response.lower()
    assert "classified" not in response.lower()


def test_what_is_an_escape_room_does_not_begin_qualification(tmp_path: Path) -> None:
    assert_education_only(make_agent(tmp_path), "What is an escape room?")


def test_education_phrases_are_classified_as_faq() -> None:
    messages = (
        "Tell me about escape rooms",
        "Tell me about your escape rooms",
        "Tell me more about your escape rooms",
        "How do escape rooms work?",
        "Explain escape rooms",
        "What are escape rooms?",
    )

    for message in messages:
        analysis = QuestionClassifier.classify(message)
        assert analysis.asked_question is True
        assert analysis.question_type == "faq"
        assert analysis.topic == "escape room education"
