"""question_classifier.py — deterministic question detection for the Breakout agent.

Before every response the agent must determine:
  1. Did the customer ask a question?
  2. What category is it? (recommendation | faq | policy | booking_signal | repair | unknown)
  3. Can we answer from knowledge?
  4. Does it need a tool?
  5. Does it need clarification?

This module is purely deterministic — no LLM calls, no network I/O.
It is the single authoritative gate that sits above the qualification pipeline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Tuple


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclass
class QuestionAnalysis:
    """Structured result of question classification."""
    asked_question: bool = False
    question_type: str = ""           # recommendation | faq | policy | booking_signal | repair | unknown
    topic: str = ""                   # human-readable topic string
    can_answer_deterministically: bool = False
    needs_tool: bool = False          # requires availability/booking tool
    needs_clarification: bool = False


# ---------------------------------------------------------------------------
# Phrase catalogues
# ---------------------------------------------------------------------------

# ---- Recommendation phrases ------------------------------------------------
# The customer is asking the agent to suggest / compare / explain a room/option.
_RECOMMENDATION_PHRASES: Tuple[str, ...] = (
    "explain more",
    "tell me more",
    "more details",
    "more information",
    "more info",
    "can you explain",
    "explain that",
    "explain it",
    "say more",
    "elaborate",
    "go on",
    "best room",
    "which room",
    "what room",
    "best option",
    "which option",
    "which one",
    "what would you recommend",
    "what do you recommend",
    "which would you recommend",
    "which would you choose",
    "which one would you recommend",
    "which one would you choose",
    "your favorite",
    "your favourite",
    "your pick",
    "your recommendation",
    "most popular room",
    "most popular",
    "popular room",
    "popular choice",
    "suggest a room",
    "suggest something",
    "recommended room",
    "top pick",
    "top choice",
    "better option",
    "better room",
    "compare",
    "comparison",
    "difference between",
    "vs",
    "versus",
    "which is better",
    "which is best",
    "what is best",
    "what's best",
    "what is the best",
    "which one is best",
    "why that",
    "why that one",
    "why this one",
    "why do you recommend",
    "why would you",
    "what makes it",
    "what's special",
    "what is special",
    "what's so good",
    "what's good about",
    "tell me about the rooms",
    "about the rooms",
    "room options",
    "what rooms",
    "what games",
    "what experiences",
    "which games",
)

# ---- FAQ phrases -----------------------------------------------------------
# General "how does X work", rules, mechanics questions.
# IMPORTANT: Only include phrases that are unambiguously questions/requests for
# information, not statements of fact (e.g. "first time" alone could be a
# statement). Statements without "?" should NOT be classified as questions.
_FAQ_PHRASES: Tuple[str, ...] = (
    "how does it work",
    "how does this work",
    "how do escape rooms work",
    "how does an escape room work",
    "what happens inside",
    "what happens if we fail",
    "what if we fail",
    "what if we don't escape",
    "what if we can't escape",
    "are we actually locked",
    "are we locked in",
    "are we locked",
    "actually locked",
    "locked inside",
    "locked in",
    "can kids play",
    "can children play",
    "is it suitable for kids",
    "is it good for kids",
    "kid friendly",
    "family friendly",
    "how long does it take",
    "how long is the game",
    "how long is the experience",
    "game duration",
    "experience duration",
    "what is an escape room",
    "what is escape room",
    "explain the rules",
    "what are the rules",
    "rules of the game",
    "game rules",
    "briefing",
    # NOTE: "before the game", "first time", "beginner", "none of us have done",
    # "never done" are intentionally NOT here — they are statements/context,
    # not question requests. They are handled by the recommendation/faq engines
    # in inbound_agent via the existing flow, not via the question-first guard.
    "what to expect",
    "what should we expect",
    "do you provide hints",
    "are hints available",
    "can we get hints",
    "game master",
    "staff help",
    "age limit",
    "minimum age",
    "age restriction",
    "age requirement",
    "do you need experience",
    "experience required",
    "prior experience",
    "walk in",
    "walk-in",
    "do we need to book",
    "advance booking",
)

# ---- Policy phrases --------------------------------------------------------
_POLICY_PHRASES: Tuple[str, ...] = (
    "cancellation policy",
    "cancel policy",
    "cancellation charges",
    "cancellation rules",
    "refund policy",
    "what if i cancel",
    "if i cancel",
    "can i cancel",
    "how do i cancel",
    "reschedule",
    "rescheduling",
    "postpone",
    "change the date",
    "change my booking",
    "modify booking",
    "late arrival",
    "running late",
    "arriving late",
    "be late",
    "what if we are late",
    "discount",
    "offer",
    "coupon",
    "promo",
    "promotional",
    "student discount",
    "group discount",
    "early bird",
    "refund",
    "money back",
)

# ---- Booking signal phrases ------------------------------------------------
# The customer is signalling readiness to book — these are NOT questions that
# need knowledge first; they progress the booking flow.
_BOOKING_SIGNAL_PHRASES: Tuple[str, ...] = (
    "tomorrow",
    "next weekend",
    "this weekend",
    "next week",
    "this week",
    "book now",
    "want to book",
    "i want to book",
    "can i book",
    "book it",
    "reserve",
    "make a booking",
    "how to book",
    "how do i book",
    "how do we book",
)

# ---- Voice repair phrases --------------------------------------------------
_REPAIR_PHRASES: Tuple[str, ...] = (
    "what?",
    "sorry?",
    "pardon?",
    "come again",
    "come again?",
    "didn't catch that",
    "didn't catch",
    "can you repeat",
    "could you repeat",
    "repeat that",
    "say that again",
    "i didn't hear",
    "what did you say",
    "what was that",
    "huh?",
    "huh",
    "excuse me?",
    "what do you mean",
    "i don't understand",
    "i dont understand",
    "unclear",
    "not sure what you mean",
    "confused",
)

# ---- Implicit question patterns --------------------------------------------
# Patterns that signal a question even without "?" — order matters, more
# specific patterns first.
# CRITICAL: All patterns use re.match (anchored to start of message) to avoid
# false positives on words appearing mid-sentence (e.g. "none of us have done
# escape rooms before" should NOT fire the 'before that' pattern).
_IMPLICIT_QUESTION_PATTERNS: Tuple[re.Pattern, ...] = (
    # "explain more", "explain that", "explain [something]"
    re.compile(r"^\s*explain\b", re.IGNORECASE),
    # "tell me more", "tell me about" (but NOT "tell me your name" during name collection)
    re.compile(r"^\s*tell\s+me\s+(more|about|why|how|what|which)\b", re.IGNORECASE),
    # "describe [something]"
    re.compile(r"^\s*describe\b", re.IGNORECASE),
    # "before that, [question]" — must be at start with comma/dash after "that"
    re.compile(r"^\s*before\s+that\s*[,\-]", re.IGNORECASE),
    # "first, [question]" — must have punctuation after "first"
    re.compile(r"^\s*first\s*[,\-]\s*", re.IGNORECASE),
    # "actually, [question]" — must have punctuation after "actually"
    re.compile(r"^\s*actually\s*[,\-]\s*", re.IGNORECASE),
    # "by the way, [question]"
    re.compile(r"^\s*by\s+the\s+way\b", re.IGNORECASE),
    # "wait, [question]"
    re.compile(r"^\s*wait\s*[,\-]\s*", re.IGNORECASE),
    # "quick question", "one question"
    re.compile(r"\b(quick|one|a)\s+question\b", re.IGNORECASE),
    # standalone "why"
    re.compile(r"^\s*why\s*[?.!]?\s*$", re.IGNORECASE),
    # "why [verb]", "why is", "why do", "why would"
    re.compile(r"^\s*why\s+(is|are|do|does|would|should|can|could|has|have|will|was|were)\b", re.IGNORECASE),
)

# Explicit question starters (matches beginning of message)
_QUESTION_STARTER_PATTERN = re.compile(
    r"^\s*(what|where|which|who|when|how|why|is|are|do|does|can|could|should|would|will|have|has|did|was|were)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class QuestionClassifier:
    """
    Lightweight deterministic classifier.

    Usage::
        analysis = QuestionClassifier.classify("explain more")
        if analysis.asked_question:
            # must answer before qualification
    """

    @classmethod
    def classify(cls, message: str) -> QuestionAnalysis:
        """Classify a customer message into a QuestionAnalysis."""
        lowered = message.lower().strip()
        stripped = lowered.rstrip(" .!?,;")

        # ---- 0. Repair check (highest priority) --------------------------
        if cls._matches_any(stripped, _REPAIR_PHRASES):
            return QuestionAnalysis(
                asked_question=True,
                question_type="repair",
                topic="repeat previous",
                can_answer_deterministically=True,
                needs_tool=False,
                needs_clarification=False,
            )

        # ---- 1. Booking signal check -------------------------------------
        # Booking signals are NOT questions that need knowledge first.
        if cls._matches_any(stripped, _BOOKING_SIGNAL_PHRASES) and not cls._ends_with_question_mark(lowered):
            # Only treat as booking signal if there's no other question pattern
            if not cls._is_implicit_question(lowered) and not cls._matches_any(stripped, _FAQ_PHRASES + _POLICY_PHRASES + _RECOMMENDATION_PHRASES):
                return QuestionAnalysis(
                    asked_question=False,
                    question_type="booking_signal",
                    topic="booking intent",
                    can_answer_deterministically=False,
                    needs_tool=True,
                    needs_clarification=False,
                )

        # ---- 2. Category detection ---------------------------------------
        if cls._matches_any(stripped, _FAQ_PHRASES) or cls._is_faq_question(lowered):
            return QuestionAnalysis(
                asked_question=True,
                question_type="faq",
                topic=cls._extract_faq_topic(lowered),
                can_answer_deterministically=True,
                needs_tool=False,
                needs_clarification=False,
            )

        if cls._matches_any(stripped, _RECOMMENDATION_PHRASES) or cls._is_recommendation_question(lowered):
            return QuestionAnalysis(
                asked_question=True,
                question_type="recommendation",
                topic=cls._extract_recommendation_topic(lowered),
                can_answer_deterministically=True,
                needs_tool=False,
                needs_clarification=False,
            )

        if cls._matches_any(stripped, _POLICY_PHRASES):
            return QuestionAnalysis(
                asked_question=True,
                question_type="policy",
                topic=cls._extract_policy_topic(lowered),
                can_answer_deterministically=True,
                needs_tool=False,
                needs_clarification=False,
            )

        # ---- 3. Implicit question detection ------------------------------
        if cls._is_implicit_question(lowered):
            return QuestionAnalysis(
                asked_question=True,
                question_type="unknown",
                topic=lowered[:60],
                can_answer_deterministically=False,
                needs_tool=False,
                needs_clarification=True,
            )

        # ---- 4. Explicit question mark / starter -------------------------
        has_q_mark = cls._ends_with_question_mark(lowered)
        has_q_starter = bool(_QUESTION_STARTER_PATTERN.match(lowered))
        if has_q_mark or has_q_starter:
            # Try to assign a sub-category
            question_type, topic, can_answer = cls._categorise_generic_question(lowered)
            return QuestionAnalysis(
                asked_question=True,
                question_type=question_type,
                topic=topic,
                can_answer_deterministically=can_answer,
                needs_tool=False,
                needs_clarification=not can_answer,
            )

        # ---- 5. Not a question -------------------------------------------
        return QuestionAnalysis(
            asked_question=False,
            question_type="",
            topic="",
            can_answer_deterministically=False,
            needs_tool=False,
            needs_clarification=False,
        )

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _matches_any(text: str, phrases: Tuple[str, ...]) -> bool:
        """Return True if any phrase is contained in text."""
        return any(phrase in text for phrase in phrases)

    @staticmethod
    def _ends_with_question_mark(lowered: str) -> bool:
        return lowered.rstrip() .endswith("?")

    @staticmethod
    def _is_implicit_question(lowered: str) -> bool:
        """Return True if any implicit question pattern fires."""
        for pat in _IMPLICIT_QUESTION_PATTERNS:
            if pat.search(lowered):
                return True
        return False

    @staticmethod
    def _is_recommendation_question(lowered: str) -> bool:
        """Detect recommendation questions that don't appear verbatim in the list."""
        # "your favourite [thing]" / "what's your [thing]"
        if re.search(r"\b(your\s+(favorite|favourite|pick|choice|recommendation|recommendation))\b", lowered):
            return True
        # "which [room|option|game|experience]"
        if re.search(r"\bwhich\s+(room|option|game|experience|one)\b", lowered):
            return True
        # "what [room|game|experience] would you"
        if re.search(r"\bwhat\s+(room|game|experience)\b.*\b(you|recommend|suggest)\b", lowered):
            return True
        # "recommend" or "suggest" anywhere
        if re.search(r"\b(recommend|suggest)\b", lowered):
            return True
        # standalone "why" followed by a recommendation context
        if re.search(r"\bwhy\b", lowered) and re.search(r"\b(that|this|it|room|recommend|suggest|option)\b", lowered):
            return True
        return False

    @staticmethod
    def _is_faq_question(lowered: str) -> bool:
        """Detect FAQ questions beyond the phrase list."""
        if re.search(r"\bhow\s+does\b", lowered):
            return True
        if re.search(r"\bwhat\s+(happens|is\s+an\s+escape|are\s+the\s+rules)\b", lowered):
            return True
        if re.search(r"\bhow\s+long\b", lowered):
            return True
        if re.search(r"\bcan\s+(kids|children|we)\s+(play|join|participate)\b", lowered):
            return True
        return False

    @staticmethod
    def _categorise_generic_question(lowered: str) -> Tuple[str, str, bool]:
        """Assign type/topic/answerability to a generic question."""
        if re.search(r"\b(cancel|refund|reschedule|discount|late|policy)\b", lowered):
            return "policy", "cancellation/policy", True
        if re.search(r"\b(room|game|experience|escape|play|puzzle)\b", lowered):
            return "faq", "escape room general", True
        if re.search(r"\b(recommend|suggest|best|popular|which)\b", lowered):
            return "recommendation", "room/option recommendation", True
        if re.search(r"\b(location|where|address|park)\b", lowered):
            return "faq", "location/parking", True
        if re.search(r"\b(book|reserve|slot|available|availability)\b", lowered):
            return "booking_signal", "booking inquiry", False
        return "unknown", lowered[:60], False

    @staticmethod
    def _extract_recommendation_topic(lowered: str) -> str:
        for keyword in ("room", "game", "experience", "option"):
            if keyword in lowered:
                return f"room/experience recommendation"
        return "recommendation"

    @staticmethod
    def _extract_faq_topic(lowered: str) -> str:
        if "lock" in lowered or "locked" in lowered:
            return "are we locked in"
        if "fail" in lowered or "don't escape" in lowered:
            return "what happens if we fail"
        if "kid" in lowered or "child" in lowered:
            return "kids suitability"
        if "long" in lowered or "duration" in lowered:
            return "game duration"
        if "rule" in lowered or "how does" in lowered:
            return "how escape rooms work"
        if "age" in lowered:
            return "age requirement"
        return "faq general"

    @staticmethod
    def _extract_policy_topic(lowered: str) -> str:
        if "cancel" in lowered or "refund" in lowered:
            return "cancellation/refund"
        if "reschedule" in lowered or "postpone" in lowered or "change" in lowered:
            return "rescheduling"
        if "late" in lowered:
            return "late arrival"
        if "discount" in lowered or "offer" in lowered:
            return "discounts/offers"
        return "policy general"
