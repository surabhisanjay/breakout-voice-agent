"""slot_filler.py — turn-by-turn slot collection for the booking agent.

The SlotFiller sits between intent detection and the booking workflow.
When an intent is confirmed but required fields are missing, it:
  1. Asks for one slot at a time (in priority order).
  2. On the next turn, interprets the reply as an answer to THAT slot —
     bypassing the intent classifier entirely.
  3. Resolves relative dates ("tomorrow", "this saturday", "next week")
     into ISO-8601 strings before storing.
  4. Fires `on_complete` with a fully populated handoff once all required
     slots are filled.

Usage (drop into your existing session/router loop):

    filler = SlotFiller()
    result = filler.advance(message, session)

    if result["state"] == "collecting":
        reply_to_customer(result["prompt"])
    elif result["state"] == "complete":
        run_booking_workflow(result["handoff"])
    elif result["state"] == "idle":
        pass  # no active collection, route normally
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Relative-date resolver
# ---------------------------------------------------------------------------

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

def resolve_date(text: str, today: Optional[date] = None) -> Optional[str]:
    """Return ISO-8601 date string from a natural-language fragment, or None."""
    today = today or date.today()
    t = text.lower().strip()

    # Exact keywords
    if t in ("today",):
        return today.isoformat()
    if t in ("tomorrow", "tmrw", "tmr", "tom"):
        return (today + timedelta(days=1)).isoformat()
    if t in ("day after tomorrow", "day after"):
        return (today + timedelta(days=2)).isoformat()

    # "this saturday", "next friday", etc.
    m = re.search(r"\b(this|next)?\s*(" + "|".join(_WEEKDAYS) + r")\b", t)
    if m:
        qualifier, weekday_name = m.group(1), m.group(2)
        target_wd = _WEEKDAYS[weekday_name]
        delta = (target_wd - today.weekday()) % 7
        if delta == 0:
            delta = 7  # "this Monday" when today is Monday → next Monday
        if qualifier == "next":
            delta += 7
        return (today + timedelta(days=delta)).isoformat()

    # "in N days/weeks"
    m = re.search(r"\bin\s+(\d+)\s+(day|week)s?\b", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = n * (7 if unit == "week" else 1)
        return (today + timedelta(days=delta)).isoformat()

    # Numeric patterns: DD/MM, DD-MM, DD/MM/YYYY
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", t)
    if m:
        d_, mo, yr = int(m.group(1)), int(m.group(2)), m.group(3)
        year = int(yr) if yr else today.year
        if year < 100:
            year += 2000
        try:
            return date(year, mo, d_).isoformat()
        except ValueError:
            pass

    # Month-name patterns: "15 August", "Aug 15", "15th Aug 2026"
    month_names = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    m = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+("
        + "|".join(month_names)
        + r")[a-z]*(?:\s+(\d{4}))?\b",
        t,
    )
    if not m:
        m = re.search(
            r"\b("
            + "|".join(month_names)
            + r")[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?\b",
            t,
        )
        if m:
            mon_str, day_str, yr_str = m.group(1), m.group(2), m.group(3)
        else:
            mon_str = day_str = yr_str = None
    else:
        day_str, mon_str, yr_str = m.group(1), m.group(2), m.group(3)

    if mon_str and day_str:
        mo  = month_names[mon_str[:3].lower()]
        d_  = int(day_str)
        yr  = int(yr_str) if yr_str else today.year
        try:
            resolved = date(yr, mo, d_)
            # If the date has already passed this year, assume next year
            if resolved < today and not yr_str:
                resolved = date(yr + 1, mo, d_)
            return resolved.isoformat()
        except ValueError:
            pass

    return None


# ---------------------------------------------------------------------------
# Slot definitions per intent
# ---------------------------------------------------------------------------

# Each slot: (key, question_to_ask, required)
_SLOT_SCHEMAS: Dict[str, List[Tuple[str, str, bool]]] = {
    "birthday_party": [
        ("preferred_date", "What date are you thinking? 🎂", True),
        ("participants",   "How many people will be joining?", True),
        ("customer_name",  "Great! May I have your name?", True),
        ("phone",          "And your phone number?", True),
    ],
    "bachelor_party": [
        ("preferred_date", "When is the big day? 🎉", True),
        ("participants",   "How many in the group?", True),
        ("customer_name",  "Your name, please?", True),
        ("phone",          "Best number to reach you?", True),
    ],
    "farewell_party": [
        ("preferred_date", "What date works for the farewell?", True),
        ("participants",   "How many guests are expected?", True),
        ("customer_name",  "Your name?", True),
        ("phone",          "Your phone number?", True),
    ],
    "couple_event": [
        ("preferred_date", "What date did you have in mind? 💑", True),
        ("customer_name",  "May I know your name?", True),
        ("phone",          "Your contact number?", True),
    ],
    "corporate_event": [
        ("preferred_date", "What date suits your team?", True),
        ("participants",   "Roughly how many employees?", True),
        ("customer_name",  "Your name and company?", True),
        ("phone",          "Best number to reach you?", True),
    ],
    "virtual_event": [
        ("preferred_date", "What date are you looking at?", True),
        ("participants",   "How many participants online?", True),
        ("customer_name",  "Your name?", True),
        ("phone",          "Your phone number?", True),
    ],
    "escape_room_inquiry": [
        ("preferred_date", "When would you like to visit?", True),
        ("participants",   "How many players?", True),
        ("customer_name",  "Your name?", True),
        ("phone",          "Your contact number?", True),
    ],
}


# ---------------------------------------------------------------------------
# Slot parsers — convert a raw reply into a typed value
# ---------------------------------------------------------------------------

_PHONE_RE = re.compile(r"\b[6-9]\d{9}\b")
_COUNT_RE  = re.compile(
    r"\b(\d+)\s*(?:people|persons?|guests?|of us|pax|players?|heads?|members?)?\b",
    re.IGNORECASE,
)


def _parse_slot(key: str, text: str) -> Optional[Any]:
    """Attempt to parse `text` as a value for `key`. Returns None on failure."""
    t = text.strip()

    if key == "preferred_date":
        return resolve_date(t)

    if key == "participants":
        m = _COUNT_RE.search(t)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 500:   # sanity bounds
                return n
        return None

    if key == "phone":
        m = _PHONE_RE.search(t)
        return m.group(0) if m else None

    if key == "customer_name":
        # Accept anything 2–50 chars that looks like a name
        clean = re.sub(r"[^a-zA-Z\s]", "", t).strip()
        if 2 <= len(clean) <= 50:
            return clean.title()
        return None

    # Generic fallback — accept non-empty strings
    return t if t else None


# ---------------------------------------------------------------------------
# SlotFiller
# ---------------------------------------------------------------------------

class SlotFiller:
    """Manages multi-turn slot collection for a single booking conversation."""

    def advance(self, message: str, session: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process one customer turn within the slot-filling flow.

        Expects `session` to contain:
          - "intent":          the confirmed booking intent
          - "slot_filling":    internal collection state (managed here)
          - "entities":        accumulated slot values

        Returns:
          {
            "state":   "idle" | "collecting" | "complete",
            "prompt":  str | None,   # next question to ask the customer
            "handoff": dict | None,  # populated when state == "complete"
            "slot_filled": str | None,  # which slot was just filled
          }
        """
        intent = session.get("intent", "")
        schema = _SLOT_SCHEMAS.get(intent)

        # No schema for this intent → not our concern
        if not schema:
            return {"state": "idle", "prompt": None, "handoff": None, "slot_filled": None}

        entities: Dict[str, Any] = session.setdefault("entities", {})
        sf_state: Dict[str, Any] = session.setdefault("slot_filling", {})

        # ---- Try to fill the slot we last asked about ----
        waiting_for = sf_state.get("waiting_for")
        slot_filled = None

        if waiting_for and message.strip():
            value = _parse_slot(waiting_for, message)
            if value is not None:
                entities[waiting_for] = value
                sf_state["waiting_for"] = None
                slot_filled = waiting_for
            else:
                # Couldn't parse — ask again with a gentle nudge
                prompt = self._reprompt(waiting_for, message)
                return {"state": "collecting", "prompt": prompt, "handoff": None, "slot_filled": None}

        # ---- Find the next unfilled required slot ----
        for key, question, required in schema:
            if required and key not in entities:
                sf_state["waiting_for"] = key
                return {"state": "collecting", "prompt": question, "handoff": None, "slot_filled": slot_filled}

        # ---- All required slots filled ----
        sf_state["waiting_for"] = None
        handoff = {
            "intent":          intent,
            "customer_name":   entities.get("customer_name", ""),
            "phone":           entities.get("phone", ""),
            "participants":    entities.get("participants", 1),
            "preferred_date":  entities.get("preferred_date", ""),
        }
        return {"state": "complete", "prompt": None, "handoff": handoff, "slot_filled": slot_filled}

    @staticmethod
    def _reprompt(key: str, bad_input: str) -> str:
        hints = {
            "preferred_date":  "I didn't quite catch the date — could you say something like \"tomorrow\", \"15 August\", or \"next Saturday\"?",
            "participants":    "Just a number works — how many people will be joining?",
            "phone":           "Please share a 10-digit mobile number.",
            "customer_name":   "Could you share your name?",
        }
        return hints.get(key, f"Sorry, I didn't get that. Could you try again?")


# ---------------------------------------------------------------------------
# Integrated router (ties SlotFiller + IntentDetector together)
# ---------------------------------------------------------------------------

from intent_detector import IntentDetector, CONF_PRESERVED   # type: ignore

_BOOKING_INTENTS = {
    "birthday_party", "bachelor_party", "farewell_party",
    "couple_event", "corporate_event", "virtual_event", "escape_room_inquiry",
}

def handle_turn(message: str, session: Dict[str, Any]) -> Dict[str, Any]:
    """
    Single entry point for every customer turn.

    Returns:
      {
        "reply_type":  "question" | "booking_ready" | "faq" | "confirm_offer" | "deny_ack",
        "prompt":      str | None,
        "handoff":     dict | None,
        "debug": { intent, confidence, reason, entities }
      }
    """
    filler   = SlotFiller()
    detector = IntentDetector()

    # 1. If we're mid-collection, route the message to the slot filler first
    if session.get("slot_filling", {}).get("waiting_for"):
        result = filler.advance(message, session)
        if result["state"] == "collecting":
            return {"reply_type": "question", "prompt": result["prompt"], "handoff": None, "debug": {}}
        if result["state"] == "complete":
            return {"reply_type": "booking_ready", "prompt": None, "handoff": result["handoff"], "debug": {}}

    # 2. Handle confirm / deny of a pending booking offer
    pending_offer = session.get("pending_booking_offer")
    ir = detector.detect(message, session.get("intent", ""), pending_offer)

    session["intent"]  = ir.intent
    session["entities"] = {**session.get("entities", {}), **ir.entities}

    debug = {"intent": ir.intent, "confidence": ir.confidence, "reason": ir.reason, "entities": ir.entities}

    if ir.intent == "confirm_booking":
        session.pop("pending_booking_offer", None)
        result = filler.advance("", session)   # trigger slot collection if needed
        if result["state"] == "collecting":
            return {"reply_type": "question", "prompt": result["prompt"], "handoff": None, "debug": debug}
        if result["state"] == "complete":
            return {"reply_type": "booking_ready", "prompt": None, "handoff": result["handoff"], "debug": debug}

    if ir.intent == "deny_booking":
        session.pop("pending_booking_offer", None)
        return {"reply_type": "deny_ack", "prompt": "No problem! Let me know if there's anything else I can help with.", "handoff": None, "debug": debug}

    # 3. New booking intent — start slot collection
    if ir.intent in _BOOKING_INTENTS and ir.confidence >= CONF_PRESERVED:
        result = filler.advance("", session)
        if result["state"] == "collecting":
            return {"reply_type": "question", "prompt": result["prompt"], "handoff": None, "debug": debug}

    # 4. Needs clarification
    if ir.needs_clarification:
        from intent_detector import _build_clarification  # type: ignore
        return {"reply_type": "question", "prompt": _build_clarification(session), "handoff": None, "debug": debug}

    # 5. FAQ or unhandled
    return {"reply_type": "faq", "prompt": None, "handoff": None, "debug": debug}
