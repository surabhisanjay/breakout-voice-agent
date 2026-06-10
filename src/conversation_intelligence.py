from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .conversation_memory import ConversationMemory


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
        if "which one" in lowered:
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
        if "would that work" in lowered or "is that better" in lowered or "work for kids" in lowered:
            if "that" in lowered:
                last_room = discussed[-1]
                # Regex word boundary match for 'that'
                return re.sub(r"\bthat\b", last_room, message, flags=re.IGNORECASE)

        return message

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
        if exp == "beginner" and ("recommend" in lowered or "first time" in lowered or "suggest" in lowered or "never" in lowered or "none of us" in lowered):
            room_keywords = ("murder", "hostage", "wizarding", "pharaoh", "forest", "classified", "undercover", "prison", "bomb")
            if not any(room in lowered for room in room_keywords):
                return (
                    "No problem at all! We love hosting first-time players. I'd usually recommend starting with "
                    "Murder Mystery because it gives you investigation-style puzzles and teamwork without feeling "
                    "overwhelming. If you want something slightly more exciting, Hostage is a great alternative. "
                )

        return None
