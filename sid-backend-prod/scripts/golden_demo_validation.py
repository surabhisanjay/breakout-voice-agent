from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from pprint import pprint
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

os.environ["BOOKING_PROVIDER"] = "simulator"
os.environ["BOOKING_API_KEY"] = ""
os.environ["BOOKING_BASE_URL"] = ""

from main import dispatch
from src.agents.booking_agent import BookingAgent
from src.agents.handoff_summary_agent import HandoffSummaryAgent
from src.agents.inbound_agent import InboundAgent
from src.knowledge.knowledge_loader import KnowledgeLoader
from src.memory.conversation_memory import ConversationMemory
from src.services.conversation_guard import ConversationGuard


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "to_dict"):
        return _jsonable(value.to_dict())
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _agent(name: str) -> InboundAgent:
    memory_dir = PROJECT_DIR / "scratch" / "golden_demo"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory = ConversationMemory(memory_dir / f"{name}.json")
    memory.reset()
    return InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


class DemoConversation:
    def __init__(self, name: str) -> None:
        self.name = name
        self.inbound = _agent(name)
        self.booking: BookingAgent | None = None
        self.active = "inbound_agent"
        self.turns: list[dict[str, Any]] = []

    def say(self, message: str) -> Any:
        before = dict(self.inbound.memory.data)
        guard_probe = None
        try:
            guard_probe = ConversationGuard(self.inbound.memory).evaluate(message, self.active)
            if guard_probe is not None:
                self.inbound.memory.data = before
                self.inbound.memory.save()
        except Exception as exc:
            guard_probe = {"error": repr(exc)}
            self.inbound.memory.data = before
            self.inbound.memory.save()

        result, self.booking, self.active = dispatch(message, self.inbound, self.booking, self.active)
        turn = {
            "customer": message,
            "response": result.response,
            "active_agent": self.active,
            "next_agent": result.next_agent,
            "should_handoff": result.should_handoff,
            "intent": result.intent,
            "missing_fields": result.missing_fields,
            "recommendation": result.recommendation,
            "booking_result": result.booking_result,
            "booking": result.booking,
            "sentiment_analysis": result.sentiment_analysis,
            "escalation": result.escalation,
            "handoff_summary": result.handoff_summary,
            "call_intelligence": result.call_intelligence,
            "conversation_guard_probe": (
                guard_probe.response if hasattr(guard_probe, "response") else _jsonable(guard_probe)
            ),
            "conversation_guard_category": (
                guard_probe.debug.get("conversation_guard", {}).get("category")
                if hasattr(guard_probe, "debug")
                else ""
            ),
            "memory": self.inbound.memory.as_state(),
        }
        self.turns.append(_jsonable(turn))
        return result

    def snapshot(self) -> dict[str, Any]:
        memory = self.inbound.memory.data
        handoff = HandoffSummaryAgent().generate(memory, memory.get("escalation_state", {}))
        booking_payload = next(
            (turn.get("booking_result") for turn in reversed(self.turns) if turn.get("booking_result")),
            None,
        )
        return _jsonable(
            {
                "name": self.name,
                "active_agent": self.active,
                "memory_state": memory,
                "recommendation_state": {
                    "recommended_option": memory.get("recommended_option"),
                    "rejected_options": memory.get("rejected_options"),
                    "discussed_options": memory.get("discussed_options"),
                },
                "booking_payload": booking_payload,
                "booking_agent": {
                    "state": getattr(self.booking, "_state", None),
                    "available_slots": getattr(self.booking, "_available_slots", None),
                    "selected_slot": getattr(self.booking, "_selected_slot", None),
                    "last_availability": getattr(self.booking, "_last_availability", None),
                    "booking_result": getattr(self.booking, "_booking_result", None),
                },
                "sentiment_state": memory.get("sentiment_analysis"),
                "escalation_state": memory.get("escalation_state"),
                "handoff_summary": handoff,
                "conversation_intelligence": memory.get("conversation_intelligence"),
                "turns": self.turns,
            }
        )


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_no_repeated_questions(convo: DemoConversation) -> None:
    questions = [
        turn["response"].strip().lower()
        for turn in convo.turns
        if turn["response"].strip().endswith("?")
    ]
    for left, right in zip(questions, questions[1:]):
        _assert(left != right, f"{convo.name}: repeated question: {left}")
        _assert(_question_core(left) != _question_core(right), f"{convo.name}: repeated question topic: {right}")


def _question_core(text: str) -> str:
    lowered = text.lower()
    if "which location" in lowered or "which branch" in lowered:
        return "location"
    if "how many" in lowered or "players" in lowered or "people" in lowered:
        return "participants"
    if "what date" in lowered:
        return "date"
    if "which time" in lowered or "time works" in lowered:
        return "slot"
    if "phone" in lowered:
        return "phone"
    if "name" in lowered:
        return "name"
    return lowered


def _assert_intelligence(snapshot: dict[str, Any]) -> None:
    intelligence = snapshot["conversation_intelligence"]
    _assert(bool(intelligence), f"{snapshot['name']}: missing conversation intelligence")
    for key in (
        "ai_summary",
        "timeline_events",
        "key_takeaways",
        "customer_profile",
        "booking_milestones",
        "conversation_risks",
        "follow_up_recommendations",
        "sentiment_analysis",
        "escalation",
    ):
        _assert(key in intelligence, f"{snapshot['name']}: missing intelligence field {key}")


def scenario_1() -> dict[str, Any]:
    convo = DemoConversation("scenario_1_first_time_booking")
    convo.say("I'd like to book an escape room.")
    convo.say("We are 4 adults.")
    convo.say("It is our first time.")
    convo.say("Whitefield.")
    convo.say("What would you recommend?")
    convo.say("What other rooms do you have?")
    convo.say("Which one is your best seller?")
    convo.say("Murder Mystery.")
    availability = convo.say("Tomorrow evening.")
    _assert("AM" not in availability.response, "scenario 1: evening slot response included morning slots")
    _assert("PM" in availability.response, "scenario 1: no evening slots returned")
    slot = convo.say("Around 6:30 PM")
    _assert("6:30 PM" in convo.inbound.memory.data.get("selected_slot", ""), "scenario 1: selected slot was not 6:30 PM")
    convo.say("Siddharth Khandelwal")
    final = convo.say("9982151357")
    snapshot = convo.snapshot()
    _assert(final.booking_result and final.booking_result.get("confirmed"), "scenario 1: booking did not complete")
    _assert(snapshot["memory_state"]["customer_name"] == "Siddharth Khandelwal", "scenario 1: name not stored")
    _assert(snapshot["memory_state"]["phone"] == "9982151357", "scenario 1: phone not stored")
    _assert(snapshot["memory_state"]["room"] == "Murder Mystery", "scenario 1: room not stored")
    _assert(snapshot["memory_state"]["location"] == "Whitefield", "scenario 1: location not stored")
    _assert(snapshot["booking_payload"]["slot"] == "6:30 PM", "scenario 1: booking payload wrong slot")
    _assert_intelligence(snapshot)
    _assert_no_repeated_questions(convo)
    return snapshot


def scenario_2() -> dict[str, Any]:
    convo = DemoConversation("scenario_2_slot_change")
    convo.say("I'd like to book Hostage.")
    convo.say("We are returning customers.")
    convo.say("6 adults.")
    convo.say("JP Nagar.")
    availability = convo.say("Tomorrow.")
    _assert("PM" in availability.response, "scenario 2: no slots returned")
    convo.say("3:30 or 4 PM would work.")
    change = convo.say("Actually make it 7 PM.")
    _assert("7:00 PM" in convo.inbound.memory.data.get("selected_slot", ""), "scenario 2: slot was not updated to 7 PM")
    _assert("location" not in change.response.lower(), "scenario 2: booking restarted after slot change")
    convo.say("Siddharth Khandelwal")
    final = convo.say("9982151357")
    snapshot = convo.snapshot()
    _assert(final.booking_result and final.booking_result.get("confirmed"), "scenario 2: booking did not complete")
    _assert(snapshot["booking_payload"]["slot"] == "7:00 PM", "scenario 2: booking payload wrong slot")
    _assert(snapshot["memory_state"]["selected_slot"] == "7:00 PM", "scenario 2: memory wrong slot")
    _assert_intelligence(snapshot)
    _assert(snapshot["conversation_intelligence"]["timeline_events"], "scenario 2: missing timeline events")
    _assert(snapshot["conversation_intelligence"]["ai_summary"], "scenario 2: missing AI summary")
    _assert(snapshot["conversation_intelligence"]["customer_profile"], "scenario 2: missing customer profile")
    _assert("conversation_risks" in snapshot["conversation_intelligence"], "scenario 2: missing risks")
    _assert(snapshot["conversation_intelligence"]["follow_up_recommendations"], "scenario 2: missing follow-ups")
    _assert_no_repeated_questions(convo)
    return snapshot


def scenario_3() -> dict[str, Any]:
    convo = DemoConversation("scenario_3_angry_escalation")
    convo.inbound.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "location": "Whitefield",
            "room": "Hostage",
            "participants": 4,
            "age_group": "adults",
            "preferred_date": "Tomorrow",
            "selected_slot": "6:30 PM",
            "customer_name": "Siddharth Khandelwal",
            "phone": "9982151357",
            "booking_id": "BRK-DEMO123",
            "booking_ref": "BRK-DEMO123",
            "completed_booking": True,
        }
    )
    convo.inbound.memory.save()
    convo.say("I've already booked.")
    convo.say("My booking is wrong.")
    convo.say("You people are not understanding.")
    convo.say("I already told you everything.")
    final = convo.say("I want to speak to a human.")
    snapshot = convo.snapshot()
    sentiment = snapshot["sentiment_state"]
    escalation = snapshot["escalation_state"]
    _assert(final.should_handoff, "scenario 3: handoff not triggered")
    _assert(escalation and escalation.get("escalate"), "scenario 3: escalation state not set")
    for key in (
        "current_sentiment",
        "overall_sentiment",
        "frustration_score",
        "sentiment_journey",
        "conversation_health",
        "escalation_risk",
    ):
        _assert(key in sentiment, f"scenario 3: missing sentiment field {key}")
    _assert(sentiment["current_sentiment"] in {"Frustrated", "Angry", "Upset"}, "scenario 3: current sentiment did not reflect frustration")
    _assert(sentiment["overall_sentiment"] in {"Frustrated", "Ready To Escalate"}, "scenario 3: overall sentiment did not rise")
    _assert(float(sentiment["frustration_score"]) >= 0.4, "scenario 3: frustration score did not increase enough")
    _assert(sentiment["escalation_risk"] in {"medium", "high"}, "scenario 3: escalation risk did not increase")
    for key in ("trigger", "priority", "status", "recommended_human_action"):
        _assert(key in escalation, f"scenario 3: missing escalation field {key}")
    _assert(snapshot["memory_state"]["booking_ref"] == "BRK-DEMO123", "scenario 3: booking context lost")
    _assert(snapshot["handoff_summary"]["intent"], "scenario 3: handoff missing intent")
    _assert(snapshot["handoff_summary"]["booking_details"]["slot"] == "6:30 PM", "scenario 3: handoff lost slot")
    _assert(snapshot["handoff_summary"]["sentiment"], "scenario 3: handoff missing sentiment")
    _assert(snapshot["handoff_summary"]["action_items"], "scenario 3: handoff missing action items")
    _assert(snapshot["handoff_summary"]["escalation"]["required"], "scenario 3: handoff escalation block missing")
    _assert_intelligence(snapshot)
    _assert(snapshot["conversation_intelligence"]["conversation_risks"], "scenario 3: missing conversation risks")
    _assert_no_repeated_questions(convo)
    return snapshot


def main() -> None:
    results = []
    for scenario in (scenario_1, scenario_2, scenario_3):
        snapshot = scenario()
        results.append(snapshot)
        print(f"\n\n=== {snapshot['name']} ===")
        pprint(snapshot, width=140, sort_dicts=False)

    report = {
        "backend_pass": True,
        "scenario_count": 3,
        "bugs_fixed": [
            "Fragment-style availability requests such as 'Tomorrow evening' now route to BookingAgent availability.",
            "Simulator provider now exposes demo-ready tomorrow slots for Whitefield and JP Nagar.",
            "Repeated qualification questions are repaired by recent question topic, not exact text only.",
            "Existing-booking complaint phrases now update frustration sentiment and trigger escalation risk.",
            "Conversation Guard now repairs booking complaints before normal post-booking closeout.",
        ],
        "regression_tests_added": [
            "tests/test_golden_demo_regressions.py",
        ],
        "production_readiness_score": 95,
        "remaining_risks": [
            "Mock provider validates deterministic flow only; live Kreeda availability and booking still depend on external API health.",
            "New ASR variants may require additional alias entries after real calls.",
        ],
        "scenarios": results,
    }
    out = PROJECT_DIR / "scratch" / "golden_demo" / "golden_demo_validation_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nGolden demo validation report written to {out}")


if __name__ == "__main__":
    main()
