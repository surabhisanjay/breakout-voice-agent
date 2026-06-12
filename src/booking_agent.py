"""
BookingAgent — handles slot confirmation and booking creation (Voice Agent).
BookingSimulator — in-memory booking backend simulator.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .agent_response import AgentResponse
from .booking_provider import (
    BookingProvider,
    ProviderAvailabilityAdapter,
    ProviderBookingAdapter,
    build_booking_provider,
)
from .conversation_modes import ConversationMode, ConversationModeDetector
from .conversation_memory import ConversationMemory
from .response_composer import ResponseComposer


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
    _STATE_WAITING_FOR_ALT_DATE = "waiting_for_alt_date"
    _STATE_BOOKING_CONFIRMED = "booking_confirmed"
    _STATE_CLOSED = "closed"

    def __init__(self, memory: ConversationMemory, provider: BookingProvider | None = None) -> None:
        self.memory = memory
        self.provider = provider or build_booking_provider()
        self.availability_tool = ProviderAvailabilityAdapter(self.provider, memory.data)
        self.booking_tool = ProviderBookingAdapter(self.provider)
        self.mode_detector = ConversationModeDetector()
        self.response_composer = ResponseComposer(
            Path(__file__).resolve().parents[1] / "prompts" / "breakout_personality_prompt.txt"
        )

        # Internal state machine
        self._state: str = self._STATE_CHECKING_AVAILABILITY
        self._available_slots: list[str] = []
        self._last_availability: dict = {}
        self._booking_result: dict | None = None

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
        self.memory.add_turn("customer", message)

        if self._state == self._STATE_CHECKING_AVAILABILITY:
            response, booking_result = self._handle_availability_check()

        elif self._state == self._STATE_WAITING_FOR_SLOT:
            response, booking_result = self._handle_slot_selection(message)

        elif self._state == self._STATE_WAITING_FOR_ALT_DATE:
            response, booking_result = self._handle_alt_date(message)

        elif self._state == self._STATE_BOOKING_CONFIRMED:
            response, booking_result = self._handle_post_booking(message)

        else:  # _STATE_CLOSED
            response = "Is there anything else I can help you with today?"
            booking_result = self._booking_result

        response = self._clean_response(response)
        mode = self.mode_detector.detect(message, self.memory.data, str(self.memory.data.get("intent", "")))
        self.memory.data["conversation_mode"] = mode.value
        response = self.response_composer.compose(
            draft=response,
            message=message,
            state=self.memory.data,
            intent=str(self.memory.data.get("intent", "")),
            mode=ConversationMode.BOOKING if mode != ConversationMode.RESCUE else mode,
        )
        self.memory.add_turn("agent", response)

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

        # === TOOL CALL FIRST ===
        booking_result = self.booking_tool.create(self.memory.data, matched)
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
                f"Is there anything else I can help you with?"
            ), booking_result
        return (
            f"Perfect{', ' + name if name else ''}. I've prepared the booking for "
            f"{participants} {'guest' if str(participants) == '1' else 'guests'} at {location} "
            f"on {date} at {matched}. Your checkout reference is {booking_id}. "
            "The booking will be confirmed after checkout is completed."
        ), booking_result

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
        lowered = message.lower()

        if any(word in lowered for word in ("no", "that's all", "nothing", "thanks", "thank you", "bye", "goodbye")):
            name = str(self.memory.data.get("customer_name", ""))
            return (
                f"Thank you{', ' + name if name else ''}. "
                f"We look forward to seeing you. Have a great day!"
            ), self._booking_result

        return (
            "Of course. Is there anything specific you'd like to know before your visit?"
        ), self._booking_result

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
            r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
            lowered,
            re.IGNORECASE,
        )
        if match:
            hour = match.group(1)
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
