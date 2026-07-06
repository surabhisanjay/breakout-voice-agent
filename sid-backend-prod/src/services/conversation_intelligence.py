from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .question_classifier import QuestionClassifier, QuestionAnalysis

if TYPE_CHECKING:
    from ..memory.conversation_memory import ConversationMemory


# Re-export so callers can import from here without knowing the module layout.
__all__ = ["ConversationIntelligenceLayer", "QuestionClassifier", "QuestionAnalysis"]


class ConversationIntelligenceLayer:
    def __init__(self, memory: ConversationMemory):
        self.memory = memory

    def process_utterance(self, raw_message: str) -> str:
        """
        Preprocesses the customer's utterance before intent classification and routing.
        1. Resolves pronouns/deictic references (e.g., "Which one?", "Would that work for kids?").
        2. Detects and stores customer concerns and preferences in memory.
        Returns the resolved/enriched utterance.
        """
        lowered = raw_message.lower().strip(" .!?")

        # 1. Extract concerns, preferences, and experience level
        self._extract_concerns_and_preferences(lowered)

        # 2. Perform reference resolution
        resolved_message = self._resolve_references(raw_message)
        return resolved_message

    def _extract_concerns_and_preferences(self, lowered: str) -> None:
        """Scans the message for user concerns or preferences and updates memory."""
        # Check experience level
        beginner_terms = (
            "never done", "first time", "first-time", "beginner", "no experience",
            "never tried", "none of us have done", "haven't done", "havent done",
            "first timer", "first-timer", "never played"
        )
        if any(term in lowered for term in beginner_terms):
            self.memory.data["experience_level"] = "beginner"

        # Check concern: dislikes puzzles
        puzzle_dislike = (
            "don't like puzzles", "don't enjoy puzzles", "dont like puzzles",
            "not enjoy puzzles", "no puzzles", "dislike puzzles", "hate puzzles",
            "don't like riddle", "not a fan of puzzles"
        )
        if any(term in lowered for term in puzzle_dislike):
            if "no_puzzles" not in self.memory.data["concerns"]:
                self.memory.data["concerns"].append("no_puzzles")

        # Check preference: challenging
        challenging_terms = ("challenging", "hard", "difficult", "hardest", "complex", "brain teaser")
        if any(term in lowered for term in challenging_terms):
            if "challenging" not in self.memory.data["customer_preferences"]:
                self.memory.data["customer_preferences"].append("challenging")

        # Check preference: story / adventure
        story_terms = ("story", "theme", "relaxed", "adventure", "mystery", "immersive")
        if any(term in lowered for term in story_terms):
            if "story-driven" not in self.memory.data["customer_preferences"]:
                self.memory.data["customer_preferences"].append("story-driven")

        self.memory.save()

    def _resolve_references(self, message: str) -> str:
        """Resolves pronouns like 'one', 'that one', 'that' to specific discussed rooms."""
        lowered = message.lower()
        discussed = self.memory.data.get("discussed_options", [])
        if not discussed:
            return message

        # 1. "which one" -> "which of [room1] or [room2]"
        # "Which is best?" stays untouched so the agent can resolve it
        # against the last discussed topic, including food and packages.
        if "which one" in lowered and not any(term in lowered for term in ("recommend", "suggest", "choose", "pick")):
            if len(discussed) >= 2:
                resolved_rooms = " or ".join(discussed[-2:])
                return re.sub(r"which one", f"which of {resolved_rooms}", message, flags=re.IGNORECASE)
            elif len(discussed) == 1:
                return re.sub(r"which one", f"which of {discussed[0]}", message, flags=re.IGNORECASE)

        # 2. "what about that one" / "that one"
        if "what about that one" in lowered:
            last_room = discussed[-1]
            return re.sub(r"what about that one", f"what about {last_room}", message, flags=re.IGNORECASE)
        elif "that one" in lowered:
            last_room = discussed[-1]
            return re.sub(r"that one", last_room, message, flags=re.IGNORECASE)

        # 3. "would that work for kids" / "is that better"
        if (
            "would that work" in lowered
            or "is that better" in lowered
            or "is that one better" in lowered
            or "work for kids" in lowered
        ):
            if "that" in lowered:
                last_room = discussed[-1]
                # Regex word boundary match for 'that'
                return re.sub(r"\bthat\b", last_room, message, flags=re.IGNORECASE)

        return message

    # ---------------------------------------------------------------------- #
    # Question classification                                                 #
    # ---------------------------------------------------------------------- #

    def classify_question(self, message: str) -> QuestionAnalysis:
        """
        Classify whether the customer's message is a question, and what type.

        Returns a QuestionAnalysis dataclass with:
          asked_question      — True if the customer asked something
          question_type       — recommendation | faq | policy | repair | booking_signal | unknown
          topic               — human-readable topic string
          can_answer_deterministically — whether knowledge is available
          needs_tool          — whether a booking/availability tool is required
          needs_clarification — whether the agent should ask a follow-up
        """
        return QuestionClassifier.classify(message)

    def get_consultative_selling_reply(self, message: str) -> str | None:
        """
        Returns consultative advice and selling lines based on extracted concerns or preferences.
        """
        lowered = message.lower()
        concerns = self.memory.data.get("concerns", [])
        exp = self.memory.data.get("experience_level", "")
        intent = self.memory.data.get("intent", "")

        # 1. Friend dislikes puzzles concern
        if "no_puzzles" in concerns and ("puzzle" in lowered or "fun" in lowered or "enjoy" in lowered):
            # Acknowledge concern, reassure, suggest Murder Mystery
            # Remove concern once answered to avoid infinite looping
            if "no_puzzles" in self.memory.data["concerns"]:
                self.memory.data["concerns"].remove("no_puzzles")
                self.memory.save()
            return (
                "That's actually quite common! Escape rooms at Breakout aren't just about hard puzzles — "
                "they are highly immersive, story-driven adventures where team members search for physical clues, "
                "decode mysteries, and collaborate. Even those who don't love puzzles usually have a blast "
                "finding hidden compartments and helping the team. For your group, I'd suggest Murder Mystery "
                "because it's highly collaborative and investigative rather than purely logic-heavy."
            )

        # 2. First timer consultative advice
        if exp == "beginner" and not self.memory.data.get("discussed_options") and ("recommend" in lowered or "first time" in lowered or "suggest" in lowered or "never" in lowered or "none of us" in lowered):
            room_keywords = ("murder", "hostage", "wizarding", "pharaoh", "forest", "classified", "undercover", "prison", "bomb")
            if not any(room in lowered for room in room_keywords):
                if self.memory.data.get("participants") and not self.memory.data.get("age_group"):
                    follow_up = "What age group are the players: adults, kids, or a mix?"
                elif not self.memory.data.get("participants"):
                    follow_up = "How many people are joining?"
                elif not self.memory.data.get("location"):
                    follow_up = "Which location would you like to visit?"
                else:
                    follow_up = "Would you like me to check availability?"
                return (
                    "No worries. I'd probably start with Murder Mystery for a first visit. "
                    f"Hostage is the more urgent option if the group wants extra pressure. {follow_up}"
                )

        return None
