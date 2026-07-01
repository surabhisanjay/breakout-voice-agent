from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class WhatsAppIntent(str, Enum):
    NEW_BOOKING = "new_booking"
    PRICE_ENQUIRY = "price_enquiry"
    BIRTHDAY_CELEBRATION = "birthday_celebration"
    CORPORATE_OUTING = "corporate_team_outing"
    ROOM_ENQUIRY = "room_theme_enquiry"
    LOCATION_ENQUIRY = "location_enquiry"
    EXISTING_BOOKING = "existing_booking_question"
    LATE_ARRIVAL = "late_arrival_help"
    PAYMENT_CONFIRMATION = "payment_confirmation_question"
    GENERAL_FAQ = "general_faq"


@dataclass(frozen=True)
class WhatsAppInboundMessage:
    phone: str
    text: str
    message_id: str = ""
    contact_name: str = ""
    event_type: str = "message"


@dataclass(frozen=True)
class WatiDeliveryResult:
    attempted: bool
    sent: bool
    status_code: int = 0
    message_id: str = ""
    reason: str = ""
    response: dict[str, Any] | str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WhatsAppAgentResult:
    session_id: str
    intent: str
    agent_intent: str
    response: str
    duplicate: bool = False
    delivery: WatiDeliveryResult = field(
        default_factory=lambda: WatiDeliveryResult(attempted=False, sent=False)
    )
    state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["delivery"] = self.delivery.to_dict()
        return data
