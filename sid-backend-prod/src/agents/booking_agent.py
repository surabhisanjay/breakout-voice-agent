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
from datetime import datetime, timezone
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
from ..services.recommendation_engine import RecommendationEngine
from ..services.price_breakdown import price_breakdown_from_totals


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
        "prison break", "missile attack", "undercover", "curse of the pharaoh",
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
    _STATE_WAITING_FOR_TIME_PREFERENCE = "waiting_for_time_preference"
    _STATE_WAITING_FOR_SLOT_CONFIRMATION = "waiting_for_slot_confirmation"
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
        if self._escalation_locked():
            start_turn = time.time()
            self.memory.add_turn("customer", message)
            response = "I've already handed this over to our team. They'll be able to help with that and the rest of the context in one go."
            self.memory.add_turn("agent", response)
            return AgentResponse(
                response=response,
                intent=str(self.memory.data.get("intent") or "general_faq"),
                next_agent="escalation_agent",
                should_handoff=True,
                state=self.memory.as_state(),
                escalation=dict(self.memory.data.get("escalation_state") or {}),
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

        if self._is_payment_link_request(lowered):
            response, booking_result = self._handle_payment_link_request()
            return self._finalize_response(response, message, start_turn, booking_result)

        if self._is_payment_status_request(lowered):
            response, booking_result = self._handle_payment_status_request()
            return self._finalize_response(response, message, start_turn, booking_result)

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
            "missile attack": "Missile Attack",
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

        # Check for location, participants, date, or time modification
        slot_input = self._extract_slot(message)
        ambiguous_time_reference = self._ambiguous_bare_time_reference(message)
        target_location = self.memory._extract_location(lowered)
        participant_range = self.memory._extract_participant_range(normalized_lowered)
        target_participants = (
            participant_range[1]
            if participant_range
            else self.memory._extract_participants(normalized_lowered)
        )
        if (
            slot_input
            and target_participants
            and not participant_range
            and not re.search(
                r"\b(?:people|persons|guests|kids|children|adults|participants|players|members|friends|employees|colleagues|of us|group)\b",
                normalized_lowered,
            )
        ):
            target_participants = ""
        target_date = self.memory._extract_preferred_date(message)
        target_phone = self.memory._extract_phone(message)
        old_room = self.memory.data.get("room")
        old_location = self.memory.data.get("location")
        old_date = self.memory.data.get("preferred_date")
        old_participants = self.memory.data.get("participants")
        from .qualification_agent import QualificationAgent
        has_explicit_name_signal = bool(
            re.search(r"\bname\s*:", lowered)
            or re.search(
                r"\b(?:name\s+|my first name is|my name is|i am|i'm|this is|book under|actually\s+(?:use|make it|change(?:\s+it)?\s+to))\b",
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
                and not slot_input
            )
            or (
                self._state in (self._STATE_WAITING_FOR_FIRST_NAME, self._STATE_WAITING_FOR_LAST_NAME)
                and not slot_input
                and not target_phone
            )
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
        is_reschedule_policy_question = bool(
            re.search(
                r"\b(?:reschedul(?:e|ing)|postpon(?:e|ing))\b",
                lowered,
            )
        ) and not bool(
            re.search(r"\b(?:reschedul(?:e|ing)|postpon(?:e|ing))\b.{0,30}\b(?:my|the|this)\s+(?:booking|reservation)\b", lowered)
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

        is_closest_slot_reference = bool(re.search(r"\b(?:closest|nearest)\b", lowered))
        if ambiguous_time_reference:
            self.memory.data["preferred_time"] = ""
            self.memory.data["requested_time"] = ""
            if self.memory.data.get("time_preference") == "specific":
                self.memory.data["time_preference"] = ""
            self.memory.save()
        if (
            slot_input
            and not self._slot_outside_operating_window(slot_input)
            and not is_closest_slot_reference
            and slot_input != self.memory.data.get("preferred_time")
        ):
            self.memory.set_field("preferred_time", slot_input, message, expected_field="preferred_time")
            self.memory.data["requested_time"] = slot_input
            self.memory.data["time_preference"] = "specific"
            self.memory.save()
        self._capture_time_preference(message)

        # Check if any field is different from the memory snapshot at the
        # start of the turn. Some fields are persisted above by design.
        room_changed = bool(target_room and target_room != old_room)
        location_changed = bool(target_location and target_location != old_location)
        date_changed = bool(target_date and target_date != old_date)

        any_field_changed = room_changed or location_changed or participants_changed or date_changed
        clean_room_correction = bool(
            target_room
            and re.match(r"^\s*(?:no|nah|nope)(?:[\s,.-]+(?:no|nah|nope))*[\s,.-]+", lowered)
        )
        
        is_change_request = any_field_changed and (
            any(w in lowered for w in ("actually", "correct", "change", "instead", "switch", "want", "prefer", "rather", "choose", "update", "modify", "reschedule", "now", "different")) or
            clean_room_correction or
            participants_changed
            or self._state in (self._STATE_WAITING_FOR_SLOT, self._STATE_WAITING_FOR_ALT_DATE)
        )

        is_slot_availability_question = bool(
            slot_input
            and re.search(r"\b(?:available|availability|free|open|search|check|slots?|slot)\b", lowered)
            and not re.search(r"\b(?:actually|change|switch|instead|rather|make it|update|modify|reschedule|use)\b", lowered)
        )
        is_price_question = bool(
            re.search(r"\b(?:price|pricing|cost|costs|rate|rates|how much)\b", lowered)
        )
        is_discount_question = bool(
            re.search(r"\b(?:discounts?|offers?|coupon|promo|deal|membership)\b", lowered)
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
        explicit_change_words = explicit_change_words or clean_room_correction
        explicit_slot_change = bool(
            slot_input
            and explicit_change_words
            and slot_input != self.memory.data.get("selected_slot")
        )
        explicit_room_change = bool(
            target_room
            and explicit_change_words
            and not (location_changed or participants_changed or date_changed)
            and self._state in (self._STATE_WAITING_FOR_SLOT, self._STATE_WAITING_FOR_ALT_DATE, self._STATE_CHECKING_AVAILABILITY)
        )
        explicit_date_change = bool(
            target_date
            and explicit_change_words
            and not (room_changed or location_changed or participants_changed)
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
            if not self._available_slots:
                response, _ = self._handle_availability_check()
            else:
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
            response = self._direct_slot_availability_response(slot_input)
            booking_result = None

        elif is_discount_question:
            response = self._append_booking_resume(self._discount_answer())
            booking_result = None

        elif explicit_slot_change:
            matched = self._match_slot(slot_input, self._available_slots) or slot_input
            self._selected_slot = matched
            self.memory.data["selected_slot"] = matched
            self.memory.data["slot_confirmed"] = True
            self.memory.save()
            if not self.memory.data.get("customer_name"):
                self._state = self._STATE_WAITING_FOR_FIRST_NAME
                response = f"Sure, I've updated your slot to {matched}. May I have your name?"
                booking_result = None
            elif not self.memory.data.get("phone"):
                self._state = self._STATE_WAITING_FOR_PHONE
                response = f"Sure, I've updated your slot to {matched}. Could you share the phone number for the booking?"
                booking_result = None
            else:
                response, booking_result = self._prepare_selected_booking(matched)

        elif is_price_question:
            response = self._append_booking_resume(self._price_answer())
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

        elif is_reschedule_policy_question:
            response = self._append_booking_resume(self._reschedule_policy_explanation())
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
            elif not self._has_time_preference():
                self._state = self._STATE_WAITING_FOR_TIME_PREFERENCE
                response = f"{ack}{self._time_preference_question()}"
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
                    response = f"{ack}{self._availability_slots_response(date, location, participants)}"
                else:
                    response = f"{ack}I found availability on {date} at {location}, but no verified {self.memory.data.get('preferred_period')} slots for {participants} players. Could you share another time window?"
            elif availability is not None:
                self._state = self._STATE_WAITING_FOR_ALT_DATE
                response = f"{ack}I checked availability on {date} at {location} for {participants} players, but unfortunately we don't have slots. Could you share an alternative date?"

            booking_result = None
        else:
            age_input = self.memory._extract_age_group(lowered, allow_bare_range=True)[0]
            is_slot_confirmation_response = (
                self._state == self._STATE_WAITING_FOR_SLOT_CONFIRMATION
                and (
                    self._is_generic_slot_confirmation(lowered)
                    or self._is_negative_slot_confirmation(lowered)
                    or bool(slot_input)
                )
            )
            actionable_state_input = (
                (self._state == self._STATE_WAITING_FOR_SLOT and bool(slot_input))
                or is_slot_confirmation_response
                or (self._state == self._STATE_WAITING_FOR_AGE and bool(age_input))
                or (self._state == self._STATE_WAITING_FOR_FIRST_NAME and bool(target_name))
                or (self._state == self._STATE_WAITING_FOR_LAST_NAME and bool(target_name))
                or (self._state == self._STATE_WAITING_FOR_PHONE and bool(target_phone))
                or (self._state == self._STATE_WAITING_FOR_ALT_DATE and bool(target_date))
                or (self._state == self._STATE_WAITING_FOR_TIME_PREFERENCE and self._has_time_preference())
            )
            if actionable_state_input:
                if self._state in (self._STATE_WAITING_FOR_SLOT, self._STATE_WAITING_FOR_SLOT_CONFIRMATION):
                    response, booking_result = self._handle_slot_selection(message)
                elif self._state == self._STATE_WAITING_FOR_AGE:
                    response, booking_result = self._handle_age_group(message)
                elif self._state == self._STATE_WAITING_FOR_FIRST_NAME:
                    response, booking_result = self._handle_contact_first_name(message)
                elif self._state == self._STATE_WAITING_FOR_LAST_NAME:
                    response, booking_result = self._handle_contact_last_name(message)
                elif self._state == self._STATE_WAITING_FOR_PHONE:
                    response, booking_result = self._handle_contact_phone(message)
                elif self._state == self._STATE_WAITING_FOR_TIME_PREFERENCE:
                    response, booking_result = self._handle_availability_check()
                else:
                    response, booking_result = self._handle_alt_date(message)
                return self._finalize_response(response, message, start_turn, booking_result)

            if self._is_waiting_for_booking_detail() and (target_name or target_phone):
                response, booking_result = self._continue_booking_detail_collection()
                return self._finalize_response(response, message, start_turn, booking_result)

            if ambiguous_time_reference:
                response = self._append_booking_resume(
                    f"Just to confirm, do you mean {ambiguous_time_reference} PM? I won't use an AM time unless you say AM clearly."
                )
                booking_result = None
                return self._finalize_response(response, message, start_turn, booking_result)

            unknown_room_name = self._unknown_room_selection_name(message)
            if unknown_room_name:
                response = self._append_booking_resume(
                    self._unknown_room_selection_response(unknown_room_name)
                )
                booking_result = None
                return self._finalize_response(response, message, start_turn, booking_result)

            # Check for general FAQ / room explanations (interruption) before
            # re-asking the active state prompt. This keeps benign questions
            # from being treated as failed answers to the pending field.
            from ..knowledge.demo_knowledge import get_demo_answer
            booking_started = bool(self.memory.data.get("booking_started")) or self._state in (
                self._STATE_WAITING_FOR_TIME_PREFERENCE,
                self._STATE_WAITING_FOR_SLOT,
                self._STATE_WAITING_FOR_SLOT_CONFIRMATION,
                self._STATE_WAITING_FOR_AGE,
                self._STATE_WAITING_FOR_FIRST_NAME,
                self._STATE_WAITING_FOR_LAST_NAME,
                self._STATE_WAITING_FOR_PHONE,
            )
            if booking_started:
                interruption_answer = self._booking_interruption_answer(message)
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

                elif self._state == self._STATE_WAITING_FOR_TIME_PREFERENCE:
                    if self._has_time_preference():
                        response, booking_result = self._handle_availability_check()
                    else:
                        response = self._time_preference_question()
                        booking_result = None

                elif self._state in (self._STATE_WAITING_FOR_SLOT, self._STATE_WAITING_FOR_SLOT_CONFIRMATION):
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

    def _escalation_locked(self) -> bool:
        state = self.memory.data.get("escalation_state") or {}
        return bool(
            isinstance(state, dict)
            and state.get("escalate")
            and str(state.get("status") or "").lower() not in {"resolved", "closed"}
        )

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
        elif self._state == self._STATE_WAITING_FOR_SLOT_CONFIRMATION:
            next_field = "slot_confirmation"
        elif self._state == self._STATE_WAITING_FOR_TIME_PREFERENCE:
            next_field = "preferred_time"
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
            recommended = str(self.memory.data.get("recommended_option") or "").strip()
            if not recommended:
                try:
                    recommendation = RecommendationEngine.deterministic_fallback("recommend a room", self.memory.data)
                    recommended = str(getattr(recommendation, "option", "") or "").strip()
                except Exception:
                    recommended = ""
            if not recommended:
                recommended = "Murder Mystery"
            self.memory.set_field("recommended_option", recommended, expected_field="recommended_option")
            self.memory.data["last_discussed_topic"] = "rooms"
            discussed = list(self.memory.data.get("discussed_options") or [])
            if recommended not in discussed:
                discussed.append(recommended)
                self.memory.data["discussed_options"] = discussed[-5:]
            self.memory.save()
            return (
                f"Based on your details, I'd recommend {recommended}. "
                f"Would you like to book {recommended}, or hear another recommendation?"
            ), None
        if not date:
            return self._missing_field_response("preferred_date"), None
        if self._is_package_inquiry():
            return self._start_event_coordination(), None
        if not self._has_time_preference():
            self._state = self._STATE_WAITING_FOR_TIME_PREFERENCE
            return self._time_preference_question(), None

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
            persisted_slot = str(self.memory.data.get("selected_slot") or "")
            if persisted_slot and self._match_slot(persisted_slot, self._available_slots):
                return self._handle_slot_selection(persisted_slot)
            return self._availability_slots_response(date, location, participants), None
        else:
            self._state = self._STATE_WAITING_FOR_ALT_DATE
            return (
                f"I'm sorry, we don't have availability at {location} on {date}. "
                f"Could you share an alternative date you'd be comfortable with?"
            ), None

    def _direct_slot_availability_response(self, slot_input: str) -> str:
        """Answer cold-start slot questions by hydrating availability before reading slot cache."""
        if self._slot_outside_operating_window(slot_input):
            return self._outside_hours_response(slot_input)

        if not self._available_slots:
            location = str(self.memory.data.get("location") or "").strip()
            room = str(self.memory.data.get("room") or "").strip()
            date = str(self.memory.data.get("preferred_date") or "").strip()
            if location and room and date:
                response, _ = self._handle_availability_check()
                return response
            if not location:
                return (
                    f"I can check {slot_input} directly. Which Breakout location are you planning to visit: "
                    "Whitefield, Koramangala, or JP Nagar?"
                )
            if not room:
                return f"I can check {slot_input} at {location}. Which escape room should I check?"
            return f"I can check {slot_input} for that room. What date should I use?"

        matched = self._match_slot(slot_input, self._available_slots)
        room = str(self.memory.data.get("room") or "the room")
        location = str(self.memory.data.get("location") or "the selected location")
        date = str(self.memory.data.get("preferred_date") or "the selected date")
        if matched:
            return f"Yes, {matched} is available for {room} at {location} on {date}."
        nearest = self._nearest_slots(slot_input, self._available_slots, limit=3)
        self.memory.data["last_suggested_slots"] = nearest
        self.memory.save()
        slots_text = self._format_slots(nearest)
        if nearest:
            return (
                f"I couldn't find exactly {slot_input} for {room} at {location} on {date}. "
                f"The closest available slots are {slots_text}. Which would you prefer?"
            )
        return (
            f"I couldn't find {slot_input} for {room} at {location} on {date}. "
            "Could you share another time window?"
        )

    def _handle_slot_selection(self, message: str) -> tuple[str, dict | None]:
        """
        Customer has chosen a slot — validate, then confirm the booking.
        """
        if not self.memory.data.get("preferred_date"):
            self._state = self._STATE_CHECKING_AVAILABILITY
            return "Before I check available time slots, what date would you like to visit?", None

        lowered_msg = message.lower()
        pending_slot = str(self.memory.data.get("pending_slot_confirmation") or "").strip()
        chosen = ""
        if pending_slot:
            if self._is_affirmative_slot_confirmation(lowered_msg):
                chosen = pending_slot
                self.memory.data["pending_slot_confirmation"] = ""
                self.memory.save()
            elif self._is_negative_slot_confirmation(lowered_msg):
                self.memory.data["pending_slot_confirmation"] = ""
                self.memory.save()
                self._state = self._STATE_WAITING_FOR_SLOT
                slots_text = self._format_slots(self.memory.data.get("last_suggested_slots") or self._available_slots)
                return f"No problem. The available slots are {slots_text}. Which time would you prefer?", None
            else:
                self.memory.data["pending_slot_confirmation"] = ""
                self.memory.save()
        closest_slot_phrases = (
            "closest", "nearest", "closest one", "nearest one",
            "take the closest", "take nearest", "closest option", "nearest option",
        )
        is_closest_reference = any(phrase in lowered_msg for phrase in closest_slot_phrases)
        raw_extracted_slot = self._extract_slot(message)
        if not chosen and is_closest_reference and self._available_slots:
            suggested = [
                slot for slot in self.memory.data.get("last_suggested_slots", [])
                if isinstance(slot, str) and self._match_slot(slot, self._available_slots)
            ]
            if suggested:
                chosen = suggested[0]
            elif raw_extracted_slot and not re.search(r"\b(?:closest|nearest|the)\s+one\b", lowered_msg):
                nearest = self._nearest_slots(raw_extracted_slot, self._available_slots, limit=1)
                chosen = nearest[0] if nearest else ""
        if not chosen:
            chosen = raw_extracted_slot

        if chosen and not is_closest_reference and self._available_slots and re.search(
            r"\b(?:closest|nearest|whichever\s+is\s+(?:close|closest|nearest))\b",
            lowered_msg,
        ):
            matched_or_nearest = self._match_slot(chosen, self._available_slots)
            if not matched_or_nearest:
                nearest = self._nearest_slots(chosen, self._available_slots, limit=1)
                matched_or_nearest = nearest[0] if nearest else ""
            if matched_or_nearest:
                chosen = matched_or_nearest

        # Handle "closest/nearest one" after we have just suggested nearest slots.
        if not chosen and self._available_slots:
            if is_closest_reference:
                preferred = str(
                    self.memory.data.get("preferred_time")
                    or self.memory.data.get("requested_time")
                    or ""
                )
                nearest = self._nearest_slots(preferred, self._available_slots, limit=1) if preferred else []
                chosen = nearest[0] if nearest else ""

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
            elif self._is_generic_slot_confirmation(lowered_msg):
                suggested = [
                    slot for slot in self.memory.data.get("last_suggested_slots", [])
                    if isinstance(slot, str) and self._match_slot(slot, self._available_slots)
                ]
                if suggested:
                    slot_to_confirm = suggested[0]
                    self.memory.data["pending_slot_confirmation"] = slot_to_confirm
                    self.memory.save()
                    self._state = self._STATE_WAITING_FOR_SLOT_CONFIRMATION
                    return f"Just to confirm, would you like the {slot_to_confirm} slot?", None

        if not chosen:
            slots_text = self._format_slots(self._available_slots)
            return (
                f"Sorry, I didn't catch that. The available slots are {slots_text}. "
                f"Which time would you prefer?"
            ), None
        if self._slot_outside_operating_window(chosen):
            return self._outside_hours_response(chosen), None

        # Fuzzy-match: accept "3 PM", "3:00 pm", "three pm"
        matched = self._match_slot(chosen, self._available_slots)
        if not matched:
            nearest = self._nearest_slots(chosen, self._available_slots, limit=3)
            self.memory.data["last_suggested_slots"] = nearest
            self.memory.save()
            slots_text = self._format_slots(nearest or self._available_slots)
            return (
                f"Sorry, {chosen} isn't one of the available slots. "
                f"The nearest available slots are {slots_text}. Which would you prefer?"
            ), None

        self._selected_slot = matched
        self.memory.data["selected_slot"] = matched
        self.memory.data["pending_slot_confirmation"] = ""
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

        repeated_slot = self._extract_slot(message)
        if repeated_slot:
            matched = self._match_slot(repeated_slot, self._available_slots) or self._selected_slot or repeated_slot
            if matched:
                self._selected_slot = matched
                self.memory.data["selected_slot"] = matched
                self.memory.data["slot_confirmed"] = True
                self.memory.save()
            return f"I have {matched or 'that slot'} saved. May I have your name?", None
        phone = self.memory._extract_phone(message)
        if phone:
            self.memory.set_field("phone", phone, message, expected_field="phone")
            if self.memory.data.get("customer_name"):
                self._state = self._STATE_READY_FOR_BOOKING
                return self._prepare_selected_booking(self._selected_slot)
            return "I've saved that phone number. May I have the booking name?", None
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
            return self._next_booking_detail_prompt(prefix="Got it. "), None
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
        self.memory.data["order_id"] = str(booking_result.get("order_id") or "")
        self.memory.data["orderId"] = str(booking_result.get("order_id") or "")
        self.memory.data["venue_id"] = str(booking_result.get("venue_id") or self.memory.data.get("venue_id") or "")
        self.memory.data["venueId"] = self.memory.data["venue_id"]
        booking_status = str(booking_result.get("status") or "RESERVED").upper()
        self.memory.data["booking_status"] = booking_status
        self.memory.data["bookingStatus"] = booking_status
        payment_status = str(booking_result.get("payment_status") or ("PAID" if booking_status == "CONFIRMED" else "UNPAID")).upper()
        self.memory.data["payment_status"] = payment_status
        self.memory.data["paymentStatus"] = payment_status
        payment_url = str(booking_result.get("payment_url") or "")
        payment_deadline = str(booking_result.get("payment_deadline") or "")
        self.memory.data["payment_url"] = payment_url
        self.memory.data["paymentUrl"] = payment_url
        self.memory.data["payment_deadline"] = payment_deadline
        self.memory.data["paymentDeadline"] = payment_deadline
        self.memory.data["email_notification_sent"] = bool(booking_result.get("email_notification_sent", False))
        self.memory.data["emailNotificationSent"] = bool(booking_result.get("email_notification_sent", False))
        self.memory.data["whatsapp_notification_sent"] = bool(booking_result.get("whatsapp_notification_sent", False))
        self.memory.data["whatsappNotificationSent"] = bool(booking_result.get("whatsapp_notification_sent", False))
        self._capture_price_breakdown(booking_result)
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
        self.memory.data["payment_url"] = whatsapp_payload.get("payment_link", "")
        self.memory.data["paymentUrl"] = whatsapp_payload.get("payment_link", "")
        self.memory.data["whatsapp_payload"] = whatsapp_payload
        self.memory.data["whatsapp_delivery"] = whatsapp_delivery
        self.memory.save()
        logger.info("BOOKING_ID=%s", booking_id)
        logger.info("BOOKING_REF=%s", self.memory.data["booking_ref"])
        self._state = self._STATE_BOOKING_CONFIRMED

        location = booking_result["location"]
        date = booking_result["date"]
        participants = booking_result["participants"]
        name = str(self.memory.data.get("customer_name", ""))

        if booking_status in {"RESERVED", "PAYMENT_PENDING", "PENDING"}:
            logger.info("PAYMENT_RESERVED=true bookingId=%s", booking_id)
            self.memory.data["current_workflow"] = "BOOKING_PENDING_PAYMENT"
            self.memory.data["booking_phase"] = "BOOKING_PENDING_PAYMENT"
            self.memory.save()
            room = str(self.memory.data.get("room") or booking_result.get("room") or "your room")
            price = self.memory.data.get("price_breakdown") or {}
            price_note = (
                f" Total price: {price.get('currency', 'INR')} {price['final_price']}."
                if price.get("final_price") is not None
                else ""
            )
            return (
                f"I've reserved your slot — {room} at {location}, {date} at {matched}. "
                f"Booking ID {booking_id}. Reference {booking_reference}. "
                f"{price_note} "
                "Sending the payment link to your WhatsApp — valid for 15 minutes. "
                "Your booking will be confirmed once payment is completed. "
                "I can also help with food options, parking, or arrival guidance."
            ), booking_result

        if booking_result.get("confirmed"):
            self.memory.data["current_workflow"] = "BOOKING_PENDING_PAYMENT"
            self.memory.data["booking_phase"] = "BOOKING_PENDING_PAYMENT"
            self.memory.save()
            return (
                f"Perfect{', ' + name if name else ''}. "
                f"Your booking has been reserved. "
                f"{participants} {'guest' if str(participants) == '1' else 'guests'} "
                f"at {location} on {date} at {matched}. "
                f"Your reference number is {booking_reference}. "
                "I've sent the payment link on WhatsApp. "
                "The payment link stays active for 15 minutes. "
                "Once payment is completed, your booking will be confirmed. "
                "Have a great time. "
                "I can also help with food options, parking, or arrival guidance."
            ), booking_result
        return (
            f"Perfect{', ' + name if name else ''}. I've prepared the booking for "
            f"{participants} {'guest' if str(participants) == '1' else 'guests'} at {location} "
            f"on {date} at {matched}. Your checkout reference is {booking_id}. "
            f"The booking will be confirmed after checkout is completed. {self._concierge_follow_up()}"
        ), booking_result

    def _capture_price_breakdown(self, booking_result: dict[str, Any]) -> None:
        """Persist Kreeda's authoritative total without blocking a successful booking."""
        breakdown = price_breakdown_from_totals(booking_result.get("totals"))
        if not breakdown:
            booking_id = str(booking_result.get("booking_id") or "").strip()
            venue_id = str(
                booking_result.get("venue_id")
                or self.memory.data.get("venueId")
                or self.memory.data.get("venue_id")
                or ""
            ).strip()
            if booking_id and venue_id:
                try:
                    payment_status = self.orchestrator.check_payment_status(venue_id, booking_id)
                    if isinstance(payment_status, dict):
                        booking_result["payment_status_result"] = payment_status
                        breakdown = price_breakdown_from_totals(payment_status.get("totals"))
                except Exception as exc:
                    logger.warning(
                        "KREEDA_PRICE_LOOKUP_FAILED_NON_BLOCKING booking_id=%s reason=%s:%s",
                        booking_id,
                        type(exc).__name__,
                        exc,
                    )
        if breakdown:
            booking_result["price_breakdown"] = breakdown
            self.memory.data["price_breakdown"] = breakdown

    @staticmethod
    def _is_payment_link_request(lowered: str) -> bool:
        return bool(
            re.search(
                r"\b(?:didn'?t|not|never|haven'?t|have not)\s+(?:receive|get|got)\b.{0,40}\b(?:payment\s+)?link\b",
                lowered,
            )
            or re.search(r"\b(?:resend|send|share)\b.{0,30}\b(?:payment\s+)?link\b", lowered)
        )

    def _handle_payment_link_request(self) -> tuple[str, dict | None]:
        booking_id = str(self.memory.data.get("booking_id") or "").strip()
        payment_link = str(
            self.memory.data.get("paymentUrl")
            or self.memory.data.get("payment_url")
            or self.memory.data.get("payment_link")
            or ""
        ).strip()
        whatsapp_payload = self.memory.data.get("whatsapp_payload")
        if booking_id and payment_link:
            delivery: dict[str, Any] | None = None
            if isinstance(whatsapp_payload, dict):
                try:
                    delivery = WatiClient().send_booking_payment_link(whatsapp_payload).to_dict()
                    whatsapp_payload["wati_resend"] = delivery
                    self.memory.data["whatsapp_payload"] = whatsapp_payload
                    self.memory.data["whatsapp_delivery"] = delivery
                    self.memory.save()
                except Exception as exc:
                    logger.exception(
                        "WATI_RESEND_FAILED_NON_BLOCKING booking_id=%s reason=%s:%s",
                        booking_id,
                        type(exc).__name__,
                        exc,
                    )
                    delivery = {
                        "attempted": True,
                        "sent": False,
                        "provider": "WATI",
                        "reason": f"client_exception:{type(exc).__name__}: {exc}",
                    }
            status_note = "I've resent it on WhatsApp. " if delivery and delivery.get("sent") else "I still have the payment link saved. "
            return (
                f"{status_note}Your booking ID is {booking_id}. "
                f"Payment link: {payment_link}. The link is valid for 15 minutes from generation."
            ), self._booking_result

        if booking_id and not payment_link:
            return (
                f"I found booking ID {booking_id}, but I don't have a payment link saved in this conversation. "
                "I'll keep the booking details intact so the team can regenerate or resend it."
            ), self._booking_result

        return (
            "I don't see a completed booking yet, so no payment link was generated. "
            f"{self._next_booking_detail_prompt()}"
        ), None

    @staticmethod
    def _is_payment_status_request(lowered: str) -> bool:
        return bool(
            re.search(r"\bi\s+(?:have\s+)?paid\b", lowered)
            or re.search(r"\bpayment\s+(?:is\s+)?(?:done|completed)\b", lowered)
            or re.search(r"\bpaid\s+(?:already|now|successfully)\b", lowered)
            or (
            re.search(r"\b(?:paid|payment|checkout)\b", lowered)
            and re.search(r"\b(?:done|completed|received|through|status|confirm|confirmed|went)\b", lowered)
            )
        )

    def _handle_payment_status_request(self) -> tuple[str, dict | None]:
        booking_id = str(self.memory.data.get("booking_id") or "").strip()
        venue_id = str(self.memory.data.get("venueId") or self.memory.data.get("venue_id") or "").strip()
        if not booking_id:
            return (
                "I don't see a completed reservation yet, so there is no payment status to verify. "
                f"{self._next_booking_detail_prompt()}"
            ), self._booking_result
        if not venue_id:
            return (
                f"I have booking ID {booking_id}, but I don't have the venue ID needed to verify payment with Kreeda. "
                "I'll keep the booking details intact for the team to check."
            ), self._booking_result
        try:
            status_result = self.orchestrator.check_payment_status(venue_id, booking_id)
        except Exception as exc:
            logger.exception(
                "KREEDA_PAYMENT_STATUS_CHECK_FAILED booking_id=%s reason=%s:%s",
                booking_id,
                type(exc).__name__,
                exc,
            )
            return (
                "I couldn't verify the payment status with Kreeda just now. "
                "Your reservation details are still saved, and I won't ask you to pay again until we can verify it."
            ), self._booking_result
        return self._apply_payment_status_result(status_result), self._booking_result

    def _apply_payment_status_result(self, status_result: dict[str, Any]) -> str:
        booking_id = str(status_result.get("bookingId") or self.memory.data.get("booking_id") or "")
        status = str(status_result.get("status") or "").upper()
        is_paid = bool(status_result.get("isPaid")) or status == "CONFIRMED"
        self.memory.data["last_payment_status"] = status_result
        if is_paid:
            self.memory.data["booking_status"] = "CONFIRMED"
            self.memory.data["bookingStatus"] = "CONFIRMED"
            self.memory.data["payment_status"] = "PAID"
            self.memory.data["paymentStatus"] = "PAID"
            self.memory.data["current_workflow"] = "BOOKING_CONFIRMED"
            self.memory.data["booking_phase"] = "BOOKING_CONFIRMED"
            self.memory.save()
            return (
                "Your payment has been received successfully. "
                "Your booking is now confirmed. "
                "We look forward to seeing you."
            )

        deadline = str(
            status_result.get("paymentDeadline")
            or self.memory.data.get("paymentDeadline")
            or self.memory.data.get("payment_deadline")
            or ""
        )
        if status in {"EXPIRED", "CANCELLED", "RELEASED"} or self._payment_deadline_passed(deadline):
            self.memory.data["booking_status"] = "EXPIRED"
            self.memory.data["bookingStatus"] = "EXPIRED"
            self.memory.data["payment_status"] = "EXPIRED"
            self.memory.data["paymentStatus"] = "EXPIRED"
            self.memory.data["current_workflow"] = "booking"
            self.memory.data["booking_phase"] = "PAYMENT_EXPIRED"
            self._clear_selected_slot()
            self._available_slots = []
            self._last_availability = {}
            self.memory.save()
            return (
                "The payment window has expired, so your reservation has been released automatically. "
                "The slot may now be available for other customers. "
                "I can check the latest availability and reserve another slot if you'd like."
            )

        self.memory.data["booking_status"] = "RESERVED"
        self.memory.data["bookingStatus"] = "RESERVED"
        self.memory.data["payment_status"] = "UNPAID"
        self.memory.data["paymentStatus"] = "UNPAID"
        self.memory.save()
        link = str(self.memory.data.get("paymentUrl") or self.memory.data.get("payment_link") or "")
        suffix = f" Payment link: {link}" if link else ""
        return (
            f"Booking ID {booking_id} is still reserved, and payment is not marked complete yet."
            f"{suffix}"
        )

    @staticmethod
    def _payment_deadline_passed(value: str) -> bool:
        if not value:
            return False
        try:
            deadline = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) >= deadline.astimezone(timezone.utc)
        except ValueError:
            return False

    def _is_waiting_for_booking_detail(self) -> bool:
        return self._state in {
            self._STATE_WAITING_FOR_AGE,
            self._STATE_WAITING_FOR_FIRST_NAME,
            self._STATE_WAITING_FOR_LAST_NAME,
            self._STATE_WAITING_FOR_PHONE,
            self._STATE_READY_FOR_BOOKING,
        }

    def _continue_booking_detail_collection(self) -> tuple[str, dict | None]:
        if self._booking_gate_ready():
            return self._prepare_selected_booking(self._selected_slot or str(self.memory.data.get("selected_slot") or ""))
        return self._next_booking_detail_prompt(prefix="Got it. "), None

    def _booking_gate_ready(self) -> bool:
        return not self._booking_gate_missing_fields()

    def _next_booking_detail_prompt(self, prefix: str = "") -> str:
        if not self.memory.data.get("selected_slot"):
            self._state = self._STATE_WAITING_FOR_SLOT
            slots_text = self._format_slots(self._available_slots)
            return f"{prefix}Which available slot would you prefer{': ' + slots_text if slots_text else ''}?"
        self._selected_slot = self._selected_slot or str(self.memory.data.get("selected_slot") or "")
        if not self.memory.data.get("customer_name"):
            self._state = self._STATE_WAITING_FOR_FIRST_NAME
            return f"{prefix}May I have your name?"
        if not self.memory.data.get("phone"):
            self._state = self._STATE_WAITING_FOR_PHONE
            return f"{prefix}What's the best phone number for the booking?"
        if not self.memory.data.get("age_group"):
            self._state = self._STATE_WAITING_FOR_AGE
            return f"{prefix}Are the players adults, kids, or a mix?"
        self._state = self._STATE_READY_FOR_BOOKING
        return f"{prefix}I have everything I need to reserve the booking."

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
        if not self._has_time_preference():
            self._state = self._STATE_WAITING_FOR_TIME_PREFERENCE
            return self._time_preference_question(), None

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
            return self._availability_slots_response(new_date, location, participants), None
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
        alts = availability.get("alternative_games", [])
        alt_text = ""
        if alts:
            alt_text = f" However, we have other games that can fit your group size, such as {', '.join(alts)}."
        return (
            f"Kreeda returned time slots, but none can fit all {participants} players in one room{capacity_text}. "
            f"I haven't treated those times as bookable for your group. Our events team can help split the group across rooms.{alt_text}"
        )

    def _cancellation_policy_explanation(self, *, include_reschedule: bool = False) -> str:
        policy = get_venue_policy(str(self.memory.data.get("location") or ""))
        cancellation = str(policy.get("cancellationPolicy") or "").strip()
        reschedule = str(policy.get("reschedulePolicy") or "").strip()
        if include_reschedule and reschedule:
            return f"{cancellation} Rescheduling: {reschedule}".strip()
        return cancellation or reschedule

    def _reschedule_policy_explanation(self) -> str:
        policy = get_venue_policy(str(self.memory.data.get("location") or ""))
        reschedule = str(policy.get("reschedulePolicy") or "").strip()
        cancellation = str(policy.get("cancellationPolicy") or "").strip()
        return reschedule or cancellation

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
        if self._state == self._STATE_WAITING_FOR_SLOT_CONFIRMATION:
            pending = str(self.memory.data.get("pending_slot_confirmation") or "").strip()
            if pending:
                return f"{answer} Just to confirm, would you like the {pending} slot?"
        if self._state == self._STATE_WAITING_FOR_SLOT:
            slots_text = self._format_slots(self._available_slots)
            return f"{answer} The available slots are {slots_text}. Which time would you prefer?"
        if self._state == self._STATE_WAITING_FOR_TIME_PREFERENCE:
            return f"{answer} What time works best for you: morning, afternoon, or evening?"
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

    def _capture_time_preference(self, message: str) -> None:
        lowered = message.lower()
        if self._is_any_time_preference(lowered):
            self.memory.data["time_preference"] = "any"
            self.memory.data["preferred_time"] = ""
            self.memory.data["requested_time"] = ""
            self.memory.data["preferred_period"] = ""
            self.memory.save()
            return
        period = self.memory._extract_preferred_period(lowered)
        if period:
            self.memory.set_field("preferred_period", period, message, expected_field="preferred_period")
            self.memory.data["time_preference"] = "period"
            self.memory.data["requested_time"] = period
            self.memory.save()

    @staticmethod
    def _is_any_time_preference(lowered: str) -> bool:
        return bool(
            re.search(
                r"\b(?:any\s+time|anytime|no\s+preference|no\s+specific\s+time|"
                r"whatever\s+is\s+available|anything\s+works|any\s+slot|any\s+available)\b",
                lowered,
            )
        )

    def _has_time_preference(self) -> bool:
        return bool(
            self.memory.data.get("selected_slot")
            or self.memory.data.get("preferred_time")
            or self.memory.data.get("preferred_period")
            or self.memory.data.get("time_preference") == "any"
        )

    @staticmethod
    def _time_preference_question() -> str:
        return "What time works best for you: morning, afternoon, or evening?"

    @staticmethod
    def _format_slot_lines(slots: list[str]) -> str:
        return "\n".join(f"• {slot}" for slot in slots)

    def _availability_slots_response(self, date: str, location: str, participants: int) -> str:
        name = str(self.memory.data.get("customer_name", ""))
        greeting = f"Thank you{', ' + name if name else ''}. " if name else ""
        preferred_time = str(self.memory.data.get("preferred_time") or "").strip()
        if preferred_time:
            matched = self._match_slot(preferred_time, self._available_slots)
            if matched:
                suggested = [matched]
                self.memory.data["last_suggested_slots"] = suggested
                self.memory.save()
                return (
                    f"{greeting}I've checked availability for {date} at {location} for {participants} players. "
                    f"{matched} is available. Would you like that slot?"
                )
            nearest = self._nearby_slots_for_preferred_time(preferred_time, self._available_slots, limit=3)
            self.memory.data["last_suggested_slots"] = nearest
            self.memory.save()
            slots_text = self._format_slot_lines(nearest or self._available_slots)
            return (
                f"{greeting}I've checked availability for {date} at {location} for {participants} players. "
                f"I couldn't find exactly {preferred_time}.\n\n"
                f"The closest available slots are:\n\n{slots_text}\n\n"
                "Which time would you prefer?"
            )

        period = str(self.memory.data.get("preferred_period") or "").strip()
        if period:
            slots_text = self._format_slot_lines(self._available_slots)
            return (
                f"{greeting}I've checked availability for {date} at {location} for {participants} players. "
                f"The available {period} slots are:\n\n{slots_text}\n\n"
                "Which time would you prefer?"
            )

        slots_text = self._format_slot_lines(self._available_slots)
        return (
            f"{greeting}I've checked availability for {date} at {location} for {participants} players. "
            f"The available slots are:\n\n{slots_text}\n\n"
            "Which time would you prefer?"
        )

    @classmethod
    def _slot_outside_operating_window(cls, slot: str) -> bool:
        minutes = cls._slot_total_minutes(slot)
        if minutes < 0:
            return False
        return minutes < 9 * 60 or minutes > 23 * 60

    def _outside_hours_response(self, slot: str) -> str:
        location = str(self.memory.data.get("location") or "").strip()
        room = str(self.memory.data.get("room") or "").strip()
        date = str(self.memory.data.get("preferred_date") or "that date").strip()
        context = ""
        if room and location:
            context = f" for {room} at {location} on {date}"
        elif location:
            context = f" at {location} on {date}"
        return (
            f"{slot} is outside Breakout's bookable slot window{context}, so it won't be available. "
            "The nearest real options are later in the day; I can check late-morning, afternoon, or evening slots."
            + ("" if location else " Share the location if you want me to check the nearest real alternatives.")
        )

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

    @classmethod
    def _nearby_slots_for_preferred_time(cls, chosen: str, available: list[str], limit: int = 3) -> list[str]:
        target = cls._slot_total_minutes(chosen)
        if target < 0:
            return cls._nearest_slots(chosen, available, limit=limit)
        parsed = [
            (cls._slot_total_minutes(slot), slot)
            for slot in cls._sorted_slots(available)
            if cls._slot_total_minutes(slot) >= 0
        ]
        before = [slot for minutes, slot in parsed if minutes <= target]
        after = [slot for minutes, slot in parsed if minutes > target]
        selected: list[str] = []
        if before:
            selected.append(before[-1])
        selected.extend(after[: max(limit - len(selected), 0)])
        if len(selected) < limit:
            for slot in reversed(before[:-1]):
                if slot not in selected:
                    selected.append(slot)
                if len(selected) >= limit:
                    break
        return selected[:limit]

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
        self._available_slots = []
        self._last_availability = {}
        self.memory.data["selected_slot"] = ""
        self.memory.data["last_suggested_slots"] = []
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
                "locked", "lock in", "lock inside", "hints", "hint system", "clue",
                "wheelchair", "accessible", "accessibility", "group photo", "photo",
                "restroom", "washroom", "bathroom", "toilet", "loo", "birthday", "bday", "cake",
            )
        )

    def _booking_interruption_answer(self, message: str) -> str:
        if self._is_booking_faq(message):
            return self._booking_faq_answer(message)
        if self._is_direct_question(message):
            return "I'm not sure about that — our branch team would be the best people to confirm."
        return ""

    @staticmethod
    def _is_direct_question(message: str) -> bool:
        lowered = message.lower().strip()
        return bool(
            "?" in message
            or re.match(
                r"^(?:can|could|do|does|did|is|are|will|would|what|when|where|why|how|if)\b",
                lowered,
            )
        )

    @staticmethod
    def _is_generic_slot_confirmation(lowered: str) -> bool:
        cleaned = lowered.strip(" .!?")
        generic = {
            "yes", "yeah", "yep", "yup", "sure", "ok", "okay",
            "that works", "works", "works for me", "sounds good",
            "fine", "perfect", "great", "let's do it", "lets do it",
            "go ahead", "confirm", "please confirm",
        }
        return cleaned in generic or bool(
            re.fullmatch(
                r"(?:yes|yeah|yep|sure|ok|okay|sounds good|that works)[,. ]*(?:let'?s do it|go ahead|confirm it)?",
                cleaned,
            )
        )

    @classmethod
    def _is_affirmative_slot_confirmation(cls, lowered: str) -> bool:
        return cls._is_generic_slot_confirmation(lowered)

    @staticmethod
    def _is_negative_slot_confirmation(lowered: str) -> bool:
        cleaned = lowered.strip(" .!?")
        return cleaned in {"no", "nope", "nah", "not that one", "different one"} or cleaned.startswith("no ")

    @staticmethod
    def _discount_answer() -> str:
        return (
            "Yes. The knowledge base lists 10% off for 4 or more players, "
            "10% off for 6 or more players, and a 10% student discount with valid ID."
        )

    def _price_answer(self) -> str:
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
        return "Pricing depends on location, room, date, and group size. Once those are set, I can confirm the final amount through the booking flow."

    @classmethod
    def _unknown_room_selection_name(cls, message: str) -> str:
        lowered = message.lower()
        if any(room in lowered for room in cls._ESCAPE_ROOM_NAMES):
            return ""
        if "?" in message or any(
            term in lowered
            for term in (
                "closest", "nearest", "discount", "offer", "coupon", "promo",
                "recommend", "suggest", "available", "availability", "slot",
                "that works", "works for me", "sounds good", "do you have",
            )
        ):
            return ""
        if "locked inside" in lowered and re.search(r"\b(?:let'?s|lets|book|choose|pick|go with|do)\b", lowered):
            return "Locked Inside"
        return ""

    def _unknown_room_selection_response(self, room_name: str) -> str:
        options = self._format_slots(
            ["Murder Mystery", "Hostage", "Classified", "Undercover", "Bomb Defusal", "Missile Attack"]
        )
        return f"I don't see a room called {room_name} in the current room list. The bookable room names include {options}. Which room did you mean?"

    def _booking_faq_answer(self, message: str) -> str:
        lowered = message.lower()
        if "wheelchair" in lowered or "accessible" in lowered or "accessibility" in lowered:
            return "I don't have verified accessibility details for every room here, so the branch team should confirm the exact room access before you arrive."
        if "group photo" in lowered or "photo" in lowered:
            return "You can ask the branch team for a quick group photo after the game; it depends on on-site timing, but it is usually a simple request."
        if any(term in lowered for term in ("restroom", "washroom", "bathroom", "toilet", "loo")):
            return "If someone needs the restroom mid-game, staff can help them step out safely. It may use some game time, but safety and comfort come first."
        if "birthday" in lowered or "bday" in lowered:
            return "Yes, an escape room can work well for a birthday group, especially if everyone is comfortable with puzzles and teamwork. Add-ons like cake or food should be confirmed with the branch team."
        if "cake" in lowered:
            return "Cake or celebration add-ons should be confirmed with the branch team, especially if you need storage, setup, or a table after the game."
        if "locked" in lowered or "lock inside" in lowered or "lock in" in lowered:
            return "You are not actually locked in. The staff monitors the game and can open the room if needed."
        if "hint" in lowered or "clue" in lowered:
            return "The game master can provide hints when your team needs help, so first-time players do not get stuck for long."
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
        bare = re.search(r"\b(?:(around|about|at|by|closest|nearest)\s+)?(\d{1,2})(?::(\d{2}))?\b", lowered)
        if bare:
            hour_text = bare.group(2)
            direct_anchor = bool(bare.group(1))
            short_numeric_reply = bool(re.fullmatch(r"\s*\d{1,2}(?::\d{2})?\s*", lowered))
            adjacent_period = bool(
                re.search(rf"\b{re.escape(hour_text)}(?::\d{{2}})?\s+(?:evening|tonight)\b", lowered)
            )
            conversational_slot_anchor = bool(
                re.search(r"\b(?:then|works|slot|closest|nearest)\b", lowered)
            )
        if bare and (direct_anchor or short_numeric_reply or adjacent_period or conversational_slot_anchor):
            after = lowered[bare.end(): bare.end() + 24]
            before = lowered[max(0, bare.start() - 12): bare.start()]
            if (
                re.match(r"\s*(?:people|persons|guests|kids|children|adults|participants|players|members|friends|employees|colleagues|of us)\b", after)
                or re.search(r"\b(?:for|group of)\s*$", before)
            ):
                return ""
            hour = int(hour_text)
            if 1 <= hour <= 11:
                minute = bare.group(3) or "00"
                has_minutes = bool(bare.group(3))
                if has_minutes and hour <= 8:
                    period = "PM"
                elif hour >= 5:
                    period = "PM"
                else:
                    return ""
                return f"{hour}:{minute} {period}"

        return ""

    @staticmethod
    def _ambiguous_bare_time_reference(message: str) -> str:
        lowered = message.lower().strip()
        if re.search(r"\b(?:am|pm|morning|afternoon|evening|tonight)\b", lowered):
            return ""
        _WORDS = {"one": "1", "two": "2", "three": "3", "four": "4"}
        for word, digit in _WORDS.items():
            lowered = re.sub(rf"\b{word}\b", digit, lowered)
        match = re.search(r"\b(?:around|about|at|by)\s+([1-4])\b(?!\s*:)", lowered)
        if match:
            return match.group(1)
        if re.fullmatch(r"\s*([1-4])\s*", lowered):
            return lowered.strip()
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
