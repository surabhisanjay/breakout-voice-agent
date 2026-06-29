from __future__ import annotations

from typing import Any

from ..core.handoff_generator import HandoffGenerator


class HandoffSummaryAgent:
    """Build a concise, deterministic summary for a human takeover."""

    def generate(
        self,
        memory: dict[str, Any],
        escalation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        escalation_state = escalation or memory.get("escalation_state", {}) or {}
        intent = str(memory.get("intent", ""))
        participants = memory.get("participants") or memory.get("company_size", "")
        room = memory.get("room") or memory.get("recommended_option", "")
        outstanding = self._outstanding_questions(memory, intent)
        booking_status = self._booking_status(memory)
        sentiment = dict(memory.get("sentiment_analysis") or {
            "current_sentiment": str(memory.get("sentiment", "neutral")).replace("_", " ").title(),
            "overall_sentiment": str(memory.get("sentiment", "neutral")).replace("_", " ").title(),
            "sentiment_score": float(memory.get("sentiment_confidence") or 0.0),
            "frustration_score": 0.0,
            "escalation_risk": "low",
            "customer_mood": str(memory.get("sentiment", "neutral")).replace("_", " ").title(),
            "sentiment_journey": [],
            "frustration_reasons": [],
            "conversation_health": "healthy",
        })
        sentiment.setdefault("sentiment", memory.get("sentiment", "neutral"))

        facts: list[str] = []
        if memory.get("customer_name"):
            facts.append(f"Customer: {memory['customer_name']}.")
        if memory.get("event_type"):
            facts.append(f"Interested in {memory['event_type']}.")
        if participants:
            facts.append(f"Group size: {participants}.")
        if memory.get("age_group"):
            facts.append(f"Age group: {memory['age_group']}.")
        if memory.get("experience_level"):
            facts.append(f"Experience: {memory['experience_level']}.")
        if memory.get("location"):
            facts.append(f"Location: {memory['location']}.")
        if memory.get("preferred_date"):
            facts.append(f"Date: {memory['preferred_date']}.")
        if room:
            facts.append(f"Room preference: {room}.")
        support_context = dict(memory.get("support_context") or {})
        if support_context.get("existing_booking"):
            timing = f" from {support_context['booked_when']}" if support_context.get("booked_when") else ""
            facts.append(f"Customer reports an existing booking{timing}.")
        if support_context.get("payment_reported") == "paid":
            facts.append("Customer reports payment completed.")
        if support_context.get("confirmation_received") is False:
            facts.append("Booking confirmation was not received.")
        facts.append(f"Booking status: {booking_status}.")
        if escalation_state.get("reason"):
            facts.append(f"Escalation: {escalation_state['reason']}.")
        if outstanding:
            facts.append(f"Outstanding: {', '.join(outstanding)}.")

        action_items = self._action_items(memory, escalation_state, outstanding)
        follow_ups = self._follow_up_recommendations(memory, escalation_state)
        customer_concerns = list(dict.fromkeys(list(memory.get("concerns") or []) + list(sentiment.get("frustration_reasons") or [])))
        escalation_payload = {
            "required": bool(escalation_state.get("escalate") or escalation_state.get("required")),
            "priority": escalation_state.get("priority") or ("high" if escalation_state.get("escalate") else "low"),
            "reason": escalation_state.get("reason", ""),
        }
        payload = {
            "customer_name": memory.get("customer_name", ""),
            "phone_available": bool(memory.get("phone")),
            "phone": memory.get("phone", ""),
            "intent": intent,
            "conversation_summary": " ".join(facts),
            "booking_details": {
                "location": memory.get("location", ""),
                "room": room,
                "date": memory.get("preferred_date", ""),
                "slot": memory.get("selected_slot", ""),
                "participants": int(participants or 0),
            },
            "sentiment": sentiment,
            "key_takeaways": self._key_takeaways(memory),
            "action_items": action_items,
            "follow_up_recommendations": follow_ups,
            "customer_concerns": customer_concerns,
            "support_context": support_context,
            "escalation": escalation_payload,
            # Backward-compatible flat fields below.
            "location": memory.get("location", ""),
            "preferred_date": memory.get("preferred_date", ""),
            "selected_slot": memory.get("selected_slot", ""),
            "participants": participants,
            "age_group": memory.get("age_group", ""),
            "experience_level": memory.get("experience_level", ""),
            "room_preference": room,
            "booking_id": memory.get("booking_id", ""),
            "booking_ref": memory.get("booking_ref", ""),
            "booking_status": booking_status,
            "sentiment_label": memory.get("sentiment", "neutral"),
            "escalation_reason": escalation_state.get("reason", ""),
            "outstanding_questions": outstanding,
            "summary": " ".join(facts),
        }
        return payload

    @staticmethod
    def _booking_status(memory: dict[str, Any]) -> str:
        if memory.get("completed_booking"):
            return "completed"
        workflow = str(memory.get("current_workflow", "general"))
        if workflow == "booking":
            return "in progress"
        if memory.get("booking_consent_pending") or workflow == "awaiting_booking":
            return "awaiting customer confirmation"
        return "not started"

    @staticmethod
    def _outstanding_questions(memory: dict[str, Any], intent: str) -> list[str]:
        required = list(HandoffGenerator.REQUIRED_BY_INTENT.get(intent, []))
        missing = [field for field in required if not memory.get(field)]
        if memory.get("pending_confirmation"):
            field = memory["pending_confirmation"].get("field", "detail")
            missing.append(f"confirm {field}")
        return list(dict.fromkeys(missing))

    @staticmethod
    def _key_takeaways(memory: dict[str, Any]) -> list[str]:
        takeaways: list[str] = []
        if memory.get("experience_level") == "beginner":
            takeaways.append("First-time player")
        if memory.get("participants") or memory.get("company_size"):
            takeaways.append(f"Group size: {memory.get('participants') or memory.get('company_size')}")
        if memory.get("location"):
            takeaways.append(f"Selected location: {memory['location']}")
        if memory.get("room") or memory.get("recommended_option"):
            takeaways.append(f"Room preference: {memory.get('room') or memory.get('recommended_option')}")
        if memory.get("selected_slot"):
            takeaways.append(f"Selected slot: {memory['selected_slot']}")
        return takeaways

    @staticmethod
    def _action_items(
        memory: dict[str, Any],
        escalation: dict[str, Any],
        outstanding: list[str],
    ) -> list[str]:
        if escalation.get("escalate") or escalation.get("required"):
            return ["Review escalation", "Contact customer", "Resolve customer issue"]
        if outstanding:
            return [f"Collect {field}" for field in outstanding]
        if memory.get("selected_slot") and not memory.get("booking_id"):
            return ["Confirm booking"]
        if memory.get("booking_id") and memory.get("booking_ref"):
            return ["Send booking confirmation"]
        return ["Follow up with customer"]

    @staticmethod
    def _follow_up_recommendations(memory: dict[str, Any], escalation: dict[str, Any]) -> list[str]:
        if escalation.get("escalate") or escalation.get("required"):
            return ["Human agent should respond using this handoff summary"]
        if memory.get("booking_id") and memory.get("booking_ref"):
            return ["Send booking confirmation", "Offer arrival guidance"]
        if memory.get("current_workflow") == "booking":
            return ["Continue booking from saved state"]
        if memory.get("event_type") in {"Corporate Event", "Birthday Party"}:
            return ["Offer food package information"]
        return ["Follow up if the customer does not complete booking"]
