from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


PHASE1_BUSINESS_STATE_FIELDS = (
    "conversation_status",
    "booking_status",
    "payment_status",
    "customer_intent",
    "priority",
    "escalation_reason",
    "confidence_score",
    "follow_up_eligible",
    "follow_up_reason",
    "human_taken_over",
    "conversation_closed",
    "waiting_for_human",
    "customer_replied",
    "negative_sentiment",
    "timestamp",
)

_NEGATIVE_SENTIMENTS = {
    "angry",
    "frustrated",
    "negative",
    "upset",
    "ready to escalate",
    "ready_to_escalate",
}
_BOOKING_COMPLETE = {"booked", "completed", "confirmed", "success", "successful"}
_BOOKING_STOP = _BOOKING_COMPLETE | {"cancelled", "canceled", "expired", "released"}
_PAYMENT_COMPLETE = {"paid", "completed", "confirmed", "success", "successful"}
_PAYMENT_STOP = _PAYMENT_COMPLETE | {"cancelled", "canceled", "expired", "refunded"}
_CLOSED_STATUSES = {"closed", "resolved", "cancelled", "canceled", "lost"}
_HUMAN_OWNED_STATUSES = {"assigned", "accepted", "in_progress", "in progress", "human_owned"}
_MISSED_CALL_STATUSES = {"missed", "missed_call", "no_answer", "no-answer", "unanswered"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalised(value: Any) -> str:
    return str(value or "").strip().lower()


def _first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return ""


def _result_value(result: Any, name: str, default: Any = None) -> Any:
    if result is None:
        return default
    return getattr(result, name, default)


def _last_customer_message(memory: dict[str, Any]) -> str:
    conversation = memory.get("conversation")
    if not isinstance(conversation, list):
        return ""
    for turn in reversed(conversation):
        if not isinstance(turn, dict):
            continue
        role = _normalised(turn.get("role") or turn.get("speaker") or turn.get("speaker_type"))
        if role not in {"customer", "user", "human"}:
            continue
        return str(turn.get("content") or turn.get("text") or turn.get("message") or "").strip()
    return ""


def _sentiment_label(memory: dict[str, Any], result: Any) -> str:
    sentiment = _dict(_result_value(result, "sentiment_analysis", {}))
    stored = _dict(memory.get("sentiment_analysis"))
    return _normalised(
        _first_value(
            sentiment.get("sentiment"),
            sentiment.get("current_sentiment"),
            sentiment.get("overall_sentiment"),
            stored.get("sentiment"),
            stored.get("current_sentiment"),
            stored.get("overall_sentiment"),
            memory.get("sentiment"),
        )
    )


def _confidence_score(memory: dict[str, Any], result: Any) -> float:
    debug = _dict(_result_value(result, "debug", {}))
    reasoner = _dict(debug.get("reasoner_decision"))
    sentiment = _dict(_result_value(result, "sentiment_analysis", {}))
    candidates = (
        _result_value(result, "intent_confidence", None),
        reasoner.get("confidence"),
        sentiment.get("confidence"),
        memory.get("intent_confidence"),
        memory.get("sentiment_confidence"),
    )
    for value in candidates:
        if value in (None, ""):
            continue
        try:
            score = round(max(0.0, min(float(value), 1.0)), 4)
            if score > 0.0:
                return score
        except (TypeError, ValueError):
            continue
    return 0.0


def _customer_replied_after_follow_up(memory: dict[str, Any]) -> bool:
    if bool(memory.get("customer_replied_after_follow_up") or memory.get("customer_replied")):
        return True
    follow_up = _dict(memory.get("follow_up"))
    return _normalised(follow_up.get("status")) == "replied"


def mark_customer_reply_after_follow_up(
    memory: dict[str, Any],
    *,
    received_at: str | None = None,
) -> bool:
    """Record a reply only when an external follow-up marker already exists.

    This does not schedule or send a follow-up. It lets a future Closiro-owned
    sender mark a follow-up as sent and have the next inbound message stop it.
    """

    follow_up = _dict(memory.get("follow_up"))
    sent_marker = _first_value(
        memory.get("last_follow_up_sent_at"),
        memory.get("follow_up_sent_at"),
        follow_up.get("sent_at"),
        follow_up.get("last_sent_at"),
    )
    if not sent_marker:
        return False
    timestamp = received_at or utc_now_iso()
    memory["customer_replied"] = True
    memory["customer_replied_after_follow_up"] = True
    memory["last_customer_reply_at"] = timestamp
    follow_up.update({"status": "replied", "customer_replied_at": timestamp})
    memory["follow_up"] = follow_up
    return True


def build_phase1_business_state(
    memory: dict[str, Any],
    result: Any = None,
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Derive the Phase 1 business state consumed by Closiro.

    The function is deliberately state-only: it sends no WhatsApp message,
    creates no timer, and performs no assignment or manager workflow.
    """

    escalation = _dict(_result_value(result, "escalation", {})) or _dict(memory.get("escalation_state"))
    escalation_active = bool(
        escalation.get("escalate")
        or escalation.get("required")
        or _result_value(result, "next_agent", "") == "escalation_agent"
    )
    escalation_status = _normalised(escalation.get("status") or memory.get("escalation_status"))
    escalation_reason = str(escalation.get("reason") or memory.get("escalation_reason") or "").strip()

    booking_status = str(
        _first_value(memory.get("bookingStatus"), memory.get("booking_status"), "not_started")
    ).strip()
    payment_status = str(
        _first_value(memory.get("paymentStatus"), memory.get("payment_status"), "not_started")
    ).strip()
    booking_normalised = _normalised(booking_status)
    payment_normalised = _normalised(payment_status)

    sentiment_label = _sentiment_label(memory, result)
    negative_sentiment = sentiment_label in _NEGATIVE_SENTIMENTS or any(
        term in sentiment_label for term in ("angry", "frustrat", "negative", "upset", "escalat")
    )
    if any(term in escalation_reason.lower() for term in ("anger", "frustration", "negative sentiment")):
        negative_sentiment = True

    human_taken_over = bool(
        memory.get("human_taken_over")
        or memory.get("agent_taken_over")
        or memory.get("ai_disabled")
        or escalation_status in _HUMAN_OWNED_STATUSES
    )
    waiting_for_human = bool(escalation_active and not human_taken_over)

    explicit_conversation_status = _normalised(memory.get("conversation_status"))
    conversation_closed = bool(
        memory.get("conversation_closed")
        or memory.get("manually_closed")
        or explicit_conversation_status in _CLOSED_STATUSES
    )
    customer_replied = _customer_replied_after_follow_up(memory)

    intent = str(_first_value(_result_value(result, "intent", ""), memory.get("intent"), "unknown")).strip()
    intent_normalised = _normalised(intent)
    call_status = _normalised(
        _first_value(memory.get("call_status"), memory.get("callStatus"), memory.get("vapi_call_status"))
    )
    missed_call = call_status in _MISSED_CALL_STATUSES or intent_normalised in {"missed_call", "missed call"}
    last_customer_message = _last_customer_message(memory).lower()
    information_requested = bool(
        intent_normalised in {"information_request", "information_requested", "request_information"}
        or "information" in intent_normalised
        or any(
            phrase in last_customer_message
            for phrase in ("more information", "more info", "send details", "share details", "pictures", "photos")
        )
    )
    booking_started = bool(
        memory.get("booking_started")
        or _normalised(memory.get("current_workflow")) in {"booking", "awaiting_booking"}
        or booking_normalised not in {"", "not_started", "not started"}
    )

    if conversation_closed:
        conversation_status = "closed"
    elif human_taken_over:
        conversation_status = "human_owned"
    elif waiting_for_human:
        conversation_status = "waiting_for_human"
    elif missed_call:
        conversation_status = "missed_call"
    elif booking_normalised in _BOOKING_COMPLETE:
        conversation_status = "booking_completed"
    elif payment_normalised in _PAYMENT_COMPLETE:
        conversation_status = "payment_completed"
    elif booking_started:
        conversation_status = "booking_started"
    elif information_requested:
        conversation_status = "information_requested"
    elif explicit_conversation_status:
        conversation_status = explicit_conversation_status
    else:
        conversation_status = "active"

    stop_reason = ""
    if conversation_closed:
        stop_reason = "conversation_closed"
    elif booking_normalised in _BOOKING_STOP:
        stop_reason = f"booking_{booking_normalised.replace(' ', '_')}"
    elif payment_normalised in _PAYMENT_STOP:
        stop_reason = f"payment_{payment_normalised.replace(' ', '_')}"
    elif human_taken_over:
        stop_reason = "human_taken_over"
    elif waiting_for_human:
        stop_reason = "waiting_for_human"
    elif customer_replied:
        stop_reason = "customer_replied"
    elif negative_sentiment:
        stop_reason = "negative_sentiment"

    if stop_reason:
        follow_up_eligible = False
        follow_up_reason = stop_reason
    elif missed_call:
        follow_up_eligible = True
        follow_up_reason = "missed_call"
    elif booking_started:
        follow_up_eligible = True
        follow_up_reason = "booking_started_not_completed"
    elif information_requested:
        follow_up_eligible = True
        follow_up_reason = "information_requested"
    else:
        follow_up_eligible = True
        follow_up_reason = "general_inquiry_unresolved"

    priority = str(escalation.get("priority") or memory.get("priority") or "low").strip().lower()
    if negative_sentiment and escalation_active:
        priority = "high"

    state = {
        "conversation_status": conversation_status,
        "booking_status": booking_status,
        "payment_status": payment_status,
        "customer_intent": intent,
        "priority": priority,
        "escalation_reason": escalation_reason,
        "confidence_score": _confidence_score(memory, result),
        "follow_up_eligible": follow_up_eligible,
        "follow_up_reason": follow_up_reason,
        "human_taken_over": human_taken_over,
        "conversation_closed": conversation_closed,
        "waiting_for_human": waiting_for_human,
        "customer_replied": customer_replied,
        "negative_sentiment": negative_sentiment,
        "timestamp": timestamp or utc_now_iso(),
    }
    return state
