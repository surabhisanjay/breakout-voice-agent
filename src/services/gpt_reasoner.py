from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any


ALLOWED_ACTIONS = {
    "answer_faq",
    "answer_rules",
    "answer_policy",
    "answer_parking",
    "answer_food",
    "answer_location",
    "answer_discount",
    "answer_unknown_question",   # customer asked something we can't find in knowledge
    "explain_room",
    "compare_rooms",
    "recommend_room",
    "recommend_location",
    "ask_missing_information",
    "check_availability",
    "prepare_booking",
    "complete_booking",
    "resume_qualification",
    "end_conversation",
    "repeat_previous_question",
    "fallback",
}


PROTECTED_FIELDS = {
    "participants",
    "company_size",
    "age_group",
    "preferred_date",
    "location",
    "phone",
    "customer_name",
}


@dataclass
class ReasonerDecision:
    action: str = "fallback"
    topic: str = ""
    room: str = ""
    reason: str = ""
    next_action: str = ""
    should_resume_qualification: bool = False
    confidence: float = 0.0
    protected_updates: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReasonerDecision":
        action = str(data.get("action", "fallback")).strip()
        if action not in ALLOWED_ACTIONS:
            action = "fallback"
        next_action = str(data.get("next_action", "")).strip()
        if next_action and next_action not in ALLOWED_ACTIONS:
            next_action = ""
        protected_updates = {
            key: value
            for key, value in dict(data.get("protected_updates") or {}).items()
            if key in PROTECTED_FIELDS
        }
        return cls(
            action=action,
            topic=str(data.get("topic", "") or ""),
            room=str(data.get("room", "") or ""),
            reason=str(data.get("reason", "") or ""),
            next_action=next_action,
            should_resume_qualification=bool(data.get("should_resume_qualification", False)),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            protected_updates=protected_updates,
        )

    def actionable(self) -> bool:
        return self.action not in {"", "fallback"} and self.confidence >= 0.45


class GPTReasoner:
    """
    Optional conversation-planning layer.

    It chooses what the deterministic agents should do next. It never creates
    bookings, checks slots, writes protected slots, or calls Kreeda directly.
    """

    def __init__(
        self,
        model: str = "gpt-4.1-mini",
        enabled: bool | None = None,
        timeout: float = 2.0,
    ) -> None:
        self.model = model
        env_enabled = os.environ.get("BREAKOUT_GPT_REASONER", "false").lower() == "true"
        self.enabled = env_enabled if enabled is None else enabled
        self.timeout = timeout
        self.last_error = ""
        self.last_latency = 0.0

    def decide(
        self,
        *,
        message: str,
        memory: dict[str, Any],
        pending_question: str = "",
        available_actions: list[str] | None = None,
    ) -> ReasonerDecision | None:
        self.last_error = ""
        self.last_latency = 0.0
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not self.enabled or not api_key:
            return None

        payload = self._reasoner_payload(message, memory, pending_question, available_actions)
        start = time.perf_counter()
        try:
            try:
                import openai

                OpenAI = getattr(openai, "OpenAI", None)
                APITimeoutError = getattr(openai, "APITimeoutError", Exception)
                APIConnectionError = getattr(openai, "APIConnectionError", Exception)
                RateLimitError = getattr(openai, "RateLimitError", Exception)
                AuthenticationError = getattr(openai, "AuthenticationError", Exception)
            except (ImportError, AttributeError):
                OpenAI = None
                APITimeoutError = Exception
                APIConnectionError = Exception
                RateLimitError = Exception
                AuthenticationError = Exception

            if OpenAI is None:
                raise RuntimeError("openai package not installed")

            client = OpenAI(api_key=api_key, timeout=self.timeout, max_retries=0)
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                tools=[self._decision_tool()],
                tool_choice={"type": "function", "function": {"name": "choose_breakout_action"}},
                max_tokens=180,
            )
            args = self._extract_tool_arguments(response)
            if not args:
                self.last_error = "empty_reasoner_decision"
                return None
            decision = ReasonerDecision.from_dict(args)
            self.last_latency = time.perf_counter() - start
            if decision.protected_updates:
                decision.protected_updates = {}
            return decision if decision.actionable() else None
        except (APITimeoutError, APIConnectionError, RateLimitError, AuthenticationError) as exc:
            self.last_latency = time.perf_counter() - start
            self.last_error = f"openai_failure: {exc}"
            self.enabled = False
            return None
        except Exception as exc:
            self.last_latency = time.perf_counter() - start
            self.last_error = f"openai_failure: {exc}"
            self.enabled = False
            return None

    @staticmethod
    def _reasoner_payload(
        message: str,
        memory: dict[str, Any],
        pending_question: str,
        available_actions: list[str] | None,
    ) -> dict[str, Any]:
        safe_memory = {
            key: memory.get(key, "")
            for key in (
                "customer_name",
                "phone",
                "location",
                "participants",
                "company_size",
                "age_group",
                "experience_level",
                "challenge_preference",
                "event_type",
                "preferred_date",
                "food_required",
                "budget_range",
                "room",
                "intent",
                "recommended_option",
                "current_workflow",
                "conversation_mode",
                "booking_consent_pending",
                "completed_booking",
                "booking_ref",
                "last_discussed_topic",
                "discussed_options",
            )
        }
        return {
            "customer_message": message,
            "conversation_memory": safe_memory,
            "recent_conversation": memory.get("conversation", [])[-8:],
            "booking_state": {
                "completed_booking": bool(memory.get("completed_booking")),
                "current_workflow": memory.get("current_workflow", ""),
                "booking_consent_pending": bool(memory.get("booking_consent_pending")),
            },
            "pending_qualification_question": pending_question,
            "available_actions": available_actions or sorted(ALLOWED_ACTIONS),
        }

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are the Breakout Escape Rooms conversation reasoner. "
            "Your ONLY job is to choose the next deterministic action — do NOT write the final response.\n\n"
            "MANDATORY QUESTION-FIRST RULE (highest priority, no exceptions):\n"
            "Before choosing any qualification action, you MUST determine:\n"
            "  1. Did the customer ask a question? (explicit '?' OR implicit phrasing like 'explain more', "
            "'tell me more', 'why', 'before that', 'your favorite', 'best room', 'most popular')\n"
            "  2. If yes — answer the question FIRST using the appropriate action:\n"
            "     - Recommendation questions ('explain more', 'tell me more', 'which room', 'your favorite', "
            "'best room', 'most popular room', 'why', 'suggest') → recommend_room or compare_rooms\n"
            "     - How-it-works / rules FAQ ('how does it work', 'are we locked in', 'what happens if we fail', "
            "'can kids play', 'how long') → answer_faq or answer_rules\n"
            "     - Policy ('cancellation', 'refund', 'reschedule', 'discount', 'late arrival') → answer_policy\n"
            "     - Repair ('what?', 'sorry?', 'come again', 'didn't catch') → repeat_previous_question\n"
            "     - Unknown question (customer asked something with no match in knowledge) → answer_unknown_question\n"
            "  3. Only AFTER the question is answered (or if no question was asked) may you use:\n"
            "     ask_missing_information, resume_qualification, check_availability, prepare_booking, complete_booking.\n\n"
            "Additional rules:\n"
            "- After a completed booking, only answer questions unless the customer explicitly asks to "
            "create, change, reschedule, or cancel a booking.\n"
            "- Resolve contextual references using last_discussed_topic and discussed_options.\n"
            "- Never perform bookings directly and never overwrite participant count, age group, date, "
            "location, name, or phone.\n"
            "- Never hallucinate. If the answer is not in knowledge, use answer_unknown_question.\n"
            "- set should_resume_qualification=true when you answer a question mid-qualification so the "
            "agent appends the pending qualification question after the answer."
        )

    @staticmethod
    def _decision_tool() -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "choose_breakout_action",
                "description": "Choose the next safe deterministic action for the Breakout agent.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": sorted(ALLOWED_ACTIONS)},
                        "topic": {"type": "string"},
                        "room": {"type": "string"},
                        "reason": {"type": "string"},
                        "next_action": {"type": "string", "enum": sorted(ALLOWED_ACTIONS)},
                        "should_resume_qualification": {"type": "boolean"},
                        "confidence": {"type": "number"},
                        "protected_updates": {"type": "object"},
                    },
                    "required": ["action", "confidence"],
                    "additionalProperties": False,
                },
            },
        }

    @staticmethod
    def _extract_tool_arguments(response: Any) -> dict[str, Any]:
        try:
            message = response.choices[0].message
            tool_calls = getattr(message, "tool_calls", None) or []
            if tool_calls:
                raw_args = tool_calls[0].function.arguments
                return json.loads(raw_args or "{}")
            content = getattr(message, "content", "") or ""
            if content.strip().startswith("{"):
                return json.loads(content)
        except Exception:
            return {}
        return {}
