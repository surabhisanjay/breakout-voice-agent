from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ..memory.conversation_memory import ConversationMemory


@dataclass(frozen=True)
class SentimentResult:
    sentiment: str
    confidence: float
    escalation_recommended: bool
    reason: str
    stage: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SentimentAgent:
    """Fast, persistent sentiment tracking with an optional LLM fallback."""

    SIGNALS: tuple[tuple[str, tuple[str, ...], float], ...] = (
        ("angry", ("unacceptable", "ridiculous", "furious", "very angry", "terrible service", "useless"), 0.94),
        ("frustrated", ("frustrated", "you keep asking", "not listening", "i already told", "this is wrong", "again and again", "still not working"), 0.88),
        ("confused", ("confused", "don't understand", "do not understand", "what do you mean", "not clear", "unclear"), 0.82),
        ("urgent", ("urgent", "as soon as possible", "asap", "running late", "in a hurry", "right now", "last minute"), 0.84),
        ("hesitant", ("not sure", "maybe", "need to think", "check with", "not ready", "don't confirm", "do not confirm", "in a dilemma"), 0.80),
        ("excited", ("excited", "can't wait", "cannot wait", "sounds fun", "so sweet", "love that"), 0.78),
        ("satisfied", ("that's helpful", "that is helpful", "perfect", "sounds good", "great, thanks", "thank you so much"), 0.76),
    )

    def __init__(
        self,
        memory: ConversationMemory,
        llm_classifier: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.memory = memory
        self.llm_classifier = llm_classifier

    def analyze(self, message: str, stage: str = "") -> SentimentResult:
        clean = re.sub(r"\s+", " ", message.lower()).strip()
        active_stage = stage or str(self.memory.data.get("conversation_mode", "discovery"))
        sentiment, confidence, reason = self._rule_based(clean)

        if sentiment == "neutral" and self.llm_classifier is not None:
            try:
                assisted = self.llm_classifier(message, active_stage) or {}
                candidate = str(assisted.get("sentiment", "neutral"))
                candidate_confidence = float(assisted.get("confidence", 0.0))
                if candidate in {"frustrated", "confused", "excited", "urgent", "hesitant", "angry", "satisfied", "neutral"} and candidate_confidence >= 0.65:
                    sentiment = candidate
                    confidence = min(max(candidate_confidence, 0.0), 1.0)
                    reason = str(assisted.get("reason", "LLM-assisted classification"))
            except Exception:
                pass

        history = self.memory.data.setdefault("sentiment_history", [])
        recent_negative = sum(
            1 for item in history[-2:]
            if item.get("sentiment") in {"frustrated", "angry"}
        )
        escalation_recommended = sentiment == "angry" or (
            sentiment == "frustrated" and recent_negative >= 1
        )
        result = SentimentResult(
            sentiment=sentiment,
            confidence=round(confidence, 2),
            escalation_recommended=escalation_recommended,
            reason=reason,
            stage=active_stage,
        )

        history.append({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            **result.to_dict(),
        })
        self.memory.data["sentiment_history"] = history[-30:]
        self.memory.data["sentiment"] = sentiment
        self.memory.data["sentiment_confidence"] = result.confidence
        self.memory.data["sentiment_reason"] = reason
        self.memory.save()
        return result

    def _rule_based(self, message: str) -> tuple[str, float, str]:
        for sentiment, phrases, confidence in self.SIGNALS:
            matched = next((phrase for phrase in phrases if phrase in message), "")
            if matched:
                return sentiment, confidence, f'Customer used "{matched}".'
        if re.fullmatch(r"(?:what|sorry|pardon|come again|what was that)[?.! ]*", message):
            return "confused", 0.72, "Customer requested conversational repair."
        return "neutral", 0.55, "No strong sentiment signal detected."
