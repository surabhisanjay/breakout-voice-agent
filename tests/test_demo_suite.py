"""
tests/test_demo_suite.py
=========================
10-scenario demo test suite.

Scenarios:
  1.  First-time group of 7 friends — memory extraction, recommendation, no re-ask
  2.  Couple date — Murder Mystery first, no Prison Break
  3.  Family with kids — age-appropriate recommendation
  4.  Corporate team outing — Scavenger Hunt / Escape Room combo
  5.  Running late — reassurance, state preserved
  6.  Explain Murder Mystery — natural explanation
  7.  Explain all rooms — all 6 rooms listed
  8.  What happens if we fail? — reassuring answer
  9.  Change room during booking — update memory, no restart
  10. Complete booking start-to-finish — full conversation
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from main import dispatch  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402
from src.agents.inbound_agent import InboundAgent  # noqa: E402
from src.knowledge.knowledge_loader import KnowledgeLoader  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────

class NoopComposer:
    """Passthrough composer — returns draft unchanged so tests are deterministic."""
    last_error = ""
    last_latency = 0.0
    system_prompt = "loaded"
    few_shot_examples = []

    def compose(self, draft: str, **kwargs: Any) -> str:
        return draft


def make_agent(tmp_path: Path) -> InboundAgent:
    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = ConversationMemory(tmp_path / "session.json")
    agent = InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
        use_ollama=False,
    )
    agent.response_composer = NoopComposer()
    return agent


def turn(agent: InboundAgent, message: str) -> str:
    """Send one message, return the agent response text."""
    booking_agent = None
    result, _, _ = dispatch(message, agent, booking_agent, "inbound_agent")
    return result.response


# ── scenario 1 — first-time group of 7 friends ─────────────────────────────

def test_scenario_1_first_time_friends(tmp_path: Path) -> None:
    """
    Customer reveals: 7 friends, first-timers.
    New flow: age_group question comes first, then recommendation
    after age_group is provided.
    """
    agent = make_agent(tmp_path)
    response = turn(agent, "We are seven friends and none of us has ever done an escape room before.")

    # New flow: age_group question comes first (no recommendation without age_group)
    assert "adult" in response.lower() or "kids" in response.lower() or "age" in response.lower(), (
        f"Expected age group question: {response}"
    )

    # Must not re-ask for participants
    assert "how many people" not in response.lower(), (
        f"Re-asked for participants already known to be 7: {response}"
    )

    # Memory must record participants = 7
    assert agent.memory.data.get("participants") == 7, (
        f"participants not stored: {agent.memory.data.get('participants')}"
    )


# ── scenario 2 — couple date ────────────────────────────────────────────────

def test_scenario_2_couple_date(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    response = turn(agent, "We are a couple planning a date night. What would you recommend?")

    assert "Murder Mystery" in response, f"Expected Murder Mystery for couple: {response}"
    assert "Prison Break" not in response, f"Prison Break should not appear for couple: {response}"
    assert "teamwork" in response.lower() or "investigation" in response.lower(), (
        f"Response should explain why Murder Mystery suits a couple: {response}"
    )


# ── scenario 3 — family with kids ──────────────────────────────────────────

def test_scenario_3_family_with_kids(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    response = turn(agent, "We have 5 children aged 11 who want to try an escape room.")

    assert "location" in response.lower(), (
        f"Expected location qualification before recommendation: {response}"
    )
    # Must not recommend Bomb Defusal or Classified for young kids
    assert "Bomb Defusal" not in response, f"Should not recommend Bomb Defusal for kids: {response}"
    assert "Classified" not in response, f"Should not recommend Classified for kids: {response}"


# ── scenario 4 — corporate team outing ─────────────────────────────────────

def test_scenario_4_corporate_team(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    response = turn(agent, "We are a team of 20 employees planning a team outing.")

    assert "Scavenger Hunt" in response or "Escape Room" in response, (
        f"Expected corporate activity recommendation: {response}"
    )
    assert "team" in response.lower() or "20" in response, (
        f"Response should acknowledge the team size: {response}"
    )


# ── scenario 5 — running late ───────────────────────────────────────────────

def test_scenario_5_running_late(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    # First turn: establish context
    turn(agent, "We are 4 friends booked for Koramangala at 3 PM.")
    # Second turn: interruption
    response = turn(agent, "We are running 20 minutes late.")

    assert "Don't worry" in response or "worry" in response.lower(), (
        f"Expected reassuring response for late arrival: {response}"
    )
    assert "back to back" in response.lower() or "scheduled time" in response.lower(), (
        f"Expected explanation about session timing: {response}"
    )
    # State must be preserved
    assert agent.memory.data.get("location") == "Koramangala", (
        f"Location should be preserved after interruption: {agent.memory.data}"
    )


# ── scenario 6 — explain Murder Mystery ────────────────────────────────────

def test_scenario_6_explain_murder_mystery(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    response = turn(agent, "Can you tell me more about Murder Mystery?")

    assert "Murder Mystery" in response, f"Expected Murder Mystery explanation: {response}"
    assert any(word in response.lower() for word in ("detective", "clue", "investigat", "mystery", "puzzle")), (
        f"Expected description of Murder Mystery: {response}"
    )


# ── scenario 7 — explain all rooms ─────────────────────────────────────────

def test_scenario_7_explain_all_rooms(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    response = turn(agent, "Can you explain all the rooms you have?")

    rooms = ["Murder Mystery", "Hostage", "Classified", "Bomb Defusal", "Prison Break", "Undercover"]
    missing = [r for r in rooms if r not in response]
    assert not missing, f"Missing rooms in response: {missing}\nResponse: {response}"


# ── scenario 8 — what happens if we fail ───────────────────────────────────

def test_scenario_8_what_happens_if_fail(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    response = turn(agent, "What happens if we fail to escape?")

    assert "not actually locked" in response.lower() or "game master" in response.lower(), (
        f"Expected reassuring failure answer: {response}"
    )
    # Should not make failure sound scary
    assert "locked forever" not in response.lower() or "won't keep" in response.lower(), (
        f"Response should reassure: {response}"
    )


# ── scenario 9 — change room during booking ────────────────────────────────

def test_scenario_9_room_change_during_booking(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    # Build qualification state
    agent.memory.data.update({
        "intent": "escape_room_inquiry",
        "participants": 4,
        "age_group": "adults",
        "location": "Koramangala",
        "preferred_date": "18 June",
        "recommended_option": "Murder Mystery or Hostage",
        "room": "Murder Mystery",
    })

    # Customer changes room preference
    response = turn(agent, "Actually, can we do Classified instead?")

    # Response should acknowledge the change
    assert "Classified" in response, f"Expected Classified acknowledged: {response}"

    # Should NOT restart qualification (name/phone should not be re-asked mid-flow
    # unless the agent has already moved to contact collection)
    assert "how many people" not in response.lower(), (
        f"Should not re-ask participants: {response}"
    )
    assert "how many will attend" not in response.lower(), (
        f"Should not re-ask participants: {response}"
    )


# ── scenario 10 — complete booking start to finish ──────────────────────────

def test_scenario_10_complete_booking(tmp_path: Path) -> None:
    """
    Full conversation: inquiry → booking agent handoff → no API errors surfaced → state preserved.

    After turn 1 (all qualification fields supplied), dispatch hands off to BookingAgent.
    The booking agent then owns the conversation and runs slot selection.
    We verify: state integrity throughout and that no API errors reach the customer.
    """
    agent = make_agent(tmp_path)
    booking_agent = None
    active = "inbound_agent"

    # Turn 1 — supply all qualification fields at once
    r1, booking_agent, active = dispatch(
        "We are six adults planning an escape room this Saturday at Koramangala.",
        agent, booking_agent, active
    )
    assert r1.response, "Turn 1 must produce a response"
    assert agent.memory.data.get("participants") == 6, (
        f"participants should be 6: {agent.memory.data.get('participants')}"
    )
    assert agent.memory.data.get("location") == "Koramangala", (
        f"location should be Koramangala: {agent.memory.data.get('location')}"
    )

    # No API errors should be in the response
    for bad_term in ("unauthorized", "401", "403", "api error"):
        assert bad_term not in r1.response.lower(), (
            f"API error leaked to customer in turn 1: {r1.response}"
        )

    # Turn 2 — supply age group to keep qualification moving
    r2, booking_agent, active = dispatch(
        "We are all adults in our 20s.",
        agent, booking_agent, active
    )
    assert r2.response, "Turn 2 must produce a response"
    for bad_term in ("unauthorized", "401", "403"):
        assert bad_term not in r2.response.lower(), (
            f"API error leaked to customer in turn 2: {r2.response}"
        )

    # State must survive across all turns
    assert agent.memory.data.get("participants") == 6, (
        f"participants must remain 6 after turn 2: {agent.memory.data.get('participants')}"
    )
    assert agent.memory.data.get("location") == "Koramangala", (
        f"location must remain Koramangala after turn 2: {agent.memory.data.get('location')}"
    )

    # Turn 3 — booking consent / availability check
    r3, booking_agent, active = dispatch(
        "Great, yes please check availability.",
        agent, booking_agent, active
    )
    assert r3.response, "Turn 3 must produce a response"
    for bad_term in ("unauthorized", "401", "403"):
        assert bad_term not in r3.response.lower(), (
            f"API error leaked to customer in turn 3: {r3.response}"
        )

    # Final state check
    assert agent.memory.data.get("participants") == 6, "participants must survive full flow"
    assert agent.memory.data.get("location") == "Koramangala", "location must survive full flow"


def test_emergency_demo_seven_turn_flow_preserves_intake(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    agent = make_agent(tmp_path)
    booking_agent = None
    active = "inbound_agent"
    turns = [
        "We are seven friends and none of us have ever done an escape room before.",
        "All adults.",
        "Can you explain all the rooms?",
        "We are running 20 minutes late.",
        "Actually I want Classified instead.",
        "My name is Surabhi.",
        "8217008407",
    ]

    responses = []
    for message in turns:
        result, booking_agent, active = dispatch(message, agent, booking_agent, active)
        responses.append(result.response)
        assert result.response
        assert "api error" not in result.response.lower()
        assert "traceback" not in result.response.lower()

    assert active == "inbound_agent"
    assert booking_agent is None
    assert agent.memory.data["participants"] == 7
    assert agent.memory.data["age_group"] == "adults"
    assert agent.memory.data["experience_level"] == "beginner"
    assert agent.memory.data["room"] == "Classified"
    assert agent.memory.data["recommended_option"] == "Classified"
    assert agent.memory.data["location"] == "Koramangala"
    assert agent.memory.data["customer_name"] == "Surabhi"
    assert agent.memory.data["phone"] == "8217008407"
    assert "available slots" not in responses[5].lower()
    assert "available slots" not in responses[6].lower()
