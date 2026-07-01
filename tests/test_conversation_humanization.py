from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.inbound_agent import InboundAgent
from src.knowledge.knowledge_loader import KnowledgeLoader
from src.memory.conversation_memory import ConversationMemory


def make_human_agent(tmp_path: Path) -> InboundAgent:
    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = ConversationMemory(tmp_path / "session.json")
    return InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


def test_humanization_no_repeated_questions(tmp_path: Path) -> None:
    agent = make_human_agent(tmp_path)
    # Customer says: "We are a group of seven friends."
    # Memory should populate participants = 7
    res = agent.handle_message("We are a group of seven friends.")
    assert agent.memory.data["participants"] == 7
    # Should not ask for participants again.
    assert "how many people" not in res["response"].lower()
    assert "escape room before" in res["response"].lower() or "first one" in res["response"].lower()


def test_humanization_acknowledgement_and_explanation_before_qualification(tmp_path: Path) -> None:
    agent = make_human_agent(tmp_path)
    res = agent.handle_message("We want an escape room.")
    assert res["response"] == "How many people are joining?"


def test_humanization_recommendation_before_qualification(tmp_path: Path) -> None:
    agent = make_human_agent(tmp_path)
    # Customer says they are first-timers and 7 friends — new flow asks age_group first
    res = agent.handle_message("We are a group of seven friends and have never done an escape room.")
    # First, age group question should appear (no recommendation yet without age_group)
    assert "adult" in res["response"].lower() or "kids" in res["response"].lower() or "age" in res["response"].lower()

    # After providing age_group, the agent asks location instead of recommending unprompted.
    res2 = agent.handle_message("We are all adults.")
    assert res2["response"] == "Which location would you like to visit?"


def test_humanization_rapport_and_reassurance_faq(tmp_path: Path) -> None:
    agent = make_human_agent(tmp_path)
    res1 = agent.handle_message("We are running late.")
    assert "Don't worry" in res1["response"] or "arriving late" in res1["response"]
    
    res2 = agent.handle_message("What happens if we don't escape?")
    assert "won't keep you locked" in res2["response"]


def test_regression_friends_and_first_time_players(tmp_path: Path) -> None:
    agent = make_human_agent(tmp_path)
    # Put the agent in participants qualification state
    agent.memory.data["intent"] = "escape_room_inquiry"
    agent.qualification_agent._waiting_for = "participants"
    agent.memory.save()

    # Customer says: "We are seven friends and none of us has ever done an escape room before."
    res = agent.handle_message("We are seven friends and none of us has ever done an escape room before.")
    
    response_text = res["response"]
    
    # New flow: age_group question comes first (no recommendation without age_group)
    assert "adult" in response_text.lower() or "kids" in response_text.lower() or "age" in response_text.lower()
    assert "Good question." not in response_text
    assert "Let me get that checked for you." not in response_text

    # After providing age_group, location comes before recommendation.
    res2 = agent.handle_message("We are all adults.")
    response_text2 = res2["response"]
    assert response_text2 == "Which location would you like to visit?"
