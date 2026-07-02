from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..orchestration.booking_orchestrator import BookingOrchestrator


@dataclass
class FollowUpResult:
    action: str
    message: str = ""
    booking_status: str = ""
    payment_status: str = ""
    should_send: bool = False
    payment_url: str = ""
    minutes_remaining: int | None = None
    provider_result: dict[str, Any] = field(default_factory=dict)


class FollowUpAgent:
    """
    Deterministic scheduled follow-up helper.

    This is intentionally not part of dispatch() routing. It reads booking
    state, asks Kreeda for payment truth, and returns a suggested reminder or
    terminal payment-state action.
    """

    def __init__(self, orchestrator: BookingOrchestrator | None = None) -> None:
        self.orchestrator = orchestrator or BookingOrchestrator()

    def evaluate(self, memory: dict[str, Any], *, now: datetime | None = None) -> FollowUpResult:
        now = now or datetime.now(timezone.utc)
        booking_id = str(memory.get("booking_id") or memory.get("bookingId") or "").strip()
        venue_id = str(memory.get("venueId") or memory.get("venue_id") or "").strip()
        booking_status = str(memory.get("bookingStatus") or memory.get("booking_status") or "").upper()
        payment_status = str(memory.get("paymentStatus") or memory.get("payment_status") or "").upper()
        if not booking_id or booking_status not in {"RESERVED", "PAYMENT_PENDING", "PENDING"}:
            return FollowUpResult(action="none", booking_status=booking_status, payment_status=payment_status)

        provider_result: dict[str, Any] = {}
        if venue_id:
            provider_result = self.orchestrator.check_payment_status(venue_id, booking_id)
            provider_status = str(provider_result.get("status") or "").upper()
            is_paid = bool(provider_result.get("isPaid")) or provider_status == "CONFIRMED"
            if is_paid:
                memory["bookingStatus"] = memory["booking_status"] = "CONFIRMED"
                memory["paymentStatus"] = memory["payment_status"] = "PAID"
                return FollowUpResult(
                    action="stop",
                    message="Payment is complete. No reminder needed.",
                    booking_status="CONFIRMED",
                    payment_status="PAID",
                    should_send=False,
                    provider_result=provider_result,
                )
            if provider_status in {"EXPIRED", "CANCELLED", "RELEASED"}:
                return self._mark_expired(memory, provider_result)

        deadline = self._parse_deadline(
            str(
                provider_result.get("paymentDeadline")
                or memory.get("paymentDeadline")
                or memory.get("payment_deadline")
                or ""
            )
        )
        if deadline and now >= deadline:
            if venue_id:
                provider_result = self.orchestrator.check_payment_status(venue_id, booking_id)
                provider_status = str(provider_result.get("status") or "").upper()
                if bool(provider_result.get("isPaid")) or provider_status == "CONFIRMED":
                    memory["bookingStatus"] = memory["booking_status"] = "CONFIRMED"
                    memory["paymentStatus"] = memory["payment_status"] = "PAID"
                    return FollowUpResult(
                        action="stop",
                        message="Payment is complete. No reminder needed.",
                        booking_status="CONFIRMED",
                        payment_status="PAID",
                        provider_result=provider_result,
                    )
            return self._mark_expired(memory, provider_result)

        payment_url = str(memory.get("paymentUrl") or memory.get("payment_url") or memory.get("payment_link") or "").strip()
        minutes_remaining = self._minutes_remaining(deadline, now) if deadline else None
        remaining_text = (
            f"about {minutes_remaining} minutes"
            if minutes_remaining is not None
            else "a limited time"
        )
        message = (
            f"Reminder: your Breakout booking {booking_id} is reserved and payment is still pending. "
            f"Please complete payment within {remaining_text}: {payment_url}"
        )
        return FollowUpResult(
            action="remind",
            message=message,
            booking_status="RESERVED",
            payment_status="UNPAID",
            should_send=bool(payment_url),
            payment_url=payment_url,
            minutes_remaining=minutes_remaining,
            provider_result=provider_result,
        )

    @staticmethod
    def _mark_expired(memory: dict[str, Any], provider_result: dict[str, Any]) -> FollowUpResult:
        memory["bookingStatus"] = memory["booking_status"] = "EXPIRED"
        memory["paymentStatus"] = memory["payment_status"] = "EXPIRED"
        memory["selected_slot"] = ""
        return FollowUpResult(
            action="expired",
            message=(
                "The payment window has expired, so your reservation has been released automatically. "
                "I can check the latest availability and reserve another slot if you'd like."
            ),
            booking_status="EXPIRED",
            payment_status="EXPIRED",
            should_send=True,
            provider_result=provider_result,
        )

    @staticmethod
    def _parse_deadline(value: str) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _minutes_remaining(deadline: datetime | None, now: datetime) -> int | None:
        if deadline is None:
            return None
        return max(int((deadline - now).total_seconds() // 60), 0)
