from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from typing import Any


def price_breakdown_from_totals(totals: Any) -> dict[str, Any]:
    """Normalize Kreeda payment totals for frontend price cards."""
    if not isinstance(totals, dict):
        return {}

    try:
        subtotal = float(totals.get("subtotal"))
        total = float(totals.get("total"))
    except (TypeError, ValueError):
        return {}

    if subtotal < 0 or total < 0:
        return {}

    discount = max(round(subtotal - total, 2), 0.0)
    discount_percent = round((discount / subtotal) * 100, 2) if subtotal else 0.0

    def clean(value: float) -> int | float:
        return int(value) if value.is_integer() else value

    return {
        "base_price": clean(subtotal),
        "discount": clean(discount),
        "discount_applied": discount > 0,
        "discount_percent": discount_percent,
        "final_price": clean(total),
        "currency": str(totals.get("currency") or "INR").upper(),
        "source": "kreeda_payment_status",
    }


def estimated_price_breakdown(
    participants: Any,
    preferred_date: str,
    room: str = "",
) -> dict[str, Any]:
    """Compute a conservative estimate from approved knowledge-base price bands."""
    try:
        count = int(participants)
    except (TypeError, ValueError):
        return {}
    if count <= 0:
        return {}

    date_type = _date_type(preferred_date)
    if not date_type:
        return {}

    per_person = _per_person_price(count, room, date_type)
    if per_person <= 0:
        return {}

    base_price = per_person * count
    discount_percent = 10.0 if count >= 4 else 0.0
    discount = round(base_price * discount_percent / 100, 2)
    final_price = round(base_price - discount, 2)

    def clean(value: float) -> int | float:
        return int(value) if float(value).is_integer() else value

    return {
        "base_price": clean(float(base_price)),
        "discount": clean(float(discount)),
        "discount_applied": discount > 0,
        "discount_percent": discount_percent,
        "final_price": clean(float(final_price)),
        "currency": "INR",
        "source": "knowledge_base_estimate",
        "date_type": date_type,
    }


def _per_person_price(count: int, room: str, date_type: str) -> int:
    room_key = room.strip().lower()
    premium_rooms = {
        "curse of the pharaoh",
        "prison break",
        "the wizarding championship",
        "forbidden forest",
        "the forbidden forest",
    }
    premium = room_key in premium_rooms
    weekend = date_type == "weekend"

    if premium:
        if count <= 5:
            return 1000 if weekend else 900
        return 900 if weekend else 800

    if count <= 3:
        return 900 if weekend else 800
    return 800 if weekend else 700


def _date_type(value: str) -> str:
    lowered = str(value or "").strip().lower()
    if not lowered:
        return ""
    if "weekend" in lowered or "saturday" in lowered or "sunday" in lowered:
        return "weekend"
    if any(day in lowered for day in ("monday", "tuesday", "wednesday", "thursday", "friday")):
        return "weekday"
    if lowered in {"today", "tomorrow"}:
        target = date.today() + (timedelta(days=1) if lowered == "tomorrow" else timedelta(days=0))
        return "weekend" if target.weekday() >= 5 else "weekday"
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y"):
        try:
            parsed = datetime.strptime(lowered, fmt).date()
            return "weekend" if parsed.weekday() >= 5 else "weekday"
        except ValueError:
            pass
    cleaned = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", lowered)
    for fmt in ("%d %B", "%d %b", "%B %d", "%b %d"):
        try:
            parsed_dt = datetime.strptime(cleaned, fmt)
            parsed = parsed_dt.replace(year=date.today().year).date()
            return "weekend" if parsed.weekday() >= 5 else "weekday"
        except ValueError:
            pass
    return ""
