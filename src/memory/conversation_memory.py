from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


DEFAULT_MEMORY = {
    "customer_name": "",
    "first_name": "",
    "last_name": "",
    "phone": "",
    "email": "",
    "call_id": "",
    "call_status": "unknown",
    "call_status_source": "none",
    "call_started_at": "",
    "call_ended_at": "",
    "call_ended_reason": "",
    "last_call_activity_at": "",
    "vapi_assistant_id": "",
    "vapi_phone_number_id": "",
    "vapi_monitor_listen_url": "",
    "vapi_monitor_control_url": "",
    "vapi_provider": "",
    "vapi_environment": "",
    "vapi_region": "",
    "vapi_event_count": 0,
    "vapi_events": [],
    "vapi_transcript_events": [],
    "vapi_errors": [],
    "location": "",
    "participants": "",
    "participants_min": "",
    "participants_max": "",
    "age_group": "",
    "age_detail": "",
    "experience_level": "",
    "challenge_preference": "",
    "company_size": "",
    "event_type": "",
    "preferred_date": "",
    "preferred_time": "",
    "food_required": "",
    "budget_range": "",
    "room": "",
    "intent": "",
    "sentiment": "neutral",
    "sentiment_confidence": 0.0,
    "sentiment_reason": "",
    "sentiment_history": [],
    "score_history": [],
    "learning_metrics": {
        "turns": 0,
        "intent_counts": {},
        "sentiment_counts": {},
        "handoff_count": 0,
        "booking_confirmed_count": 0,
        "whatsapp_sent_count": 0,
        "last_insights": [],
    },
    "escalation_state": {"escalate": False, "reason": "", "summary": ""},
    "escalation_history": [],
    "escalation_requests": [],
    "support_tickets": [],
    "unresolved_queries": [],
    "failed_answer_count": 0,
    "authentication_failure_count": 0,
    "payment_status": "not_started",
    "recommended_option": "",
    "selected_slot": "",
    # ------------------------------------------------------------------ #
    # Workflow state — tracks which mode the agent is currently in.       #
    # Values: "general" | "qualification" | "awaiting_booking" | "booking"#
    # ------------------------------------------------------------------ #
    "current_workflow": "general",
    "conversation_mode": "sales",
    "booking_consent_pending": False,
    "booking_started": False,
    "conversation": [],
    "discussed_options": [],
    "last_discussed_topic": "",
    "pending_policy_explanation": False,
    "customer_preferences": [],
    "concerns": [],
    "pending_confirmation": None,
    "audit_trail": [],
}


class ConversationMemory:
    LOCATIONS = ("Koramangala", "Whitefield", "JP Nagar")
    FLOW_FIELDS = {
        "escape_room_inquiry": ["participants", "age_group", "location"],
        "birthday_party": ["location", "participants", "preferred_date"],
        "corporate_event": ["location", "company_size", "preferred_date"],
        "bachelor_party": ["participants", "location", "preferred_date"],
        "farewell_party": ["participants", "location", "preferred_date"],
        "couple_event": ["participants", "location", "preferred_date"],
        "virtual_event": ["participants", "preferred_date"],
        "cancellation_request": ["customer_name", "phone"],
        "general_faq": [],
    }
    CONTACT_FIELDS = ["customer_name", "phone", "email"]

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
        for key in [
            "discussed_options", "customer_preferences", "concerns", "audit_trail",
            "sentiment_history", "score_history", "escalation_history",
            "escalation_requests", "support_tickets", "unresolved_queries",
            "vapi_events", "vapi_transcript_events", "vapi_errors",
        ]:
            if not isinstance(merged.get(key), list):
                merged[key] = []
        if not isinstance(merged.get("learning_metrics"), dict):
            merged["learning_metrics"] = copy.deepcopy(DEFAULT_MEMORY["learning_metrics"])
        if not isinstance(merged.get("escalation_state"), dict):
            merged["escalation_state"] = copy.deepcopy(DEFAULT_MEMORY["escalation_state"])
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
            "participants_min",
            "participants_max",
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

    def set_field(self, field: str, new_value: Any, message: str = "", expected_field: str = "") -> bool:
        """
        Safely update a memory field, determining if it is:
        - "new" (field was empty)
        - "modification" (field had value, message contains explicit change triggers)
        - "correction" (field had value, message does not have triggers - requires confirmation if confidence low)
        Returns True if the update was accepted/applied, False if it was deferred for confirmation.
        """
        old_value = self.data.get(field, "")
        
        # Normalize types for comparison
        str_old = str(old_value).strip().lower()
        str_new = str(new_value).strip().lower()
        
        if str_new == "" or str_new == str_old:
            return False
            
        if old_value == "":
            update_type = "new"
        else:
            lowered_msg = message.lower()
            change_indicators = ["instead", "now", "actually", "change", "switch", "update", "correct", "rather", "prefer", "want to do", "different", "can we do", "i want", "reschedule", "modify", "meant", "no", "not", "incorrect", "wrong", "typo", "mistake"]
            intent_changed = getattr(self, "_intent_changing", False)
            is_explicit_change = (
                intent_changed or
                any(indicator in lowered_msg for indicator in change_indicators) or
                len(lowered_msg.split()) <= 2 or
                (
                    field == "participants"
                    and bool(
                        re.search(
                            r"\b(?:\d{1,4}\s*(?:people|persons|guests|participants|players|members|friends|adults)"
                            r"|we\s+are\s+(?:actually|now)?\s*\d{1,4}|group\s+of\s+\d{1,4})\b",
                            lowered_msg,
                        )
                    )
                ) or
                (
                    field == "age_group"
                    and any(
                        phrase in lowered_msg
                        for phrase in (
                            "all adults", "we are adults", "we're adults", "adult group",
                            "all kids", "we are kids", "we're kids", "kids group",
                            "all teens", "we are teens", "we're teens", "teen group",
                        )
                    )
                )
            )
            
            if is_explicit_change:
                # High-confidence update (explicit correction or modification)
                if any(kw in lowered_msg for kw in ["instead", "now", "change", "switch", "update", "reschedule", "modify", "different"]):
                    update_type = "modification"
                else:
                    update_type = "correction"
            else:
                # Low-confidence correction/contradiction
                update_type = "correction"
                
                # Defer location/room/participants/date/age_group changes that are not explicit
                if field in ["location", "room", "participants", "preferred_date", "age_group"] and field != expected_field:
                    self.data["pending_confirmation"] = {
                        "field": field,
                        "old_value": old_value,
                        "new_value": new_value,
                        "type": update_type
                    }
                    self.save()
                    return False

        # Apply update
        if field == "participants" and old_value not in ("", new_value):
            logger.info("PARTICIPANT_OVERRIDE=true")
            logger.info("OLD_PARTICIPANTS=%s", old_value)
            logger.info("NEW_PARTICIPANTS=%s", new_value)
        self.data[field] = new_value
        if field == "age_group":
            self.data["age_detail"] = ""
        if field == "participants":
            self.data["participants_min"] = ""
            self.data["participants_max"] = ""
        if field == "participants" and self.data.get("intent") == "corporate_event":
            self.data["company_size"] = new_value
        elif field == "company_size":
            self.data["participants"] = new_value
        
        # Log to audit trail
        if "audit_trail" not in self.data or not isinstance(self.data["audit_trail"], list):
            self.data["audit_trail"] = []
            
        if field != "recommended_option":
            self.data["audit_trail"].append({
                "field": field,
                "old_value": old_value,
                "new_value": new_value,
                "type": update_type
            })
        self.save()
        return True

    def update_from_message(self, message: str, intent: str, recommendation: str = "", expected_field: str = "") -> dict[str, Any]:
        return self.merge_message(message, intent, recommendation, expected_field)

    def merge_message(self, message: str, intent: str, recommendation: str = "", expected_field: str = "") -> dict[str, Any]:
        self._intent_changing = bool(intent and intent != self.data.get("intent", ""))
        text = self.normalize_number_words(message.strip())
        lowered = text.lower()
        extracted: dict[str, Any] = {}

        if intent:
            if self.set_field("intent", intent, message, expected_field):
                extracted["intent"] = intent
            event_type = self.EVENT_BY_INTENT.get(intent, "")
            if event_type:
                if self.set_field("event_type", event_type, message, expected_field):
                    extracted["event_type"] = event_type

        if recommendation:
            if self.set_field("recommended_option", recommendation, message, expected_field):
                extracted["recommended_option"] = recommendation

        name = self._extract_name(text)
        if name:
            if self.set_field("customer_name", name, message, expected_field):
                extracted["customer_name"] = name

        phone = self._extract_phone(text)
        if phone:
            if self.set_field("phone", phone, message, expected_field):
                extracted["phone"] = phone

        email = self._extract_email(text)
        if email:
            if self.set_field("email", email, message, expected_field):
                extracted["email"] = email

        location = self._extract_location(lowered)
        if location:
            if self.set_field("location", location, message, expected_field):
                extracted["location"] = location

        room = self._extract_room(lowered)
        if room:
            if self.set_field("room", room, message, expected_field):
                extracted["room"] = room

        participant_range = self._extract_participant_range(lowered)
        participants = participant_range[1] if participant_range else self._extract_participants(lowered)
        if participants:
            if self.set_field("participants", participants, message, expected_field):
                extracted["participants"] = participants
            if participant_range:
                self.data["participants_min"], self.data["participants_max"] = participant_range

        age_group, age_detail = self._extract_age_group(
            lowered,
            allow_bare_range=expected_field == "age_group",
        )
        if age_group:
            if self.set_field("age_group", age_group, message, expected_field):
                extracted["age_group"] = age_group
        if age_detail:
            if self.set_field("age_detail", age_detail, message, expected_field):
                extracted["age_detail"] = age_detail

        experience_level = self._extract_experience_level(lowered)
        if experience_level:
            if self.set_field("experience_level", experience_level, message, expected_field):
                extracted["experience_level"] = experience_level

        challenge_preference = self._extract_challenge_preference(lowered)
        if challenge_preference:
            if self.set_field("challenge_preference", challenge_preference, message, expected_field):
                extracted["challenge_preference"] = challenge_preference

        company_size = self._extract_company_size(lowered)
        if company_size:
            if self.set_field("company_size", company_size, message, expected_field):
                extracted["company_size"] = company_size

        preferred_date = self._extract_preferred_date(text)
        if preferred_date:
            logger.info("DATE_EXTRACTED=%s", preferred_date)
            logger.info("DATE_NORMALIZED=%s", preferred_date)
            if self.set_field("preferred_date", preferred_date, message, expected_field):
                extracted["preferred_date"] = preferred_date
            logger.info("DATE_PERSISTED=%s", self.data.get("preferred_date", ""))

        preferred_time = self._extract_time(text)
        if preferred_time:
            if self.set_field("preferred_time", preferred_time, message, expected_field):
                extracted["preferred_time"] = preferred_time

        food_required = self._extract_food_required(lowered)
        if food_required != "":
            if self.set_field("food_required", food_required, message, expected_field):
                extracted["food_required"] = food_required

        budget_range = self._extract_budget_range(text)
        if budget_range:
            if self.set_field("budget_range", budget_range, message, expected_field):
                extracted["budget_range"] = budget_range

        self.data["sentiment"] = self._detect_sentiment(lowered)
        extracted["sentiment"] = self.data["sentiment"]
        self.save()
        if hasattr(self, "_intent_changing"):
            del self._intent_changing
        return extracted

    def add_turn(self, role: str, content: str) -> None:
        self.data.setdefault("conversation", []).append({"role": role, "content": content})
        self.data["conversation"] = self.data["conversation"][-30:]
        self.save()
        if role == "agent":
            snapshot = {
                key: self.data.get(key, "")
                for key in (
                    "intent", "participants", "participants_min", "participants_max",
                    "age_group", "experience_level", "location", "preferred_date",
                    "room", "selected_slot", "booking_id", "booking_ref",
                )
            }
            snapshot["has_customer_name"] = bool(self.data.get("customer_name"))
            snapshot["has_phone"] = bool(self.data.get("phone"))
            logger.info("MEMORY_SNAPSHOT=%s", json.dumps(snapshot, sort_keys=True))
            logger.info(
                "MISSING_FIELDS=%s",
                json.dumps(self._diagnostic_missing_fields()),
            )

    def _diagnostic_missing_fields(self) -> list[str]:
        if self.data.get("intent") == "escape_room_inquiry":
            required = [
                "participants", "age_group", "location", "room", "preferred_date",
                "selected_slot", "first_name", "last_name", "phone", "booking_id",
            ]
            return [field for field in required if not self.data.get(field)]
        return self.missing_fields(self.data.get("intent", ""), include_contact=True)

    def missing_fields(self, intent: str | None = None, include_contact: bool = False) -> list[str]:
        active_intent = intent or self.data.get("intent", "")
        required = list(self.FLOW_FIELDS.get(active_intent, []))
        if include_contact and active_intent not in ("", "general_faq"):
            required.extend(field for field in self.CONTACT_FIELDS if field not in required)
        return [field for field in required if self.data.get(field) is not False and not self.data.get(field)]

    def flow_complete(self, intent: str | None = None) -> bool:
        return not self.missing_fields(intent=intent, include_contact=False)

    def handoff_ready(self, intent: str | None = None) -> bool:
        active_intent = intent or self.data.get("intent", "")
        if active_intent in ("", "general_faq"):
            return False
        if active_intent == "escape_room_inquiry":
            return all(
                self.data.get(field)
                for field in ("participants", "age_group", "location", "preferred_date", "room")
            )
        
        from ..agents.qualification_agent import QualificationAgent
        required = list(QualificationAgent.REQUIRED_BY_INTENT.get(active_intent, []))
        
        # Package/event qualification still collects contact before its handoff.
        for field in ["customer_name", "phone"]:
            if field not in required:
                required.append(field)
                
        for field in required:
            if field == "event_type":
                continue
            if not self.data.get(field) and not (field == "participants" and self.data.get("company_size")):
                return False
        return True

    def booking_ready(self) -> bool:
        required = [
            "age_group", "location", "preferred_date", "selected_slot",
            "phone",
        ]
        if self.data.get("intent") == "escape_room_inquiry":
            required.append("room")
        name_parts = str(self.data.get("customer_name", "")).split()
        has_first_name = bool(self.data.get("first_name") or name_parts)
        has_last_name = bool(self.data.get("last_name") or len(name_parts) > 1)
        return bool(
            (self.data.get("participants") or self.data.get("company_size"))
            and has_first_name
            and has_last_name
            and all(self.data.get(field) for field in required)
        )


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
        compound_ordinals = {
            "twenty first": 21,
            "twenty second": 22,
            "twenty third": 23,
            "twenty fourth": 24,
            "twenty fifth": 25,
            "twenty sixth": 26,
            "twenty seventh": 27,
            "twenty eighth": 28,
            "twenty ninth": 29,
        }
        for phrase, value in compound_ordinals.items():
            text = re.sub(rf"\b{phrase}\b", str(value), text, flags=re.IGNORECASE)

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
            r"\bname\s*:\s*([A-Za-z]+(?:\s+[A-Za-z]+){0,2}?)(?=\s*(?:[.;,]|contact\s+number|phone|mobile|$))",
            r"\bactually\s+(?:use|make it|change(?:\s+it)?\s+to)\s+([A-Za-z]+(?:\s+[A-Za-z]+){0,2}?)(?=\s*(?:[.;,]|$))",
            r"\bmy first name is\s+([A-Za-z]+)(?=\s*(?:[.;,]|$))",
            r"\bmy name is\s+([A-Za-z]+(?:\s+[A-Za-z]+){0,2}?)(?=\s+(?:and\s+)?(?:my\s+)?phone|[,.;]|$)",
            r"\bname\s+([A-Za-z]+(?:\s+[A-Za-z]+){0,2}?)(?=\s*(?:[.;,]|$))",
            r"\bbook under\s+([A-Za-z]+(?:\s+[A-Za-z]+){0,2}?)(?=\s*(?:[.;,]|$))",
            r"\bthis is\s+(my\s+first\s+time)(?=\s*(?:[.;,]|$))",
            r"\bi am\s+([A-Za-z]+)(?=\s+(?:and\s+)?(?:my\s+)?phone|[,.;]|$)",
            r"\bi'm\s+([A-Za-z]+)(?=\s+(?:and\s+)?(?:my\s+)?phone|[,.;]|$)",
            r"\bthis is\s+([A-Za-z]+)(?=\s+(?:and\s+)?(?:my\s+)?phone|[,.;]|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                candidate = " ".join(match.group(1).split()).title()
                logger.info("NAME_CANDIDATE=%s", candidate)
                if ConversationMemory._is_plausible_name(candidate):
                    logger.info("NAME_ACCEPTED=%s", candidate)
                    logger.info("CUSTOMER_NAME_EXTRACTED=%s", candidate)
                    return candidate
                logger.info("NAME_REJECTED=%s", candidate)
        return ""

    @staticmethod
    def _is_plausible_name(candidate: str) -> bool:
        words = [word.lower() for word in candidate.split()]
        if not 1 <= len(words) <= 3:
            return False
        rejected_words = {
            "a", "an", "the", "my", "our", "your", "first", "time", "beginner",
            "adult", "adults", "kid", "kids", "teen", "teens", "friend", "friends",
            "booking", "book", "room", "escape", "ready", "tomorrow", "today", "friday",
            "phone", "number", "available", "availability",
            "frustrated", "angry", "upset", "furious", "confused", "unhappy",
            "injured", "hurt", "waiting", "complaining",
            # Booking-domain words that are not plausible names
            "game", "games", "slot", "slots", "date", "yes", "no", "ok", "okay",
            "sure", "wait", "hi", "hello", "hey", "please", "thanks", "thank",
            "change", "cancel", "reschedule", "confirm", "booking", "reserve",
        }
        return all(
            word.isalpha() and len(word) >= 2 and word not in rejected_words
            for word in words
        )

    @staticmethod
    def _extract_phone(text: str) -> str:
        spoken_digits = ConversationMemory._extract_spoken_phone(text)
        if spoken_digits:
            logger.info("PHONE_EXTRACTED=%s", spoken_digits)
            return spoken_digits

        labelled = re.search(
            r"\b(?:phone|contact\s+number|mobile)(?:\s+number)?\s*(?::|is|-)?\s*"
            r"(\+?[0-9][0-9\s-]{6,16}[0-9])\b",
            text,
            flags=re.IGNORECASE,
        )
        if labelled:
            digits = re.sub(r"\D", "", labelled.group(1))
            if digits.startswith("91") and len(digits) == 12:
                digits = digits[2:]
            logger.info("PHONE_EXTRACTED=%s", digits)
            return digits
        match = re.search(r"(?:(?:\+91[\s-]?)|0)?([6-9]\d{9})\b", text.replace(" ", ""))
        if match:
            logger.info("PHONE_EXTRACTED=%s", match.group(1))
            return match.group(1)
        return ""

    @staticmethod
    def _extract_spoken_phone(text: str) -> str:
        lowered = text.lower()
        if not re.search(r"\b(?:zero|oh|o|one|two|three|four|five|six|seven|eight|nine|double|triple)\b", lowered):
            return ""
        tokens = re.findall(r"[a-z]+|\d", lowered)
        digit_words = {
            "zero": "0", "oh": "0", "o": "0",
            "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
            "six": "6", "seven": "7", "eight": "8", "nine": "9",
        }
        digits: list[str] = []
        multiplier = 1
        seen_phone_context = False
        context_words = {"phone", "mobile", "contact", "number", "call", "whatsapp", "is"}
        for token in tokens:
            if token in context_words:
                seen_phone_context = True
                continue
            if token == "double":
                multiplier = 2
                continue
            if token == "triple":
                multiplier = 3
                continue
            if token.isdigit():
                digits.append(token)
                multiplier = 1
                continue
            digit = digit_words.get(token)
            if digit is not None:
                digits.extend([digit] * multiplier)
                multiplier = 1
                continue
            multiplier = 1

        number = "".join(digits)
        if number.startswith("91") and len(number) == 12:
            number = number[2:]
        if 7 <= len(number) <= 12 and (seen_phone_context or len(re.findall(r"\b(?:zero|oh|o|one|two|three|four|five|six|seven|eight|nine|double|triple)\b", lowered)) >= 4):
            return number
        return ""

    @staticmethod
    def _extract_time(text: str) -> str:
        lowered = text.lower().strip()
        words = {
            "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
            "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
            "eleven": "11", "twelve": "12",
        }
        for word, digit in words.items():
            lowered = re.sub(rf"\b{word}\b", digit, lowered)
        match = re.search(r"\b(\d{1,2})(?::(\d{2})?)?\s*(am|pm)\b", lowered)
        if match:
            hour = str(int(match.group(1)))
            minute = match.group(2) or "00"
            return f"{hour}:{minute} {match.group(3).upper()}"
        return ""

    @staticmethod
    def _extract_email(text: str) -> str:
        match = re.search(r"\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b", text)
        return match.group(1) if match else ""

    @classmethod
    def _extract_location(cls, lowered: str) -> str:
        exact_matches = [
            location for location in cls.LOCATIONS if location.lower() in lowered
        ]
        if len(exact_matches) > 1:
            return ""
        # Exact match first
        if exact_matches:
            return exact_matches[0]
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
        rooms = {
            "murder mystery": "Murder Mystery",
            "hostage": "Hostage",
            "curse of the pharaoh": "Curse of the Pharaoh",
            "pharaoh's curse": "Curse of the Pharaoh",
            "pharaohs curse": "Curse of the Pharaoh",
            "classified": "Classified",
            "undercover": "Undercover",
            "the wizarding championship": "The Wizarding Championship",
            "wizarding championship": "The Wizarding Championship",
            "wizard championship": "The Wizarding Championship",
            "the forbidden forest": "The Forbidden Forest",
            "forbidden forest": "The Forbidden Forest",
            "bomb defusal": "Bomb Defusal",
            "bomb defuser": "Bomb Defusal",
            "defusal": "Bomb Defusal",
            "prison break": "Prison Break",
            "prison break-out": "Prison Break",
            "prison breakout": "Prison Break",
            "zodiac": "Zodiac",
        }
        for pattern, canonical in rooms.items():
            if pattern in lowered:
                return canonical
                
        # Fuzzy match using difflib for room names
        import difflib
        room_names = list(set(rooms.values()))
        words = re.findall(r"[a-z]+", lowered)
        for word in words:
            if len(word) >= 5:
                matches = difflib.get_close_matches(word, [r.lower() for r in room_names], n=1, cutoff=0.75)
                if matches:
                    idx = [r.lower() for r in room_names].index(matches[0])
                    return room_names[idx]
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
            r"\bwe are\s+(?:(?:actually|now)\s+)?(\d{1,4})\b",
            r"\b(\d{1,4})\s*of us\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if match:
                return int(match.group(1))

        # Check for "a couple" or "two of us"
        if "couple" in lowered or "two of us" in lowered or "both of us" in lowered or "wife and me" in lowered or "husband and me" in lowered or "my wife and i" in lowered:
            return 2

        # Check for "son and 9 friends"
        son_friend_match = re.search(r"\b(?:my\s+)?(?:son|daughter|wife|husband|friend|brother|sister|partner)\s+and\s+(\d{1,2})\b", lowered)
        if son_friend_match:
            return int(son_friend_match.group(1)) + 1

        # Check for "me and my 6 friends"
        me_friend_match = re.search(r"\b(?:me|i|myself)\s+and\s+(?:my\s+)?(\d{1,2})\b", lowered)
        if me_friend_match:
            return int(me_friend_match.group(1)) + 1

        return ""

    @staticmethod
    def _extract_participant_range(lowered: str) -> tuple[int, int] | None:
        match = re.search(
            r"\b(\d{1,4})\s*(?:to|[-–—])\s*(\d{1,4})\s*"
            r"(?:people|persons|guests|participants|players|members|friends|adults)\b",
            lowered,
        )
        if not match:
            return None
        low, high = sorted((int(match.group(1)), int(match.group(2))))
        return low, high

    @staticmethod
    def _extract_age_group(lowered: str, allow_bare_range: bool = False) -> tuple[str, str]:
        # High-confidence quick checks first
        if any(phrase in lowered for phrase in ("all adults", "mostly adults", "only adults", "we are all adults", "adult group", "everyone is an adult")):
            return "adults", ""
        if any(phrase in lowered for phrase in ("all kids", "mostly kids", "only kids", "we are all kids", "kids group", "family with children", "family with kids")):
            return "kids", ""

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

        # Numeric ranges are ages only with explicit age context. Without this
        # guard, group sizes ("10 to 15 people") and times ("3 to 6 PM")
        # corrupt age_group and age_detail.
        has_age_context = allow_bare_range or bool(
            re.search(r"\b(?:age|ages|aged|years?\s+old|year-olds?|kids?|children|teens?)\b", lowered)
        )
        range_patterns = [
            r"\b(?:ages?|age)\s+(\d{1,2})\s*(?:to|[-–—])\s*(\d{1,2})\s*years?\b",
            r"\b(\d{1,2})\s*(?:to|[-–—])\s*(\d{1,2})\s*years?\b",
            r"\b(\d{1,2})\s*(?:to|[-–—])\s*(\d{1,2})\b",
        ]

        for pattern in range_patterns if has_age_context else ():
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
            "none of us has ever done", "none of us have ever done",
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
            "day after tomorrow",   # must come BEFORE "tomorrow" (substring guard)
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
