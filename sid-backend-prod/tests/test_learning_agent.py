from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.learning_agent import LearningAgent


def test_learning_agent_attributes_repeated_failures_to_agent() -> None:
    conversations = [{
        "turns": [
            {"role": "customer", "text": "Yeah, tell me."},
            {"role": "agent", "agent": "inbound_agent", "text": "Would you like me to show a brief overview or the full list?"},
            {"role": "customer", "text": "All rooms."},
            {"role": "agent", "agent": "inbound_agent", "text": "The rooms are loading. Please hold on."},
        ]
    }]

    report = LearningAgent().analyze(conversations).to_dict()

    assert report["agent_scorecards"]["inbound_agent"]["status"] == "underperforming"
    issues = report["agent_scorecards"]["inbound_agent"]["issues"]
    assert issues["permission_loop"] == 1
    assert issues["fake_loading_state"] == 1
    assert report["recommendations"]


def test_learning_agent_keeps_clean_agents_healthy() -> None:
    conversations = [{
        "turns": [
            {"role": "customer", "text": "We are four people at Whitefield."},
            {"role": "agent", "agent": "qualification_agent", "text": "Great. What date are you planning for?"},
            {"role": "customer", "text": "Tomorrow."},
            {"role": "agent", "agent": "booking_agent", "text": "I found 6 PM and 8 PM. Which time works best?"},
        ]
    }]

    report = LearningAgent().analyze(conversations)

    assert report.overall_score == 100
    assert not report.underperforming_agents


def test_learning_agent_does_not_average_away_dense_repetition() -> None:
    turns = []
    for _ in range(3):
        turns.extend([
            {"role": "customer", "text": "I already answered."},
            {"role": "agent", "agent": "qualification_agent", "text": "How many people are joining?"},
        ])

    report = LearningAgent().analyze([{"turns": turns}])

    assert report.agent_scorecards["qualification_agent"]["status"] == "underperforming"
    assert report.agent_scorecards["qualification_agent"]["issues"]["repeated_response"] == 2
