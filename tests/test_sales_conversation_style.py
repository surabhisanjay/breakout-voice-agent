from __future__ import annotations

import json
import re
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.memory.conversation_memory import ConversationMemory  # noqa: E402
from src.agents.inbound_agent import InboundAgent  # noqa: E402
from src.knowledge.knowledge_loader import KnowledgeLoader  # noqa: E402


ACKNOWLEDGEMENTS = (
    "awesome", "nice", "got it", "makes sense", "perfect", "sounds good",
    "no worries", "absolutely", "beautiful", "don't worry", "sure",
)
AI_FILLERS = (
    "i can help with that",
    "i'd be happy to assist",
    "it gives you",
    "would you like me to",
    "good question",
)


def make_agent(tmp_path: Path) -> InboundAgent:
    return InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=ConversationMemory(tmp_path / "session.json"),
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


def sentence_lengths(text: str) -> list[int]:
    return [len(sentence.split()) for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]


def test_transcript_examples_match_short_human_sales_style() -> None:
    examples = json.loads((PROJECT_DIR / "prompts" / "transcript_examples.json").read_text(encoding="utf-8"))
    responses = [example["composed_response"] for example in examples]
    lengths = [length for response in responses for length in sentence_lengths(response)]

    assert sum(lengths) / len(lengths) <= 12
    assert max(len(response.split()) for response in responses) <= 35
    assert sum(response.lower().startswith(ACKNOWLEDGEMENTS) for response in responses) / len(responses) >= 0.8
    assert not any(filler in response.lower() for response in responses for filler in AI_FILLERS)


def test_generated_recommendations_are_brief_and_conversational(tmp_path: Path) -> None:
    scenarios = (
        "We are seven friends and none of us have done an escape room before.",
        "We need a corporate event for fifteen employees.",
        "I want a birthday party.",
        "We are a couple.",
    )
    responses = [make_agent(tmp_path / str(index)).handle_message(message).response for index, message in enumerate(scenarios)]

    assert max(len(response.split()) for response in responses) <= 45
    assert sum(response.lower().startswith(ACKNOWLEDGEMENTS) for response in responses) >= 3
    assert not any(filler in response.lower() for response in responses for filler in AI_FILLERS)
    assert all(response.count("?") <= 1 for response in responses)


def test_first_timer_recommendation_uses_one_reason_and_next_question(tmp_path: Path) -> None:
    response = make_agent(tmp_path).handle_message(
        "We are seven adults and none of us have done an escape room before."
    ).response

    assert "Murder Mystery" in response
    assert len(response.split()) <= 40
    assert response.count("?") == 1
    assert "It gives you" not in response
