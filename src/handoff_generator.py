from __future__ import annotations

import json


class HandoffGenerator:
    REQUIRED_BY_INTENT = {
        "escape_room_inquiry": ["age_group", "participants", "location", "customer_name", "phone"],
        "birthday_party": ["location", "participants", "preferred_date", "customer_name", "phone"],
        "corporate_event": ["company_size", "location", "preferred_date", "customer_name", "phone"],
        "bachelor_party": ["participants", "location", "preferred_date", "customer_name", "phone"],
        "farewell_party": ["participants", "location", "preferred_date", "customer_name", "phone"],
        "couple_event": ["participants", "location", "preferred_date", "customer_name", "phone"],
        "virtual_event": ["participants", "preferred_date", "customer_name", "phone"],
        "cancellation_request": ["customer_name", "phone"],
    }

    def ready(self, memory: dict) -> bool:
        intent = memory.get("intent", "")
        if intent in ("", "general_faq"):
            return False
        return all(memory.get(field) for field in self.REQUIRED_BY_INTENT.get(intent, []))

    def generate(self, memory: dict) -> dict:
        customer_name = memory.get("customer_name", "")
        intent = memory.get("intent", "")
        location = memory.get("location", "")
        participants = memory.get("participants", "")
        age_group = memory.get("age_group", "")
        company_size = memory.get("company_size", "")
        event_type = memory.get("event_type", "")
        recommended_option = memory.get("recommended_option", "")
        preferred_date = memory.get("preferred_date", "")

        summary_bits = []
        if customer_name:
            summary_bits.append(f"Customer name is {customer_name}.")
        if intent:
            summary_bits.append(f"Intent is {intent}.")
        if event_type:
            summary_bits.append(f"Customer is interested in {event_type}.")
        if location:
            summary_bits.append(f"Preferred location is {location}.")
        if participants:
            summary_bits.append(f"Group size is approximately {participants}.")
        if age_group:
            summary_bits.append(f"Age group is {age_group}.")
        if company_size:
            summary_bits.append(f"Company size is approximately {company_size}.")
        if preferred_date:
            summary_bits.append(f"Preferred date is {preferred_date}.")
        if recommended_option:
            summary_bits.append(f"Recommended option: {recommended_option}.")

        return {
            "customer_name": customer_name,
            "intent": intent,
            "location": location,
            "participants": participants,
            "age_group": age_group,
            "company_size": company_size,
            "event_type": event_type,
            "sentiment": memory.get("sentiment", "neutral"),
            "recommended_option": recommended_option,
            "summary": " ".join(summary_bits).strip(),
        }

    def to_json(self, memory: dict) -> str:
        if not self.ready(memory):
            return json.dumps(
                {
                    "ready": False,
                    "summary": "Not enough information has been collected for a handoff yet.",
                },
                indent=2,
            )
        return json.dumps(self.generate(memory), indent=2)
