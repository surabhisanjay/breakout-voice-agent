from argparse import Namespace
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from main import build_inbound_agent, dispatch
from src.memory.conversation_memory import ConversationMemory
from src.agents.escalation_agent import EscalationAgent

def test_out_of_context_question_escalation(tmp_path: Path, monkeypatch) -> None:
    # 1. Disable test-mode short-circuits to check real name & notes collection
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    
    # 2. Initialize a new session memory with the customer's phone number (caller ID)
    memory = ConversationMemory(tmp_path / "out_of_context.json")
    memory.data["phone"] = "8217008407"
    memory.data["has_phone"] = True
    memory.save()
    
    # 3. Build InboundAgent with no openai key to ensure fallback mode behavior
    inbound = build_inbound_agent(Namespace(model=None, no_openai=True), memory)
    booking = None
    active = "inbound_agent"
    
    # scenario turn 0: Customer asks an out-of-context question
    # This is a knowledge gap and triggers an escalation. Since we have their phone, it asks for their name.
    final, booking, active = dispatch(
        "What is the weather like in Bangalore today?",
        inbound,
        booking,
        active
    )
    
    assert final.escalation["escalate"] is True
    assert final.escalation["reason"] == "Agent could not answer the customer query"
    assert final.escalation["trigger"] == "knowledge_gap"
    assert final.next_agent == "inbound_agent"
    assert "name" in final.response.lower()
    
    # scenario turn 1: Customer provides the name Surabhi
    final, booking, active = dispatch(
        "Surabhi",
        inbound,
        booking,
        active
    )
    
    # Since they provided their name, it should now ask for additional notes
    assert inbound.memory.data.get("customer_name") == "Surabhi"
    assert inbound.memory.data.get("escalation_notes_pending") is True
    assert "anything else" in final.response.lower()
    
    # scenario turn 2: Customer provides additional information / notes
    # Since we are running in production mode, it will prompt for WhatsApp follow-up consent.
    final, booking, active = dispatch(
        "No, just connect me please.",
        inbound,
        booking,
        active
    )
    
    assert inbound.memory.data.get("whatsapp_followup_consent_pending") is True
    assert "whatsapp" in final.response.lower()
    assert final.next_agent == "inbound_agent"
    
    # scenario turn 3: Customer responds with yes (same number)
    final, booking, active = dispatch(
        "Yes, this number is fine",
        inbound,
        booking,
        active
    )
    
    # Escalation should now be completed and routed to the escalation agent
    assert final.next_agent == "escalation_agent"
    assert final.should_handoff is True
    assert inbound.memory.data.get("escalation_requested") is False
    assert inbound.memory.data.get("escalation_notes") == "No, just connect me please."
    assert "reach out to you shortly" in final.response.lower()


def test_repeated_out_of_context_loops(tmp_path: Path) -> None:
    # Test that repeated out of context questions trigger repeated loop escalation reason
    memory = ConversationMemory(tmp_path / "repeated_out_of_context.json")
    inbound = build_inbound_agent(Namespace(model=None, no_openai=True), memory)
    booking = None
    active = "inbound_agent"
    
    # Send out of context questions 3 times
    for _ in range(3):
        final, booking, active = dispatch(
            "Can you tell me how to bake a chocolate cake?",
            inbound,
            booking,
            active
        )
        
    assert final.escalation["escalate"] is True
    assert final.escalation["reason"] == "Repeated conversation loop detected"
    assert final.next_agent == "escalation_agent"


def test_escalation_whatsapp_consent_flow(tmp_path: Path, monkeypatch) -> None:
    # Disable test-mode short-circuits to check real follow-up prompt
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    
    memory = ConversationMemory(tmp_path / "escalation_whatsapp.json")
    memory.data["phone"] = "8217008407"
    memory.data["has_phone"] = True
    memory.save()
    
    inbound = build_inbound_agent(Namespace(model=None, no_openai=True), memory)
    booking = None
    active = "inbound_agent"
    
    # 1. Trigger escalation
    final, booking, active = dispatch(
        "I need to speak to a human representative",
        inbound,
        booking,
        active
    )
    assert final.escalation["escalate"] is True
    
    # 2. Provide name
    final, booking, active = dispatch(
        "Surabhi",
        inbound,
        booking,
        active
    )
    
    # 3. Provide notes
    final, booking, active = dispatch(
        "No, just connect me",
        inbound,
        booking,
        active
    )
    
    # Since we are escalating, it should now prompt for WhatsApp follow-up consent!
    assert inbound.memory.data.get("whatsapp_followup_consent_pending") is True
    assert "whatsapp" in final.response.lower()
    assert final.next_agent == "inbound_agent"
    
    # 4. User responds with yes (same number)
    final, booking, active = dispatch(
        "Yes, this number is fine",
        inbound,
        booking,
        active
    )
    
    # It should restore the goodbye/handoff message and handoff to escalation_agent!
    assert inbound.memory.data.get("whatsapp_followup_consent") is True
    assert inbound.memory.data.get("whatsapp_followup_phone") == "8217008407"
    assert final.next_agent == "escalation_agent"
    assert final.should_handoff is True
    assert "reach out to you shortly" in final.response.lower()
