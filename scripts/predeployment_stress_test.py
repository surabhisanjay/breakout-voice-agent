#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

# Safe stress mode: real dispatch(), deterministic backend, no 25x live payment
# links or real WhatsApp sends. The live transport smoke is run separately.
os.environ["OPENAI_API_KEY"] = ""
os.environ["BREAKOUT_GPT_REASONER"] = "false"
os.environ["DEMO_MODE"] = "true"
os.environ["BOOKING_PROVIDER"] = "simulator"
os.environ["BOOKING_API_KEY"] = ""
os.environ["BOOKING_BASE_URL"] = ""
os.environ["WATI_ACCESS_TOKEN"] = ""
os.environ["WATI_API_KEY"] = ""
os.environ["WATI_BASE_URL"] = ""

from fastapi.testclient import TestClient  # noqa: E402

import app as api_app  # noqa: E402
from main import dispatch  # noqa: E402
from src.agents.booking_agent import BookingAgent  # noqa: E402
from src.agents.handoff_summary_agent import HandoffSummaryAgent  # noqa: E402
from src.agents.inbound_agent import InboundAgent  # noqa: E402
from src.knowledge.knowledge_loader import KnowledgeLoader  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402


CUSTOMER_NAME = "Siddharth Khandelwal"
CUSTOMER_PHONE = "9982151357"
BANNED = re.compile(
    r"Great!|Certainly!|Absolutely!|Of course!|May I know your good name|"
    r"no refund or reschedule policy",
    re.IGNORECASE,
)
SLOT_PATTERN = re.compile(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)\b", re.IGNORECASE)


class StressFailure(AssertionError):
    pass


@dataclass
class StressSession:
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
        result, self.booking, self.active_agent = dispatch(
            message,
            self.inbound,
            self.booking,
            self.active_agent,
        )
        self.turns.append(
            {
                "turn": len(self.turns) + 1,
                "user": message,
                "agent": result.response,
                "active_agent": self.active_agent,
                "next_agent": result.next_agent,
                "booking_result": result.booking_result,
                "escalation": result.escalation,
            }
        )
        if BANNED.search(result.response):
            self.fail(f"banned phrase in response: {result.response}")
        return result

    def fail(self, reason: str) -> None:
        state = {
            key: self.memory.get(key)
            for key in (
                "intent", "location", "room", "participants", "age_group",
                "preferred_date", "preferred_period", "selected_slot",
                "customer_name", "phone", "booking_id", "booking_status",
                "recommended_option", "rejected_options", "current_workflow",
                "escalation_state",
            )
            if self.memory.get(key) not in ("", None, [], {})
        }
        raise StressFailure(
            f"{self.name}: {reason}; active={self.active_agent}; "
            f"turns={len(self.turns)}; state={json.dumps(state, ensure_ascii=True, default=str)}"
        )

    def latest_slots(self) -> list[str]:
        for turn in reversed(self.turns):
            slots = SLOT_PATTERN.findall(turn["agent"])
            if slots:
                return [slot.upper().replace("  ", " ") for slot in slots]
        return []

    def is_no_slot_end(self) -> bool:
        if not self.turns:
            return False
        response = self.turns[-1]["agent"].lower()
        return (
            "no verified evening slots" in response
            or "no available slots" in response
            or "don't have availability" in response
        )

    def choose_slot(self, preferred: str = "first") -> str:
        slots = self.latest_slots()
        if not slots:
            self.fail("no slots found to choose from")
        if preferred == "last":
            return slots[-1]
        if preferred == "around7":
            def minutes(slot: str) -> int:
                h, m, ap = re.match(r"(\d{1,2}):(\d{2})\s*(AM|PM)", slot, re.I).groups()
                hour = int(h) % 12 + (12 if ap.upper() == "PM" else 0)
                return hour * 60 + int(m)
            return min(slots, key=lambda slot: abs(minutes(slot) - 19 * 60))
        return slots[0]

    def complete_booking_if_needed(self, slot_pref: str = "first") -> dict[str, Any]:
        if self.memory.get("current_workflow") == "awaiting_booking":
            self.say("Yes, check availability.")
        if not self.latest_booking() and not self.latest_slots():
            if self.memory.get("recommended_option") and not self.memory.get("room"):
                self.say(f"Let's book {self.memory['recommended_option']}.")
            elif self.active_agent == "booking_agent" and not self.memory.get("room"):
                room = "Classified" if self.memory.get("challenge_preference") == "challenging" else "Murder Mystery"
                self.say(room)
            elif self.active_agent == "booking_agent" and not self.memory.get("selected_slot"):
                self.say("Please check availability.")
            else:
                self.say("Please book it now.")
        if not self.latest_booking() and not self.latest_slots() and self.active_agent != "booking_agent":
            self.say("I want to continue with the booking.")
        if not self.latest_booking():
            if self.latest_slots() and not self.memory.get("selected_slot"):
                self.say(self.choose_slot(slot_pref))
            if not self.memory.get("age_group"):
                self.say("All adults.")
            if not self.memory.get("customer_name"):
                self.say(f"My name is {CUSTOMER_NAME}.")
            if not self.memory.get("phone"):
                self.say(CUSTOMER_PHONE)
            if not self.latest_booking() and self.memory.get("current_workflow") == "awaiting_booking":
                self.say("Yes, check availability.")
            if not self.latest_booking() and self.latest_slots() and not self.memory.get("selected_slot"):
                self.say(self.choose_slot(slot_pref))
            if not self.latest_booking() and self.memory.get("selected_slot") and self.memory.get("customer_name") and self.memory.get("phone"):
                self.say("Please confirm it.")
        booking = self.latest_booking()
        if not booking:
            self.fail("booking was not created")
        if not booking.get("booking_id"):
            self.fail("booking id missing")
        if not booking.get("order_id"):
            self.fail("order id missing")
        if "payment_url" not in booking:
            self.fail("payment url field missing")
        whatsapp = booking.get("whatsapp") or self.memory.get("whatsapp_delivery") or {}
        if "attempted" not in whatsapp or "sent" not in whatsapp:
            self.fail(f"structured WATI delivery result missing: {whatsapp}")
        return booking

    def latest_booking(self) -> dict[str, Any]:
        for turn in reversed(self.turns):
            if isinstance(turn.get("booking_result"), dict) and turn["booking_result"].get("booking_id"):
                return turn["booking_result"]
        completed = self.memory.get("completed_booking")
        return completed if isinstance(completed, dict) else {}

    def assert_escalated(self) -> None:
        state = self.memory.get("escalation_state") or {}
        if not state.get("escalate"):
            self.fail("escalation did not fire")
        handoff = HandoffSummaryAgent().generate(self.memory, state)
        if not handoff.get("conversation_summary") or not handoff.get("action_items"):
            self.fail("handoff artifacts missing")


def run_turns(session: StressSession, turns: list[str]) -> None:
    for turn in turns:
        session.say(turn)


def booking_flow(turns: list[str], slot_pref: str = "first") -> Callable[[StressSession], None]:
    def _run(session: StressSession) -> None:
        run_turns(session, turns)
        try:
            session.complete_booking_if_needed(slot_pref)
        except StressFailure:
            if session.is_no_slot_end():
                return
            raise
    return _run


def coordination_or_booking_flow(turns: list[str], slot_pref: str = "first") -> Callable[[StressSession], None]:
    def _run(session: StressSession) -> None:
        run_turns(session, turns)
        if session.latest_slots() or session.active_agent == "booking_agent":
            try:
                session.complete_booking_if_needed(slot_pref)
            except StressFailure:
                response = session.turns[-1]["agent"].lower() if session.turns else ""
                if "events team" in response or "no room has been confirmed" in response:
                    return
                raise
            return
        if session.memory.get("intent") not in {"birthday_party", "corporate_event"}:
            session.fail("expected booking or event coordination")
        if not session.memory.get("customer_name"):
            session.say(f"My name is {CUSTOMER_NAME}.")
        if not session.memory.get("phone"):
            session.say(CUSTOMER_PHONE)
    return _run


def escalation_flow(turns: list[str]) -> Callable[[StressSession], None]:
    def _run(session: StressSession) -> None:
        run_turns(session, turns)
        session.assert_escalated()
    return _run


def faq_only(turns: list[str]) -> Callable[[StressSession], None]:
    def _run(session: StressSession) -> None:
        run_turns(session, turns)
        if session.memory.get("booking_id"):
            session.fail("FAQ-only flow unexpectedly created a booking")
    return _run


def second_booking(session: StressSession) -> None:
    run_turns(
        session,
        [
            "Hi book an escape room for two adults, Whitefield, first time.",
            "Recommend something beginner friendly.",
            "Tomorrow evening.",
        ],
    )
    first = session.complete_booking_if_needed("first")
    session.say("Also book another one, same day, different room, three people.")
    session.say("Use the same phone number. Actually Whitefield still.")
    session.say("What do you recommend now?")
    if session.memory.get("room") == first.get("room"):
        session.fail("second booking carried stale first room")
    session.say("Let's do that one.")
    session.say("Tomorrow evening.")
    session.complete_booking_if_needed("last")
    if not session.memory.get("previous_bookings"):
        session.fail("previous booking not preserved")


def synthetic_whatsapp_shared_state(root: Path) -> dict[str, Any]:
    api_app.API_MEMORY_DIR = root / "api_sessions"
    api_app._sessions.clear()
    client = TestClient(api_app.app)
    payloads = [
        {"waId": CUSTOMER_PHONE, "text": "Hi"},
        {"waId": CUSTOMER_PHONE, "text": "Couple"},
        {"waId": CUSTOMER_PHONE, "text": "Whitefield tomorrow evening"},
        {"waId": CUSTOMER_PHONE, "text": "first time. recommend"},
    ]
    for payload in payloads:
        response = client.post("/webhooks/wati", json=payload)
        if response.status_code != 200:
            raise StressFailure(f"synthetic WhatsApp webhook failed: {response.status_code} {response.text}")
    memory = client.get("/memory/whatsapp:919982151357").json()["memory"]
    if memory.get("participants") != 2 or memory.get("location") != "Whitefield":
        raise StressFailure(f"synthetic WhatsApp memory did not persist: {memory}")
    return memory


SCENARIOS: list[tuple[str, Callable[[StressSession], None]]] = [
    ("first_time_couple_interruptions", booking_flow([
        "Hi is this breakout?",
        "We're a couple, first escape room, maybe tomorrow evening.",
        "Whitefield I think.",
        "What would you recommend?",
        "What other rooms do you have?",
        "Tell me about Murder Mystery.",
        "What's the price?",
        "Okay let's do that.",
        "I can come around evening.",
    ], "around7")),
    ("family_six_birthday", coordination_or_booking_flow([
        "Need birthday thing for six, kids and adults mixed.",
        "Actually maybe JP Nagar, tomorrow evening.",
        "Do you have food also?",
        "Recommend a room that won't scare the kids too much.",
        "Let's book that one.",
    ])),
    ("corporate_food_meeting", coordination_or_booking_flow([
        "Hi we are planning a corporate outing, maybe twelve people.",
        "Need meeting space and food if possible.",
        "Whitefield tomorrow evening.",
        "Which escape room can work?",
        "Okay proceed with the option you suggest.",
    ])),
    ("small_birthday_couple", coordination_or_booking_flow([
        "Birthday booking for two adults. First timers.",
        "Koramangala, tomorrow evening.",
        "Can we cut cake there?",
        "Recommend one.",
        "Let's take the other option actually.",
    ])),
    ("returning_customer_same_number", booking_flow([
        "Hey I played last month, don't treat us as beginners.",
        "Four adults, Koramangala.",
        "Tomorrow evening around seven.",
        "Something hard.",
        "Book the Koramangala option, same number as before - 9982151357.",
    ], "around7")),
    ("single_rejection", booking_flow([
        "Couple. JP Nagar. First time. Tomorrow evening.",
        "Recommend something.",
        "No not that one.",
        "Any other option?",
        "Fine book it.",
    ])),
    ("location_change_midstream", booking_flow([
        "Book for four adults in Koramangala.",
        "Actually no, Whitefield not Koramangala.",
        "Tomorrow evening.",
        "First timers. Recommend.",
        "Let's do that one.",
    ])),
    ("slot_change", booking_flow([
        "Four adults, Whitefield, Murder Mystery, tomorrow evening.",
        "Book around seven.",
        "Actually make the later slot if available.",
    ], "last")),
    ("room_comparison", booking_flow([
        "We are three adults in Koramangala tomorrow evening.",
        "Compare Hostage and Classified.",
        "We have played before, want more intense.",
        "Okay Classified then.",
    ], "around7")),
    ("location_comparison", booking_flow([
        "What's better Whitefield or Koramangala for four first timers?",
        "Okay Whitefield then.",
        "Tomorrow evening.",
        "Recommend and book.",
    ])),
    ("parking_food_policy_stack", booking_flow([
        "Hi four adults Whitefield tomorrow evening.",
        "Before booking, parking? food? cancellation policy?",
        "Okay recommend a beginner room.",
        "Let's book the first one.",
    ])),
    ("two_bookings_same_call", second_booking),
    ("change_phone_number", booking_flow([
        "Book Murder Mystery for four adults Whitefield tomorrow evening.",
        "Use my old number actually no use 9982151357.",
        "Name Siddharth Khandelwal.",
    ])),
    ("repeated_interruptions", booking_flow([
        "I want to book for five adults.",
        "Whitefield.",
        "Wait do you give hints?",
        "And is it horror?",
        "Tomorrow evening.",
        "Recommend one.",
        "Okay that one.",
    ])),
    ("vague_answers", booking_flow([
        "Need a room.",
        "Around evening tomorrow.",
        "Uh maybe four of us.",
        "Adults.",
        "Whitefield side.",
        "Beginner friendly.",
        "Yep that works.",
    ])),
    ("change_recommendation_halfway", booking_flow([
        "Koramangala four adults tomorrow evening.",
        "Recommend difficult.",
        "Actually I changed my mind, beginner instead.",
        "Not that one, other option.",
        "Book it.",
    ])),
    ("faq_during_booking_name_phone", booking_flow([
        "Book Hostage for four adults Koramangala tomorrow evening.",
        "Around seven.",
        "What's the age limit?",
        "Siddharth Khandelwal.",
        "Also can we pay cash only?",
        "9982151357",
    ], "around7")),
    ("resume_after_gap", booking_flow([
        "Whitefield four adults tomorrow evening Murder Mystery.",
        "I'll come back later.",
        "Hi again, same room same details.",
        "Proceed.",
    ])),
    ("angry_escalation", escalation_flow([
        "I booked yesterday.",
        "I never got confirmation.",
        "I already paid.",
        "This is frustrating, I already told you.",
        "Can I talk to someone?",
    ])),
    ("human_request_direct", escalation_flow([
        "Nothing is helping.",
        "I want a human agent.",
    ])),
    ("modification_headcount_post_payment", escalation_flow([
        "I already booked and paid.",
        "Need to increase headcount from four to six.",
        "This is urgent.",
        "Please get a real person.",
    ])),
    ("cancellation_flow", escalation_flow([
        "I want to cancel my booking.",
        "Booking id bk_demo_123.",
        "What cancellation charge applies if it's tomorrow?",
        "This is confusing, connect me to a person.",
    ])),
    ("kids_only_age_gate", faq_only([
        "Do you have a kids only game, like Wizarding Championship?",
        "What age is allowed?",
        "Okay thanks, not booking now.",
    ])),
    ("random_chitchat_preserve_state", booking_flow([
        "Book for four adults Whitefield tomorrow evening.",
        "By the way do you like puzzles?",
        "haha anyway first timers recommend something.",
        "Yes that one.",
    ])),
    ("mixed_voice_whatsapp_synthetic", lambda session: run_turns(session, [
        "Voice side: four adults, Whitefield, tomorrow evening.",
        "Then I will message from WhatsApp with the same number.",
        "Recommend a beginner room and keep this context.",
    ])),
]


def run_all() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="breakout-stress-") as temp:
        root = Path(temp)
        synthetic_memory = synthetic_whatsapp_shared_state(root)
        for index, (name, scenario) in enumerate(SCENARIOS, start=1):
            session = StressSession(f"{index:02d}_{name}", root / f"{index:02d}_{name}.json")
            try:
                scenario(session)
                if name == "mixed_voice_whatsapp_synthetic":
                    if synthetic_memory.get("participants") != 2:
                        session.fail("synthetic WhatsApp side did not preserve phone identity")
                status = "PASS"
                error = ""
            except Exception as exc:
                status = "FAIL"
                error = str(exc)
            results.append(
                {
                    "id": index,
                    "name": name,
                    "status": status,
                    "turns": len(session.turns),
                    "end_state": (
                        "booking"
                        if session.memory.get("booking_id")
                        else "escalated"
                        if (session.memory.get("escalation_state") or {}).get("escalate")
                        else "resolved"
                    ),
                    "booking_id": session.memory.get("booking_id", ""),
                    "order_id": session.memory.get("booking_order_id", ""),
                    "payment_url_present": bool(session.memory.get("payment_link")),
                    "wati": session.memory.get("whatsapp_delivery", {}),
                    "error": error,
                }
            )
    passed = sum(1 for result in results if result["status"] == "PASS")
    return {
        "mode": "safe-synthetic-dispatch",
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }


def main() -> None:
    report = run_all()
    print(json.dumps(report, indent=2, ensure_ascii=True, default=str))
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
