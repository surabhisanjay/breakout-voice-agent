from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from integrations.langgraph_booking_node import run_booking_workflow
from src.booking_agent import BookingSimulator as BookingAgent, BookingError


def test_run_booking_workflow_basic(tmp_path: Path) -> None:
    agent = BookingAgent()
    agent.add_slot("2026-07-01", "18:00", "birthday_party", capacity=10)
    handoff = {
        "customer_name": "Anita",
        "phone": "9876543210",
        "participants": 4,
        "intent": "birthday_party",
        "preferred_date": "2026-07-01",
    }

    result = run_booking_workflow(handoff, require_payment=False, agent=agent)

    assert result["status"] == "booked"
    assert result["booking_ref"]
    assert result["payment_required"] is False
    assert result["error"] is None
    assert isinstance(result["log"], list)


def test_run_booking_workflow_missing_fields_fails() -> None:
    handoff = {
        "customer_name": "Anita",
        "participants": 4,
        "intent": "birthday_party",
    }
    result = run_booking_workflow(handoff, require_payment=False, agent=BookingAgent())

    assert result["status"] == "failed"
    assert "missing_required_fields" in result["error"]
    assert result["booking_ref"] is None


def test_run_booking_workflow_idempotent_already_booked(monkeypatch: Any) -> None:
    agent = BookingAgent()
    slot_id = agent.add_slot("2026-07-01", "18:00", "escape_room_inquiry", capacity=10)
    handoff = {
        "customer_name": "Anita",
        "phone": "9876543210",
        "participants": 3,
        "intent": "escape_room_inquiry",
        "preferred_date": "2026-07-01",
    }

    original_create = agent.create_booking
    calls = {"count": 0}

    def flaky_create(slot_id_arg: str, customer: dict, require_payment: bool = False):
        calls["count"] += 1
        if calls["count"] == 1:
            raise BookingError("already_booked")
        return original_create(slot_id_arg, customer, require_payment=require_payment)

    monkeypatch.setattr(agent, "create_booking", flaky_create)

    result = run_booking_workflow(handoff, require_payment=False, agent=agent)

    assert result["status"] == "booked"
    assert result["booking_ref"]
    assert calls["count"] >= 2
