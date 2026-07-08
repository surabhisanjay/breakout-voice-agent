from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from unittest.mock import MagicMock


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.evaluation_agent import EvaluationAgent
from src.agents.follow_up_agent import FollowUpAgent


def _memory(deadline: datetime) -> dict:
    return {
        "booking_id": "bk_123",
        "venue_id": "venue_123",
        "bookingStatus": "RESERVED",
        "paymentStatus": "UNPAID",
        "paymentUrl": "https://pay.example/order?pr=true",
        "paymentDeadline": deadline.isoformat(),
        "selected_slot": "7:00 PM",
    }


def test_follow_up_agent_generates_pending_payment_reminder() -> None:
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    memory = _memory(now + timedelta(minutes=11))
    orchestrator = MagicMock()
    orchestrator.check_payment_status.return_value = {
        "bookingId": "bk_123",
        "status": "RESERVED",
        "isPaid": False,
    }

    result = FollowUpAgent(orchestrator).evaluate(memory, now=now)

    assert result.action == "remind"
    assert result.should_send is True
    assert result.minutes_remaining == 11
    assert "https://pay.example/order?pr=true" in result.message
    orchestrator.check_payment_status.assert_called_once_with("venue_123", "bk_123")


def test_follow_up_agent_stops_when_payment_is_confirmed() -> None:
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    memory = _memory(now + timedelta(minutes=11))
    orchestrator = MagicMock()
    orchestrator.check_payment_status.return_value = {
        "bookingId": "bk_123",
        "status": "CONFIRMED",
        "isPaid": True,
    }

    result = FollowUpAgent(orchestrator).evaluate(memory, now=now)

    assert result.action == "stop"
    assert result.should_send is False
    assert memory["bookingStatus"] == "CONFIRMED"
    assert memory["paymentStatus"] == "PAID"


def test_follow_up_agent_marks_expired_without_manual_cancel() -> None:
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    memory = _memory(now - timedelta(minutes=1))
    orchestrator = MagicMock()
    orchestrator.check_payment_status.return_value = {
        "bookingId": "bk_123",
        "status": "EXPIRED",
        "isPaid": False,
    }

    result = FollowUpAgent(orchestrator).evaluate(memory, now=now)

    assert result.action == "expired"
    assert result.should_send is True
    assert memory["bookingStatus"] == "EXPIRED"
    assert memory["paymentStatus"] == "EXPIRED"
    assert memory["selected_slot"] == ""
    assert not hasattr(orchestrator, "cancel_booking") or not orchestrator.cancel_booking.called


def test_evaluation_agent_scores_clean_conversation_high() -> None:
    conversation = {
        "memory": {
            "participants": 4,
            "location": "Whitefield",
            "preferred_date": "Tomorrow",
            "room": "Murder Mystery",
            "selected_slot": "7:00 PM",
            "booking_id": "bk_123",
            "paymentStatus": "UNPAID",
        },
        "turns": [
            {"role": "customer", "text": "Book for tomorrow"},
            {"role": "agent", "response": "I found slots. Which one works?"},
        ],
    }

    result = EvaluationAgent().evaluate(conversation)

    assert result.score >= 88
    assert not result.flags
    assert result.category_scores["booking"] >= 90


def test_evaluation_agent_flags_forbidden_phrase_and_repeated_question() -> None:
    conversation = {
        "memory": {"booking_started": True},
        "turns": [
            {"role": "agent", "response": "May I know your good name?"},
            {"role": "agent", "response": "May I know your good name?"},
        ],
    }

    result = EvaluationAgent().evaluate(conversation)

    assert result.score < 90
    assert any(flag.startswith("robotic_or_forbidden_phrase") for flag in result.flags)
    assert "repeated_question" in result.flags
