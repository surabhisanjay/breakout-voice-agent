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
        pre_decision_memory = dict(self.memory.data)
        decision = self._decision(normalized, active_agent)
        if not decision:
            return None

        if decision.category in {
            "capacity",
            "comparison",
            "direct_question",
            "room_list",
            "room_description",
            "pricing",
            "parking",
            "payment",
            "policy",
            "packages",
            "slot_comparison",
        }:
            for field in (
                "location",
                "participants",
                "participants_min",
                "participants_max",
                "age_group",
                "preferred_date",
                "room",
                "selected_slot",
                "booking_id",
                "booking_ref",
                "booking_order_id",
            ):
                self.memory.data[field] = pre_decision_memory.get(field, "")
        self._merge_guard_message(normalized, decision.category)
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

    def _merge_guard_message(self, message: str, category: str) -> None:
        """Persist facts even when the guard answers before normal routing."""
        if category in {
            "recommendation",
            "capacity",
            "comparison",
            "direct_question",
            "room_list",
            "room_description",
            "pricing",
            "parking",
            "payment",
            "policy",
            "packages",
            "slot_comparison",
        }:
            return
        lowered = message.lower()
        current = str(self.memory.data.get("intent") or "")
        intent = current
        if re.search(r"\b(?:corporate|team outing|office|employees|company event|team members|staff)\b", lowered):
            intent = "corporate_event"
        elif re.search(r"\b(?:birthday|birthdays|bday)\b", lowered):
            intent = "birthday_party"
        if not intent or intent == "general_faq":
            if (
                self.memory._extract_location(lowered)
                or self.memory._extract_room(lowered)
                or self.memory._extract_participants(lowered)
                or self.memory._extract_participant_range(lowered)
                or self.memory._extract_experience_level(lowered)
                or re.search(r"\b(?:escape room|rooms?|kids|children|adults|players|first time|first[- ]?timer)\b", lowered)
            ):
                intent = "escape_room_inquiry"
            else:
                intent = current or "general_faq"
        if intent == "corporate_event" and current not in ("", "general_faq", "corporate_event"):
            for field in (
                "experience_level",
                "age_group",
                "age_detail",
                "room",
                "recommended_option",
                "selected_slot",
                "booking_id",
                "booking_ref",
                "booking_order_id",
            ):
                self.memory.data[field] = ""
        self.memory.merge_message(message, intent)

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
        if self._is_booking_help_request(lowered):
            return GuardDecision(
                "I can help with that. Are you trying to make a new booking or fix an existing booking?",
                "booking_help",
                False,
            )
        if self._is_clean_correction_with_known_value(lowered):
            return None
        if self._is_frustration_or_confusion(lowered):
            repair_count = int(self.memory.data.get("repair_turn_count") or 0) + 1
            self.memory.data["repair_turn_count"] = repair_count
            if repair_count >= 2:
                return GuardDecision(
                    "You're right; I repeated myself. I've kept the details you already shared. "
                    "I can connect you to a human, or continue from where we left off.",
                    "repair",
                )
            return GuardDecision(
                "I'm sorry this hasn't helped. I've kept the details you already shared. "
                "Tell me the exact part you want fixed and I'll stay with it.",
                "repair",
            )
        if self._is_pause_or_callback(lowered):
            return GuardDecision(self._pause_answer(), "pause_booking", False)
        if self._is_room_or_location_comparison(lowered):
            return GuardDecision(self._comparison_answer(lowered), "comparison", False)
        if self._is_recommendation_or_rejection(lowered) and re.search(
            r"\b(?:recommend|suggest|what would you choose|if you were me|pick|choose|something harder|not too easy|"
            r"another option|what else|not this|not that|don't want|dont want|do not want)\b",
            lowered,
        ):
            return GuardDecision(self._recommendation_answer(lowered), "recommendation", False)
        direct_answer = self._direct_customer_question_answer(lowered)
        if direct_answer:
            return GuardDecision(direct_answer, "direct_question", active_agent == "booking_agent")
        if self._is_room_list(lowered):
            return GuardDecision(self._room_list_answer(), "room_list", False)
        if self._is_pricing(lowered):
            return GuardDecision(
                self._append_resume(self._pricing_answer(lowered)),
                "pricing",
                active_agent == "booking_agent",
            )
        if self._is_room_capacity(lowered):
            return GuardDecision(self._capacity_answer(lowered), "capacity")
        if self._is_room_description(lowered):
            answer = self._room_description_answer(lowered)
            if answer:
                return GuardDecision(answer, "room_description", False)
        if self._is_recommendation_or_rejection(lowered):
            return GuardDecision(self._recommendation_answer(lowered), "recommendation", False)
        if self._is_slot_comparison(lowered):
            return GuardDecision(self._slot_comparison_answer(lowered), "slot_comparison")
        if self._is_payment(lowered):
            return GuardDecision("Payment is confirmed through the booking checkout flow. I can first verify the room, date, and slot, then the confirmed checkout details can be shared.", "payment")
        if "parking" in lowered or re.search(r"\bpark\b", lowered):
            return GuardDecision(self._append_resume(self._parking_answer()), "parking", active_agent == "booking_agent")
        if self._is_cancellation_or_late_refund(lowered):
            return GuardDecision(self._policy_answer(lowered), "policy")
        if "package" in lowered or "packages" in lowered:
            return GuardDecision(self._package_answer(lowered), "packages")
        return None

    def _is_booking_flow_message(self, lowered: str) -> bool:
        if self._is_room_capacity(lowered):
            return False
        if re.search(
            r"\bi\s+want\s+(?:a\s+|an\s+)?(?:corporate|birthday|bachelor|farewell|couple|virtual)\b",
            lowered,
        ):
            return True
        if re.search(
            r"\b(?:want|would like|i would like|i'?d like|like|need|ready)\s+to\s+(?:book|reserve)|"
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

    @staticmethod
    def _is_booking_help_request(lowered: str) -> bool:
        return bool(
            re.search(
                r"\b(?:need|want|looking for)\s+help\s+(?:with|on)\s+(?:a\s+|my\s+)?booking\b",
                lowered,
            )
        )

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
                r"confused|frustrating|frustrated|this (?:is not|isn't) helping|"
                r"you keep repeating(?: yourself)?|going in circles|you are wrong|booking is wrong|"
                r"my booking is wrong|i already told)\b",
                lowered,
            )
        )

    def _is_clean_correction_with_known_value(self, lowered: str) -> bool:
        if not re.match(r"^\s*(?:no|nah|nope)(?:[\s,.-]+(?:no|nah|nope))*[\s,.-]+", lowered):
            return False
        return any(room.lower() in lowered for room in self._room_names())

    def _direct_customer_question_answer(self, lowered: str) -> str:
        if "?" not in lowered and not re.match(
            r"^(?:can|could|do|does|is|are|will|would|what|when|where|why|how|tell|explain)\b",
            lowered,
        ):
            return ""
        self._merge_guard_message(lowered, "direct_question")
        answer = ""
        room = self._current_room_context()
        room_display = room or "Murder Mystery"
        if re.search(r"\bwhy\b.{0,30}\brecommend\b|\bwhy\b.{0,30}\bthat room\b", lowered):
            if room_display.lower() == "murder mystery":
                answer = (
                    "I recommend Murder Mystery because it is beginner friendly, balanced in difficulty, "
                    "and easy for first-time players to settle into."
                )
            else:
                answer = f"I recommend {room_display} because it fits the group context and gives a clear team mission without needing prior escape-room experience."
        elif re.search(r"\b(?:is it|are they|how)\b.{0,25}\b(?:difficult|hard|difficulty|challenging)\b|\bdifficulty\b", lowered):
            answer = (
                f"{room_display} is approachable rather than punishing. It still has puzzles and time pressure, "
                "but beginners can get moving without feeling lost."
            )
        elif re.search(r"\b(?:beginner|beginners|first[- ]?timer|first time)\b.{0,35}\b(?:finish|complete|do|play|manage)\b", lowered):
            answer = (
                "Yes. Beginners can play and finish with teamwork, and the game master can support the group with hints if needed."
            )
        elif re.search(r"\b(?:scary|horror|haunted|fear|frightening)\b", lowered):
            answer = (
                f"{room_display} is not a jump-scare horror experience. It is more mystery and suspense than frightening."
            )
        elif re.search(r"\b(?:children|kids|child|kid)\b", lowered):
            answer = (
                "Yes, children can play selected rooms. The best location depends on age and branch, and younger kids may need adult supervision."
            )
        elif re.search(r"\b(?:birthday|birthdays|bday)\b", lowered):
            answer = (
                "Yes, Breakout hosts birthday experiences. Parties can combine escape rooms with add-ons like food or other activities depending on group size and branch."
            )
        elif re.search(r"\b(?:corporate|team outing|office|employees|company event)\b", lowered):
            answer = (
                "Yes, Breakout handles corporate events and team outings, including Escape Room formats and group-friendly add-ons like food or coordinated activities."
            )
        elif re.search(r"\b(?:how long|duration|last|game take|session take)\b", lowered):
            answer = "Each escape room session runs for about 50 minutes. It is best to arrive 10-20 minutes early for briefing."
        elif re.search(r"\b(?:direction|directions|address|how do i get|how to get|route|map)\b", lowered):
            location = str(self.memory.data.get("location") or "").strip()
            if location:
                answer = f"I can use the {location} branch for directions. The confirmation can carry the exact map and arrival guidance."
            else:
                answer = "Breakout has branches at Whitefield, Koramangala, and JP Nagar. Once you pick the branch, I can keep the directions tied to that booking."
        elif "outside food" in lowered:
            answer = "Outside food policies can vary by branch and booking type, so please confirm that with the branch team before carrying food in."
        elif re.search(r"\b(?:food|snack|snacks|meal|meals|beverages|catering)\b", lowered):
            answer = "Food options include snack boxes, hi-tea, meal options, and event menus depending on the package and branch."
        elif re.search(
            r"\b(?:certified|certificate|certification|allergy|allergies|allergen|vr\s+headset|prop\s+report|"
            r"safety\s+report|inspection\s+report)\b",
            lowered,
        ):
            answer = (
                "I don't have certified prop, allergen, or inspection documentation in this chat. "
                "The branch team can verify that specific documentation before you visit."
            )
        if not answer:
            return ""
        return self._append_resume(answer)

    def _append_resume(self, answer: str) -> str:
        resume = self._resume_prompt()
        if not resume or answer.rstrip().endswith("?"):
            return answer
        return f"{answer} {resume}"

    def _resume_prompt(self) -> str:
        if self.memory.data.get("booking_id"):
            return ""
        if not self.memory.data.get("location"):
            return "By the way, which branch are you planning to visit?"
        if not self.memory.data.get("participants"):
            return "How many people are joining?"
        if not self.memory.data.get("room"):
            recommended = str(self.memory.data.get("recommended_option") or self._current_room_context() or "").strip()
            if recommended:
                return f"Would you like to book {recommended}, or hear another recommendation?"
            return "Would you like me to recommend a room?"
        if not self.memory.data.get("preferred_date"):
            return "What date are you planning for?"
        if not (
            self.memory.data.get("selected_slot")
            or self.memory.data.get("preferred_time")
            or self.memory.data.get("preferred_period")
            or self.memory.data.get("time_preference") == "any"
        ):
            return "What time works best: morning, afternoon, or evening?"
        return ""

    def _current_room_context(self) -> str:
        explicit = str(self.memory.data.get("room") or self.memory.data.get("recommended_option") or "").strip()
        if explicit:
            return explicit
        room_names = self._room_names()
        for turn in reversed(self.memory.data.get("conversation", [])[-8:]):
            if turn.get("role") != "agent":
                continue
            content = str(turn.get("content") or "").lower()
            for room in room_names:
                if room.lower() in content:
                    return room
        return ""

    def _parking_answer(self) -> str:
        location = str(self.memory.data.get("location") or "").lower()
        if location == "whitefield":
            return "Whitefield has basement parking available."
        if location == "koramangala":
            return "Koramangala has basement and street parking available."
        if location == "jp nagar":
            return "JP Nagar has street parking available."
        return "Parking depends on the branch: Whitefield has basement parking, Koramangala has basement and street parking, and JP Nagar has street parking."

    @staticmethod
    def _is_room_list(lowered: str) -> bool:
        return bool(re.search(r"\b(?:what|which|list|show|tell).{0,20}\b(?:rooms|games|themes)\b|\brooms?\s+do\s+you\s+have\b", lowered))

    @staticmethod
    def _is_room_capacity(lowered: str) -> bool:
        if re.search(r"\b(?:price|pricing|costs?|charges?|rates?|how much|tax|taxes|discounts?|coupons?|offers?|deals?|promo|student)\b", lowered):
            return False
        if re.search(r"\b(?:available|availability|slots?|openings?|today|tomorrow)\b", lowered):
            return False
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
                r"most people play|most people|popular room|second best|second option|not this one|not that one|not murder mystery|not hostage|not classified|not undercover|not bomb defusal|don't like|dont like|do not like|don't want|dont want|do not want|any of these|same things|already played|"
                r"which|what should|what would you choose|if you were me|pick|choose|first[- ]?timer|first time|never done|beginner|something for|something harder|something easier)\b",
                lowered,
            )
        )
        if not has_question_or_request:
            return False
        return bool(
            re.search(
                r"\b(?:recommend|recommendation|suggest|best|best seller|bestseller|most popular|what else|another option|any other option|other option|"
                r"most people play|most people|popular room|second best|second option|not this one|not that one|not murder mystery|not hostage|not classified|not undercover|not bomb defusal|don't like|dont like|do not like|don't want|dont want|do not want|any of these|same things|already played|"
                r"what would you choose|if you were me|first[- ]?timer|first time|never done|beginner|couple|kids|corporate|birthday|scary|horror|thrilling|difficult|not too easy|something for|"
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
        return bool(re.search(r"\b(?:price|pricing|costs?|charges?|rates?|how much|tax|taxes|including taxes|discounts?|coupons?|offers?|deals?|promo)\b", lowered))

    @staticmethod
    def _is_pause_or_callback(lowered: str) -> bool:
        return bool(
            re.search(
                r"\b(?:i'?ll|i will|we'?ll|we will)\s+(?:let\s+you\s+know|call\s+back|get\s+back|confirm\s+later)\b"
                r"|\b(?:not\s+now|later|need\s+to\s+think|will\s+decide)\b",
                lowered,
            )
        )

    def _pause_answer(self) -> str:
        location = str(self.memory.data.get("location") or "").strip()
        participants = self.memory.data.get("participants") or ""
        experience = str(self.memory.data.get("experience_level") or "").strip()
        details = []
        if participants:
            details.append(f"{participants} players")
        if location:
            details.append(location)
        if experience:
            details.append("first-time players" if experience == "beginner" else experience)
        suffix = f" I have noted {', '.join(map(str, details))}." if details else ""
        return f"Sure, no pressure.{suffix} When you're ready, I can check the latest availability again."

    @staticmethod
    def _is_payment(lowered: str) -> bool:
        return bool(re.search(r"\b(?:payment|pay|paid|upi|card|checkout)\b", lowered))

    @staticmethod
    def _is_cancellation_or_late_refund(lowered: str) -> bool:
        return bool(
            re.search(
                r"\b(?:cancel|cancellation|refund|late|running late|reschedul(?:e|ing)|postpon(?:e|ing))\b",
                lowered,
            )
        )

    def _room_list_answer(self) -> str:
        options = self.recommender.available_options(self.memory.data, limit=10)
        location = str(self.memory.data.get("location") or "").strip()
        if not options:
            options = ["Murder Mystery", "Hostage", "Undercover", "Classified", "Bomb Defusal", "Missile Attack", "Curse of the Pharaoh", "Forbidden Forest"]
        scope = f" at {location}" if location else ""
        next_step = "Which style do you prefer: easier, story-led, or more challenging?"
        if not location:
            next_step = "Which Breakout location are you planning to visit?"
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
                return "For younger children and family groups, I usually recommend Whitefield because it has strong family-friendly options. Which Breakout location are you planning to visit?"
            return "Whitefield is strong for family-friendly choices, Koramangala has a broader challenge mix, and JP Nagar is good for compact escape-room bookings. Which Breakout location are you planning to visit?"
        if "murder mystery" in lowered and "hostage" in lowered:
            return "Murder Mystery is calmer and story-led; Hostage is more urgent and pressure-driven. For first timers, I would pick Murder Mystery."
        if "murder mystery" in lowered and "prison break" in lowered:
            return (
                "Murder Mystery is calmer and investigation-led, with clues and story-solving that suit first-time players. "
                "Prison Break is harder and more mission-style, with escape tactics and stronger pressure."
            )
        return "The main difference is usually story style, pressure, and difficulty. Tell me the two rooms you are choosing between and I will narrow it to one."

    def _recommendation_answer(self, lowered: str) -> str:
        rejected_reference = bool(
            re.search(
                r"\b(?:not this|not that|not murder mystery|not hostage|not classified|not undercover|not bomb defusal|don't like|dont like|do not like|don't want|dont want|do not want|any of these|same things|another option|what else|already played|second best)\b",
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
            wants_harder = bool(
                re.search(r"\b(?:harder|not\s+too\s+easy|not\s+easy|challenging|difficult|intense|pressure)\b", lowered)
            )
            if rejected_reference and current_recommendation:
                option = "Hostage" if current_recommendation != "Hostage" else "Murder Mystery"
                reason = "It is another strong all-branch option with more urgency than Murder Mystery."
            elif wants_harder:
                option = "Hostage"
                reason = "It is the stronger all-branch step up from Murder Mystery without turning into a horror experience."
            else:
                option = "Murder Mystery"
                beginner_context = (
                    str(self.memory.data.get("experience_level") or "") == "beginner"
                    or re.search(r"\b(?:first[- ]?timer|first time|never done|beginner|what would you choose|if you were me)\b", lowered)
                )
                reason = (
                    "It is beginner friendly, balanced in difficulty, and works well as a first time escape-room experience."
                    if beginner_context
                    else "It is the safest global recommendation because it is story-led, approachable, and available across branches."
                )
            self.memory.set_field("recommended_option", option, lowered, expected_field="recommended_option")
            discussed = list(self.memory.data.get("discussed_options", []))
            if option not in discussed:
                discussed.append(option)
                self.memory.data["discussed_options"] = discussed[-5:]
            self.memory.data["last_discussed_topic"] = "rooms"
            self.memory.save()
            return (
                f"Across all branches I'd recommend {option}. {reason} "
                "Once I know your preferred branch, I'll refine that to the best available room for that location. Share the branch when you're ready; which branch works best for your group?"
            )
        if (
            re.search(r"\b(?:thrill|thrilling|intense|harder|adrenaline)\b", lowered)
            and str(self.memory.data.get("location", "")).lower() == "koramangala"
            and int(self.memory.data.get("participants") or 0) < 3
        ):
            self.memory.set_field("recommended_option", "Hostage", lowered, expected_field="recommended_option")
            self.memory.save()
            return "Classified is the more intense Koramangala option, but it needs at least 3 players. For 2 players, stay with Hostage."
        rejected = {str(option) for option in self.memory.data.get("rejected_options", [])}
        try:
            recommendation = self.recommender.recommend(lowered, self.memory.data)
        except Exception:
            recommendation = None
        if not getattr(recommendation, "option", ""):
            recommendation = RecommendationEngine.deterministic_fallback(lowered, self.memory.data)
        if not getattr(recommendation, "option", "") or recommendation.option in rejected:
            recommendation = RecommendationEngine.deterministic_fallback(lowered, self.memory.data)
        option = str(getattr(recommendation, "option", "") or "").strip()
        reason = str(getattr(recommendation, "reason", "") or "").strip()
        if not option or option in rejected:
            for candidate in self.recommender.available_options(self.memory.data, limit=10):
                if candidate and candidate not in rejected:
                    option = candidate
                    reason = f"{candidate} is the best remaining fit for the details shared."
                    break
        if not option:
            option = "Murder Mystery"
            reason = "It is the safest fallback recommendation when the room preference is still unclear."
        self.memory.set_field("recommended_option", option, lowered, expected_field="recommended_option")
        discussed = list(self.memory.data.get("discussed_options", []))
        if option and option not in discussed:
            discussed.append(option)
            self.memory.data["discussed_options"] = discussed[-5:]
        self.memory.data["last_discussed_topic"] = "rooms"
        self.memory.save()

        next_step = ""
        if not self.memory.data.get("preferred_date"):
            next_step = " What date are you thinking of visiting?"
        elif not self.memory.data.get("room"):
            next_step = f" Would you like to go ahead with {option}?"
        return f"I'd recommend {option}. {reason}{next_step}"

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

    def _pricing_answer(self, lowered: str) -> str:
        if re.search(r"\b(?:discounts?|coupons?|offers?|deals?|promo|student)\b", lowered):
            if "student" in lowered:
                return "Yes, the knowledge base lists a 10% student discount with valid ID. For groups, it also lists 10% off for 4 or more players."
            return "Yes. The knowledge base lists 10% off for 4 or more players, 10% off for 6 or more players, and a 10% student discount with valid ID."
        if not self.memory.data.get("price_breakdown"):
            try:
                self.memory._refresh_estimated_price()
            except Exception:
                pass
        breakdown = dict(self.memory.data.get("price_breakdown") or {})
        final_price = breakdown.get("final_price")
        base_price = breakdown.get("base_price")
        discount = breakdown.get("discount")
        if final_price:
            if discount:
                return f"The estimated total price for the current details is INR {final_price} after a 10% discount. The base price is INR {base_price} and the discount is INR {discount}."
            return f"The estimated total price for the current details is INR {final_price}."
        return "Pricing depends on the location, room, date, and group size. Once those are set, I can use the booking flow to confirm the final amount."

    def _policy_answer(self, lowered: str) -> str:
        if "late" in lowered:
            return "Don't worry. Games are scheduled back to back, so call the branch as soon as possible because arriving after the scheduled time may affect the slot."
        policy = get_venue_policy(str(self.memory.data.get("location") or ""))
        cancellation = str(policy.get("cancellationPolicy") or "").strip()
        reschedule = str(policy.get("reschedulePolicy") or "").strip()
        if re.search(r"\b(?:reschedul(?:e|ing)|postpon(?:e|ing))\b", lowered):
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
        if not re.search(r"\b(?:not this|not that|not murder mystery|not hostage|not classified|not undercover|not bomb defusal|don't like|dont like|do not like|don't want|dont want|do not want|any of these|same things|another option|what else|already played|second best)\b", lowered):
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
