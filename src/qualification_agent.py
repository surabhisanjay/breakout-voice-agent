from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .conversation_memory import ConversationMemory


@dataclass(frozen=True)
class QualificationResult:
    qualified: bool
    missing_fields: list[str]
    next_question: str
    summary: dict[str, Any]
    response: str = ""


class QualificationAgent:
    REQUIRED_BY_INTENT = {
        "escape_room_inquiry": ["event_type", "participants", "location", "age_group"],
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
        "event_type": "Sure. What type of event are you planning?",
        "participants": "How many people will attend?",
        "location": "Which location would you prefer: Koramangala, Whitefield, or JP Nagar?",
        "preferred_date": "What date are you planning for?",
        "age_group": "What is the age group of the players?",
        "food_required": "Would you require food and beverages?",
        "budget_range": "What budget range are you considering? We have Basic, Standard, and Premium options.",
        "customer_name": "May I have your name?",
        "phone": "Could I have your phone number so the team can share the details?",
    }

    # Warm acknowledgment spoken before the next question
    _ACK_PREFIXES: dict[str, str] = {
        "location": "Perfect.",
        "preferred_date": "Great.",
        "customer_name": "",          # filled dynamically using the name
        "phone": "Thank you.",
        "food_required": "Noted.",
        "budget_range": "Got it.",
        "participants": "Got it.",
        "age_group": "Perfect.",
    }

    def __init__(self, memory: ConversationMemory):
        self.memory = memory
        # Tracks which field we explicitly asked for on the previous turn.
        # Validation only applies when _waiting_for matches the expected field.
        self._waiting_for: str = ""

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
        expected = current.missing_fields[0] if current.missing_fields else ""

        # Capture the field value explicitly
        self._capture_expected_field(message, expected, active_intent)
        # Also run generic memory extraction (location, participants, etc.)
        self.memory.update_from_message(message, active_intent)
        self._capture_bare_count(message, active_intent)

        # Re-evaluate
        after = self.qualify(active_intent)

        new_field_captured = len(after.missing_fields) < len(current.missing_fields)

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
        response = self._build_response(expected, after)

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
                self.memory.data["preferred_date"] = preferred_date
                self.memory.save()

        elif expected == "food_required":
            food_required = self.memory._extract_food_required(lowered)
            if food_required != "":
                self.memory.data["food_required"] = food_required
                self.memory.save()

        elif expected == "budget_range" and self._is_valid_budget(normalized):
            self.memory.data["budget_range"] = normalized.strip(" .").lower()
            self.memory.save()

        elif expected == "participants":
            self._capture_bare_count(normalized, intent)

        elif expected == "location":
            # Use full fuzzy extraction (exact + difflib + hard-coded table)
            location = self.memory._extract_location(lowered)
            if location:
                self.memory.data["location"] = location
                self.memory.save()

        elif expected == "customer_name":
            name = self._extract_bare_name(message)
            if name:
                self.memory.data["customer_name"] = name
                self.memory.save()

        elif expected == "phone":
            phone = self.memory._extract_phone(normalized)
            if phone:
                self.memory.data["phone"] = phone
                self.memory.save()

    # ------------------------------------------------------------------ #
    #  Validation                                                          #
    # ------------------------------------------------------------------ #

    def _invalid_response_for_expected_field(self, message: str, expected: str) -> str:
        normalized = self.memory.normalize_number_words(message).strip()
        lowered = normalized.lower().strip(" .!?")

        if expected == "preferred_date":
            if not self.memory._extract_preferred_date(normalized):
                return "Sorry, I didn't understand the date. Could you provide a date like 18 June or June 18?"
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

        if self._is_garbage_transcript(lowered):
            return "Sorry, I didn't catch that. Could you repeat it?"
        return ""

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _capture_bare_count(self, message: str, intent: str) -> None:
        normalized = self.memory.normalize_number_words(message).strip()
        match = re.fullmatch(r"\d{1,5}", normalized)
        if not match:
            return

        count = int(match.group(0))
        if intent == "corporate_event" and not self.memory.data.get("company_size"):
            self.memory.data["company_size"] = count
        if not self.memory.data.get("participants"):
            self.memory.data["participants"] = count
        self.memory.save()

    @staticmethod
    def _extract_bare_name(message: str) -> str:
        """
        Accepts:
        - Bare single capitalised word: Siddharth
        - "my name is …", "I am …", "I'm …", "this is …"
        Rejects: numbers, garbage, common short words.
        """
        text = message.strip()
        # Prefixed patterns first
        prefixed = [
            r"\bmy name is\s+([A-Za-z][A-Za-z ]{1,40})",
            r"\bi am\s+([A-Za-z][A-Za-z ]{1,40})",
            r"\bi'm\s+([A-Za-z][A-Za-z ]{1,40})",
            r"\bthis is\s+([A-Za-z][A-Za-z ]{1,40})",
        ]
        for pattern in prefixed:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return " ".join(match.group(1).split()).title()

        # Bare single word — must be all alpha, 2+ chars, not a common noise word
        _NOISE = {
            "yes", "no", "okay", "ok", "sure", "hi", "hello", "hey",
            "thanks", "thank", "great", "perfect", "good", "please", "sorry",
            "we", "i", "me", "my", "your", "the", "and", "see", "you", "then",
        }
        clean = re.sub(r"[^a-zA-Z\s]", "", text).strip()
        words = clean.split()
        if len(words) == 1:
            word = words[0]
            if len(word) >= 2 and word.lower() not in _NOISE and word.isalpha():
                return word.title()
        # Two-word name (First Last)
        if len(words) == 2 and all(w.isalpha() and len(w) >= 2 and w.lower() not in _NOISE for w in words):
            return " ".join(w.title() for w in words)
        return ""

    @staticmethod
    def _is_valid_count(message: str) -> bool:
        return bool(
            re.fullmatch(r"\d{1,5}", message.strip())
            or re.search(
                r"\b\d{1,5}\s*(people|persons|guests|kids|children|adults|participants|players|members)\b",
                message.lower(),
            )
        )

    @staticmethod
    def _is_valid_budget(message: str) -> bool:
        lowered = message.lower().strip(" .!?")
        if lowered in {"standard", "premium", "basic", "regular"}:
            return True
        if re.search(r"\b(?:budget|range|under|below|within|around|about)\b", lowered):
            return True
        return bool(re.fullmatch(r"₹?\s*\d[\d,]*(?:\s*-\s*₹?\s*\d[\d,]*)?", lowered))

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
            return True
        if re.fullmatch(r"[a-z]\d+", lowered):
            return True
        if lowered in {"ah but its in google", "ah but it's in google"}:
            return True
        return False

    def _has_value(self, field: str) -> bool:
        if field == "participants" and self.memory.data.get("company_size"):
            return True
        return bool(self.memory.data.get(field))

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
