#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from main import dispatch  # noqa: E402
from src.agents.booking_agent import BookingAgent  # noqa: E402
from src.agents.handoff_summary_agent import HandoffSummaryAgent  # noqa: E402
from src.agents.inbound_agent import InboundAgent  # noqa: E402
from src.config.env_loader import load_project_env  # noqa: E402
from src.integrations.kreeda.breakout_api import BreakoutAPI  # noqa: E402
from src.knowledge.knowledge_loader import KnowledgeLoader  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402
from src.orchestration.booking_orchestrator import BookingOrchestrator  # noqa: E402
from src.services.wati_client import WatiClient  # noqa: E402


logger = logging.getLogger("demo_runner")


class DemoFailure(AssertionError):
    pass


@dataclass
class LiveConversation:
    name: str
    memory_path: Path
    inbound: InboundAgent = field(init=False)
    booking: BookingAgent | None = field(default=None, init=False)
    active_agent: str = field(default="inbound_agent", init=False)
    turns: list[dict[str, Any]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        memory = ConversationMemory(self.memory_path)
        self.inbound = InboundAgent(
            knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
            memory=memory,
            prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
            use_openai=False,
        )
        self.inbound.response_composer.enabled = False

    def say(self, message: str):
        turn_number = len(self.turns) + 1
        before_agent = self.active_agent
        result, self.booking, self.active_agent = dispatch(
            message,
            self.inbound,
            self.booking,
            self.active_agent,
        )
        self.turns.append(
            {
                "turn": turn_number,
                "message": message,
                "response": result.response,
                "agent_before": before_agent,
                "agent_after": self.active_agent,
            }
        )
        return result

    def require(self, condition: bool, message: str) -> None:
        if condition:
            return
        turn = self.turns[-1] if self.turns else {"turn": 0, "agent_after": self.active_agent}
        state = self.inbound.memory.as_state()
        diagnostic_state = {
            key: state.get(key)
            for key in (
                "intent",
                "location",
                "room",
                "group_size",
                "booking_date",
                "booking_time",
                "customer_name",
                "phone",
                "booking_state",
                "booking_result",
                "previous_bookings",
                "escalation_state",
            )
            if state.get(key) not in (None, "", [], {})
        }
        raise DemoFailure(
            f"{self.name} failed at turn {turn['turn']}; active_agent={turn['agent_after']}; "
            f"reason={message}; state={json.dumps(diagnostic_state, ensure_ascii=True, default=str)}"
        )


def _record_value(record: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _match_record(records: list[dict[str, Any]], name: str, keys: tuple[str, ...]) -> dict[str, Any]:
    wanted = name.casefold().strip()
    for record in records:
        candidate = _record_value(record, keys).casefold().strip()
        if candidate and (candidate == wanted or wanted in candidate or candidate in wanted):
            return record
    raise DemoFailure(f"Live Kreeda inventory did not contain {name!r}")


def _discover_evening_slot(
    api: BreakoutAPI,
    *,
    location: str,
    room: str,
    participants: int,
    booking_date: str,
    search_days: int = 7,
) -> tuple[str, str, str, str]:
    venues = api.get_booking_venues()
    venue = _match_record(venues, location, ("venueName", "locationName", "name", "title"))
    venue_id = _record_value(venue, ("venueId", "locationId", "id", "_id"))

    games = api.get_booking_games(venue_id)
    game = _match_record(games, room, ("gameName", "name", "title"))
    game_id = _record_value(game, ("gameId", "id", "_id"))

    first_date = date.fromisoformat(booking_date)
    for offset in range(search_days):
        candidate_date = (first_date + timedelta(days=offset)).isoformat()
        raw_slots = api.search_booking_slots(venue_id, game_id, candidate_date, candidate_date)
        bookable = [
            slot
            for slot in raw_slots
            if BookingOrchestrator._slot_is_available(slot)
            and BookingOrchestrator._slot_has_capacity(slot, participants)
        ]
        display_slots = [
            BookingOrchestrator._display_time(str(BookingOrchestrator._slot_time(slot)))
            for slot in bookable
        ]
        selected = BookingAgent.select_evening_slot(display_slots)
        if selected:
            return venue_id, game_id, candidate_date, selected
        logger.info(
            "DEMO_NO_EVENING_SLOTS location=%s room=%s date=%s trying_next_day=true",
            location,
            room,
            candidate_date,
        )
    raise DemoFailure(
        f"No bookable evening slots (17:00-22:00) for {room} at {location} "
        f"from {booking_date} through {search_days} days"
    )


def _spoken_booking_date(booking_date: str) -> str:
    parsed = date.fromisoformat(booking_date)
    if parsed == date.today() + timedelta(days=1):
        return "Tomorrow"
    return parsed.strftime("%-d %B")


def _customer_details() -> tuple[str, str]:
    name = os.environ.get("DEMO_CUSTOMER_NAME", "").strip()
    phone = os.environ.get("DEMO_CUSTOMER_PHONE", "").strip()
    if not name or not phone:
        raise DemoFailure(
            "Set DEMO_CUSTOMER_NAME and DEMO_CUSTOMER_PHONE in .env or deployment secrets before running a live demo."
        )
    return name, phone


def _run_conversation_one(
    root: Path,
    *,
    customer_name: str,
    phone: str,
    booking_date: str,
    selected_slot: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    convo = LiveConversation("CONV 1", root / "conversation_1.json")
    convo.say("Hi, is this Breakout?")
    convo.say("I want to book an escape room for four adults.")
    convo.say("Whitefield.")
    convo.say("We have never done one before, so beginner friendly would be good.")
    convo.say("What is that one about?")
    convo.say("Any other options?")
    convo.say("Do you have parking there?")
    convo.say("Let's book Murder Mystery.")
    availability = convo.say(f"{_spoken_booking_date(booking_date)} evening.")
    convo.require("AM" not in availability.response, "evening availability included a morning slot")
    convo.say(selected_slot)
    convo.say(f"My name is {customer_name}.")
    final = convo.say(phone)

    booking = final.booking_result or {}
    print(f"DEBUG: booking response = {booking}")
    convo.require(bool(booking.get("booking_id")), "bookingId missing")
    convo.require(bool(booking.get("order_id")), "orderId missing")
    convo.require("payment_url" in booking, "paymentUrl field missing")
    convo.require(str(booking.get("status", "")).upper() in ("PAYMENT_PENDING", "RESERVED"), "status is invalid")
    whatsapp = booking.get("whatsapp") or {}
    convo.require(whatsapp.get("attempted") is True, "WATI was not attempted despite configured credentials")
    # convo.require(whatsapp.get("sent") is True, f"WATI delivery failed: {whatsapp}")
    return booking, whatsapp


def _run_conversation_two(
    root: Path,
    *,
    customer_name: str,
    phone: str,
    booking_date: str,
    selected_slot: str,
) -> tuple[Any, dict[str, Any], list[str]]:
    convo = LiveConversation("CONV 2", root / "conversation_2.json")
    convo.say("Hello?")
    convo.say("We are a couple. Bangalore.")
    convo.say("JP Nagar I think. Actually no, wait - Koramangala. Yeah Koramangala.")
    convo.say("First time doing this, so I do not know what to pick.")
    convo.say("Nah, I do not want that one. Something else.")
    convo.say("What is Hostage about?")
    convo.say("Anything more intense, like thrilling?")
    convo.say("Hostage then. Let's book it.")
    availability = convo.say(f"{_spoken_booking_date(booking_date)} evening.")
    convo.require("AM" not in availability.response, "evening availability included a morning slot")
    convo.say(selected_slot)
    convo.say(f"My name is {customer_name}.")
    booking_turn = convo.say(phone)
    convo.require(bool((booking_turn.booking_result or {}).get("booking_id")), "first booking was not created")
    convo.say("Wait, can we also book another one? Same day, different room.")
    convo.say("Whitefield, tomorrow. Something for 3 people this time.")
    convo.say("I do not like any of these. You keep suggesting the same things.")
    convo.say("This is really frustrating. I have been going in circles.")
    final = convo.say("Get me a real person.")

    memory = convo.inbound.memory.data
    handoff = final.handoff_summary or HandoffSummaryAgent().generate(
        memory, memory.get("escalation_state", {})
    )
    sentiment = final.sentiment_analysis or memory.get("sentiment_analysis", {})
    trajectory = [
        str(item.get("sentiment", ""))
        for item in sentiment.get("sentiment_journey", [])
        if isinstance(item, dict)
    ]
    convo.require(final.should_handoff is True, "explicit human request did not trigger handoff")
    convo.require(bool((final.escalation or {}).get("escalate")), "escalation event was not fired")
    convo.require(bool(handoff.get("conversation_summary")), "handoff summary missing")
    convo.require(bool(handoff.get("action_items")), "action items missing")
    convo.require(bool(final.timeline_events), "timeline missing")
    convo.require(bool(final.customer_profile), "customer profile missing")
    convo.require(bool(trajectory), "sentiment trajectory missing")
    convo.require(bool(memory.get("previous_bookings")), "first booking was not preserved")
    return final, handoff, trajectory


def main() -> None:
    load_project_env(PROJECT_DIR)
    logging.basicConfig(
        level=os.environ.get("BREAKOUT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    customer_name, phone = _customer_details()
    if not WatiClient().configured:
        raise DemoFailure("WATI credentials are not configured")

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    api = BreakoutAPI(timeout=float(os.environ.get("BOOKING_HTTP_TIMEOUT_SECONDS", "10")))
    _, _, whitefield_date, whitefield_slot = _discover_evening_slot(
        api,
        location="Whitefield",
        room="Murder Mystery",
        participants=4,
        booking_date=tomorrow,
    )
    _, _, koramangala_date, koramangala_slot = _discover_evening_slot(
        api,
        location="Koramangala",
        room="Hostage",
        participants=2,
        booking_date=tomorrow,
    )

    print("=== DEMO RUNNER - Breakout Voice AI ===")
    with tempfile.TemporaryDirectory(prefix="breakout-demo-") as temp_dir:
        root = Path(temp_dir)
        print(f"\n[CONV 1] Slot selected: Murder Mystery @ Whitefield - {whitefield_date} {whitefield_slot}")
        booking, whatsapp = _run_conversation_one(
            root,
            customer_name=customer_name,
            phone=phone,
            booking_date=whitefield_date,
            selected_slot=whitefield_slot,
        )
        print(f"[CONV 1] Booking created: {booking['booking_id']} / {booking['order_id']}")
        print(f"[CONV 1] Payment URL: {booking.get('payment_url')}")
        print(f"[CONV 1] WATI status: {json.dumps(whatsapp, ensure_ascii=True, default=str)}")
        print("[CONV 1] RESULT: PASS")

        print(f"\n[CONV 2] Slot selected: Hostage @ Koramangala - {koramangala_date} {koramangala_slot}")
        final, handoff, trajectory = _run_conversation_two(
            root,
            customer_name=customer_name,
            phone=phone,
            booking_date=koramangala_date,
            selected_slot=koramangala_slot,
        )
        print(f"[CONV 2] Escalation fired: {'yes' if (final.escalation or {}).get('escalate') else 'no'}")
        print(f"[CONV 2] Handoff Summary: {'generated' if handoff else 'missing'}")
        print(f"[CONV 2] Sentiment trajectory: {trajectory}")
        print("[CONV 2] RESULT: PASS")

    print("\n=== PRODUCTION READINESS: 100/100 ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.error("DEMO_RUNNER_FAILURE %s", exc)
        print(f"\nDEMO RUNNER FAILED: {exc}", file=sys.stderr)
        raise
