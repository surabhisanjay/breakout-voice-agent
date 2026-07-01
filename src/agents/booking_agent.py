"""
BookingAgent — handles slot confirmation and booking creation (Voice Agent).
BookingSimulator — in-memory booking backend simulator.
"""
from __future__ import annotations

import os
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.agent_response import AgentResponse
from ..orchestration.booking_orchestrator import BookingOrchestrator
from ..core.conversation_modes import ConversationMode, ConversationModeDetector
from ..memory.conversation_memory import ConversationMemory
from ..response_composer import ResponseComposer
from ..services.whatsapp_payload import build_wati_booking_payload
from ..services.wati_client import WatiClient, WatiSendResult
from ..services.venue_policy import get_venue_policy


logger = logging.getLogger(__name__)


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
    _ESCAPE_ROOM_NAMES = {
        "murder mystery", "hostage", "classified", "bomb defusal", "bomb diffusal",
        "prison break", "undercover", "curse of the pharaoh",
        "the wizarding championship", "the forbidden forest",
    }
    # Internal conversation states
    _STATE_CHECKING_AVAILABILITY = "checking_availability"
    _STATE_WAITING_FOR_SLOT = "waiting_for_slot"
    _STATE_WAITING_FOR_AGE = "waiting_for_age"
    _STATE_WAITING_FOR_FIRST_NAME = "waiting_for_first_name"
    _STATE_WAITING_FOR_LAST_NAME = "waiting_for_last_name"
    _STATE_WAITING_FOR_PHONE = "waiting_for_phone"
    _STATE_READY_FOR_BOOKING = "ready_for_booking"
    _STATE_WAITING_FOR_ALT_DATE = "waiting_for_alt_date"
    _STATE_BOOKING_CONFIRMED = "booking_confirmed"
    _STATE_CLOSED = "closed"
    _STATE_COORDINATION = "coordination"
    _STATE_WAITING_FOR_BOOKING_REF = "waiting_for_booking_ref"
    _STATE_WAITING_FOR_NAME = _STATE_WAITING_FOR_FIRST_NAME

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
                explicit_room = str(self.mem.get("room") or "").strip()
                recommendation = str(self.mem.get("recommended_option") or "").strip()
                room = explicit_room or (
                    recommendation if recommendation.lower() in BookingAgent._ESCAPE_ROOM_NAMES else ""
                )
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
        self._selected_slot: str = str(memory.data.get("selected_slot") or "")
        if memory.data.get("booking_id") and memory.data.get("booking_ref"):
            self._state = self._STATE_BOOKING_CONFIRMED

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
        message = self.memory.apply_reference_aliases(
            self.memory.normalize_entity_aliases(message)
        )
        start_turn = time.time()
        self.memory.add_turn("customer", message)

        if self.memory.data.get("completed_booking"):
            response = "Great. Thanks for choosing Breakout. Have a wonderful day."
            return self._finalize_response(response, message, start_turn, self._booking_result)

        if self._state == self._STATE_COORDINATION:
            response, booking_result = self._handle_coordination(message)
            return self._finalize_response(response, message, start_turn, booking_result)
        if self._state == self._STATE_WAITING_FOR_BOOKING_REF:
            response, booking_result = self._handle_booking_reference(message)
            return self._finalize_response(response, message, start_turn, booking_result)

        lowered = message.lower()
        normalized_lowered = self.memory.normalize_number_words(message).lower()

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
        if not target_room:
            target_room = self.memory._extract_room(lowered)

        # Check for location, participants, or date modification
        target_location = self.memory._extract_location(lowered)
        participant_range = self.memory._extract_participant_range(normalized_lowered)
        target_participants = (
            participant_range[1]
            if participant_range
            else self.memory._extract_participants(normalized_lowered)
        )
        target_date = self.memory._extract_preferred_date(message)
        target_phone = self.memory._extract_phone(message)
        old_room = self.memory.data.get("room")
        old_location = self.memory.data.get("location")
        old_date = self.memory.data.get("preferred_date")
        old_participants = self.memory.data.get("participants")
        from .qualification_agent import QualificationAgent
        has_explicit_name_signal = bool(
            re.search(
                r"\b(?:name\s*:|name\s+|my first name is|my name is|i am|i'm|this is|book under|actually\s+(?:use|make it|change(?:\s+it)?\s+to))\b",
                lowered,
            )
        )
        target_name = (
            QualificationAgent._extract_bare_name(message)
            if (
                has_explicit_name_signal
                and not target_room
                and not target_location
                and not target_date
                and not target_participants
            )
            or self._state in (self._STATE_WAITING_FOR_FIRST_NAME, self._STATE_WAITING_FOR_LAST_NAME)
            else ""
        )
        if (
            not target_name
            and not target_room
            and re.fullmatch(r"\s*actually\s+[A-Za-z]+\s+[A-Za-z]+(?:\s+[A-Za-z]+)?\s*[.!?]?\s*", message, re.IGNORECASE)
        ):
            target_name = QualificationAgent._extract_bare_name(
                re.sub(r"^\s*actually\s+", "", message, flags=re.IGNORECASE)
            )
        if (
            not target_name
            and not target_room
            and not target_location
            and not target_date
            and not target_participants
            and re.fullmatch(r"\s*[A-Za-z][A-Za-z.'-]+(?:\s+[A-Za-z][A-Za-z.'-]+){1,2}\s*[.!?]?\s*", message)
        ):
            target_name = QualificationAgent._extract_bare_name(message)
        participants_changed = bool(
            target_participants
            and target_participants != old_participants
        )

        # Participant corrections are operational inputs, not low-confidence
        # conversation changes. Persist them before any capacity decision.
        if participants_changed:
            self.memory.set_field(
                "participants",
                target_participants,
                message,
                expected_field="participants",
            )

        # A required field may be supplied naturally while another field is
        # still missing. Capture it immediately before any availability check.
        if target_date and target_date != old_date:
            self.memory.set_field("preferred_date", target_date, message, expected_field="preferred_date")
        if target_location and not self.memory.data.get("location"):
            self.memory.set_field("location", target_location, message, expected_field="location")
        if target_name and self._state != self._STATE_WAITING_FOR_LAST_NAME:
            self._store_name_parts(target_name, message)
        if target_phone and not self.memory.data.get("phone"):
            self.memory.set_field("phone", target_phone, message, expected_field="phone")
            logger.info("PHONE_PERSISTED=true")

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
                r"\b(?:cancel|delete)\s+(?:my\s+|the\s+|this\s+)?(?:booking|reservation|appointment|slot)\b",
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

        slot_input = self._extract_slot(message)
        if slot_input and slot_input != self.memory.data.get("preferred_time"):
            self.memory.set_field("preferred_time", slot_input, message, expected_field="preferred_time")

        # Check if any field is different from the memory snapshot at the
        # start of the turn. Some fields are persisted above by design.
        room_changed = bool(target_room and target_room != old_room)
        location_changed = bool(target_location and target_location != old_location)
        date_changed = bool(target_date and target_date != old_date)

        any_field_changed = room_changed or location_changed or participants_changed or date_changed
        
        is_change_request = any_field_changed and (
            any(w in lowered for w in ("actually", "correct", "change", "instead", "switch", "want", "prefer", "rather", "choose", "update", "modify", "reschedule", "now", "different")) or
            participants_changed
            or self._state in (self._STATE_WAITING_FOR_SLOT, self._STATE_WAITING_FOR_ALT_DATE)
        )

        is_slot_availability_question = bool(
            slot_input
            and re.search(r"\b(?:available|availability|free|open)\b", lowered)
        )
        is_price_question = bool(
            re.search(r"\b(?:price|pricing|cost|costs|rate|rates|how much)\b", lowered)
        )
        is_discount_question = bool(
            re.search(r"\b(?:discount|offers?|coupon|promo|deal|membership)\b", lowered)
        )
        is_slot_summary_question = self._is_slot_summary_question(lowered)
        is_slot_fallback_question = bool(
            re.search(
                r"\b(?:what\s+if|if)\b.{0,30}\b(?:slot|time)\b.{0,20}\b(?:unavailable|not available|taken|gone)\b",
                lowered,
            )
        )
        explicit_change_words = bool(
            re.search(r"\b(?:actually|change|switch|instead|rather|make it|update|modify|reschedule|use)\b", lowered)
        )
        explicit_slot_change = bool(
            slot_input
            and explicit_change_words
            and slot_input != self.memory.data.get("selected_slot")
        )
        explicit_room_change = bool(
            target_room
            and explicit_change_words
            and self._state in (self._STATE_WAITING_FOR_SLOT, self._STATE_WAITING_FOR_ALT_DATE, self._STATE_CHECKING_AVAILABILITY)
        )
        explicit_date_change = bool(
            target_date
            and explicit_change_words
            and self._state in (self._STATE_WAITING_FOR_SLOT, self._STATE_WAITING_FOR_ALT_DATE, self._STATE_CHECKING_AVAILABILITY)
        )

        if is_slot_fallback_question:
            selected = str(self.memory.data.get("selected_slot") or self._selected_slot or "that slot")
            alternatives = [slot for slot in self._available_slots if slot != selected]
            nearest = self._nearest_slots(selected, alternatives, limit=2) if selected != "that slot" else alternatives[:2]
            alternative_text = self._format_slots(nearest) if nearest else "the next live options"
            response = self._append_booking_resume(
                f"If {selected} becomes unavailable, I won't switch you silently. "
                f"I'll recheck live inventory and offer the nearest verified alternatives, currently {alternative_text}."
            )
            booking_result = None

        elif is_slot_summary_question:
            response = self._slot_summary_response(message)
            booking_result = None

        elif explicit_room_change:
            self.memory.set_field("room", target_room, message, expected_field="room")
            self._clear_selected_slot()
            self._state = self._STATE_CHECKING_AVAILABILITY
            response = self._room_change_acknowledgement(target_room)
            booking_result = None

        elif explicit_date_change:
            self.memory.set_field("preferred_date", target_date, message, expected_field="preferred_date")
            self._clear_selected_slot()
            self._state = self._STATE_CHECKING_AVAILABILITY
            response = self._date_change_acknowledgement(target_date)
            booking_result = None

        elif is_slot_availability_question:
            matched = self._match_slot(slot_input, self._available_slots)
            room = str(self.memory.data.get("room", "the room"))
            location = str(self.memory.data.get("location", "the selected location"))
            date = str(self.memory.data.get("preferred_date", "the selected date"))
            if matched:
                response = f"Yes, {matched} is available for {room} at {location} on {date}."
            else:
                nearest = self._nearest_slots(slot_input, self._available_slots, limit=3)
                self.memory.data["last_suggested_slots"] = nearest
                self.memory.save()
                slots_text = self._format_slots(nearest or self._available_slots)
                response = (
                    f"No, {slot_input} is not in the cached availability for {room} at {location} on {date}. "
                    f"The nearest available slots are {slots_text}."
                )
            booking_result = None

        elif is_discount_question:
            response = (
                "I don't have confirmed discount information from the booking system right now, "
                "but I can continue with the booking and our team can confirm the final amount."
            )
            booking_result = None

        elif explicit_slot_change:
            matched = self._match_slot(slot_input, self._available_slots) or slot_input
            self._selected_slot = matched
            self.memory.data["selected_slot"] = matched
            self.memory.data["slot_confirmed"] = True
            self.memory.save()
            response = f"Sure, I've updated your slot to {matched}."
            booking_result = None

        elif is_price_question:
            response = (
                "I don't have exact pricing from the booking system right now, but I can continue "
                "with the booking and our team can confirm the final amount."
            )
            booking_result = None

        elif self.memory.data.get("pending_policy_explanation") and is_affirmative_follow_up:
            self.memory.data["pending_policy_explanation"] = False
            self.memory.save()
            response = self._append_booking_resume(self._cancellation_policy_explanation())
            booking_result = None

        elif is_contextual_best and self.memory.data.get("last_discussed_topic"):
            response = self._append_booking_resume(self._contextual_best_response())
            booking_result = None

        elif is_cancellation_policy_question:
            self.memory.data["pending_policy_explanation"] = False
            self.memory.save()
            response = self._append_booking_resume(self._cancellation_policy_explanation())
            booking_result = None

        elif is_cancel_request:
            booking_ref = self.memory.data.get("booking_ref") or (self._booking_result or {}).get("booking_id")
            if booking_ref:
                response = self._cancel_booking(str(booking_ref))
            else:
                response = (
                    "I don't have a booking reference on file to cancel. "
                    "Could you share your booking ID or reference number?"
                )
                self._state = self._STATE_WAITING_FOR_BOOKING_REF
            booking_result = None

        elif is_change_request:
            changes_desc = []

            if room_changed or location_changed or date_changed:
                self._clear_selected_slot()
            
            if room_changed:
                self.memory.set_field("room", target_room, message, expected_field="room")
                changes_desc.append(f"room to {target_room}")
                
            if location_changed:
                self.memory.set_field("location", target_location, message, expected_field="location")
                changes_desc.append(f"location to {target_location}")
                
            if participants_changed:
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

            if availability is not None and availability.get("available") and availability.get("capacity_supported") is False:
                self._available_slots = []
                response = f"{ack}{self._start_coordination(availability, participants)}"
            elif availability is not None and availability["available"]:
                self._available_slots = self._visible_slots(availability)
                self._state = self._STATE_WAITING_FOR_SLOT
                if self._available_slots:
                    slots_text = self._format_slots(self._available_slots)
                    period = self._period_label_prefix()
                    response = f"{ack}For {date} at {location}, I found {period}slots at {slots_text} for {participants} players. Which time works best for you?"
                else:
                    response = f"{ack}I found availability on {date} at {location}, but no verified {self.memory.data.get('preferred_period')} slots for {participants} players. Could you share another time window?"
            elif availability is not None:
                self._state = self._STATE_WAITING_FOR_ALT_DATE
                response = f"{ack}I checked availability on {date} at {location} for {participants} players, but unfortunately we don't have slots. Could you share an alternative date?"

            booking_result = None
        else:
            age_input = self.memory._extract_age_group(lowered, allow_bare_range=True)[0]
            actionable_state_input = (
                (self._state == self._STATE_WAITING_FOR_SLOT and bool(slot_input))
                or (self._state == self._STATE_WAITING_FOR_AGE and bool(age_input))
                or (self._state == self._STATE_WAITING_FOR_FIRST_NAME and bool(target_name))
                or (self._state == self._STATE_WAITING_FOR_LAST_NAME and bool(target_name))
                or (self._state == self._STATE_WAITING_FOR_PHONE and bool(target_phone))
                or (self._state == self._STATE_WAITING_FOR_ALT_DATE and bool(target_date))
            )
            if actionable_state_input:
                if self._state == self._STATE_WAITING_FOR_SLOT:
                    response, booking_result = self._handle_slot_selection(message)
                elif self._state == self._STATE_WAITING_FOR_AGE:
                    response, booking_result = self._handle_age_group(message)
                elif self._state == self._STATE_WAITING_FOR_FIRST_NAME:
                    response, booking_result = self._handle_contact_first_name(message)
                elif self._state == self._STATE_WAITING_FOR_LAST_NAME:
                    response, booking_result = self._handle_contact_last_name(message)
                elif self._state == self._STATE_WAITING_FOR_PHONE:
                    response, booking_result = self._handle_contact_phone(message)
                else:
                    response, booking_result = self._handle_alt_date(message)
                return self._finalize_response(response, message, start_turn, booking_result)

            # Check for general FAQ / room explanations (interruption). Once
            # booking owns the workflow, missing-field and tool progression win.
            from ..knowledge.demo_knowledge import get_demo_answer
            booking_started = bool(self.memory.data.get("booking_started"))
            if booking_started:
                interruption_answer = self._booking_faq_answer(message) if self._is_booking_faq(message) else None
            elif "food" in lowered:
                interruption_answer = self._food_faq_answer(message)
            else:
                interruption_answer = get_demo_answer(message)

            if interruption_answer:
                response = self._append_booking_resume(interruption_answer)
                booking_result = None
            else:
                # Normal booking state machine
                if self._state == self._STATE_CHECKING_AVAILABILITY:
                    response, booking_result = self._handle_availability_check()
                    requested_slot = self._extract_slot(message)
                    if requested_slot and self._state == self._STATE_WAITING_FOR_SLOT:
                        response, booking_result = self._handle_slot_selection(message)

                elif self._state == self._STATE_WAITING_FOR_SLOT:
                    # Bug 5 guard: if slot is already confirmed and customer sends a bare
                    # affirmative ("yes", "ok", "sure"), do NOT re-ask for slot selection.
                    # Advance to the next required field instead.
                    slot_already_confirmed = (
                        bool(self.memory.data.get("slot_confirmed"))
                        and bool(self._selected_slot)
                        and is_affirmative_follow_up
                        and not slot_input
                    )
                    if slot_already_confirmed:
                        if not self.memory.data.get("age_group"):
                            self._state = self._STATE_WAITING_FOR_AGE
                            response = "Are the players adults, kids, or a mix?"
                            booking_result = None
                        elif not self.memory.data.get("customer_name"):
                            self._state = self._STATE_WAITING_FOR_FIRST_NAME
                            response = "May I have your name?"
                            booking_result = None
                        elif not self.memory.data.get("phone"):
                            self._state = self._STATE_WAITING_FOR_PHONE
                            response = "What's the best phone number for the booking?"
                            booking_result = None
                        else:
                            response, booking_result = self._prepare_selected_booking(self._selected_slot)
                    else:
                        response, booking_result = self._handle_slot_selection(message)

                elif self._state == self._STATE_WAITING_FOR_AGE:
                    response, booking_result = self._handle_age_group(message)

                elif self._state == self._STATE_WAITING_FOR_FIRST_NAME:
                    response, booking_result = self._handle_contact_first_name(message)

                elif self._state == self._STATE_WAITING_FOR_LAST_NAME:
                    response, booking_result = self._handle_contact_last_name(message)

                elif self._state == self._STATE_WAITING_FOR_PHONE:
                    response, booking_result = self._handle_contact_phone(message)

                elif self._state == self._STATE_READY_FOR_BOOKING:
                    response, booking_result = self._prepare_selected_booking(self._selected_slot)

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
        elif self._state == self._STATE_WAITING_FOR_FIRST_NAME:
            next_field = "first_name"
        elif self._state == self._STATE_WAITING_FOR_LAST_NAME:
            next_field = "last_name"
        elif self._state == self._STATE_WAITING_FOR_PHONE:
            next_field = "phone"
        elif self._state == self._STATE_READY_FOR_BOOKING:
            next_field = "ready_for_booking"
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

        coordination_ready = bool(
            self._state == self._STATE_COORDINATION
            and self.memory.data.get("coordination_ready")
        )
        return AgentResponse(
            response=response,
            intent=str(self.memory.data.get("intent", "general_faq")),
            next_agent="events_team" if coordination_ready else "booking_agent",
            should_handoff=(
                self._state == self._STATE_BOOKING_CONFIRMED or coordination_ready
            ),
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
            return self._missing_field_response("location"), None
        if not self.memory.data.get("room") and self.memory.data.get("intent") == "escape_room_inquiry":
            return "Which escape room would you like to book?", None
        if not date:
            return self._missing_field_response("preferred_date"), None
        if self._is_package_inquiry():
            return self._start_event_coordination(), None

        # === TOOL CALL FIRST ===
        availability = self.availability_tool.check(location, date, participants)
        self._last_availability = availability

        if availability.get("available") and availability.get("capacity_supported") is False:
            self._available_slots = []
            return self._start_coordination(availability, participants), None
        if availability["available"]:
            self._available_slots = self._visible_slots(availability)
            self._state = self._STATE_WAITING_FOR_SLOT
            if not self._available_slots:
                period = str(self.memory.data.get("preferred_period") or "requested").strip()
                follow_up = (
                    "Would a different day work?"
                    if period.lower() == "evening"
                    else "Could you share another time window?"
                )
                return (
                    f"I found availability at {location} on {date}, but no verified {period} slots for "
                    f"{participants} players. {follow_up}"
                ), None
            persisted_slot = str(self.memory.data.get("selected_slot") or self.memory.data.get("preferred_time") or "")
            if persisted_slot and self._match_slot(persisted_slot, self._available_slots):
                return self._handle_slot_selection(persisted_slot)
            slots_text = self._format_slots(self._available_slots)
            name = str(self.memory.data.get("customer_name", ""))
            greeting = f"Thank you{', ' + name if name else ''}. " if name else ""
            period = self._period_label_prefix()
            return (
                f"{greeting}I've checked availability for {date} at {location}. "
                f"We have {period}slots at {slots_text}. "
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

        if chosen and self._available_slots and re.search(
            r"\b(?:closest|nearest|whichever\s+is\s+(?:close|closest|nearest))\b",
            message.lower(),
        ):
            matched_or_nearest = self._match_slot(chosen, self._available_slots)
            if not matched_or_nearest:
                nearest = self._nearest_slots(chosen, self._available_slots, limit=1)
                matched_or_nearest = nearest[0] if nearest else ""
            if matched_or_nearest:
                chosen = matched_or_nearest

        # Handle "first available", "first slot", "earliest one", ordinal phrasing
        if not chosen and self._available_slots:
            lowered_msg = message.lower()
            first_slot_phrases = (
                "first available", "first slot", "first one", "earliest",
                "first time", "go with first", "take the first", "first option",
            )
            if any(phrase in lowered_msg for phrase in first_slot_phrases):
                earliest = self._sorted_slots(self._available_slots)[0]
                chosen = self._extract_slot(earliest)
                if not chosen:
                    chosen = earliest
            elif (
                lowered_msg.strip(" .!?") in {"that works", "works", "works for me", "let's do it", "lets do it", "okay", "ok"}
                or "that works" in lowered_msg
                or "let's do it" in lowered_msg
                or "lets do it" in lowered_msg
            ):
                suggested = [
                    slot for slot in self.memory.data.get("last_suggested_slots", [])
                    if isinstance(slot, str)
                ]
                if suggested:
                    chosen = suggested[0]

        if not chosen:
            slots_text = self._format_slots(self._available_slots)
            return (
                f"Sorry, I didn't catch that. The available slots are {slots_text}. "
                f"Which time would you prefer?"
            ), None

        # Fuzzy-match: accept "3 PM", "3:00 pm", "three pm"
        matched = self._match_slot(chosen, self._available_slots)
        if not matched:
            nearest = self._nearest_slots(chosen, self._available_slots, limit=3)
            if re.search(r"\b(?:around|about)\b", message.lower()):
                preferred = self.select_evening_slot(self._available_slots)
                if preferred:
                    nearest = [preferred, *[slot for slot in nearest if slot != preferred]][:3]
            self.memory.data["last_suggested_slots"] = nearest
            self.memory.save()
            slots_text = self._format_slots(nearest or self._available_slots)
            return (
                f"Sorry, {chosen} isn't one of the available slots. "
                f"The nearest available slots are {slots_text}. Which would you prefer?"
            ), None

        self._selected_slot = matched
        self.memory.data["selected_slot"] = matched
        # Mark slot as confirmed so we never re-ask for confirmation on subsequent affirmatives
        self.memory.data["slot_confirmed"] = True
        self.memory.save()
        self._ensure_name_parts()

        if not self.memory.data.get("age_group"):
            self._state = self._STATE_WAITING_FOR_AGE
            return "Perfect, I've found that slot. Before I lock it in, are the players adults, kids, or a mix?", None
        if not self.memory.data.get("customer_name"):
            self._state = self._STATE_WAITING_FOR_FIRST_NAME
            return f"Perfect. I've found an available slot at {matched}. May I have your name?", None
        if not self.memory.data.get("phone"):
            self._state = self._STATE_WAITING_FOR_PHONE
            return f"Perfect, {self.memory.data.get('first_name') or self.memory.data.get('customer_name')}. What's the best phone number for the booking?", None

        return self._prepare_selected_booking(matched)

    def _handle_age_group(self, message: str) -> tuple[str, dict | None]:
        age_group, age_detail = self.memory._extract_age_group(message.lower(), allow_bare_range=True)
        if not age_group:
            return "Sorry, I didn't catch the age group. Are the players adults, kids, or a mix?", None
        self.memory.set_field("age_group", age_group, message, expected_field="age_group")
        if age_detail:
            self.memory.set_field("age_detail", age_detail, message, expected_field="age_group")
        self._ensure_name_parts()
        if not self.memory.data.get("customer_name"):
            self._state = self._STATE_WAITING_FOR_FIRST_NAME
            return f"Perfect. Before I lock in {self._selected_slot}, may I have your name?", None
        if not self.memory.data.get("phone"):
            self._state = self._STATE_WAITING_FOR_PHONE
            return f"Perfect, {self.memory.data.get('first_name') or self.memory.data.get('customer_name')}. What's the best phone number for the booking?", None
        return self._prepare_selected_booking(self._selected_slot)

    def _handle_contact_first_name(self, message: str) -> tuple[str, dict | None]:
        from .qualification_agent import QualificationAgent

        if self._declines_last_name(message) and self.memory.data.get("customer_name"):
            if self.memory.data.get("phone"):
                self._state = self._STATE_READY_FOR_BOOKING
                return self._prepare_selected_booking(self._selected_slot)
            self._state = self._STATE_WAITING_FOR_PHONE
            return "No problem. What's the best phone number for the booking?", None
        name = QualificationAgent._extract_bare_name(message)
        if not name:
            return "Sorry, I didn't catch the name. Could you say it again?", None
        self._store_name_parts(name, message)
        if self.memory.data.get("phone"):
            self._state = self._STATE_READY_FOR_BOOKING
            return self._prepare_selected_booking(self._selected_slot)
        self._state = self._STATE_WAITING_FOR_PHONE
        return f"Perfect, {self.memory.data.get('customer_name', name)}. What's the best phone number for the booking?", None

    def _handle_contact_last_name(self, message: str) -> tuple[str, dict | None]:
        from .qualification_agent import QualificationAgent

        if self._declines_last_name(message):
            self.memory.data["last_name"] = ""
            self.memory.save()
            if self.memory.data.get("phone"):
                self._state = self._STATE_READY_FOR_BOOKING
                return self._prepare_selected_booking(self._selected_slot)
            self._state = self._STATE_WAITING_FOR_PHONE
            return "No problem. What's the best phone number for the booking?", None

        last_name = QualificationAgent._extract_bare_name(message)
        if not last_name or len(last_name.split()) != 1:
            return "Sorry, I didn't catch the last name. Could you say it again?", None
        self.memory.data["last_name"] = last_name
        first_name = str(self.memory.data.get("first_name", "")).strip()
        self.memory.data["customer_name"] = f"{first_name} {last_name}".strip()
        self.memory.save()
        logger.info("NAME_PERSISTED first_name=%s has_last_name=true", first_name)
        if self.memory.data.get("phone"):
            self._state = self._STATE_READY_FOR_BOOKING
            return self._prepare_selected_booking(self._selected_slot)
        self._state = self._STATE_WAITING_FOR_PHONE
        return f"Thanks, {first_name}. What's the best phone number for the booking?", None

    def _handle_contact_phone(self, message: str) -> tuple[str, dict | None]:
        phone = self.memory._extract_phone(message)
        if not phone:
            return "Sorry, I didn't catch the phone number. Could you repeat it?", None
        self.memory.set_field("phone", phone, message, expected_field="phone")
        logger.info("PHONE_PERSISTED=true")
        self._state = self._STATE_READY_FOR_BOOKING
        return self._prepare_selected_booking(self._selected_slot)

    def _prepare_selected_booking(self, matched: str) -> tuple[str, dict | None]:
        self._ensure_name_parts()
        gate_missing: list[str] = []
        if not self._last_availability.get("available"):
            gate_missing.append("availability")
        if self._last_availability.get("verified") is False:
            gate_missing.append("verified_availability")
        if matched not in self._available_slots:
            gate_missing.append("selected_slot_available")
        if matched not in self._last_availability.get("slots", self._available_slots):
            gate_missing.append("selected_slot_cached")
        logger.info(
            "BOOKING_GATE_CHECK slot=%s state=%s availability=%s",
            matched,
            self._state,
            bool(self._last_availability.get("available")),
        )
        if gate_missing:
            logger.warning("BOOKING_GATE_MISSING_FIELDS=%s", gate_missing)
            self._state = self._STATE_CHECKING_AVAILABILITY
            return "I need to verify that slot is still available before I can create the booking.", None
        missing = self._booking_gate_missing_fields()
        if missing:
            logger.warning("BOOKING_GATE_MISSING_FIELDS=%s", missing)
            return f"I still need your {self._spoken_field(missing[0])} before I can prepare the booking.", None
        logger.info("BOOKING_GATE_PASSED=true slot=%s", matched)

        self._state = self._STATE_READY_FOR_BOOKING
        booking_result = self.booking_tool.create(self.memory.data, matched)
        if not booking_result.get("booking_id") or not booking_result.get("booking_reference"):
            return self._recover_from_booking_failure(booking_result)
        self._booking_result = booking_result
        booking_id = str(booking_result.get("booking_id", ""))
        booking_reference = str(booking_result.get("booking_reference") or "")
        if not booking_result.get("confirmed") or not booking_id or not booking_reference:
            self._state = self._STATE_READY_FOR_BOOKING
            return (
                "I couldn't confirm the booking with Kreeda, so I haven't marked it as booked. "
                "Your selected slot and booking details are still saved so we can retry."
            ), booking_result

        self.memory.data["booking_id"] = booking_id
        self.memory.data["booking_ref"] = booking_reference
        self.memory.data["booking_order_id"] = str(booking_result.get("order_id") or "")
        booking_status = str(booking_result.get("status") or "CONFIRMED").upper()
        self.memory.data["booking_status"] = booking_status
        whatsapp_payload = build_wati_booking_payload(self.memory.data, booking_result)
        try:
            wati_result = WatiClient().send_booking_payment_link(whatsapp_payload)
        except Exception as exc:
            logger.exception(
                "WATI_SEND_FAILED_NON_BLOCKING booking_id=%s reason=%s:%s",
                booking_id,
                type(exc).__name__,
                exc,
            )
            wati_result = WatiSendResult(
                attempted=True,
                sent=False,
                reason=f"client_exception:{type(exc).__name__}: {exc}",
            )
        whatsapp_delivery = wati_result.to_dict()
        whatsapp_payload["send"] = bool(wati_result.sent)
        whatsapp_payload["wati_send"] = whatsapp_delivery
        booking_result["whatsapp"] = whatsapp_delivery
        self.memory.data["payment_link"] = whatsapp_payload.get("payment_link", "")
        self.memory.data["whatsapp_payload"] = whatsapp_payload
        self.memory.data["whatsapp_delivery"] = whatsapp_delivery

        from src.integrations.whatsapp import WhatsAppClient
        whatsapp_result = WhatsAppClient().send_booking_confirmation(
            str(self.memory.data.get("phone", "")),
            {**booking_result, "slot": matched},
        )
        booking_result["whatsapp_confirmation"] = whatsapp_result.to_dict()
        self.memory.data["whatsapp_confirmation"] = whatsapp_result.to_dict()

        self.memory.save()
        logger.info("BOOKING_ID=%s", booking_id)
        logger.info("BOOKING_REF=%s", self.memory.data["booking_ref"])
        self._state = self._STATE_BOOKING_CONFIRMED

        location = booking_result["location"]
        date = booking_result["date"]
        participants = booking_result["participants"]
        name = str(self.memory.data.get("customer_name", ""))

        if booking_status in {"PAYMENT_PENDING", "PENDING"}:
            logger.info("PAYMENT_PENDING=true bookingId=%s", booking_id)
            room = str(self.memory.data.get("room") or booking_result.get("room") or "your room")
            return (
                f"I've reserved your slot — {room} at {location}, {date.lower()} at {matched}. "
                "Sending the payment link to your WhatsApp — valid for 15 minutes. "
                "Booking confirmed once paid."
            ), booking_result

        if booking_result.get("confirmed"):
            return (
                f"Perfect{', ' + name if name else ''}. "
                f"Your booking is confirmed. "
                f"{participants} {'guest' if str(participants) == '1' else 'guests'} "
                f"at {location} on {date} at {matched}. "
                f"Your reference number is {booking_reference}. "
                "I'll prepare the payment link and WhatsApp confirmation now. "
                "The payment link stays active for 15 minutes. "
                "Once payment is completed, you'll receive the confirmation on WhatsApp. "
                "Have a great time. "
                "I can also help with food options, parking, or arrival guidance."
            ), booking_result
        return (
            f"Perfect{', ' + name if name else ''}. I've prepared the booking for "
            f"{participants} {'guest' if str(participants) == '1' else 'guests'} at {location} "
            f"on {date} at {matched}. Your checkout reference is {booking_id}. "
            f"The booking will be confirmed after checkout is completed. {self._concierge_follow_up()}"
        ), booking_result

    def _is_package_inquiry(self) -> bool:
        if self.memory.data.get("intent") in {
            "birthday_party", "corporate_event", "bachelor_party", "farewell_party",
            "couple_event", "virtual_event",
        }:
            return True
        if self.memory.data.get("room"):
            return False
        package_text = " ".join(
            str(self.memory.data.get(field, ""))
            for field in ("recommended_option", "event_type")
        ).lower()
        return any(term in package_text for term in ("scavenger", "karaoke", "showstopper", "package"))

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

    def _booking_gate_missing_fields(self) -> list[str]:
        required = [
            "participants", "age_group", "location", "preferred_date",
            "selected_slot", "customer_name", "phone",
        ]
        if self.memory.data.get("intent") == "escape_room_inquiry":
            required.append("room")
        return [
            field
            for field in required
            if not (
                self.memory.data.get(field)
                or (field == "participants" and self.memory.data.get("company_size"))
            )
        ]

    def _store_name_parts(self, name: str, message: str = "") -> None:
        parts = name.split(maxsplit=1)
        if not parts:
            return
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""
        self.memory.data["first_name"] = first_name
        self.memory.data["last_name"] = last_name
        combined = " ".join(value for value in (first_name, last_name) if value)
        self.memory.set_field("customer_name", combined, message, expected_field="customer_name")
        self.memory.save()
        logger.info(
            "NAME_PERSISTED first_name=%s has_last_name=%s",
            first_name,
            bool(self.memory.data.get("last_name")),
        )

    def _ensure_name_parts(self) -> None:
        if self.memory.data.get("first_name") and self.memory.data.get("customer_name"):
            return
        name = str(self.memory.data.get("customer_name", "")).strip()
        if name:
            self._store_name_parts(name)

    @staticmethod
    def _declines_last_name(message: str) -> bool:
        lowered = message.lower()
        return bool(
            re.search(
                r"\b(?:don'?t|do not|won'?t|will not|not comfortable|prefer not)\b.*\b(?:last name|surname|full name)\b",
                lowered,
            )
        )

    def _recover_from_booking_failure(self, booking_result: dict) -> tuple[str, dict | None]:
        error = str(booking_result.get("error", ""))
        missing_field = str(booking_result.get("missing_field", "")).lower()
        lowered_error = error.lower()
        if not missing_field:
            if "customer.lastname" in lowered_error or "last name" in lowered_error:
                missing_field = "last_name"
            elif "customer.firstname" in lowered_error or "first name" in lowered_error:
                missing_field = "first_name"
            elif "customer.phone" in lowered_error or "phone" in lowered_error:
                missing_field = "phone"

        if missing_field in {"customer.lastname", "lastname", "last_name"}:
            # The Kreeda API requires a non-empty lastName. Instead of asking the customer
            # for a last name they may not have, auto-fill "NA" and retry the booking.
            # This prevents the zombie last-name collection loop.
            logger.info("LAST_NAME_ZOMBIE_GUARD: auto-filling 'NA' and retrying instead of asking customer")
            self.memory.data["last_name"] = "NA"
            self.memory.save()
            # Retry booking immediately with NA last name
            return self._prepare_selected_booking(self._selected_slot)
        if missing_field in {"customer.firstname", "firstname", "first_name"}:
            self.memory.data["first_name"] = ""
            self.memory.save()
            self._state = self._STATE_WAITING_FOR_FIRST_NAME
            return "Kreeda still needs your first name. What is your first name?", booking_result
        if missing_field in {"customer.phone", "phone"}:
            self.memory.data["phone"] = ""
            self.memory.save()
            self._state = self._STATE_WAITING_FOR_PHONE
            return "Kreeda still needs your phone number. What is the best phone number for the booking?", booking_result

        self._state = self._STATE_READY_FOR_BOOKING
        return (
            "Kreeda couldn't confirm the booking yet. Your room, location, date, and selected slot are still saved for retry."
        ), booking_result

    @staticmethod
    def _spoken_field(field: str) -> str:
        return {
            "participants": "group size",
            "age_group": "age group",
            "preferred_date": "date",
            "first_name": "name",
            "last_name": "last name",
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

        if availability.get("available") and availability.get("capacity_supported") is False:
            self._available_slots = []
            return self._start_coordination(availability, participants), None
        if availability["available"]:
            self._available_slots = self._visible_slots(availability)
            self._state = self._STATE_WAITING_FOR_SLOT
            if not self._available_slots:
                period = str(self.memory.data.get("preferred_period") or "requested").strip()
                return (
                    f"Great, {location} has availability on {new_date}, but no verified {period} slots for "
                    f"{participants} players. Could you share another time window?"
                ), None
            slots_text = self._format_slots(self._available_slots)
            period = self._period_label_prefix()
            return (
                f"Great. We have availability at {location} on {new_date}. "
                f"The {period}slots are {slots_text}. "
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
            "like food options, parking, or arrival guidance?"
        )

    @staticmethod
    def _capacity_limitation_response(availability: dict[str, Any], participants: int) -> str:
        max_capacity = availability.get("max_available_capacity")
        capacity_text = f"; the largest currently has {max_capacity} places" if max_capacity else ""
        return (
            f"Kreeda returned time slots, but none can fit all {participants} players in one room{capacity_text}. "
            "I haven't treated those times as bookable for your group. Our events team can help split the group across rooms."
        )

    def _cancellation_policy_explanation(self, *, include_reschedule: bool = False) -> str:
        policy = get_venue_policy(str(self.memory.data.get("location") or ""))
        cancellation = str(policy.get("cancellationPolicy") or "").strip()
        reschedule = str(policy.get("reschedulePolicy") or "").strip()
        if include_reschedule and reschedule:
            return f"{cancellation} Rescheduling: {reschedule}".strip()
        return cancellation or reschedule

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
        if not self.memory.data.get("location"):
            return f"{answer} Which location would you prefer?"
        if not self.memory.data.get("preferred_date"):
            return f"{answer} What date would you like to visit?"
        if self._state == self._STATE_WAITING_FOR_SLOT:
            slots_text = self._format_slots(self._available_slots)
            return f"{answer} Your available slots are {slots_text}. Which time works best?"
        if self._state == self._STATE_WAITING_FOR_ALT_DATE:
            return f"{answer} What alternative date would work for you?"
        if self._state == self._STATE_WAITING_FOR_AGE:
            return f"{answer} Are the players adults, kids, or a mix?"
        if self._state == self._STATE_WAITING_FOR_FIRST_NAME:
            return f"{answer} May I have your name?"
        if self._state == self._STATE_WAITING_FOR_PHONE:
            return f"{answer} What's the best phone number for the booking?"
        return answer

    @staticmethod
    def _is_slot_summary_question(message: str) -> bool:
        return bool(
            re.search(
                r"\b(?:show|list|what|which|only|any)\b.*\bslots?\b|"
                r"\b(?:earliest|first available|evening slots?|morning slots?|afternoon slots?)\b",
                message,
            )
        )

    def _slot_summary_response(self, message: str) -> str:
        slots = self._sorted_slots(self._available_slots)
        lowered = message.lower()
        if not slots:
            return "I don't have any verified slots to show yet."
        if "earliest" in lowered or "first available" in lowered:
            return f"The earliest available slot is {slots[0]}. Would you like that time?"
        if "evening" in lowered:
            slots = [slot for slot in slots if self._slot_hour(slot) >= 17]
            label = "evening"
        elif "morning" in lowered:
            slots = [slot for slot in slots if self._slot_hour(slot) < 12]
            label = "morning"
        elif "afternoon" in lowered:
            slots = [slot for slot in slots if 12 <= self._slot_hour(slot) < 17]
            label = "afternoon"
        else:
            label = "available"
        if not slots:
            return f"There are no verified {label} slots in the current availability."
        return f"The {label} slots are {self._format_slots(slots)}. Which time works best?"

    def _visible_slots(self, availability: dict[str, Any]) -> list[str]:
        slots = list(availability.get("bookable_slots") or availability.get("slots") or [])
        period = str(self.memory.data.get("preferred_period") or "").lower()
        if period == "evening":
            slots = self.evening_slots(slots)
        elif period == "morning":
            slots = [slot for slot in slots if self._slot_hour(slot) < 12]
        elif period == "afternoon":
            slots = [slot for slot in slots if 12 <= self._slot_hour(slot) < 17]
        slots = self._sorted_slots(slots)
        return slots[:4] if period else slots

    @classmethod
    def evening_slots(cls, slots: list[str]) -> list[str]:
        """Return verified slots in the inclusive 17:00-22:00 window."""
        return [
            slot
            for slot in slots
            if 17 * 60 <= cls._slot_total_minutes(slot) <= 22 * 60
        ]

    @classmethod
    def select_evening_slot(cls, slots: list[str]) -> str:
        """Prefer 19:00 when available, otherwise use the first evening slot."""
        evening = cls.evening_slots(slots)
        preferred = next(
            (slot for slot in evening if cls._slot_total_minutes(slot) == 19 * 60),
            "",
        )
        return preferred or (evening[0] if evening else "")

    def _period_label_prefix(self) -> str:
        period = str(self.memory.data.get("preferred_period") or "").strip()
        return f"{period} " if period else ""

    @classmethod
    def _sorted_slots(cls, slots: list[str]) -> list[str]:
        return sorted(slots, key=lambda slot: (cls._slot_hour(slot), cls._slot_minute(slot)))

    @staticmethod
    def _slot_hour(slot: str) -> int:
        try:
            return datetime.strptime(slot.strip().upper(), "%I:%M %p").hour
        except ValueError:
            return 99

    @staticmethod
    def _slot_minute(slot: str) -> int:
        try:
            return datetime.strptime(slot.strip().upper(), "%I:%M %p").minute
        except ValueError:
            return 99

    @staticmethod
    def _slot_total_minutes(slot: str) -> int:
        try:
            parsed = datetime.strptime(slot.strip().upper(), "%I:%M %p")
            return parsed.hour * 60 + parsed.minute
        except ValueError:
            return 24 * 60

    def _start_coordination(self, availability: dict[str, Any], participants: int) -> str:
        self._state = self._STATE_COORDINATION
        self.memory.data["current_workflow"] = "large_group_coordination"
        self.memory.data["booking_started"] = True
        self.memory.save()
        base = self._capacity_limitation_response(availability, participants)
        if not self.memory.data.get("customer_name"):
            return f"{base} What name should the events team use?"
        if not self.memory.data.get("phone"):
            return f"{base} What phone number should the events team use?"
        self.memory.data["coordination_ready"] = True
        self.memory.save()
        return f"{base} I have the contact details needed for event coordination."

    def _start_event_coordination(self) -> str:
        self._state = self._STATE_COORDINATION
        self.memory.data["current_workflow"] = "event_coordination"
        self.memory.data["booking_started"] = True
        self.memory.save()
        event_type = str(self.memory.data.get("event_type") or "event")
        base = (
            f"This is a {event_type.lower()} package inquiry and coordination request, not a single-room booking. "
            "The events team must confirm the format and availability before anything is booked."
        )
        if not self.memory.data.get("customer_name"):
            return f"{base} What name should the events team use?"
        if not self.memory.data.get("phone"):
            return f"{base} What phone number should the events team use?"
        self.memory.data["coordination_ready"] = True
        self.memory.save()
        return f"{base} I have the contact details needed for coordination."

    def _handle_coordination(self, message: str) -> tuple[str, dict | None]:
        phone = self.memory._extract_phone(message)
        if phone:
            self.memory.set_field("phone", phone, message, expected_field="phone")
        elif not self.memory.data.get("customer_name"):
            from .qualification_agent import QualificationAgent
            name = QualificationAgent._extract_bare_name(message)
            if name:
                self.memory.set_field("customer_name", name, message, expected_field="customer_name")
        if not self.memory.data.get("customer_name"):
            return "What name should the events team use?", None
        if not self.memory.data.get("phone"):
            return "What phone number should the events team use?", None
        self.memory.data["coordination_ready"] = True
        self.memory.save()
        return "I have the details needed for our events team to coordinate the group. No room has been confirmed yet.", None

    def _handle_booking_reference(self, message: str) -> tuple[str, dict | None]:
        candidate = message.strip().strip("., ")
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,80}", candidate):
            return "Please share the booking ID or reference number so I can verify the cancellation.", None
        self.memory.data["booking_ref"] = candidate
        self.memory.save()
        return self._cancel_booking(candidate), None

    def _cancel_booking(self, booking_ref: str) -> str:
        result = self.orchestrator.cancel_booking(booking_ref, reason="customer request")
        status = str(result.get("status", "")).lower() if isinstance(result, dict) else ""
        if status not in {"cancelled", "canceled"}:
            self._state = self._STATE_WAITING_FOR_BOOKING_REF
            return "I couldn't verify that cancellation, so I haven't marked the booking as cancelled."
        self._state = self._STATE_CLOSED
        return f"I've successfully cancelled your booking {booking_ref}."

    def _clear_selected_slot(self) -> None:
        self._selected_slot = ""
        self.memory.data["selected_slot"] = ""
        for field in (
            "_kreeda_cart_id", "_kreeda_cart_signature", "_booking_idempotency_key",
        ):
            self.memory.data[field] = ""
        self.memory.save()

    @staticmethod
    def _is_booking_faq(message: str) -> bool:
        lowered = message.lower()
        return any(
            term in lowered
            for term in (
                "parking", "park", "how long", "duration", "rules", "how does it work",
                "how does the escape room work", "what happens inside", "food", "menu",
                "arrival", "directions", "direction", "dress code", "what should i wear",
                "age restriction", "age restrictions", "minimum age", "kids allowed",
                "children allowed", "cancellation", "cancel policy", "refund policy",
            )
        )

    def _booking_faq_answer(self, message: str) -> str:
        lowered = message.lower()
        if "parking" in lowered or re.search(r"\bpark\b", lowered):
            return self._parking_faq_answer(message)
        if "cancellation" in lowered or "cancel policy" in lowered or "refund policy" in lowered:
            return self._cancellation_policy_explanation(
                include_reschedule="reschedul" in lowered or "postpone" in lowered
            )
        if "food" in lowered or "menu" in lowered:
            return self._food_faq_answer(message)
        if "arrival" in lowered or "late" in lowered:
            return "Please arrive a little before your scheduled slot so the team can brief you before the game."
        if "direction" in lowered:
            return "I can keep the booking moving here, and the branch team can share exact directions with the confirmation."
        if "dress code" in lowered or "wear" in lowered:
            return "There is no special dress code; comfortable clothing and closed footwear are a good idea."
        if "age restriction" in lowered or "minimum age" in lowered or "kids allowed" in lowered or "children allowed" in lowered:
            return "Age suitability can vary by room, so I’ll keep the age group on the booking and the team can confirm the best fit."
        from ..knowledge.demo_knowledge import get_demo_answer
        return get_demo_answer(message) or ""

    def _room_change_acknowledgement(self, room: str) -> str:
        date = str(self.memory.data.get("preferred_date", "")).strip()
        selected_time = str(
            self.memory.data.get("preferred_time") or self._selected_slot or ""
        ).strip()
        if date and selected_time:
            return f"Got it. I've switched the room to {room}. Would you like to keep the same date and time?"
        if date:
            return f"Got it. I've switched the room to {room}. Would you like to keep {date}?"
        return f"Got it. I've switched the room to {room}. What date would you like to visit?"

    def _date_change_acknowledgement(self, date: str) -> str:
        room = str(self.memory.data.get("room", "")).strip()
        if room:
            return f"No problem. I'll check {date} instead. Would you like the same room?"
        return f"No problem. I'll check {date} instead. Which room would you like?"

    def _parking_faq_answer(self, message: str) -> str:
        lowered = message.lower()
        location = str(self.memory.data.get("location", "")).lower()
        if "whitefield" in lowered or location == "whitefield":
            return "Whitefield has basement parking available."
        if "koramangala" in lowered or location == "koramangala":
            return "Koramangala has basement and street parking available."
        if "jp nagar" in lowered or location == "jp nagar":
            return "JP Nagar has street parking available."
        return "Parking depends on the branch: Koramangala has basement and street parking, Whitefield has basement parking, and JP Nagar has street parking."

    def _food_faq_answer(self, message: str) -> str:
        lowered = message.lower()
        intent = str(self.memory.data.get("intent", ""))
        if "birthday" in lowered or intent == "birthday_party":
            return "Food options for birthday parties include choices that can be coordinated with the package; the team can confirm the available menu for your guest count."
        if "corporate" in lowered or intent == "corporate_event":
            return "Food options include lighter snacks, hi-tea, or meal options for corporate events depending on the package and group size."
        return "Food options include continental food, build-your-menu options, mixed snack boxes, hi-tea options, and meal options for events."

    def _missing_field_response(self, field: str) -> str:
        acknowledgements: list[str] = []
        date = str(self.memory.data.get("preferred_date", "")).strip()
        preferred_time = str(self.memory.data.get("preferred_time", "")).strip()
        room = str(self.memory.data.get("room", "")).strip()
        if date:
            acknowledgements.append(f"Got it, {date}.")
        if preferred_time:
            acknowledgements.append(f"Got it, {preferred_time}.")
        if room:
            acknowledgements.append(f"I've noted {room}.")
        if field == "location":
            question = "Which location would you prefer?"
        elif field == "preferred_date":
            location = str(self.memory.data.get("location", "")).strip()
            question = f"Perfect, I've noted {location}. What date would you like to visit?" if location else "What date would you like to visit?"
        else:
            question = "What would you like to choose next?"
        prefix = " ".join(dict.fromkeys(acknowledgements))
        return f"{prefix} {question}".strip()

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
        _MINUTE_WORDS = {
            "oh five": "05", "zero five": "05", "five": "05", "ten": "10",
            "fifteen": "15", "twenty": "20", "twenty five": "25",
            "thirty": "30", "forty": "40", "forty five": "45", "fifty": "50",
        }
        for phrase, minute in sorted(_MINUTE_WORDS.items(), key=lambda item: len(item[0]), reverse=True):
            lowered = re.sub(rf"\b(\d{{1,2}})\s+{phrase}\s*(am|pm)\b", rf"\1:{minute} \2", lowered)

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

        # Bare evening-style times in booking context, such as a selected slot or "around 7".
        if re.search(
            r"\b\d{1,2}\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
            r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
            r"nov(?:ember)?|dec(?:ember)?)\b",
            lowered,
        ) and not re.search(r"\b(?:around|about|at|then|works|slot|pm|tonight)\b", lowered):
            return ""
        bare = re.search(r"\b(?:around|about|at|for|by)?\s*(\d{1,2})(?::(\d{2}))?\b", lowered)
        if bare and re.search(r"\b(?:around|about|at|then|works|slot|pm|evening|tonight|closest|nearest)\b", lowered):
            hour = int(bare.group(1))
            if 1 <= hour <= 11:
                minute = bare.group(2) or "00"
                period = "PM" if 5 <= hour <= 11 else "AM"
                return f"{hour}:{minute} {period}"

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
            slot_n = normalise(slot)
            if slot_n == chosen_n or (not re.search(r"\b(?:AM|PM)\b", chosen_n) and slot_n.startswith(f"{chosen_n} ")):
                return slot
        return ""

    @classmethod
    def _nearest_slots(cls, chosen: str, available: list[str], limit: int = 3) -> list[str]:
        def minutes(value: str) -> int | None:
            try:
                parsed = datetime.strptime(value.strip().upper(), "%I:%M %p")
                return parsed.hour * 60 + parsed.minute
            except ValueError:
                return None

        chosen_minutes = minutes(chosen)
        if chosen_minutes is None:
            return []
        ranked = sorted(
            (
                (abs(slot_minutes - chosen_minutes), slot_minutes, slot)
                for slot in available
                for slot_minutes in [minutes(slot)]
                if slot_minutes is not None
            ),
            key=lambda item: (item[0], item[1]),
        )
        return [slot for _delta, _mins, slot in ranked[:limit]]

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
