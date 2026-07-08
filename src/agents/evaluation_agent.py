from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


ROBOTIC_PHRASES = (
    "may i know your good name",
    "certainly",
    "absolutely",
    "no refund or reschedule policy",
)


@dataclass
class EvaluationResult:
    score: int
    category_scores: dict[str, int] = field(default_factory=dict)
    reasoning: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


class EvaluationAgent:
    """
    Scores completed conversations after the fact.

    It is deliberately not called during dispatch() and never makes live
    routing decisions.
    """

    def evaluate(self, conversation: dict[str, Any]) -> EvaluationResult:
        transcript = conversation.get("transcript") or conversation.get("turns") or []
        memory = conversation.get("memory") or {}
        responses = [str(turn.get("response") or turn.get("text") or "") for turn in transcript if isinstance(turn, dict)]
        joined = "\n".join(responses).lower()

        categories = {
            "memory": self._score_memory(memory),
            "routing": 90,
            "recommendation": self._score_recommendation(memory, joined),
            "booking": self._score_booking(memory),
            "payment": self._score_payment(memory),
            "escalation": self._score_escalation(memory, conversation),
            "hallucinations": self._score_hallucination(joined),
            "conversation_quality": self._score_quality(joined),
            "tool_usage": self._score_tool_usage(conversation),
        }
        flags: list[str] = []
        reasoning: list[str] = []
        for phrase in ROBOTIC_PHRASES:
            if phrase in joined:
                flags.append(f"robotic_or_forbidden_phrase:{phrase}")
        if self._has_repeated_questions(responses):
            flags.append("repeated_question")
        if not memory:
            flags.append("missing_memory_snapshot")
        for key, value in categories.items():
            if value < 80:
                reasoning.append(f"{key} scored {value}.")
        if not reasoning:
            reasoning.append("Conversation meets deterministic production quality checks.")
        score = max(0, min(100, round(sum(categories.values()) / len(categories)) - len(flags) * 4))
        return EvaluationResult(score=score, category_scores=categories, reasoning=reasoning, flags=flags)

    @staticmethod
    def _score_memory(memory: dict[str, Any]) -> int:
        if not memory:
            return 60
        filled = sum(1 for key in ("participants", "location", "preferred_date", "room", "selected_slot") if memory.get(key))
        return min(100, 78 + filled * 4)

    @staticmethod
    def _score_recommendation(memory: dict[str, Any], transcript: str) -> int:
        rejected = memory.get("rejected_options") or []
        recommended = memory.get("recommended_option")
        if recommended and recommended in rejected:
            return 45
        if "recommend" in transcript or recommended:
            return 92
        return 86

    @staticmethod
    def _score_booking(memory: dict[str, Any]) -> int:
        if memory.get("booking_id"):
            return 96 if memory.get("selected_slot") else 82
        if memory.get("booking_started") and not memory.get("booking_id"):
            return 84
        return 88

    @staticmethod
    def _score_payment(memory: dict[str, Any]) -> int:
        status = str(memory.get("paymentStatus") or memory.get("payment_status") or "").upper()
        if status in {"PAID", "UNPAID", "EXPIRED"}:
            return 94
        if memory.get("booking_id"):
            return 76
        return 88

    @staticmethod
    def _score_escalation(memory: dict[str, Any], conversation: dict[str, Any]) -> int:
        escalation = memory.get("escalation_state") or conversation.get("escalation") or {}
        if escalation.get("escalate") and not conversation.get("handoff_summary"):
            return 70
        return 92

    @staticmethod
    def _score_hallucination(transcript: str) -> int:
        if re.search(r"\bguaranteed\b|\balways available\b|free refund", transcript):
            return 50
        return 94

    @staticmethod
    def _score_quality(transcript: str) -> int:
        score = 94
        for phrase in ROBOTIC_PHRASES:
            if phrase in transcript:
                score -= 18
        return max(score, 30)

    @staticmethod
    def _score_tool_usage(conversation: dict[str, Any]) -> int:
        if conversation.get("backend_errors"):
            return 72
        return 90

    @staticmethod
    def _has_repeated_questions(responses: list[str]) -> bool:
        questions = [response.strip().lower() for response in responses if "?" in response]
        seen: set[str] = set()
        for question in questions:
            if question in seen:
                return True
            seen.add(question)
        return False
