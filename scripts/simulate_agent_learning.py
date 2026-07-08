from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from main import _dispatch_impl, build_inbound_agent  # noqa: E402
from src.agents.learning_agent import LearningAgent  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402


SCENARIOS = {
    "uncertain_room_exploration": [
        "I want to make a booking.", "I don't know which room.", "Okay.", "Yeah, tell me.", "Whitefield",
    ],
    "all_rooms_followup": [
        "What escape rooms do you have?", "All rooms.", "Koramangala", "Tell me about Murder Mystery.",
    ],
    "first_time_booking": [
        "We want an escape room for four adults.", "Whitefield", "It is our first time.",
        "What do you recommend?", "Tomorrow", "Evening", "7 PM", "Riya Patel", "9876543210",
    ],
    "recommendation_rejection": [
        "We are three experienced players at Koramangala.", "Recommend something challenging.",
        "No, another option.", "Something with more story.",
    ],
    "birthday_package": [
        "I need a birthday party for 12 children.", "Whitefield", "Do you provide food?", "Next Saturday",
    ],
    "corporate_event": [
        "We need a corporate outing for 30 employees.", "Koramangala", "We need food too.", "25 July",
    ],
    "faq_interruption": [
        "Book an escape room for five adults.", "JP Nagar", "Is parking available?", "How long is the game?", "Tomorrow",
    ],
    "availability_before_details": [
        "Do you have anything at 7 PM tomorrow?", "Whitefield", "Four people", "Adults",
    ],
    "customer_frustration": [
        "I want to book.", "Why do you keep asking questions?", "That is not what I asked.", "Connect me to a human.",
    ],
    "cancellation_without_reference": [
        "I need to cancel my booking.", "I don't have the booking ID.", "Please connect me to someone.",
    ],
    "mid_flow_changes": [
        "Book for four adults at Whitefield tomorrow.", "Actually make that six people.",
        "Change the location to Koramangala.", "Evening works.",
    ],
    "partial_and_vague_speech": [
        "hello I want book", "friends", "maybe four", "not sure", "tell me", "JP Nagar",
    ],
}


def _agent_for_turn(active_after: str, result: Any, memory: ConversationMemory) -> str:
    if result.next_agent == "escalation_agent":
        return "escalation_agent"
    if active_after == "booking_agent" or memory.data.get("booking_started"):
        return "booking_agent"
    if getattr(memory, "data", {}).get("intent") and getattr(result, "missing_fields", []):
        return "qualification_agent"
    return "inbound_agent"


def run(output: Path) -> dict[str, Any]:
    os.environ.update({
        "DEMO_MODE": "true",
        "OPENAI_API_KEY": "",
        "BREAKOUT_GPT_REASONER": "false",
        "BOOKING_PROVIDER": "simulator",
        "CLOSIRO_SYNC_ENABLED": "false",
    })
    logging.disable(logging.CRITICAL)
    session_dir = output.parent / "agent_learning_sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    conversations = []

    for scenario_name, customer_turns in SCENARIOS.items():
        memory = ConversationMemory(session_dir / f"{scenario_name}.json")
        memory.reset()
        inbound = build_inbound_agent(Namespace(model="gpt-4o-mini", no_openai=True), memory)
        inbound.response_composer.enabled = False
        booking = None
        active = "inbound_agent"
        transcript = []
        for message in customer_turns:
            transcript.append({"role": "customer", "text": message})
            result, booking, active = _dispatch_impl(message, inbound, booking, active, scenario_name)
            transcript.append({
                "role": "agent",
                "agent": _agent_for_turn(active, result, memory),
                "text": result.response,
                "next_agent": result.next_agent,
            })
        conversations.append({
            "scenario": scenario_name,
            "turns": transcript,
            "memory": memory.as_state(),
            "sentiment": memory.data.get("sentiment_analysis") or {},
            "escalation": memory.data.get("escalation_state") or {},
        })

    learning = LearningAgent().analyze(conversations).to_dict()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "simulation_mode": "deterministic_local",
        "scenario_count": len(conversations),
        "learning_report": learning,
        "conversations": conversations,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate Breakout conversations and monitor agent quality.")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_DIR / "scratch" / "agent_learning_report.json",
    )
    args = parser.parse_args()
    report = run(args.output)
    print(json.dumps({
        "scenario_count": report["scenario_count"],
        "overall_score": report["learning_report"]["overall_score"],
        "underperforming_agents": report["learning_report"]["underperforming_agents"],
        "recurring_issues": report["learning_report"]["recurring_issues"],
        "output": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
