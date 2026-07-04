from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
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
    sentiment_profile: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        profile = payload.pop("sentiment_profile", {}) or {}
        payload.update(profile)
        return payload


class SentimentAgent:
    """Conversation-wide sentiment tracker with legacy one-turn compatibility."""

    SIGNALS: tuple[tuple[str, tuple[str, ...], float], ...] = (
        ("angry", ("unacceptable", "ridiculous", "furious", "very angry", "i am angry", "i'm angry", "angry", "terrible service", "useless"), 0.94),
        ("frustrated", ("frustrated", "frustrating", "going in circles", "keep suggesting", "same things", "you keep asking", "you keep repeating", "not listening", "not understanding", "i already told", "booking is wrong", "my booking is wrong", "want to speak to a human", "speak to a human", "this is wrong", "this is not helping", "this isn't helping", "not helping", "again and again", "still not working"), 0.88),
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
                allowed = {
                    "frustrated", "confused", "excited", "urgent", "hesitant",
                    "angry", "satisfied", "neutral", "curious", "ready_to_book",
                }
                if candidate in allowed and candidate_confidence >= 0.65:
                    sentiment = candidate
                    confidence = min(max(candidate_confidence, 0.0), 1.0)
                    reason = str(assisted.get("reason", "LLM-assisted classification"))
            except Exception:
                pass

        history = self.memory.data.setdefault("sentiment_history", [])
        pattern_reasons = self._conversation_pattern_reasons(clean)
        recent_negative = sum(
            1 for item in history[-2:]
            if item.get("sentiment") in {"frustrated", "angry", "upset", "urgent"}
        )
        escalation_recommended = sentiment in {"angry", "upset"} or (
            sentiment in {"frustrated", "urgent"} and recent_negative >= 1
        ) or len(pattern_reasons) >= 2

        profile = self._build_sentiment_profile(
            current_sentiment=sentiment,
            confidence=confidence,
            reason=reason,
            stage=active_stage,
            pattern_reasons=pattern_reasons,
        )
        if escalation_recommended or profile["frustration_score"] >= 0.7:
            profile["escalation_risk"] = "high"
            profile["customer_mood"] = "Ready To Escalate"
        elif profile["frustration_score"] >= 0.4:
            profile["escalation_risk"] = "medium"
        if self.memory.booking_ready() or re.search(r"\b(?:confirm booking|book it|ready to book|let'?s book)\b", clean):
            if profile["escalation_risk"] != "high":
                profile["customer_mood"] = "Ready To Book"

        result = SentimentResult(
            sentiment=sentiment,
            confidence=round(confidence, 2),
            escalation_recommended=escalation_recommended,
            reason=reason,
            stage=active_stage,
            sentiment_profile=profile,
        )

        history.append({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            **result.to_dict(),
        })
        self.memory.data["sentiment_history"] = history[-30:]
        self.memory.data["sentiment"] = sentiment
        self.memory.data["sentiment_confidence"] = result.confidence
        self.memory.data["sentiment_reason"] = reason
        self.memory.data["sentiment_analysis"] = profile
        self.memory.save()
        return result

    def _rule_based(self, message: str) -> tuple[str, float, str]:
        if re.search(r"\b(?:no\s+no\s+no|that's not what i asked|that is not what i asked|you are not understanding|not understanding|you don't understand|you are not listening|my booking is wrong|booking is wrong|i already told|not happy|unhappy|disappointed)\b", message):
            return "frustrated", 0.94, "Customer reported repeated misunderstanding or unhappiness."
        if re.search(r"\b(?:connect(?: me)? to (?:a )?(?:human|person|agent|representative|support|operator)|talk to (?:a )?(?:human|person|agent|representative|support|operator)|speak to (?:a )?(?:human|person|agent|representative|support|operator)|talk to someone|human instead|transfer me|put me through|escalate)\b", message):
            return "frustrated", 0.95, "Customer explicitly requested human agent transfer."
        if re.search(r"\b(?:don't book|do not book|cancel the booking|stop the booking|don't make the booking|do not make the booking)\b", message):
            return "frustrated", 0.92, "Customer rejected or requested booking cancellation."
        if re.search(r"\b(?:ready to book|book it|confirm booking|please confirm|let's book|lets book)\b", message):
            return "ready_to_book", 0.86, "Customer is ready to book."
        for sentiment, phrases, confidence in self.SIGNALS:
            matched = next((phrase for phrase in phrases if phrase in message), "")
            if matched:
                return sentiment, confidence, f'Customer used "{matched}".'
        if re.fullmatch(r"(?:what|sorry|pardon|come again|what was that)[?.! ]*", message):
            return "confused", 0.72, "Customer requested conversational repair."
        if "?" in message and any(term in message for term in ("what", "which", "how", "can", "do you")):
            return "curious", 0.64, "Customer asked a question."
        return "neutral", 0.55, "No strong sentiment signal detected."

    def _conversation_pattern_reasons(self, latest_message: str) -> list[str]:
        conversation = self.memory.data.get("conversation", [])
        customer_turns = [
            str(turn.get("content", "")).lower()
            for turn in conversation
            if turn.get("role") == "customer"
        ]
        combined_recent = " ".join(customer_turns[-6:] + [latest_message])
        correction_terms = ("actually", "instead", "change", "switch", "not that", "i said", "i already told")
        correction_count = sum(1 for turn in customer_turns[-8:] if any(term in turn for term in correction_terms))
        reasons: list[str] = []
        if correction_count >= 2:
            reasons.append("Repeated corrections")
        if combined_recent.count("?") >= 3 and re.search(
            r"\b(?:confused|unclear|not\s+clear|not\s+helping|not\s+understanding|again|same|already\s+told|going\s+in\s+circles)\b",
            combined_recent,
        ):
            reasons.append("Repeated questions")
        if "no no no" in combined_recent:
            reasons.append("Customer interruption or rejection")
        if "that's not what i asked" in combined_recent or "that is not what i asked" in combined_recent:
            reasons.append("Customer said the answer missed the question")
        if "you are not understanding" in combined_recent or "not understanding" in combined_recent or "you don't understand" in combined_recent:
            reasons.append("Customer reported misunderstanding")
        if "booking is wrong" in combined_recent:
            reasons.append("Customer reported incorrect booking")
        if "going in circles" in combined_recent or "keep suggesting" in combined_recent or "keep repeating" in combined_recent or "same things" in combined_recent:
            reasons.append("Repeated recommendation loop")
        if re.search(r"\b(?:forget it|leave it|stop|don't book|do not book|not booking anymore)\b", combined_recent):
            reasons.append("Booking abandonment risk")
        return list(dict.fromkeys(reasons))

    def _build_sentiment_profile(
        self,
        current_sentiment: str,
        confidence: float,
        reason: str,
        stage: str,
        pattern_reasons: list[str],
    ) -> dict[str, Any]:
        history = self.memory.data.get("sentiment_history", [])
        label = {
            "ready_to_book": "Ready To Book",
            "excited": "Excited",
            "satisfied": "Positive",
            "curious": "Curious",
            "neutral": "Neutral",
            "confused": "Confused",
            "urgent": "Impatient",
            "hesitant": "Confused",
            "frustrated": "Frustrated",
            "angry": "Angry",
            "upset": "Upset",
        }
        current_label = label.get(current_sentiment, current_sentiment.replace("_", " ").title())
        scored = [current_sentiment] + [str(item.get("sentiment", "neutral")) for item in history[-9:]]
        score_map = {
            "excited": 0.9, "satisfied": 0.78, "ready_to_book": 0.82, "curious": 0.62,
            "neutral": 0.5, "hesitant": 0.38, "confused": 0.32, "urgent": 0.28,
            "frustrated": 0.16, "upset": 0.12, "angry": 0.05,
        }
        sentiment_score = sum(score_map.get(item, 0.5) for item in scored) / max(len(scored), 1)
        frustration_score = min(
            1.0,
            sum(1 for item in scored if item in {"frustrated", "angry", "upset", "urgent"}) * 0.18
            + len(pattern_reasons) * 0.16,
        )
        labels = [label.get(str(item.get("sentiment", "neutral")), "Neutral") for item in history[-9:]]
        labels.append(current_label)
        negative = {"Confused", "Impatient", "Frustrated", "Angry", "Upset", "Ready To Escalate"}
        positive = {"Interested", "Curious", "Positive", "Excited", "Ready To Book"}
        negative_count = sum(1 for item in labels if item in negative)
        positive_count = sum(1 for item in labels if item in positive)
        if frustration_score >= 0.7:
            overall = "Ready To Escalate"
            health = "poor"
        elif negative_count > positive_count and negative_count >= 2:
            overall = "Frustrated"
            health = "watch"
        elif positive_count >= 2:
            overall = "Positive"
            health = "healthy"
        else:
            overall = current_label
            health = "healthy" if sentiment_score >= 0.45 else "watch"
        journey = [
            {
                "turn": idx + 1,
                "sentiment": item_label,
                "stage": str(item.get("stage", "")),
                "reason": str(item.get("reason", "")),
            }
            for idx, (item, item_label) in enumerate(zip(history[-9:], labels[:-1]))
        ]
        journey.append({
            "turn": len(journey) + 1,
            "sentiment": current_label,
            "stage": stage,
            "reason": reason,
        })
        return {
            "current_sentiment": current_label,
            "overall_sentiment": overall,
            "sentiment_score": round(sentiment_score, 2),
            "frustration_score": round(frustration_score, 2),
            "escalation_risk": "low",
            "customer_mood": current_label,
            "sentiment_journey": journey,
            "frustration_reasons": pattern_reasons,
            "conversation_health": health,
        }
