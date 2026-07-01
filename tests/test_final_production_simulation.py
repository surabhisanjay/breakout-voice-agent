from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import random
import sys
from unittest.mock import MagicMock

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from main import build_inbound_agent, dispatch
from src.agents.booking_agent import BookingAgent
from src.memory.conversation_memory import ConversationMemory


@pytest.mark.parametrize("seed", range(20))
def test_random_booking_paths_have_no_loops_false_confirmation_or_memory_loss(
    tmp_path: Path, seed: int
) -> None:
    rng = random.Random(seed)
    memory = ConversationMemory(tmp_path / "simulation.json")
    memory.data.update({
        "intent": "escape_room_inquiry", "event_type": "Escape Room",
        "booking_started": True, "current_workflow": "booking",
        "participants": 4, "age_group": "adults", "location": "Whitefield",
        "preferred_date": "25 June", "room": "Murder Mystery",
    })
    memory.save()
    agent = BookingAgent(memory)
    agent.response_composer.enabled = False
    agent.availability_tool.check = MagicMock(return_value={
        "available": True, "verified": True, "capacity_supported": True,
        "slots": ["3:00 PM", "7:00 PM"],
    })
    agent.booking_tool.create = MagicMock(return_value={
        "booking_id": f"booking-{seed}", "booking_reference": f"reference-{seed}",
        "confirmed": True, "location": "Whitefield", "date": "25 June",
        "slot": "7:00 PM", "participants": 4, "event_type": "Escape Room",
        "customer_name": "Riya Patel", "phone": "9876543210",
    })

    responses = [agent.handle_message("ready").response]
    responses.append(agent.handle_message(rng.choice((
        "Is parking available?", "How long is the game?", "What food do you have?",
    ))).response)
    mutation = rng.choice(("participants", "date", "room", "none"))
    if mutation == "participants":
        responses.append(agent.handle_message("We are actually 5 people").response)
        assert memory.data["participants"] == 5
    elif mutation == "date":
        responses.append(agent.handle_message("Change the date to 26 June").response)
        assert memory.data["preferred_date"] == "26 June"
    elif mutation == "room":
        responses.append(agent.handle_message("Switch the room to Hostage").response)
        assert memory.data["room"] == "Hostage"

    responses.append(agent.handle_message("7 PM").response)
    responses.append(agent.handle_message("Riya Patel").response)
    final = agent.handle_message("9876543210")
    responses.append(final.response)

    assert memory.data["booking_id"] == f"booking-{seed}"
    assert memory.data["booking_ref"] == f"reference-{seed}"
    assert memory.data["customer_name"] == "Riya Patel"
    assert sum("booking is confirmed" in response.lower() for response in responses) == 1
    assert all(responses)


@pytest.mark.parametrize("message", (
    "I want a refund", "give me my money back", "I want a human",
    "connect me to an agent", "someone got injured", "my friend fainted",
))
def test_manager_interruptions_terminate_in_escalation(
    tmp_path: Path, message: str
) -> None:
    memory = ConversationMemory(tmp_path / "manager.json")
    inbound = build_inbound_agent(Namespace(model=None, no_openai=True), memory)
    result, _, _ = dispatch(message, inbound, None, "inbound_agent")
    assert result.next_agent == "escalation_agent"
    assert result.should_handoff is True
    assert result.handoff_summary


@pytest.mark.parametrize("intent,event_type", (
    ("corporate_event", "Corporate Event"),
    ("birthday_party", "Birthday Package"),
    ("bachelor_party", "Bachelor Party"),
    ("farewell_party", "Farewell Party"),
))
def test_event_simulations_end_in_coordination_without_fake_booking(
    tmp_path: Path, intent: str, event_type: str
) -> None:
    memory = ConversationMemory(tmp_path / "event.json")
    memory.data.update({
        "intent": intent, "event_type": event_type, "participants": 20,
        "company_size": 20 if intent == "corporate_event" else "",
        "location": "Whitefield", "preferred_date": "26 June",
        "customer_name": "Riya", "phone": "9876543210",
        "booking_started": True, "current_workflow": "booking",
    })
    memory.save()
    agent = BookingAgent(memory)
    agent.response_composer.enabled = False
    agent.availability_tool.check = MagicMock()

    result = agent.handle_message("ready")

    agent.availability_tool.check.assert_not_called()
    assert result.next_agent == "events_team"
    assert result.should_handoff is True
    assert not memory.data.get("booking_id")
    assert "confirmed" not in result.response.lower()
