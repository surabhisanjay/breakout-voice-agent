from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from .integrations.breakout_api import BreakoutAPI, BreakoutAPIError
from .tools.availability_tool import AvailabilityTool
from .tools.booking_tool import BookingTool


class BookingProvider(ABC):
    @abstractmethod
    def check_availability(self, location: str, date: str, participants: int, room: str = "") -> dict:
        raise NotImplementedError

    @abstractmethod
    def prepare_booking(self, memory: dict, chosen_slot: str) -> dict:
        raise NotImplementedError


class SimulatorProvider(BookingProvider):
    def __init__(self) -> None:
        self.availability_tool = AvailabilityTool()
        self.booking_tool = BookingTool()

    def check_availability(self, location: str, date: str, participants: int, room: str = "") -> dict:
        return self.availability_tool.check(location, date, participants)

    def prepare_booking(self, memory: dict, chosen_slot: str) -> dict:
        return self.booking_tool.create(memory, chosen_slot)


class BreakoutAPIProvider(BookingProvider):
    def __init__(self, client: BreakoutAPI | None = None) -> None:
        self.client = client or BreakoutAPI()
        self._slot_lookup: dict[str, dict[str, Any]] = {}
        self._location: dict[str, Any] = {}
        self._game: dict[str, Any] = {}

    def get_locations(self) -> list[dict[str, Any]]:
        return self.client.get_locations()

    def get_games(self, location_id: str) -> list[dict[str, Any]]:
        return self.client.get_games(location_id)

    def get_available_slots(
        self,
        location_id: str,
        game_ids: list[str] | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.client.get_available_slots(location_id, game_ids, start_date, end_date)

    def prepare_booking(self, memory: dict, chosen_slot: str) -> dict:
        slot = self._slot_lookup.get(self._normalise_time(chosen_slot))
        if not slot:
            raise BreakoutAPIError("The selected slot is no longer available.", code="NOT_FOUND")

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
        result = self.client.prepare_booking(payload)
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

    def check_availability(self, location: str, date: str, participants: int, room: str = "") -> dict:
        locations = self.get_locations()
        location_record = self._match_record(locations, "locationName", location)
        if not location_record:
            return {"available": False, "slots": [], "location": location, "date": date, "participants": participants}

        games = self.get_games(location_record["locationId"])
        game_record = self._match_record(games, "gameName", room) if room else (games[0] if games else None)
        if not game_record:
            return {"available": False, "slots": [], "location": location, "date": date, "participants": participants}

        api_date = self._normalise_date(date)
        slots = self.get_available_slots(location_record["locationId"], [game_record["gameId"]], api_date, api_date)
        available_slots = [slot for slot in slots if slot.get("isAvailable")]
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


class ProviderAvailabilityAdapter:
    def __init__(self, provider: BookingProvider, memory: dict) -> None:
        self.provider = provider
        self.memory = memory

    def check(self, location: str, date: str, participants: int) -> dict:
        room = str(self.memory.get("room") or self.memory.get("recommended_option") or "")
        return self.provider.check_availability(location, date, participants, room)


class ProviderBookingAdapter:
    def __init__(self, provider: BookingProvider) -> None:
        self.provider = provider

    def create(self, memory: dict, chosen_slot: str) -> dict:
        return self.provider.prepare_booking(memory, chosen_slot)


def build_booking_provider() -> BookingProvider:
    if os.environ.get("BOOKING_API_KEY") and os.environ.get("BOOKING_BASE_URL"):
        return BreakoutAPIProvider()
    return SimulatorProvider()
