from __future__ import annotations

import os
from typing import Any, List, Optional
from .breakout_api import BreakoutAPI


class BreakoutBookingProvider:
    """
    Provider for Breakout Booking Integration API.
    Implements standard operations: locations, games, slots, prepare booking, release slots.
    """

    def __init__(self, client: BreakoutAPI | None = None) -> None:
        self.client = client or BreakoutAPI()

    @property
    def configured(self) -> bool:
        return self.client.configured

    def get_locations(self) -> List[Dict[str, Any]]:
        """Retrieve all locations."""
        return self.client.get_locations()

    def get_games(self, location_id: str) -> List[Dict[str, Any]]:
        """Retrieve games for a specific location."""
        return self.client.get_games(location_id)

    def get_slots(
        self,
        location_id: str,
        game_ids: List[str] | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve available slots."""
        return self.client.get_available_slots(
            location_id=location_id,
            game_ids=game_ids,
            start_date=start_date,
            end_date=end_date,
        )

    def prepare_booking(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Prepare a booking reservation."""
        return self.client.prepare_booking(payload)

    def release_slots(self, slot_ids: List[str]) -> dict[str, Any]:
        """Release previously locked slots."""
        return self.client.release_slots(slot_ids)

    def get_booking_venues(self) -> List[Dict[str, Any]]:
        return self.client.get_booking_venues()

    def get_booking_games(self, venue_id: str) -> List[Dict[str, Any]]:
        return self.client.get_booking_games(venue_id)

    def search_booking_slots(
        self,
        venue_id: str,
        game_id: str,
        start_date: str,
        end_date: str,
    ) -> List[Dict[str, Any]]:
        return self.client.search_booking_slots(venue_id, game_id, start_date, end_date)

    def create_instant_cart(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.client.create_instant_cart(payload)

    def create_confirmed_booking(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.client.create_confirmed_booking(payload)
