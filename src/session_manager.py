"""session_manager.py — persistent conversation context for the booking agent.

The core problem this solves:
  Every turn in the failing transcript shows Intent: general_faq (0.45) with
  empty entities. The session is not surviving between turns, so the agent
  can never accumulate location + room + booking intent — it just loops.

This module provides:
  - ConversationSession: holds all state for one customer conversation.
  - ContextAccumulator: extracts and merges entities from any turn, including
    implicit ones ("koramangala" → location, "murder mystery" → room).
  - turn_pipeline(): single entry point that wires context accumulation,
    intent detection, slot filling, and booking handoff in the right order.

What changes vs the old flow
  ─────────────────────────────────────────────────────────────────────────
  Old: route_turn(message, session) called a fresh IntentDetector each time
       and the caller never stored the returned session back anywhere.

  New: turn_pipeline(message, session) mutates session in-place, merges
       every entity extracted from every turn, detects a "latent booking
       intent" from accumulated context even when the classifier fires
       general_faq, and hands off as soon as all required slots are filled.
  ─────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from .intent_detector import IntentDetector, CONF_PRESERVED, _build_clarification
    from .slot_filler import SlotFiller, resolve_date, _BOOKING_INTENTS
except ImportError:  # Support direct execution from the src directory.
    from intent_detector import IntentDetector, CONF_PRESERVED, _build_clarification  # type: ignore
    from slot_filler import SlotFiller, resolve_date, _BOOKING_INTENTS  # type: ignore


# ---------------------------------------------------------------------------
# Known locations and rooms (extend from your actual inventory)
# ---------------------------------------------------------------------------

KNOWN_LOCATIONS = {
    "koramangala", "whitefield", "jp nagar", "indiranagar",
    "marathahalli", "electronic city", "brigade road",
}

KNOWN_ROOMS = {
    "murder mystery", "hostage", "curse of the pharaoh", "classified",
    "undercover", "the wizarding championship", "the forbidden forest",
    "time machine", "zodiac",
}

# Rooms that map to a booking intent
ROOM_TO_INTENT: Dict[str, str] = {
    "murder mystery":              "escape_room_inquiry",
    "hostage":                     "escape_room_inquiry",
    "curse of the pharaoh":        "escape_room_inquiry",
    "classified":                  "escape_room_inquiry",
    "undercover":                  "escape_room_inquiry",
    "the wizarding championship":  "escape_room_inquiry",
    "the forbidden forest":        "escape_room_inquiry",
    "time machine":                "escape_room_inquiry",
    "zodiac":                      "escape_room_inquiry",
}

BOOKING_TRIGGERS = frozenset({
    "book", "book it", "book that", "i want to book", "want to book",
    "let's book", "lets book", "confirm", "reserve", "make a booking",
    "go ahead", "proceed", "yes", "yeah", "sure", "ok", "okay",
})


# ---------------------------------------------------------------------------
# Conversation session
# ---------------------------------------------------------------------------

@dataclass
class ConversationSession:
    """All state for one customer conversation. Persist this between turns."""
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # Intent / slot state
    intent: str = ""
    entities: Dict[str, Any] = field(default_factory=dict)
    slot_filling: Dict[str, Any] = field(default_factory=dict)
    pending_booking_offer: Optional[Dict[str, Any]] = None

    # Conversation-level context (survives intent changes)
    context: Dict[str, Any] = field(default_factory=dict)
    # e.g. context = {"location": "koramangala", "room": "murder mystery"}

    # Full turn log for debugging
    turns: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id":           self.session_id,
            "intent":               self.intent,
            "entities":             self.entities,
            "slot_filling":         self.slot_filling,
            "pending_booking_offer": self.pending_booking_offer,
            "context":              self.context,
        }

    def touch(self) -> None:
        self.updated_at = time.time()


# ---------------------------------------------------------------------------
# Context accumulator
# ---------------------------------------------------------------------------

class ContextAccumulator:
    """Extract implicit entities from any turn and merge into session context."""

    def accumulate(self, message: str, session: ConversationSession) -> Dict[str, str]:
        """Return dict of newly extracted context keys."""
        text = message.lower().strip()
        extracted: Dict[str, str] = {}

        # Location
        for loc in KNOWN_LOCATIONS:
            if loc in text:
                extracted["location"] = loc
                break

        # Room
        for room in KNOWN_ROOMS:
            if room in text:
                extracted["room"] = room
                break

        # Date
        from slot_filler import resolve_date  # type: ignore
        date_val = resolve_date(text)
        if date_val:
            extracted["preferred_date"] = date_val

        # Booking trigger
        if any(trigger in text for trigger in BOOKING_TRIGGERS):
            extracted["booking_requested"] = "true"

        # Merge into session context (don't overwrite with empty)
        for k, v in extracted.items():
            if v:
                session.context[k] = v

        # Also promote context into entities (slot filler reads entities)
        for k in ("preferred_date", "location", "room"):
            if k in session.context and k not in session.entities:
                session.entities[k] = session.context[k]

        return extracted


# ---------------------------------------------------------------------------
# Intent resolver — uses accumulated context to detect latent booking intent
# ---------------------------------------------------------------------------

def _resolve_intent(message: str, session: ConversationSession) -> str:
    """
    Return the effective intent for this turn.

    Priority:
    1. Booking trigger word + known room in context → escape_room_inquiry
    2. Booking trigger word + any booking intent already set → keep it
    3. IntentDetector result (if confidence is adequate)
    4. Carry forward previous intent
    """
    text = message.lower().strip()
    ctx  = session.context

    # Has the customer said something like "book" / "book it"?
    booking_trigger = any(t in text for t in BOOKING_TRIGGERS)

    if booking_trigger:
        # If we know the room, map it to an intent
        room = ctx.get("room")
        if room and room in ROOM_TO_INTENT:
            return ROOM_TO_INTENT[room]
        # If there's already a booking-class intent active, keep it
        if session.intent in _BOOKING_INTENTS:
            return session.intent
        # Generic booking with location but no room → still needs room selection
        if ctx.get("location"):
            return "escape_room_inquiry"

    # Run the classifier
    detector = IntentDetector()
    ir = detector.detect(message, session.intent, session.pending_booking_offer)

    if ir.confidence >= CONF_PRESERVED:
        return ir.intent

    # Fall back to active intent rather than general_faq
    return session.intent or ir.intent


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def turn_pipeline(message: str, session: ConversationSession) -> Dict[str, Any]:
    """
    Process one customer turn end-to-end.

    Mutates `session` in-place. Returns a response dict:
    {
        "reply_type":  "question" | "booking_ready" | "faq" | "deny_ack" | "error",
        "prompt":      str | None,
        "handoff":     dict | None,
        "debug": {
            "intent", "confidence", "context", "entities", "slot_waiting_for"
        }
    }
    """
    session.touch()

    # 1. Accumulate context from this turn
    accumulator = ContextAccumulator()
    accumulator.accumulate(message, session)

    # 2. Resolve intent using full context
    effective_intent = _resolve_intent(message, session)
    session.intent   = effective_intent

    debug = {
        "intent":           session.intent,
        "context":          dict(session.context),
        "entities":         dict(session.entities),
        "slot_waiting_for": session.slot_filling.get("waiting_for"),
    }

    # 3. If mid-collection, route to slot filler
    sf = SlotFiller()
    session_dict = session.to_dict()  # SlotFiller works on plain dicts

    if session.slot_filling.get("waiting_for"):
        result = sf.advance(message, session_dict)
        _sync_back(session, session_dict)
        return _package(result, debug)

    # 4. Handle confirm / deny
    if effective_intent == "deny_booking":
        session.pending_booking_offer = None
        return {
            "reply_type": "deny_ack",
            "prompt": "No problem at all! Let me know if you'd like to explore other options.",
            "handoff": None,
            "debug": debug,
        }

    # 5. Start or continue slot collection for booking intents
    if effective_intent in _BOOKING_INTENTS:
        # Pre-fill slots from accumulated context
        if session.context.get("preferred_date") and "preferred_date" not in session.entities:
            session.entities["preferred_date"] = session.context["preferred_date"]
        if session.context.get("location") and "location" not in session.entities:
            session.entities["location"] = session.context["location"]

        session_dict = session.to_dict()
        result = sf.advance("", session_dict)   # "" → don't try to fill a slot, just get next question
        _sync_back(session, session_dict)

        if result["state"] == "collecting":
            return {"reply_type": "question", "prompt": result["prompt"], "handoff": None, "debug": debug}
        if result["state"] == "complete":
            return {"reply_type": "booking_ready", "prompt": None, "handoff": result["handoff"], "debug": debug}

    # 6. FAQ / unrecognised — but surface context so the UI can show it
    return {"reply_type": "faq", "prompt": None, "handoff": None, "debug": debug}


def _sync_back(session: ConversationSession, d: Dict[str, Any]) -> None:
    """Copy mutable slot_filling and entities back from dict to session object."""
    session.slot_filling = d.get("slot_filling", {})
    session.entities     = d.get("entities", session.entities)


def _package(sf_result: Dict[str, Any], debug: Dict[str, Any]) -> Dict[str, Any]:
    if sf_result["state"] == "collecting":
        return {"reply_type": "question",      "prompt": sf_result["prompt"], "handoff": None,                    "debug": debug}
    if sf_result["state"] == "complete":
        return {"reply_type": "booking_ready", "prompt": None,                "handoff": sf_result["handoff"],    "debug": debug}
    return     {"reply_type": "faq",           "prompt": None,                "handoff": None,                    "debug": debug}


# ---------------------------------------------------------------------------
# Demo — replays the failing transcript
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    session = ConversationSession()

    turns = [
        "I want to book",
        "koramangala",
        "murder mystery",
        "Book it",
        # --- if the above looped, these would be reached: ---
        "koramangala",
        "murder mystery",
        "book",
    ]

    for msg in turns:
        print(f"\nCustomer: {msg}")
        response = turn_pipeline(msg, session)
        reply    = response.get("prompt") or f"[{response['reply_type']}]"
        print(f"Agent:    {reply}")
        d = response["debug"]
        print(f"  intent={d['intent']}  context={d['context']}  waiting_for={d['slot_waiting_for']}")
