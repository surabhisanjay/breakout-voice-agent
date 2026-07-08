from pathlib import Path

from main import dispatch
from src.agents.booking_agent import BookingAgent
from src.agents.inbound_agent import InboundAgent
from src.agents.qualification_agent import QualificationAgent
from src.knowledge.knowledge_loader import KnowledgeLoader
from src.memory.conversation_memory import ConversationMemory
from src.services.conversation_resolution import ConversationResolver
from src.services.price_breakdown import estimated_price_breakdown, price_breakdown_from_totals
from src.services.recommendation_engine import RecommendationEngine


PROJECT_DIR = Path(__file__).resolve().parents[1]


def memory(tmp_path: Path) -> ConversationMemory:
    return ConversationMemory(tmp_path / "session.json")


def inbound(tmp_path: Path) -> InboundAgent:
    agent = InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=ConversationMemory(tmp_path / "session.json"),
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )
    agent.response_composer.enabled = False
    return agent


def test_fragment_experience_answers_are_absorbed(tmp_path: Path) -> None:
    mem = memory(tmp_path)

    mem.merge_message("no first one", "escape_room_inquiry")
    assert mem.data["experience_level"] == "beginner"

    mem.data["experience_level"] = ""
    mem.merge_message("first one", "escape_room_inquiry")
    assert mem.data["experience_level"] == "beginner"


def test_time_guardrails_reject_numeric_bleed_and_pronouns() -> None:
    assert BookingAgent._extract_slot("two adults in the evening") == ""
    assert BookingAgent._extract_slot("other option") == ""
    assert BookingAgent._extract_slot("the other beginner one") == ""
    assert BookingAgent._extract_slot("I need a room tomorrow around 1:30") == "1:30 PM"


def test_time_window_phrase_is_not_captured_as_customer_name() -> None:
    assert QualificationAgent._extract_bare_name("Around afternoon") == ""
    assert ConversationMemory._extract_name("Around afternoon") == ""


def test_compound_statement_writes_all_facts(tmp_path: Path) -> None:
    mem = memory(tmp_path)

    extracted = mem.merge_message(
        "Whitefield tomorrow, we're four, I've already decided on Hostage",
        "escape_room_inquiry",
    )

    assert extracted["location"] == "Whitefield"
    assert extracted["participants"] == 4
    assert extracted["room"] == "Hostage"
    assert mem.data["preferred_date"] == "Tomorrow"


def test_declared_dependency_graph_only_invalidates_dependents(tmp_path: Path) -> None:
    mem = memory(tmp_path)
    mem.data.update(
        {
            "location": "Whitefield",
            "room": "Hostage",
            "participants": 4,
            "preferred_date": "Saturday",
            "preferred_time": "2:30 PM",
            "selected_slot": "2:30 PM",
            "last_suggested_slots": ["2:30 PM", "3:45 PM"],
            "venue_id": "old_venue",
        }
    )

    mem.set_field("location", "Koramangala", "change to Koramangala", expected_field="location")
    assert mem.data["participants"] == 4
    assert mem.data["selected_slot"] == ""
    assert mem.data["venue_id"] == ""

    mem.data["selected_slot"] = "2:30 PM"
    mem.data["last_suggested_slots"] = ["2:30 PM"]
    mem.set_field("room", "Murder Mystery", "switch to Murder Mystery", expected_field="room")
    assert mem.data["preferred_date"] == "Saturday"
    assert mem.data["preferred_time"] == "2:30 PM"
    assert mem.data["selected_slot"] == ""

    mem.data["selected_slot"] = "2:30 PM"
    mem.set_field("preferred_date", "Sunday", "change date to Sunday", expected_field="preferred_date")
    assert mem.data["location"] == "Koramangala"
    assert mem.data["room"] == "Murder Mystery"
    assert mem.data["selected_slot"] == ""


def test_resolver_returns_goal_known_facts_and_single_next_fact(tmp_path: Path) -> None:
    mem = memory(tmp_path)
    mem.merge_message("I need tomorrow around 1:30", "escape_room_inquiry")

    resolution = ConversationResolver().resolve(mem.data, "What room do you recommend?")

    assert resolution.goal == "recommend_room"
    assert "preferred_date" in resolution.known_facts
    assert "preferred_time" in resolution.known_facts
    assert resolution.next_required_fact == "participants"


def test_recommendation_never_fires_after_room_is_stated() -> None:
    rec = RecommendationEngine().recommend(
        "What do you recommend?",
        {"intent": "escape_room_inquiry", "participants": 2, "experience_level": "beginner", "room": "Hostage"},
    )

    assert rec.option == ""
    assert rec.reason == ""


def test_direct_booking_agent_call_respects_escalation_lock(tmp_path: Path) -> None:
    mem = memory(tmp_path)
    mem.data["escalation_state"] = {
        "escalate": True,
        "status": "unresolved",
        "reason": "Customer explicitly requested a human representative",
    }
    mem.data["location"] = "Whitefield"
    mem.save()

    result = BookingAgent(mem).handle_message("Does Koramangala have evening slots tomorrow?")

    assert result.next_agent == "escalation_agent"
    assert result.should_handoff is True
    assert "team" in result.response.lower()
    assert mem.data["location"] == "Whitefield"


def test_structured_price_objects_for_estimated_and_kreeda_totals() -> None:
    discounted = estimated_price_breakdown(4, "Saturday", "Murder Mystery")
    undiscounted = estimated_price_breakdown(2, "Monday", "Murder Mystery")
    authoritative = price_breakdown_from_totals({"subtotal": 2800, "total": 2520, "currency": "INR"})

    assert discounted["base_price"] == 3200
    assert discounted["discount"] == 320
    assert discounted["final_price"] == 2880
    assert discounted["source"] == "knowledge_base_estimate"
    assert undiscounted["base_price"] == 1600
    assert undiscounted["discount_applied"] is False
    assert undiscounted["final_price"] == 1600
    assert authoritative["final_price"] == 2520
    assert authoritative["source"] == "kreeda_payment_status"


def test_booking_resolver_classifies_novel_questions_before_reasking_pending_time(tmp_path: Path) -> None:
    agent = inbound(tmp_path)
    booking = None
    active = "inbound_agent"
    turns = [
        "We are four adults and Whitefield works for us tomorrow.",
        "Actually change the location to Koramangala.",
        "Let's do Hostage.",
        "Actually make it two players.",
        "Do you have wheelchair accessible rooms?",
        "Can we get a group photo after the game?",
        "What if someone needs the restroom mid-game?",
    ]
    responses: list[str] = []

    for message in turns:
        result, booking, active = dispatch(message, agent, booking, active)
        responses.append(result.response)

    wheelchair, photo, restroom = [response.lower() for response in responses[-3:]]
    assert "accessibility" in wheelchair or "accessible" in wheelchair
    assert "what time works best" in wheelchair
    assert wheelchair.index("access") < wheelchair.index("what time works best")
    assert "photo" in photo
    assert "what time works best" in photo
    assert photo.index("photo") < photo.index("what time works best")
    assert "restroom" in restroom
    assert "what time works best" in restroom
    assert restroom.index("restroom") < restroom.index("what time works best")
    assert "connect you with our team" not in " ".join(response.lower() for response in responses[-3:])
    assert not agent.memory.data["escalation_state"]["escalate"]


def test_scenario_three_recommendation_fires_before_location(tmp_path: Path) -> None:
    agent = inbound(tmp_path)
    booking = None
    active = "inbound_agent"

    first, booking, active = dispatch("I need a room tomorrow around 1:30.", agent, booking, active)
    second, booking, active = dispatch("Two of us. What room do you recommend?", agent, booking, active)

    assert "how many" in first.response.lower()
    assert "murder mystery" in second.response.lower()
    assert "which location" in second.response.lower()
    assert second.response.lower().index("murder mystery") < second.response.lower().index("which location")
    assert agent.memory.data["recommended_option"] == "Murder Mystery"
