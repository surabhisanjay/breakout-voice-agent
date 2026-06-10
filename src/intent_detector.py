from __future__ import annotations

import re
from dataclasses import dataclass


SUPPORTED_INTENTS = {
    "escape_room_inquiry",
    "birthday_party",
    "bachelor_party",
    "farewell_party",
    "couple_event",
    "corporate_event",
    "virtual_event",
    "cancellation_request",
    "general_faq",
}


@dataclass(frozen=True)
class IntentResult:
    intent: str
    confidence: float
    reason: str


class IntentDetector:
    STRONG_TOPIC_TERMS = {
        "escape_room_inquiry": ("escape room", "room recommendation", "which room", "game", "puzzle", "hardest room"),
        "birthday_party": ("birthday", "bday", "cake"),
        "corporate_event": ("corporate", "office", "team building", "employee", "employees", "company", "hr", "team outing"),
        "bachelor_party": ("bachelor", "stag", "groom"),
        "farewell_party": ("farewell", "send off", "last day", "goodbye party"),
        "couple_event": ("couple", "date", "anniversary", "two of us", "2 of us"),
        "virtual_event": ("virtual", "online", "remote", "distributed"),
        "cancellation_request": ("cancel", "cancellation", "refund", "reschedule", "postpone"),
    }

    PATTERNS: list[tuple[str, tuple[str, ...]]] = [
        ("cancellation_request", ("cancel", "cancellation", "refund", "reschedule", "postpone")),
        ("birthday_party", ("birthday", "bday", "cake", "kids party", "child birthday")),
        ("bachelor_party", ("bachelor", "stag", "boys party", "groom")),
        ("farewell_party", ("farewell", "send off", "last day", "goodbye party")),
        ("couple_event", ("couple", "date", "anniversary", "two of us", "2 of us")),
        ("corporate_event", ("corporate", "office", "team building", "employee", "company", "hr", "team outing")),
        ("virtual_event", ("virtual", "online", "remote", "distributed")),
        ("escape_room_inquiry", ("escape room", "room", "game", "puzzle", "challenge", "adults", "kids", "players", "people", "visiting", "coming", "recommend", "suggest", "compare", "friends", "group")),
    ]

    FAQ_TERMS = (
        "is this",
        "what is",
        "how long",
        "children",
        "kids",
        "parking",
        "location",
        "locations",
        "where",
        "walk in",
        "prior experience",
    )

    def detect(self, message: str, previous_intent: str = "") -> IntentResult:
        text = message.lower().strip()
        if not text:
            return IntentResult(previous_intent or "general_faq", 0.2, "empty message")

        for intent, keywords in self.PATTERNS:
            for keyword in keywords:
                if len(keyword) <= 4:
                    match = bool(re.search(r"\b" + re.escape(keyword) + r"\b", text))
                else:
                    match = keyword in text
                if match:
                    if previous_intent and previous_intent not in ("general_faq", intent):
                        if not self._is_clear_topic_switch(text, intent):
                            return IntentResult(previous_intent, 0.62, "preserved active intent")
                    return IntentResult(intent, 0.88, f"matched keyword '{keyword}'")

        if any(term in text for term in self.FAQ_TERMS) or re.search(r"\?$", text):
            return IntentResult("general_faq", 0.72, "faq-style question")

        return IntentResult(previous_intent or "general_faq", 0.45, "no strong match; preserving prior context")

    def _is_clear_topic_switch(self, text: str, intent: str) -> bool:
        return any(term in text for term in self.STRONG_TOPIC_TERMS.get(intent, ()))
