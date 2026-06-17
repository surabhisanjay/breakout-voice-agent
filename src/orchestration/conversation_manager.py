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
        }
        if any(kw in lowered for kw in recommend_keywords):
            return "inbound_agent", "recommendation"

        # 4. Check for Rules or Rooms FAQ early
        if self._is_rules_or_rooms_faq(message):
            return "inbound_agent", "faq"

        # 4b. Explicit practical questions preempt active workflows.
        faq_keywords = {"parking", "location", "locations", "where", "how long", "duration", "is this", "what is", "walk in", "cost", "price", "toilet", "food", "available", "rooms are available"}
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
        words = set(re.findall(r"\b[a-z']+\b", lowered))
        if continuation_words.intersection(words):
            return True

        # Check for slot numbers/indices
        if any(word in lowered for word in ("first", "second", "third", "one", "two", "three", "slot")):
            return True

        return False
