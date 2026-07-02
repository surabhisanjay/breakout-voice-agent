from __future__ import annotations

from typing import Any


def build_wati_booking_payload(memory: dict[str, Any], booking_result: dict[str, Any]) -> dict[str, Any]:
    booking_id = str(
        booking_result.get("booking_id")
        or booking_result.get("booking_reference")
        or memory.get("booking_ref")
        or memory.get("booking_id")
        or ""
    )
    payment_link = str(
        booking_result.get("payment_url")
        or booking_result.get("paymentUrl")
        or memory.get("payment_url")
        or memory.get("paymentUrl")
        or memory.get("payment_link")
    )
    status = str(booking_result.get("status") or "").lower()
    if not status:
        status = "confirmed" if booking_result.get("confirmed") else "prepared"
    return {
        "customer_name": str(memory.get("customer_name") or booking_result.get("customer_name") or ""),
        "phone": str(memory.get("phone") or booking_result.get("phone") or ""),
        "booking_id": booking_id,
        "room": str(memory.get("room") or memory.get("recommended_option") or booking_result.get("room") or ""),
        "location": str(memory.get("location") or booking_result.get("location") or ""),
        "date": str(memory.get("preferred_date") or booking_result.get("date") or ""),
        "time": str(memory.get("selected_slot") or booking_result.get("slot") or ""),
        "payment_link": payment_link,
        "payment_expiry": "15 minutes",
        "payment_expiry_minutes": 15,
        "booking_status": status,
        "send": False,
        "provider": "wati",
    }
