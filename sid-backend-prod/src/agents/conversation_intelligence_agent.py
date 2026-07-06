from __future__ import annotations

import re
from typing import Any


class ConversationIntelligenceAgent:
    """Build frontend-ready manager intelligence from the full conversation state."""

    def analyze(
        self,
        memory: dict[str, Any],
        escalation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        escalation_state = escalation or memory.get("escalation_state", {}) or {}
        sentiment = dict(memory.get("sentiment_analysis") or self._fallback_sentiment(memory))
        sentiment.setdefault("sentiment", memory.get("sentiment", "neutral"))
        sentiment.setdefault("confidence", memory.get("sentiment_confidence", 0.0))
        transcript = self._transcript(memory)
        timeline_events = self._timeline_events(memory, transcript)
        booking_milestones = self._booking_milestones(memory)
        key_takeaways = self._key_takeaways(memory)
        objections = self._customer_objections(memory, transcript)
        risks = self._conversation_risks(memory, sentiment, escalation_state, objections)
        action_items = self._action_items(memory, escalation_state)
        follow_ups = self._follow_up_recommendations(memory, escalation_state, risks)
        ai_summary = self._ai_summary(memory, sentiment, key_takeaways, action_items, follow_ups)
        customer_profile = self._customer_profile(memory, sentiment)
        recording = self._recording(memory)

        return {
            "timeline_events": timeline_events,
            "key_takeaways": key_takeaways,
            "customer_objections": objections,
            "booking_milestones": booking_milestones,
            "conversation_risks": risks,
            "ai_summary": ai_summary,
            "customer_profile": customer_profile,
            "transcript": transcript,
            "recording": recording,
            "sentiment_analysis": sentiment,
            "escalation": self._escalation_view(escalation_state, sentiment),
            "follow_up_recommendations": follow_ups,
        }

    def _fallback_sentiment(self, memory: dict[str, Any]) -> dict[str, Any]:
        sentiment = str(memory.get("sentiment") or "neutral")
        label = sentiment.replace("_", " ").title() if sentiment else "Neutral"
        return {
            "current_sentiment": label,
            "overall_sentiment": label,
            "sentiment_score": float(memory.get("sentiment_confidence") or 0.5),
            "frustration_score": 0.0,
            "escalation_risk": "low",
            "customer_mood": label,
            "sentiment_journey": [],
            "frustration_reasons": [],
            "conversation_health": "healthy",
        }

    def _transcript(self, memory: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for idx, turn in enumerate(memory.get("conversation", []) or []):
            role = str(turn.get("role", ""))
            content = str(turn.get("content", ""))
            rows.append({
                "index": idx + 1,
                "time": self._time_for_turn(idx),
                "speaker": "Customer" if role == "customer" else "Assistant",
                "role": role,
                "text": content,
            })
        return rows

    def _timeline_events(self, memory: dict[str, Any], transcript: list[dict[str, Any]]) -> list[dict[str, str]]:
        events: list[dict[str, str]] = []
        support = dict(memory.get("support_context") or {})
        if support.get("existing_booking"):
            events.append({"time": self._first_time(transcript, r"booked|existing booking"), "event": "Existing Booking Reported"})
        if support.get("confirmation_received") is False:
            events.append({"time": self._first_time(transcript, r"confirmation"), "event": "Confirmation Not Received"})
        if support.get("payment_reported") == "paid":
            events.append({"time": self._first_time(transcript, r"paid|payment"), "event": "Payment Reported Completed"})
        if memory.get("experience_level") == "beginner":
            events.append(self._event(transcript, "first time", "First Time Player"))
        if memory.get("participants"):
            events.append({"time": self._first_time(transcript, r"\b\d+\b|people|adults|kids"), "event": f"Group Size Captured: {memory['participants']}"})
        if memory.get("age_group"):
            events.append({"time": self._first_time(transcript, str(memory["age_group"])), "event": f"Age Group Captured: {memory['age_group']}"})
        if memory.get("location"):
            events.append({"time": self._first_time(transcript, str(memory["location"])), "event": f"Location Selected: {memory['location']}"})
        room = memory.get("room") or memory.get("recommended_option")
        if room:
            label = "Room Selected" if memory.get("room") else "Room Recommended"
            events.append({"time": self._first_time(transcript, str(room).split(" or ")[0]), "event": f"{label}: {room}"})
        if memory.get("preferred_date"):
            events.append({"time": self._first_time(transcript, str(memory["preferred_date"])), "event": f"Date Selected: {memory['preferred_date']}"})
        if memory.get("selected_slot"):
            events.append({"time": self._first_time(transcript, str(memory["selected_slot"])), "event": f"Slot Selected: {memory['selected_slot']}"})
        if memory.get("booking_id") or memory.get("booking_ref"):
            events.append({"time": self._time_for_turn(max(len(transcript) - 1, 0)), "event": "Booking Confirmed"})
        escalation = memory.get("escalation_state", {}) or {}
        if escalation.get("escalate"):
            events.append({"time": self._time_for_turn(max(len(transcript) - 1, 0)), "event": "Escalation Triggered"})
        return [event for event in events if event.get("event")]

    def _booking_milestones(self, memory: dict[str, Any]) -> list[str]:
        milestones: list[str] = []
        fields = [
            ("participants", "Participant count captured"),
            ("age_group", "Age group captured"),
            ("location", "Location selected"),
            ("room", "Room selected"),
            ("preferred_date", "Date selected"),
            ("selected_slot", "Slot selected"),
            ("customer_name", "Customer name captured"),
            ("phone", "Phone captured"),
            ("booking_id", "Booking ID created"),
            ("booking_ref", "Booking reference created"),
        ]
        for field, label in fields:
            if memory.get(field):
                milestones.append(label)
        return milestones

    def _key_takeaways(self, memory: dict[str, Any]) -> list[str]:
        takeaways: list[str] = []
        support = dict(memory.get("support_context") or {})
        if support.get("existing_booking"):
            takeaways.append("Existing booking requires support")
        if support.get("payment_reported") == "paid":
            takeaways.append("Customer reports payment completed")
        if support.get("confirmation_received") is False:
            takeaways.append("Confirmation not received")
        if memory.get("experience_level") == "beginner":
            takeaways.append("First-time player")
        if memory.get("participants"):
            takeaways.append(f"Group size: {memory['participants']}")
        if memory.get("age_group"):
            takeaways.append(f"Age group: {memory['age_group']}")
        if memory.get("location"):
            takeaways.append(f"Selected location: {memory['location']}")
        if memory.get("room"):
            takeaways.append(f"Selected room: {memory['room']}")
        elif memory.get("recommended_option"):
            takeaways.append(f"Recommended option: {memory['recommended_option']}")
        if memory.get("selected_slot"):
            takeaways.append(f"Selected slot: {memory['selected_slot']}")
        if memory.get("booking_id") and memory.get("booking_ref"):
            takeaways.append("Booking confirmed with ID and reference")
        return takeaways

    def _customer_objections(self, memory: dict[str, Any], transcript: list[dict[str, Any]]) -> list[str]:
        objections = list(memory.get("concerns") or [])
        for row in transcript:
            if row.get("role") != "customer":
                continue
            text = str(row.get("text", "")).lower()
            if "price" in text or "cost" in text or "discount" in text:
                objections.append("Pricing or discount question")
            if "too many" in text or "capacity" in text or "split" in text:
                objections.append("Capacity concern")
            if "not what i asked" in text or "not understanding" in text:
                objections.append("Response quality concern")
            if "refund" in text:
                objections.append("Refund request")
        return list(dict.fromkeys(objections))

    def _conversation_risks(
        self,
        memory: dict[str, Any],
        sentiment: dict[str, Any],
        escalation: dict[str, Any],
        objections: list[str],
    ) -> list[str]:
        risks: list[str] = []
        if escalation.get("escalate") or escalation.get("required"):
            risks.append(str(escalation.get("reason") or "Escalation required"))
        if sentiment.get("escalation_risk") in {"medium", "high"}:
            risks.append(f"Escalation risk is {sentiment['escalation_risk']}")
        if sentiment.get("frustration_reasons"):
            risks.extend(str(item) for item in sentiment["frustration_reasons"])
        if objections:
            risks.extend(objections)
        if memory.get("booking_started") and not (memory.get("booking_id") and memory.get("booking_ref")):
            risks.append("Booking in progress without final confirmation")
        return list(dict.fromkeys(risks))

    def _action_items(self, memory: dict[str, Any], escalation: dict[str, Any]) -> list[str]:
        if escalation.get("escalate") or escalation.get("required"):
            return ["Review escalation", "Contact customer", "Resolve open issue"]
        actions: list[str] = []
        if not memory.get("customer_name"):
            actions.append("Collect customer name")
        if not memory.get("phone"):
            actions.append("Collect phone number")
        if memory.get("room") and memory.get("location") and memory.get("preferred_date") and not memory.get("selected_slot"):
            actions.append("Confirm available slot")
        if memory.get("selected_slot") and not memory.get("booking_id"):
            actions.append("Confirm booking")
        if memory.get("booking_id") and memory.get("booking_ref"):
            actions.append("Send booking confirmation")
        return actions

    def _follow_up_recommendations(
        self,
        memory: dict[str, Any],
        escalation: dict[str, Any],
        risks: list[str],
    ) -> list[str]:
        if escalation.get("escalate") or escalation.get("required"):
            return ["Human agent should call or message the customer", "Use the handoff summary before responding"]
        recs: list[str] = []
        if memory.get("booking_id") and memory.get("booking_ref"):
            recs.append("Send booking confirmation")
            recs.append("Offer arrival guidance")
        elif memory.get("booking_started"):
            recs.append("Continue booking from saved state")
        if memory.get("participants") and int(memory.get("participants") or 0) >= 8:
            recs.append("Offer group coordination support")
        if memory.get("event_type") in {"Corporate Event", "Birthday Party"}:
            recs.append("Offer food package information")
        if risks:
            recs.append("Manager should review conversation risks")
        return list(dict.fromkeys(recs or ["Follow up if customer does not complete booking"]))

    def _ai_summary(
        self,
        memory: dict[str, Any],
        sentiment: dict[str, Any],
        key_takeaways: list[str],
        action_items: list[str],
        follow_ups: list[str],
    ) -> dict[str, Any]:
        summary = self._summary_sentence(memory)
        return {
            "summary": summary,
            "intent": memory.get("intent", ""),
            "sentiment": sentiment.get("overall_sentiment") or sentiment.get("current_sentiment", ""),
            "action_items": action_items,
            "key_takeaways": key_takeaways,
            "follow_up_recommendations": follow_ups,
        }

    def _customer_profile(self, memory: dict[str, Any], sentiment: dict[str, Any]) -> dict[str, Any]:
        return {
            "customer_name": memory.get("customer_name", ""),
            "phone": memory.get("phone", ""),
            "location": memory.get("location", ""),
            "intent": memory.get("intent", ""),
            "lead_score": self._lead_score(memory, sentiment),
            "sentiment": sentiment.get("overall_sentiment") or sentiment.get("current_sentiment", ""),
            "booking_status": self._booking_status(memory),
        }

    def _recording(self, memory: dict[str, Any]) -> dict[str, Any]:
        url = str(memory.get("recording_url") or "")
        recording_id = str(memory.get("recording_id") or "")
        return {
            "available": bool(url or recording_id),
            "recording_url": url,
            "recording_id": recording_id,
        }

    def _escalation_view(self, escalation: dict[str, Any], sentiment: dict[str, Any]) -> dict[str, Any]:
        required = bool(escalation.get("escalate") or escalation.get("required"))
        priority = str(escalation.get("priority") or "")
        if not priority:
            priority = "high" if required and sentiment.get("escalation_risk") == "high" else "medium" if required else "low"
        return {
            "required": required,
            "priority": priority,
            "status": escalation.get("status") or ("unresolved" if required else "resolved"),
            "trigger": escalation.get("trigger") or escalation.get("reason", ""),
            "reason": escalation.get("reason", ""),
            "recommended_human_action": escalation.get("recommended_human_action") or (
                "Contact customer immediately" if required else "No human action required"
            ),
        }

    def _summary_sentence(self, memory: dict[str, Any]) -> str:
        support = dict(memory.get("support_context") or {})
        if support.get("existing_booking"):
            details: list[str] = ["an existing booking"]
            if support.get("booked_when"):
                details.append(f"made {support['booked_when']}")
            if support.get("payment_reported") == "paid":
                details.append("reported as paid")
            if support.get("confirmation_received") is False:
                details.append("with no confirmation received")
            return f"Customer needs support for {', '.join(details)}."
        room = memory.get("room") or memory.get("recommended_option") or "an escape room"
        location = memory.get("location") or "a Breakout location"
        participants = memory.get("participants") or memory.get("company_size") or "the group"
        date = memory.get("preferred_date") or "an undecided date"
        slot = memory.get("selected_slot")
        if memory.get("booking_id") and memory.get("booking_ref"):
            return f"Customer completed booking for {room} at {location} for {participants} participants on {date}{' at ' + slot if slot else ''}."
        if memory.get("booking_started") or memory.get("current_workflow") == "booking":
            return f"Customer is booking {room} at {location} for {participants} participants on {date}{' at ' + slot if slot else ''}."
        return f"Customer is interested in {room} at {location} for {participants} participants."

    @staticmethod
    def _booking_status(memory: dict[str, Any]) -> str:
        if (memory.get("support_context") or {}).get("existing_booking"):
            return "support_required"
        if memory.get("booking_id") and memory.get("booking_ref"):
            return "confirmed"
        if memory.get("booking_started") or memory.get("current_workflow") == "booking":
            return "in_progress"
        if memory.get("booking_consent_pending") or memory.get("current_workflow") == "awaiting_booking":
            return "awaiting_confirmation"
        return "not_started"

    @staticmethod
    def _lead_score(memory: dict[str, Any], sentiment: dict[str, Any]) -> str:
        score = 0
        for field in ("participants", "location", "preferred_date", "room", "selected_slot", "phone"):
            if memory.get(field):
                score += 1
        if sentiment.get("customer_mood") == "Ready To Book":
            score += 2
        if sentiment.get("escalation_risk") == "high":
            score -= 2
        if score >= 6:
            return "hot"
        if score >= 3:
            return "warm"
        return "cold"

    @staticmethod
    def _time_for_turn(index: int) -> str:
        seconds = max(index, 0) * 15
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _first_time(self, transcript: list[dict[str, Any]], pattern: str) -> str:
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            rx = re.compile(re.escape(pattern), re.IGNORECASE)
        for row in transcript:
            if rx.search(str(row.get("text", ""))):
                return str(row.get("time", "00:00"))
        return "00:00"

    def _event(self, transcript: list[dict[str, Any]], pattern: str, event: str) -> dict[str, str]:
        return {"time": self._first_time(transcript, pattern), "event": event}
