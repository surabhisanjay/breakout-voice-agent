from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from main import build_inbound_agent, dispatch
from src.agents.escalation_agent import EscalationAgent
from src.agents.handoff_summary_agent import HandoffSummaryAgent
from src.agents.sentiment_agent import SentimentAgent
from src.core.agent_response import AgentResponse
from src.memory.conversation_memory import ConversationMemory


def make_memory(tmp_path: Path) -> ConversationMemory:
    return ConversationMemory(tmp_path / "session.json")


def test_sentiment_history_tracks_multiple_conversation_stages(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    agent = SentimentAgent(memory)

    excited = agent.analyze("This sounds fun, we are excited.", stage="recommendation")
    hesitant = agent.analyze("I am not sure. I need to check with my friends.", stage="booking")

    assert excited.sentiment == "excited"
    assert hesitant.sentiment == "hesitant"
    assert [item["stage"] for item in memory.data["sentiment_history"]] == ["recommendation", "booking"]


def test_repeated_frustration_recommends_escalation(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    agent = SentimentAgent(memory)

    first = agent.analyze("I am frustrated because this is still not working.", stage="booking")
    second = agent.analyze("You keep asking me the same thing.", stage="booking")

    assert first.escalation_recommended is False
    assert second.sentiment == "frustrated"
    assert second.escalation_recommended is True


def test_explicit_human_request_creates_escalation_summary(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data.update({
        "customer_name": "Riya",
        "phone": "9876543210",
        "participants": 6,
        "location": "Whitefield",
        "intent": "escape_room_inquiry",
        "event_type": "Escape Room",
    })
    sentiment = SentimentAgent(memory).analyze("Please connect me to a human agent.", stage="booking")
    result = AgentResponse("One moment.", "escape_room_inquiry", "inbound_agent", False)

    escalation = EscalationAgent(memory).evaluate(
        "Please connect me to a human agent.", sentiment, result
    )

    assert escalation.escalate is True
    assert "explicitly requested" in escalation.reason
    assert "Riya" in escalation.summary
    assert "Whitefield" in escalation.summary


def test_booking_failure_recommends_escalation(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    sentiment = SentimentAgent(memory).analyze("Please try the booking.", stage="booking")
    result = AgentResponse(
        "I'm having trouble retrieving availability.",
        "escape_room_inquiry",
        "booking_agent",
        False,
        booking_result={"status": "error"},
    )

    escalation = EscalationAgent(memory).evaluate("Please try the booking.", sentiment, result)

    assert escalation.escalate is True
    assert "failure" in escalation.reason.lower()


def test_handoff_summary_is_concise_and_lists_outstanding_questions(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    memory.data.update({
        "customer_name": "Riya",
        "phone": "9876543210",
        "participants": 12,
        "age_group": "adults",
        "experience_level": "beginner",
        "location": "Whitefield",
        "preferred_date": "20 June",
        "room": "Bomb Defusal",
        "intent": "escape_room_inquiry",
        "event_type": "Escape Room",
        "current_workflow": "booking",
        "sentiment": "frustrated",
    })

    summary = HandoffSummaryAgent().generate(
        memory.data,
        {"escalate": True, "reason": "Availability lookup failed twice"},
    )

    assert summary["booking_status"] == "in progress"
    assert summary["room_preference"] == "Bomb Defusal"
    assert summary["outstanding_questions"] == []
    assert len(summary["summary"].split()) < 70


def test_dispatch_adds_intelligence_metadata_without_changing_route(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("BREAKOUT_GPT_REASONER", "false")
    memory = make_memory(tmp_path)
    inbound = build_inbound_agent(Namespace(model=None, no_openai=True), memory)

    result, booking, active_agent = dispatch(
        "We are six adults.", inbound, None, "inbound_agent"
    )

    assert booking is None
    assert active_agent == "inbound_agent"
    assert result.sentiment_analysis["sentiment"] == "neutral"
    assert result.escalation["escalate"] is False
    assert memory.data["participants"] == 6
    assert memory.data["age_group"] == "adults"


def test_dispatch_preserves_spoken_response_when_escalation_is_detected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("BREAKOUT_GPT_REASONER", "false")
    memory = make_memory(tmp_path)
    inbound = build_inbound_agent(Namespace(model=None, no_openai=True), memory)

    result, _, _ = dispatch(
        "Please connect me to a human agent.", inbound, None, "inbound_agent"
    )

    assert result.response
    assert result.escalation["escalate"] is True
    assert result.handoff_summary is not None
    assert "summary" in result.handoff_summary
