from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.services.phase1_state import (  # noqa: E402
    PHASE1_BUSINESS_STATE_FIELDS,
    build_phase1_business_state,
    mark_customer_reply_after_follow_up,
)
from src.agents.escalation_agent import EscalationAgent  # noqa: E402
from src.agents.sentiment_agent import SentimentAgent  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402


def _result(**values):
    defaults = {
        "intent": "general_faq",
        "intent_confidence": 0.82,
        "next_agent": "inbound_agent",
        "escalation": {},
        "sentiment_analysis": {"sentiment": "neutral", "confidence": 0.7},
        "debug": {},
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def _assert_complete(state: dict) -> None:
    assert set(PHASE1_BUSINESS_STATE_FIELDS).issubset(state)


def test_general_inquiry_and_information_request_are_follow_up_eligible() -> None:
    inquiry = build_phase1_business_state({}, _result())
    information = build_phase1_business_state(
        {"conversation": [{"role": "customer", "content": "Can you send more information and pictures?"}]},
        _result(intent="information_request"),
    )

    _assert_complete(inquiry)
    assert inquiry["follow_up_eligible"] is True
    assert inquiry["follow_up_reason"] == "general_inquiry_unresolved"
    assert information["conversation_status"] == "information_requested"
    assert information["follow_up_reason"] == "information_requested"


def test_booking_started_and_missed_call_publish_phase1_state() -> None:
    abandoned = build_phase1_business_state(
        {"booking_started": True, "current_workflow": "booking"},
        _result(intent="escape_room_inquiry"),
    )
    missed = build_phase1_business_state(
        {"call_status": "missed"},
        _result(intent="missed_call"),
    )

    assert abandoned["conversation_status"] == "booking_started"
    assert abandoned["follow_up_eligible"] is True
    assert abandoned["follow_up_reason"] == "booking_started_not_completed"
    assert missed["conversation_status"] == "missed_call"
    assert missed["follow_up_eligible"] is True
    assert missed["follow_up_reason"] == "missed_call"


def test_human_request_and_negative_sentiment_stop_automated_follow_up() -> None:
    human = build_phase1_business_state(
        {"escalation_state": {"escalate": True, "reason": "Customer requested human", "priority": "medium"}},
        _result(
            intent="human_request",
            next_agent="escalation_agent",
            escalation={"escalate": True, "reason": "Customer requested human", "priority": "medium"},
        ),
    )
    negative = build_phase1_business_state(
        {"sentiment": "angry"},
        _result(
            next_agent="escalation_agent",
            escalation={"escalate": True, "reason": "Customer anger detected", "priority": "high"},
            sentiment_analysis={"sentiment": "angry", "confidence": 0.94},
        ),
    )

    assert human["conversation_status"] == "waiting_for_human"
    assert human["waiting_for_human"] is True
    assert human["follow_up_eligible"] is False
    assert negative["negative_sentiment"] is True
    assert negative["priority"] == "high"
    assert negative["follow_up_reason"] == "waiting_for_human"


def test_customer_reply_after_follow_up_is_state_only_and_stops_eligibility() -> None:
    memory = {"follow_up": {"sent_at": "2026-07-05T10:00:00+00:00"}}

    marked = mark_customer_reply_after_follow_up(memory, received_at="2026-07-05T10:05:00+00:00")
    state = build_phase1_business_state(memory, _result())

    assert marked is True
    assert state["customer_replied"] is True
    assert state["follow_up_eligible"] is False
    assert state["follow_up_reason"] == "customer_replied"


def test_terminal_and_human_owned_states_are_not_follow_up_eligible() -> None:
    paid = build_phase1_business_state({"paymentStatus": "PAID"}, _result())
    closed = build_phase1_business_state({"conversation_closed": True}, _result())
    owned = build_phase1_business_state(
        {"human_taken_over": True, "escalation_state": {"escalate": True, "status": "accepted"}},
        _result(next_agent="escalation_agent", escalation={"escalate": True, "status": "accepted"}),
    )

    assert paid["follow_up_eligible"] is False
    assert paid["follow_up_reason"] == "payment_paid"
    assert closed["conversation_closed"] is True
    assert closed["conversation_status"] == "closed"
    assert owned["human_taken_over"] is True
    assert owned["conversation_status"] == "human_owned"


def test_document_negative_sentiment_phrase_escalates_immediately_at_high_priority(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "negative-sentiment.json")
    message = "This service is terrible."

    sentiment = SentimentAgent(memory).analyze(message)
    escalation = EscalationAgent(memory).evaluate(message, sentiment)
    state = build_phase1_business_state(memory.data, _result(
        next_agent="escalation_agent",
        escalation=escalation.to_dict(),
        sentiment_analysis=sentiment.to_dict(),
    ))

    assert sentiment.sentiment == "angry"
    assert escalation.escalate is True
    assert escalation.priority == "high"
    assert state["negative_sentiment"] is True
    assert state["follow_up_eligible"] is False
