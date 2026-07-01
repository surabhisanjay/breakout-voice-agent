from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from main import dispatch
from src.agents.booking_agent import BookingAgent
from src.agents.inbound_agent import InboundAgent
from src.knowledge.knowledge_loader import KnowledgeLoader
from src.memory.conversation_memory import ConversationMemory
from src.services.recommendation_engine import RecommendationEngine


def _agent(tmp_path: Path, name: str) -> InboundAgent:
    return InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=ConversationMemory(tmp_path / f"{name}.json"),
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


def _assert_safe_response(result) -> None:
    lowered = result.response.lower()
    assert result.response.strip()
    assert "traceback" not in lowered
    assert "openai" not in lowered
    assert "api error" not in lowered
    assert "would you like a brief summary" not in lowered
    assert "would you like details" not in lowered
    assert result.response.count("?") <= 1
    assert result.call_intelligence
    assert result.sentiment_analysis


def _run(agent: InboundAgent, turns: list[str], active: str = "inbound_agent") -> tuple[list, object, str]:
    booking = None
    results = []
    for turn in turns:
        result, booking, active = dispatch(turn, agent, booking, active)
        _assert_safe_response(result)
        results.append(result)
    return results, booking, active


@pytest.fixture(autouse=True)
def offline_booking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKING_PROVIDER", "simulator")
    monkeypatch.setenv("BOOKING_API_KEY", "")
    monkeypatch.setenv("BOOKING_BASE_URL", "")


DIRECT_CONTEXTS = [
    {"location": "Whitefield", "participants": 4, "age_group": "adults", "room": "Hostage", "preferred_date": "18 June"},
    {"location": "Koramangala", "participants": 5, "age_group": "adults", "room": "Murder Mystery", "preferred_date": "19 June"},
    {"location": "JP Nagar", "participants": 3, "age_group": "adults", "room": "Missile Attack", "preferred_date": "20 June"},
    {"location": "", "participants": 2, "age_group": "adults", "room": "", "preferred_date": ""},
]

DIRECT_QUESTIONS = [
    ("What rooms do you have?", ("murder mystery", "hostage")),
    ("What themes do you have?", ("murder mystery", "hostage")),
    ("What happens in Hostage?", ("hostage",)),
    ("What is Murder Mystery?", ("murder mystery",)),
    ("What is an escape room?", ("escape room",)),
    ("Compare Murder Mystery and Hostage", ("murder mystery", "hostage")),
    ("Which location is better for kids?", ("whitefield",)),
    ("Which room fits 4 people?", ("fit", "rooms")),
    ("What is the price?", ("price", "pricing")),
    ("Do you have discounts?", ("discount", "pricing")),
    ("How does payment work?", ("payment", "checkout")),
    ("Is parking available?", ("parking",)),
    ("What is cancellation policy?", ("cancellation",)),
    ("Can I get a refund?", ("refund",)),
    ("We are running late", ("scheduled", "arriving", "slot")),
    ("Do you have packages?", ("package",)),
    ("What would you recommend?", ("recommend",)),
    ("Which room is best?", ("recommend",)),
    ("What do most people play?", ("recommend",)),
    ("Recommend something harder", ("recommend",)),
    ("Recommend something for couples", ("recommend",)),
    ("Any other option?", ("recommend",)),
    ("Second best?", ("recommend",)),
    ("I don't like that one", ("recommend",)),
    ("I want to speak to a human", ("connect", "team")),
]


def test_100_deterministic_direct_questions_use_dispatch_and_do_not_force_booking(tmp_path: Path) -> None:
    executed = 0
    for idx, (question, expected_terms) in enumerate(DIRECT_QUESTIONS):
        for context in DIRECT_CONTEXTS:
            agent = _agent(tmp_path, f"direct_{idx}_{executed}")
            agent.memory.data.update({"intent": "escape_room_inquiry", "recommended_option": "Murder Mystery", **context})
            agent.memory.save()

            result, _booking, _active = dispatch(question, agent, None, "booking_agent")
            _assert_safe_response(result)
            lowered = result.response.lower()
            assert any(term in lowered for term in expected_terms), (question, result.response)
            assert "which room would you like to book" not in lowered
            assert agent.memory.data.get("location") == context["location"]
            assert agent.memory.data.get("participants") == context["participants"]
            executed += 1
            if executed == 100:
                return
    assert executed == 100


def test_200_randomized_conversations_keep_routing_memory_and_intelligence_stable(tmp_path: Path) -> None:
    rng = random.Random(20260626)
    openers = [
        "We are first timers",
        "This is our third time",
        "I want to book for {n} people",
        "Family group with kids",
        "Couple booking",
        "Corporate team of {n}",
    ]
    contexts = ["Whitefield", "Koramangala", "JP Nagar", "white food", "jp nogger"]
    questions = [item[0] for item in DIRECT_QUESTIONS]
    corrections = [
        "Actually change it to Hostage",
        "Switch location to Whitefield",
        "Change the date to 25 June",
        "Make it 6 people",
        "Use same phone",
        "Any other option?",
    ]

    for idx in range(200):
        agent = _agent(tmp_path, f"random_{idx}")
        n = rng.choice([2, 3, 4, 5, 6, 8, 12])
        turns = [
            rng.choice(openers).format(n=n),
            rng.choice(contexts),
            rng.choice(questions),
            rng.choice(corrections),
            rng.choice(["Tomorrow evening?", "7 PM", "My phone is 9876543210", "My name is Priya"]),
        ]
        results, _booking, _active = _run(agent, turns)
        assert len(results) == len(turns)
        assert isinstance(agent.memory.data.get("conversation"), list)


VOICE_UTTERANCES = [
    "white food",
    "wide field",
    "jp nogger",
    "jp nuggets",
    "murder history",
    "murder mistery",
    "hostages",
    "bomb diffusion",
    "bomb refusal",
    "same phone",
    "same room",
    "same location",
    "that one",
    "this one",
    "tomorrow evening",
    "one thirty tomorrow",
    "no no that's not what I asked",
    "I already told you",
    "connect me to a human",
    "I want my money back",
]


def test_200_voice_style_asr_conversations_use_aliases_and_do_not_crash(tmp_path: Path) -> None:
    for idx in range(200):
        agent = _agent(tmp_path, f"voice_{idx}")
        agent.memory.data.update(
            {
                "intent": "escape_room_inquiry",
                "location": "Whitefield",
                "room": "Murder Mystery",
                "recommended_option": "Murder Mystery",
                "participants": 4,
                "preferred_date": "18 June",
                "phone": "9876543210",
            }
        )
        agent.memory.save()
        turns = [
            VOICE_UTTERANCES[idx % len(VOICE_UTTERANCES)],
            VOICE_UTTERANCES[(idx + 5) % len(VOICE_UTTERANCES)],
            VOICE_UTTERANCES[(idx + 11) % len(VOICE_UTTERANCES)],
        ]
        _run(agent, turns, active="booking_agent")
        assert agent.memory.data["phone"] == "9876543210"
        assert agent.memory.data.get("location") in {"Whitefield", "JP Nagar", "Koramangala"}


INTERRUPTIONS = [
    "What rooms do you have?",
    "What is Murder Mystery?",
    "Compare Murder Mystery and Hostage",
    "What is the price?",
    "Is parking available?",
    "What is cancellation policy?",
    "Can I get a refund?",
    "Recommend something harder",
    "Any other option?",
    "Which room fits 4 people?",
]


def test_100_faq_and_recommendation_interruptions_preserve_booking_state(tmp_path: Path) -> None:
    for idx in range(100):
        agent = _agent(tmp_path, f"interrupt_{idx}")
        initial = {
            "intent": "escape_room_inquiry",
            "location": "Whitefield",
            "room": "Hostage",
            "recommended_option": "Murder Mystery",
            "participants": 4,
            "preferred_date": "18 June",
            "selected_slot": "5:00 PM",
            "customer_name": "Priya",
            "phone": "9876543210",
            "booking_started": True,
            "current_workflow": "booking",
        }
        agent.memory.data.update(initial)
        agent.memory.save()

        question = INTERRUPTIONS[idx % len(INTERRUPTIONS)]
        result, _booking, _active = dispatch(question, agent, BookingAgent(agent.memory), "booking_agent")
        _assert_safe_response(result)
        for field in ("location", "participants", "preferred_date", "selected_slot", "customer_name", "phone"):
            assert agent.memory.data.get(field) == initial[field]


RECOMMENDATION_REQUESTS = [
    "What would you recommend?",
    "Which room is best?",
    "What do most people play?",
    "Recommend something harder",
    "Recommend something easier",
    "Recommend something scary",
    "Recommend something thrilling",
    "Recommend something for couples",
    "Recommend something for first timers",
    "Recommend something for kids",
]


def test_100_recommendation_scenarios_use_location_inventory_and_remember_rejections(tmp_path: Path) -> None:
    locations = ["Whitefield", "Koramangala", "JP Nagar"]
    for idx in range(100):
        location = locations[idx % len(locations)]
        participants = [2, 3, 4, 5, 7][idx % 5]
        agent = _agent(tmp_path, f"recommend_{idx}")
        agent.memory.data.update(
            {
                "intent": "escape_room_inquiry",
                "location": location,
                "participants": participants,
                "age_group": "adults",
                "recommended_option": "Murder Mystery",
            }
        )
        agent.memory.save()

        request = RECOMMENDATION_REQUESTS[idx % len(RECOMMENDATION_REQUESTS)]
        result, _booking, _active = dispatch(request, agent, None, "inbound_agent")
        _assert_safe_response(result)
        recommended = agent.memory.data.get("recommended_option")
        available = RecommendationEngine.available_options(agent.memory.data, limit=10)
        assert recommended in available, (location, participants, request, result.response, available)

        rejected = recommended
        result, _booking, _active = dispatch("I don't like that one, any other option?", agent, None, "inbound_agent")
        _assert_safe_response(result)
        assert rejected in agent.memory.data.get("rejected_options", [])
        if len(available) > 1:
            assert agent.memory.data.get("recommended_option") != rejected


BOOKING_CHANGES = [
    ("Actually change it to Hostage", "room", "Hostage"),
    ("Actually change it to Murder Mystery", "room", "Murder Mystery"),
    ("Switch location to Koramangala", "location", "Koramangala"),
    ("Switch location to JP Nagar", "location", "JP Nagar"),
    ("Switch location to Whitefield", "location", "Whitefield"),
    ("Change the date to 25 June", "preferred_date", "25 June"),
    ("Make it 6 people", "participants", 6),
    ("Make it 3 people", "participants", 3),
    ("Use 7 PM", "selected_slot", "7:00 PM"),
    ("Use same phone", "phone", "9876543210"),
]


def test_100_booking_state_changes_are_persisted_through_dispatch(tmp_path: Path) -> None:
    for idx in range(100):
        agent = _agent(tmp_path, f"booking_{idx}")
        agent.memory.data.update(
            {
                "intent": "escape_room_inquiry",
                "location": "Whitefield",
                "room": "Murder Mystery",
                "recommended_option": "Murder Mystery",
                "participants": 4,
                "age_group": "adults",
                "preferred_date": "18 June",
                "selected_slot": "5:00 PM",
                "customer_name": "Priya",
                "phone": "9876543210",
                "booking_started": True,
                "current_workflow": "booking",
            }
        )
        agent.memory.save()
        message, field, expected = BOOKING_CHANGES[idx % len(BOOKING_CHANGES)]

        result, _booking, active = dispatch(message, agent, BookingAgent(agent.memory), "booking_agent")
        _assert_safe_response(result)
        assert active == "booking_agent"
        assert agent.memory.data.get(field) == expected


def test_escalation_handoff_and_conversation_intelligence_contracts_survive_dispatch(tmp_path: Path) -> None:
    agent = _agent(tmp_path, "escalation_contract")
    agent.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "location": "Whitefield",
            "room": "Hostage",
            "participants": 4,
            "preferred_date": "18 June",
            "customer_name": "Priya",
            "phone": "9876543210",
        }
    )
    agent.memory.save()

    result, _booking, active = dispatch("I am furious and want to speak to a human", agent, None, "booking_agent")

    _assert_safe_response(result)
    assert active == "escalation_agent"
    assert result.should_handoff is True
    assert result.escalation["escalate"] is True
    assert result.handoff_summary["conversation_summary"]
    assert result.handoff_summary["booking_details"]["location"] == "Whitefield"
    assert result.call_intelligence["escalation"]["required"] is True
    assert result.call_intelligence["transcript"]
    assert result.timeline_events == result.call_intelligence["timeline_events"]
