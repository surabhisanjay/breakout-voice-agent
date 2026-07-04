from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class KnownFact:
    value: Any
    confidence: float
    source: str


@dataclass(frozen=True)
class ConversationResolution:
    goal: str
    known_facts: dict[str, KnownFact]
    next_required_fact: str | None


class ConversationResolver:
    """Derive the current customer goal and next missing fact from memory."""

    CONTACT_FIELDS = ("customer_name", "phone")
    BOOKING_FACTS = (
        "location",
        "room",
        "preferred_date",
        "preferred_time",
        "preferred_period",
        "time_preference",
        "selected_slot",
        "participants",
        "age_group",
        "experience_level",
        "occasion",
        "event_type",
    )

    def resolve(self, memory: dict[str, Any], message: str = "") -> ConversationResolution:
        lowered = message.lower()
        goal = self._goal(memory, lowered)
        known_facts = self._known_facts(memory)
        next_required = self._next_required_fact(goal, memory)
        return ConversationResolution(goal, known_facts, next_required)

    def _known_facts(self, memory: dict[str, Any]) -> dict[str, KnownFact]:
        facts: dict[str, KnownFact] = {}
        fact_meta = memory.get("fact_meta") if isinstance(memory.get("fact_meta"), dict) else {}
        for field in (*self.BOOKING_FACTS, *self.CONTACT_FIELDS):
            value = memory.get(field)
            if value in ("", None, [], {}):
                continue
            meta = fact_meta.get(field) if isinstance(fact_meta.get(field), dict) else {}
            facts[field] = KnownFact(
                value=value,
                confidence=float(meta.get("confidence", 1.0)),
                source=str(meta.get("source", "explicit")),
            )
        return facts

    @staticmethod
    def _goal(memory: dict[str, Any], lowered_message: str) -> str:
        escalation = memory.get("escalation_state")
        if isinstance(escalation, dict) and escalation.get("escalate"):
            return "escalate"
        if any(term in lowered_message for term in ("human", "real person", "manager", "supervisor")):
            return "escalate"
        if any(term in lowered_message for term in ("recommend", "suggest", "which room", "which game", "best room")):
            return "recommend_room"
        if any(term in lowered_message for term in ("available", "availability", "slot", "slots", "book", "reserve")):
            return "check_or_book_slot"
        if memory.get("booking_started") or memory.get("current_workflow") == "booking":
            return "check_or_book_slot"
        if memory.get("room") or memory.get("recommended_option"):
            return "check_or_book_slot"
        if memory.get("intent") and memory.get("intent") != "general_faq":
            return "qualify"
        return "answer_question"

    def _next_required_fact(self, goal: str, memory: dict[str, Any]) -> str | None:
        if goal == "escalate":
            return None

        if goal == "recommend_room":
            if memory.get("room"):
                return None
            if not memory.get("participants"):
                return "participants"
            if not (
                memory.get("experience_level")
                or memory.get("age_group")
                or memory.get("challenge_preference")
            ):
                return "experience_level"
            return None

        if goal == "check_or_book_slot":
            ordered = ["participants", "location", "room", "preferred_date", "time_preference"]
            for field in ordered:
                if field == "time_preference":
                    if not (
                        memory.get("selected_slot")
                        or memory.get("preferred_time")
                        or memory.get("preferred_period")
                        or memory.get("time_preference")
                    ):
                        return field
                elif not memory.get(field):
                    return field
            if not memory.get("selected_slot"):
                return "selected_slot"
            for field in self.CONTACT_FIELDS:
                if not memory.get(field):
                    return field
            return None

        if goal == "qualify":
            intent = str(memory.get("intent") or "")
            if intent == "escape_room_inquiry":
                for field in ("participants", "location"):
                    if not memory.get(field):
                        return field
            return None

        return None
