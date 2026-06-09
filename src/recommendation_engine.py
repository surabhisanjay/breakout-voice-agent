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
            return bool(memory.get("age_group") and memory.get("participants"))
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

        text = " ".join(
            [
                message.lower(),
                str(memory.get("event_type", "")).lower(),
                str(memory.get("participants", "")).lower(),
                str(memory.get("age_group", "")).lower(),
                str(memory.get("experience_level", "")).lower(),
                str(memory.get("intent", "")).lower(),
            ]
        )

        age = self._extract_age(text)
        intent = memory.get("intent", "")

        if "beginner" in text or "first time" in text or "first-time" in text or "never done" in text:
            return Recommendation(
                "Murder Mystery or Hostage",
                "Murder Mystery is easier to start with, while Hostage adds a bit more urgency.",
            )

        if age is not None and 5 <= age <= 8:
            return Recommendation(
                "The Wizarding Championship",
                "It is listed for children aged 5 to 8 and supports up to 8 players.",
            )

        if age is not None and 9 <= age <= 13:
            return Recommendation(
                "Murder Mystery and Hostage",
                "They are suitable options for children aged 9 and above.",
            )

        if "challenging" in text or "challenge" in text or "hard" in text or "hardest" in text or "adults" in text or "adult" in text:
            return Recommendation(
                "Classified, Undercover, Prison Break, or Bomb Defusal",
                "These are suitable choices for adults looking for a more challenging experience.",
            )

        if intent == "birthday_party":
            location = str(memory.get("location", ""))
            capacity_note = (
                " Whitefield can accommodate approximately 35-40 guests."
                if location.lower() == "whitefield"
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
