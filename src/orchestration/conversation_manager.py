from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..services.intent_detector import IntentDetector
from ..agents.booking_agent import BookingAgent
from ..services.question_classifier import QuestionClassifier

if TYPE_CHECKING:
    from ..memory.conversation_memory import ConversationMemory


class ConversationManager:
    def __init__(self, memory: ConversationMemory) -> None:
        self.memory = memory
        self.intent_detector = IntentDetector()

    def determine_routing(self, message: str, active_agent: str) -> tuple[str, str]:
        """
        Runs the preemption checks.
        Returns a tuple: (target_agent, category)
        Where target_agent is "booking_agent" or "inbound_agent"
        And category is one of the 7 specified:
          1. "continuing_workflow"
          2. "faq"
          3. "recommendation"
          4. "new_booking"
          5. "new_corporate"
          6. "new_birthday"
          7. "new_escape_room"
        """
        lowered = message.lower().strip()

        # A positive response to a recommendation must first resolve the
        # recommended room in InboundAgent. Routing it directly to BookingAgent
        # loses the selected room and restarts the choice.
        if (
            self.memory.data.get("recommended_option")
            and not self.memory.data.get("room")
            and lowered.strip(" .!?") in {
                "yes", "yes please", "book it", "book that", "reserve it",
                "let's book that", "lets book that", "i'll take that one", "ill take that one",
                "go ahead", "let's continue", "lets continue", "sounds good", "that works",
            }
        ):
            return "inbound_agent", "recommendation"

        # ------------------------------------------------------------------ #
        # AVAILABILITY-FIRST PREEMPTION (P0 BUG FIX)                         #
        # Availability questions must go to booking_agent BEFORE any FAQ/     #
        # QuestionClassifier check, because QuestionClassifier classifies     #
        # "Do you have slots at 1:30?" as a generic FAQ and would route it   #
        # to inbound_agent instead.                                            #
        # ------------------------------------------------------------------ #
        if self._is_availability_question(message):
            return "booking_agent", "continuing_workflow"

        if active_agent == "booking_agent" and self._is_bare_booking_field_answer(message):
            return "booking_agent", "continuing_workflow"

        if self._is_explicit_booking_change(message):
            return "booking_agent", "continuing_workflow"

        if self._is_recommendation_or_rejection(message):
            return "inbound_agent", "recommendation"

        if self._is_knowledge_or_comparison_question(message):
            return "inbound_agent", "faq"

        if self._is_exploration_context(message):
            return "inbound_agent", "recommendation"

        # ------------------------------------------------------------------ #
        # PRE-ROUTING: QuestionClassifier runs before any keyword check.      #
        # Priority order: Customer Question → Recommendation → FAQ →          #
        #   Qualification → Booking                                           #
        #                                                                     #
        # This catches question forms that keyword lists miss:                #
        #   "explain more", "tell me more", "why", "before that…",           #
        #   "your favorite", "best room", "most popular room", etc.          #
        # ------------------------------------------------------------------ #
        q_analysis = QuestionClassifier.classify(message)
        if q_analysis.asked_question and q_analysis.question_type not in ("booking_signal", ""):
            if q_analysis.question_type == "repair":
                # Stay on the current agent; it will repeat the previous question
                return active_agent, "faq"
            if q_analysis.question_type == "recommendation":
                return "inbound_agent", "recommendation"
            if q_analysis.question_type in ("faq", "policy", "unknown"):
                target = "booking_agent" if active_agent == "booking_agent" else "inbound_agent"
                return target, "faq"

        if self._booking_signal_with_context(message):
            return "booking_agent", "continuing_workflow"

        if (
            self.memory.data.get("booking_started")
            and not self._is_explicit_topic_switch(message)
            and self._is_booking_continuation(message)
        ):
            return "booking_agent", "continuing_workflow"

        if (
            self._has_booking_context()
            and not self._is_explicit_topic_switch(message)
            and self._is_booking_topic(message)
        ):
            return "booking_agent", "continuing_workflow"

        # Once booking owns the conversation, concrete booking inputs must win
        # over room-name and question classification. Otherwise a message such
        # as "book Undercover at 8:20 PM" is routed as a room FAQ and loses the
        # verified slot state.
        if active_agent == "booking_agent" and self._is_booking_continuation(message):
            return "booking_agent", "continuing_workflow"

        if active_agent == "booking_agent" and "food" in lowered:
            return "booking_agent", "continuing_workflow"

        cancellation_policy_terms = (
            "cancellation policy", "cancel policy", "cancellation charges",
            "cancellation rules", "refund policy", "what if i cancel",
        )
        if any(term in lowered for term in cancellation_policy_terms):
            target = "booking_agent" if active_agent == "booking_agent" else "inbound_agent"
            return target, "faq"

        # Let's run the intent detector without context to see the absolute intent of this message
        intent_res = self.intent_detector.detect(message, previous_intent="")
        detected_intent = intent_res.intent

        # 1. Check for New Corporate Event (Category 5)
        corporate_keywords = {"corporate", "office", "team building", "employee", "employees", "company", "hr", "team outing", "staff"}
        if detected_intent == "corporate_event" or any(kw in lowered for kw in corporate_keywords):
            return "inbound_agent", "new_corporate"

        # 2. Check for New Birthday Event (Category 6)
        birthday_keywords = {"birthday", "bday", "cake", "birthday party", "kids party", "child birthday"}
        if detected_intent == "birthday_party" or any(kw in lowered for kw in birthday_keywords):
            return "inbound_agent", "new_birthday"

        # 3. Explicit recommendation requests preempt active workflows.
        recommend_keywords = {
            "recommend", "suggest", "better for", "recommendation", "which one",
            "which rooms", "which would you choose", "what would you recommend",
            "what would you choose", "if you were me",
            "something for",
        }
        if any(kw in lowered for kw in recommend_keywords):
            return "inbound_agent", "recommendation"

        # 4. Check for Rules or Rooms FAQ early
        if self._is_rules_or_rooms_faq(message):
            return "inbound_agent", "faq"

        # 4b. Availability questions preempt qualification entirely — route to booking_agent.
        # This must run BEFORE the generic faq_keywords check because "available" appears
        # in faq_keywords and would incorrectly route the message to inbound_agent.
        if self._is_availability_question(message):
            return "booking_agent", "continuing_workflow"

        # 4c. Explicit practical questions preempt active workflows.
        faq_keywords = {"parking", "location", "locations", "where", "how long", "duration", "is this", "what is", "walk in", "cost", "price", "toilet", "food", "rooms are available"}
        if (detected_intent == "general_faq" and intent_res.confidence > 0.5) or "?" in lowered or any(kw in lowered for kw in faq_keywords):
            return "inbound_agent", "faq"

        # 5. Check for New Escape Room Inquiry (Category 7)
        escape_room_keywords = {"escape room", "room suggestion", "room recommendation", "which room", "hardest room", "puzzles", "none of us have done", "beginner", "experienced"}
        if detected_intent == "escape_room_inquiry" or any(kw in lowered for kw in escape_room_keywords):
            return "inbound_agent", "new_escape_room"

        # 6. Check for New Booking Request (Category 4)
        booking_keywords = {"visiting", "players", "participants", "people", "group of", "want to book", "book a", "bachelor", "stag", "farewell", "couple"}
        other_inquiry_intents = {"bachelor_party", "farewell_party", "couple_event", "virtual_event"}
        if detected_intent in other_inquiry_intents or any(kw in lowered for kw in booking_keywords):
            return "inbound_agent", "new_booking"

        # 7. Check if this is continuing the booking workflow (Category 1)
        # Booking workflow only applies if booking agent is currently active
        if active_agent == "booking_agent":
            return "booking_agent", "continuing_workflow"

        # Default to whatever is currently active
        return active_agent, "continuing_workflow"

    def _is_recommendation_or_rejection(self, message: str) -> bool:
        lowered = message.lower().strip()
        if self._is_availability_question(message):
            return False
        if re.search(r"\bwhich\s+location|location\s+is\s+better|which\s+branch|branch\s+is\s+better\b", lowered):
            return False
        return bool(
            re.search(
                r"\b(?:recommend|suggest|best|better|popular|most people|favorite|favourite|"
                r"which\s+(?:room|game|option|one)|what\s+would\s+you\s+recommend|"
                r"what\s+would\s+you\s+choose|if\s+you\s+were\s+me|"
                r"something\s+for\s+\d+\s+(?:people|players|adults|kids|children|of us)|"
                r"second\s+(?:best|recommendation|option)|another\s+(?:option|room|game)|"
                r"any\s+other\s+(?:option|room|game)|other\s+options?|different\s+(?:option|room|game)|"
                r"first[- ]?timer|first time|never done|beginner|"
                r"don't\s+(?:like|want)\s+(?:that|this|one|murder mystery|hostage|classified|undercover|bomb defusal)|do\s+not\s+(?:like|want)\s+(?:that|this|one|murder mystery|hostage|classified|undercover|bomb defusal)|"
                r"don't\s+like\s+any\s+of\s+(?:these|them)|dont\s+like\s+any\s+of\s+(?:these|them)|same\s+things|"
                r"not\s+that\s+one|already\s+played\s+(?:that|this|it|one)|played\s+that\s+already|"
                r"something\s+(?:harder|easier|scarier|for\s+couples?|for\s+kids?|for\s+adults?))\b",
                lowered,
            )
        )

    def _is_explicit_booking_change(self, message: str) -> bool:
        lowered = message.lower().strip()
        has_change = bool(
            re.search(r"\b(?:actually|change|switch|instead|rather|make it|update|modify|reschedule|use)\b", lowered)
        )
        if not has_change:
            return False
        normalized = self.memory.normalize_number_words(message).lower()
        return bool(
            self.memory._extract_room(lowered)
            or self.memory._extract_location(lowered)
            or self.memory._extract_preferred_date(message)
            or BookingAgent._extract_slot(message)
            or self.memory._extract_participant_range(normalized)
            or self.memory._extract_participants(normalized)
        )

    def _is_bare_booking_field_answer(self, message: str) -> bool:
        lowered = message.lower().strip(" .!?")
        if len(lowered.split()) > 3:
            return False
        if "?" in message or re.match(r"^(?:can|could|do|does|is|are|will|would|what|which|how)\b", lowered):
            return False
        normalized = self.memory.normalize_number_words(message).lower()
        return bool(
            lowered in {"adult", "adults", "kids", "children", "teens", "teenagers", "mixed", "mix", "family"}
            or self.memory._extract_age_group(lowered, allow_bare_range=True)[0]
            or self.memory._extract_location(lowered)
            or BookingAgent._extract_slot(message)
            or self.memory._extract_participant_range(normalized)
            or self.memory._extract_participants(normalized)
        )

    def _is_knowledge_or_comparison_question(self, message: str) -> bool:
        lowered = message.lower().strip()
        if self._is_availability_question(message):
            return False
        if "?" not in lowered and not re.match(
            r"^\s*(what|which|how|tell|explain|compare|difference|describe)\b",
            lowered,
        ):
            return False
        return bool(
            re.search(
                r"\b(?:what\s+rooms?|which\s+rooms?|rooms?\s+do\s+you\s+have|themes?\s+do\s+you\s+have|"
                r"what\s+themes?|what\s+games?|games?\s+do\s+you\s+have|options?\s+(?:are\s+)?available|"
                r"what\s+happens\s+in|what\s+is\s+(?:murder mystery|hostage|classified|bomb defusal|"
                r"undercover|prison break|missile attack|an?\s+escape room)|"
                r"how\s+does\s+an?\s+escape room|what\s+are\s+the\s+rules|how\s+long|provide\s+hints|"
                r"locked\s+in|kids\s+play|tell\s+me\s+about|explain|compare|difference\s+between|vs|versus|"
                r"which\s+location|location\s+is\s+better|koramangala|whitefield|jp nagar)\b",
                lowered,
            )
        )

    def _is_exploration_context(self, message: str) -> bool:
        lowered = message.lower().strip()
        if self._is_availability_question(message):
            return False
        if self._booking_signal_with_context(message):
            return False
        return bool(
            re.search(
                r"\b(?:first\s+time|first-time|first\s+timers?|never\s+done|done\s+(?:this|escape rooms?)\s+before|"
                r"(?:second|third|fourth|fifth)\s+time|played\s+before|experienced|beginners?|"
                r"harder|challenging|easy|easier|couple|couples|kids|children|adults|family|"
                r"don't\s+like\s+puzzles|dont\s+like\s+puzzles|story|mystery|investigation|scary|horror|thrill)\b",
                lowered,
            )
        )

    def _is_rules_or_rooms_faq(self, message: str) -> bool:
        lowered = message.lower().strip()
        rules_patterns = [
            r"\bexplain\s+(?:the\s+)?rules\b",
            r"\bwhat\s+are\s+the\s+rules\b",
            r"\bhow\s+does\s+it\s+work\b",
            r"\bwhat\s+happens\s+inside\b",
            r"\bfirst\s+time\s+here\b",
        ]
        rooms_patterns = [
            r"\bexplain\s+(?:the\s+)?rooms\b",
            r"\bcan\s+(?:you\s+)?explain\s+(?:the\s+)?rooms\b",
            r"\bwhat\s+rooms\b",
            r"\btell\s+me\s+about\s+(?:the\s+)?rooms\b",
            r"\broom\s+options\b",
        ]
        extra_faq_phrases = ("can explain rooms", "explain rooms", "tell me about rooms", "explain rules", "how does it work")
        
        for pat in rules_patterns + rooms_patterns:
            if re.search(pat, lowered):
                return True
        if any(phrase in lowered for phrase in extra_faq_phrases):
            return True
        return False

    def _is_booking_continuation(self, message: str) -> bool:
        lowered = message.lower().strip()

        if self._is_explicit_booking_change(message):
            return True

        if self._is_recommendation_or_rejection(message) or self._is_knowledge_or_comparison_question(message):
            return False

        normalized = self.memory.normalize_number_words(message).lower()
        if (
            not self._is_explicit_topic_switch(message)
            and (
                self.memory._extract_participant_range(normalized)
                or self.memory._extract_participants(normalized)
            )
        ):
            return True

        # Cancellation / Rescheduling is handled by booking agent
        cancel_keywords = {"cancel", "cancellation", "refund", "reschedule", "postpone"}
        if any(kw in lowered for kw in cancel_keywords):
            return True

        # Has date
        extracted_date = self.memory._extract_preferred_date(message)
        if extracted_date:
            return True

        # Has slot time
        extracted_slot = BookingAgent._extract_slot(message)
        if extracted_slot:
            return True

        # Confirmation / negation words
        continuation_words = {
            "yes", "yep", "yeah", "sure", "confirm", "go ahead", "no", "nope",
            "that's all", "nothing", "thanks", "thank you", "bye", "goodbye",
            "correct", "perfect", "ok", "okay", "that works", "works for me"
        }
        cleaned = lowered.strip(" .!?")
        if cleaned in continuation_words:
            return True

        # Check for slot numbers/indices
        words = set(re.findall(r"\b[a-z']+\b", lowered))
        if (
            self.memory.data.get("current_workflow") == "booking"
            and self.memory.data.get("selected_slot")
            and {"first", "second", "third", "one", "two", "three", "slot"}.intersection(words)
        ):
            return True

        return False

    def _has_booking_context(self) -> bool:
        return all(
            self.memory.data.get(field)
            for field in ("room", "location", "preferred_date")
        )

    def _booking_signal_with_context(self, message: str) -> bool:
        lowered = message.lower()
        booking_signal = bool(
            re.search(
                r"\b(?:book|booking|reserve|proceed|confirm\s+booking|availability|available\s+slots?)\b",
                lowered,
            )
        )
        if not booking_signal:
            return False
        return bool(
            self.memory.data.get("room")
            or self.memory.data.get("location")
            or self.memory._extract_room(lowered)
            or self.memory._extract_location(lowered)
        )

    @staticmethod
    def _is_availability_question(message: str) -> bool:
        """
        Returns True when the customer is asking about slot/date availability.
        These questions must be answered before any qualification question is asked.

        Matches patterns like:
          - "Do you have slots at 1:30 tomorrow?"
          - "Is 5:20 available?"
          - "Any openings this Saturday?"
          - "What slots do you have tomorrow?"
          - "Are there any slots available?"
          - "Can I book for tomorrow at 3?"
        """
        lowered = message.lower()

        # Exclusion guard: these are NOT availability questions even if they have a time
        is_not_availability = bool(
            re.search(
                r"\b(?:running\s+late|minutes?\s+late|late\s+for|we\s+have\s+a\s+booking|"
                r"our\s+booking|my\s+booking|existing\s+booking|cancel|reschedule|"
                r"cancellation|refund|already\s+booked|confirmed\s+booking)\b",
                lowered,
            )
        )
        if is_not_availability:
            return False

        # Has a time-of-day or date anchor (bare HH:MM is also valid, e.g. "Is 5:20 available?")
        has_time = bool(
            re.search(
                r"(?:"
                r"\b\d{1,2}:\d{2}\b"                                                    # bare HH:MM  e.g. 5:20
                r"|"
                r"\b\d{1,2}\s*(?:am|pm)\b"                                              # 3pm / 3 am
                r"|"
                r"\b(?:tomorrow|today|tonight|this\s+(?:saturday|sunday|monday|tuesday|wednesday|thursday|friday)"
                r"|next\s+(?:week|saturday|sunday|monday|tuesday|wednesday|thursday|friday)|weekend)\b"
                r")",
                lowered,
            )
        )
        has_availability_signal = bool(
            re.search(
                r"\b(?:available|availability|open|openings?|slot|slots|booking|book|time|times|check|"
                r"have|have\s+(?:any|a)\s+slot|morning|afternoon|evening|tonight)\b",
                lowered,
            )
        )
        has_question = "?" in lowered or bool(
            re.search(r"\b(?:do you|is there|are there|can i|any|what|when|show me|check|is\b)\b", lowered)
        )
        # Strong slot+availability phrase can bypass the time anchor requirement
        # e.g. "Are there any slots available?" has no time anchor but is clearly about availability.
        strong_slot_phrase = bool(
            re.search(r"\b(?:slots?\s+available|any\s+slots?|check\s+availability|slot\s+availability)\b", lowered)
        )
        if strong_slot_phrase and has_question:
            return True
        time_window_fragment = bool(
            re.search(r"\b(?:morning|afternoon|evening|tonight)\b", lowered)
            and re.search(r"\b(?:tomorrow|today|tonight|this\s+\w+|next\s+\w+|weekend)\b", lowered)
        )
        return has_time and has_availability_signal and (has_question or time_window_fragment)

    def _is_explicit_topic_switch(self, message: str) -> bool:
        lowered = message.lower()
        current_intent = str(self.memory.data.get("intent", ""))
        if current_intent != "corporate_event" and re.search(
            r"\b(?:corporate|office|employees?|company|team building)\b", lowered
        ):
            return True
        if current_intent != "birthday_party" and re.search(
            r"\b(?:birthday|bday|birthday party)\b", lowered
        ):
            return True
        return current_intent != "escape_room_inquiry" and "escape room" in lowered

    def _is_booking_topic(self, message: str) -> bool:
        lowered = message.lower()
        normalized = self.memory.normalize_number_words(message).lower()
        return bool(
            BookingAgent._extract_slot(message)
            or self.memory._extract_participant_range(normalized)
            or self.memory._extract_participants(normalized)
            or re.search(
                r"\b(?:availability|available|price|pricing|cost|book|booking|reserve|"
                r"confirmation|confirm|capacity|fit|accommodate|slot|time|discount|offer|"
                r"coupon|promo|deal|membership)\b",
                lowered,
            )
        )
