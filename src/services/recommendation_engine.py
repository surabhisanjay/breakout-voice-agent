from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Recommendation:
    option: str
    reason: str


class RecommendationEngine:
    # Verified against Kreeda get_available_games. Numbered duplicate rooms are
    # collapsed to their customer-facing name and widest supported range.
    ROOM_INVENTORY = {
        "koramangala": {
            "Murder Mystery": (2, 7), "Hostage": (2, 7), "Classified": (3, 7),
            "Undercover": (3, 8), "Curse of the Pharaoh": (4, 8),
            "Forbidden Forest": (4, 10),
        },
        "jp nagar": {
            "Murder Mystery": (2, 7), "Hostage": (2, 7), "Missile Attack": (3, 8),
        },
        "whitefield": {
            "Murder Mystery": (2, 7), "Hostage": (2, 7), "Undercover": (3, 8),
            "Bomb Defusal": (4, 8),
        },
    }

    def can_recommend(self, memory: dict) -> bool:
        intent = memory.get("intent", "")
        if intent == "escape_room_inquiry":
            return any(
                memory.get(field)
                for field in (
                    "participants",
                    "age_group",
                    "experience_level",
                    "challenge_preference",
                )
            )
        if intent == "birthday_party":
            return bool(memory.get("location") and memory.get("participants") and memory.get("preferred_date"))
        if intent == "corporate_event":
            return bool(memory.get("company_size") and memory.get("location") and memory.get("preferred_date"))
        if intent in {"bachelor_party", "farewell_party", "couple_event"}:
            return bool(memory.get("participants") and memory.get("location") and memory.get("preferred_date"))
        if intent == "virtual_event":
            return bool(memory.get("participants") and memory.get("preferred_date"))
        return False

    @classmethod
    def available_options(cls, memory: dict, limit: int = 6) -> list[str]:
        location = str(memory.get("location", "")).lower()
        participants = int(memory.get("participants") or 0)
        inventories = (
            [cls.ROOM_INVENTORY[location]]
            if location in cls.ROOM_INVENTORY
            else list(cls.ROOM_INVENTORY.values())
        )
        preferred_order = [
            "Murder Mystery", "Hostage", "Undercover", "Classified",
            "Bomb Defusal", "Missile Attack", "Curse of the Pharaoh", "Forbidden Forest",
        ]
        available: list[str] = []
        for room in preferred_order:
            supported = any(
                room in inventory
                and (not participants or inventory[room][0] <= participants <= inventory[room][1])
                for inventory in inventories
            )
            if supported:
                available.append(room)
        return available[:limit]

    def recommend(self, message: str, memory: dict) -> Recommendation:
        if not self.can_recommend(memory):
            return Recommendation("", "")

        intent = memory.get("intent", "")
        participants = memory.get("participants", "")
        age_group = memory.get("age_group", "")
        experience_level = memory.get("experience_level", "")
        location = memory.get("location", "")
        event_type = memory.get("event_type", "")
        preferences = memory.get("customer_preferences", [])
        concerns = memory.get("concerns", [])

        text = " ".join(
            [
                message.lower(),
                str(event_type).lower(),
                str(participants).lower(),
                str(age_group).lower(),
                str(memory.get("age_detail", "")).lower(),
                str(experience_level).lower(),
                str(memory.get("challenge_preference", "")).lower(),
                str(location).lower(),
                str(intent).lower(),
            ]
        )

        # 1. Puzzle dislike concern
        if "no_puzzles" in concerns or "no puzzles" in text or "puzzle" in text:
            return Recommendation(
                "Murder Mystery",
                "Murder Mystery focuses on finding physical clues and investigating a story rather than purely abstract logic, making it great for players who don't like standard puzzles."
            )

        # 2. Kids-centric (family groups / children)
        age = self._extract_age(text)
        explicit_children = bool(re.search(r"\b(?:kids?|children|child)\b", message.lower()))
        if explicit_children or (
            age_group != "teens"
            and ((age is not None and 5 <= age <= 8) or age_group == "kids" or "kids" in text)
        ):
            if age is not None and 5 <= age <= 8:
                return Recommendation(
                    "The Wizarding Championship",
                    "It is designed specifically for children aged 5 to 8 and is highly interactive."
                )
            if age is not None and 9 <= age <= 13:
                return Recommendation(
                    "Murder Mystery and Hostage",
                    "They are suitable options for children aged 9 and above."
                )
            # Family group (kids + adults) or older kids
            return Recommendation(
                "Murder Mystery or Hostage",
                "These rooms are extremely family-friendly and work well for a mix of kids and adults."
            )

        # 3. Event flows remain separate from single-room recommendations.
        if intent == "corporate_event":
            return Recommendation(
                "Corporate event coordination",
                "Corporate groups need event-level capacity and scheduling rather than a single-room assumption.",
            )
        if participants and isinstance(participants, int) and participants > 8:
            return Recommendation(
                "Multi-room event coordination",
                "No single escape room can hold this group, so room splitting must be confirmed before booking.",
            )

        # 4. Beginner / first time
        if "beginner" in text or "first time" in text or "first-time" in text or "never done" in text or experience_level == "beginner":
            return self._validated_room_recommendation(
                ("Murder Mystery", "Hostage"),
                location,
                participants,
                "Murder Mystery is easier to start with as a classic detective investigation, while Hostage adds a bit of urgency.",
            )

        # 5. Challenging / Adults / Experienced
        if "challenging" in text or "hard" in text or "adults" in text or "experienced" in text or "challenging" in preferences:
            candidates = {
                "whitefield": ("Undercover", "Bomb Defusal", "Murder Mystery", "Hostage"),
                "jp nagar": ("Missile Attack", "Murder Mystery", "Hostage"),
                "koramangala": ("Classified", "Undercover", "Murder Mystery", "Hostage"),
            }.get(
                str(location).lower(),
                ("Classified", "Bomb Defusal", "Undercover", "Prison Break"),
            )
            return self._validated_room_recommendation(
                candidates,
                location,
                participants,
                "These rooms match the selected location and supported group size.",
            )

        if "challenging" in text or "challenge" in text or "hard" in text or "hardest" in text:
            return Recommendation(
                "challenging",
                "The group wants a more challenging escape room.",
            )

        if "story" in text or "mystery" in text or "investigation" in text:
            return Recommendation(
                "story",
                "The group prefers a story-driven or investigation-style room.",
            )

        if "adult" in text or "adults" in text:
            return Recommendation(
                "adults",
                "Adult group context is available for a stronger recommendation.",
            )

        if intent == "birthday_party":
            location_str = str(memory.get("location", ""))
            capacity_note = (
                " Whitefield can accommodate approximately 35-40 guests."
                if location_str.lower() == "whitefield"
                else ""
            )
            return Recommendation(
                "Birthday Party Package",
                f"It includes escape room activities and party support.{capacity_note}",
            )

        if intent == "corporate_event":
            return Recommendation(
                "Corporate Event Package",
                "It is designed for team-building, employee engagement, and group challenges.",
            )

        if intent == "bachelor_party":
            return Recommendation(
                "Prison Break, Bomb Defusal, Undercover, or Classified",
                "These are high-energy rooms that suit adult celebration groups.",
            )

        if intent == "couple_event":
            return Recommendation(
                "Murder Mystery, Prison Break, or Undercover",
                "Murder Mystery is lighter, while Prison Break and Undercover add more challenge.",
            )

        if intent == "virtual_event":
            return Recommendation(
                "Virtual Event Package",
                "It is the best fit for remote or distributed groups.",
            )

        if intent == "farewell_party":
            return Recommendation(
                "Escape Room Event Package",
                "It works well for friend, college, school, or office farewell groups.",
            )

        # This sentinel preserves the existing qualification path. It is not
        # presented as inventory; location-aware branches above supply the
        # customer-facing room choices.
        return Recommendation(
            "Murder Mystery, Hostage, Prison Break, Classified, Undercover, or Bomb Defusal",
            "The final choice depends on group age, group size, and challenge preference.",
        )

    @classmethod
    def _validated_room_recommendation(
        cls,
        candidates: tuple[str, ...],
        location: str,
        participants: int | str,
        reason: str,
    ) -> Recommendation:
        inventory = cls.ROOM_INVENTORY.get(str(location).lower())
        if not inventory:
            return Recommendation(" or ".join(candidates[:2]), reason)
        count = int(participants) if participants else 0
        valid = [
            room
            for room in candidates
            if room in inventory and (not count or inventory[room][0] <= count <= inventory[room][1])
        ]
        if not valid:
            return Recommendation(
                "Multi-room event coordination",
                "No room at this location supports the current group size as a single booking.",
            )
        return Recommendation(" or ".join(valid[:2]), reason)

    @staticmethod
    def _extract_age(text: str) -> int | None:
        patterns = [
            r"aged?\s+(\d{1,2})",
            r"(\d{1,2})\s*years?\s*old",
            r"(\d{1,2})\s*years?",
            r"age\s+(\d{1,2})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return int(match.group(1))
        return None
