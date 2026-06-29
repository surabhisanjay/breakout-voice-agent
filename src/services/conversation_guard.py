from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.agent_response import AgentResponse
from ..memory.conversation_memory import ConversationMemory
from .venue_policy import get_venue_policy
from .recommendation_engine import RecommendationEngine


@dataclass(frozen=True)
class GuardDecision:
    response: str
    category: str
    keep_active_agent: bool = True


class ConversationGuard:
    """Deterministic direct-answer layer that runs before agent routing."""

    def __init__(self, memory: ConversationMemory) -> None:
        self.memory = memory
        self.recommender = RecommendationEngine()

    def evaluate(self, message: str, active_agent: str) -> AgentResponse | None:
        # Check state-changing acceptance against the original utterance before
        # reference expansion turns "that one" into a multi-room comparison.
        if self._is_booking_flow_message(message.lower().strip()):
            return None
        normalized = self.memory.apply_reference_aliases(
            self.memory.normalize_entity_aliases(message)
        )
        decision = self._decision(normalized, active_agent)
        if not decision:
            return None

        self.memory.add_turn("customer", normalized)
        self.memory.data["conversation_mode"] = "faq" if decision.category != "recommendation" else "sales"
        self.memory.data["last_guard_category"] = decision.category
        self.memory.save()
        self.memory.add_turn("agent", decision.response)
        next_agent = active_agent if decision.keep_active_agent and active_agent else "inbound_agent"
        return AgentResponse(
            response=decision.response,
            intent=str(self.memory.data.get("intent") or "general_faq"),
            next_agent=next_agent,
            should_handoff=False,
            state=self.memory.as_state(),
            recommendation={
                "option": str(self.memory.data.get("recommended_option") or ""),
                "reason": "",
            },
            debug={"conversation_guard": {"category": decision.category}},
        )

    def _decision(self, message: str, active_agent: str) -> GuardDecision | None:
        lowered = message.lower().strip()
        if not lowered:
            return None

        # Intake and recommendation acceptance change business state. Let the
        # deterministic dispatcher persist them before the guard answers any
        # informational request; otherwise the guard would swallow the turn.
        if self._is_booking_flow_message(lowered):
            return None

        if self._is_human_transfer(lowered):
            return GuardDecision("I'll connect you with our team and pass along the details already shared.", "human_transfer", False)
        if self._is_existing_booking_support(lowered):
            return GuardDecision(self._existing_booking_support_answer(lowered), "booking_support", False)
        if self._is_frustration_or_confusion(lowered):
            return GuardDecision("You're right, let me answer that directly. Tell me the exact part you want fixed and I'll stay with it.", "repair")
        if self._is_room_list(lowered):
            return GuardDecision(self._room_list_answer(), "room_list", False)
        if self._is_room_capacity(lowered):
            return GuardDecision(self._capacity_answer(lowered), "capacity")
        if self._is_room_description(lowered):
            answer = self._room_description_answer(lowered)
            if answer:
                return GuardDecision(answer, "room_description", False)
        if self._is_room_or_location_comparison(lowered):
            return GuardDecision(self._comparison_answer(lowered), "comparison", False)
        if self._is_recommendation_or_rejection(lowered):
            return GuardDecision(self._recommendation_answer(lowered), "recommendation", False)
        if self._is_slot_comparison(lowered):
            return GuardDecision(self._slot_comparison_answer(lowered), "slot_comparison")
        if self._is_pricing(lowered):
            return GuardDecision("I don't have exact pricing from the booking system right now. Pricing depends on location, room, date, and group size, and the team can confirm the exact final amount.", "pricing")
        if self._is_payment(lowered):
            return GuardDecision("Payment is confirmed through the booking checkout flow. I can first verify the room, date, and slot, then the confirmed checkout details can be shared.", "payment")
        if "parking" in lowered or re.search(r"\bpark\b", lowered):
            if active_agent == "booking_agent":
                return None
            return GuardDecision("Parking depends on the branch, but guests usually get location-specific arrival guidance with the booking confirmation.", "parking")
        if self._is_cancellation_or_late_refund(lowered):
            return GuardDecision(self._policy_answer(lowered), "policy")
        if "package" in lowered or "packages" in lowered:
            return GuardDecision(self._package_answer(lowered), "packages")
        return None

    def _is_booking_flow_message(self, lowered: str) -> bool:
        if self._is_room_capacity(lowered):
            return False
        if re.search(
            r"\b(?:want|would like|need|ready)\s+to\s+(?:book|reserve)|"
            r"\b(?:book|reserve)\s+(?:it|that|a|an|for)|\blet'?s\s+(?:book|continue)|"
            r"\b(?:i'?ll\s+take\s+that|go\s+ahead)\b",
            lowered,
        ):
            return True
        if re.search(r"\b(?:recommend|suggest|which room|what room)\b", lowered) and (
            self.memory._extract_location(lowered)
            or self.memory._extract_participants(lowered)
            or self.memory._extract_relationship(lowered)
            or self.memory._extract_preferred_date(lowered)
        ):
            return True
        return bool(
            self.memory.data.get("recommended_option")
            and lowered.strip(" .!?") in {"yes", "yes please", "sure", "okay", "ok", "please do"}
        )

    @staticmethod
    def _is_human_transfer(lowered: str) -> bool:
        return bool(re.search(r"\b(?:human|person|representative|manager|call me|talk to someone|speak to someone)\b", lowered))

    def _is_existing_booking_support(self, lowered: str) -> bool:
        direct = bool(
            re.search(
                r"\b(?:already booked|booked yesterday|existing booking|never received (?:my )?confirmation|"
                r"confirmation (?:never arrived|did not arrive|has not arrived)|already paid|payment (?:is )?(?:done|completed))\b",
                lowered,
            )
        )
        support = self.memory.data.get("support_context") or {}
        return direct or bool(support.get("existing_booking") and "something is wrong" in lowered)

    def _existing_booking_support_answer(self, lowered: str) -> str:
        context = dict(self.memory.data.get("support_context") or {})
        concerns = list(self.memory.data.get("concerns") or [])

        if "booked yesterday" in lowered or "already booked" in lowered or "existing booking" in lowered:
            context["existing_booking"] = True
            if "yesterday" in lowered:
                context["booked_when"] = "yesterday"
            answer = "I can help trace an existing booking. Tell me what seems wrong, and I'll keep the details together."
            concern = "Existing booking requires support"
        elif "never received" in lowered or "confirmation never" in lowered or "confirmation did not" in lowered or "confirmation has not" in lowered:
            context["existing_booking"] = True
            context["confirmation_received"] = False
            answer = "I've noted that the confirmation never arrived. I can pass the payment and booking context to the team without making you repeat it."
            concern = "Booking confirmation not received"
        elif "paid" in lowered or "payment" in lowered:
            context["existing_booking"] = True
            context["payment_reported"] = "paid"
            answer = "I've noted that payment was completed but the confirmation is missing. I won't ask you to pay again."
            concern = "Customer reports payment completed"
        else:
            context["existing_booking"] = True
            context["issue_reported"] = True
            answer = "Understood. I'm keeping this as an existing-booking issue. What happened after you booked?"
            concern = "Customer reports an issue with an existing booking"

        if concern not in concerns:
            concerns.append(concern)
        self.memory.data["support_context"] = context
        self.memory.data["concerns"] = concerns[-8:]
        self.memory.save()
        return answer

    @staticmethod
    def _is_frustration_or_confusion(lowered: str) -> bool:
        return bool(
            re.search(
                r"\b(?:not what i asked|not listening|don't understand|dont understand|not understanding|"
                r"confused|frustrating|frustrated|you are wrong|booking is wrong|my booking is wrong|i already told|no no)\b",
                lowered,
            )
        )

    @staticmethod
    def _is_room_list(lowered: str) -> bool:
        return bool(re.search(r"\b(?:what|which|list|show|tell).{0,20}\b(?:rooms|games|themes)\b|\brooms?\s+do\s+you\s+have\b", lowered))

    @staticmethod
    def _is_room_capacity(lowered: str) -> bool:
        return bool(
            ("?" in lowered or re.search(r"\b(?:which|what|can|will|does|do)\b", lowered))
            and re.search(r"\b(?:fit|fits|capacity|hold|accommodate|for\s+\d{1,3}\s+(?:people|players))\b", lowered)
        )

    @staticmethod
    def _is_room_description(lowered: str) -> bool:
        return bool(re.search(r"\b(?:what\s+is|what\s+happens\s+in|tell\s+me\s+about|describe|explain)\b", lowered))

    def _is_room_or_location_comparison(self, lowered: str) -> bool:
        room_count = sum(room.lower() in lowered for room in self._room_names())
        return room_count >= 2 or bool(re.search(r"\b(?:compare|difference| vs |versus|which location|which branch|location is better)\b", lowered))

    @staticmethod
    def _is_recommendation_or_rejection(lowered: str) -> bool:
        has_question_or_request = "?" in lowered or bool(
            re.search(
                r"\b(?:recommend|recommendation|suggest|best|best seller|bestseller|most popular|what else|another option|any other option|other option|"
                r"most people play|most people|popular room|second best|second option|not this one|not that one|don't like|dont like|do not like|don't want|dont want|do not want|any of these|same things|already played|"
                r"which|what should|pick|choose|first[- ]?timer|first time|never done|beginner|something for|something harder|something easier)\b",
                lowered,
            )
        )
        if not has_question_or_request:
            return False
        return bool(
            re.search(
                r"\b(?:recommend|recommendation|suggest|best|best seller|bestseller|most popular|what else|another option|any other option|other option|"
                r"most people play|most people|popular room|second best|second option|not this one|not that one|don't like|dont like|do not like|don't want|dont want|do not want|any of these|same things|already played|"
                r"first[- ]?timer|first time|never done|beginner|couple|kids|corporate|birthday|scary|horror|thrilling|difficult|something for|"
                r"hardest|easiest|something harder|something easier)\b",
                lowered,
            )
        )

    @staticmethod
    def _is_availability(lowered: str) -> bool:
        return bool(re.search(r"\b(?:available|availability|slots?|openings?|do you have\s+\d|tomorrow evening)\b", lowered))

    @staticmethod
    def _is_slot_comparison(lowered: str) -> bool:
        return len(re.findall(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", lowered)) >= 2

    @staticmethod
    def _is_pricing(lowered: str) -> bool:
        return bool(re.search(r"\b(?:price|pricing|costs?|rates?|how much|discounts?|coupons?|offers?|deals?|promo)\b", lowered))

    @staticmethod
    def _is_payment(lowered: str) -> bool:
        return bool(re.search(r"\b(?:payment|pay|paid|upi|card|checkout)\b", lowered))

    @staticmethod
    def _is_cancellation_or_late_refund(lowered: str) -> bool:
        return bool(re.search(r"\b(?:cancel|cancellation|refund|late|running late|reschedule|postpone)\b", lowered))

    def _room_list_answer(self) -> str:
        options = self.recommender.available_options(self.memory.data, limit=10)
        location = str(self.memory.data.get("location") or "").strip()
        if not options:
            options = ["Murder Mystery", "Hostage", "Undercover", "Classified", "Bomb Defusal", "Missile Attack", "Curse of the Pharaoh", "Forbidden Forest"]
        scope = f" at {location}" if location else ""
        next_step = "Which style do you prefer: easier, story-led, or more challenging?"
        if not location:
            next_step = "Which location should I use?"
        return f"The available room options{scope} include {self._spoken_list(options)}. {next_step}"

    def _capacity_answer(self, lowered: str) -> str:
        count_match = re.search(r"\b(\d{1,3})\b", lowered)
        count = int(count_match.group(1)) if count_match else int(self.memory.data.get("participants") or 0)
        options = self.recommender.available_options({**self.memory.data, "participants": count}, limit=10)
        if options:
            return f"For {count} people, the fitting rooms are {self._spoken_list(options)}. Which room should I check availability for?"
        return "That group size likely needs multiple rooms or manual coordination. I can keep the details ready for the team."

    def _room_description_answer(self, lowered: str) -> str:
        descriptions = {
            "murder mystery": "Murder Mystery is an investigation-style room focused on clues, suspects, and story-solving.",
            "hostage": "Hostage is more urgent and pressure-led, with a rescue-style mission feel.",
            "bomb defusal": "Bomb Defusal is a higher-pressure mission where the group works against the clock.",
            "undercover": "Undercover is a story-led mission with suspense and teamwork.",
            "classified": "Classified is a sharper challenge with cryptic, investigation-style puzzles.",
            "missile attack": "Missile Attack is a mission-style room with urgency and coordinated problem-solving.",
            "escape room": "An escape room is a team game where you search for clues, solve puzzles, and complete a themed mission within a time limit.",
        }
        for key, answer in descriptions.items():
            if key in lowered:
                return f"{answer} Want a recommendation from these options?"
        return ""

    def _comparison_answer(self, lowered: str) -> str:
        if "location" in lowered or "branch" in lowered or "whitefield" in lowered or "koramangala" in lowered or "jp nagar" in lowered:
            if "kids" in lowered or "children" in lowered:
                return "For younger children and family groups, I usually recommend Whitefield because it has strong family-friendly options. Which location should I use?"
            return "Whitefield is strong for family-friendly choices, Koramangala has a broader challenge mix, and JP Nagar is good for compact escape-room bookings. Which location should I use?"
        if "murder mystery" in lowered and "hostage" in lowered:
            return "Murder Mystery is calmer and story-led; Hostage is more urgent and pressure-driven. For first timers, I would pick Murder Mystery."
        return "The main difference is usually story style, pressure, and difficulty. Tell me the two rooms you are choosing between and I will narrow it to one."

    def _recommendation_answer(self, lowered: str) -> str:
        rejected_reference = bool(
            re.search(
                r"\b(?:not this|not that|don't like|dont like|do not like|don't want|dont want|do not want|any of these|same things|another option|what else|already played|second best)\b",
                lowered,
            )
        )
        previous_room = str(self.memory.data.get("room") or "")
        current_recommendation = str(
            self.memory.data.get("recommended_option") or previous_room
        )
        self.memory.merge_message(lowered, str(self.memory.data.get("intent") or "escape_room_inquiry"))
        self._record_rejection_if_needed(lowered)
        if rejected_reference:
            # Reference expansion changes "that one" into a room name so the
            # guard can understand it. That synthetic name is not a selection.
            self.memory.data["room"] = (
                previous_room if previous_room and previous_room != current_recommendation else ""
            )
            self.memory.save()
        if not self.memory.data.get("location"):
            if re.search(r"\b(?:first[- ]?timer|first time|never done|beginner)\b", lowered):
                return (
                    "For a first time, I'd start with Murder Mystery because it is the easiest room to settle into. "
                    "Share the branch that works best for you."
                )
            return "Which location would you like to visit? Once I know the branch, I can recommend one room confidently."
        if (
            re.search(r"\b(?:thrill|thrilling|intense|harder|adrenaline)\b", lowered)
            and str(self.memory.data.get("location", "")).lower() == "koramangala"
            and int(self.memory.data.get("participants") or 0) < 3
        ):
            self.memory.set_field("recommended_option", "Hostage", lowered, expected_field="recommended_option")
            self.memory.save()
            return "Classified is the more intense Koramangala option, but it needs at least 3 players. For 2 players, stay with Hostage."
        try:
            recommendation = self.recommender.recommend(lowered, self.memory.data)
        except Exception:
            recommendation = None
        if not getattr(recommendation, "option", ""):
            recommendation = RecommendationEngine.deterministic_fallback(lowered, self.memory.data)
        rejected = {str(option) for option in self.memory.data.get("rejected_options", [])}
        if recommendation.option in rejected:
            recommendation = RecommendationEngine.deterministic_fallback(lowered, self.memory.data)
        self.memory.set_field("recommended_option", recommendation.option, lowered, expected_field="recommended_option")
        discussed = list(self.memory.data.get("discussed_options", []))
        if recommendation.option and recommendation.option not in discussed:
            discussed.append(recommendation.option)
            self.memory.data["discussed_options"] = discussed[-5:]
        self.memory.data["last_discussed_topic"] = "rooms"
        self.memory.save()

        next_step = ""
        if not self.memory.data.get("preferred_date"):
            next_step = " What date are you thinking of visiting?"
        elif not self.memory.data.get("room"):
            next_step = f" Would you like to go ahead with {recommendation.option}?"
        return f"I'd recommend {recommendation.option}. {recommendation.reason}{next_step}"

    def _availability_answer(self) -> str:
        location = self.memory.data.get("location")
        room = self.memory.data.get("room") or self.memory.data.get("recommended_option")
        date = self.memory.data.get("preferred_date")
        missing = [label for label, value in (("location", location), ("room", room), ("date", date)) if not value]
        if missing:
            return f"I can check availability. Please share the {missing[0]} first."
        return "I can check live availability for that room, date, and location now. Which time should I try first?"

    @staticmethod
    def _slot_comparison_answer(lowered: str) -> str:
        slots = re.findall(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", lowered)
        if len(slots) >= 2:
            return f"{slots[-1].upper()} is safer if you want more arrival buffer; {slots[0].upper()} is better if you want to finish earlier."
        return "For timing, the later slot is usually safer if arrival could be tight."

    def _policy_answer(self, lowered: str) -> str:
        if "late" in lowered:
            return "Don't worry. Games are scheduled back to back, so call the branch as soon as possible because arriving after the scheduled time may affect the slot."
        policy = get_venue_policy(str(self.memory.data.get("location") or ""))
        cancellation = str(policy.get("cancellationPolicy") or "").strip()
        reschedule = str(policy.get("reschedulePolicy") or "").strip()
        if "reschedule" in lowered or "postpone" in lowered:
            return reschedule or cancellation
        return cancellation or reschedule

    @staticmethod
    def _package_answer(lowered: str) -> str:
        if "corporate" in lowered:
            return "Corporate packages can combine escape rooms with larger group coordination and add-ons like food. How many employees are attending?"
        if "birthday" in lowered:
            return "Birthday packages can combine escape rooms with party support and optional food. How many guests are expected?"
        return "Packages depend on event type, location, group size, and food needs. What type of event is this for?"

    def _record_rejection_if_needed(self, lowered: str) -> None:
        if not re.search(r"\b(?:not this|not that|don't like|dont like|do not like|don't want|dont want|do not want|any of these|same things|another option|what else|already played|second best)\b", lowered):
            return
        current = str(self.memory.data.get("recommended_option") or self.memory.data.get("room") or "").strip()
        if not current:
            return
        self.memory.data.setdefault("rejected_options", [])
        if isinstance(self.memory.data["rejected_options"], list) and current not in self.memory.data["rejected_options"]:
            self.memory.data["rejected_options"].append(current)
            self.memory.data["rejected_options"] = self.memory.data["rejected_options"][-8:]
            self.memory.save()

    @staticmethod
    def _room_names() -> list[str]:
        return ["Murder Mystery", "Hostage", "Classified", "Undercover", "Bomb Defusal", "Missile Attack", "Curse of the Pharaoh", "Forbidden Forest"]

    @staticmethod
    def _spoken_list(options: list[str]) -> str:
        if len(options) <= 1:
            return options[0] if options else ""
        return ", ".join(options[:-1]) + f", and {options[-1]}"
