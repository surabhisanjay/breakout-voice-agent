from __future__ import annotations

import re


CANCELLATION_POLICY_RESPONSE = (
    "Here is the exact cancellation policy. "
    "Cancel 3 days or more before the slot for a full refund. "
    "Cancel 2 to under 3 days before for a 75% refund, 1 to under 2 days before for a 50% refund, "
    "and 2 hours to under 1 day before for a 25% refund. "
    "Cancellations under 2 hours before the slot and no-shows are not refundable."
)


def is_cancellation_policy_question(message: str) -> bool:
    """Distinguish policy/refund questions from requests to cancel a booking."""
    lowered = re.sub(r"\s+", " ", message.lower()).strip()
    if not re.search(r"\b(?:cancel(?:lation)?|refund)\b", lowered):
        return False
    if "cancellation" in lowered:
        return True

    policy_signals = (
        "policy",
        "charges",
        "charge",
        "fee",
        "fees",
        "breakdown",
        "percentage",
        "percent",
        "how much refund",
        "refund amount",
        "refund will i get",
        "refundable",
        "explain",
        "tell me",
        "what if",
        "if i cancel",
        "if we cancel",
        "how does cancellation",
        "can bookings be cancelled",
    )
    return any(signal in lowered for signal in policy_signals)
