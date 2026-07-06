from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import app as api_app  # noqa: E402
from src.agents.evaluation_agent import EvaluationAgent  # noqa: E402


OUTPUT_DIR = PROJECT_DIR / "scratch" / "web_chat_hardening"


def _scenario_library(rng: random.Random) -> list[list[str]]:
    locations = ["Whitefield", "Koramangala", "JP Nagar"]
    rooms = ["Murder Mystery", "Hostage", "Undercover", "Bomb Defusal"]
    messy_openers = [
        "hi uh is this breakout",
        "hello I want book",
        "haan booking karna hai",
        "can you help with escape room",
        "I am checking for my friends",
    ]
    scenarios: list[list[str]] = []
    for idx in range(50):
        location = rng.choice(locations)
        room = rng.choice(rooms)
        size = rng.choice([2, 3, 4, 8, 10])
        style = idx % 10
        if style == 0:
            turns = [rng.choice(messy_openers), f"We are {size} people", "first time only", location, "What do you recommend?", "Parking available?", "Tomorrow evening"]
        elif style == 1:
            turns = ["I want to book", "six people actually four", "we played before", "something difficult", "No not that one", "Any other option?", f"What about {location}?"]
        elif style == 2:
            turns = ["Birthday booking chahiye", "kids and adults mix", "food also?", "cake allowed?", location, "tomorrow", "around evening"]
        elif style == 3:
            turns = ["Corporate outing", "around 30 employees", "need food and meeting space", "what packages", "connect me to human"]
        elif style == 4:
            turns = [f"Book {room}", "actually no change location", location, "tomorrow", "any time works", "stop here don't reserve yet"]
        elif style == 5:
            turns = ["I paid but no confirmation", "I didn't receive payment link", "this is frustrating", "I want a real person"]
        elif style == 6:
            turns = ["Compare rooms", "which is scary", "not too scary actually", f"{location} better?", "directions?", "parking?"]
        elif style == 7:
            turns = ["School group enquiry", "10 kids", "age limit?", "Wizarding game?", "food?", "not booking now"]
        elif style == 8:
            turns = ["Need to cancel", "reschedule maybe", "what is policy", "I don't have booking id", "human please"]
        else:
            turns = ["Hi", "couple", "tomorrow evening", "around 7", "what slots", "Siddharth", "not sharing phone yet"]
        scenarios.append(turns)
    return scenarios


def _client(simulator: bool = True) -> TestClient:
    if simulator:
        os.environ["BOOKING_PROVIDER"] = "simulator"
        os.environ["DEMO_MODE"] = "true"
        os.environ["OPENAI_API_KEY"] = ""
        os.environ["BREAKOUT_GPT_REASONER"] = "false"
    api_app.API_MEMORY_DIR = OUTPUT_DIR / "api_sessions"
    api_app._sessions.clear()
    return TestClient(api_app.app)


def _sentiment_chart(memory: dict[str, Any]) -> list[dict[str, Any]]:
    history = memory.get("sentiment_history") or []
    chart = []
    for idx, item in enumerate(history, start=1):
        sentiment = str(item.get("sentiment") or item.get("label") or "Neutral").lower()
        score = 0.0
        if sentiment in {"positive", "happy"}:
            score = 0.75
        elif sentiment in {"negative", "frustrated", "angry"}:
            score = -0.72
        elif sentiment == "neutral":
            score = 0.18
        chart.append({"turn": idx, "score": score, "label": sentiment.title()})
    if not chart:
        chart.append({"turn": 1, "score": 0.18, "label": "Neutral"})
    return chart


def _csat(memory: dict[str, Any], responses: list[str]) -> dict[str, Any]:
    sentiment = str(memory.get("sentiment") or "neutral").lower()
    escalated = bool((memory.get("escalation_state") or {}).get("escalate"))
    booking_id = bool(memory.get("booking_id"))
    score = 4.2
    if sentiment in {"negative", "frustrated", "angry"}:
        score -= 1.0
    if escalated:
        score -= 0.4
    if booking_id:
        score += 0.5
    if any("couldn't" in response.lower() for response in responses):
        score -= 0.2
    score = max(1.0, min(5.0, round(score, 1)))
    return {
        "score": score,
        "confidence": 0.72,
        "reason": "Predicted from final sentiment, escalation state, booking completion, and error language.",
    }


def _summary(session_id: str, turns: list[str], responses: list[str], memory: dict[str, Any], elapsed: float) -> dict[str, Any]:
    escalation = memory.get("escalation_state") or {}
    return {
        "conversation_id": session_id,
        "customer_name": memory.get("customer_name", ""),
        "phone": memory.get("phone", ""),
        "intent": memory.get("intent", ""),
        "outcome": "escalated" if escalation.get("escalate") else ("booking_reserved" if memory.get("booking_id") else "resolved_or_abandoned"),
        "duration_seconds": round(elapsed, 2),
        "turn_count": len(turns),
        "venue": memory.get("location", ""),
        "room": memory.get("room") or memory.get("recommended_option", ""),
        "date": memory.get("preferred_date", ""),
        "time": memory.get("selected_slot", ""),
        "booking_id": memory.get("booking_id", ""),
        "payment_status": memory.get("paymentStatus") or memory.get("payment_status") or "",
        "escalation_status": "escalated" if escalation.get("escalate") else "none",
        "escalation_reason": escalation.get("reason", ""),
        "final_sentiment": memory.get("sentiment", "neutral"),
        "conversation_result": responses[-1] if responses else "",
    }


def _metrics(conversation_summaries: list[dict[str, Any]], all_turns: list[str], response_times: list[float]) -> dict[str, Any]:
    joined = "\n".join(all_turns).lower()
    return {
        "recommendation_requested": len(re.findall(r"recommend|suggest|which room", joined)),
        "recommendation_accepted": len(re.findall(r"book it|let'?s do|go with", joined)),
        "recommendation_rejected": len(re.findall(r"no not|something else|don't like", joined)),
        "booking_requested": len(re.findall(r"\bbook|booking|reserve\b", joined)),
        "booking_completed": sum(1 for item in conversation_summaries if item["booking_id"]),
        "booking_abandoned": sum(1 for item in conversation_summaries if not item["booking_id"] and "book" in item["conversation_result"].lower()),
        "parking_requested": joined.count("parking"),
        "food_requested": joined.count("food"),
        "directions_requested": joined.count("directions"),
        "pricing_requested": joined.count("price") + joined.count("pricing"),
        "cancellation_requested": joined.count("cancel"),
        "reschedule_requested": joined.count("reschedule"),
        "faq_count": sum(joined.count(word) for word in ["parking", "food", "directions", "policy", "age", "price"]),
        "payment_link_sent": sum(1 for item in conversation_summaries if item["payment_status"] == "UNPAID"),
        "payment_pending": sum(1 for item in conversation_summaries if item["payment_status"] == "UNPAID"),
        "payment_completed": sum(1 for item in conversation_summaries if item["payment_status"] == "PAID"),
        "escalation_requested": len(re.findall(r"human|connect me|transfer|real person", joined)),
        "human_connected": sum(1 for item in conversation_summaries if item["escalation_status"] == "escalated"),
        "conversation_completed": len(conversation_summaries),
        "average_response_time_seconds": round(sum(response_times) / len(response_times), 3) if response_times else 0,
        "backend_tool_call_count": 0,
        "api_call_count": len(response_times),
        "conversation_duration_seconds": round(sum(item["duration_seconds"] for item in conversation_summaries), 2),
    }


def run_stress(seed: int | None = None) -> dict[str, Any]:
    logging.disable(logging.CRITICAL)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed if seed is not None else time.time_ns())
    client = _client(simulator=True)
    scenarios = _scenario_library(rng)
    evaluator = EvaluationAgent()
    conversation_summaries: list[dict[str, Any]] = []
    sentiment_charts: dict[str, list[dict[str, Any]]] = {}
    csat_predictions: dict[str, dict[str, Any]] = {}
    evaluations: dict[str, dict[str, Any]] = {}
    all_turns: list[str] = []
    response_times: list[float] = []
    failures: list[dict[str, Any]] = []
    learning_counter: Counter[str] = Counter()

    for idx, turns in enumerate(scenarios, start=1):
        session_id = f"stress-{idx:02d}-{rng.randrange(100000, 999999)}"
        started = time.perf_counter()
        responses: list[str] = []
        for turn_no, turn in enumerate(turns, start=1):
            all_turns.append(turn)
            before = time.perf_counter()
            response = client.post("/chat", json={"session_id": session_id, "message": turn})
            elapsed = time.perf_counter() - before
            response_times.append(elapsed)
            if response.status_code != 200:
                failures.append({"session_id": session_id, "turn": turn_no, "status_code": response.status_code, "body": response.text})
                break
            body = response.json()
            text = body.get("response", "")
            responses.append(text)
            lowered = f"{turn}\n{text}".lower()
            for topic in ("parking", "food", "directions", "policy", "price", "payment", "human", "cancel", "reschedule"):
                if topic in lowered:
                    learning_counter[topic] += 1
            if turn_no < len(turns) and body.get("booking", {}).get("booking_id"):
                failures.append({"session_id": session_id, "turn": turn_no, "error": "Stress conversation reached booking unexpectedly"})
                break
        memory = client.get(f"/memory/{session_id}").json()["memory"]
        elapsed_total = time.perf_counter() - started
        summary = _summary(session_id, turns, responses, memory, elapsed_total)
        conversation_summaries.append(summary)
        sentiment_charts[session_id] = _sentiment_chart(memory)
        csat_predictions[session_id] = _csat(memory, responses)
        evaluation = evaluator.evaluate({"memory": memory, "turns": memory.get("conversation", []), "backend_errors": failures})
        evaluations[session_id] = {
            "score": evaluation.score,
            "category_scores": evaluation.category_scores,
            "reasoning": evaluation.reasoning,
            "flags": evaluation.flags,
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "transport": "web_chat",
        "stress_passed": len(failures) == 0,
        "failures": failures,
        "conversation_summaries": conversation_summaries,
        "metrics": _metrics(conversation_summaries, all_turns, response_times),
        "sentiment_charts": sentiment_charts,
        "csat_predictions": csat_predictions,
        "evaluations": evaluations,
        "learning_report": {
            "common_faqs": learning_counter.most_common(),
            "common_booking_failures": [failure.get("error") or failure.get("body") for failure in failures],
            "drop_off_points": [item for item in conversation_summaries if not item["booking_id"] and item["outcome"] != "escalated"][:10],
            "escalation_reasons": Counter(
                item["escalation_reason"] or "Escalation requested"
                for item in conversation_summaries
                if item["escalation_status"] == "escalated"
            ).most_common(),
            "confusion_patterns": ["Payment-link recovery", "Cancellation without booking id", "Location/date switching"],
            "conversation_loops_found": [failure for failure in failures if "loop" in json.dumps(failure).lower()],
            "repeated_tool_failures": [],
            "suggested_backend_improvements": [
                "Continue expanding deterministic date parsing for vague future dates.",
                "Expose optional room media URLs from the knowledge/provider layer when available.",
            ],
        },
    }
    (OUTPUT_DIR / "web_chat_hardening_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    report = run_stress(seed=args.seed)
    print(json.dumps({
        "stress_passed": report["stress_passed"],
        "conversations": len(report["conversation_summaries"]),
        "failures": report["failures"],
        "output": str(OUTPUT_DIR / "web_chat_hardening_report.json"),
    }, indent=2))
    return 0 if report["stress_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
