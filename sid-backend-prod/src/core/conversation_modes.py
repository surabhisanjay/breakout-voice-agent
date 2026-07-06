from __future__ import annotations

import re
from enum import Enum


class ConversationMode(str, Enum):
    SALES = "sales"
    RECOMMENDATION = "recommendation"
    BOOKING = "booking"
    RESCUE = "rescue"
    FAQ = "faq"


class ConversationModeDetector:
    RESCUE_TERMS = (
        "running late", "we are late", "we're late", "will be late",
        "confused", "don't understand", "do not understand", "stuck",
        "unclear", "can't hear", "cannot hear", "help us",
    )
    RECOMMENDATION_TERMS = (
        "recommend", "suggest", "which room", "which one", "best room",
        "first time", "first-time", "never played", "never done",
    )
    BOOKING_TERMS = (
        "book", "booking", "availability", "available slot", "confirm",
        "reserve", "payment link",
    )
    FAQ_TERMS = (
        "what happens", "what is", "how long", "parking", "food",
        "location", "locations", "age limit", "minimum players",
        "cancellation", "cancel", "refund", "briefing", "game rules",
        "actually locked", "don't escape", "do not escape",
    )

    def detect(self, message: str, state: dict, intent: str = "") -> ConversationMode:
        lowered = message.lower()
        workflow = str(state.get("current_workflow", ""))

        if any(term in lowered for term in self.RESCUE_TERMS) or re.search(r"\b(?:running|arriving|be)\b.*\blate\b", lowered):
            return ConversationMode.RESCUE
        if any(term in lowered for term in self.RECOMMENDATION_TERMS):
            return ConversationMode.RECOMMENDATION
        if any(term in lowered for term in self.FAQ_TERMS):
            return ConversationMode.FAQ
        if any(term in lowered for term in self.BOOKING_TERMS):
            return ConversationMode.BOOKING
        if intent in {"general_faq", "cancellation_request"}:
            return ConversationMode.FAQ
        if workflow in {"booking", "awaiting_booking"}:
            return ConversationMode.BOOKING
        if intent in {"escape_room_inquiry", "couple_event"} and state.get("recommended_option"):
            return ConversationMode.RECOMMENDATION
        if "?" in message:
            return ConversationMode.FAQ
        return ConversationMode.SALES
