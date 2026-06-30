from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.agent_response import AgentResponse
    from ..memory.conversation_memory import ConversationMemory
    from .sentiment_agent import SentimentResult


@dataclass(frozen=True)
class ScoreResult:
    lead_score: int
    booking_readiness: int
    sentiment_score: int
    escalation_risk: int
    conversation_quality: int
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ScoringAgent:
    """Computes stable operational scores from conversation state."""

    BOOKING_FIELDS = ("location", "participants", "age_group", "preferred_date", "phone")
    INTENT_WEIGHTS = {
        "corporate_event": 24,
        "birthday_party": 20,
        "bachelor_party": 18,
        "farewell_party": 18,
        "couple_event": 16,
        "escape_room_inquiry": 14,
        "virtual_event": 12,
        "general_faq": 4,
        "cancellation_request": 0,
    }
    SENTIMENT_SCORES = {
        "excited": 88,
        "satisfied": 84,
        "neutral": 70,
        "hesitant": 58,
        "confused": 48,
        "urgent": 45,
        "frustrated": 32,
        "angry": 12,
    }

    def __init__(self, memory: ConversationMemory) -> None:
        self.memory = memory

    def score(
        self,
        message: str,
        result: AgentResponse,
        sentiment: SentimentResult,
    ) -> ScoreResult:
        state = self.memory.data
        intent = str(result.intent or state.get("intent", "general_faq"))
        reasons: list[str] = []

        readiness_fields = [field for field in self.BOOKING_FIELDS if state.get(field)]
        booking_readiness = round((len(readiness_fields) / len(self.BOOKING_FIELDS)) * 100)
        if booking_readiness >= 80:
            reasons.append("Most required booking fields are captured.")
        elif readiness_fields:
            reasons.append(f"Captured booking fields: {', '.join(readiness_fields)}.")
        else:
            reasons.append("Booking details are still early-stage.")

        sentiment_score = self.SENTIMENT_SCORES.get(sentiment.sentiment, 70)
        if sentiment.sentiment in {"frustrated", "angry", "confused"}:
            reasons.append(f"Customer sentiment is {sentiment.sentiment}.")
        elif sentiment.sentiment in {"excited", "satisfied"}:
            reasons.append(f"Customer sentiment is {sentiment.sentiment}.")

        lead_score = min(
            100,
            self.INTENT_WEIGHTS.get(intent, 4)
            + round(booking_readiness * 0.48)
            + round(sentiment_score * 0.18)
            + (12 if state.get("room") or state.get("recommended_option") else 0)
            + (10 if result.next_agent == "booking_agent" or state.get("booking_started") else 0),
        )

        escalation_risk = 0
        if sentiment.sentiment == "angry":
            escalation_risk += 65
        elif sentiment.sentiment == "frustrated":
            escalation_risk += 45
        elif sentiment.sentiment in {"confused", "urgent"}:
            escalation_risk += 25
        if result.escalation.get("escalate"):
            escalation_risk = max(escalation_risk, 85)
        escalation_risk = min(100, escalation_risk)

        missing_penalty = min(28, len(result.missing_fields or []) * 6)
        quality = max(
            0,
            min(
                100,
                82
                + (8 if result.response and len(result.response.split()) <= 55 else -5)
                - missing_penalty
                - round(escalation_risk * 0.35),
            ),
        )

        score = ScoreResult(
            lead_score=lead_score,
            booking_readiness=booking_readiness,
            sentiment_score=sentiment_score,
            escalation_risk=escalation_risk,
            conversation_quality=quality,
            reasons=reasons[:5],
        )
        history = self.memory.data.setdefault("score_history", [])
        history.append({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "message": message,
            "intent": intent,
            **score.to_dict(),
        })
        self.memory.data["score_history"] = history[-50:]
        self.memory.save()
        return score
