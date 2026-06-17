from __future__ import annotations

import os
import logging
import re
from typing import Any, Dict, List, Optional
from datetime import datetime

from ..integrations.kreeda.breakout_booking_provider import BreakoutBookingProvider
from ..integrations.kreeda.agent_contract_provider import AgentContractProvider
from ..core.booking_provider import BookingProvider, SimulatorProvider

logger = logging.getLogger(__name__)


class BookingOrchestrator:
    """
    Orchestrates booking actions between BreakoutBookingProvider (Integration B)
    for operational flow and AgentContractProvider (Integration A) for advanced operations.
    """

    def __init__(
        self,
        booking_provider: BreakoutBookingProvider | None = None,
        contract_provider: AgentContractProvider | None = None,
        simulator_provider: SimulatorProvider | None = None,
    ) -> None:
        # Always initialise simulator as safety net
        self.simulator = simulator_provider or SimulatorProvider()

        # Check if live credentials are configured
        api_key = os.environ.get("BOOKING_API_KEY", "")
        base_url = os.environ.get("BOOKING_BASE_URL", "")
        credentials_present = bool(api_key and base_url)

        self.is_live = False
        self.booking_provider = None
        self.contract_provider = None

        demo_mode = os.environ.get("DEMO_MODE", "false").lower() == "true"

        if credentials_present and not demo_mode:
            try:
                from ..integrations.kreeda.breakout_api import BreakoutAPI
                self.booking_provider = booking_provider or BreakoutBookingProvider(
                    client=BreakoutAPI(timeout=2.0)
                )
                self.contract_provider = contract_provider or AgentContractProvider(timeout=2.0)
                self.is_live = True
                logger.info("BookingOrchestrator configured for LIVE mode; connectivity is checked on first use.")
            except Exception as exc:
                logger.warning(f"Live booking provider failed. Falling back to simulator. Reason: {exc}")
                self.is_live = False
        else:
            logger.info("BookingOrchestrator initialized in SIMULATOR mode.")


        # Caching/lookup dictionaries for live mode to match names to IDs
        self._slot_lookup: Dict[str, Dict[str, Any]] = {}
        self._location: Dict[str, Any] = {}
        self._game: Dict[str, Any] = {}

    def check_availability(self, *args, **kwargs) -> Any:
        """
        Polymorphic wrapper for check_availability.
        Routes to _check_availability_operational or _check_availability_langgraph.
        """
        # If at least 3 positional args are provided, or "participants"/"location" in kwargs,
        # it is the operational check_availability(location, date, participants, room).
        # Otherwise, it is the LangGraph check_availability(service, date).
        is_operational = False
        if len(args) >= 3:
            is_operational = True
        elif "participants" in kwargs or "location" in kwargs:
            is_operational = True
            
        if is_operational:
            return self._check_availability_operational(*args, **kwargs)
        else:
            return self._check_availability_langgraph(*args, **kwargs)

    def _check_availability_operational(self, location: str, date: str, participants: int, room: str = "") -> dict[str, Any]:
        """
        Operational booking flow: checks slot availability.
        Routes to BreakoutBookingProvider in live mode, or SimulatorProvider in mock mode.
        """
        if not self.is_live:
            return self.simulator.check_availability(location, date, participants, room)

        try:
            locations = self.booking_provider.get_locations()
            location_record = self._match_record(locations, "locationName", location)
            if not location_record:
                return {"available": False, "slots": [], "location": location, "date": date, "participants": participants}

            games = self.booking_provider.get_games(location_record["locationId"])
            game_record = self._match_record(games, "gameName", room) if room else (games[0] if games else None)
            if not game_record:
                return {"available": False, "slots": [], "location": location, "date": date, "participants": participants}

            api_date = self._normalise_date(date)
            slots = self.booking_provider.get_slots(
                location_record["locationId"],
                [game_record["gameId"]],
                api_date,
                api_date
            )
            available_slots = [slot for slot in slots if slot.get("isAvailable")]
            
            # Save lookup state for preparing bookings
            self._location = location_record
            self._game = game_record
            self._slot_lookup = {
                self._normalise_time(str(slot.get("time", ""))): slot for slot in available_slots
            }
            display_slots = [self._display_time(str(slot.get("time", ""))) for slot in available_slots]

            return {
                "available": bool(display_slots),
                "slots": display_slots,
                "location": location,
                "date": date,
                "participants": participants,
            }
        except Exception as exc:
            logger.warning(f"Live check_availability failed ({type(exc).__name__}: {exc}). Using simulator.")
            self.is_live = False
            return self.simulator.check_availability(location, date, participants, room)

    def prepare_booking(self, memory: dict[str, Any], chosen_slot: str) -> dict[str, Any]:
        """
        Operational booking flow: prepares a booking reservation.
        Routes to BreakoutBookingProvider in live mode, or SimulatorProvider in mock mode.
        """
        missing = [
            field
            for field in ("age_group", "location", "preferred_date", "customer_name", "phone")
            if not memory.get(field)
        ]
        if not (memory.get("participants") or memory.get("company_size")):
            missing.append("participants")
        if missing:
            return {
                "booking_id": "",
                "confirmed": False,
                "prepared": False,
                "error": f"Missing required booking fields: {', '.join(missing)}",
            }

        if not self.is_live:
            return self.simulator.prepare_booking(memory, chosen_slot)

        try:
            slot = self._slot_lookup.get(self._normalise_time(chosen_slot))
            if not slot:
                return {"booking_id": "", "confirmed": False, "error": "The selected slot is no longer available."}

            first_name, last_name = self._split_name(str(memory.get("customer_name", "")))
            payload = {
                "locationId": self._location.get("locationId"),
                "gameId": self._game.get("gameId") or slot.get("gameId"),
                "slotId": slot.get("slotId"),
                "isPrivate": bool(self._game.get("privateEnabled", True)),
                "customerFirstName": first_name,
                "customerLastName": last_name,
                "customerPhone": memory.get("phone", ""),
            }
            result = self.booking_provider.prepare_booking(payload)
            booking_id = result.get("bookingId", "")
            return {
                "booking_id": booking_id,
                "confirmed": False,
                "prepared": True,
                "location": memory.get("location", ""),
                "date": memory.get("preferred_date", ""),
                "slot": chosen_slot,
                "participants": memory.get("participants") or memory.get("company_size", ""),
                "event_type": memory.get("event_type", ""),
                "customer_name": memory.get("customer_name", ""),
                "phone": memory.get("phone", ""),
            }
        except Exception as exc:
            logger.warning(f"Live prepare_booking failed ({type(exc).__name__}: {exc}). Using simulator.")
            self.is_live = False
            return self.simulator.prepare_booking(memory, chosen_slot)

    def release_slots(self, slot_ids: List[str]) -> dict[str, Any]:
        """
        Operational booking flow: releases slots.
        Routes to BreakoutBookingProvider in live mode, or returns mock response in mock mode.
        """
        if not self.is_live:
            return {"status": "success", "released_slots": slot_ids}
        return self.booking_provider.release_slots(slot_ids)

    def cancel_booking(self, booking_ref: str, reason: str | None = None) -> dict[str, Any]:
        """
        Advanced booking operations: cancels a booking.
        Routes to AgentContractProvider in live mode, or returns mock response in mock mode.
        """
        if not self.is_live:
            booking = self.simulator.bookings.get(booking_ref)
            if not booking:
                booking = self.find_booking(booking_ref)
                self.simulator.bookings[booking_ref] = booking
            booking["status"] = "cancelled"
            return {
                "booking_id": booking_ref,
                "status": "cancelled",
                "reason": reason or "customer_request",
                "timestamp": datetime.now().isoformat(),
            }
        return self.contract_provider.cancel_booking(booking_ref, reason)

    def reschedule_booking(self, booking_ref: str, new_slot_id: str) -> dict[str, Any]:
        """
        Advanced booking operations: reschedules a booking.
        Routes to AgentContractProvider in live mode, or returns mock response in mock mode.
        """
        if not self.is_live:
            booking = self.simulator.bookings.get(booking_ref)
            if not booking:
                booking = self.find_booking(booking_ref)
                self.simulator.bookings[booking_ref] = booking
            slot = self.simulator.slots.get(new_slot_id)
            if slot:
                booking["slot"] = slot["time"]
                booking["date"] = slot["date"]
                booking["location"] = slot["location"]
                slot["available"] = False
            else:
                booking["slot"] = new_slot_id
            booking["status"] = "rescheduled"
            return {
                "booking_id": booking_ref,
                "status": "rescheduled",
                "new_slot_id": new_slot_id,
                "timestamp": datetime.now().isoformat(),
            }
        return self.contract_provider.reschedule_booking(booking_ref, new_slot_id)

    def find_booking(self, booking_ref: str) -> dict[str, Any]:
        """
        Advanced booking operations: looks up a booking by reference.
        Routes to AgentContractProvider in live mode, or returns mock response in mock mode.
        """
        if not self.is_live:
            booking = self.simulator.bookings.get(booking_ref)
            if booking:
                return booking
            return {
                "booking_id": booking_ref,
                "confirmed": True,
                "location": "Koramangala",
                "date": "18 June",
                "slot": "12:00 PM",
                "participants": 4,
                "customer_name": "Demo User",
                "phone": "9876543210",
                "status": "confirmed",
            }
        return self.contract_provider.find_booking(booking_ref)

    # ------------------------------------------------------------------ #
    # LangGraph Compatibility Methods                                     #
    # ------------------------------------------------------------------ #

    def add_slot(self, date: str, time_str: str, service: str, capacity: int = 10) -> str:
        """Add a slot. Delegates to simulator in mock mode, or returns a mock ID in live."""
        if not self.is_live:
            from ..agents.booking_agent import BookingSimulator
            if isinstance(self.simulator, BookingSimulator):
                return self.simulator.add_slot(date, time_str, service, capacity)
            return "sim-slot-id"
        import uuid
        return f"slot-{uuid.uuid4().hex[:6]}"

    def _check_availability_langgraph(self, service: str, date: str | None = None) -> list[Any]:
        """Compatibility method for check_availability by service. Used by LangGraph."""
        if not self.is_live:
            from ..agents.booking_agent import BookingSimulator
            if isinstance(self.simulator, BookingSimulator):
                return self.simulator.check_availability(service, date)
            return []
        
        # Live mode: shim using get_slots for a default location
        try:
            locations = self.booking_provider.get_locations()
            loc_record = locations[0] if locations else {"locationId": "default-loc-id"}
            api_date = self._normalise_date(date or "2026-07-01")
            slots = self.booking_provider.get_slots(loc_record["locationId"], None, api_date, api_date)
            available_slots = [slot for slot in slots if slot.get("isAvailable")]
            
            # Save lookup state
            self._location = loc_record
            self._slot_lookup = {
                self._normalise_time(str(slot.get("time", ""))): slot for slot in available_slots
            }
            
            from dataclasses import dataclass
            @dataclass
            class SlotShim:
                slot_id: str
                date: str
                time: str
                service: str
                capacity: int
                locked_by: Optional[str] = None
                booking_ref: Optional[str] = None

            return [
                SlotShim(
                    slot_id=s.get("slotId", "id"),
                    date=api_date,
                    time=s.get("time", ""),
                    service=service,
                    capacity=s.get("capacity", 10)
                ) for s in available_slots
            ]
        except Exception:
            return []

    def lock_slot(self, slot_id: str, owner_id: str, ttl_seconds: int = 30) -> bool:
        """Lock a slot. Delegates to simulator in mock mode, or returns True in live."""
        if not self.is_live:
            from ..agents.booking_agent import BookingSimulator
            if isinstance(self.simulator, BookingSimulator):
                return self.simulator.lock_slot(slot_id, owner_id, ttl_seconds)
            return True
        return True

    def unlock_slot(self, slot_id: str, owner_id: str) -> None:
        """Unlock a slot. Delegates to simulator in mock mode, or no-op in live."""
        if not self.is_live:
            from ..agents.booking_agent import BookingSimulator
            if isinstance(self.simulator, BookingSimulator):
                self.simulator.unlock_slot(slot_id, owner_id)

    def create_booking(self, slot_id: str, customer: dict[str, Any], require_payment: bool = False) -> Any:
        """Create a booking reservation."""
        if not self.is_live:
            from ..agents.booking_agent import BookingSimulator
            if isinstance(self.simulator, BookingSimulator):
                return self.simulator.create_booking(slot_id, customer, require_payment)
            
            # Mock fallback if simulator is not BookingSimulator
            from dataclasses import dataclass
            @dataclass
            class BookingRecordShim:
                reference: str
                slot_id: str
                customer: dict
                status: str = "confirmed"
                payment_required: bool = False
                payment_payload: dict | None = None
            return BookingRecordShim("BK-MOCK", slot_id, customer, "confirmed", require_payment, None)

        first_name, last_name = self._split_name(customer.get("customer_name", ""))
        payload = {
            "locationId": self._location.get("locationId", "default-loc-id"),
            "gameId": self._game.get("gameId", "default-game-id"),
            "slotId": slot_id,
            "isPrivate": True,
            "customerFirstName": first_name,
            "customerLastName": last_name,
            "customerPhone": customer.get("phone", ""),
        }
        res = self.booking_provider.prepare_booking(payload)
        
        from dataclasses import dataclass
        @dataclass
        class BookingRecordShim:
            reference: str
            slot_id: str
            customer: dict
            status: str = "confirmed"
            payment_required: bool = False
            payment_payload: dict | None = None
            
        return BookingRecordShim(
            reference=res.get("bookingId", "BK-LIVE"),
            slot_id=slot_id,
            customer=customer,
            payment_required=require_payment,
            payment_payload=res if require_payment else None
        )

    # ------------------------------------------------------------------ #
    # Normalisation / Utility Helpers                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _match_record(records: list[dict[str, Any]], key: str, value: str) -> dict[str, Any] | None:
        wanted = value.lower().strip()
        for record in records:
            candidate = str(record.get(key, "")).lower().strip()
            if candidate == wanted or wanted in candidate or candidate in wanted:
                return record
        return None

    @staticmethod
    def _normalise_date(value: str) -> str:
        for fmt in ("%d %B", "%d %b", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value, fmt)
                if fmt != "%Y-%m-%d":
                    parsed = parsed.replace(year=datetime.now().year)
                return parsed.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return value

    @staticmethod
    def _normalise_time(value: str) -> str:
        value = value.strip().upper().replace(".", "")
        value = re.sub(r"(?<=\d):(?=\s*(?:AM|PM)\b)", ":00", value)
        value = re.sub(r"\s*(AM|PM)$", r" \1", value)
        for fmt in ("%I:%M %p", "%I %p", "%H:%M"):
            try:
                return datetime.strptime(value, fmt).strftime("%H:%M")
            except ValueError:
                continue
        return value

    @staticmethod
    def _display_time(value: str) -> str:
        try:
            return datetime.strptime(value, "%H:%M").strftime("%-I:%M %p")
        except ValueError:
            return value

    @staticmethod
    def _split_name(value: str) -> tuple[str, str]:
        parts = value.split(maxsplit=1)
        return (parts[0], parts[1] if len(parts) > 1 else "") if parts else ("", "")
