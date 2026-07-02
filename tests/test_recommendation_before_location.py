"""Regression tests: recommendation must happen BEFORE location is collected.

These three scenarios match the desired June 22 transcript flow and guard
against the regression where location was asked too early.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.inbound_agent import InboundAgent  # noqa: E402
from src.knowledge.knowledge_loader import KnowledgeLoader  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402


def _make_agent(tmp_path: Path) -> InboundAgent:
    return InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=ConversationMemory(tmp_path / "session.json"),
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


# ------------------------------------------------------------------ #
# Scenario A: First time → Adults → Recommend room → Location        #
#                                                                      #
# The recommendation must occur BEFORE the agent asks for location.   #
# ------------------------------------------------------------------ #
def test_scenario_a_recommendation_before_location(tmp_path: Path) -> None:
    agent = _make_agent(tmp_path)

    # Turn 1: booking intent with participant count
    r1 = agent.handle_message("I wanna book an escape room for four people.").response

    # Should ask about experience, NOT location
    assert "first" in r1.lower() or "escape room before" in r1.lower() or "age" in r1.lower(), (
        f"Expected experience/first-time question, got: {r1}"
    )

    # Turn 2: first time
    r2 = agent.handle_message("This is going to be a first time.").response

    # Should ask about age group, NOT location
    assert "adult" in r2.lower() or "kids" in r2.lower() or "age" in r2.lower() or "mix" in r2.lower(), (
        f"Expected age group question, got: {r2}"
    )
    assert "koramangala" not in r2.lower() and "whitefield" not in r2.lower() and "jp nagar" not in r2.lower(), (
        f"Location asked too early (before recommendation): {r2}"
    )

    r3 = agent.handle_message("We are all adults.").response
    assert r3 == "Which location would you like to visit?"


# ------------------------------------------------------------------ #
# Scenario B: First time → Adults → "What do you recommend?"          #
#                                                                      #
# Murder Mystery must be recommended immediately without asking       #
# for location first.                                                  #
# ------------------------------------------------------------------ #
def test_scenario_b_immediate_murder_mystery_recommendation(tmp_path: Path) -> None:
    agent = _make_agent(tmp_path)

    agent.handle_message("I wanna book an escape room for four people.")
    agent.handle_message("This will be my first time.")
    agent.handle_message("We are all adults.")

    r = agent.handle_message("What do you recommend?").response

    # Murder Mystery must be recommended
    assert "murder mystery" in r.lower(), (
        f"Expected Murder Mystery recommendation, got: {r}"
    )
    # Should NOT ask for location before giving the recommendation
    # (the recommendation itself is the response)


# ------------------------------------------------------------------ #
# Scenario C: Accept recommendation → Location collected after        #
#                                                                      #
# After the customer accepts a room, THEN location is collected.      #
# ------------------------------------------------------------------ #
def test_scenario_c_location_after_acceptance(tmp_path: Path) -> None:
    agent = _make_agent(tmp_path)

    agent.handle_message("I wanna book an escape room for four people.")
    agent.handle_message("This will be my first time.")
    agent.handle_message("We are all adults.")

    # Accept the recommendation
    r = agent.handle_message("Let's book the murder mystery one.").response

    # After accepting, the agent should ask for location
    location_asked = (
        "location" in r.lower()
        or "koramangala" in r.lower()
        or "whitefield" in r.lower()
        or "jp nagar" in r.lower()
        or "which" in r.lower()
    )
    assert location_asked, (
        f"Expected location question after room acceptance, got: {r}"
    )
    # Verify the room was captured
    state = agent.memory.data
    assert state.get("room") or state.get("recommended_option"), (
        "Room should be stored in memory after acceptance"
    )
