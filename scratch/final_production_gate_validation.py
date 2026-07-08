#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import app as api_app  # noqa: E402
from main import dispatch  # noqa: E402
from src.agents.booking_agent import BookingAgent  # noqa: E402
from src.agents.handoff_summary_agent import HandoffSummaryAgent  # noqa: E402
from src.agents.inbound_agent import InboundAgent  # noqa: E402
from src.config.env_loader import booking_provider_label, load_project_env  # noqa: E402
from src.knowledge.knowledge_loader import KnowledgeLoader  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402


OUTPUT_DIR = PROJECT_DIR / "scratch" / "final_production_gate_validation"
CUSTOMER_NAME = "Siddharth Khandelwal"
CUSTOMER_PHONE = "9982151357"
SLOT_RE = re.compile(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)\b", re.IGNORECASE)


BOOKING_FIELDS = (
    "participants",
    "experience_level",
    "location",
    "room",
    "preferred_date",
    "preferred_time",
    "preferred_period",
    "selected_slot",
    "age_group",
    "customer_name",
    "phone",
)

REPORTING_KEYS = (
    "booking",
    "payment",
    "media",
    "price_breakdown",
    "conversation_summary",
    "handoff_summary",
    "evaluation",
    "metrics",
    "csat",
    "sentiment",
    "sentiment_graph",
    "learning",
    "follow_up",
    "conversation_history",
)


@dataclass
class GateSession:
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
        before = self._state_snapshot()
        started = time.perf_counter()
        result, self.booking, self.active_agent = dispatch(
            message,
            self.inbound,
            self.booking,
            self.active_agent,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        reporting = api_app._reporting_payload(
            f"gate-{self.name}",
            result,
            self.memory,
            latency_ms,
        )
        self.inbound.memory.save()
        response_contract = {
            "response": result.response,
            "next_agent": result.next_agent,
            "media": api_app._extract_media_payload(result),
            "booking": api_app._extract_booking_payload(result),
            "payment": api_app._extract_payment_payload(result),
            "price_breakdown": api_app._extract_price_breakdown(result),
            "escalation": result.escalation or {},
            "handoff_summary": result.handoff_summary,
            "sentiment_analysis": result.sentiment_analysis or {},
            "call_intelligence": result.call_intelligence or {},
            "ai_summary": result.ai_summary or {},
            "customer_profile": result.customer_profile or {},
            "timeline_events": result.timeline_events or [],
            "follow_up_recommendations": result.follow_up_recommendations or [],
            "transcript": result.transcript or [],
            "recording": result.recording or {},
            **reporting,
        }
        after = self._state_snapshot()
        self.turns.append(
            {
                "turn": len(self.turns) + 1,
                "customer": message,
                "assistant": result.response,
                "active_agent": self.active_agent,
                "next_agent": result.next_agent,
                "should_handoff": result.should_handoff,
                "latency_ms": round(latency_ms, 1),
                "tool_calls": self._tool_calls(result),
                "before": before,
                "after": after,
                "response_contract": response_contract,
                "booking_result": result.booking_result or result.booking or {},
                "debug": result.debug or {},
            }
        )
        return result

    def _state_snapshot(self) -> dict[str, Any]:
        data = self.memory
        filled = {field: data.get(field) for field in BOOKING_FIELDS if data.get(field) not in ("", None, [], {})}
        missing = [field for field in BOOKING_FIELDS if field not in filled]
        pending_field = self._pending_field(missing)
        booking_state = {
            "agent_state": getattr(self.booking, "_state", "") if self.booking is not None else "",
            "available_slots": list(getattr(self.booking, "_available_slots", []) or []) if self.booking is not None else [],
            "last_availability": getattr(self.booking, "_last_availability", {}) if self.booking is not None else {},
            "selected_slot": data.get("selected_slot", ""),
            "booking_id": data.get("booking_id", ""),
            "order_id": data.get("order_id") or data.get("orderId") or "",
            "payment_url": data.get("paymentUrl") or data.get("payment_url") or data.get("payment_link") or "",
            "price_breakdown": data.get("price_breakdown") or {},
        }
        return {
            "current_booking_state": {key: data.get(key) for key in BOOKING_FIELDS if data.get(key) not in ("", None, [], {})},
            "filled_fields": filled,
            "missing_fields": missing,
            "conversation_memory": dict(data),
            "conversation_stage": data.get("current_workflow") or data.get("conversation_mode") or "",
            "pending_field": pending_field,
            "extracted_entities": self._latest_extracted_entities(),
            "current_intent": data.get("intent", ""),
            "recommendation_state": {
                "recommended_option": data.get("recommended_option", ""),
                "rejected_options": data.get("rejected_options", []),
                "discussed_options": data.get("discussed_options", []),
                "customer_preferences": data.get("customer_preferences", []),
            },
            "booking_state": booking_state,
            "escalation_state": data.get("escalation_state", {}),
            "sentiment": {
                "sentiment": data.get("sentiment", ""),
                "confidence": data.get("sentiment_confidence", 0.0),
                "reason": data.get("sentiment_reason", ""),
                "history": data.get("sentiment_history", []),
            },
        }

    def _latest_extracted_entities(self) -> dict[str, Any]:
        for turn in reversed(self.turns):
            debug = turn.get("debug") or {}
            extracted = debug.get("extracted_entities")
            if isinstance(extracted, dict) and extracted:
                return extracted
        return {}

    @staticmethod
    def _pending_field(missing: list[str]) -> str:
        for field in (
            "participants",
            "experience_level",
            "location",
            "room",
            "preferred_date",
            "preferred_time",
            "preferred_period",
            "selected_slot",
            "age_group",
            "customer_name",
            "phone",
        ):
            if field in missing:
                return field
        return ""

    def _tool_calls(self, result: Any) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        booking_result = result.booking_result or result.booking or {}
        if isinstance(booking_result, dict) and booking_result.get("booking_id"):
            calls.append(
                {
                    "tool": "create_booking",
                    "provider": booking_provider_label(),
                    "booking_id": booking_result.get("booking_id"),
                    "order_id": booking_result.get("order_id"),
                    "payment_url": booking_result.get("payment_url"),
                    "status": booking_result.get("status"),
                }
            )
        if isinstance(booking_result, dict) and booking_result.get("whatsapp"):
            calls.append({"tool": "wati_send_booking_payment_link", "result": booking_result.get("whatsapp")})
        if self.booking is not None and getattr(self.booking, "_last_availability", None):
            availability = getattr(self.booking, "_last_availability", {}) or {}
            if availability.get("slots"):
                calls.append(
                    {
                        "tool": "search_available_seats",
                        "provider": booking_provider_label(),
                        "location": self.memory.get("location", ""),
                        "room": self.memory.get("room", ""),
                        "date": self.memory.get("preferred_date", ""),
                        "slots": availability.get("slots", []),
                        "available": availability.get("available"),
                    }
                )
        return calls

    def transcript(self) -> list[dict[str, str]]:
        return [
            {"turn": turn["turn"], "customer": turn["customer"], "assistant": turn["assistant"]}
            for turn in self.turns
        ]


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


def _slots_from_response(text: str) -> list[str]:
    seen: list[str] = []
    for slot in SLOT_RE.findall(text):
        normalized = slot.upper()
        if normalized not in seen:
            seen.append(normalized)
    return seen


def _run_conversation_1(root: Path) -> dict[str, Any]:
    session = GateSession("conversation_1", root / "conversation_1.json")
    turns = [
        "Hi, I'd like to book an escape room tomorrow.",
        "There are two of us.",
        "It's our first escape room.",
        "What do you recommend?",
        "Is parking available?",
        "Is food allowed?",
        "Is Murder Mystery scary?",
        "Whitefield.",
        "Around 2 PM tomorrow.",
        "Let's take the closest one.",
        "We are adults.",
        f"My name is {CUSTOMER_NAME}.",
        f"My phone number is {CUSTOMER_PHONE}.",
    ]
    for turn in turns:
        session.say(turn)
    payment_url = str(
        session.memory.get("paymentUrl")
        or session.memory.get("payment_url")
        or session.memory.get("payment_link")
        or ""
    )
    return {
        "session": session,
        "payment_reachability": _payment_url_reachable(payment_url),
        "kreeda_payloads": {
            "booking_result": _latest_booking_result(session),
            "completed_booking": session.memory.get("completed_booking") or {},
            "booking_memory": {
                "booking_id": session.memory.get("booking_id"),
                "booking_ref": session.memory.get("booking_ref"),
                "order_id": session.memory.get("order_id") or session.memory.get("orderId"),
                "venue_id": session.memory.get("venue_id") or session.memory.get("venueId"),
                "booking_status": session.memory.get("bookingStatus") or session.memory.get("booking_status"),
                "payment_status": session.memory.get("paymentStatus") or session.memory.get("payment_status"),
                "payment_url": payment_url,
                "payment_deadline": session.memory.get("paymentDeadline") or session.memory.get("payment_deadline"),
            },
        },
        "wati_payloads": {
            "payload": session.memory.get("whatsapp_payload") or {},
            "delivery": session.memory.get("whatsapp_delivery") or {},
        },
    }


def _run_conversation_2(root: Path) -> dict[str, Any]:
    session = GateSession("conversation_2", root / "conversation_2.json")
    turns = [
        "Hi.",
        "Actually we're four.",
        "No, we're six.",
        "Actually Whitefield.",
        "No Koramangala.",
        "Recommend something.",
        "Hostage.",
        "No Murder Mystery.",
        "Tomorrow.",
        "Actually Friday.",
        "Around afternoon.",
        "No around 4 PM.",
        "Can children play?",
        "Is parking free?",
        "Can we celebrate birthdays?",
        "Actually I don't like this.",
        "You're repeating yourself.",
        "This isn't helping.",
        "I already answered that.",
        "I want a human.",
        "Please connect me to an agent.",
        "Do you have any 4 PM slots?",
        "Can you still answer whether parking is available?",
    ]
    for turn in turns:
        session.say(turn)
    state = session.memory.get("escalation_state") or {}
    handoff = HandoffSummaryAgent().generate(session.memory, state) if state.get("escalate") else {}
    return {"session": session, "handoff_summary": handoff}


def _latest_booking_result(session: GateSession) -> dict[str, Any]:
    for turn in reversed(session.turns):
        result = turn.get("booking_result")
        if isinstance(result, dict) and result.get("booking_id"):
            return result
    return {}


def _objects_populated(session: GateSession) -> dict[str, bool]:
    final_contract = session.turns[-1]["response_contract"] if session.turns else {}
    return {key: bool(final_contract.get(key)) for key in REPORTING_KEYS}


def _quality_checks(conversation_1: dict[str, Any], conversation_2: dict[str, Any]) -> dict[str, Any]:
    s1: GateSession = conversation_1["session"]
    s2: GateSession = conversation_2["session"]
    s1_text = "\n".join(turn["assistant"].lower() for turn in s1.turns)
    s2_after_escalation = []
    escalation_seen = False
    for turn in s2.turns:
        if (turn["after"].get("escalation_state") or {}).get("escalate"):
            escalation_seen = True
        if escalation_seen:
            s2_after_escalation.append(turn["assistant"].lower())
    booking = _latest_booking_result(s1)
    whatsapp = conversation_1["wati_payloads"]["delivery"]
    return {
        "conversation_1": {
            "booking_completed": bool(s1.memory.get("booking_id") and s1.memory.get("paymentUrl")),
            "payment_url_reachable": bool(conversation_1["payment_reachability"].get("reachable")),
            "wati_http_200": int(whatsapp.get("status_code") or 0) == 200,
            "wati_message_id_present": bool(whatsapp.get("message_id")),
            "nearby_slots_only": len(_slots_from_response(s1.turns[8]["assistant"] if len(s1.turns) > 8 else "")) <= 5,
            "faq_answers_resume": all(
                marker in s1_text
                for marker in ("parking", "food", "murder mystery")
            ),
            "price_breakdown_present": bool(s1.memory.get("price_breakdown")),
            "booking": booking,
        },
        "conversation_2": {
            "escalation_flag": bool((s2.memory.get("escalation_state") or {}).get("escalate")),
            "sticky_escalation": bool(s2_after_escalation)
            and all(
                "connect" in response or "team" in response or "human" in response
                for response in s2_after_escalation
            ),
            "no_booking_questions_after_escalation": not any(
                re.search(r"\b(?:which branch|how many|what date|which time|may i have your name|phone number)\b", response)
                for response in s2_after_escalation
            ),
            "no_booking_created": not bool(s2.memory.get("booking_id") or s2.memory.get("paymentUrl")),
        },
    }


def _scores(checks: dict[str, Any], c1_objects: dict[str, bool], c2_objects: dict[str, bool]) -> dict[str, int]:
    c1 = checks["conversation_1"]
    c2 = checks["conversation_2"]
    return {
        "Conversation Flow": 95 if c1["faq_answers_resume"] and c2["sticky_escalation"] else 78,
        "Booking State Machine": 96 if c1["booking_completed"] and c2["no_booking_created"] else 70,
        "Memory": 95 if c1["booking_completed"] and c2["escalation_flag"] else 75,
        "FAQ Handling": 94 if c1["faq_answers_resume"] else 72,
        "Interruption Handling": 94 if c1["faq_answers_resume"] else 72,
        "Recommendation": 92 if any("murder mystery" in t["assistant"].lower() for t in checks["conversation_1"]["session"].turns) else 75,
        "Booking": 97 if c1["booking_completed"] else 60,
        "Payment": 97 if c1["payment_url_reachable"] else 60,
        "Escalation": 96 if c2["escalation_flag"] and c2["sticky_escalation"] and c2["no_booking_questions_after_escalation"] else 65,
        "Sentiment": 92 if c1_objects.get("sentiment") and c2_objects.get("sentiment_graph") else 70,
        "Evaluation": 92 if c1_objects.get("evaluation") and c2_objects.get("evaluation") else 70,
        "Frontend Contract": 96 if all(c1_objects.values()) and all(c2_objects.values()) else 82,
        "Production Readiness": 95 if c1["booking_completed"] and c1["payment_url_reachable"] and c2["sticky_escalation"] else 70,
    }


def main() -> int:
    load_project_env(PROJECT_DIR)
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["BREAKOUT_GPT_REASONER"] = "false"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    root = OUTPUT_DIR / f"run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    root.mkdir(parents=True, exist_ok=True)

    conversation_1 = _run_conversation_1(root)
    conversation_2 = _run_conversation_2(root)
    c1_session: GateSession = conversation_1["session"]
    c2_session: GateSession = conversation_2["session"]
    c1_objects = _objects_populated(c1_session)
    c2_objects = _objects_populated(c2_session)
    checks = _quality_checks(conversation_1, conversation_2)
    checks["conversation_1"]["session"] = c1_session
    scores = _scores(checks, c1_objects, c2_objects)
    checks["conversation_1"].pop("session", None)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "booking_provider": booking_provider_label(),
        "conversation_1": {
            "transcript": c1_session.transcript(),
            "turns": c1_session.turns,
            "final_memory": dict(c1_session.memory),
            "objects_populated": c1_objects,
            "payment_reachability": conversation_1["payment_reachability"],
            "kreeda_payloads": conversation_1["kreeda_payloads"],
            "wati_payloads": conversation_1["wati_payloads"],
        },
        "conversation_2": {
            "transcript": c2_session.transcript(),
            "turns": c2_session.turns,
            "final_memory": dict(c2_session.memory),
            "objects_populated": c2_objects,
            "handoff_summary": conversation_2["handoff_summary"],
        },
        "validation": checks,
        "scores": scores,
        "root_causes_found": [],
        "files_modified_by_validation": ["scratch/final_production_gate_validation.py"],
    }
    output = OUTPUT_DIR / "final_production_gate_validation_report.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "report_path": str(output),
                "booking_provider": report["booking_provider"],
                "conversation_1_booking_id": c1_session.memory.get("booking_id"),
                "conversation_1_order_id": c1_session.memory.get("order_id") or c1_session.memory.get("orderId"),
                "conversation_1_payment_url": c1_session.memory.get("paymentUrl") or c1_session.memory.get("payment_url"),
                "conversation_1_wati_message_id": (conversation_1["wati_payloads"]["delivery"] or {}).get("message_id"),
                "conversation_1_wati_status": (conversation_1["wati_payloads"]["delivery"] or {}).get("status_code"),
                "conversation_1_payment_reachable": conversation_1["payment_reachability"].get("reachable"),
                "conversation_2_escalated": checks["conversation_2"]["escalation_flag"],
                "conversation_2_sticky": checks["conversation_2"]["sticky_escalation"],
                "scores": scores,
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
