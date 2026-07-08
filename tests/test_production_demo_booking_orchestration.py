from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import MagicMock

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from main import dispatch
from src.agents.booking_agent import BookingAgent
from src.agents.inbound_agent import InboundAgent
from src.knowledge.knowledge_loader import KnowledgeLoader
from src.memory.conversation_memory import ConversationMemory


@pytest.fixture(autouse=True)
def simulator_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("BOOKING_PROVIDER", "simulator")
    monkeypatch.setenv("BOOKING_API_KEY", "")
    monkeypatch.setenv("BOOKING_BASE_URL", "")
    monkeypatch.delenv("WATI_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("WATI_API_KEY", raising=False)
    monkeypatch.delenv("WATI_BASE_URL", raising=False)


def _agent(tmp_path: Path, name: str) -> InboundAgent:
    return InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=ConversationMemory(tmp_path / f"{name}.json"),
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


def _run(agent: InboundAgent, turns: list[str]) -> tuple[list[str], object, str]:
    booking = None
    active = "inbound_agent"
    responses: list[str] = []
    for turn in turns:
        result, booking, active = dispatch(turn, agent, booking, active)
        responses.append(result.response)
    return responses, booking, active


def _reserved_booking_agent(tmp_path: Path, name: str = "payment_status") -> BookingAgent:
    memory = ConversationMemory(tmp_path / f"{name}.json")
    memory.data.update(
        {
            "booking_id": "bk_123",
            "booking_ref": "or_123",
            "booking_order_id": "or_123",
            "order_id": "or_123",
            "venue_id": "venue_123",
            "venueId": "venue_123",
            "booking_status": "RESERVED",
            "bookingStatus": "RESERVED",
            "payment_status": "UNPAID",
            "paymentStatus": "UNPAID",
            "payment_link": "https://pay.example/order?pr=true",
            "paymentUrl": "https://pay.example/order?pr=true",
            "selected_slot": "5:20 PM",
            "location": "Whitefield",
            "preferred_date": "Tomorrow",
            "participants": 4,
            "age_group": "adults",
            "customer_name": "Siddharth Khandelwal",
            "phone": "9982151357",
        }
    )
    memory.save()
    agent = BookingAgent(memory)
    agent._available_slots = ["5:20 PM"]
    agent._last_availability = {"available": True, "verified": True, "slots": ["5:20 PM"]}
    return agent


def test_payment_status_polling_confirms_paid_booking(tmp_path: Path) -> None:
    agent = _reserved_booking_agent(tmp_path)
    agent.orchestrator.check_payment_status = MagicMock(
        return_value={
            "bookingId": "bk_123",
            "orderId": "or_123",
            "status": "CONFIRMED",
            "isPaid": True,
            "totals": {"paid": 2500, "due": 0},
        }
    )

    result = agent.handle_message("I paid, did it go through?")

    agent.orchestrator.check_payment_status.assert_called_once_with("venue_123", "bk_123")
    assert agent.memory.data["bookingStatus"] == "CONFIRMED"
    assert agent.memory.data["paymentStatus"] == "PAID"
    assert "payment has been received" in result.response.lower()


def test_payment_status_expiry_clears_stale_slot(tmp_path: Path) -> None:
    agent = _reserved_booking_agent(tmp_path, "payment_expired")
    agent.orchestrator.check_payment_status = MagicMock(
        return_value={
            "bookingId": "bk_123",
            "orderId": "or_123",
            "status": "EXPIRED",
            "isPaid": False,
            "totals": {"paid": 0, "due": 2500},
        }
    )

    result = agent.handle_message("Has payment completed?")

    assert agent.memory.data["bookingStatus"] == "EXPIRED"
    assert agent.memory.data["paymentStatus"] == "EXPIRED"
    assert agent.memory.data["selected_slot"] == ""
    assert agent._available_slots == []
    assert "reservation has been released automatically" in result.response.lower()


def test_booking_creates_after_out_of_order_name_phone_age(tmp_path: Path) -> None:
    agent = _agent(tmp_path, "out_of_order")

    responses, booking, active = _run(
        agent,
        [
            "Book Murder Mystery for 4 people",
            "Whitefield",
            "Tomorrow",
            "Around 2 PM",
            "5:20 PM",
            "Siddharth Khandelwal",
            "9982151357",
            "adults",
        ],
    )

    memory = agent.memory.data
    assert active == "booking_agent"
    assert booking is not None
    assert memory["selected_slot"] == "5:20 PM"
    assert memory["phone"] == "9982151357"
    assert memory["age_group"] == "adults"
    assert memory["booking_id"]
    assert memory["payment_link"]
    assert memory["booking_status"] == "RESERVED"
    assert memory["bookingStatus"] == "RESERVED"
    assert memory["paymentStatus"] == "UNPAID"
    assert memory["current_workflow"] == "BOOKING_PENDING_PAYMENT"
    assert "reserved your slot" in responses[-1].lower()


def test_slot_selection_reuses_cached_availability_and_persists_slot(tmp_path: Path) -> None:
    agent = _agent(tmp_path, "cached_availability")
    _run(agent, ["Book Hostage for 6 adults", "JP Nagar", "Tomorrow", "Evening"])
    booking_agent = None
    active = "booking_agent"
    result, booking_agent, active = dispatch("7 PM", agent, booking_agent, active)

    assert agent.memory.data["selected_slot"] == "7:00 PM"
    assert "shall i check availability" not in result.response.lower()
    assert "check availability" not in result.response.lower()


def test_payment_link_request_resends_when_booking_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _agent(tmp_path, "payment_resend")
    _run(
        agent,
        [
            "Book Murder Mystery for 4 adults",
            "Whitefield",
            "Tomorrow evening",
            "7:40 PM",
            "Siddharth Khandelwal",
            "9982151357",
        ],
    )
    assert agent.memory.data["booking_id"]
    monkeypatch.setattr(
        "src.agents.booking_agent.WatiClient.send_booking_payment_link",
        MagicMock(return_value=type("Result", (), {"to_dict": lambda self: {"attempted": True, "sent": True, "provider": "WATI"}, "sent": True})()),
    )

    result, _booking, _active = dispatch(
        "I didn't receive the payment link",
        agent,
        None,
        "booking_agent",
    )

    assert agent.memory.data["payment_link"] in result.response
    assert agent.memory.data["booking_id"] in result.response
    assert "resent" in result.response.lower()


def test_payment_link_request_without_booking_continues_saved_booking(tmp_path: Path) -> None:
    agent = _agent(tmp_path, "payment_without_booking")
    _run(agent, ["Book Murder Mystery for 4 adults", "Whitefield", "Tomorrow evening"])

    result, _booking, _active = dispatch(
        "I didn't receive the payment link",
        agent,
        None,
        "booking_agent",
    )

    assert not agent.memory.data["booking_id"]
    assert "no payment link was generated" in result.response.lower()
    assert "slot" in result.response.lower() or "time" in result.response.lower()


def test_phone_number_is_not_mutated_by_parser(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "phone.json")
    assert memory._extract_phone("9982151357") == "9982151357"
    assert memory._extract_phone("Phone number is 9982151357") == "9982151357"
    assert memory._extract_phone("send it to 9982151357 please") == "9982151357"


@pytest.mark.parametrize("index", range(50))
def test_50_booking_conversations_reach_pending_payment(tmp_path: Path, index: int) -> None:
    people = [2, 3, 4, 5, 6][index % 5]
    location = ["Whitefield", "JP Nagar"][index % 2]
    room = "Murder Mystery" if location == "Whitefield" else "Hostage"
    slot = "7:40 PM" if location == "Whitefield" else "7:00 PM"
    opener = (
        f"We are {people} people and want a recommendation"
        if index % 3 == 0
        else f"Book {room} for {people} people"
    )
    turns = [
        opener,
        "First time" if index % 4 == 0 else "We have played before",
        location,
        f"Let's do {room}",
        "Is parking available?" if index % 7 == 0 else "What food options do you have?",
        "Tomorrow evening",
        slot,
    ]
    if index % 2 == 0:
        turns.extend(["Siddharth Khandelwal", "9982151357", "adults"])
    else:
        turns.extend(["adults", "Siddharth Khandelwal", "9982151357"])

    agent = _agent(tmp_path, f"matrix_{index}")
    responses, booking, active = _run(agent, turns)
    memory = agent.memory.data

    assert active == "booking_agent"
    assert booking is not None, (index, responses)
    assert memory["participants"] == people
    assert memory["location"] == location
    assert memory["room"] == room
    assert memory["selected_slot"] == slot
    assert memory["customer_name"] == "Siddharth Khandelwal"
    assert memory["phone"] == "9982151357"
    assert memory["age_group"] == "adults"
    assert memory["booking_id"]
    assert memory["booking_ref"]
    assert memory["payment_link"]
    assert memory["booking_status"] == "RESERVED"
    assert memory["bookingStatus"] == "RESERVED"
    assert memory["paymentStatus"] == "UNPAID"
    assert memory["current_workflow"] == "BOOKING_PENDING_PAYMENT"
    assert sum("reserved your slot" in response.lower() for response in responses) == 1
