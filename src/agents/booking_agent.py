"""
BookingAgent — handles slot confirmation and booking creation (Voice Agent).
BookingSimulator — in-memory booking backend simulator.
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.agent_response import AgentResponse
from ..orchestration.booking_orchestrator import BookingOrchestrator
from ..core.conversation_modes import ConversationMode, ConversationModeDetector
from ..memory.conversation_memory import ConversationMemory
from ..response_composer import ResponseComposer


class BookingError(Exception):
    pass


class EscalationRequired(Exception):
    pass


@dataclass
class Slot:
    slot_id: str
    date: str
    time: str
    service: str
    capacity: int
    locked_by: Optional[str] = None
    booking_ref: Optional[str] = None


@dataclass
class BookingRecord:
    reference: str
    slot_id: str
    customer: Dict[str, Any]
    status: str = "confirmed"
    payment_required: bool = False
    payment_payload: Optional[Dict[str, Any]] = None


class BookingAgent:
    # Internal conversation states
    _STATE_CHECKING_AVAILABILITY = "checking_availability"
    _STATE_WAITING_FOR_SLOT = "waiting_for_slot"
    _STATE_WAITING_FOR_AGE = "waiting_for_age"
    _STATE_WAITING_FOR_NAME = "waiting_for_name"
    _STATE_WAITING_FOR_PHONE = "waiting_for_phone"
    _STATE_WAITING_FOR_ALT_DATE = "waiting_for_alt_date"
    _STATE_BOOKING_CONFIRMED = "booking_confirmed"
    _STATE_CLOSED = "closed"

    def __init__(
        self,
        memory: ConversationMemory,
        orchestrator: BookingOrchestrator | None = None,
        provider: BookingProvider | None = None,
    ) -> None:
        self.memory = memory
        if orchestrator is not None:
            self.orchestrator = orchestrator
        elif provider is not None:
            self.orchestrator = BookingOrchestrator(
                booking_provider=None,
                contract_provider=None,
            )
            self.orchestrator.is_live = True
            
            class AdapterBookingProvider:
                def __init__(self, p): self.p = p
                def get_locations(self): return self.p.get_locations() if hasattr(self.p, "get_locations") else []
                def get_games(self, loc_id): return self.p.get_games(loc_id) if hasattr(self.p, "get_games") else []
                def get_slots(self, loc_id, game_ids=None, start_date=None, end_date=None):
                    return self.p.get_available_slots(loc_id, game_ids, start_date, end_date) if hasattr(self.p, "get_available_slots") else []
                def prepare_booking(self, payload): return self.p.prepare_booking(payload) if hasattr(self.p, "prepare_booking") else {}
                def release_slots(self, slot_ids): return {}
            
            self.orchestrator.booking_provider = AdapterBookingProvider(provider)
        else:
            self.orchestrator = BookingOrchestrator()

        class OrchestratorAvailabilityAdapter:
            def __init__(self, orch: BookingOrchestrator, mem: dict):
                self.orch = orch
                self.mem = mem
            def check(self, location: str, date: str, participants: int) -> dict:
                room = str(self.mem.get("room") or self.mem.get("recommended_option") or "")
                return self.orch.check_availability(location, date, participants, room)

        class OrchestratorBookingAdapter:
            def __init__(self, orch: BookingOrchestrator):
                self.orch = orch
            def create(self, memory: dict, chosen_slot: str) -> dict:
                return self.orch.prepare_booking(memory, chosen_slot)

        self.availability_tool = OrchestratorAvailabilityAdapter(self.orchestrator, memory.data)
        self.booking_tool = OrchestratorBookingAdapter(self.orchestrator)
        self.mode_detector = ConversationModeDetector()
        self.response_composer = ResponseComposer(
            Path(__file__).resolve().parents[2] / "prompts" / "breakout_personality_prompt.txt"
        )

        # Internal state machine
        self._state: str = self._STATE_CHECKING_AVAILABILITY
        self._available_slots: list[str] = []
        self._last_availability: dict = {}
        self._booking_result: dict | None = None
        self._selected_slot: str = ""

    # ------------------------------------------------------------------ #
    # Public interface                                                     #
    # ------------------------------------------------------------------ #

    def handle_message(self, message: str) -> AgentResponse:
        """
        Process one customer turn.

        Always runs tool logic first, then builds the customer-facing
        response from the tool output.  Never generates a response before
        the tool has been consulted.
        """
        import time
        start_turn = time.time()
        self.memory.add_turn("customer", message)

        if self.memory.data.get("completed_booking"):
            response = "Great. Thanks for choosing Breakout. Have a wonderful day."
            return self._finalize_response(response, message, start_turn, self._booking_result)

        lowered = message.lower()

        if self._is_name_lookup(lowered):
            stored_name = str(self.memory.data.get("customer_name", "")).strip()
            if stored_name:
                response = f"You're booked under {stored_name}."
            else:
                response = "I don't have your name yet."
            booking_result = None
            return self._finalize_response(response, message, start_turn, booking_result)

        # Check for room change / modification
        rooms = {
            "murder mystery": "Murder Mystery",
            "hostage": "Hostage",
            "classified": "Classified",
            "bomb defusal": "Bomb Defusal",
            "prison break": "Prison Break",
            "undercover": "Undercover",
            "curse of the pharaoh": "Curse of the Pharaoh",
            "the wizarding championship": "The Wizarding Championship",
            "the forbidden forest": "The Forbidden Forest",
        }

        target_room = None
        for room_key, room_val in rooms.items():
            if room_key in lowered:
                target_room = room_val
                break

        # Check for location, participants, or date modification
        target_location = self.memory._extract_location(lowered)
        target_participants = self.memory._extract_participants(lowered)
        target_date = self.memory._extract_preferred_date(message)

        # A missing required field may be supplied naturally while the booking
        # agent owns the call. Capture it before any availability check.
        if target_date and not self.memory.data.get("preferred_date"):
            self.memory.set_field("preferred_date", target_date, message, expected_field="preferred_date")
        if target_location and not self.memory.data.get("location"):
            self.memory.set_field("location", target_location, message, expected_field="location")

        is_cancellation_policy_question = any(
            phrase in lowered
            for phrase in (
                "cancellation policy", "cancel policy", "cancellation charges",
                "cancellation rules", "refund policy", "what if i cancel",
                "how does cancellation", "can bookings be cancelled",
            )
        )
        is_cancel_request = bool(
            re.search(
                r"\b(?:cancel|delete)\s+(?:my\s+|the\s+|this\s+)?(?:booking|reservation)\b",
                lowered,
            )
            or re.search(r"\bi\s+(?:want|need|would like)\s+to\s+cancel\b", lowered)
        ) and not is_cancellation_policy_question

        is_affirmative_follow_up = lowered.strip(" .!?") in {
            "yes", "yes please", "sure", "okay", "ok", "please do",
        }
        is_contextual_best = lowered.strip(" .!?") in {
            "which is best", "which one is best", "what is best", "what's best",
        }

        # Check if any field is different from current memory
        room_changed = bool(target_room and target_room != self.memory.data.get("room"))
        location_changed = bool(target_location and target_location != self.memory.data.get("location"))
        participants_changed = bool(target_participants and target_participants != self.memory.data.get("participants"))
        date_changed = bool(target_date and target_date != self.memory.data.get("preferred_date"))

        any_field_changed = room_changed or location_changed or participants_changed or date_changed
        
        is_change_request = any_field_changed and (
            any(w in lowered for w in ("change", "instead", "switch", "want", "prefer", "rather", "choose", "update", "modify", "reschedule", "now", "different")) or
            self._state in (self._STATE_WAITING_FOR_SLOT, self._STATE_WAITING_FOR_ALT_DATE)
        )

        if self.memory.data.get("pending_policy_explanation") and is_affirmative_follow_up:
            self.memory.data["pending_policy_explanation"] = False
            self.memory.save()
            response = self._append_booking_resume(self._cancellation_policy_explanation())
            booking_result = None

        elif is_contextual_best and self.memory.data.get("last_discussed_topic"):
            response = self._append_booking_resume(self._contextual_best_response())
            booking_result = None

        elif is_cancellation_policy_question:
            self.memory.data["pending_policy_explanation"] = True
            self.memory.save()
            response = self._append_booking_resume(
                "Cancellation charges depend on how far in advance the cancellation is made. "
                "I can explain the policy without changing your booking."
            )
            booking_result = None

        elif is_cancel_request:
            booking_ref = self.memory.data.get("booking_ref") or (self._booking_result or {}).get("booking_id")
            if booking_ref:
                self.orchestrator.cancel_booking(booking_ref, reason="customer request")
                response = f"I've successfully cancelled your booking {booking_ref}. Let me know if you need help with anything else."
            else:
                response = "No problem, I've cancelled the booking process. Let me know if you'd like to plan something else."
            self._state = self._STATE_CLOSED
            booking_result = None

        elif is_change_request:
            changes_desc = []
            
            if room_changed:
                self.memory.set_field("room", target_room, message, expected_field="room")
                changes_desc.append(f"room to {target_room}")
                
            if location_changed:
                self.memory.set_field("location", target_location, message, expected_field="location")
                changes_desc.append(f"location to {target_location}")
                
            if participants_changed:
                self.memory.set_field("participants", target_participants, message, expected_field="participants")
                changes_desc.append(f"player count to {target_participants}")
                
            if date_changed:
                self.memory.set_field("preferred_date", target_date, message, expected_field="preferred_date")
                changes_desc.append(f"date to {target_date}")
                
            # Acknowledge the change
            ack = f"Sure, I've updated your " + ", ".join(changes_desc) + ". "
            
            # If room changed, maybe add explanation
            if room_changed:
                from ..knowledge.demo_knowledge import get_demo_answer
                explanation = get_demo_answer(target_room)
                if explanation:
                    ack += explanation + " "

            location = str(self.memory.data.get("location", ""))
            date = str(self.memory.data.get("preferred_date", ""))
            participants = int(self.memory.data.get("participants") or self.memory.data.get("company_size") or 2)

            if not date:
                self._state = self._STATE_CHECKING_AVAILABILITY
                response = f"{ack}What date would you like to visit?"
                booking_result = None
                availability = None
            else:
                availability = self.availability_tool.check(location, date, participants)
                self._last_availability = availability

            if availability is not None and availability["available"]:
                self._available_slots = availability["slots"]
                self._state = self._STATE_WAITING_FOR_SLOT
                slots_text = self._format_slots(self._available_slots)
                response = f"{ack}For {date} at {location}, I found slots at {slots_text} for {participants} players. Which time works best for you?"
            elif availability is not None:
                self._state = self._STATE_WAITING_FOR_ALT_DATE
                response = f"{ack}I checked availability on {date} at {location} for {participants} players, but unfortunately we don't have slots. Could you share an alternative date?"

            booking_result = None
        else:
            # Check for general FAQ / room explanations (interruption)
            from ..knowledge.demo_knowledge import get_demo_answer
            if "food" in lowered:
                interruption_answer = (
                    "Food options include continental food, build-your-menu options, mix snack boxes, "
                    "hi-tea options, and Indian buffet options for corporate events."
                )
            else:
                interruption_answer = get_demo_answer(message)

            if interruption_answer:
                response = self._append_booking_resume(interruption_answer)
                booking_result = None
            else:
                # Normal booking state machine
                if self._state == self._STATE_CHECKING_AVAILABILITY:
                    response, booking_result = self._handle_availability_check()

                elif self._state == self._STATE_WAITING_FOR_SLOT:
                    response, booking_result = self._handle_slot_selection(message)

                elif self._state == self._STATE_WAITING_FOR_AGE:
                    response, booking_result = self._handle_age_group(message)

                elif self._state == self._STATE_WAITING_FOR_NAME:
                    response, booking_result = self._handle_contact_name(message)

                elif self._state == self._STATE_WAITING_FOR_PHONE:
                    response, booking_result = self._handle_contact_phone(message)

                elif self._state == self._STATE_WAITING_FOR_ALT_DATE:
                    response, booking_result = self._handle_alt_date(message)

                elif self._state in (self._STATE_BOOKING_CONFIRMED, self._STATE_CLOSED):
                    response, booking_result = self._handle_post_booking(message)

        return self._finalize_response(response, message, start_turn, booking_result)

    def _finalize_response(
        self,
        response: str,
        message: str,
        start_turn: float,
        booking_result: dict | None,
    ) -> AgentResponse:
        response = self._clean_response(response)
        mode = self.mode_detector.detect(message, self.memory.data, str(self.memory.data.get("intent", "")))
        self.memory.data["conversation_mode"] = mode.value
        draft = response

        # Measure response composer time (OpenAI)
        composer_start = time.time()
        response = self.response_composer.compose(
            draft=draft,
            message=message,
            state=self.memory.data,
            intent=str(self.memory.data.get("intent", "")),
            mode=ConversationMode.BOOKING if mode != ConversationMode.RESCUE else mode,
        )
        composer_latency = time.time() - composer_start

        if "openai_failure" in self.response_composer.last_error:
            source = "fallback"
        elif response != draft:
            source = "openai"
        else:
            source = "deterministic_template"
        debug = os.environ.get("BREAKOUT_DEBUG", "false").lower() == "true"
        if debug:
            print(f"response_source: {source}")

        self._update_last_discussed_topic(response)
        self.memory.add_turn("agent", response)

        # Print instrumentation details
        filled = [k for k, v in self.memory.data.items() if v and k in ("participants", "location", "preferred_date", "customer_name", "phone", "email")]
        missing = [k for k in ("participants", "location", "preferred_date", "customer_name", "phone", "email") if k not in filled]

        # Determine next expected field
        if self._state == self._STATE_WAITING_FOR_SLOT:
            next_field = "slot_selection"
        elif self._state == self._STATE_WAITING_FOR_AGE:
            next_field = "age_group"
        elif self._state == self._STATE_WAITING_FOR_NAME:
            next_field = "customer_name"
        elif self._state == self._STATE_WAITING_FOR_PHONE:
            next_field = "phone"
        elif self._state == self._STATE_WAITING_FOR_ALT_DATE:
            next_field = "alternative_date"
        elif self._state == self._STATE_BOOKING_CONFIRMED:
            next_field = "confirm"
        else:
            next_field = "intent"

        if debug:
            print(f"filled_fields: {filled}")
            print(f"missing_fields: {missing}")
            print(f"next_expected_field: {next_field}")

        openai_latency = composer_latency if source in ("openai", "fallback") else 0.0
        if debug:
            print(f"OpenAI latency: {openai_latency:.4f}s")
            print(f"openai_latency_ms: {openai_latency * 1000:.1f}")

        total_latency = time.time() - start_turn
        if debug:
            print(f"total response latency: {total_latency:.4f}s")
            print(f"total_latency_ms: {total_latency * 1000:.1f}")

        return AgentResponse(
            response=response,
            intent=str(self.memory.data.get("intent", "general_faq")),
            next_agent="booking_agent",
            should_handoff=self._state == self._STATE_BOOKING_CONFIRMED,
            state=self.memory.as_state(),
            booking_result=booking_result,
        )

    # ------------------------------------------------------------------ #
    # State handlers                                                       #
    # ------------------------------------------------------------------ #

    def _handle_availability_check(self) -> tuple[str, dict | None]:
        """
        First turn: call AvailabilityTool, transition to slot selection
        or alternative date request.
        """
        location = str(self.memory.data.get("location", ""))
        date = str(self.memory.data.get("preferred_date", ""))
        participants = int(self.memory.data.get("participants") or self.memory.data.get("company_size") or 2)

        if not location:
            return "Which Breakout location would you prefer?", None
        if not date:
            return f"Perfect, I've noted {location}. What date would you like to visit?", None

        # === TOOL CALL FIRST ===
        availability = self.availability_tool.check(location, date, participants)
        self._last_availability = availability

        if availability["available"]:
            self._available_slots = availability["slots"]
            self._state = self._STATE_WAITING_FOR_SLOT
            slots_text = self._format_slots(self._available_slots)
            name = str(self.memory.data.get("customer_name", ""))
            greeting = f"Thank you{', ' + name if name else ''}. " if name else ""
            return (
                f"{greeting}I've checked availability for {date} at {location}. "
                f"We have slots at {slots_text}. "
                f"Which time works best for you?"
            ), None
        else:
            self._state = self._STATE_WAITING_FOR_ALT_DATE
            return (
                f"I'm sorry, we don't have availability at {location} on {date}. "
                f"Could you share an alternative date you'd be comfortable with?"
            ), None

    def _handle_slot_selection(self, message: str) -> tuple[str, dict | None]:
        """
        Customer has chosen a slot — validate, then confirm the booking.
        """
        if not self.memory.data.get("preferred_date"):
            self._state = self._STATE_CHECKING_AVAILABILITY
            return "Before I check available time slots, what date would you like to visit?", None

        chosen = self._extract_slot(message)

        if not chosen:
            slots_text = self._format_slots(self._available_slots)
            return (
                f"Sorry, I didn't catch that. The available slots are {slots_text}. "
                f"Which time would you prefer?"
            ), None

        # Fuzzy-match: accept "3 PM", "3:00 pm", "three pm"
        matched = self._match_slot(chosen, self._available_slots)
        if not matched:
            slots_text = self._format_slots(self._available_slots)
            return (
                f"Sorry, {chosen} isn't one of the available slots. "
                f"You can choose from {slots_text}. Which would you prefer?"
            ), None

        self._selected_slot = matched

        if not self.memory.data.get("age_group"):
            self._state = self._STATE_WAITING_FOR_AGE
            return "Perfect, I've found that slot. Before I lock it in, are the players adults, kids, or a mix?", None
        if not self.memory.data.get("customer_name"):
            self._state = self._STATE_WAITING_FOR_NAME
            return f"Perfect. I've found an available slot at {matched}. Before I lock that in, may I get your name?", None
        if not self.memory.data.get("phone"):
            self._state = self._STATE_WAITING_FOR_PHONE
            return f"Perfect, {self.memory.data['customer_name']}. What's the best phone number for the booking?", None

        return self._prepare_selected_booking(matched)

    def _handle_age_group(self, message: str) -> tuple[str, dict | None]:
        age_group, age_detail = self.memory._extract_age_group(message.lower())
        if not age_group:
            return "Sorry, I didn't catch the age group. Are the players adults, kids, or a mix?", None
        self.memory.set_field("age_group", age_group, message, expected_field="age_group")
        if age_detail:
            self.memory.set_field("age_detail", age_detail, message, expected_field="age_group")
        if not self.memory.data.get("customer_name"):
            self._state = self._STATE_WAITING_FOR_NAME
            return f"Perfect. Before I lock in {self._selected_slot}, may I get your name?", None
        if not self.memory.data.get("phone"):
            self._state = self._STATE_WAITING_FOR_PHONE
            return f"Perfect, {self.memory.data['customer_name']}. What's the best phone number for the booking?", None
        return self._prepare_selected_booking(self._selected_slot)

    def _handle_contact_name(self, message: str) -> tuple[str, dict | None]:
        from .qualification_agent import QualificationAgent

        name = QualificationAgent._extract_bare_name(message)
        if not name:
            return "Sorry, I didn't catch the name. Could you say it again?", None
        self.memory.set_field("customer_name", name, message, expected_field="customer_name")
        self._state = self._STATE_WAITING_FOR_PHONE
        return f"Perfect, {name}. What's the best phone number for the booking?", None

    def _handle_contact_phone(self, message: str) -> tuple[str, dict | None]:
        phone = self.memory._extract_phone(message)
        if not phone:
            return "Sorry, I didn't catch the phone number. Could you repeat it?", None
        self.memory.set_field("phone", phone, message, expected_field="phone")
        return self._prepare_selected_booking(self._selected_slot)

    def _prepare_selected_booking(self, matched: str) -> tuple[str, dict | None]:
        if not self.memory.booking_ready():
            missing = self._missing_booking_fields()
            return f"I still need your {self._spoken_field(missing[0])} before I can prepare the booking.", None

        booking_result = self.booking_tool.create(self.memory.data, matched)
        if not booking_result.get("booking_id"):
            self._state = self._STATE_WAITING_FOR_SLOT
            return "Sorry, I couldn't prepare that booking. Let's confirm the details and try again.", None
        self._booking_result = booking_result
        self._state = self._STATE_BOOKING_CONFIRMED

        location = booking_result["location"]
        date = booking_result["date"]
        participants = booking_result["participants"]
        booking_id = booking_result["booking_id"]
        name = str(self.memory.data.get("customer_name", ""))

        if booking_result.get("confirmed"):
            return (
                f"Perfect{', ' + name if name else ''}. "
                f"Your booking is confirmed. "
                f"{participants} {'guest' if str(participants) == '1' else 'guests'} "
                f"at {location} on {date} at {matched}. "
                f"Your reference number is {booking_id}. "
                f"You'll receive a confirmation on the number you've provided. "
                f"{self._concierge_follow_up()}"
            ), booking_result
        return (
            f"Perfect{', ' + name if name else ''}. I've prepared the booking for "
            f"{participants} {'guest' if str(participants) == '1' else 'guests'} at {location} "
            f"on {date} at {matched}. Your checkout reference is {booking_id}. "
            f"The booking will be confirmed after checkout is completed. {self._concierge_follow_up()}"
        ), booking_result

    def _missing_booking_fields(self) -> list[str]:
        missing = [
            field
            for field in ("participants", "age_group", "location", "preferred_date", "customer_name", "phone")
            if not (
                self.memory.data.get(field)
                or (field == "participants" and self.memory.data.get("company_size"))
            )
        ]
        return missing

    @staticmethod
    def _spoken_field(field: str) -> str:
        return {
            "participants": "group size",
            "age_group": "age group",
            "preferred_date": "date",
            "customer_name": "name",
            "phone": "phone number",
        }.get(field, field)

    @staticmethod
    def _is_name_lookup(lowered: str) -> bool:
        cleaned = lowered.strip(" .!?")
        return cleaned in {"my name", "my name is what", "what is my name", "what's my name"}

    def _handle_alt_date(self, message: str) -> tuple[str, dict | None]:
        """
        Customer has provided an alternative date — re-check availability.
        """
        # Try to extract a new date from the message
        new_date = self.memory._extract_preferred_date(message)
        if not new_date:
            return (
                "Sorry, I didn't catch the date. Could you say something like "
                "20 June or next Saturday?"
            ), None

        # Update memory with the new date and re-run availability
        self.memory.data["preferred_date"] = new_date
        self.memory.save()

        location = str(self.memory.data.get("location", ""))
        participants = int(self.memory.data.get("participants") or self.memory.data.get("company_size") or 2)

        # === TOOL CALL FIRST ===
        availability = self.availability_tool.check(location, new_date, participants)
        self._last_availability = availability

        if availability["available"]:
            self._available_slots = availability["slots"]
            self._state = self._STATE_WAITING_FOR_SLOT
            slots_text = self._format_slots(self._available_slots)
            return (
                f"Great. We have availability at {location} on {new_date}. "
                f"The slots are {slots_text}. "
                f"Which time works best for you?"
            ), None
        else:
            # Still unavailable — ask again, but don't loop more than once
            self._state = self._STATE_CLOSED
            return (
                f"Unfortunately we don't have availability at {location} on {new_date} either. "
                f"I'd recommend calling our team directly so they can check all upcoming slots "
                f"and get you the best option. Shall I arrange for someone to call you back?"
            ), None

    def _handle_post_booking(self, message: str) -> tuple[str, dict | None]:
        """
        After booking is confirmed — answer any residual questions and close.
        """
        self._state = self._STATE_CLOSED
        lowered = message.lower().strip(" .!?")

        # Clean punctuation to avoid splitting issues, keeping apostrophes for that's/we're
        import re
        cleaned = re.sub(r'[^a-z0-9\s\']', ' ', lowered)
        words = cleaned.split()

        goodbye_words_set = {"no", "nothing", "thanks", "thank", "bye", "goodbye", "ok", "okay", "okays", "fine", "none"}
        goodbye_phrases = ["thats all", "that is all", "all set", "thank you", "we are good", "we're good", "no thanks", "no thank you", "no that's all", "no thats all"]
        inquiry_keywords = {
            "food", "parking", "cancellation", "cancel", "policy", "rules", "room", "rooms",
            "arrival", "guidance", "direction", "directions", "where", "how", "what", "menu",
            "slot", "slots", "change", "modify", "reschedule"
        }

        has_goodbye = any(w in words for w in goodbye_words_set) or any(p in cleaned for p in goodbye_phrases)
        has_inquiry = any(kw in words for kw in inquiry_keywords)

        is_goodbye = (has_goodbye and not has_inquiry) or (len(words) <= 3 and any(w in words for w in goodbye_words_set))

        if is_goodbye:
            self.memory.data["completed_booking"] = True
            self.memory.save()
            return (
                "Great. Thanks for choosing Breakout. Have a wonderful day."
            ), self._booking_result

        # Answer post-booking FAQs and append concierge follow up
        from ..knowledge.demo_knowledge import get_demo_answer
        if "food" in lowered:
            interruption_answer = (
                "Food options include continental food, build-your-menu options, mix snack boxes, "
                "hi-tea options, and Indian buffet options for corporate events."
            )
        else:
            interruption_answer = get_demo_answer(message)

        if interruption_answer:
            return f"{interruption_answer} {self._concierge_follow_up()}", self._booking_result

        return (
            self._concierge_follow_up()
        ), self._booking_result

    @staticmethod
    def _concierge_follow_up() -> str:
        return (
            "Perfect, that's all set from my side. Anything you'd like to know before you come in, "
            "like food options, parking, the cancellation policy, or arrival guidance?"
        )

    @staticmethod
    def _cancellation_policy_explanation() -> str:
        return (
            "Sure. Cancellations made 3 days or more in advance have no cancellation fee. "
            "The fee is 25% with less than 3 days' notice, 50% with less than 2 days, and 75% with less than 1 day. "
            "Cancellations less than 2 hours before the slot, and no-shows, are not refundable."
        )

    def _contextual_best_response(self) -> str:
        topic = self.memory.data.get("last_discussed_topic", "")
        if topic == "food":
            return (
                "If you want a full meal, I'd narrow it to the Indian buffet. "
                "For lighter refreshments, hi-tea or the mixed snack boxes are a better fit. "
                "Are you planning a full meal or something lighter?"
            )
        if topic == "rooms":
            discussed = self.memory.data.get("discussed_options", [])
            if "Murder Mystery" in discussed and "Hostage" in discussed:
                return (
                    "I'd lean toward Murder Mystery for a more investigation-led experience. "
                    "Hostage is the alternative if your group wants more urgency. "
                    "Would you prefer mystery-solving or a faster-paced challenge?"
                )
            if discussed:
                return f"Based on what we discussed, I'd narrow it to {discussed[-1]}. Would you like the room details?"
        return "For packages, the best fit depends on the group and occasion. How many guests are you planning for?"

    def _update_last_discussed_topic(self, response_text: str) -> None:
        lowered = response_text.lower()
        rooms = (
            "Murder Mystery", "Hostage", "Prison Break", "Classified", "Undercover",
            "Bomb Defusal", "The Wizarding Championship", "Curse of the Pharaoh", "The Forbidden Forest",
        )
        discussed = list(self.memory.data.get("discussed_options", []))
        for room in rooms:
            if room.lower() in lowered:
                if room in discussed:
                    discussed.remove(room)
                discussed.append(room)
        if any(room.lower() in lowered for room in rooms):
            self.memory.data["discussed_options"] = discussed[-5:]
            self.memory.data["last_discussed_topic"] = "rooms"
        elif any(term in lowered for term in ("food options", "indian buffet", "hi-tea", "snack box", "continental food")):
            self.memory.data["last_discussed_topic"] = "food"
        elif "package" in lowered:
            self.memory.data["last_discussed_topic"] = "packages"
        self.memory.save()

    def _append_booking_resume(self, answer: str) -> str:
        if not self.memory.data.get("preferred_date"):
            return f"{answer} What date would you like to visit?"
        if self._state == self._STATE_WAITING_FOR_SLOT:
            slots_text = self._format_slots(self._available_slots)
            return f"{answer} Your available times are {slots_text}. Which time works best?"
        if self._state == self._STATE_WAITING_FOR_ALT_DATE:
            return f"{answer} What alternative date would work for you?"
        return answer

    # ------------------------------------------------------------------ #
    # Slot helpers                                                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _format_slots(slots: list[str]) -> str:
        """Format slot list naturally: '10:00 AM, 12:00 PM, and 3:00 PM'."""
        if not slots:
            return "no available slots"
        if len(slots) == 1:
            return slots[0]
        return ", ".join(slots[:-1]) + f", and {slots[-1]}"

    @staticmethod
    def _extract_slot(message: str) -> str:
        """
        Extract a time reference from the customer's message.
        Handles: "3 PM", "3:00 PM", "3pm", "15:00", "three pm"
        """
        lowered = message.lower().strip()

        # Normalise number words for times
        _WORDS = {
            "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
            "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
            "eleven": "11", "twelve": "12",
        }
        for word, digit in _WORDS.items():
            lowered = re.sub(rf"\b{word}\b", digit, lowered)

        # Try to match time patterns
        # "3:00 PM" / "10:00 AM" / "3pm" / "3 pm"
        match = re.search(
            r"\b(\d{1,2})(?::(\d{2})?)?\s*(am|pm)\b",
            lowered,
            re.IGNORECASE,
        )
        if match:
            hour = str(int(match.group(1)))
            minute = match.group(2) or "00"
            period = match.group(3).upper()
            return f"{hour}:{minute} {period}"

        # "15:00" style 24-hr
        match24 = re.search(r"\b(\d{2}):(\d{2})\b", lowered)
        if match24:
            h, m = int(match24.group(1)), match24.group(2)
            period = "AM" if h < 12 else "PM"
            hour12 = h if h <= 12 else h - 12
            if hour12 == 0:
                hour12 = 12
            return f"{hour12}:{m} {period}"

        return ""

    @staticmethod
    def _match_slot(chosen: str, available: list[str]) -> str:
        """
        Fuzzy-match the extracted slot against the available list.
        Treats "3:00 PM" == "3 PM" by normalising to hour+period.
        """
        def normalise(s: str) -> str:
            s = s.strip().upper()
            # Strip leading zero from hour: "03:00 PM" → "3:00 PM"
            s = re.sub(r"^0(\d)", r"\1", s)
            # "3:00 PM" → "3 PM" for comparison
            s = re.sub(r":00\b", "", s)
            return s

        chosen_n = normalise(chosen)
        for slot in available:
            if normalise(slot) == chosen_n:
                return slot
        return ""

    @staticmethod
    def _clean_response(response: str) -> str:
        """Strip whitespace and normalise."""
        return " ".join(response.split())


class BookingSimulator:
    """
    Simple in-memory booking simulator for scheduling and reservation management.
    Renamed from BookingAgent to avoid naming conflicts.
    """

    def __init__(self) -> None:
        # slot_id -> Slot
        self.slots: Dict[str, Slot] = {}
        # booking_ref -> BookingRecord
        self.bookings: Dict[str, BookingRecord] = {}
        # simple lock to protect in-memory structures
        self._lock = threading.Lock()

    # -------------------- Calendar / availability --------------------
    def add_slot(self, date: str, time_str: str, service: str, capacity: int = 10) -> str:
        slot_id = str(uuid.uuid4())
        slot = Slot(slot_id=slot_id, date=date, time=time_str, service=service, capacity=capacity)
        with self._lock:
            self.slots[slot_id] = slot
        return slot_id

    def check_availability(self, service: str, date: Optional[str] = None) -> List[Slot]:
        with self._lock:
            result = [
                s
                for s in self.slots.values()
                if s.service == service
                and (date is None or s.date == date)
                and s.booking_ref is None
                and s.locked_by is None
            ]
        return result

    # -------------------- Slot locking & booking --------------------
    def lock_slot(self, slot_id: str, owner_id: str, ttl_seconds: int = 30) -> bool:
        with self._lock:
            slot = self.slots.get(slot_id)
            if not slot:
                raise BookingError("slot_not_found")
            if slot.locked_by or slot.booking_ref:
                return False
            slot.locked_by = owner_id

        # start a background timer to release the lock
        def _release():
            time.sleep(ttl_seconds)
            with self._lock:
                s = self.slots.get(slot_id)
                if s and s.locked_by == owner_id and not s.booking_ref:
                    s.locked_by = None

        threading.Thread(target=_release, daemon=True).start()
        return True

    def unlock_slot(self, slot_id: str, owner_id: str) -> None:
        with self._lock:
            slot = self.slots.get(slot_id)
            if slot and slot.locked_by == owner_id and not slot.booking_ref:
                slot.locked_by = None

    def create_booking(self, slot_id: str, customer: Dict[str, Any], require_payment: bool = False) -> BookingRecord:
        with self._lock:
            slot = self.slots.get(slot_id)
            if not slot:
                raise BookingError("slot_not_found")
            if slot.booking_ref:
                raise BookingError("already_booked")
            # ensure slot is locked or free
            if slot.locked_by and slot.locked_by != customer.get("owner_id"):
                raise BookingError("slot_locked")

            # create booking
            ref = f"BK-{uuid.uuid4().hex[:8]}"
            payload = None
            if require_payment:
                amount = self._estimate_amount(slot, customer)
                payload = self.generate_payment_payload(amount, ref)

            record = BookingRecord(
                reference=ref,
                slot_id=slot_id,
                customer=customer,
                payment_required=require_payment,
                payment_payload=payload
            )
            self.bookings[ref] = record
            slot.booking_ref = ref
            slot.locked_by = None
            return record

    def _estimate_amount(self, slot: Slot, customer: Dict[str, Any]) -> int:
        # naive price estimator: base price plus per-person
        base = 1000
        per_person = 150
        participants = int(customer.get("participants") or 1)
        return base + per_person * participants

    def generate_payment_payload(self, amount: int, booking_ref: str) -> Dict[str, Any]:
        return {
            "booking_ref": booking_ref,
            "amount": amount,
            "currency": "INR",
            "payment_gateway": "sample_gateway",
        }

    # -------------------- Reschedule / cancel --------------------
    def reschedule(self, booking_ref: str, new_slot_id: str) -> BookingRecord:
        with self._lock:
            record = self.bookings.get(booking_ref)
            if not record:
                raise BookingError("booking_not_found")
            new_slot = self.slots.get(new_slot_id)
            if not new_slot:
                raise BookingError("slot_not_found")
            if new_slot.booking_ref:
                raise BookingError("slot_unavailable")
            # move booking
            old_slot = self.slots.get(record.slot_id)
            if old_slot:
                old_slot.booking_ref = None
            new_slot.booking_ref = booking_ref
            record.slot_id = new_slot_id
            return record

    def cancel(self, booking_ref: str, reason: Optional[str] = None) -> BookingRecord:
        with self._lock:
            record = self.bookings.get(booking_ref)
            if not record:
                raise BookingError("booking_not_found")
            record.status = "cancelled"
            slot = self.slots.get(record.slot_id)
            if slot:
                slot.booking_ref = None
            return record

    # -------------------- Helpers / integrations --------------------
    def get_booking(self, booking_ref: str) -> Optional[BookingRecord]:
        return self.bookings.get(booking_ref)

    def available_slots_payload(self, service: str, date: Optional[str] = None) -> List[Dict[str, Any]]:
        slots = self.check_availability(service, date)
        return [
            {"slot_id": s.slot_id, "date": s.date, "time": s.time, "service": s.service, "capacity": s.capacity}
            for s in slots
        ]
