from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.memory.conversation_memory import ConversationMemory
from src.agents.inbound_agent import InboundAgent
from src.agents.booking_agent import BookingAgent
from src.agents.escalation_agent import EscalationAgent, EscalationResult
from src.agents.sentiment_agent import SentimentResult
from src.orchestration.booking_orchestrator import BookingOrchestrator
from src.integrations.kreeda.breakout_booking_provider import BreakoutBookingProvider
from src.integrations.kreeda.agent_contract_provider import AgentContractProvider


def test_invalid_location_guidance(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "invalid_loc.json")
    memory.data.update({
        "intent": "birthday_party",
        "event_type": "Birthday Party",
        "participants": 6,
    })
    memory.save()

    # Create InboundAgent with manual qualification
    from src.knowledge.knowledge_loader import KnowledgeLoader
    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    agent = InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
        use_ollama=False,
    )

    # Trigger location recovery
    response = agent.handle_message("Let's do Malleswaram branch")
    assert "We don't have a branch in Malleswaram" in response.response
    assert "Koramangala, Whitefield, and JP Nagar" in response.response


def test_capacity_limitation_alternatives(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.delenv("BOOKING_PROVIDER", raising=False)

    provider = MagicMock(spec=BreakoutBookingProvider)
    provider.get_booking_venues.return_value = [{"venueId": "jp-1", "name": "JP Nagar"}]
    provider.get_booking_games.return_value = [
        {
            "gameId": "murder-1", "name": "Murder Mystery", "peopleMin": 2, "peopleMax": 7,
            "peopleCategories": [{"categoryId": "adult", "categoryName": "Adults", "max": 7}],
        },
        {
            "gameId": "missile-1", "name": "Missile Attack", "peopleMin": 4, "peopleMax": 12,
            "peopleCategories": [{"categoryId": "adult", "categoryName": "Adults", "max": 12}],
        }
    ]
    # search returns time slot, but capacity is full (available = 5, but we check for 10 players)
    provider.search_booking_slots.return_value = [{
        "eventId": "event-7pm", "gameId": "murder-1", "date": "2026-07-20",
        "time": "19:00", "available": 5, "isAvailable": True,
    }]

    orchestrator = BookingOrchestrator(
        booking_provider=provider,
        contract_provider=MagicMock(spec=AgentContractProvider),
    )

    memory = ConversationMemory(tmp_path / "capacity_alt.json")
    memory.data.update({
        "intent": "escape_room_inquiry", "event_type": "Escape Room",
        "participants": 10, "age_group": "adults", "location": "JP Nagar",
        "preferred_date": "2026-07-20", "room": "Murder Mystery",
        "preferred_time": "7:00 PM", "time_preference": "specific",
    })
    memory.save()

    agent = BookingAgent(memory, orchestrator=orchestrator)
    res = agent.handle_message("ready")
    
    assert "Missile Attack" in res.response
    assert "fit all 10 players" in res.response.lower()


def test_language_switch_escalation(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "lang.json")
    agent = EscalationAgent(memory)
    sentiment = SentimentResult("neutral", 0.9, False, "No anger", "discovery")

    res = agent.evaluate("Hi, can you speak in Hindi please?", sentiment)
    assert res.escalate is True
    assert "language" in res.reason.lower()


def test_policy_dispute_escalation(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "policy.json")
    agent = EscalationAgent(memory)
    sentiment = SentimentResult("neutral", 0.9, False, "No anger", "discovery")

    res = agent.evaluate("This cancellation policy is completely unfair, I refuse to pay this charge", sentiment)
    assert res.escalate is True
    assert "disputed" in res.reason.lower() or "policy" in res.reason.lower()
