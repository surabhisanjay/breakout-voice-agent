#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from main import _enrich_conversation_result, _observe_customer_sentiment, dispatch  # noqa: E402
from src.agents.booking_agent import BookingAgent  # noqa: E402
from src.agents.handoff_summary_agent import HandoffSummaryAgent  # noqa: E402
from src.agents.inbound_agent import InboundAgent  # noqa: E402
from src.config.env_loader import load_project_env  # noqa: E402
from src.integrations.kreeda.breakout_api import BreakoutAPI  # noqa: E402
from src.knowledge.knowledge_loader import KnowledgeLoader  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402
from src.orchestration.booking_orchestrator import BookingOrchestrator  # noqa: E402


OUTPUT_DIR = PROJECT_DIR / "scratch" / "final_consolidated_live_validation"
CUSTOMER_NAME = "Siddharth Khandelwal"
CUSTOMER_PHONE = "9982151357"
SLOT_RE = re.compile(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)\b", re.IGNORECASE)


@dataclass
class LiveSession:
    name: str
    memory_path: Path
    inbound: InboundAgent = field(init=False)
    booking: BookingAgent | None = None
    active_agent: str = "inbound_agent"
    turns: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        memory = ConversationMemory(self.memory_path)
        self.inbound = InboundAgent(
            knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
            memory=memory,
            prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
            use_openai=False,
        )
        self.inbound.response_composer.enabled = False

    @property
    def memory(self) -> dict[str, Any]:
        return self.inbound.memory.data

    def say(self, message: str) -> Any:
        before = self._core_state()
        started = time.perf_counter()
        result, self.booking, self.active_agent = dispatch(
            message,
            self.inbound,
            self.booking,
            self.active_agent,
        )
        elapsed = round(time.perf_counter() - started, 3)
        after = self._core_state()
        self.turns.append(
            {
                "turn": len(self.turns) + 1,
                "customer": message,
                "assistant": result.response,
                "active_agent": self.active_agent,
                "next_agent": result.next_agent,
                "should_handoff": result.should_handoff,
                "before": before,
                "after": after,
                "booking_result": result.booking_result or result.booking or {},
                "escalation": result.escalation or {},
                "handoff_summary": result.handoff_summary or {},
                "sentiment_analysis": result.sentiment_analysis or {},
                "latency_seconds": elapsed,
            }
        )
        return result

    def _core_state(self) -> dict[str, Any]:
        fields = (
            "intent",
            "location",
            "room",
            "recommended_option",
            "participants",
            "age_group",
            "experience_level",
            "preferred_date",
            "preferred_time",
            "preferred_period",
            "time_preference",
            "selected_slot",
            "customer_name",
            "phone",
            "booking_id",
            "booking_ref",
            "order_id",
            "payment_url",
            "price_breakdown",
            "current_workflow",
            "escalation_state",
        )
        return {field: self.memory.get(field) for field in fields if self.memory.get(field) not in ("", None, [], {})}

    def slots_from_last_response(self) -> list[str]:
        if not self.turns:
            return []
        return [slot.upper() for slot in SLOT_RE.findall(self.turns[-1]["assistant"])]

    def latest_booking(self) -> dict[str, Any]:
        for turn in reversed(self.turns):
            booking = turn.get("booking_result")
            if isinstance(booking, dict) and booking.get("booking_id"):
                return booking
        completed = self.memory.get("completed_booking")
        return completed if isinstance(completed, dict) else {}

    def has_booking_artifacts(self) -> bool:
        return bool(
            self.memory.get("booking_id")
            or self.memory.get("payment_url")
            or self.memory.get("paymentUrl")
            or self.memory.get("payment_link")
        )


def _choose_second(slots: list[str]) -> str:
    unique = []
    for slot in slots:
        if slot not in unique:
            unique.append(slot)
    if len(unique) < 2:
        raise RuntimeError(f"Need at least two slots to change selection, got {unique}")
    return unique[1]


def _payment_url_reachable(url: str) -> dict[str, Any]:
    if not url:
        return {"reachable": False, "status_code": None, "reason": "missing_url"}
    try:
        response = requests.get(url, timeout=12, allow_redirects=True)
        return {
            "reachable": 200 <= response.status_code < 400,
            "status_code": response.status_code,
            "final_url": response.url,
            "reason": "",
        }
    except Exception as exc:
        return {"reachable": False, "status_code": None, "reason": f"{type(exc).__name__}: {exc}"}


def _spoken_price_text(turns: list[dict[str, Any]]) -> str:
    for turn in reversed(turns):
        text = str(turn.get("assistant") or "")
        if re.search(r"\b(?:total price|price|inr|₹|rs\.?)\b", text, re.IGNORECASE):
            return text
    return ""


def _run_scenario_1(root: Path) -> dict[str, Any]:
    session = LiveSession("scenario_1", root / "scenario_1.json")
    session.say("We are four adults and Whitefield works for us tomorrow.")
    session.say("Actually change the location to Koramangala.")
    session.say("Let's do Hostage.")
    session.say("Actually make it two players.")
    session.say("Do you have wheelchair accessible rooms?")
    session.say("Can we get a group photo after the game?")
    session.say("What if someone needs the restroom mid-game?")
    if (session.memory.get("escalation_state") or {}).get("escalate"):
        return {
            "session": session,
            "availability_turn": "",
            "first_slot": "",
            "changed_slot": "",
            "booking": {},
            "whatsapp": {},
            "payment_reachability": {"reachable": False, "reason": "scenario_escalated_before_booking"},
            "spoken_price": _spoken_price_text(session.turns),
            "price_breakdown": session.memory.get("price_breakdown") or {},
            "failure": "novel_interruption_triggered_escalation_before_booking",
        }
    session.say("Switch the room to Murder Mystery instead.")
    session.say("Actually make it four players again.")
    availability = session.say("Tomorrow afternoon, around 2:30 if possible.")
    slots = session.slots_from_last_response()
    if not slots:
        availability = session.say("Please show the available afternoon slots.")
        slots = session.slots_from_last_response()
    if not slots:
        return {
            "session": session,
            "availability_turn": availability.response,
            "first_slot": "",
            "changed_slot": "",
            "booking": {},
            "whatsapp": {},
            "payment_reachability": {"reachable": False, "reason": "no_slots_surfaced"},
            "spoken_price": _spoken_price_text(session.turns),
            "price_breakdown": session.memory.get("price_breakdown") or {},
            "failure": "no_slots_surfaced_before_booking",
        }
    first_slot = slots[0]
    session.say(first_slot)
    changed_slot = _choose_second(slots)
    session.say(f"Actually change the slot to {changed_slot}.")
    session.say(f"My name is {CUSTOMER_NAME}.")
    final = session.say(CUSTOMER_PHONE)
    booking = final.booking_result or final.booking or session.latest_booking()
    payment_url = str(booking.get("payment_url") or session.memory.get("paymentUrl") or session.memory.get("payment_url") or "")
    payment_reachability = _payment_url_reachable(payment_url)
    whatsapp = booking.get("whatsapp") or session.memory.get("whatsapp_delivery") or {}
    price = session.memory.get("price_breakdown") or booking.get("price_breakdown") or {}
    return {
        "session": session,
        "availability_turn": availability.response,
        "first_slot": first_slot,
        "changed_slot": changed_slot,
        "booking": booking,
        "whatsapp": whatsapp,
        "payment_reachability": payment_reachability,
        "spoken_price": _spoken_price_text(session.turns),
        "price_breakdown": price,
    }


def _run_scenario_2(root: Path) -> dict[str, Any]:
    session = LiveSession("scenario_2", root / "scenario_2.json")
    session.say("Do you have parking?")
    session.say("What is the cancellation policy?")
    session.say("Can we ask for hints inside?")
    session.say("I want to book Murder Mystery at Whitefield tomorrow afternoon for four adults.")
    session.say("Is there food nearby or snacks available?")
    session.say("Let's take 1:50 PM.")
    result = session.say("My name is Siddharth Khandelwal.")
    return {"session": session, "final": result}


def _run_scenario_3(root: Path) -> dict[str, Any]:
    session = LiveSession("scenario_3", root / "scenario_3.json")
    session.say("I need a room tomorrow around 1:30.")
    session.say("Two of us. What room do you recommend?")
    session.say("Whitefield works.")
    session.say("Actually change location to Koramangala.")
    session.say("Let's do Hostage instead.")
    availability = session.say("Check slots around 1:30.")
    session.say("Let's take 1:10 PM.")
    result = session.say("My name is Siddharth Khandelwal.")
    return {"session": session, "availability_turn": availability.response, "final": result}


def _run_scenario_4(root: Path) -> dict[str, Any]:
    session = LiveSession("scenario_4", root / "scenario_4.json")
    session.say("I've been trying to change my booking and this is getting frustrating.")
    session.say("You keep repeating yourself.")
    trigger = session.say("I want a human.")
    session.say("Finally. This has been useless.")
    post_direct = session.say("Can you at least tell me if Koramangala has evening slots tomorrow?")
    session.say("And do you have parking there?")
    state = session.memory.get("escalation_state") or {}
    handoff = trigger.handoff_summary or HandoffSummaryAgent().generate(session.memory, state)
    sentiment = trigger.sentiment_analysis or session.memory.get("sentiment_analysis") or {}
    return {
        "session": session,
        "trigger": trigger,
        "post_direct": post_direct,
        "handoff": handoff,
        "sentiment": sentiment,
    }


def _run_scenario_5(root: Path) -> dict[str, Any]:
    session = LiveSession("scenario_5", root / "scenario_5.json")
    session.say("No actually... wait.")
    session.say("What if children can play?")
    session.say("Actually let's do Whitefield.")
    session.say("No Koramangala.")
    session.say("Actually back to Whitefield.")
    session.say("We'll be six.")
    session.say("Actually make it two.")
    session.say("No wait, make it four adults.")
    session.say("I'll come around afternoon tomorrow.")
    session.say("Can I bring cake?")
    session.say("Let's take 1:50 PM.")
    result = session.say("My name is Siddharth Khandelwal.")
    return {"session": session, "final": result}


def _cleanup_orphan() -> dict[str, Any]:
    api = BreakoutAPI(timeout=float(os.environ.get("BOOKING_HTTP_TIMEOUT_SECONDS", "10")))
    target = "bk_JUk7nv"
    status_results: list[dict[str, Any]] = []
    try:
        venues = api.get_booking_venues()
    except Exception as exc:
        return {"booking_id": target, "lookup_error": f"{type(exc).__name__}: {exc}", "resolved": False}

    for venue in venues:
        venue_id = str(venue.get("venueId") or venue.get("locationId") or venue.get("id") or venue.get("_id") or "")
        if not venue_id:
            continue
        try:
            status = api.check_payment_status(venue_id, target)
            if isinstance(status, dict):
                status_results.append({"venue_id": venue_id, "status": status})
        except Exception as exc:
            status_results.append({"venue_id": venue_id, "error": f"{type(exc).__name__}: {exc}"})

    usable = [
        item for item in status_results
        if isinstance(item.get("status"), dict) and item["status"].get("bookingId")
    ]
    resolution = {
        "booking_id": target,
        "lookups": status_results,
        "matched": usable[-1] if usable else {},
        "action": "none",
        "resolved": False,
    }
    if not usable:
        resolution["action"] = "no_matching_payment_status_found"
        return resolution

    matched = usable[-1]
    status = matched["status"]
    state = str(status.get("status") or "").upper()
    is_paid = bool(status.get("isPaid"))
    deadline_raw = str(status.get("paymentDeadline") or "")
    deadline_passed = False
    if deadline_raw:
        try:
            deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            deadline_passed = datetime.now(timezone.utc) >= deadline.astimezone(timezone.utc)
        except ValueError:
            pass
    resolution["deadline_passed"] = deadline_passed
    if is_paid or state in {"CONFIRMED", "PAID"}:
        resolution["action"] = "paid_or_confirmed_no_cancellation"
        resolution["resolved"] = True
        return resolution
    if state in {"EXPIRED", "CANCELLED", "CANCELED", "RELEASED"} or deadline_passed:
        resolution["action"] = "auto_expired_or_released_no_active_cancellation_needed"
        resolution["resolved"] = True
        return resolution
    try:
        cancel = BookingOrchestrator().cancel_booking(target, reason="cleanup_orphaned_validation_reservation")
        resolution["cancel_result"] = cancel
        resolution["action"] = "active_cancellation_attempted"
        resolution["resolved"] = str(cancel.get("status") or "").lower() in {"cancelled", "canceled"}
    except Exception as exc:
        resolution["cancel_error"] = f"{type(exc).__name__}: {exc}"
        resolution["action"] = "active_cancellation_failed"
    return resolution


def _no_booking_created(session: LiveSession) -> bool:
    return not session.has_booking_artifacts()


def _transcript(session: LiveSession) -> list[dict[str, str]]:
    return [
        {
            "turn": turn["turn"],
            "customer": turn["customer"],
            "assistant": turn["assistant"],
        }
        for turn in session.turns
    ]


def _interruption_pass(session: LiveSession, phrase: str) -> bool:
    target = phrase.lower()
    for turn in session.turns:
        if target in turn["customer"].lower():
            before = turn["before"]
            after = turn["after"]
            preserved = all(
                before.get(field) == after.get(field)
                for field in ("location", "room", "participants", "preferred_date", "preferred_time")
                if before.get(field) not in ("", None)
            )
            response = turn["assistant"].lower()
            escalated = (after.get("escalation_state") or {}).get("escalate")
            deflected = (
                "which location" in response
                or "which branch" in response
                or "what time works best" in response
                or "available slots" in response
                or "connect you with our team" in response
            )
            return preserved and not escalated and not deflected and len(response) > 10
    return False


def _scenario_summary(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    session: LiveSession = payload["session"]
    return {
        "name": name,
        "transcript": _transcript(session),
        "final_memory": session._core_state(),
        "booking_artifacts_present": session.has_booking_artifacts(),
    }


def main() -> int:
    load_project_env(PROJECT_DIR)
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["BREAKOUT_GPT_REASONER"] = "false"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    root = OUTPUT_DIR / f"run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    root.mkdir(parents=True, exist_ok=True)
    s1 = _run_scenario_1(root)
    s2 = _run_scenario_2(root)
    s3 = _run_scenario_3(root)
    s4 = _run_scenario_4(root)
    s5 = _run_scenario_5(root)
    cleanup = _cleanup_orphan()

    scenario_1_session: LiveSession = s1["session"]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "live",
        "scenario_1": {
            **_scenario_summary("scenario_1", s1),
            "fact_changes_isolated": (
                scenario_1_session.memory.get("location") == "Koramangala"
                and scenario_1_session.memory.get("room") == "Murder Mystery"
                and int(scenario_1_session.memory.get("participants") or 0) == 4
            ),
            "wheelchair_accessibility_interruption": _interruption_pass(
                scenario_1_session, "Do you have wheelchair accessible rooms?"
            ),
            "group_photo_interruption": _interruption_pass(
                scenario_1_session, "Can we get a group photo after the game?"
            ),
            "restroom_interruption": _interruption_pass(
                scenario_1_session, "What if someone needs the restroom mid-game?"
            ),
            "first_slot": s1["first_slot"],
            "changed_slot": s1["changed_slot"],
            "booking": s1["booking"],
            "whatsapp": s1["whatsapp"],
            "payment_reachability": s1["payment_reachability"],
            "spoken_price": s1["spoken_price"],
            "price_breakdown": s1["price_breakdown"],
            "failure": s1.get("failure", ""),
        },
        "scenario_2": {
            **_scenario_summary("scenario_2", s2),
            "name_collected": bool(s2["session"].memory.get("customer_name")),
            "no_booking_created": _no_booking_created(s2["session"]),
        },
        "scenario_3": {
            **_scenario_summary("scenario_3", s3),
            "name_collected": bool(s3["session"].memory.get("customer_name")),
            "no_booking_created": _no_booking_created(s3["session"]),
            "availability_turn": s3["availability_turn"],
            "narrowed_slots": SLOT_RE.findall(s3["availability_turn"]),
        },
        "scenario_4": {
            **_scenario_summary("scenario_4", s4),
            "sticky_escalation": all(
                (turn.get("after", {}).get("escalation_state") or {}).get("escalate")
                for turn in s4["session"].turns[2:]
            ),
            "never_reached_name_or_booking": not s4["session"].memory.get("customer_name") and _no_booking_created(s4["session"]),
            "handoff": s4["handoff"],
            "sentiment": s4["sentiment"],
        },
        "scenario_5": {
            **_scenario_summary("scenario_5", s5),
            "name_collected": bool(s5["session"].memory.get("customer_name")),
            "no_booking_created": _no_booking_created(s5["session"]),
        },
        "cleanup": cleanup,
    }
    output = OUTPUT_DIR / "final_consolidated_live_validation_report.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps({"report_path": str(output), **_console_summary(report)}, indent=2, ensure_ascii=False, default=str))
    return 0


def _console_summary(report: dict[str, Any]) -> dict[str, Any]:
    booking = report["scenario_1"]["booking"]
    whatsapp = report["scenario_1"]["whatsapp"]
    return {
        "scenario_1_booking_id": booking.get("booking_id"),
        "scenario_1_order_id": booking.get("order_id"),
        "scenario_1_payment_url": booking.get("payment_url"),
        "scenario_1_wati_message_id": whatsapp.get("message_id"),
        "scenario_1_wati_status_code": whatsapp.get("status_code"),
        "scenario_1_payment_reachable": report["scenario_1"]["payment_reachability"].get("reachable"),
        "scenario_2_name_collected": report["scenario_2"]["name_collected"],
        "scenario_2_no_booking_created": report["scenario_2"]["no_booking_created"],
        "scenario_3_name_collected": report["scenario_3"]["name_collected"],
        "scenario_3_no_booking_created": report["scenario_3"]["no_booking_created"],
        "scenario_4_sticky": report["scenario_4"]["sticky_escalation"],
        "scenario_4_no_name_or_booking": report["scenario_4"]["never_reached_name_or_booking"],
        "scenario_5_name_collected": report["scenario_5"]["name_collected"],
        "scenario_5_no_booking_created": report["scenario_5"]["no_booking_created"],
        "cleanup_action": report["cleanup"].get("action"),
        "cleanup_resolved": report["cleanup"].get("resolved"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
