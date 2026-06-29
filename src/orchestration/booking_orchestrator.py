from __future__ import annotations

import os
import logging
import re
import uuid
from typing import Any, Dict, List, Optional
from datetime import date as calendar_date, datetime, timedelta

from ..integrations.kreeda.breakout_booking_provider import BreakoutBookingProvider
from ..integrations.kreeda.agent_contract_provider import AgentContractProvider
from ..core.booking_provider import BookingProvider, SimulatorProvider
from ..config.env_loader import booking_credentials_present, load_project_env, should_use_live_booking

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
        load_project_env()
        # Always initialise simulator as safety net
        self.simulator = simulator_provider or SimulatorProvider()

        self.is_live = False
        self.booking_provider = None
        self.contract_provider = None
        self.fallback_reason = ""

        provider_mode = os.environ.get("BOOKING_PROVIDER", "auto").strip().lower()
        if should_use_live_booking():
            try:
                from ..integrations.kreeda.breakout_api import BreakoutAPI
                self.booking_provider = booking_provider or BreakoutBookingProvider(
                    client=BreakoutAPI(timeout=self._live_booking_timeout())
                )
                # The operational availability/cart/booking path does not use
                # contract discovery. Construct it lazily for advanced actions.
                self.contract_provider = contract_provider
                self.is_live = True
                logger.info(
                    "Kreeda provider initialized successfully. base_url=%s mode=%s",
                    os.environ.get("BOOKING_BASE_URL", ""),
                    provider_mode or "auto",
                )
            except Exception as exc:
                self.fallback_reason = f"provider_initialization_failed:{type(exc).__name__}: {exc}"
                logger.warning("Simulator fallback reason: %s", self.fallback_reason)
                self.is_live = False
        else:
            if provider_mode in {"simulator", "mock", "offline"}:
                self.fallback_reason = f"BOOKING_PROVIDER={provider_mode}"
            elif not booking_credentials_present():
                self.fallback_reason = "missing BOOKING_API_KEY or BOOKING_BASE_URL"
            else:
                self.fallback_reason = "live booking disabled"
            logger.info("BookingOrchestrator initialized in SIMULATOR mode. reason=%s", self.fallback_reason)


        # Caching/lookup dictionaries for live mode to match names to IDs
        self._slot_lookup: Dict[str, Dict[str, Any]] = {}
        self._location: Dict[str, Any] = {}
        self._game: Dict[str, Any] = {}

    @staticmethod
    def _live_booking_timeout() -> float:
        raw = os.environ.get("BOOKING_HTTP_TIMEOUT_SECONDS", "10.0")
        try:
            timeout = float(raw)
        except (TypeError, ValueError):
            logger.warning("Invalid BOOKING_HTTP_TIMEOUT_SECONDS=%r; using 10.0", raw)
            return 10.0
        return max(timeout, 1.0)

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
            logger.info(
                "Simulator availability fallback. reason=%s location=%s date=%s participants=%s room=%s",
                self.fallback_reason or "not_live",
                location,
                date,
                participants,
                room,
            )
            return self.simulator.check_availability(location, date, participants, room)

        try:
            logger.info(
                "Kreeda availability request: location=%s date=%s participants=%s room=%s",
                location,
                date,
                participants,
                room,
            )
            locations = self.booking_provider.get_booking_venues()
            logger.info("Kreeda location mapping candidates: %s", self._summarise_records(locations, ("venueId", "locationId", "id", "venueName", "locationName", "name")))
            location_record = self._match_record(locations, ("venueName", "locationName", "name", "title"), location)
            if not location_record:
                logger.warning("Kreeda location mapping failed: requested_location=%s", location)
                return {"available": False, "slots": [], "location": location, "date": date, "participants": participants}

            location_id = self._record_value(location_record, ("venueId", "locationId", "id", "_id"))
            logger.info(
                "Kreeda location mapping selected: requested=%s locationId=%s name=%s",
                location,
                location_id,
                self._record_value(location_record, ("venueName", "locationName", "name", "title")),
            )

            games = self.booking_provider.get_booking_games(location_id)
            logger.info("Kreeda room mapping candidates: %s", self._summarise_records(games, ("gameId", "id", "gameName", "name")))
            game_record = self._match_record(games, ("gameName", "name", "title"), room) if room else (games[0] if games else None)
            if not game_record:
                logger.warning(
                    "Kreeda room mapping failed: requested_room=%s location=%s locationId=%s",
                    room,
                    location,
                    location_id,
                )
                return {"available": False, "slots": [], "location": location, "date": date, "participants": participants}

            api_date = self._normalise_date(date)
            if not api_date:
                raise ValueError(f"Unsupported booking date: {date}")
            logger.info("DATE_EXTRACTED=%s", date)
            logger.info("DATE_NORMALIZED=%s", api_date)
            game_id = self._record_value(game_record, ("gameId", "id", "_id"))
            logger.info(
                "Kreeda room mapping selected: requested=%s gameId=%s name=%s api_date=%s",
                room,
                game_id,
                self._record_value(game_record, ("gameName", "name", "title")),
                api_date,
            )
            slots = self.booking_provider.search_booking_slots(location_id, game_id, api_date, api_date)
            logger.info("Kreeda raw slot count: %s", len(slots))
            available_slots = [slot for slot in slots if self._slot_is_available(slot)]
            game_min = self._optional_int(game_record.get("peopleMin"))
            game_max = self._optional_int(game_record.get("peopleMax"))
            game_supports_group = (
                (game_min is None or participants >= game_min)
                and (game_max is None or participants <= game_max)
            )
            bookable_slots = [
                slot
                for slot in available_slots
                if game_supports_group and self._slot_has_capacity(slot, participants)
            ]
            
            # Save lookup state for preparing bookings
            self._location = location_record
            self._game = game_record
            self._slot_lookup = {
                self._normalise_time(str(self._slot_time(slot))): slot for slot in bookable_slots
            }
            display_slots = [self._display_time(str(self._slot_time(slot))) for slot in available_slots]
            bookable_display_slots = [self._display_time(str(self._slot_time(slot))) for slot in bookable_slots]
            logger.info(
                "Kreeda availability response: available=%s slots=%s capacity_supported=%s bookable_slots=%s locationId=%s gameId=%s",
                bool(display_slots),
                len(display_slots),
                bool(bookable_display_slots),
                len(bookable_display_slots),
                location_id,
                game_id,
            )
            if display_slots:
                logger.info(
                    "KREEDA_AVAILABILITY_SUCCESS venueId=%s gameId=%s date=%s slots=%s",
                    location_id,
                    game_id,
                    api_date,
                    len(display_slots),
                )

            return {
                "available": bool(display_slots),
                "slots": bookable_display_slots,
                "all_available_slots": display_slots,
                "location": location,
                "date": date,
                "participants": participants,
                "verified": True,
                "capacity_supported": bool(bookable_display_slots),
                "bookable_slots": bookable_display_slots,
                "max_available_capacity": max(
                    (
                        capacity
                        for slot in available_slots
                        if (capacity := self._slot_available_capacity(slot)) is not None
                    ),
                    default=None,
                ) or game_max,
                "min_group_size": game_min,
                "max_group_size": game_max,
            }
        except Exception as exc:
            self.fallback_reason = f"availability_failed:{type(exc).__name__}: {exc}"
            logger.warning("Kreeda availability failed: %s", self.fallback_reason)
            return {
                "available": False,
                "slots": [],
                "location": location,
                "date": date,
                "participants": participants,
                "verified": False,
                "error": self.fallback_reason,
            }

    def prepare_booking(self, memory: dict[str, Any], chosen_slot: str) -> dict[str, Any]:
        """
        Operational booking flow: prepares a booking reservation.
        Routes to BreakoutBookingProvider in live mode, or SimulatorProvider in mock mode.
        """
        split_first, split_last = self._split_name(str(memory.get("customer_name", "")))
        first_name = str(memory.get("first_name") or split_first).strip()
        last_name = str(memory.get("last_name") or split_last).strip()
        # Use "NA" as lastName fallback so the Kreeda API never rejects single-name bookings.
        # This eliminates the last-name zombie loop entirely.
        api_last_name = last_name if last_name else "NA"
        missing = [
            field
            for field in ("age_group", "location", "preferred_date", "phone")
            if not memory.get(field)
        ]
        if not first_name:
            missing.append("customer.firstName")
        if not (memory.get("participants") or memory.get("company_size")):
            missing.append("participants")
        if missing:
            logger.warning("KREEDA_CREATE_BOOKING_FAILURE reason=missing_required_fields fields=%s", missing)
            return {
                "booking_id": "",
                "confirmed": False,
                "prepared": False,
                "error": f"Missing required booking fields: {', '.join(missing)}",
            }

        if not self.is_live:
            logger.info(
                "Simulator booking fallback. reason=%s chosen_slot=%s",
                self.fallback_reason or "not_live",
                chosen_slot,
            )
            return self.simulator.prepare_booking(memory, chosen_slot)

        create_payload: dict[str, Any] = {}
        cart_id = str(memory.get("_kreeda_cart_id") or "")
        idempotency_key = str(memory.get("_booking_idempotency_key") or "")

        try:
            slot = self._slot_lookup.get(self._normalise_time(chosen_slot))
            if not slot:
                logger.warning("KREEDA_CREATE_BOOKING_FAILURE reason=selected_slot_not_verified slot=%s", chosen_slot)
                return {"booking_id": "", "confirmed": False, "error": "The selected slot is no longer available."}

            venue_id = self._record_value(self._location, ("venueId", "locationId", "id", "_id"))
            game_id = self._record_value(self._game, ("gameId", "id", "_id")) or self._record_value(slot, ("gameId", "game_id"))
            people = self._people_payload(self._game, str(memory.get("age_group", "")), int(memory.get("participants") or memory.get("company_size")))
            if not people:
                logger.warning("KREEDA_CREATE_BOOKING_FAILURE reason=people_category_unavailable")
                return {
                    "booking_id": "",
                    "confirmed": False,
                    "prepared": False,
                    "error": "Kreeda has no matching people category for this age group.",
                }
            slot_payload = {
                "gameId": game_id,
                "date": str(slot.get("date") or self._normalise_date(str(memory.get("preferred_date", "")))),
                "time": self._normalise_time(str(self._slot_time(slot))),
                "people": people,
                "isPrivate": bool(self._game.get("privateEnabled", False)),
            }
            cart_signature = "|".join(
                (venue_id, game_id, slot_payload["date"], slot_payload["time"], str(people))
            )
            cart_id = ""
            if memory.get("_kreeda_cart_signature") == cart_signature:
                cart_id = str(memory.get("_kreeda_cart_id") or "")
            if not cart_id:
                logger.info(
                    "KREEDA_PREPARE_BOOKING venueId=%s gameId=%s date=%s time=%s participants=%s",
                    venue_id,
                    game_id,
                    slot_payload["date"],
                    slot_payload["time"],
                    sum(item["number"] for item in people),
                )
                logger.info(
                    "KREEDA_CREATE_CART_REQUEST venueId=%s gameId=%s date=%s time=%s",
                    venue_id,
                    game_id,
                    slot_payload["date"],
                    slot_payload["time"],
                )
                try:
                    cart_result = self.booking_provider.create_instant_cart(
                        {"venueId": venue_id, "slots": [slot_payload]}
                    )
                except Exception as exc:
                    logger.exception(
                        "KREEDA_CREATE_CART_FAILURE reason=%s:%s",
                        type(exc).__name__,
                        exc,
                    )
                    raise
                cart_id = str(cart_result.get("cartId", ""))
                memory["_kreeda_cart_id"] = cart_id
                memory["_kreeda_cart_signature"] = cart_signature
                if cart_id:
                    logger.info("KREEDA_CREATE_CART_SUCCESS cartId=%s venueId=%s", cart_id, venue_id)
            if not cart_id:
                logger.warning("KREEDA_CREATE_CART_FAILURE reason=missing_cart_id")
                logger.warning("KREEDA_CREATE_BOOKING_FAILURE reason=missing_cart_id")
                return {
                    "booking_id": "",
                    "confirmed": False,
                    "prepared": False,
                    "error": "Kreeda did not return a cartId.",
                }

            idempotency_key = str(memory.get("_booking_idempotency_key") or uuid.uuid4())
            memory["_booking_idempotency_key"] = idempotency_key
            create_payload = {
                "venueId": venue_id,
                "cartId": cart_id,
                "slots": [slot_payload],
                "customer": {
                    "firstName": first_name,
                    "lastName": api_last_name,
                    "phone": self._international_phone(str(memory.get("phone", ""))),
                },
                "sendPaymentRequest": False,
                "idempotencyKey": idempotency_key,
            }
            logger.info(
                "Kreeda booking request: venueId=%s cartId=%s gameId=%s customer=%s phone_suffix=%s",
                venue_id,
                cart_id,
                game_id,
                self._redact_name(str(memory.get("customer_name", ""))),
                self._phone_suffix(str(memory.get("phone", ""))),
            )
            logger.info(
                "KREEDA_CREATE_BOOKING_REQUEST venueId=%s cartId=%s gameId=%s idempotencyKey=%s",
                venue_id,
                cart_id,
                game_id,
                idempotency_key,
            )
            result = self.booking_provider.create_confirmed_booking(create_payload)
            booking_id = result.get("bookingId", "")
            booking_reference = self._record_value(
                result, ("orderId", "bookingReference", "bookingRef", "reference")
            )
            raw_status = str(result.get("status", "") or "PAYMENT_PENDING").upper()
            has_payment_url = bool(result.get("paymentUrl") or result.get("orderUrl"))
            status = "PAYMENT_PENDING" if has_payment_url and raw_status in {"RESERVED", "PENDING"} else raw_status
            confirmed = bool(
                booking_id
                and booking_reference
                and status in {"CONFIRMED", "BOOKED", "RESERVED", "PAYMENT_PENDING", "PENDING"}
            )
            logger.info(
                "Kreeda booking response: bookingId=%s response_keys=%s",
                booking_id,
                sorted(result.keys()) if isinstance(result, dict) else [],
            )
            if confirmed:
                logger.info(
                    "KREEDA_CREATE_BOOKING_SUCCESS bookingId=%s bookingReference=%s status=%s",
                    booking_id,
                    booking_reference,
                    status,
                )
                logger.info("KREEDA_BOOKING_CREATED bookingId=%s status=%s", booking_id, status)
                logger.info("BOOKING_ID=%s", booking_id)
                logger.info("BOOKING_REFERENCE=%s", booking_reference)
                logger.info("BOOKING_REF=%s", booking_reference)
                if status in {"PAYMENT_PENDING", "PENDING"}:
                    logger.info("PAYMENT_PENDING=true bookingId=%s", booking_id)
            else:
                logger.warning(
                    "KREEDA_CREATE_BOOKING_FAILURE reason=unconfirmed_response bookingId=%s bookingReference=%s status=%s",
                    booking_id,
                    booking_reference,
                    status,
                )
            return {
                "booking_id": booking_id,
                "booking_reference": booking_reference,
                "order_id": result.get("orderId", ""),
                "order_url": result.get("orderUrl", ""),
                "payment_url": result.get("paymentUrl") or result.get("orderUrl", ""),
                "status": status,
                "confirmed": confirmed,
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
            self.fallback_reason = f"prepare_booking_failed:{type(exc).__name__}: {exc}"
            logger.exception("KREEDA_CREATE_BOOKING_FAILURE reason=%s", self.fallback_reason)
            logger.warning("Kreeda booking failed: %s", self.fallback_reason)
            return {
                "booking_id": "",
                "confirmed": False,
                "prepared": False,
                "error": self.fallback_reason,
                "provider_error": str(exc),
                "provider_error_type": type(exc).__name__,
                "provider_error_code": str(getattr(exc, "code", "")),
                "retryable": str(getattr(exc, "code", "")) in {"TIMEOUT", "NETWORK_ERROR"},
                "cart_id": cart_id or str(memory.get("_kreeda_cart_id") or ""),
                "idempotency_key": idempotency_key or str(memory.get("_booking_idempotency_key") or ""),
                "payload_fields": sorted(create_payload.keys()) if create_payload else [],
                "state_preserved": True,
                "missing_field": self._missing_customer_field(str(exc)),
            }

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
        return self._contract().cancel_booking(booking_ref, reason)

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
        return self._contract().reschedule_booking(booking_ref, new_slot_id)

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
        return self._contract().find_booking(booking_ref)

    def _contract(self) -> AgentContractProvider:
        if self.contract_provider is None:
            self.contract_provider = AgentContractProvider(timeout=2.0)
        return self.contract_provider

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
                logger.info(
                    "Simulator booking fallback. reason=%s slotId=%s",
                    self.fallback_reason or "not_live",
                    slot_id,
                )
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
        api_last_name = last_name if last_name else "NA"
        payload = {
            "locationId": self._record_value(self._location, ("locationId", "id", "_id")) or "default-loc-id",
            "gameId": self._record_value(self._game, ("gameId", "id", "_id")) or "default-game-id",
            "slotId": slot_id,
            "isPrivate": True,
            "customerFirstName": first_name,
            "customerLastName": api_last_name,
            "customerPhone": customer.get("phone", ""),
        }
        logger.info(
            "Kreeda booking request: locationId=%s gameId=%s slotId=%s customer=%s phone_suffix=%s",
            payload.get("locationId"),
            payload.get("gameId"),
            payload.get("slotId"),
            self._redact_name(str(customer.get("customer_name", ""))),
            self._phone_suffix(str(customer.get("phone", ""))),
        )
        res = self.booking_provider.prepare_booking(payload)
        logger.info(
            "Kreeda booking response: bookingId=%s response_keys=%s",
            res.get("bookingId", ""),
            sorted(res.keys()) if isinstance(res, dict) else [],
        )
        
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
    def _match_record(records: list[dict[str, Any]], keys: tuple[str, ...] | str, value: str) -> dict[str, Any] | None:
        wanted = BookingOrchestrator._normalise_match_text(value)
        candidate_keys = (keys,) if isinstance(keys, str) else keys
        for record in records:
            candidate = BookingOrchestrator._normalise_match_text(
                str(BookingOrchestrator._record_value(record, candidate_keys))
            )
            if candidate == wanted or wanted in candidate or candidate in wanted:
                return record
        return None

    @staticmethod
    def _normalise_match_text(value: str) -> str:
        lowered = value.lower().strip()
        lowered = lowered.replace("diffusal", "defusal")
        lowered = re.sub(r"[^a-z0-9 ]+", " ", lowered)
        return re.sub(r"\s+", " ", lowered).strip()

    @staticmethod
    def _record_value(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
        for key in keys:
            value = record.get(key)
            if value not in (None, ""):
                return value
        return ""

    @staticmethod
    def _summarise_records(records: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
        summary: list[dict[str, Any]] = []
        for record in records[:20]:
            summary.append({key: record.get(key) for key in keys if key in record})
        return summary

    @staticmethod
    def _slot_is_available(slot: dict[str, Any]) -> bool:
        for key in ("isAvailable", "available", "is_available"):
            if key in slot:
                value = slot.get(key)
                if isinstance(value, str):
                    return value.strip().lower() in {"true", "1", "yes", "available"}
                return bool(value)
        status = str(slot.get("status", "")).strip().lower()
        if status:
            return status in {"available", "open", "active"}
        return True

    @staticmethod
    def _slot_has_capacity(slot: dict[str, Any], participants: int) -> bool:
        available = slot.get("available")
        if isinstance(available, bool) or available is None:
            return True
        try:
            return int(available) >= participants
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _slot_available_capacity(slot: dict[str, Any]) -> int | None:
        available = slot.get("available")
        if isinstance(available, bool) or available is None:
            return None
        try:
            return int(available)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value in (None, "") or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _people_payload(game: dict[str, Any], age_group: str, participants: int) -> list[dict[str, Any]]:
        categories = game.get("peopleCategories", [])
        if not isinstance(categories, list):
            return []
        wanted = "kids" if age_group.strip().lower() in {"kid", "kids", "children"} else "adult"
        for category in categories:
            name = str(category.get("categoryName", "")).lower()
            category_id = category.get("categoryId")
            if category_id and wanted in name:
                maximum = category.get("max")
                if maximum not in (None, "") and participants > int(maximum):
                    return []
                return [{"categoryId": category_id, "number": participants}]
        return []

    @staticmethod
    def _slot_time(slot: dict[str, Any]) -> Any:
        for key in ("time", "slotTime", "startTime", "start_time", "displayTime"):
            value = slot.get(key)
            if value:
                return value
        return ""

    @staticmethod
    def _normalise_date(value: str, reference_date: calendar_date | None = None) -> str:
        raw = value.strip()
        lowered = raw.lower()
        today = reference_date or calendar_date.today()

        if lowered == "today":
            return today.isoformat()
        if lowered == "tomorrow":
            return (today + timedelta(days=1)).isoformat()
        if lowered in {"day after tomorrow", "the day after tomorrow"}:
            return (today + timedelta(days=2)).isoformat()

        weekday_names = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        weekday_match = re.fullmatch(r"(?:(next)\s+)?(" + "|".join(weekday_names) + r")", lowered)
        if weekday_match:
            target = weekday_names[weekday_match.group(2)]
            days_ahead = (target - today.weekday()) % 7
            if days_ahead == 0 or weekday_match.group(1):
                days_ahead += 7
            return (today + timedelta(days=days_ahead)).isoformat()

        if lowered in {"this weekend", "next weekend"}:
            days_to_saturday = (5 - today.weekday()) % 7
            if lowered == "next weekend":
                days_to_saturday += 7
            return (today + timedelta(days=days_to_saturday)).isoformat()

        for fmt in ("%d %B", "%d %b", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(raw, fmt)
                if fmt != "%Y-%m-%d":
                    parsed = parsed.replace(year=today.year)
                    if parsed.date() < today:
                        if (today - parsed.date()).days > 7:
                            return ""
                        parsed = parsed.replace(year=today.year + 1)
                return parsed.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return ""

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
        for fmt in ("%H:%M:%S",):
            try:
                return datetime.strptime(value, fmt).strftime("%H:%M")
            except ValueError:
                continue
        return value

    @staticmethod
    def _display_time(value: str) -> str:
        normalised = BookingOrchestrator._normalise_time(value)
        for fmt in ("%H:%M", "%I:%M %p", "%I %p"):
            try:
                return datetime.strptime(normalised, fmt).strftime("%-I:%M %p")
            except ValueError:
                continue
        return value

    @staticmethod
    def _split_name(value: str) -> tuple[str, str]:
        parts = value.split(maxsplit=1)
        return (parts[0], parts[1] if len(parts) > 1 else "") if parts else ("", "")

    @staticmethod
    def _missing_customer_field(error: str) -> str:
        lowered = error.lower()
        if "customer.lastname" in lowered or "last name" in lowered:
            return "last_name"
        if "customer.firstname" in lowered or "first name" in lowered:
            return "first_name"
        if "customer.phone" in lowered or "phone" in lowered:
            return "phone"
        return ""

    @staticmethod
    def _phone_suffix(value: str) -> str:
        digits = re.sub(r"\D", "", value)
        return digits[-4:] if len(digits) >= 4 else ""

    @staticmethod
    def _international_phone(value: str) -> str:
        stripped = value.strip()
        if stripped.startswith("+"):
            return "+" + re.sub(r"\D", "", stripped)
        digits = re.sub(r"\D", "", stripped)
        if len(digits) == 10:
            return f"+91{digits}"
        return f"+{digits}" if digits else ""

    @staticmethod
    def _redact_name(value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        first = value.split()[0]
        return f"{first[:1]}***"
