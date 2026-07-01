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
        facts.append(f"Booking status: {booking_status}.")
        if escalation_state.get("reason"):
            facts.append(f"Escalation: {escalation_state['reason']}.")
        if outstanding:
            facts.append(f"Outstanding: {', '.join(outstanding)}.")

        return {
            "call_id": memory.get("call_id", ""),
            "customer_name": memory.get("customer_name", ""),
            "phone": memory.get("phone", ""),
            "intent": intent,
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
            "payment_status": memory.get("payment_status", "not_started"),
            "sentiment": memory.get("sentiment", "neutral"),
            "escalation_reason": escalation_state.get("reason", ""),
            "escalation_category": escalation_state.get("category", ""),
            "priority": escalation_state.get("priority", "normal"),
            "support_ticket_id": escalation_state.get("support_ticket_id", ""),
            "transfer_status": escalation_state.get("transfer_status", "not_required"),
            "recommended_next_action": escalation_state.get("recommended_action", ""),
            "transcript": list(memory.get("conversation", [])),
            "outstanding_questions": outstanding,
            "summary": " ".join(facts),
        }

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
