from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
    """
    Simple in-memory booking agent for scheduling and reservation management.

    This is a local simulator: a production system should replace the
    calendar and locking with durable services (DB + distributed lock).
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

            record = BookingRecord(reference=ref, slot_id=slot_id, customer=customer, payment_required=require_payment, payment_payload=payload)
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
