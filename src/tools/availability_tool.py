"""
AvailabilityTool — checks slot availability for a given location / date.

STUB IMPLEMENTATION
-------------------
Returns deterministic mock data so the agent can demonstrate the full
booking flow end-to-end without a live API.

TO INTEGRATE WITH BREAKOUT'S BOOKING SYSTEM
--------------------------------------------
Replace the body of `check()` with an HTTP call to the booking API,
e.g.:
    import httpx
    response = httpx.get(
        "https://api.breakout.in/v1/availability",
        params={"location": location, "date": date, "participants": participants},
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=10,
    )
    return response.json()

No other files need to change — the interface is fixed.
"""
from __future__ import annotations


class AvailabilityTool:
    """
    Check slot availability for a given Breakout Escape Rooms location and date.

    Returns
    -------
    dict with keys:
        available   : bool   — True if any slots exist
        slots       : list   — ["10:00 AM", "12:00 PM", ...] (empty if unavailable)
        location    : str
        date        : str
        participants: int
    """

    # Stub slot data — keyed by (location.lower(), date.lower())
    _STUB_SLOTS: dict[tuple[str, str], list[str]] = {
        ("koramangala", "18 june"): ["10:00 AM", "12:00 PM", "3:00 PM", "6:00 PM"],
        ("whitefield", "18 june"): ["11:00 AM", "2:00 PM", "5:00 PM"],
        ("jp nagar", "18 june"): ["10:00 AM", "1:00 PM"],
    }

    _DEFAULT_SLOTS = ["10:00 AM", "12:00 PM", "3:00 PM"]

    def check(self, location: str, date: str, participants: int) -> dict:
        """
        Check availability for the given location and date.

        Parameters
        ----------
        location    : canonical location string, e.g. "Koramangala"
        date        : date string as captured by memory, e.g. "18 June"
        participants: integer head-count

        Returns
        -------
        Availability dict (see module docstring).
        """
        key = (location.strip().lower(), date.strip().lower())
        slots = self._STUB_SLOTS.get(key, self._DEFAULT_SLOTS)

        return {
            "available": len(slots) > 0,
            "slots": slots,
            "location": location,
            "date": date,
            "participants": participants,
        }
