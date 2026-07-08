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
        if memory.get("room"):
            return False
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
        if memory.get("room"):
            return Recommendation("", "")
        if not self.can_recommend(memory):
            return self.deterministic_fallback(message, memory)

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

        if self._is_rejected(memory):
            return self.deterministic_fallback(message, memory)
        if "couple" in text:
            return Recommendation(
                "Murder Mystery",
                "Murder Mystery is story-led and gives two players steady teamwork and investigation.",
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
                    "Murder Mystery",
                    "Murder Mystery is the most approachable room for children aged 9 and above."
                )
            # Family group (kids + adults) or older kids
            return Recommendation(
                "Murder Mystery",
                "Murder Mystery is the most approachable family-friendly fit for this group."
            )

        # 3. Event flows remain separate from single-room recommendations.
        if intent == "corporate_event":
            return Recommendation(
                "Corporate event coordination",
                "Corporate groups need event-level capacity and scheduling rather than a single-room assumption.",
            )
        if self._participant_count(participants) > 8:
            return Recommendation(
                "Multi-room event coordination",
                "No single escape room can hold this group, so room splitting must be confirmed before booking.",
            )

        first_time = bool(re.search(r"\b(?:first[- ]?time|beginner|never done)\b", text)) or experience_level == "beginner"
        thrill = bool(re.search(r"\b(?:thrill|thrilling|urgent|pressure|intense|adrenaline|harder?|difficult|challenging|challenge|not too easy|not easy)\b", text))

        # 4. First-time thrill seekers need a controlled step up, not the
        # hardest room. At Whitefield this is Hostage.
        if first_time and thrill:
            return self._validated_room_recommendation(
                ("Hostage", "Murder Mystery"),
                location,
                participants,
                "Hostage gives first-timers a more urgent mission feel while staying suitable for a new adult group.",
                limit=1,
            )

        # 5. Beginner / first time
        if first_time:
            return self._validated_room_recommendation(
                ("Murder Mystery", "Hostage"),
                location,
                participants,
                "Murder Mystery is beginner-friendly and gives a first time group a clear, story-led introduction to escape rooms.",
                limit=1,
            )

        # 5. Challenging / Adults / Experienced
        if "challenging" in text or "hard" in text or "adults" in text or "experienced" in text or "challenging" in preferences:
            candidates = {
                "whitefield": ("Undercover", "Bomb Defusal", "Murder Mystery", "Hostage"),
                "jp nagar": ("Missile Attack", "Murder Mystery", "Hostage"),
                "koramangala": ("Classified", "Undercover", "Murder Mystery", "Hostage"),
            }.get(
                str(location).lower(),
                ("Hostage", "Classified", "Bomb Defusal", "Undercover"),
            )
            return self._validated_room_recommendation(
                candidates,
                location,
                participants,
                "These rooms match the selected location and supported group size.",
                limit=1,
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

        return self.deterministic_fallback(message, memory)

    @classmethod
    def deterministic_fallback(cls, message: str, memory: dict) -> Recommendation:
        """Return a usable deterministic recommendation for every input state."""
        if memory.get("room"):
            return Recommendation("", "")
        participants = cls._participant_count(memory.get("participants"))
        location = str(memory.get("location", "")).lower()
        text = " ".join(
            (message.lower(), str(memory.get("experience_level", "")).lower(),
             str(memory.get("challenge_preference", "")).lower(), str(memory.get("age_group", "")).lower())
        )
        if memory.get("last_discussed_topic") == "locations" and not memory.get("location"):
            return Recommendation(
                "Koramangala",
                "Koramangala has the broadest challenge mix for customers comparing branches.",
            )
        if participants > 8:
            return Recommendation(
                "Multi-room event coordination",
                "This group is larger than a single room capacity, so the booking should be split across suitable rooms.",
            )

        inventory = cls.ROOM_INVENTORY.get(location, {})
        candidates = cls.available_options(memory, limit=8)
        rejected = {str(option) for option in memory.get("rejected_options", [])}
        candidates = [option for option in candidates if option not in rejected]
        if not candidates:
            candidates = ["Hostage", "Undercover", "Murder Mystery"]

        first_time = bool(re.search(r"\b(?:first[- ]?time|beginner|never done)\b", text))
        thrill = bool(re.search(r"\b(?:thrill|thrilling|urgent|pressure|intense|adrenaline|hard|difficult|challenging|challenge)\b", text))
        kids = bool(re.search(r"\b(?:kids?|children|child)\b", text))
        if "couple" in text:
            room = "Murder Mystery" if "Murder Mystery" in candidates else candidates[0]
            return Recommendation(room, f"{room} is story-led and gives two players steady teamwork and investigation.")
        if first_time and thrill and "Hostage" in candidates:
            return Recommendation("Hostage", "Hostage gives first-timers an urgent, thrilling mission without requiring expert escape-room experience.")
        if kids and "Murder Mystery" in candidates:
            return Recommendation("Murder Mystery", "Murder Mystery is the most approachable family-friendly starting point.")
        if thrill:
            priority = ["Hostage", "Bomb Defusal", "Undercover", "Classified", "Missile Attack", "Murder Mystery"]
            room = next((option for option in priority if option in candidates), candidates[0])
            return Recommendation(room, f"{room} is the stronger pressure and teamwork fit for this group.")
        if first_time and "Murder Mystery" in candidates:
            return Recommendation("Murder Mystery", "Murder Mystery is beginner-friendly and a great first escape-room experience.")
        room = "Murder Mystery" if "Murder Mystery" in candidates else candidates[0]
        location_note = " at the selected location" if inventory else ""
        return Recommendation(room, f"{room} is the most reliable all-round room choice{location_note}.")

    @classmethod
    def _validated_room_recommendation(
        cls,
        candidates: tuple[str, ...],
        location: str,
        participants: int | str,
        reason: str,
        limit: int = 2,
    ) -> Recommendation:
        inventory = cls.ROOM_INVENTORY.get(str(location).lower())
        if not inventory:
            return Recommendation(" or ".join(candidates[:limit]), reason)
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
        return Recommendation(" or ".join(valid[:limit]), reason)

    @staticmethod
    def _participant_count(value: int | str | None) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _is_rejected(memory: dict) -> bool:
        current = str(memory.get("recommended_option") or memory.get("room") or "")
        return bool(current and current in {str(option) for option in memory.get("rejected_options", [])})

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
