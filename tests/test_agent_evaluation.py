from __future__ import annotations

import sys
from pathlib import Path
import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.memory.conversation_memory import ConversationMemory
from src.agents.learning_agent import AgentEvaluator, LearningAgent
from src.core.agent_response import AgentResponse
from src.agents.scoring_agent import ScoreResult
from src.agents.sentiment_agent import SentimentResult


def test_agent_evaluator_scores_robotic_fallback_low() -> None:
    # A robotic, dry fallback response
    eval_result = AgentEvaluator.evaluate(
        customer_message="Why is it so expensive?",
        agent_response="I don't have that detail to hand right now, but our team can help. May I take your name and number so someone can call you back?",
        intent="escape_room_inquiry",
        missing_fields=["phone"],
        next_agent="inbound_agent",
        should_handoff=False,
        sentiment="frustrated",
        state={}
    )

    assert eval_result.intent_identified == 2
    assert eval_result.empathy == 0
    assert eval_result.question_answered == 0
    assert eval_result.naturalness == 0
    assert eval_result.flow_advancement == 0
    assert eval_result.context_retained == 2
    assert eval_result.total_score <= 6
    assert len(eval_result.reasons) > 0


def test_agent_evaluator_scores_empathetic_high() -> None:
    # An empathetic, conversational response answering a question and advancing booking
    eval_result = AgentEvaluator.evaluate(
        customer_message="Why is it so expensive?",
        agent_response="I completely get that! Since you're coming as a group, let me check what options make the most sense for you. About how many people are joining?",
        intent="escape_room_inquiry",
        missing_fields=["participants"],
        next_agent="qualification_agent",
        should_handoff=False,
        sentiment="frustrated",
        state={}
    )

    assert eval_result.intent_identified == 2
    assert eval_result.empathy == 2
    assert eval_result.question_answered == 2
    assert eval_result.naturalness == 2
    assert eval_result.flow_advancement == 2
    assert eval_result.context_retained == 2
    assert eval_result.total_score >= 12


def test_agent_evaluator_safety_objection_scoring() -> None:
    eval_result = AgentEvaluator.evaluate(
        customer_message="Will we get trapped inside?",
        agent_response="No need to worry, you're not actually locked in and can exit at any time. How many players will be joining?",
        intent="escape_room_inquiry",
        missing_fields=["participants"],
        next_agent="qualification_agent",
        should_handoff=False,
        sentiment="hesitant",
        state={}
    )

    assert eval_result.intent_identified == 2
    assert eval_result.empathy == 2
    assert eval_result.question_answered == 2
    assert eval_result.total_score >= 12


def test_agent_evaluator_context_retained_repetitive() -> None:
    eval_result = AgentEvaluator.evaluate(
        customer_message="How many people can we bring?",
        agent_response="Our games hold up to 12 players. By the way, how many players will be in your group?",
        intent="escape_room_inquiry",
        missing_fields=["participants"],
        next_agent="qualification_agent",
        should_handoff=False,
        sentiment="neutral",
        state={"participants": 8}
    )

    assert eval_result.context_retained == 1



def test_learning_agent_records_evaluation_history(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    learning_agent = LearningAgent(memory)

    result = AgentResponse(
        response="Got it! To suggest the perfect game, how many people are joining?",
        intent="escape_room_inquiry",
        intent_confidence=0.9,
        next_agent="qualification_agent",
        should_handoff=False,
        state={},
        missing_fields=["participants"]
    )
    sentiment = SentimentResult(
        sentiment="neutral",
        confidence=0.8,
        escalation_recommended=False,
        reason="",
        stage="sales"
    )
    score = ScoreResult(
        lead_score=27,
        booking_readiness=0,
        sentiment_score=70,
        escalation_risk=0,
        conversation_quality=80,
        reasons=[]
    )

    snapshot = learning_agent.observe_turn(
        message="Hi, I want to book an escape room.",
        result=result,
        sentiment=sentiment,
        score=score
    )

    # Check metrics and report contain latest evaluation
    assert "latest_agent_evaluation" in snapshot.metrics
    assert snapshot.metrics["latest_agent_evaluation"]["total_score"] > 0
    assert "latest_agent_evaluation" in snapshot.report

    # Check memory has history
    assert "agent_eval_history" in memory.data
    assert len(memory.data["agent_eval_history"]) == 1
    assert memory.data["agent_eval_history"][0]["customer_message"] == "Hi, I want to book an escape room."
