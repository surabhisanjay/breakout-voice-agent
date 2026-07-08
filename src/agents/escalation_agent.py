from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, TYPE_CHECKING

from .handoff_summary_agent import HandoffSummaryAgent
from .sentiment_agent import SentimentResult

if TYPE_CHECKING:
    from ..core.agent_response import AgentResponse
    from ..memory.conversation_memory import ConversationMemory


@dataclass(frozen=True)
class EscalationResult:
    escalate: bool
    reason: str
    summary: str
    priority: str = "low"
    status: str = "resolved"
    trigger: str = ""
    recommended_human_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EscalationAgent:
    """Detect takeover conditions without changing business routing."""

    HUMAN_REQUEST = re.compile(
        r"\b(?:speak|talk|connect|transfer)\s+(?:me\s+)?(?:to|with)\s+(?:a\s+|an\s+|the\s+|your\s+)?(?:human|person|manager|supervisor|agent|team|support|someone|anyone)\b"
        r"|\btransfer\s+me\b"
        r"|\breal\s+person\b"
        r"|\bi\s+(?:want|need)\s+(?:a\s+|an\s+)?(?:human|person|manager|supervisor|agent)\b"
        r"|\bi\s+(?:do\s+not|don't|dont)\s+want\s+(?:an?\s+)?(?:ai|bot|chatbot|automation)\b"
        r"|\bi\s+(?:do\s+not|don't|dont)\s+want\s+to\s+(?:talk|speak|chat)\s+(?:to|with)\s+(?:an?\s+)?(?:ai|bot|chatbot|automation)\b"
        r"|\bget\s+me\s+(?:a\s+)?(?:human|person)\b"
        r"|\bconnect\s+me\s+(?:to|with)\s+(?:someone|anyone|a\s+(?:human|person|manager|supervisor|agent))\b"
        r"|\bplease\s+connect\s+me\s+(?:with|to)\b"
        r"|\b(?:speak|talk)\s+to\s+(?:your|a|the)\s+manager\b",
        re.IGNORECASE,
    )
    SAFETY_REQUEST = re.compile(
        r"\b(?:injured|injury|hurt|bleeding|cannot\s+breathe|can't\s+breathe|unresponsive|"
        r"trouble\s+breathing|difficulty\s+breathing|hard\s+to\s+breathe|breathing\s+trouble|"
        r"not\s+responding|fire|smoke|burning|medical\s+emergency|safety\s+emergency|"
        r"fainted|faint|collapsed|collapse|passed\s+out|unconscious|seizure|"
        r"heart\s+attack|not\s+breathing|someone\s+fell)\b",
        re.IGNORECASE,
    )
    REFUND_REQUEST = re.compile(
        r"\b(?:i\s+)?(?:want|need|request|would\s+like|deserve|demand|expecting|asking\s+for)\s+(?:a\s+)?refund\b"
        r"|\brefund\s+me\b"
        r"|\b(?:want|need|give\s+me|get)\s+(?:my\s+)?money\s+back\b"
        r"|\bgive\s+me\s+(?:a\s+)?refund\b",
        re.IGNORECASE,
    )
    LANGUAGE_REQUEST = re.compile(
        r"\b(?:speak|talk|chat|understand|conversing|support|configured)\s+(?:in\s+)?(?:hindi|kannada|tamil|telugu|bengali|marathi|spanish|french|german)\b"
        r"|\b(?:hindi|kannad|kannada)\s+(?:please|batao|mei|mein|me)\b",
        re.IGNORECASE,
    )
    POLICY_DISPUTE = re.compile(
        r"\b(?:policy\s+(?:is\s+)?(?:completely\s+)?unfair|unfair\s+policy|dispute\s+(?:this\s+)?charge|refuse\s+(?:to\s+pay\s+)?(?:this\s+)?charge|charging\s+me\s+wrong|illegal\s+charge|refund\s+dispute|policy\s+dispute|cancel\s+without\s+fee|not\s+paying\s+fee|unauthorized\s+charge)\b",
        re.IGNORECASE,
    )
    MISUNDERSTANDING = (
        "that's not what i asked", "that is not what i asked", "you keep asking",
        "you keep repeating", "i already told you", "not listening", "not understanding", "you misunderstood",
        "booking is wrong", "my booking is wrong", "again and again",
    )
    FAILURE_TEXT = (
        "having trouble", "unable to retrieve", "couldn't retrieve", "could not retrieve",
        "booking failed", "api failure", "something went wrong",
    )

    def __init__(self, memory: ConversationMemory) -> None:
        self.memory = memory
        self.summary_agent = HandoffSummaryAgent()

    def evaluate(
        self,
        message: str,
        sentiment: SentimentResult,
        result: AgentResponse | None = None,
    ) -> EscalationResult:
        lowered = message.lower()
        reason = ""
        previous_escalation = self.memory.data.get("escalation_state", {}) or {}

        response_text = str(getattr(result, "response", "")).lower()
        booking_result = getattr(result, "booking_result", None) or getattr(result, "booking", None)
        failed = any(term in response_text for term in self.FAILURE_TEXT)
        if isinstance(booking_result, dict):
            failed = failed or str(booking_result.get("status", "")).lower() in {"error", "failed"}
        failure_count = int(self.memory.data.get("failed_answer_count", 0))
        failure_count = failure_count + 1 if failed else max(0, failure_count - 1)
        self.memory.data["failed_answer_count"] = failure_count

        recent = self.memory.data.get("sentiment_history", [])[-3:]
        negative_count = sum(
            1 for item in recent if item.get("sentiment") in {"frustrated", "angry"}
        )
        recent_agent_turns = [
            re.sub(r"\s+", " ", str(turn.get("content", "")).lower()).strip()
            for turn in self.memory.data.get("conversation", [])
            if turn.get("role") == "agent" and turn.get("content")
        ][-3:]
        repeated_loop = bool(
            len(recent_agent_turns) == 3
            and len(set(recent_agent_turns)) == 1
            and not self._is_active_booking_update(message)
        )
        explicit_loop_complaint = any(phrase in lowered for phrase in self.MISUNDERSTANDING)
        explicit_negative_signal = (
            sentiment.sentiment in {"frustrated", "angry", "upset", "urgent"}
            or explicit_loop_complaint
        )
        profile_high_risk = getattr(sentiment, "sentiment_profile", {}).get("escalation_risk") == "high"

        if self.SAFETY_REQUEST.search(message):
            reason = "Customer reported an immediate safety concern"
        elif self.HUMAN_REQUEST.search(message):
            reason = "Customer explicitly requested a human representative"
        elif self.REFUND_REQUEST.search(message):
            reason = "Customer requested a refund"
        elif sentiment.sentiment == "angry":
            reason = "Customer anger detected"
        elif profile_high_risk and explicit_negative_signal:
            reason = "High conversation-wide escalation risk"
        elif (sentiment.escalation_recommended and explicit_negative_signal) or negative_count >= 2:
            reason = "Repeated customer frustration detected"
        elif explicit_loop_complaint:
            reason = "Repeated misunderstanding reported by customer"
        elif failure_count >= 2:
            reason = "Multiple agent or integration failures"
        elif failed:
            reason = "Booking or integration failure"
        elif repeated_loop and negative_count >= 1:
            reason = "Repeated conversation loop detected"
        elif self.LANGUAGE_REQUEST.search(message):
            reason = "Customer requested language or translation not supported by AI"
        elif self.POLICY_DISPUTE.search(message):
            reason = "Customer disputed venue policy or charges"
        elif any(term in lowered for term in ("refund dispute", "policy dispute", "this policy is unfair", "refuse this charge")):
            reason = "Policy or refund dispute"

        if not reason and previous_escalation.get("escalate"):
            preserved = EscalationResult(
                True,
                str(previous_escalation.get("reason") or "Escalation already requested"),
                str(previous_escalation.get("summary") or "Escalation already requested."),
                priority=str(previous_escalation.get("priority") or "medium"),
                status=str(previous_escalation.get("status") or "unresolved"),
                trigger=str(previous_escalation.get("trigger") or "manual_review"),
                recommended_human_action=str(
                    previous_escalation.get("recommended_human_action")
                    or "Human should review the existing escalation"
                ),
            )
            self.memory.data["escalation_state"] = preserved.to_dict()
            self.memory.save()
            return preserved

        provisional = {"escalate": bool(reason), "reason": reason}
        # Safety: if summary generation fails, never suppress a safety/human escalation.
        summary = ""
        if reason:
            try:
                summary = self.summary_agent.generate(self.memory.data, provisional)["summary"]
            except Exception:  # pragma: no cover
                summary = "(summary unavailable)"
        priority, action = self._priority_and_action(reason, sentiment)
        escalation = EscalationResult(
            bool(reason),
            reason,
            summary,
            priority=priority,
            status="unresolved" if reason else "resolved",
            trigger=self._trigger_for_reason(reason),
            recommended_human_action=action,
        )
        self.memory.data["escalation_state"] = escalation.to_dict()
        if escalation.escalate:
            history = self.memory.data.setdefault("escalation_history", [])
            history.append({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                **escalation.to_dict(),
            })
            self.memory.data["escalation_history"] = history[-10:]
        self.memory.save()
        return escalation

    @staticmethod
    def _trigger_for_reason(reason: str) -> str:
        lowered = reason.lower()
        if "safety" in lowered:
            return "safety_concern"
        if "refund" in lowered:
            return "refund_request"
        if "human" in lowered:
            return "human_request"
        if "frustration" in lowered or "anger" in lowered:
            return "frustration"
        if "loop" in lowered:
            return "unresolved_loop"
        if "failure" in lowered:
            return "integration_failure"
        return "none" if not reason else "manual_review"

    @staticmethod
    def _priority_and_action(reason: str, sentiment: SentimentResult) -> tuple[str, str]:
        if not reason:
            return "low", "No human action required"
        lowered = reason.lower()
        if "safety" in lowered:
            return "high", "Immediately alert on-site staff or emergency services and contact the customer"
        if "refund" in lowered:
            return "high", "Review booking/payment context and contact customer about refund request"
        if "human" in lowered:
            return "medium", "Connect customer to a human representative with full call context"
        if getattr(sentiment, "sentiment_profile", {}).get("escalation_risk") == "high":
            return "high", "Manager should review and contact customer before the issue escalates"
        if "frustration" in lowered or "anger" in lowered or "loop" in lowered:
            return "medium", "Human should take over or send a corrective follow-up"
        return "medium", "Manager should review the conversation"

    @staticmethod
    def _is_active_booking_update(message: str) -> bool:
        lowered = message.lower().strip()
        if any(
            term in lowered
            for term in (
                "actually", "instead", "change", "switch", "update", "correct",
                "correction", "make it", "use", "rather", "we are", "we're",
                "there are", "of us", "people", "players", "participants",
                "back to booking", "continue booking", "let's book", "lets book",
                "my name is", "phone", "mobile", "contact number",
                "morning", "afternoon", "evening",
            )
        ):
            return True
        if re.search(r"\b(?:june|july|august|september|october|november|december|tomorrow|today|next\s+\w+)\b", lowered):
            return True
        if re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", lowered):
            return True
        if re.search(r"\b(?:murder mystery|hostage|classified|bomb defusal|bomb diffusal|prison break|undercover)\b", lowered):
            return True
        if re.search(r"\b(?:koramangala|whitefield|jp\s+nagar)\b", lowered):
            return True
        return False
