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
    support_ticket_id: str = ""
    transfer_required: bool = False
    transfer_status: str = "not_required"
    recommended_action: str = ""
    category: str = ""

    def __post_init__(self) -> None:
        escalate = self.escalate
        object.__setattr__(self, "transfer_required", escalate)
        object.__setattr__(self, "transfer_status", "pending_configuration" if escalate else "not_required")
        object.__setattr__(self, "recommended_action", self.recommended_human_action or "Review the conversation and contact the customer.")
        
        t = self.trigger
        category = t
        if t == "safety_concern":
            category = "safety"
        elif t == "refund_request":
            category = "refund"
        elif t == "unresolved_loop":
            category = "conversation_failure"
        elif t == "integration_failure":
            category = "integration_failure"
        object.__setattr__(self, "category", category)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        trigger = self.category
        if trigger == "refund":
            trigger = "refund_request"
        elif trigger == "safety":
            trigger = "safety_concern"
        d["trigger"] = trigger
        return d


class EscalationAgent:
    """Detect takeover conditions without changing business routing."""

    HUMAN_REQUEST = re.compile(
        r"\b(?:speak|talk|connect|transfer|put\s+me\s+through|reach)\s+(?:me\s+)?(to|with)\s+(?:a\s+|an\s+|the\s+|your\s+)?(?:human|person|manager|supervisor|agent|team|support|representative|rep|operator|someone|somebody|customer\s+service)\b"
        r"|\b(?:real|live|actual)\s+(?:person|human|agent|representative|rep)\b"
        r"|\bi\s+(?:want|need)\s+(?:to\s+(?:speak|talk|connect)\s+(?:to|with)\s+)?(?:a\s+|an\s+)?(?:human|person|manager|supervisor|agent|representative|rep|operator|someone|somebody|support|customer\s+service)\b"
        r"|\bget\s+me\s+(?:a\s+)?(?:human|person|representative|rep|manager|operator|someone|somebody)\b"
        r"|\bconnect\s+me\s+(?:to|with)\s+(?:someone|anyone|somebody|anybody|a\s+(?:human|person|manager|supervisor|agent|representative|rep|operator|support|customer\s+service))\b"
        r"|\bplease\s+connect\s+me\s+(?:with|to)\b"
        r"|\b(?:speak|talk)\s+to\s+(?:your|a|the)\s+manager\b"
        r"|\b(?:human|live\s+agent|customer\s+(?:care|service)|representative|operator)\s+please\b"
        r"|\b(?:can|could|would)\s+i\s+(?:speak|talk)\s+(?:to|with)\s+(?:someone|anyone|a\s+person|a\s+human|an\s+agent)\b",
        re.IGNORECASE,
    )
    SAFETY_REQUEST = re.compile(
        r"\b(?:injured|injury|hurt|bleeding|cannot\s+breathe|can't\s+breathe|unresponsive|"
        r"not\s+responding|fire|smoke|burning|medical\s+emergency|safety\s+emergency|"
        r"fainted|faint|collapsed|collapse|passed\s+out|unconscious|seizure|"
        r"heart\s+attack|not\s+breathing|someone\s+fell|emergency|ambulance|hospital|"
        r"first\s+aid|safety\s+concern|safety\s+issue|panic\s+attack|claustrophobia|"
        r"claustrophobic|let\s+me\s+out|get\s+me\s+out|threat|threatening|harassment|"
        r"harassing|weapon|gun|knife|attack\s+someone|hurt\s+someone)\b",
        re.IGNORECASE,
    )
    REFUND_REQUEST = re.compile(
        r"\b(?:want|need|get|ask|asking|request|demand|deserve|have|receive|process|issue|claim|obtain|would\s+like|desire|expecting)\s+(?:for\s+)?(?:(?:me\s+)?a\s+|my\s+|the\s+|any\s+)?refund\b"
        r"|\brefund\s+(?:me|my|us|the|this|that|card|booking|payment|account|charge)\b"
        r"|\b(?:give\s+me|get|want|need|have)\s+(?:my\s+)?money\s+back\b"
        r"|\bgive\s+me\s+(?:a\s+)?refund\b"
        r"|\b(?:can|could|would|will|please)\s+(?:you\s+)?refund\b"
        r"|\brefund\s+please\b"
        r"|\bplease\s+refund\b",
        re.IGNORECASE,
    )
    MISUNDERSTANDING = (
        "that's not what i asked", "that is not what i asked", "you keep asking",
        "i already told you", "not listening", "not understanding", "you misunderstood",
        "booking is wrong", "my booking is wrong", "again and again",
    )
    FAILURE_TEXT = (
        "having trouble", "unable to retrieve", "couldn't retrieve", "could not retrieve",
        "unable to fetch", "couldn't fetch", "could not fetch", "failed to fetch",
        "booking failed", "booking creation error", "api failure", "api unavailable",
        "service unavailable", "request timed out", "connection timed out", "something went wrong",
    )
    PAYMENT_ISSUE = re.compile(
        r"\b(?:payment\s+(?:completed|successful|succeeded|done)\s+but\s+(?:the\s+)?booking\s+(?:is\s+)?not\s+confirmed|"
        r"payment\s+(?:failed|declined)|"
        r"paid\s+but|payment\s+link\s+(?:is\s+)?not\s+working|duplicate\s+payment|"
        r"charged\s+twice|debited\s+twice|money\s+(?:was\s+)?deducted|booking\s+not\s+confirmed\s+after\s+payment)\b",
        re.IGNORECASE,
    )
    AUTHENTICATION_FAILURE = re.compile(
        r"\b(?:verification\s+failed|unable\s+to\s+verify|cannot\s+verify|can't\s+verify|"
        r"identity\s+mismatch|details\s+(?:do\s+not|don't)\s+match|wrong\s+otp|invalid\s+otp|"
        r"authentication\s+failed|booking\s+verification\s+failed)\b",
        re.IGNORECASE,
    )
    UNRESOLVED_TEXT = (
        "i'm not sure", "i am not sure", "don't have that detail", "do not have that detail",
        "unable to answer", "can't answer", "cannot answer", "unsupported request",
        "team would be the best people to help",
    )
    DIRECT_FRUSTRATION = re.compile(
        r"\b(?:this\s+is\s+(?:ridiculous|unacceptable|useless)|you(?:'re|\s+are)\s+not\s+helping|"
        r"you\s+keep\s+(?:repeating|asking)|i(?:'ve|\s+have)\s+(?:called|asked|said)\s+(?:multiple|several)\s+times|"
        r"how\s+many\s+times|stop\s+repeating|terrible\s+service|i(?:'m|\s+am)\s+(?:angry|furious|frustrated))\b",
        re.IGNORECASE,
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

        response_text = str(getattr(result, "response", "")).lower()
        booking_result = getattr(result, "booking_result", None) or getattr(result, "booking", None)
        failed = any(term in response_text for term in self.FAILURE_TEXT)
        unresolved = any(term in response_text for term in self.UNRESOLVED_TEXT)
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
        existing_state = self.memory.data.get("escalation_state", {}) or {}
        is_escalated = bool(existing_state.get("escalate"))
        repeated_loop = bool(
            len(recent_agent_turns) == 3
            and len(set(recent_agent_turns)) == 1
            and not self._is_active_booking_update(message)
        ) or (is_escalated and unresolved)
        repeated_customer_question = self._is_repeated_customer_question(message)

        auth_failures = int(self.memory.data.get("authentication_failure_count", 0))
        if self.AUTHENTICATION_FAILURE.search(message):
            auth_failures += 1
        self.memory.data["authentication_failure_count"] = auth_failures

        payment_issue = bool(self.PAYMENT_ISSUE.search(message))
        payment_status = str(self.memory.data.get("payment_status", "")).lower()
        payment_issue = payment_issue or (
            payment_status in {"paid", "success", "successful", "completed"}
            and not (self.memory.data.get("booking_id") or self.memory.data.get("booking_ref"))
        )

        if self.SAFETY_REQUEST.search(message):
            reason = "Customer reported an immediate safety concern"
        elif self.HUMAN_REQUEST.search(message):
            reason = "Customer explicitly requested a human representative"
        elif payment_issue:
            reason = "Payment issue requires manual support"
        elif auth_failures >= 2:
            reason = "Multiple failed authentication attempts"
        elif self.REFUND_REQUEST.search(message):
            reason = "Customer requested a refund"
        elif self.DIRECT_FRUSTRATION.search(message):
            reason = "Customer frustration detected"
        elif sentiment.sentiment == "angry":
            reason = "Customer anger detected"
        elif getattr(sentiment, "sentiment_profile", {}).get("escalation_risk") == "high":
            reason = "High conversation-wide escalation risk"
        elif sentiment.escalation_recommended or negative_count >= 2:
            reason = "Repeated customer frustration detected"
        elif any(phrase in lowered for phrase in self.MISUNDERSTANDING):
            reason = "Repeated misunderstanding reported by customer"
        elif repeated_loop:
            reason = "Repeated conversation loop detected"
        elif repeated_customer_question:
            reason = "Repeated conversation loop detected"
        elif unresolved:
            reason = "Agent could not answer the customer query"
        elif failure_count >= 2:
            reason = "Multiple agent or integration failures"
        elif failed:
            reason = "Booking or integration failure"
        elif any(term in lowered for term in ("refund dispute", "policy dispute", "this policy is unfair", "refuse this charge")):
            reason = "Policy or refund dispute"

        provisional = {"escalate": bool(reason), "reason": reason}
        # Safety: if summary generation fails, never suppress a safety/human escalation.
        summary = ""
        if reason:
            try:
                summary = self.summary_agent.generate(self.memory.data, provisional)["summary"]
            except Exception:  # pragma: no cover
                summary = "(summary unavailable)"

        priority, action = self._priority_and_action(reason, sentiment)
        
        existing_state = self.memory.data.get("escalation_state", {}) or {}
        ticket_id = ""
        if reason:
            if existing_state.get("reason") == reason and existing_state.get("support_ticket_id"):
                ticket_id = str(existing_state["support_ticket_id"])
            else:
                counter = len(self.memory.data.get("support_tickets", [])) + 1
                call_id = re.sub(r"[^A-Za-z0-9]", "", str(self.memory.data.get("call_id", "call")))[-8:] or "call"
                ticket_id = f"ESC-{call_id}-{counter:03d}"

        escalation = EscalationResult(
            escalate=bool(reason),
            reason=reason,
            summary=summary,
            priority=priority,
            status="unresolved" if reason else "resolved",
            trigger=self._trigger_for_reason(reason),
            recommended_human_action=action,
            support_ticket_id=ticket_id,
        )

        self.memory.data["escalation_state"] = escalation.to_dict()
        if escalation.escalate:
            now = datetime.now().isoformat(timespec="seconds")
            try:
                handoff_payload = self.summary_agent.generate(self.memory.data, escalation.to_dict())
            except Exception:
                handoff_payload = {
                    "call_id": self.memory.data.get("call_id", ""),
                    "customer_name": self.memory.data.get("customer_name", ""),
                    "phone": self.memory.data.get("phone", ""),
                    "intent": self.memory.data.get("intent", ""),
                    "booking_id": self.memory.data.get("booking_id", ""),
                    "booking_ref": self.memory.data.get("booking_ref", ""),
                    "payment_status": self.memory.data.get("payment_status", "not_started"),
                    "sentiment": self.memory.data.get("sentiment", "neutral"),
                    "escalation_reason": reason,
                    "recommended_next_action": action,
                    "transcript": list(self.memory.data.get("conversation", [])),
                    "summary": summary or "Escalation summary unavailable; review the attached transcript.",
                }
            history = self.memory.data.setdefault("escalation_history", [])
            history.append({
                "timestamp": now,
                **escalation.to_dict(),
            })
            self.memory.data["escalation_history"] = history[-10:]
            requests = self.memory.data.setdefault("escalation_requests", [])
            if not requests or requests[-1].get("support_ticket_id") != ticket_id:
                requests.append({"timestamp": now, **escalation.to_dict(), "handoff": handoff_payload})
            self.memory.data["escalation_requests"] = requests[-20:]
            tickets = self.memory.data.setdefault("support_tickets", [])
            if not tickets or tickets[-1].get("ticket_id") != ticket_id:
                tickets.append({
                    "ticket_id": ticket_id,
                    "created_at": now,
                    "status": "open",
                    "priority": priority,
                    "category": escalation.category,
                    "reason": reason,
                    "recommended_action": action,
                })
            self.memory.data["support_tickets"] = tickets[-20:]
            
            if unresolved or "gap" in escalation.category or "could not answer" in reason.lower():
                unresolved_queries = self.memory.data.setdefault("unresolved_queries", [])
                unresolved_queries.append({
                    "timestamp": now,
                    "query": message,
                    "agent_response": str(getattr(result, "response", "")),
                    "support_ticket_id": ticket_id,
                })
                self.memory.data["unresolved_queries"] = unresolved_queries[-20:]

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
        if "loop" in lowered or "repeated the same unresolved question" in lowered:
            return "unresolved_loop"
        if "booking or integration failure" in lowered or "booking failure" in lowered:
            return "booking_failure"
        if "failure" in lowered:
            return "integration_failure"
        if "payment" in lowered:
            return "payment"
        if "authentication" in lowered:
            return "authentication"
        if "could not answer" in lowered or "knowledge gap" in lowered:
            return "knowledge_gap"
        return "none" if not reason else "manual_review"

    @staticmethod
    def _priority_and_action(reason: str, sentiment: SentimentResult) -> tuple[str, str]:
        if not reason:
            return "low", "No human action required"
        lowered = reason.lower()
        if "safety" in lowered:
            return "critical", "Immediately alert on-site staff or emergency services and contact the customer"
        if "refund" in lowered:
            return "high", "Review booking/payment context and contact customer about refund request"
        if "human" in lowered:
            return "high", "Connect customer to a human representative with full call context"
        if "payment" in lowered or "authentication" in lowered:
            return "high", "Resolve payment or authentication issue manually and reconcile the transaction"
        if getattr(sentiment, "sentiment_profile", {}).get("escalation_risk") == "high":
            return "high", "Manager should review and contact customer before the issue escalates"
        if "frustration" in lowered or "anger" in lowered or "loop" in lowered:
            return "high", "Human should take over or send a corrective follow-up"
        return "medium", "Manager should review the conversation"

    @staticmethod
    def _is_active_booking_update(message: str) -> bool:
        lowered = message.lower().strip()
        if any(
            term in lowered
            for term in (
                "actually", "instead", "change", "switch", "update", "correct",
                "correction", "make it", "use", "rather", "we are", "we're",
                "my name is", "phone", "mobile", "contact number",
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

    def _is_repeated_customer_question(self, message: str) -> bool:
        """Detect a question repeated three times, independent of agent wording."""
        canonical = self._canonical_question(message)
        if not canonical or self._is_active_booking_update(message):
            return False
        customer_turns = [
            self._canonical_question(str(turn.get("content", "")))
            for turn in self.memory.data.get("conversation", [])[-12:]
            if turn.get("role") in {"customer", "user"}
        ]
        # The current message is normally already persisted, but direct callers
        # evaluate before persistence. Include it only when it is absent.
        if not customer_turns or customer_turns[-1] != canonical:
            customer_turns.append(canonical)
        return sum(1 for turn in customer_turns if turn == canonical) >= 3

    @staticmethod
    def _canonical_question(message: str) -> str:
        clean = re.sub(r"[^a-z0-9\s]", " ", message.lower())
        clean = re.sub(
            r"\b(?:please|just|again|actually|well|so|um|uh|can\s+you|could\s+you|would\s+you|"
            r"i\s+want\s+to\s+know|tell\s+me|i\s+asked)\b",
            " ",
            clean,
        )
        clean = re.sub(r"\s+", " ", clean).strip()
        clean = re.sub(
            r"^(?:what|which|how|when|where|why)\s+(?:is|are|does|do|can|will|would)\s+(?:the\s+|a\s+|an\s+)?",
            "",
            clean,
        )
        clean = re.sub(r"^(?:the|a|an)\s+", "", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        if len(clean.split()) < 3:
            return ""
        question_signal = re.search(
            r"\b(?:what|when|where|which|who|why|how|policy|price|cost|refund|cancel|availability|available)\b",
            clean,
        )
        return clean if question_signal else ""
