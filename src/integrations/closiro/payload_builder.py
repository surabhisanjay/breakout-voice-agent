from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import ClosiroWebhookPayload


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _summary_text(chat_payload: dict[str, Any]) -> str:
    conversation_summary = _dict(chat_payload.get("conversation_summary"))
    ai_summary = _dict(chat_payload.get("ai_summary"))
    return str(
        conversation_summary.get("summary")
        or ai_summary.get("summary")
        or chat_payload.get("response")
        or ""
    )


def _duration_seconds(chat_payload: dict[str, Any]) -> int:
    metrics = _dict(chat_payload.get("metrics"))
    turn_count = metrics.get("turn_count") or len(_list(chat_payload.get("conversation_history")))
    try:
        return max(int(turn_count) * 8, 1)
    except (TypeError, ValueError):
        return 1


def _base_payload(session_id: str, chat_payload: dict[str, Any]) -> dict[str, Any]:
    conversation_summary = _dict(chat_payload.get("conversation_summary"))
    booking = _dict(chat_payload.get("booking"))
    return {
        "session_id": session_id,
        "call_id": conversation_summary.get("conversation_id") or session_id,
        "booking": booking,
        "payment": _dict(chat_payload.get("payment")),
        "recommendation": _dict(chat_payload.get("recommendation")),
        "media": _list(chat_payload.get("media")),
        "price_breakdown": _dict(chat_payload.get("price_breakdown")),
        "conversation_summary": conversation_summary,
        "handoff_summary": chat_payload.get("handoff_summary"),
        "evaluation": _dict(chat_payload.get("evaluation")),
        "metrics": _dict(chat_payload.get("metrics")),
        "csat": _dict(chat_payload.get("csat")),
        "sentiment": _dict(chat_payload.get("sentiment")),
        "sentiment_graph": _list(chat_payload.get("sentiment_graph")),
        "learning": _dict(chat_payload.get("learning")),
        "follow_up": _dict(chat_payload.get("follow_up")),
        "conversation_history": _list(chat_payload.get("conversation_history")),
        "recording": _dict(chat_payload.get("recording")),
        "booking_reference": booking.get("reference") or booking.get("booking_id") or "",
        "payment_url": _dict(chat_payload.get("payment")).get("payment_url") or "",
    }


def _float_score(value: Any) -> float:
    try:
        return round(max(0.0, min(float(value), 1.0)), 4)
    except (TypeError, ValueError):
        return 0.0


def _recording_reference(chat_payload: dict[str, Any]) -> str:
    recording = chat_payload.get("recording")
    if isinstance(recording, str):
        return recording.strip()
    recording_data = _dict(recording)
    return str(
        recording_data.get("url")
        or recording_data.get("recording_url")
        or recording_data.get("reference")
        or recording_data.get("id")
        or chat_payload.get("call_recording_reference")
        or ""
    ).strip()


def build_escalation_context(session_id: str, chat_payload: dict[str, Any]) -> dict[str, Any]:
    escalation = _dict(chat_payload.get("escalation"))
    handoff = _dict(chat_payload.get("handoff_summary"))
    summary = _dict(chat_payload.get("conversation_summary"))
    business_state = _dict(chat_payload.get("business_state"))
    booking = _dict(chat_payload.get("booking"))
    payment = _dict(chat_payload.get("payment"))
    customer_details = _dict(chat_payload.get("customer_details"))
    customer_profile = _dict(chat_payload.get("customer_profile"))

    customer_details = {
        **customer_details,
        "name": customer_details.get("name") or handoff.get("customer_name") or summary.get("customer_name") or customer_profile.get("name") or "",
        "phone": customer_details.get("phone") or handoff.get("phone") or summary.get("phone") or customer_profile.get("phone") or "",
        "email": customer_details.get("email") or handoff.get("email") or customer_profile.get("email") or "",
        "whatsapp_number": customer_details.get("whatsapp_number") or handoff.get("whatsapp_number") or customer_profile.get("whatsapp_number") or "",
    }

    conversation_summary = summary or {
        "summary": handoff.get("conversation_summary") or handoff.get("summary") or _summary_text(chat_payload)
    }
    transcript = _list(chat_payload.get("conversation_history")) or _list(chat_payload.get("transcript"))
    escalation_reason = str(escalation.get("reason") or business_state.get("escalation_reason") or "Escalation requested")
    priority = str(escalation.get("priority") or business_state.get("priority") or "medium")
    customer_intent = str(
        chat_payload.get("customer_intent")
        or business_state.get("customer_intent")
        or summary.get("intent")
        or handoff.get("intent")
        or "unknown"
    )
    confidence_score = _float_score(
        chat_payload.get("confidence_score")
        if chat_payload.get("confidence_score") not in (None, "")
        else business_state.get("confidence_score")
    )
    booking_status = str(
        business_state.get("booking_status")
        or booking.get("status")
        or payment.get("booking_status")
        or handoff.get("booking_status")
        or "not_started"
    )
    payment_status = str(
        business_state.get("payment_status")
        or payment.get("status")
        or summary.get("payment_status")
        or "not_started"
    )
    preferred_contact_method = str(
        chat_payload.get("preferred_contact_method")
        or customer_details.get("preferred_contact_method")
        or chat_payload.get("channel")
        or ("whatsapp" if session_id.startswith("whatsapp:") else "web_chat")
    )
    timestamp = str(chat_payload.get("timestamp") or business_state.get("timestamp") or _utc_now())

    return {
        "customer_details": customer_details,
        "conversation_summary": conversation_summary,
        "transcript": transcript,
        "call_recording_reference": _recording_reference(chat_payload),
        "escalation_reason": escalation_reason,
        "priority": priority,
        "customer_intent": customer_intent,
        "confidence_score": confidence_score,
        "booking_status": booking_status,
        "payment_status": payment_status,
        "preferred_contact_method": preferred_contact_method,
        "timestamp": timestamp,
    }


def build_call_ended_payload(session_id: str, chat_payload: dict[str, Any]) -> ClosiroWebhookPayload:
    body = _base_payload(session_id, chat_payload)
    body.update(
        {
            "message": {
                "type": "end-of-call-report",
                "summary": _summary_text(chat_payload),
                "call": {"id": body["call_id"]},
            },
            "ended_at": _utc_now(),
            "duration_seconds": _duration_seconds(chat_payload),
            "outcome": _dict(chat_payload.get("conversation_summary")).get("outcome") or "",
        }
    )
    return ClosiroWebhookPayload(
        event_type="call-ended",
        endpoint="/api/v1/webhooks/vapi/call-ended",
        body=body,
        sync_key=f"call-ended:{session_id}:{body.get('booking_reference') or body.get('outcome')}",
    )


def build_escalation_payload(session_id: str, chat_payload: dict[str, Any]) -> ClosiroWebhookPayload:
    escalation = _dict(chat_payload.get("escalation"))
    body = _base_payload(session_id, chat_payload)
    context = build_escalation_context(session_id, chat_payload)
    reason = context["escalation_reason"]
    priority = context["priority"]
    body.update(
        {
            "message": {
                "type": "tool-calls",
                "toolCalls": [
                    {
                        "function": {
                            "name": "escalate",
                            "arguments": {
                                "reason": reason,
                                "priority": priority,
                            },
                        }
                    }
                ],
                "call": {"id": body["call_id"]},
            },
            "reason": reason,
            "priority": priority,
            "triggered_at": context["timestamp"],
            **context,
        }
    )
    return ClosiroWebhookPayload(
        event_type="escalation",
        endpoint="/api/v1/webhooks/vapi/escalation",
        body=body,
        sync_key=f"escalation:{session_id}",
    )
