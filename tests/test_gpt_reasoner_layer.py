from __future__ import annotations

import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.inbound_agent import InboundAgent  # noqa: E402
from src.knowledge.knowledge_loader import KnowledgeLoader  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402
from src.services.gpt_reasoner import GPTReasoner, ReasonerDecision  # noqa: E402


class StaticReasoner:
    def __init__(self, decision: ReasonerDecision | Exception | None) -> None:
        self.decision = decision
        self.last_error = ""
        self.enabled = True

    def decide(self, **_kwargs):
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision


def make_agent(tmp_path: Path, decision: ReasonerDecision | Exception | None = None) -> InboundAgent:
    agent = InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=ConversationMemory(tmp_path / "session.json"),
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
        use_reasoner=False,
    )
    agent.reasoner = StaticReasoner(decision)
    return agent


def test_reasoner_disabled_without_openai_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reasoner = GPTReasoner(enabled=True)

    decision = reasoner.decide(message="What about parking?", memory={}, pending_question="")

    assert decision is None


def test_reasoner_faq_interruption_answers_then_resumes_qualification(tmp_path: Path) -> None:
    agent = make_agent(
        tmp_path,
        ReasonerDecision(
            action="answer_faq",
            topic="what is an escape room",
            should_resume_qualification=True,
            confidence=0.9,
        ),
    )
    agent.handle_message("We want an escape room.")

    response = agent.handle_message("Before that, can you explain how escape rooms work?").response

    assert "escape room" in response.lower()
    assert "team game" in response.lower() or "themed room" in response.lower()
    assert "how many people" in response.lower() or "perfect game" in response.lower()


def test_reasoner_contextual_reference_compares_current_rooms(tmp_path: Path) -> None:
    agent = make_agent(
        tmp_path,
        ReasonerDecision(action="compare_rooms", confidence=0.92),
    )
    agent.memory.data["discussed_options"] = ["Murder Mystery", "Hostage"]
    agent.memory.data["recommended_option"] = "Murder Mystery and Hostage"
    agent.memory.data["last_discussed_topic"] = "rooms"
    agent.memory.save()

    response = agent.handle_message("Which one would you personally choose?").response

    assert "Murder Mystery" in response
    assert "Hostage" in response
    assert "location" not in response.lower()


def test_booking_complete_faq_does_not_restart_booking(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.memory.data["completed_booking"] = True
    agent.memory.data["customer_name"] = "Surabhi"
    agent.memory.data["location"] = "Whitefield"
    agent.memory.data["participants"] = 7
    agent.memory.save()

    response = agent.handle_message("What about parking?").response

    assert "parking" in response.lower()
    assert "Whitefield" in response
    assert agent.memory.data["completed_booking"] is True
    assert agent.memory.data["participants"] == 7


def test_reasoner_voice_repair_repeats_pending_question_without_advancing(tmp_path: Path) -> None:
    agent = make_agent(
        tmp_path,
        ReasonerDecision(action="repeat_previous_question", confidence=0.95),
    )
    first = agent.handle_message("We are a group of seven friends.")
    assert "age group" in first.response.lower()

    response = agent.handle_message("what?").response

    assert "age group" in response.lower()
    assert agent.memory.data["participants"] == 7
    assert agent.memory.data["age_group"] == ""


def test_reasoner_does_not_overwrite_protected_slots(tmp_path: Path) -> None:
    agent = make_agent(
        tmp_path,
        ReasonerDecision(
            action="recommend_location",
            confidence=0.9,
            protected_updates={"participants": 2, "age_group": "kids"},
        ),
    )
    agent.memory.data["participants"] = 6
    agent.memory.data["age_group"] = "adults"
    agent.memory.data["last_discussed_topic"] = "locations"
    agent.memory.save()

    agent.handle_message("Which location would you choose?")

    assert agent.memory.data["participants"] == 6
    assert agent.memory.data["age_group"] == "adults"


def test_reasoner_failure_falls_back_to_current_routing(tmp_path: Path) -> None:
    agent = make_agent(tmp_path, RuntimeError("reasoner unavailable"))

    response = agent.handle_message("What locations do you have?").response

    assert "Koramangala" in response
    assert "Whitefield" in response
    assert "JP Nagar" in response
