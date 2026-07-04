from __future__ import annotations

from pathlib import Path
import pytest

from src.core.agent_response import AgentResponse
from src.memory.conversation_memory import ConversationMemory
from src.agents.inbound_agent import InboundAgent
from src.knowledge.knowledge_loader import KnowledgeLoader
from main import dispatch, _process_whatsapp_followup_flow

PROJECT_DIR = Path(__file__).resolve().parents[1]

def make_agent(tmp_path: Path, memory: ConversationMemory) -> InboundAgent:
    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    return InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


def test_whatsapp_followup_consent_same_number(tmp_path: Path) -> None:
    # Set up memory
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update({
        "phone": "9876543210",
        "customer_name": "Riya Patel",
        "completed_booking": True
    })
    memory.save()

    inbound = make_agent(tmp_path, memory)

    # 1. Trigger goodbye turn
    goodbye_res = AgentResponse(
        response="Great. Thanks for choosing Breakout. Have a wonderful day.",
        intent="general_faq",
        next_agent="inbound_agent",
        should_handoff=False,
    )

    # Process outgoing flow (simulating dispatch return wrapping)
    res = _process_whatsapp_followup_flow("bye", inbound, goodbye_res)
    assert "is your name riya patel" in res.response.lower()
    assert memory.data.get("whatsapp_followup_name_confirm_pending") is True

    # 2. Simulate next turn: user confirms name
    res, _, _ = dispatch("Yes, that's right", inbound, None, "inbound_agent")
    assert "Would you like us to send follow-up details via WhatsApp" in res.response
    assert memory.data.get("whatsapp_followup_consent_pending") is True

    # 3. Simulate next turn: user says "yes, same number"
    response, _, _ = dispatch("yes, same number", inbound, None, "inbound_agent")
    
    # Assert it recovers original goodbye response and sets consent and phone number
    assert "Great. Thanks for choosing Breakout. Have a wonderful day." in response.response
    assert memory.data.get("whatsapp_followup_consent") is True
    assert memory.data.get("whatsapp_followup_phone") == "9876543210"
    assert memory.data.get("whatsapp_followup_consent_pending") is False


def test_whatsapp_followup_consent_different_number(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session2.json")
    memory.data.update({
        "phone": "9876543210",
        "customer_name": "Riya Patel",
        "completed_booking": True
    })
    memory.save()

    inbound = make_agent(tmp_path, memory)

    # 1. Trigger goodbye
    goodbye_res = AgentResponse(
        response="Great. Thanks for choosing Breakout. Have a wonderful day.",
        intent="general_faq",
        next_agent="inbound_agent",
        should_handoff=False,
    )
    res = _process_whatsapp_followup_flow("bye", inbound, goodbye_res)
    assert "is your name riya patel" in res.response.lower()

    # 2. Confirm name
    res, _, _ = dispatch("yes", inbound, None, "inbound_agent")
    assert "Would you like us to send follow-up details via WhatsApp" in res.response
    assert memory.data.get("whatsapp_followup_consent_pending") is True

    # 3. Simulate next turn: user says "send it to a different number"
    res, _, _ = dispatch("send it to a different number", inbound, None, "inbound_agent")
    assert "What is the phone number" in res.response
    assert memory.data.get("whatsapp_followup_phone_pending") is True

    # 4. Simulate next turn: user gives the number
    res, _, _ = dispatch("nine eight seven six five four three two one zero", inbound, None, "inbound_agent")
    assert "Great. Thanks for choosing Breakout. Have a wonderful day." in res.response
    assert memory.data.get("whatsapp_followup_consent") is True
    assert memory.data.get("whatsapp_followup_phone") == "9876543210"
    assert memory.data.get("whatsapp_followup_phone_pending") is False


def test_whatsapp_followup_consent_declined(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session3.json")
    memory.data.update({
        "phone": "9876543210",
        "customer_name": "Riya Patel",
        "completed_booking": True
    })
    memory.save()

    inbound = make_agent(tmp_path, memory)

    # 1. Trigger goodbye
    goodbye_res = AgentResponse(
        response="Great. Thanks for choosing Breakout. Have a wonderful day.",
        intent="general_faq",
        next_agent="inbound_agent",
        should_handoff=False,
    )
    res = _process_whatsapp_followup_flow("bye", inbound, goodbye_res)
    assert "is your name riya patel" in res.response.lower()

    # 2. Confirm name
    res, _, _ = dispatch("yes", inbound, None, "inbound_agent")
    assert "Would you like us to send follow-up details via WhatsApp" in res.response

    # 3. Simulate next turn: user says "no thanks"
    res, _, _ = dispatch("no thanks", inbound, None, "inbound_agent")
    assert "Great. Thanks for choosing Breakout. Have a wonderful day." in res.response
    assert memory.data.get("whatsapp_followup_consent") is False


def test_format_whatsapp_followup() -> None:
    from app import format_whatsapp_followup
    
    # 1. Standard (non-escalated) format
    data_standard = {
        "customer_name": "Surabhi",
        "room": "Museum Heist",
        "location": "Bangalore",
        "participants": "5",
        "preferred_date": "2026-07-05",
        "preferred_time": "17:00"
    }
    msg_standard = format_whatsapp_followup(data_standard)
    assert "Thanks for calling Breakout!" in msg_standard
    assert "We'll connect you with someone shortly" not in msg_standard
    assert "- *Theme/Room*: Museum Heist" in msg_standard
    assert "- *Location*: Bangalore" in msg_standard
    assert "Status" not in msg_standard
    
    # 2. Escalated format
    data_escalated = {
        "customer_name": "Surabhi",
        "room": "Museum Heist",
        "location": "Bangalore",
        "participants": "5",
        "preferred_date": "2026-07-05",
        "preferred_time": "17:00",
        "escalation_state": {
            "escalate": True
        }
    }
    msg_escalated = format_whatsapp_followup(data_escalated)
    assert "Thanks for calling Breakout!" in msg_escalated
    assert "We'll connect you with someone shortly." in msg_escalated
    assert "- *Theme/Room*: Museum Heist" in msg_escalated
    assert "Status" not in msg_escalated


def test_whatsapp_followup_consent_topic_switching(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session_switch.json")
    memory.data.update({
        "phone": "9876543210",
        "customer_name": "Riya Patel",
        "completed_booking": True
    })
    memory.save()

    inbound = make_agent(tmp_path, memory)

    # 1. Trigger goodbye
    goodbye_res = AgentResponse(
        response="Great. Thanks for choosing Breakout. Have a wonderful day.",
        intent="general_faq",
        next_agent="inbound_agent",
        should_handoff=False,
    )
    res = _process_whatsapp_followup_flow("bye", inbound, goodbye_res)
    assert "is your name riya patel" in res.response.lower()
    assert memory.data.get("whatsapp_followup_name_confirm_pending") is True

    # 2. User switches to asking about booking instead of confirming name
    res, _, _ = dispatch("what games do you have?", inbound, None, "inbound_agent")
    
    # Assert escalation state has been cleared and it routes to normal agent query
    assert memory.data.get("whatsapp_followup_name_confirm_pending") is False
    assert memory.data.get("escalation_requested") is False
    # Check that it answers the question instead of confirmation question
    assert "is your name" not in res.response.lower()
    assert len(res.response) > 0
