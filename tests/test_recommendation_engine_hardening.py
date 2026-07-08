from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from main import dispatch
from src.agents.inbound_agent import InboundAgent
from src.knowledge.knowledge_loader import KnowledgeLoader
from src.memory.conversation_memory import ConversationMemory
from src.services.recommendation_engine import RecommendationEngine


@pytest.fixture(autouse=True)
def offline_booking(monkeypatch: pytest.MonkeyPatch) -> None:
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


def _recommendation_agent(tmp_path: Path, **overrides) -> InboundAgent:
    agent = _agent(tmp_path)
    agent.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "event_type": "Escape Room",
            "participants": 4,
            "age_group": "adults",
            "location": "Whitefield",
            "experience_level": "beginner",
        }
    )
    agent.memory.data.update(overrides)
    agent.memory.save()
    return agent


def test_dispatch_recommends_murder_mystery_for_whitefield_first_timers(tmp_path: Path) -> None:
    agent = _recommendation_agent(tmp_path)

    result, _booking, active = dispatch("Just recommend one beginner-friendly room.", agent, None, "inbound_agent")

    assert active == "inbound_agent"
    assert "Murder Mystery" in result.response
    assert agent.memory.data["recommended_option"] == "Murder Mystery"
    assert "theme" not in result.response.lower()


def test_dispatch_recommends_hostage_for_whitefield_first_time_thrill(tmp_path: Path) -> None:
    agent = _recommendation_agent(tmp_path)

    result, _booking, _active = dispatch("Recommend something thrilling.", agent, None, "inbound_agent")

    assert "Hostage" in result.response
    assert agent.memory.data["recommended_option"] == "Hostage"


def test_just_recommend_one_has_no_theme_question_or_generic_fallback(tmp_path: Path) -> None:
    agent = _recommendation_agent(tmp_path, experience_level="")

    result, _booking, _active = dispatch("Just recommend one.", agent, None, "inbound_agent")

    assert agent.memory.data["recommended_option"]
    assert "theme" not in result.response.lower()
    assert not _contains_recommendation_failure(result.response)


def test_dispatch_uses_deterministic_fallback_when_engine_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _recommendation_agent(tmp_path)
    monkeypatch.setattr(RecommendationEngine, "recommend", lambda *_args, **_kwargs: None)

    result, _booking, _active = dispatch("What would you recommend?", agent, None, "inbound_agent")

    assert "Murder Mystery" in result.response
    assert agent.memory.data["recommended_option"] == "Murder Mystery"
    assert not _contains_recommendation_failure(result.response)


def test_one_hundred_randomized_dispatch_recommendation_conversations(tmp_path: Path) -> None:
    rng = random.Random(20260626)
    locations = ["Whitefield", "Koramangala", "JP Nagar"]
    ages = ["adults", "teens", "kids"]
    experiences = ["beginner", "experienced", ""]
    prompts = [
        "What would you recommend?",
        "Just recommend one.",
        "Recommend something beginner-friendly.",
        "Recommend something thrilling.",
        "Which room is best?",
    ]
    rooms_or_split = (
        "Murder Mystery", "Hostage", "Undercover", "Classified", "Bomb Defusal",
        "Missile Attack", "Forbidden Forest", "Wizarding Championship", "Multi-room",
    )
    for index in range(100):
        agent = _recommendation_agent(
            tmp_path / str(index),
            location=rng.choice(locations),
            participants=rng.randint(2, 12),
            age_group=rng.choice(ages),
            experience_level=rng.choice(experiences),
        )
        result, _booking, _active = dispatch(rng.choice(prompts), agent, None, "inbound_agent")

        assert result.response
        assert any(token.lower() in result.response.lower() for token in rooms_or_split)
        assert agent.memory.data["recommended_option"]
        assert not _contains_recommendation_failure(result.response)


def _contains_recommendation_failure(response: str) -> bool:
    lowered = response.lower()
    return any(
        phrase in lowered
        for phrase in (
            "can't retrieve recommendation",
            "cannot retrieve recommendation",
            "having trouble",
            "i don't have recommendation",
            "i cannot retrieve",
        )
    )
