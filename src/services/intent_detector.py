"""intent_detector.py — context-aware intent detection for the booking agent.

Key improvements over v1:
  - Confirmation-state resolution: affirmatives/negatives checked BEFORE
    keyword matching, so "yes book that" never hits the classifier.
  - Slot-filling extraction: pulls participants, dates, names, phones inline.
  - Soft-context carry: low-confidence turns inherit the previous intent
    instead of falling back to general_faq.
  - Confidence thresholds are named constants, not magic numbers.
  - IntentResult is richer: carries extracted entities and a `needs_clarification` flag.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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
    "confirm_booking",   # new: explicit confirmation of a pending offer
    "deny_booking",      # new: explicit rejection of a pending offer
}

# Confidence thresholds
CONF_STRONG     = 0.88   # keyword match
CONF_PRESERVED  = 0.75   # carried over from previous intent (raised from 0.62)
CONF_FAQ        = 0.72   # FAQ heuristic
CONF_FALLBACK   = 0.45   # no match — now triggers clarification instead of silent drop
CONF_AFFIRM     = 0.95   # affirmative on a pending offer — near-certain
CONF_DENY       = 0.95   # negative on a pending offer


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IntentResult:
    intent: str
    confidence: float
    reason: str
    entities: Dict[str, Any] = field(default_factory=dict)
    needs_clarification: bool = False   # True → agent should ask a follow-up


# ---------------------------------------------------------------------------
# Affirmative / negative word lists
# ---------------------------------------------------------------------------

_AFFIRMATIVES = frozenset({
    "yes", "yep", "yeah", "yup", "sure", "ok", "okay", "okey",
    "go ahead", "go for it", "do it", "book it", "book that",
    "sounds good", "perfect", "confirmed", "confirm", "absolutely",
    "please", "please do", "that works", "let's do it", "let's go",
    "let's book that", "lets book that", "i'll take that one", "ill take that one",
    "let's continue", "lets continue",
})

# Relationship language is normal booking context. It becomes a package
# inquiry only when the customer explicitly requests a celebration add-on.
_COUPLE_PACKAGE_TERMS = (
    "anniversary package", "proposal package", "couple package", "romantic package",
    "candle light", "candlelight", "decorations", "celebration package",
    "surprise setup", "flowers", "balloons", "cake package",
)

_BOOKING_TRIGGERS = re.compile(
    r"\b(?:i want to book|want to book|can i book|book it|book now|book a|book for|reserve a|i want to reserve|how do i book|how can i book|how to book|how do you book|how do we book|book that|make a booking|want to make a booking|booking|bookings|reserve|reservation)\b",
    re.IGNORECASE,
)

_BOOKING_EVENT_HINTS = re.compile(
    r"\b(?:escape room|room|birthday|corporate|bachelor|farewell|couple|virtual|online|team building|party|"
    r"anniversary package|proposal package|couple package|romantic package|celebration package)\b",
    re.IGNORECASE,
)

_NEGATIVES = frozenset({
    "no", "nope", "nah", "don't", "do not", "cancel", "stop",
    "never mind", "nevermind", "forget it", "not now", "skip it",
    "actually no", "hold on", "wait",
})


def _is_affirmative(text: str) -> bool:
    t = text.lower().strip().rstrip(".")
    return t in _AFFIRMATIVES or any(a in t for a in _AFFIRMATIVES)


def _is_negative(text: str) -> bool:
    t = text.lower().strip().rstrip(".")
    return t in _NEGATIVES or any(n in t for n in _NEGATIVES)


def _is_escape_room_follow_up(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "what would you recommend",
            "what do you recommend",
            "which would you recommend",
            "which one would you recommend",
            "which room",
            "what room",
            "suggest",
            "recommend",
            "challenging",
            "beginner-friendly",
            "beginner friendly",
            "story-driven",
            "story driven",
            "more details",
            "tell me more",
            "compare",
            "whitefield",
            "koramangala",
            "jp nagar",
        )
    )


# ---------------------------------------------------------------------------
# Lightweight slot / entity extractor
# ---------------------------------------------------------------------------

_DATE_PATTERN = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?(?:\s+\d{4})?)\b",
    re.IGNORECASE,
)
_PHONE_PATTERN  = re.compile(r"\b[6-9]\d{9}\b")                 # Indian mobile
_COUNT_PATTERN  = re.compile(r"\b(\d+)\s*(?:people|persons?|guests?|of us|pax|players?|heads?)\b", re.IGNORECASE)
_NAME_TRIGGERS  = re.compile(r"(?:my name(?: is)?|i am|i'm|call me)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", re.IGNORECASE)


def extract_entities(text: str) -> Dict[str, Any]:
    entities: Dict[str, Any] = {}

    date_match = _DATE_PATTERN.search(text)
    if date_match:
        entities["preferred_date"] = date_match.group(0)

    phone_match = _PHONE_PATTERN.search(text)
    if phone_match:
        entities["phone"] = phone_match.group(0)

    count_match = _COUNT_PATTERN.search(text)
    if count_match:
        entities["participants"] = int(count_match.group(1))

    name_match = _NAME_TRIGGERS.search(text)
    if name_match:
        entities["customer_name"] = name_match.group(1)

    relationship_text = text.lower()
    if re.search(
        r"\b(?:couple|husband\s+and\s+wife|wife\s+and\s+husband|"
        r"girlfriend\s+and\s+boyfriend|boyfriend\s+and\s+girlfriend|"
        r"my\s+(?:husband|wife|girlfriend|boyfriend)\s+and\s+i|two\s+of\s+us)\b",
        relationship_text,
    ):
        entities["relationship"] = "couple"
        entities.setdefault("participants", 2)

    return entities


# ---------------------------------------------------------------------------
# Core detector
# ---------------------------------------------------------------------------

class IntentDetector:

    # ---- keyword taxonomy ----
    STRONG_TOPIC_TERMS: Dict[str, tuple] = {
        "escape_room_inquiry": ("escape room", "room recommendation", "which room", "game", "puzzle", "hardest room", "booking", "bookings", "reserve", "reservation"),
        "birthday_party":      ("birthday", "bday", "cake"),
        "corporate_event":     ("corporate", "office", "team building", "employee", "employees", "company", "hr", "team outing"),
        "bachelor_party":      ("bachelor", "stag", "groom"),
        "farewell_party":      ("farewell", "send off", "last day", "goodbye party"),
        "couple_event":        _COUPLE_PACKAGE_TERMS,
        "virtual_event":       ("virtual", "online", "remote", "distributed"),
        "cancellation_request":("cancel", "cancellation", "refund", "reschedule", "postpone"),
    }

    # Ordered: more specific intents first to prevent early-exit on generic terms
    PATTERNS: list[tuple[str, tuple]] = [
        ("cancellation_request", ("cancel", "cancellation", "refund", "reschedule", "postpone")),
        ("birthday_party",       ("birthday", "bday", "cake", "kids party", "child birthday")),
        ("bachelor_party",       ("bachelor", "stag", "boys party", "groom")),
        ("farewell_party",       ("farewell", "send off", "last day", "goodbye party")),
        ("couple_event",         _COUPLE_PACKAGE_TERMS),
        ("corporate_event",      ("corporate", "office", "team building", "employee", "company", "hr", "team outing")),
        ("virtual_event",        ("virtual", "online", "remote", "distributed")),
        ("escape_room_inquiry",  ("escape room", "room", "game", "puzzle", "challenge", "adults", "kids", "players", "people", "visiting", "coming", "recommend", "suggest", "compare", "friends", "group", "recommend a room", "suggest a room", "booking", "bookings", "reserve", "reservation")),
    ]

    FAQ_TERMS = (
        "is this", "what is", "how long", "children", "parking",
        "location", "locations", "where", "walk in", "prior experience",
        "how much", "price", "cost", "timings", "open", "age limit",
    )

    # ---- public API ----

    def detect(
        self,
        message: str,
        previous_intent: str = "",
        pending_offer: Optional[Dict[str, Any]] = None,
    ) -> IntentResult:
        """Classify `message` into an intent.

        Args:
            message:         Raw customer utterance.
            previous_intent: Intent resolved on the immediately prior turn.
            pending_offer:   If the agent made a booking offer last turn,
                             pass the offer dict here so affirmatives resolve
                             to `confirm_booking` before keyword matching.
        """
        text = message.lower().strip()
        entities = extract_entities(message)

        # ---- 0. Empty message ----
        if not text:
            return IntentResult(
                previous_intent or "general_faq", 0.2,
                "empty message", entities,
            )

        # ---- 1. Confirmation / denial of a pending offer ----
        #         This MUST run before keyword matching.
        if pending_offer:
            if _is_affirmative(text):
                return IntentResult(
                    "confirm_booking", CONF_AFFIRM,
                    "affirmative response to pending offer",
                    {**entities, "offer": pending_offer},
                )
            if _is_negative(text):
                return IntentResult(
                    "deny_booking", CONF_DENY,
                    "negative response to pending offer",
                    entities,
                )

        # ---- 2. Direct booking trigger ----
        if _BOOKING_TRIGGERS.search(text):
            if _BOOKING_EVENT_HINTS.search(text):
                if "corporate" in text or "office" in text or "company" in text or "employee" in text:
                    detected_intent = "corporate_event"
                elif "birthday" in text or "bday" in text:
                    detected_intent = "birthday_party"
                elif any(term in text for term in _COUPLE_PACKAGE_TERMS):
                    detected_intent = "couple_event"
                elif "bachelor" in text:
                    detected_intent = "bachelor_party"
                elif "farewell" in text:
                    detected_intent = "farewell_party"
                elif "virtual" in text or "online" in text:
                    detected_intent = "virtual_event"
                else:
                    detected_intent = "escape_room_inquiry"

                return IntentResult(
                    detected_intent, CONF_STRONG,
                    f"matched booking trigger with specific hint: {detected_intent}",
                    entities,
                )
            # Generic booking request without explicit event type; do not force escape_room_inquiry.
            if (
                previous_intent == "escape_room_inquiry"
                or entities.get("participants")
                or entities.get("relationship") == "couple"
                or bool(re.search(r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:adults?|kids?|children|people|players)\b", text))
                or any(location in text for location in ("whitefield", "koramangala", "jp nagar", "jp nagr"))
                or any(room in text for room in (
                    "murder mystery", "hostage", "classified", "bomb defusal",
                    "bomb diffusal", "prison break", "undercover",
                ))
            ):
                return IntentResult(
                    "escape_room_inquiry", CONF_STRONG,
                    "booking trigger with escape-room context",
                    entities,
                )
            return IntentResult(
                "general_faq", CONF_FALLBACK,
                "generic booking trigger",
                entities,
                needs_clarification=True,
            )

        if previous_intent == "escape_room_inquiry":
            if _is_escape_room_follow_up(text):
                return IntentResult(
                    "escape_room_inquiry", CONF_PRESERVED,
                    "escape-room follow-up preserved from prior context",
                    entities,
                )
            if re.fullmatch(r"(?:around|about|approximately|approx|roughly|maybe)?\s*\d{1,5}", text):
                return IntentResult(
                    "escape_room_inquiry", CONF_PRESERVED,
                    "participant-count follow-up preserved from prior context",
                    entities,
                )
            if re.fullmatch(r"\d{1,2}\s*(?:to|[-–—])\s*\d{1,2}(?:\s*years?)?", text):
                return IntentResult(
                    "escape_room_inquiry", CONF_PRESERVED,
                    "age-range follow-up preserved from prior context",
                    entities,
                )
            if re.fullmatch(r"(?:people will be joining\s+)?i want to book", text):
                return IntentResult(
                    "escape_room_inquiry", CONF_PRESERVED,
                    "noisy whisper follow-up preserved from prior context",
                    entities,
                    needs_clarification=True,
                )

        if (
            "recommend" in text or "suggest" in text or "which room" in text
        ) and (
            entities.get("participants")
            or entities.get("relationship") == "couple"
            or any(location in text for location in ("whitefield", "koramangala", "jp nagar", "jp nagr"))
        ):
            return IntentResult(
                "escape_room_inquiry", CONF_STRONG,
                "recommendation request with escape-room booking context",
                entities,
            )

        if (
            previous_intent in ("", "general_faq")
            and (
                re.search(r"\b(visiting|coming|players|kids|children|adults|people)\b", text)
                or bool(_COUNT_PATTERN.search(text))
            )
            and any(location in text for location in ("whitefield", "koramangala", "jp nagar", "jp nagr", "jp"))
            and not any(k in text for k in ("birthday", "bday", "corporate", "bachelor", "farewell", "cancel"))
        ):
            return IntentResult(
                "escape_room_inquiry", CONF_PRESERVED,
                "slot-carrying room-selection context inferred from message",
                entities,
            )

        # ---- 3. Keyword matching ----
        for intent, keywords in self.PATTERNS:
            for keyword in keywords:
                if len(keyword) <= 4:
                    match = bool(re.search(r"\b" + re.escape(keyword) + r"\b", text))
                else:
                    match = keyword in text
                if match:
                    if previous_intent and previous_intent not in ("general_faq", intent):
                        if not self._is_clear_topic_switch(text, intent):
                            return IntentResult(
                                previous_intent, CONF_PRESERVED,
                                "preserved active intent — no clear topic switch",
                                entities,
                            )
                    return IntentResult(
                        intent, CONF_STRONG,
                        f"matched keyword '{keyword}'",
                        entities,
                    )

        # ---- 3. FAQ heuristic ----
        if any(term in text for term in self.FAQ_TERMS) or re.search(r"\?$", text):
            return IntentResult("general_faq", CONF_FAQ, "faq-style question", entities)

        # ---- 4. Context carry-forward (was: silent 0.45 fallback) ----
        #         If we have a prior intent, keep it with a moderate confidence
        #         and flag that clarification might help.
        if previous_intent and previous_intent != "general_faq":
            return IntentResult(
                previous_intent, CONF_FALLBACK,
                "no strong match; carrying prior intent forward",
                entities,
                needs_clarification=True,
            )

        # ---- 5. True fallback ----
        return IntentResult(
            "general_faq", CONF_FALLBACK,
            "no match found",
            entities,
            needs_clarification=True,
        )

    def _is_clear_topic_switch(self, text: str, intent: str) -> bool:
        return any(term in text for term in self.STRONG_TOPIC_TERMS.get(intent, ()))


# ---------------------------------------------------------------------------
# Suggested router integration
# ---------------------------------------------------------------------------

def route_turn(
    message: str,
    session: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Thin router layer that wraps IntentDetector with session state.

    `session` is mutated in-place and returned. Callers should persist it
    between turns.

    Returns a routing decision:
      {
        "intent":              str,
        "confidence":          float,
        "entities":            dict,
        "handoff":             bool,
        "needs_clarification": bool,
        "clarification_prompt": str | None,
      }
    """
    detector = IntentDetector()
    previous_intent = session.get("intent", "")
    pending_offer   = session.get("pending_booking_offer")

    result = detector.detect(message, previous_intent, pending_offer)

    # Update session
    session["intent"]   = result.intent
    session["entities"] = {**session.get("entities", {}), **result.entities}

    # Clear the pending offer once it's been resolved either way
    if result.intent in ("confirm_booking", "deny_booking"):
        session.pop("pending_booking_offer", None)

    clarification_prompt = None
    if result.needs_clarification:
        clarification_prompt = _build_clarification(session)

    handoff = result.intent in (
        "birthday_party", "bachelor_party", "farewell_party",
        "couple_event", "corporate_event", "virtual_event",
        "confirm_booking",
    ) and result.confidence >= CONF_PRESERVED

    return {
        "intent":               result.intent,
        "confidence":           result.confidence,
        "entities":             result.entities,
        "handoff":              handoff,
        "needs_clarification":  result.needs_clarification,
        "clarification_prompt": clarification_prompt,
        "reason":               result.reason,
    }


def _build_clarification(session: Dict[str, Any]) -> str:
    intent  = session.get("intent", "")
    entities = session.get("entities", {})

    if intent and intent != "general_faq":
        missing = []
        if not entities.get("preferred_date"):
            missing.append("the date you have in mind")
        if not entities.get("participants"):
            missing.append("how many people will be joining")
        if missing:
            return f"Just to confirm — could you share {' and '.join(missing)}?"
        return f"Just to confirm — did you want to go ahead and book a {intent.replace('_', ' ')}?"

    return "I want to make sure I help you with the right thing — could you tell me a bit more about what you're looking for?"
