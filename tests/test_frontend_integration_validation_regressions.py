from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from main import build_inbound_agent, dispatch
from src.memory.conversation_memory import ConversationMemory


def _agent(tmp_path: Path, monkeypatch, *, no_openai: bool = True):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("BREAKOUT_GPT_REASONER", "false")
    monkeypatch.setenv("BOOKING_PROVIDER", "simulator")
    memory = ConversationMemory(tmp_path / "session.json")
    return build_inbound_agent(Namespace(model=None, no_openai=no_openai), memory)


def test_restroom_mid_game_faq_answers_and_resumes_before_booking_handoff(tmp_path: Path, monkeypatch) -> None:
    agent = _agent(tmp_path, monkeypatch, no_openai=False)
    booking = None
    active = "inbound_agent"

    result, booking, active = dispatch(
        "Hi, I'd like to book an escape room for 4 adults. We're first timers, what would you recommend?",
        agent,
        booking,
        active,
    )
    result, booking, active = dispatch("Whitefield works.", agent, booking, active)
    result, booking, active = dispatch("What if someone needs the restroom mid-game?", agent, booking, active)

    assert "restroom" in result.response.lower()
    assert "staff" in result.response.lower()
    assert "murder mystery" in result.response.lower()
    assert "book" in result.response.lower() or "choose another room" in result.response.lower()
    assert agent.memory.data["participants"] == 4
    assert agent.memory.data["location"] == "Whitefield"
    assert agent.memory.data.get("booking_id", "") == ""


def test_short_recommendation_preferences_do_not_fall_into_unclear_repair(tmp_path: Path, monkeypatch) -> None:
    agent = _agent(tmp_path, monkeypatch)
    booking = None
    active = "inbound_agent"

    result, booking, active = dispatch("What would you choose for 4 adults?", agent, booking, active)
    assert "murder mystery" in result.response.lower()

    hard, booking, active = dispatch("Hard.", agent, booking, active)
    adventure, booking, active = dispatch("Adventure.", agent, booking, active)

    combined = f"{hard.response} {adventure.response}".lower()
    assert "didn't catch" not in combined
    assert "connect you with our team" not in combined
    assert "challenge" in hard.response.lower() or "hard" in hard.response.lower()
    assert "adventure" in adventure.response.lower() or "mission" in adventure.response.lower()
    assert agent.memory.data.get("recommended_option")
    assert agent.memory.data.get("booking_id", "") == ""


def test_outside_food_faq_uses_policy_answer_not_menu_options(tmp_path: Path, monkeypatch) -> None:
    agent = _agent(tmp_path, monkeypatch)
    booking = None
    active = "inbound_agent"

    result, booking, active = dispatch("Can we bring outside food?", agent, booking, active)

    lowered = result.response.lower()
    assert "outside food" in lowered
    assert "confirm" in lowered
    assert "continental food" not in lowered
    assert "snack boxes" not in lowered
