from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Recommendation:
    option: str
    reason: str


class RecommendationEngine:
    def can_recommend(self, memory: dict) -> bool:
        intent = memory.get("intent", "")
        if intent == "escape_room_inquiry":
            return True
        if intent == "birthday_party":
            return bool(memory.get("location") and memory.get("participants") and memory.get("preferred_date"))
        if intent == "corporate_event":
            return bool(memory.get("company_size") and memory.get("location") and memory.get("preferred_date"))
        if intent in {"bachelor_party", "farewell_party", "couple_event"}:
            return bool(memory.get("participants") and memory.get("location") and memory.get("preferred_date"))
        if intent == "virtual_event":
            return bool(memory.get("participants") and memory.get("preferred_date"))
        return False

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
        if (age is not None and 5 <= age <= 8) or age_group == "kids" or "kids" in text:
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

        # 3. Beginner / first time
        if "beginner" in text or "first time" in text or "first-time" in text or "never done" in text or experience_level == "beginner":
            return Recommendation(
                "Murder Mystery or Hostage",
                "Murder Mystery is easier to start with as a classic detective investigation, while Hostage adds a bit of urgency."
            )

        # 4. Large group size / Corporate
        if (participants and isinstance(participants, int) and participants > 8) or intent == "corporate_event":
            return Recommendation(
                "Escape Rooms and Scavenger Hunt",
                "A combination of multiple rooms like Undercover and Bomb Defusal, alongside a Scavenger Hunt, works best for larger teams to keep everyone engaged."
            )

        # 5. Challenging / Adults / Experienced
        if "challenging" in text or "hard" in text or "adults" in text or "experienced" in text or "challenging" in preferences:
            if location.lower() == "whitefield" or str(location).lower() == "whitefield":
                return Recommendation(
                    "Classified, Undercover, or Bomb Defusal",
                    "These are highly immersive, higher-difficulty rooms perfect for adults or experienced players looking for a challenge."
                )
            if location.lower() == "jp nagar" or str(location).lower() == "jp nagar":
                return Recommendation(
                    "Prison Break",
                    "Prison Break is our hardest and most mission-oriented room in JP Nagar."
                )
            return Recommendation(
                "Classified, Undercover, Prison Break, or Bomb Defusal",
                "These are suitable choices for adults looking for a more challenging experience."
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

        return Recommendation(
            "Murder Mystery, Hostage, Prison Break, Classified, Undercover, or Bomb Defusal",
            "The final choice depends on group age, group size, and challenge preference.",
        )

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
