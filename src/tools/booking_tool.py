"""
BookingTool — creates a confirmed booking in the Breakout system.

STUB IMPLEMENTATION
-------------------
Returns a deterministic mock confirmation dict so the full booking
confirmation flow can be demonstrated end-to-end without a live API.

TO INTEGRATE WITH BREAKOUT'S BOOKING SYSTEM
--------------------------------------------
Replace the body of `create()` with an HTTP POST to the booking API:
    import httpx
    response = httpx.post(
        "https://api.breakout.in/v1/bookings",
        json=payload,
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=10,
    )
    return response.json()

No other files need to change — the interface is fixed.
"""
from __future__ import annotations

import uuid


class BookingTool:
    """
    Create a confirmed booking in the Breakout Escape Rooms system.

    Returns
    -------
    dict with keys:
        booking_id  : str   — unique reference, e.g. "BRK-20260618-A3F"
        confirmed   : bool
        location    : str
        date        : str
        slot        : str   — chosen time slot, e.g. "3:00 PM"
        participants: int
        event_type  : str
        customer_name: str
        phone       : str
    """

    def create(self, memory: dict, chosen_slot: str) -> dict:
        """
        Create a booking from the current session memory.

        Parameters
        ----------
        memory      : full ConversationMemory.data dict
        chosen_slot : time slot chosen by the customer, e.g. "3:00 PM"

        Returns
        -------
        Booking confirmation dict (see module docstring).
        """
        booking_id = f"BRK-{uuid.uuid4().hex[:8].upper()}"

        return {
            "booking_id": booking_id,
            "confirmed": True,
            "location": memory.get("location", ""),
            "date": memory.get("preferred_date", ""),
            "slot": chosen_slot,
            "participants": memory.get("participants") or memory.get("company_size", ""),
            "event_type": memory.get("event_type", ""),
            "customer_name": memory.get("customer_name", ""),
            "phone": memory.get("phone", ""),
        }
