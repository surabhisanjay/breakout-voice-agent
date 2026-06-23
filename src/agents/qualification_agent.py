from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import Any

from ..memory.conversation_memory import ConversationMemory


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QualificationResult:
    qualified: bool
    missing_fields: list[str]
    next_question: str
    summary: dict[str, Any]
    response: str = ""


class QualificationAgent:
    REQUIRED_BY_INTENT = {
        "escape_room_inquiry": ["event_type", "participants", "age_group", "location"],
        "birthday_party": [
            "event_type", "participants", "location", "preferred_date",
            "age_group", "food_required", "budget_range",
            "customer_name", "phone",
        ],
        "corporate_event": [
            "event_type", "participants", "location", "preferred_date",
            "food_required", "budget_range",
            "customer_name", "phone",
        ],
        "bachelor_party": [
            "event_type", "participants", "location", "preferred_date",
            "food_required", "budget_range",
            "customer_name", "phone",
        ],
        "farewell_party": [
            "event_type", "participants", "location", "preferred_date",
            "food_required", "budget_range",
            "customer_name", "phone",
        ],
        "couple_event": ["event_type", "participants", "location", "preferred_date",
                         "customer_name", "phone"],
        "virtual_event": ["event_type", "participants", "preferred_date",
                          "customer_name", "phone"],
        "cancellation_request": ["event_type"],
        "general_faq": [],
    }

    QUESTIONS = {
        "event_type": "Sure. What kind of event are you planning?",
        "participants": "Got it. How many people are joining?",
        "location": "Nice. Which location works best: Koramangala, Whitefield, or JP Nagar?",
        "preferred_date": "Got it. What date are you planning for?",
        "age_group": "Perfect. What's the age group: adults, kids, or a mix?",
        "food_required": "Sounds good. Do you need food and beverages as well?",
        "budget_range": "Got it. What's the budget range: Basic, Standard, or Premium?",
        "customer_name": "Perfect. What's your name?",
        "phone": "Thanks. What's the best phone number for the booking details?",
        "email": "Perfect. What's your email address?",
    }

    # Warm acknowledgment spoken before the next question
    _ACK_PREFIXES: dict[str, str] = {
        "location": "Perfect.",
        "preferred_date": "Nice.",
        "preferred_time": "Got it.",
        "customer_name": "",          # filled dynamically using the name
        "phone": "Perfect.",
        "food_required": "Got it.",
        "budget_range": "Sounds good.",
        "participants": "Nice.",
        "age_group": "Perfect.",
        "email": "Got it.",
    }

    def __init__(self, memory: ConversationMemory):
        self.memory = memory
        # Tracks which field we explicitly asked for on the previous turn.
        # Validation only applies when _waiting_for matches the expected field.
        self._waiting_for: str = ""

    def next_missing_field(self, intent: str) -> str:
        required = self.REQUIRED_BY_INTENT.get(intent, [])
        missing = [field for field in required if not self._has_value(field)]
        return missing[0] if missing else ""

    def qualify(self, intent: str | None = None) -> QualificationResult:
        active_intent = intent or self.memory.data.get("intent", "general_faq")
        required = self.REQUIRED_BY_INTENT.get(active_intent, [])
        missing = [field for field in required if not self._has_value(field)]
        if missing:
            next_question = self.QUESTIONS.get(missing[0], "")
        else:
            next_question = "Qualification complete."

        response = next_question
        return QualificationResult(
            qualified=not missing,
            missing_fields=missing,
            next_question=next_question,
            summary=self._summary(not missing),
            response=response,
        )

    def update_and_qualify(self, message: str, intent: str | None = None) -> QualificationResult:
        active_intent = intent or self.memory.data.get("intent", "general_faq")
        current = self.qualify(active_intent)
        expected = self._waiting_for or (current.missing_fields[0] if current.missing_fields else "")

        # Capture the field value explicitly
        self._capture_expected_field(message, expected, active_intent)
        # Also run generic memory extraction (location, participants, date,
        # phone, etc.). A customer can provide a valid field while we are
        # waiting for another one; store it instead of discarding the turn.
        extracted = self.memory.update_from_message(message, active_intent, expected_field=expected)
        self._capture_bare_count(message, active_intent, expected)

        # Re-evaluate
        after = self.qualify(active_intent)

        meaningful_extracted = {
            key: value
            for key, value in extracted.items()
            if key not in {"intent", "event_type", "sentiment"} and value not in ("", None)
        }
        new_field_captured = (
            len(after.missing_fields) < len(current.missing_fields)
            or bool(meaningful_extracted)
        )

        # Only validate/reject the message if we have already explicitly asked
        # this field on the previous turn, and no new field was captured.
        if self._waiting_for == expected and not new_field_captured:
            invalid_response = self._invalid_response_for_expected_field(message, expected)
            if invalid_response:
                # Don't advance; keep _waiting_for the same field
                return QualificationResult(
                    qualified=False,
                    missing_fields=current.missing_fields,
                    next_question=current.next_question,
                    summary=self._summary(False),
                    response=invalid_response,
                )

        # Record which field we are now asking for, so next turn can validate
        self._waiting_for = after.missing_fields[0] if after.missing_fields else ""

        # Build a natural response with acknowledgment
        captured_field = next(iter(meaningful_extracted.keys()), expected) if meaningful_extracted else expected
        response = self._build_response(captured_field, after)

        return QualificationResult(
            qualified=after.qualified,
            missing_fields=after.missing_fields,
            next_question=after.next_question,
            summary=after.summary,
            response=response,
        )

    # ------------------------------------------------------------------ #
    #  Response building                                                   #
    # ------------------------------------------------------------------ #

    def _build_response(self, captured_field: str, after: QualificationResult) -> str:
        """Return a single natural turn: optional ack + next question OR completion."""
        if after.qualified:
            name = str(self.memory.data.get("customer_name", ""))
            if name:
                return f"Perfect. Thank you, {name}. I've captured all the information I need. Someone from our team will reach out to you shortly."
            return "Perfect. I've captured all the information I need. Someone from our team will reach out to you shortly."

        next_question = after.next_question
        ack = self._ack_for_captured_field(captured_field)
        if ack:
            return f"{ack} {next_question}"
        return next_question

    def _ack_for_captured_field(self, field: str) -> str:
        if field == "customer_name":
            name = str(self.memory.data.get("customer_name", ""))
            return f"Thank you, {name}." if name else "Thank you."
        if field == "preferred_date":
            date = str(self.memory.data.get("preferred_date", "")).strip()
            return f"Got it, {date}." if date else "Got it."
        if field == "preferred_time":
            preferred_time = str(self.memory.data.get("preferred_time", "")).strip()
            return f"Got it, {preferred_time}." if preferred_time else "Got it."
        return self._ACK_PREFIXES.get(field, "")

    # ------------------------------------------------------------------ #
    #  Field capture                                                       #
    # ------------------------------------------------------------------ #

    def _capture_expected_field(self, message: str, expected: str, intent: str) -> None:
        normalized = self.memory.normalize_number_words(message).strip()
        lowered = normalized.lower().strip(" .!?")

        if expected == "preferred_date":
            preferred_date = self.memory._extract_preferred_date(normalized)
            if preferred_date:
                self.memory.set_field("preferred_date", preferred_date, message, expected)

        elif expected == "food_required":
            food_required = self.memory._extract_food_required(lowered)
            if food_required != "":
                self.memory.set_field("food_required", food_required, message, expected)

        elif expected == "budget_range":
            canonical = self._extract_budget_canonical(normalized)
            if canonical:
                self.memory.set_field("budget_range", canonical, message, expected)

        elif expected == "participants":
            self._capture_bare_count(normalized, intent, expected)

        elif expected == "location":
            location = self.memory._extract_location(lowered)
            if location:
                self.memory.set_field("location", location, message, expected)
        elif expected == "age_group":
            age_group, age_detail = self.memory._extract_age_group(lowered, allow_bare_range=True)

            if age_group:
                self.memory.set_field("age_group", age_group, message, expected)
                if age_detail:
                    self.memory.set_field("age_detail", age_detail, message, expected)
        elif expected == "customer_name":
            name = self._extract_bare_name(message)
            if name:
                self.memory.set_field("customer_name", name, message, expected)

        elif expected == "phone":
            phone = self.memory._extract_phone(normalized)
            if phone:
                self.memory.set_field("phone", phone, message, expected)
        elif expected == "email":
            email = self.memory._extract_email(normalized)
            if email:
                self.memory.set_field("email", email, message, expected)


    # ------------------------------------------------------------------ #
    #  Validation                                                          #
    # ------------------------------------------------------------------ #

    def _invalid_response_for_expected_field(self, message: str, expected: str) -> str:
        normalized = self.memory.normalize_number_words(message).strip()
        lowered = normalized.lower().strip(" .!?")

        if expected == "preferred_date":
            if not self.memory._extract_preferred_date(normalized):
                return "Sorry, I didn't quite catch the date. Could you say something like 18 June or June 18?"
            return ""

        if expected == "participants":
            if not self._is_valid_count(normalized):
                if self._is_garbage_transcript(lowered):
                    return "Sorry, I didn't catch that. Could you repeat it?"
                return "Sorry, I didn't catch the group size. Could you say the number of people again?"
            return ""

        if expected == "location":
            if not self._is_valid_location(lowered):
                if self._is_garbage_transcript(lowered):
                    return "Sorry, I didn't catch that. Could you repeat it?"
                return "Sorry, which location would you prefer: Koramangala, Whitefield, or JP Nagar?"
            return ""

        if expected == "food_required":
            if self.memory._extract_food_required(lowered) == "":
                if self._is_garbage_transcript(lowered):
                    return "Sorry, I didn't catch that. Could you repeat it?"
                return "Sorry, should I include food and beverages for this event?"
            return ""

        if expected == "budget_range":
            if not self._is_valid_budget(normalized):
                if self._is_garbage_transcript(lowered):
                    return "Sorry, I didn't catch that. Could you repeat it?"
                return "Sorry, I didn't catch the budget. Could you say something like Basic, Standard, or Premium?"
            return ""

        if expected == "customer_name":
            if not self._extract_bare_name(message):
                if self._is_garbage_transcript(lowered):
                    return "Sorry, I didn't catch that. Could you repeat it?"
                return "Sorry, could you share your name again?"
            return ""

        if expected == "phone":
            if not self.memory._extract_phone(normalized):
                if self._is_garbage_transcript(lowered):
                    return "Sorry, I didn't catch that. Could you repeat it?"
                return "Sorry, I didn't catch the phone number. Could you repeat it, like 9876543210?"
            return ""

        if expected == "email":
            if not self.memory._extract_email(normalized):
                if self._is_garbage_transcript(lowered):
                    return "Sorry, I didn't catch that. Could you repeat it?"
                return "Sorry, I didn't catch the email address. Could you say it again?"
            return ""

        if self._is_garbage_transcript(lowered):
            return "Sorry, I didn't catch that. Could you repeat it?"
        return ""

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _capture_bare_count(self, message: str, intent: str, expected_field: str = "") -> None:
        normalized = self.memory.normalize_number_words(message).strip()
        match = re.search(
            r"\b(?:around|about|approximately|approx|roughly|maybe)?\s*(\d{1,5})\b",
            normalized,
            flags=re.IGNORECASE,
        )
        if not match:
            return

        count = int(match.group(1))
        
        # Avoid capturing date days (e.g. "18 June") as participant counts
        if expected_field == "preferred_date" or self.memory._extract_preferred_date(message):
            return

        participants_set = bool(self.memory.data.get("participants"))
        company_size_set = bool(self.memory.data.get("company_size"))
        
        lowered = message.lower()
        has_change = any(ind in lowered for ind in ["now", "instead", "change", "update", "switch", "actually", "modify", "correct"])

        if intent == "corporate_event":
            if not company_size_set or expected_field == "company_size" or has_change:
                self.memory.set_field("company_size", count, message, expected_field)
        if not participants_set or expected_field == "participants" or has_change:
            self.memory.set_field("participants", count, message, expected_field)

    @staticmethod
    def _extract_bare_name(message: str) -> str:
        """
        Accepts:
        - Bare single capitalised word: Siddharth
        - "my name is …", "I am …", "I'm …", "this is …"
        Rejects: numbers, garbage, common short words.
        """
        text = message.strip()
        extracted = ConversationMemory._extract_name(text)
        if extracted:
            return extracted
        # Prefixed patterns first
        prefixed = [
            r"\bmy name is\s+([A-Za-z]+(?:\s+[A-Za-z]+){0,2}?)(?=\s+(?:and\s+)?(?:my\s+)?phone|[,.;]|$)",
            r"\bi am\s+([A-Za-z]+)(?=\s+(?:and\s+)?(?:my\s+)?phone|[,.;]|$)",
            r"\bi'm\s+([A-Za-z]+)(?=\s+(?:and\s+)?(?:my\s+)?phone|[,.;]|$)",
            r"\bthis is\s+([A-Za-z]+)(?=\s+(?:and\s+)?(?:my\s+)?phone|[,.;]|$)",
        ]
        for pattern in prefixed:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                candidate = " ".join(match.group(1).split()).title()
                logger.info("NAME_CANDIDATE=%s", candidate)
                if ConversationMemory._is_plausible_name(candidate):
                    logger.info("NAME_ACCEPTED=%s", candidate)
                    return candidate
                logger.info("NAME_REJECTED=%s", candidate)
                return ""

        # Bare single word — must be all alpha, 2+ chars, not a common noise word
        _NOISE = {
            "yes", "no", "okay", "ok", "sure", "hi", "hello", "hey",
            "thanks", "thank", "great", "perfect", "good", "please", "sorry",
            "we", "i", "me", "my", "your", "the", "and", "see", "you", "then",
            "challenging", "chllangeing", "challenge", "relaxed", "adults", "kids", "hostage",
            "murder", "mystery", "classified", "bomb", "defusal", "prison", "break", "undercover",
            "escape", "room", "rooms", "game", "games"
        }
        clean = re.sub(r"[^a-zA-Z\s]", "", text).strip()
        words = clean.split()
        if len(words) == 1:
            word = words[0]
            candidate = word.title()
            logger.info("NAME_CANDIDATE=%s", candidate)
            if len(word) >= 2 and word.lower() not in _NOISE and word.isalpha() and ConversationMemory._is_plausible_name(candidate):
                logger.info("NAME_ACCEPTED=%s", candidate)
                return candidate
            logger.info("NAME_REJECTED=%s", candidate)
        # Two-word name (First Last)
        if len(words) == 2 and all(w.isalpha() and len(w) >= 2 and w.lower() not in _NOISE for w in words):
            candidate = " ".join(w.title() for w in words)
            logger.info("NAME_CANDIDATE=%s", candidate)
            if ConversationMemory._is_plausible_name(candidate):
                logger.info("NAME_ACCEPTED=%s", candidate)
                return candidate
            logger.info("NAME_REJECTED=%s", candidate)
        return ""

    @staticmethod
    def _is_valid_count(message: str) -> bool:
        return bool(
            re.fullmatch(
                r"(?:around|about|approximately|approx|roughly|maybe)?\s*\d{1,5}",
                message.strip().lower(),
            )
            or re.search(
                r"\b\d{1,5}\s*(people|persons|guests|kids|children|adults|participants|players|members|friends|employees|colleagues)\b",
                message.lower(),
            )
        )

    @staticmethod
    def _is_valid_budget(message: str) -> bool:
        """
        Returns True for any utterance that names a recognisable budget tier,
        including natural voice phrasing:
            "Premium" / "Standard" / "Basic"
            "We would like to go to premium options"
            "Premium package" / "premium plan" / "go with standard"
            Numeric ranges: "50000" / "30,000 - 50,000"
        """
        lowered = message.lower().strip(" .!?")
        # Any tier keyword is sufficient
        if re.search(r"\b(premium|standard|basic|regular)\b", lowered):
            return True
        if re.search(r"\b(?:budget|range|under|below|within|around|about)\b", lowered):
            return True
        return bool(re.fullmatch(r"₹?\s*\d[\d,]*(?:\s*-\s*₹?\s*\d[\d,]*)?", lowered))

    @staticmethod
    def _extract_budget_canonical(message: str) -> str:
        """
        Extract a clean, canonical budget label from a natural-language utterance.

        Examples:
            "Premium"                            → "premium"
            "we would like to go to premium"     → "premium"
            "premium option"                     → "premium"
            "go with standard plan"              → "standard"
            "basic package"                      → "basic"
            "₹30,000"                            → "₹30,000"
        Returns '' if no recognisable budget is found.
        """
        lowered = message.lower()
        if re.search(r"\bpremium\b", lowered):
            return "premium"
        if re.search(r"\bstandard\b", lowered):
            return "standard"
        if re.search(r"\bbasic\b", lowered):
            return "basic"
        if re.search(r"\bregular\b", lowered):
            return "standard"  # treat 'regular' as synonym for 'standard'
        # Numeric range fallback
        match = re.search(r"₹?\s*\d[\d,]*(?:\s*-\s*₹?\s*\d[\d,]*)?", message)
        if match:
            return match.group(0).strip()
        return ""

    def _is_valid_location(self, lowered: str) -> bool:
        # Exact canonical match
        if (
            lowered in {"koramangala", "whitefield", "jp nagar"}
            or ("jp" in lowered and "nagar" in lowered)
        ):
            return True
        # Fuzzy match — accept Whisper transcription variants
        return bool(self.memory._extract_location(lowered))

    @staticmethod
    def _is_garbage_transcript(lowered: str) -> bool:
        meaningful = re.findall(r"[a-z0-9]+", lowered)
        if len(meaningful) < 2 and not re.fullmatch(r"\d{1,5}", lowered):
            valid_words = {
                "challenging", "standard", "basic", "premium", "story", "beginner",
                "adults", "kids", "teens", "children", "yes", "no", "yeah", "sure",
                "ok", "okay", "y", "n", "whitefield", "koramangala", "jp", "nagar",
                "food", "drinks", "beverages", "none"
            }
            cleaned = "".join(meaningful)
            if cleaned not in valid_words:
                return True
        if re.fullmatch(r"[a-z]\d+", lowered):
            return True
        if lowered in {"ah but its in google", "ah but it's in google"}:
            return True
        return False

    def _has_value(self, field: str) -> bool:
        if field == "participants" and self.memory.data.get("company_size"):
            return True
        val = self.memory.data.get(field)
        if val is False:
            return True
        return bool(val)

    def _summary(self, qualified: bool) -> dict[str, Any]:
        data = self.memory.data
        participants = data.get("participants") or data.get("company_size") or ""
        event_type = str(data.get("event_type", ""))
        normalized_event_type = (
            event_type.lower()
            .replace(" event", "")
            .replace(" party", "")
            .replace(" ", "_")
        )
        return {
            "qualified": qualified,
            "event_type": normalized_event_type,
            "participants": participants,
            "location": data.get("location", ""),
            "preferred_date": data.get("preferred_date", ""),
            "age_group": data.get("age_group", ""),
            "food_required": data.get("food_required", ""),
            "budget_range": data.get("budget_range", ""),
            "customer_name": data.get("customer_name", ""),
            "phone": data.get("phone", ""),
        }
        normalized_event_type = (
            event_type.lower()
            .replace(" event", "")
            .replace(" party", "")
            .replace(" ", "_")
        )
        return {
            "qualified": qualified,
            "event_type": normalized_event_type,
            "participants": participants,
            "location": data.get("location", ""),
            "preferred_date": data.get("preferred_date", ""),
            "age_group": data.get("age_group", ""),
            "food_required": data.get("food_required", ""),
            "budget_range": data.get("budget_range", ""),
            "customer_name": data.get("customer_name", ""),
            "phone": data.get("phone", ""),
        }
