from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteDecision:
    next_agent: str
    reason: str
    should_handoff: bool


class Router:
    ROUTES = {
        "birthday_party": ("birthday_booking_agent", "Birthday planning belongs to the birthday specialist."),
        "bachelor_party": ("booking_agent", "Bachelor party details should move to booking/event handling."),
        "farewell_party": ("booking_agent", "Farewell event details should move to event booking."),
        "couple_event": ("booking_agent", "Couple event booking should be handled by booking."),
        "corporate_event": ("corporate_events_agent", "Corporate planning belongs to the corporate events specialist."),
        "virtual_event": ("virtual_events_agent", "Virtual event setup belongs to the virtual events specialist."),
        "cancellation_request": ("refunds_or_cancellations_agent", "Cancellation and refund requests need policy handling."),
        "escape_room_inquiry": ("booking_agent", "Escape room selection and availability should move to booking."),
        "general_faq": ("inbound_agent", "The inbound agent can continue answering basic questions."),
    }

    def route(self, intent: str, handoff_ready: bool) -> RouteDecision:
        next_agent, reason = self.ROUTES.get(intent, self.ROUTES["general_faq"])
        if intent == "general_faq":
            return RouteDecision(next_agent, reason, False)

        if not handoff_ready:
            return RouteDecision(
                "qualification_agent",
                f"Qualify missing customer context before routing to {next_agent}.",
                False,
            )

        if handoff_ready:
            return RouteDecision(next_agent, reason, True)
