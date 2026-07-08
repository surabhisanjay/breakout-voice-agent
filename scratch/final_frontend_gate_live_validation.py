#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from scratch.final_production_gate_validation import (  # noqa: E402
    CUSTOMER_NAME,
    CUSTOMER_PHONE,
    GateSession,
    _latest_booking_result,
    _payment_url_reachable,
)
from src.config.env_loader import booking_provider_label, load_project_env  # noqa: E402


OUTPUT_DIR = PROJECT_DIR / "scratch" / "final_frontend_gate_live_validation"

DIRECT_TURNS = [
    "Hi, we are four adults visiting Whitefield tomorrow around 2 PM, first time, and I want to book an escape room.",
    "What would YOU choose if you were me?",
    "Actually, make it something harder, not too easy and not horror.",
    "Is parking available?",
    "Do you have discounts for 4 players?",
    "no no, Murder Mystery",
    "Yes, book Murder Mystery.",
    "1:50 PM",
    CUSTOMER_NAME,
    CUSTOMER_PHONE,
]


def _payment_url(memory: dict[str, Any]) -> str:
    return str(
        memory.get("paymentUrl")
        or memory.get("payment_url")
        or memory.get("payment_link")
        or ""
    )


def run_direct() -> dict[str, Any]:
    load_project_env(PROJECT_DIR)
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["BREAKOUT_GPT_REASONER"] = "false"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session = GateSession(
        f"final_direct_{run_id}",
        OUTPUT_DIR / f"direct_session_{run_id}.json",
    )
    for turn in DIRECT_TURNS:
        session.say(turn)

    payment_url = _payment_url(session.memory)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": booking_provider_label(),
        "turns_requested": DIRECT_TURNS,
        "transcript": session.transcript(),
        "turns": session.turns,
        "final_memory": dict(session.memory),
        "booking_result": _latest_booking_result(session),
        "booking_id": session.memory.get("booking_id", ""),
        "order_id": session.memory.get("order_id") or session.memory.get("orderId") or "",
        "payment_url": payment_url,
        "payment_reachability": _payment_url_reachable(payment_url),
        "wati_delivery": session.memory.get("whatsapp_delivery") or {},
        "whatsapp_payload": session.memory.get("whatsapp_payload") or {},
        "price_breakdown": session.memory.get("price_breakdown") or {},
    }
    output = OUTPUT_DIR / f"direct_live_{run_id}.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    latest = OUTPUT_DIR / "direct_live_latest.json"
    latest.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "report_path": str(output),
                "provider": report["provider"],
                "booking_id": report["booking_id"],
                "order_id": report["order_id"],
                "payment_url": report["payment_url"],
                "payment_reachable": report["payment_reachability"].get("reachable"),
                "wati_message_id": report["wati_delivery"].get("message_id"),
                "wati_status": report["wati_delivery"].get("status_code"),
                "price_breakdown": report["price_breakdown"],
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )
    return report


if __name__ == "__main__":
    run_direct()
