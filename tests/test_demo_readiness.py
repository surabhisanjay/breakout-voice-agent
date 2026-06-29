from __future__ import annotations

import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from main import dispatch  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402
from src.agents.inbound_agent import InboundAgent  # noqa: E402
from src.knowledge.knowledge_loader import KnowledgeLoader  # noqa: E402


class RecordingComposer:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.last_error: str = ""
        self.last_latency: float = 0.0

    def compose(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return kwargs["draft"]


def make_agent(tmp_path: Path) -> tuple[InboundAgent, RecordingComposer]:
    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = ConversationMemory(tmp_path / "session.json")
    agent = InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
        use_ollama=False,
    )
    composer = RecordingComposer()
    agent.response_composer = composer
    return agent, composer


def assert_composed(agent: InboundAgent, composer: RecordingComposer, message: str, expected_mode: str):
    result = agent.handle_message(message)
    assert composer.calls, f"Response bypassed the composer: {message}"
    assert composer.calls[-1]["mode"].value == expected_mode
    assert agent.memory.data["conversation_mode"] == expected_mode
    return result


def test_demo_scenario_first_time_players(tmp_path: Path) -> None:
    agent, composer = make_agent(tmp_path)
    result = assert_composed(agent, composer, "What would you recommend for first timers?", "recommendation")
    assert "Murder Mystery" in result.response
    assert "Hostage" in result.response
    assert result.response.count("?") <= 1


def test_demo_scenario_couple_date(tmp_path: Path) -> None:
    agent, composer = make_agent(tmp_path)
    # Relationship-based recommendations are a deterministic backend answer;
    # Vapi owns final wording in production, so no composer call is required.
    result = agent.handle_message("We are a couple planning a date. What would you recommend?")
    assert "Murder Mystery" in result.response
    assert "teamwork" in result.response
    assert "Hostage" in result.response
    assert "Prison Break" not in result.response


def test_demo_scenario_family_with_children(tmp_path: Path) -> None:
    agent, composer = make_agent(tmp_path)
    agent.memory.data["location"] = "Whitefield"
    result = assert_composed(agent, composer, "We have 5 children aged 11. What would you recommend?", "recommendation")
    assert "Murder Mystery" in result.response
    assert "Hostage" in result.response


def test_demo_scenario_corporate_sales(tmp_path: Path) -> None:
    agent, composer = make_agent(tmp_path)
    result = assert_composed(agent, composer, "We are a team of 20 employees.", "sales")
    assert "team of 20" in result.response
    assert "Scavenger Hunt" in result.response


def test_demo_scenario_running_late(tmp_path: Path) -> None:
    agent, composer = make_agent(tmp_path)
    result = assert_composed(agent, composer, "We are running 30 minutes late.", "rescue")
    assert "Don't worry" in result.response
    assert "back to back" in result.response


def test_demo_scenario_briefing(tmp_path: Path) -> None:
    agent, composer = make_agent(tmp_path)
    result = assert_composed(agent, composer, "Can you explain the briefing before the game?", "faq")
    assert "50 minutes" in result.response
    assert "20 minutes before" in result.response
    assert result.next_agent == "inbound_agent"
    assert result.should_handoff is False


def test_demo_scenario_not_escaping(tmp_path: Path) -> None:
    agent, composer = make_agent(tmp_path)
    result = assert_composed(agent, composer, "What happens if we don't escape?", "faq")
    assert "not actually locked" in result.response


def test_demo_scenario_topic_switch_to_faq_preserves_intake(tmp_path: Path) -> None:
    agent, composer = make_agent(tmp_path)
    first, booking, active = dispatch("We need a corporate event for 20 employees.", agent, None, "inbound_agent")
    assert agent.memory.data["participants"] == 20
    second, _, _ = dispatch("What food options do you have?", agent, booking, active)
    assert "Food options include" in second.response
    assert agent.memory.data["participants"] == 20
    assert agent.memory.data["intent"] == "corporate_event"
    assert composer.calls[-1]["mode"].value == "faq"


def test_demo_scenario_recommendation_precedes_qualification(tmp_path: Path) -> None:
    agent, composer = make_agent(tmp_path)
    # New flow: without an explicit recommendation request, the agent qualifies first.
    result = assert_composed(agent, composer, "We are 7 friends and none of us have played before.", "sales")
    assert "adult" in result.response.lower() or "kids" in result.response.lower() or "age" in result.response.lower()

    # After providing age_group, location comes before recommendation.
    result2 = agent.handle_message("We are all adults.")
    assert result2.response == "Which location would you like to visit?"


def test_demo_scenario_multiple_questions(tmp_path: Path) -> None:
    agent, composer = make_agent(tmp_path)
    result = assert_composed(agent, composer, "What locations do you have and what food options are available?", "faq")
    assert "Koramangala" in result.response
    assert "Whitefield" in result.response
    assert "continental food" in result.response


def test_demo_scenario_booking_mode(tmp_path: Path) -> None:
    agent, composer = make_agent(tmp_path)
    result = assert_composed(agent, composer, "I want to book for Saturday.", "booking")
    assert "book" in result.response.lower() or "event" in result.response.lower()


def test_same_topic_followup_does_not_clear_context(tmp_path: Path) -> None:
    agent, _ = make_agent(tmp_path)
    _, booking, active = dispatch("We need a corporate event for 20 employees.", agent, None, "inbound_agent")
    dispatch("The 20 employees prefer Whitefield.", agent, booking, active)
    assert agent.memory.data["participants"] == 20
    assert agent.memory.data["location"] == "Whitefield"
    assert agent.memory.data["intent"] == "corporate_event"


def test_new_sessions_do_not_share_mutable_memory(tmp_path: Path) -> None:
    first = ConversationMemory(tmp_path / "first.json")
    second = ConversationMemory(tmp_path / "second.json")
    first.data["discussed_options"].append("Murder Mystery")
    first.data["customer_preferences"].append("story-driven")

    assert second.data["discussed_options"] == []
    assert second.data["customer_preferences"] == []
