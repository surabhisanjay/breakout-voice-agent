from __future__ import annotations

import os
from pathlib import Path
from argparse import Namespace
from main import dispatch, build_inbound_agent
from src.core.booking_provider import SimulatorProvider
from src.orchestration.booking_orchestrator import BookingOrchestrator
from src.memory.conversation_memory import ConversationMemory
from src.core.agent_response import AgentResponse

def test_payment_status_check_in_simulator(tmp_path: Path) -> None:
    provider = SimulatorProvider()
    booking_id = "BK-test-123"
    
    # Check unpaid status
    status = provider.check_payment_status("venue-1", booking_id)
    assert status["isPaid"] is False
    assert status["totals"]["due"] == 1000
    assert status["totals"]["paid"] == 0

    # Mock paid status
    provider.bookings[booking_id] = {"status": "confirmed", "isPaid": True}
    status_paid = provider.check_payment_status("venue-1", booking_id)
    assert status_paid["isPaid"] is True
    assert status_paid["totals"]["due"] == 0
    assert status_paid["totals"]["paid"] == 1000

def test_booking_orchestrator_check_payment(tmp_path: Path) -> None:
    orchestrator = BookingOrchestrator()
    orchestrator.is_live = False
    booking_id = "BK-orch-456"
    
    # Check unpaid status
    status = orchestrator.check_payment_status("venue-1", booking_id)
    assert status["isPaid"] is False
    
    # Make it paid in simulator
    orchestrator.simulator.bookings[booking_id] = {"status": "confirmed", "isPaid": True}
    status_paid = orchestrator.check_payment_status("venue-1", booking_id)
    assert status_paid["isPaid"] is True

def test_dispatch_intercepts_payment_pending_and_auto_confirms(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "payment_session.json")
    memory.data.update({
        "booking_id": "BK-dispatch-789",
        "booking_status": "PAYMENT_PENDING",
        "location": "Whitefield",
        "intent": "escape_room_inquiry"
    })
    memory.save()
    
    inbound = build_inbound_agent(Namespace(model=None, no_openai=True), memory)
    inbound.use_openai = False
    
    # Trigger dispatch turn while unpaid
    from src.agents.booking_agent import BookingAgent
    booking = BookingAgent(memory)
    booking.orchestrator.is_live = False
    
    # 1. First turn is unpaid
    result, booking_out, active_out = dispatch("Is my booking ready?", inbound, booking, "booking_agent")
    # Should not confirm yet, since isPaid is False
    assert memory.data["booking_status"] == "PAYMENT_PENDING"
    assert "payment went through" not in result.response.lower()

    # 2. Make it paid in the orchestrator simulator
    booking.orchestrator.simulator.bookings["BK-dispatch-789"] = {"status": "confirmed", "isPaid": True}
    
    result, booking_out, active_out = dispatch("Did you get the payment?", inbound, booking, "booking_agent")
    
    # Should confirm!
    assert memory.data["booking_status"] == "CONFIRMED"
    assert "payment went through" in result.response.lower()
    assert "booking is confirmed" in result.response.lower()
