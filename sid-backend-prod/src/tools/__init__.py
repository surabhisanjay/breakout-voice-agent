"""
Breakout Agent — Tool Layer

Thin, swappable tool stubs.  Each tool exposes a single callable method.
Replace the stub body with real API calls when integrating with Breakout's
booking system — no other files need to change.
"""
from .availability_tool import AvailabilityTool
from .booking_tool import BookingTool

__all__ = ["AvailabilityTool", "BookingTool"]
