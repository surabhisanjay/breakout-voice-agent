from __future__ import annotations

import logging
import os
from typing import Any

from ..config.env_loader import booking_credentials_present, load_project_env
from ..integrations.kreeda.breakout_api import BreakoutAPI


logger = logging.getLogger(__name__)

_FALLBACK_POLICY = {
    "cancellationPolicy": (
        "Cancellation charges are 0% with at least 3 days' notice, 25% with less than 3 days, "
        "50% with less than 2 days, and 75% with less than 1 day. Cancellations less than "
        "2 hours before the slot and no-shows are not refundable."
    ),
    "reschedulePolicy": (
        "Rescheduling charges are 0% with at least 2 days' notice, 25% with less than 2 days, "
        "50% with less than 1 day, and 75% with less than 2 hours' notice."
    ),
}


def get_venue_policy(location: str, client: BreakoutAPI | None = None) -> dict[str, Any]:
    """Fetch the venue policy from Kreeda, with the verified local policy as fallback."""
    load_project_env()
    if not location.strip():
        return {**_FALLBACK_POLICY, "venueName": "", "source": "verified_local_policy"}
    if client is None and not booking_credentials_present():
        return {**_FALLBACK_POLICY, "venueName": location, "source": "verified_local_policy"}

    try:
        api = client or BreakoutAPI(timeout=_policy_timeout())
        venues = api.get_booking_venues()
        venue = _match_venue(venues, location)
        venue_id = _record_value(venue, ("venueId", "locationId", "id", "_id")) if venue else ""
        if not venue_id:
            raise LookupError(f"No Kreeda venue matched {location!r}")
        result = api.call_agent_tool("get_venue_policy", {"venueId": venue_id})
        if not result.get("cancellationPolicy") and not result.get("reschedulePolicy"):
            raise ValueError("Kreeda returned an empty venue policy")
        logger.info("KREEDA_VENUE_POLICY_SUCCESS venueId=%s location=%s", venue_id, location)
        return {
            **result,
            "venueId": venue_id,
            "venueName": location,
            "source": "kreeda",
        }
    except Exception as exc:
        logger.warning(
            "KREEDA_VENUE_POLICY_FAILURE location=%s reason=%s:%s using_verified_local_policy=true",
            location,
            type(exc).__name__,
            exc,
        )
        return {**_FALLBACK_POLICY, "venueName": location, "source": "verified_local_policy"}


def venue_policy_text(location: str, *, include_reschedule: bool = False) -> str:
    policy = get_venue_policy(location)
    cancellation = str(policy.get("cancellationPolicy") or "").strip()
    reschedule = str(policy.get("reschedulePolicy") or "").strip()
    if include_reschedule and reschedule:
        return f"{cancellation} Rescheduling: {reschedule}".strip()
    return cancellation or reschedule


def _match_venue(venues: list[dict[str, Any]], location: str) -> dict[str, Any] | None:
    wanted = location.casefold().strip()
    for venue in venues:
        candidate = _record_value(venue, ("venueName", "locationName", "name", "title")).casefold().strip()
        if candidate and (candidate == wanted or wanted in candidate or candidate in wanted):
            return venue
    return None


def _record_value(record: dict[str, Any] | None, keys: tuple[str, ...]) -> str:
    if not record:
        return ""
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _policy_timeout() -> float:
    try:
        return max(float(os.environ.get("BOOKING_HTTP_TIMEOUT_SECONDS", "10")), 1.0)
    except ValueError:
        return 10.0
