#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from scratch.final_consolidated_live_validation import (  # noqa: E402
    OUTPUT_DIR,
    SLOT_RE,
    _console_summary,
    _no_booking_created,
    _run_scenario_1,
    _run_scenario_2,
    _run_scenario_3,
    _run_scenario_4,
    _run_scenario_5,
    _scenario_summary,
    _transcript,
)
from src.config.env_loader import load_project_env  # noqa: E402


def main() -> int:
    load_project_env(PROJECT_DIR)
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["BREAKOUT_GPT_REASONER"] = "false"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    root = OUTPUT_DIR / f"surgical_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    root.mkdir(parents=True, exist_ok=True)

    s1 = _run_scenario_1(root)
    s3 = _run_scenario_3(root)
    s2 = _run_scenario_2(root)
    s4 = _run_scenario_4(root)
    s5 = _run_scenario_5(root)

    scenario_1_session = s1["session"]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "live",
        "scope": "surgical_followup",
        "scenario_1": {
            **_scenario_summary("scenario_1", s1),
            "fact_changes_isolated": (
                scenario_1_session.memory.get("location") == "Koramangala"
                and scenario_1_session.memory.get("room") == "Murder Mystery"
                and int(scenario_1_session.memory.get("participants") or 0) == 4
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
        "scenario_3": {
            **_scenario_summary("scenario_3", s3),
            "name_collected": bool(s3["session"].memory.get("customer_name")),
            "no_booking_created": _no_booking_created(s3["session"]),
            "availability_turn": s3["availability_turn"],
            "narrowed_slots": SLOT_RE.findall(s3["availability_turn"]),
        },
        "quick_regression": {
            "scenario_2": {
                "name_collected": bool(s2["session"].memory.get("customer_name")),
                "no_booking_created": _no_booking_created(s2["session"]),
                "transcript": _transcript(s2["session"]),
            },
            "scenario_4": {
                "sticky_escalation": all(
                    (turn.get("after", {}).get("escalation_state") or {}).get("escalate")
                    for turn in s4["session"].turns[2:]
                ),
                "never_reached_name_or_booking": not s4["session"].memory.get("customer_name")
                and _no_booking_created(s4["session"]),
                "transcript": _transcript(s4["session"]),
            },
            "scenario_5": {
                "name_collected": bool(s5["session"].memory.get("customer_name")),
                "no_booking_created": _no_booking_created(s5["session"]),
                "transcript": _transcript(s5["session"]),
            },
        },
    }
    output = OUTPUT_DIR / "surgical_followup_validation_report.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps({"report_path": str(output), **_console_summary({
        "scenario_1": report["scenario_1"],
        "scenario_2": {
            "name_collected": report["quick_regression"]["scenario_2"]["name_collected"],
            "no_booking_created": report["quick_regression"]["scenario_2"]["no_booking_created"],
        },
        "scenario_3": report["scenario_3"],
        "scenario_4": {
            "sticky_escalation": report["quick_regression"]["scenario_4"]["sticky_escalation"],
            "never_reached_name_or_booking": report["quick_regression"]["scenario_4"]["never_reached_name_or_booking"],
        },
        "scenario_5": {
            "name_collected": report["quick_regression"]["scenario_5"]["name_collected"],
            "no_booking_created": report["quick_regression"]["scenario_5"]["no_booking_created"],
        },
        "cleanup": {"action": "see_scratch/orphan_bk_JUk7nv_cleanup.json", "resolved": True},
    })}, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
