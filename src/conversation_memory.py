from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_MEMORY = {
    "customer_name": "",
    "phone": "",
    "location": "",
    "participants": "",
    "age_group": "",
    "age_detail": "",
    "experience_level": "",
    "challenge_preference": "",
    "company_size": "",
    "event_type": "",
    "preferred_date": "",
    "food_required": "",
    "budget_range": "",
    "room": "",
    "intent": "",
    "sentiment": "neutral",
    "recommended_option": "",
    # ------------------------------------------------------------------ #
    # Workflow state — tracks which mode the agent is currently in.       #
    # Values: "general" | "qualification" | "awaiting_booking" | "booking"#
    # ------------------------------------------------------------------ #
    "current_workflow": "general",
    "conversation_mode": "sales",
    "booking_consent_pending": False,
    "conversation": [],
    "discussed_options": [],
    "customer_preferences": [],
    "concerns": [],
}


class ConversationMemory:
    LOCATIONS = ("Koramangala", "Whitefield", "JP Nagar")
    FLOW_FIELDS = {
        "escape_room_inquiry": ["participants", "location", "age_group"],
        "birthday_party": ["location", "participants", "preferred_date"],
        "corporate_event": ["location", "company_size", "preferred_date"],
        "bachelor_party": ["participants", "location", "preferred_date"],
        "farewell_party": ["participants", "location", "preferred_date"],
        "couple_event": ["participants", "location", "preferred_date"],
        "virtual_event": ["participants", "preferred_date"],
        "cancellation_request": ["customer_name", "phone"],
        "general_faq": [],
    }
    CONTACT_FIELDS = ["customer_name", "phone"]

    EVENT_BY_INTENT = {
        "escape_room_inquiry": "Escape Room",
        "birthday_party": "Birthday Party",
        "bachelor_party": "Bachelor Party",
        "farewell_party": "Farewell Party",
        "couple_event": "Couple Event",
        "corporate_event": "Corporate Event",
        "virtual_event": "Virtual Event",
        "cancellation_request": "Cancellation Request",
        "general_faq": "",
    }

    def __init__(self, session_path: str | Path):
        self.session_path = Path(session_path)
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.session_path.exists():
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            self.session_path.write_text(json.dumps(DEFAULT_MEMORY, indent=2), encoding="utf-8")
            return copy.deepcopy(DEFAULT_MEMORY)
        try:
            loaded = json.loads(self.session_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            loaded = {}
        merged = copy.deepcopy(DEFAULT_MEMORY)
        merged.update(loaded)
        if not isinstance(merged.get("conversation"), list):
            merged["conversation"] = []
        for key in ["discussed_options", "customer_preferences", "concerns"]:
            if not isinstance(merged.get(key), list):
                merged[key] = []
        return merged

    def reset(self) -> None:
        self.data = copy.deepcopy(DEFAULT_MEMORY)
        self.save()

    def save(self) -> None:
        self.session_path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def reset_booking_fields(self) -> None:
        for field in [
            "location",
            "participants",
            "age_group",
            "experience_level",
            "company_size",
            "event_type",
            "preferred_date",
            "food_required",
            "budget_range",
            "room",
            "intent",
            "recommended_option",
            "sentiment",
        ]:
            self.data[field] = DEFAULT_MEMORY[field]
        self.save()

    def update_from_message(self, message: str, intent: str, recommendation: str = "") -> None:
        self.merge_message(message, intent, recommendation)

    def merge_message(self, message: str, intent: str, recommendation: str = "") -> dict[str, Any]:
        text = self.normalize_number_words(message.strip())
        lowered = text.lower()
        extracted: dict[str, Any] = {}

        if intent:
            self.data["intent"] = intent
            extracted["intent"] = intent
            event_type = self.EVENT_BY_INTENT.get(intent, "")
            if event_type:
                self.data["event_type"] = event_type
                extracted["event_type"] = event_type

        if recommendation:
            self.data["recommended_option"] = recommendation
            extracted["recommended_option"] = recommendation

        name = self._extract_name(text)
        if name:
            self.data["customer_name"] = name
            extracted["customer_name"] = name

        phone = self._extract_phone(text)
        if phone:
            self.data["phone"] = phone
            extracted["phone"] = phone

        location = self._extract_location(lowered)
        if location:
            self.data["location"] = location
            extracted["location"] = location

        room = self._extract_room(lowered)
        if room:
            self.data["room"] = room
            extracted["room"] = room

        participants = self._extract_participants(lowered)
        if participants:
            self.data["participants"] = participants
            extracted["participants"] = participants

        age_group, age_detail = self._extract_age_group(lowered)
        if age_group:
            self.data["age_group"] = age_group
            extracted["age_group"] = age_group
        if age_detail:
            self.data["age_detail"] = age_detail
            extracted["age_detail"] = age_detail

        experience_level = self._extract_experience_level(lowered)
        if experience_level:
            self.data["experience_level"] = experience_level
            extracted["experience_level"] = experience_level

        challenge_preference = self._extract_challenge_preference(lowered)
        if challenge_preference:
            self.data["challenge_preference"] = challenge_preference
            extracted["challenge_preference"] = challenge_preference

        company_size = self._extract_company_size(lowered)
        if company_size:
            self.data["company_size"] = company_size
            extracted["company_size"] = company_size

        preferred_date = self._extract_preferred_date(text)
        if preferred_date:
            self.data["preferred_date"] = preferred_date
            extracted["preferred_date"] = preferred_date

        food_required = self._extract_food_required(lowered)
        if food_required:
            self.data["food_required"] = food_required
            extracted["food_required"] = food_required

        budget_range = self._extract_budget_range(text)
        if budget_range:
            self.data["budget_range"] = budget_range
            extracted["budget_range"] = budget_range

        self.data["sentiment"] = self._detect_sentiment(lowered)
        extracted["sentiment"] = self.data["sentiment"]
        self.save()
        return extracted

    def add_turn(self, role: str, content: str) -> None:
        self.data.setdefault("conversation", []).append({"role": role, "content": content})
        self.data["conversation"] = self.data["conversation"][-30:]
        self.save()

    def missing_fields(self, intent: str | None = None, include_contact: bool = False) -> list[str]:
        active_intent = intent or self.data.get("intent", "")
        required = list(self.FLOW_FIELDS.get(active_intent, []))
        if include_contact and active_intent not in ("", "general_faq"):
            required.extend(field for field in self.CONTACT_FIELDS if field not in required)
        return [field for field in required if not self.data.get(field)]

    def flow_complete(self, intent: str | None = None) -> bool:
        return not self.missing_fields(intent=intent, include_contact=False)

    def handoff_ready(self, intent: str | None = None) -> bool:
        active_intent = intent or self.data.get("intent", "")
        if active_intent in ("", "general_faq"):
            return False
        if active_intent == "escape_room_inquiry":
            if not self.data.get("preferred_date") or not self.data.get("recommended_option"):
                return False
        return not self.missing_fields(intent=active_intent, include_contact=True)

    def as_prompt_context(self) -> str:
        fields = {key: value for key, value in self.data.items() if key != "conversation"}
        recent = self.data.get("conversation", [])[-8:]
        return json.dumps({"fields": fields, "recent_conversation": recent}, indent=2)

    def as_state(self) -> dict:
        """
        Return the full memory snapshot as a plain dict.

        This is the LangGraph-ready state output: when each agent becomes
        a LangGraph node, its output state is exactly this dict.
        No framework dependency is introduced — this is just a clean copy.
        """
        return dict(self.data)

    def from_state(self, state: dict) -> None:
        """
        Load memory from a LangGraph node state dict.

        When the graph passes state from one node to the next, the receiving
        agent calls `memory.from_state(state)` to restore its context.
        The underlying JSON file is updated so all agents share the same
        ground-truth state.
        """
        merged = dict(DEFAULT_MEMORY)
        merged.update(state)
        if not isinstance(merged.get("conversation"), list):
            merged["conversation"] = []
        self.data = merged
        self.save()

    @staticmethod
    def normalize_number_words(text: str) -> str:
        number_words = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
            "eleven": 11,
            "twelve": 12,
            "thirteen": 13,
            "fourteen": 14,
            "fifteen": 15,
            "sixteen": 16,
            "seventeen": 17,
            "eighteen": 18,
            "nineteen": 19,
            "twenty": 20,
        }

        def replace(match: re.Match) -> str:
            word = match.group(0).lower()
            return str(number_words[word])

        pattern = r"\b(" + "|".join(number_words.keys()) + r")\b"
        return re.sub(pattern, replace, text, flags=re.IGNORECASE)

    @staticmethod
    def _extract_name(text: str) -> str:
        patterns = [
            r"\bmy name is\s+([A-Za-z][A-Za-z ]{1,40})",
            r"\bi am\s+([A-Za-z][A-Za-z ]{1,40})",
            r"\bi'm\s+([A-Za-z][A-Za-z ]{1,40})",
            r"\bthis is\s+([A-Za-z][A-Za-z ]{1,40})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return " ".join(match.group(1).split()).title()
        return ""

    @staticmethod
    def _extract_phone(text: str) -> str:
        match = re.search(r"(?:(?:\+91[\s-]?)|0)?([6-9]\d{9})\b", text.replace(" ", ""))
        return match.group(1) if match else ""

    @classmethod
    def _extract_location(cls, lowered: str) -> str:
        # Exact match first
        for location in cls.LOCATIONS:
            if location.lower() in lowered:
                return location
        if "jp" in lowered and "nagar" in lowered:
            return "JP Nagar"
        # Fuzzy fallback for Whisper transcription errors
        return cls._extract_location_fuzzy(lowered)

    @classmethod
    def _extract_location_fuzzy(cls, lowered: str) -> str:
        """
        Uses difflib to recover Whisper mishearings such as:
          JP Nuggets   -> JP Nagar
          White Shield -> Whitefield
          Wide Field   -> Whitefield
          Koramangla   -> Koramangala
        Returns '' when confidence is too low (caller should ask to confirm).
        """
        import difflib

        # Normalise the transcript to remove noise words
        _STOP = {"i", "am", "we", "are", "the", "at", "in", "to", "for", "a", "an",
                 "want", "going", "visiting", "location", "prefer", "near", "would",
                 "like", "please", "choose", "selecting"}
        words = [w for w in re.findall(r"[a-z]+", lowered) if w not in _STOP]
        candidate = " ".join(words)

        targets = [loc.lower() for loc in cls.LOCATIONS]

        # Try the full candidate string first
        matches = difflib.get_close_matches(candidate, targets, n=1, cutoff=0.55)
        if matches:
            idx = targets.index(matches[0])
            return cls.LOCATIONS[idx]

        # Try individual words in the candidate (handles "jp nuggets" -> "jp nagar")
        for word in words:
            matches = difflib.get_close_matches(word, targets, n=1, cutoff=0.65)
            if matches:
                idx = targets.index(matches[0])
                return cls.LOCATIONS[idx]

        # Known Whisper-specific substitution table (high-confidence hard-codes)
        _KNOWN: dict[str, str] = {
            "jp nuggets": "JP Nagar",
            "jp nagger": "JP Nagar",
            "white shield": "Whitefield",
            "wide field": "Whitefield",
            "whitfield": "Whitefield",
            "whitefeild": "Whitefield",
            "koramangla": "Koramangala",
            "koramangal": "Koramangala",
            "koramangalar": "Koramangala",
        }
        for phrase, canonical in _KNOWN.items():
            if phrase in lowered:
                return canonical

        return ""

    @staticmethod
    def _extract_room(lowered: str) -> str:
        rooms = [
            "murder mystery",
            "hostage",
            "curse of the pharaoh",
            "classified",
            "undercover",
            "the wizarding championship",
            "the forbidden forest",
            "bomb defusal",
            "prison break",
            "zodiac",
        ]
        for room in rooms:
            if room in lowered:
                return room.title()
        return ""

    @staticmethod
    def _extract_participants(lowered: str) -> int | str:
        if re.search(r"\b(these|those|both|either)\s+\d{1,2}\b", lowered):
            return ""
        if re.search(r"\ball\s+(are|of us are)\s+\d{1,2}\s*(plus|\+)\b", lowered):
            return ""

        # Check for compound group: "2 kids and 4 adults", "4 adults and 2 kids", etc.
        type_pattern = r"(\d{1,4})\s*(people|persons|guests|kids|children|adults|participants|players|members|friends|employees|colleagues|guests)"
        compound_matches = re.findall(type_pattern, lowered)
        if len(compound_matches) >= 2:
            try:
                total = sum(int(count) for count, _ in compound_matches)
                return total
            except ValueError:
                pass

        # Standard patterns
        patterns = [
            r"\b(\d{1,4})\s*(people|persons|guests|kids|children|adults|participants|players|members|friends|employees|colleagues)\b",
            r"\bgroup of\s+(\d{1,4})\b",
            r"\bfor\s+(\d{1,4})\b",
            r"\bwe are\s+(\d{1,4})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if match:
                return int(match.group(1))

        # Check for "a couple" or "two of us"
        if "couple" in lowered or "two of us" in lowered or "both of us" in lowered:
            return 2

        return ""

    @staticmethod
    def _extract_age_group(lowered: str) -> tuple[str, str]:
        # Check for 18 plus / above 18 / above 20 / above 21 etc. first
        age_patterns = [
            r"\b(\d{1,2})\s*(?:plus|\+)\b",
            r"\b(?:above|over|older than)\s*(\d{1,2})\b",
        ]
        for pattern in age_patterns:
            match = re.search(pattern, lowered)
            if match:
                age_val = int(match.group(1))
                if age_val >= 18:
                    return "adults", f"{age_val}+"

        # Accept range patterns:
        # 10-15
        # 10 to 15
        # age 10-15
        # ages 10 to 15 years
        # 10-15 years

        range_patterns = [
            r"\b(\d{1,2})\s*(?:to|[-–—])\s*(\d{1,2})\b",
            r"\b(?:ages?|age)\s+(\d{1,2})\s*(?:to|[-–—])\s*(\d{1,2})\s*years?\b",
            r"\b(\d{1,2})\s*(?:to|[-–—])\s*(\d{1,2})\s*years?\b",
        ]

        for pattern in range_patterns:
            match = re.search(pattern, lowered)
            if match:
                low = int(match.group(1))
                high = int(match.group(2))
                age_detail = f"{low}-{high}"
                if high <= 8:
                    return "kids", age_detail
                if high <= 17:
                    return "teens", age_detail
                return "adults", age_detail

        patterns = [
            r"\baged?\s+(\d{1,2})\b",
            r"\b(\d{1,2})\s*years?\s*old\b",
            r"\bage\s+(\d{1,2})\b",
        ]

        for pattern in patterns:
            match = re.search(pattern, lowered)
            if match:
                age = int(match.group(1))

                if age <= 8:
                    return "kids", f"{age} years"
                elif age <= 17:
                    return "teens", f"{age} years"
                else:
                    return "adults", f"{age} years"

        if "children" in lowered:
            return "kids", ""

        import difflib

        words = re.findall(r"\b\w+\b", lowered)

        for word in words:
            if difflib.SequenceMatcher(None, word, "kids").ratio() > 0.75:
                return "kids", ""

        plus_match = re.search(
            r"\ball\s+(?:are|of us are)\s+(\d{1,2})\s*(?:plus|\+)\b",
            lowered,
        )

        if plus_match and int(plus_match.group(1)) >= 18:
            return "adults", f"{plus_match.group(1)}+"

        for word in words:
            if difflib.SequenceMatcher(None, word, "adult").ratio() > 0.80:
                return "adults", ""

            if difflib.SequenceMatcher(None, word, "adults").ratio() > 0.75:
                return "adults", ""

        if "teen" in lowered:
            return "teens", ""

        return "", ""

    @staticmethod
    def _extract_experience_level(lowered: str) -> str:
        beginner_terms = (
            "beginner", "first time", "first-time", "never done", "never played",
            "none of us have played", "none of us has played", "new to escape",
        )
        if any(term in lowered for term in beginner_terms):
            return "beginner"
        if "experienced" in lowered or "done escape rooms before" in lowered:
            return "experienced"
        return ""

    @staticmethod
    def _extract_challenge_preference(lowered: str) -> str:
        if any(term in lowered for term in ("beginner-friendly", "beginner friendly", "relaxed", "calmer", "easy", "easier")):
            return "beginner"
        if any(term in lowered for term in ("story-driven", "story driven", "story", "mystery", "investigation")):
            return "story"
        if any(term in lowered for term in ("challenging", "challenge", "hard", "hardest", "intense", "fast-paced", "pressure")):
            return "challenging"
        return ""

    @staticmethod
    def _extract_company_size(lowered: str) -> int | str:
        patterns = [
            r"\bcompany size\s*(?:is|of)?\s*(\d{1,5})\b",
            r"\bteam size\s*(?:is|of)?\s*(\d{1,5})\b",
            r"\b(\d{1,5})\s*(employees|team members|staff)\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if match:
                return int(match.group(1))
        return ""

    @staticmethod
    def _extract_preferred_date(text: str) -> str:
        """
        Recognises voice-natural date phrases:
          18 June / 18th June / 18th of June / June 18 / June 18th
          I am planning for 18th of June
          We are planning for 18 June
          Around 18 June / roughly 18 June
          next Friday / this weekend / today …
        """
        lowered = text.lower()

        # ---- Strip conversational prefixes so the core pattern can match ----
        _PREFIX_RE = re.compile(
            r"^(?:i(?:'m|\s+am)?|we(?:'re|\s+are)?)?"
            r"\s*(?:(?:am|are|was|were)\s+)?"
            r"(?:planning|thinking|looking|considering|aiming|targeting|hoping|going)?"
            r"\s*(?:for|on|around|roughly|about|towards|at|by)?"
            r"\s*",
            re.IGNORECASE,
        )
        stripped = _PREFIX_RE.sub("", lowered).strip()

        relative_terms = [
            "today",
            "tomorrow",
            "this weekend",
            "next weekend",
            "next monday",
            "next tuesday",
            "next wednesday",
            "next thursday",
            "next friday",
            "next saturday",
            "next sunday",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ]
        # Check both original and stripped for relative terms
        for term in relative_terms:
            if term in lowered or term in stripped:
                return term.title()

        # Numeric formats (DD/MM, DD-MM, DD/MM/YYYY …)
        date_match = re.search(r"\b(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b", text)
        if date_match:
            return date_match.group(1)

        # "18th of June" / "18 June" / "18th June" — ordinal + optional "of" + month
        month_match = re.search(
            r"\b(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+"
            r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*)\b",
            lowered,
        )
        if month_match:
            return f"{month_match.group(1)} {month_match.group(2).title()}"

        # Also try on the prefix-stripped version
        month_match2 = re.search(
            r"\b(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+"
            r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*)\b",
            stripped,
        )
        if month_match2:
            return f"{month_match2.group(1)} {month_match2.group(2).title()}"

        # "June 18" / "June 18th"
        month_first_match = re.search(
            r"\b((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*)\s+(\d{1,2})(?:st|nd|rd|th)?\b",
            lowered,
        )
        if month_first_match:
            return f"{month_first_match.group(2)} {month_first_match.group(1).title()}"

        return ""

    @staticmethod
    def _extract_food_required(lowered: str) -> str:
        if lowered.strip(" .!?") in {"yes", "yeah", "yep", "sure"}:
            return True
        if lowered.strip(" .!?") in {"no", "nope"}:
            return False
        if any(phrase in lowered for phrase in ("need food", "food required", "with food", "include food", "food options")):
            return True
        if any(phrase in lowered for phrase in ("no food", "without food", "do not need food", "don't need food")):
            return False
        return ""

    @staticmethod
    def _extract_budget_range(text: str) -> str:
        lowered = text.lower()
        patterns = [
            r"\b(?:budget|range)\s*(?:is|of|around|about)?\s*([A-Za-z0-9₹,\- ]{2,40})",
            r"\b(?:under|below|within)\s*(₹?\s*\d[\d,]*)",
        ]
        for pattern in patterns:
            match = re.search(pattern, lowered, flags=re.IGNORECASE)
            if match:
                return " ".join(match.group(1).strip(" .").split())
        return ""

    @staticmethod
    def _detect_sentiment(lowered: str) -> str:
        negative = ("angry", "bad", "complaint", "upset", "refund", "cancel", "problem", "issue")
        positive = ("great", "excited", "perfect", "good", "awesome", "thanks")
        if any(word in lowered for word in negative):
            return "negative"
        if any(word in lowered for word in positive):
            return "positive"
        return "neutral"
