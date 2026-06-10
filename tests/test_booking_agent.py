"""
tests/test_booking_agent.py

Covers the BookingAgent end-to-end:

1. Tool-first principle: AvailabilityTool is called before ANY customer response
2. Slot presentation: available slots formatted naturally
3. Slot selection: valid slot accepted → BookingTool called → confirmation
4. Slot validation: unrecognised slot rejected with a reprompt
5. Unavailability flow: agent asks for alternative date
6. Alternative date: new date provided → re-check → slots presented
7. Post-booking: graceful close-out
8. Full end-to-end: corporate event handoff → slot → confirmation
9. Slot fuzzy matching: "3 PM" matches "3:00 PM"
10. dispatch() integration: handoff triggers BookingAgent automatically
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agent_response import AgentResponse  # noqa: E402
from src.booking_agent import BookingAgent  # noqa: E402
from src.conversation_memory import ConversationMemory  # noqa: E402
from src.tools.availability_tool import AvailabilityTool  # noqa: E402
from src.tools.booking_tool import BookingTool  # noqa: E402


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #

def make_memory(tmp_path: Path, overrides: dict | None = None) -> ConversationMemory:
    memory = ConversationMemory(tmp_path / "session.json")
    defaults = {
        "intent": "corporate_event",
        "event_type": "Corporate Event",
        "location": "Koramangala",
        "preferred_date": "18 June",
        "participants": 20,
        "company_size": 20,
        "food_required": True,
        "budget_range": "premium",
        "customer_name": "Siddharth",
        "phone": "9876543210",
    }
    if overrides:
        defaults.update(overrides)
    memory.data.update(defaults)
    memory.save()
    return memory


def make_agent(tmp_path: Path, overrides: dict | None = None) -> BookingAgent:
    memory = make_memory(tmp_path, overrides)
    return BookingAgent(memory)


# ------------------------------------------------------------------ #
# 1. Tool-first principle                                             #
# ------------------------------------------------------------------ #

def test_availability_tool_called_before_response(tmp_path: Path) -> None:
    """AvailabilityTool.check() must be called before the agent generates text."""
    agent = make_agent(tmp_path)
    call_order: list[str] = []

    original_check = agent.availability_tool.check

    def tracked_check(*args, **kwargs):
        call_order.append("tool_called")
        return original_check(*args, **kwargs)

    agent.availability_tool.check = tracked_check

    result = agent.handle_message("ready")
    call_order.append("response_returned")

    assert call_order[0] == "tool_called", "AvailabilityTool must be called BEFORE response is built"
    assert isinstance(result, AgentResponse)


# ------------------------------------------------------------------ #
# 2. Slot presentation when available                                 #
# ------------------------------------------------------------------ #

def test_available_slots_shown_in_response(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("ready")
    assert "18 June" in result.response
    assert "Koramangala" in result.response
    # At least one time slot should appear
    assert any(t in result.response for t in ["AM", "PM"]), \
        f"Expected time slots in response, got: {result.response}"


def test_response_asks_customer_to_choose_slot(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("ready")
    assert "which time" in result.response.lower() or "which slot" in result.response.lower() or \
           "time works" in result.response.lower(), \
        f"Expected slot-selection prompt, got: {result.response}"


def test_customer_name_used_in_greeting(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("ready")
    assert "Siddharth" in result.response


# ------------------------------------------------------------------ #
# 3. Slot selection → booking confirmation                            #
# ------------------------------------------------------------------ #

def test_valid_slot_triggers_booking_tool(tmp_path: Path) -> None:
    """BookingTool.create() must be called after a valid slot is chosen."""
    agent = make_agent(tmp_path)
    agent.handle_message("ready")  # get slots

    booking_calls: list[str] = []
    original_create = agent.booking_tool.create

    def tracked_create(*args, **kwargs):
        booking_calls.append("booking_tool_called")
        return original_create(*args, **kwargs)

    agent.booking_tool.create = tracked_create
    result = agent.handle_message("3 PM")

    assert booking_calls, "BookingTool.create() must be called on valid slot selection"
    assert result.booking_result is not None
    assert result.booking_result["confirmed"] is True


def test_booking_confirmation_contains_key_details(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("ready")
    result = agent.handle_message("3 PM")

    assert "Koramangala" in result.response
    assert "18 June" in result.response
    assert "3:00 PM" in result.response
    assert "BRK-" in result.response  # booking reference


def test_booking_response_contains_booking_id(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("ready")
    result = agent.handle_message("3 PM")

    assert result.booking_result is not None
    assert result.booking_result["booking_id"].startswith("BRK-")


def test_booking_should_handoff_true_after_confirmation(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("ready")
    result = agent.handle_message("3 PM")
    assert result.should_handoff is True


# ------------------------------------------------------------------ #
# 4. Slot validation — invalid slot rejected                         #
# ------------------------------------------------------------------ #

def test_unrecognised_slot_rejected_with_reprompt(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("ready")
    result = agent.handle_message("7 PM")  # not in stub slots

    assert result.booking_result is None, "No booking should be created for an invalid slot"
    assert any(word in result.response.lower() for word in ["sorry", "available", "choose"]), \
        f"Expected rejection message, got: {result.response}"


def test_invalid_slot_stays_in_slot_selection_state(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("ready")
    agent.handle_message("7 PM")  # rejected
    result = agent.handle_message("3 PM")  # should now work

    assert result.booking_result is not None
    assert result.booking_result["confirmed"] is True


# ------------------------------------------------------------------ #
# 5. Unavailability flow                                              #
# ------------------------------------------------------------------ #

def test_unavailable_response_asks_for_alternative_date(tmp_path: Path) -> None:
    agent = make_agent(tmp_path, {"location": "Virtual", "preferred_date": "99 June"})
    # Patch the tool to return unavailable
    agent.availability_tool.check = MagicMock(return_value={
        "available": False,
        "slots": [],
        "location": "Koramangala",
        "date": "99 June",
        "participants": 20,
    })

    result = agent.handle_message("ready")
    assert result.booking_result is None
    assert any(phrase in result.response.lower()
               for phrase in ["alternative", "another date", "different date", "other date"]), \
        f"Expected alternative date prompt, got: {result.response}"


# ------------------------------------------------------------------ #
# 6. Alternative date flow                                            #
# ------------------------------------------------------------------ #

def test_alternative_date_rechecks_availability(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    # First turn: mark as unavailable
    agent.availability_tool.check = MagicMock(return_value={
        "available": False, "slots": [], "location": "Koramangala",
        "date": "18 June", "participants": 20,
    })
    agent.handle_message("ready")

    # Restore real availability for the new date
    agent.availability_tool.check = MagicMock(return_value={
        "available": True,
        "slots": ["11:00 AM", "2:00 PM"],
        "location": "Koramangala",
        "date": "20 June",
        "participants": 20,
    })
    result = agent.handle_message("How about 20 June?")

    assert "20 June" in result.response or "11" in result.response or "2:00" in result.response, \
        f"Expected new date slots in response, got: {result.response}"


def test_alternative_date_updates_memory(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.availability_tool.check = MagicMock(return_value={
        "available": False, "slots": [], "location": "Koramangala",
        "date": "18 June", "participants": 20,
    })
    agent.handle_message("ready")

    agent.availability_tool.check = MagicMock(return_value={
        "available": True, "slots": ["10:00 AM"], "location": "Koramangala",
        "date": "20 June", "participants": 20,
    })
    agent.handle_message("20 June please")

    assert agent.memory.data["preferred_date"] == "20 June"


# ------------------------------------------------------------------ #
# 7. Post-booking / close-out                                         #
# ------------------------------------------------------------------ #

def test_post_booking_goodbye(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("ready")
    agent.handle_message("3 PM")  # confirmed
    result = agent.handle_message("No, that's all. Thanks.")

    assert any(word in result.response.lower()
               for word in ["thank", "great", "look forward", "day"]), \
        f"Expected closing message, got: {result.response}"


def test_post_booking_keeps_booking_result(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("ready")
    agent.handle_message("3 PM")
    result = agent.handle_message("thanks")
    assert result.booking_result is not None


# ------------------------------------------------------------------ #
# 8. Full end-to-end: handoff → slot → confirmation                  #
# ------------------------------------------------------------------ #

def test_full_booking_flow_end_to_end(tmp_path: Path) -> None:
    memory = make_memory(tmp_path)
    agent = BookingAgent(memory)

    # Turn 1: availability check (sentinel)
    r1 = agent.handle_message("ready")
    assert "Koramangala" in r1.response
    assert any(t in r1.response for t in ["AM", "PM"])

    # Turn 2: slot selection
    r2 = agent.handle_message("3:00 PM")
    assert r2.booking_result is not None
    assert r2.booking_result["confirmed"] is True
    assert r2.booking_result["slot"] == "3:00 PM"

    # Turn 3: close-out
    r3 = agent.handle_message("Thanks, that's all.")
    assert any(word in r3.response.lower() for word in ["thank", "look forward", "great"])


# ------------------------------------------------------------------ #
# 9. Slot fuzzy matching                                              #
# ------------------------------------------------------------------ #

def test_slot_3pm_matches_3_colon_00_PM(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("ready")  # slots: ["10:00 AM", "12:00 PM", "3:00 PM", "6:00 PM"]
    result = agent.handle_message("3 PM")

    assert result.booking_result is not None, "3 PM should fuzzy-match 3:00 PM"
    assert result.booking_result["slot"] == "3:00 PM"


def test_slot_12pm_matches_noon(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("ready")
    result = agent.handle_message("12 PM")
    assert result.booking_result is not None
    assert result.booking_result["slot"] == "12:00 PM"


def test_slot_10am_matches(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("ready")
    result = agent.handle_message("10 AM")
    assert result.booking_result is not None
    assert result.booking_result["slot"] == "10:00 AM"


# ------------------------------------------------------------------ #
# 10. as_state / from_state round-trip                                #
# ------------------------------------------------------------------ #

def test_as_state_round_trip(tmp_path: Path) -> None:
    """ConversationMemory.as_state() → from_state() must be lossless."""
    memory = make_memory(tmp_path)
    original_state = memory.as_state()

    memory2 = ConversationMemory(tmp_path / "session2.json")
    memory2.from_state(original_state)

    assert memory2.data["location"] == "Koramangala"
    assert memory2.data["customer_name"] == "Siddharth"
    assert memory2.data["preferred_date"] == "18 June"


def test_booking_agent_state_in_response(tmp_path: Path) -> None:
    """AgentResponse.state must contain the full memory snapshot."""
    agent = make_agent(tmp_path)
    result = agent.handle_message("ready")

    assert result.state["location"] == "Koramangala"
    assert result.state["customer_name"] == "Siddharth"


# ------------------------------------------------------------------ #
# 11. dispatch() integration test                                     #
# ------------------------------------------------------------------ #

def test_dispatch_handoff_creates_booking_agent(tmp_path: Path) -> None:
    """When InboundAgent emits should_handoff=True, dispatch() must switch to BookingAgent."""
    import main as main_module

    from src.inbound_agent import InboundAgent
    from src.knowledge_loader import KnowledgeLoader

    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = make_memory(tmp_path, {"intent": "corporate_event"})
    inbound = InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_ollama=False,
    )

    # Inject a mock handoff result from InboundAgent
    mock_inbound_result = AgentResponse(
        response="Handoff triggered.",
        intent="corporate_event",
        next_agent="booking_agent",
        should_handoff=True,
        state=memory.as_state(),
    )
    inbound.handle_message = MagicMock(return_value=mock_inbound_result)

    result, booking_inst, active = main_module.dispatch(
        "dummy message", inbound, None, "inbound_agent"
    )

    assert active == "booking_agent", "Active agent must switch to booking_agent on handoff"
    assert booking_inst is not None, "BookingAgent instance must be created on first handoff"
    assert isinstance(result, AgentResponse)


def test_dispatch_booking_agent_owns_subsequent_turns(tmp_path: Path) -> None:
    """Once active_agent is 'booking_agent', dispatch must NOT call InboundAgent."""
    import main as main_module

    from src.inbound_agent import InboundAgent
    from src.knowledge_loader import KnowledgeLoader

    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = make_memory(tmp_path)
    inbound = InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_ollama=False,
    )
    inbound.handle_message = MagicMock()  # should never be called

    booking = BookingAgent(memory)
    booking._state = BookingAgent._STATE_WAITING_FOR_SLOT
    booking._available_slots = ["10:00 AM", "3:00 PM"]

    result, _, active = main_module.dispatch("3 PM", inbound, booking, "booking_agent")

    inbound.handle_message.assert_not_called()
    assert active == "booking_agent"
    assert isinstance(result, AgentResponse)


# ------------------------------------------------------------------ #
# 12. Regression: BUG 1 — corporate_events_agent route triggers BA   #
# ------------------------------------------------------------------ #

def test_dispatch_fires_booking_agent_for_corporate_events_agent_route(tmp_path: Path) -> None:
    """
    Regression: Router returns next_agent='corporate_events_agent' for corporate_event,
    NOT 'booking_agent'.  dispatch() must still activate BookingAgent when should_handoff=True,
    regardless of which specific agent the Router names.

    This was the root cause of the production transcript showing
    'Route: corporate_events_agent | handoff=True' but BookingAgent never activating.
    """
    import main as main_module

    from src.inbound_agent import InboundAgent
    from src.knowledge_loader import KnowledgeLoader

    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = make_memory(tmp_path, {"intent": "corporate_event"})
    inbound = InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_ollama=False,
    )

    # Simulate what the Router actually returns for a qualified corporate event
    mock_inbound_result = AgentResponse(
        response="I've captured all the information.",
        intent="corporate_event",
        next_agent="corporate_events_agent",   # ← this is what Router returns in reality
        should_handoff=True,
        state=memory.as_state(),
    )
    inbound.handle_message = MagicMock(return_value=mock_inbound_result)

    result, booking_inst, active = main_module.dispatch(
        "9983340357", inbound, None, "inbound_agent"
    )

    assert active == "booking_agent", \
        "dispatch() must switch to booking_agent on any should_handoff=True, not just next_agent=='booking_agent'"
    assert booking_inst is not None
    # The response must be from BookingAgent (availability check), NOT the handoff message
    assert "availability" in result.response.lower() or "slot" in result.response.lower() or \
           "AM" in result.response or "PM" in result.response, \
        f"Expected BookingAgent availability response, got: {result.response}"


# ------------------------------------------------------------------ #
# 13. Regression: BUG 2 — Natural language budget phrases            #
# ------------------------------------------------------------------ #

def _make_qa_at_budget_step(tmp_path: Path) -> tuple:
    """
    Helper: create a QualificationAgent with all fields before budget_range
    already captured, so the next expected field is budget_range.
    Corporate event fields in order: location, preferred_date, food_required,
    budget_range, customer_name, phone.
    """
    from src.qualification_agent import QualificationAgent
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update({
        "intent": "corporate_event",
        "event_type": "Corporate Event",
        "company_size": 20,
        "participants": 20,
        "location": "Whitefield",
        "preferred_date": "18 June",
        "food_required": True,
    })
    memory.save()
    qa = QualificationAgent(memory)
    qa._waiting_for = "budget_range"
    return qa, memory


def test_budget_natural_phrase_go_to_premium(tmp_path: Path) -> None:
    """'We would like to go to premium options' must capture budget_range=premium."""
    qa, memory = _make_qa_at_budget_step(tmp_path)
    qa.update_and_qualify("We would like to go to premium options", "corporate_event")
    assert memory.data.get("budget_range") == "premium", \
        f"Expected 'premium', got: {memory.data.get('budget_range')!r}"


def test_budget_natural_phrase_premium_option(tmp_path: Path) -> None:
    """'premium option' must store 'premium'."""
    qa, memory = _make_qa_at_budget_step(tmp_path)
    qa.update_and_qualify("premium option", "corporate_event")
    assert memory.data.get("budget_range") == "premium"


def test_budget_natural_phrase_standard_package(tmp_path: Path) -> None:
    """'standard package' must store 'standard'."""
    qa, memory = _make_qa_at_budget_step(tmp_path)
    qa.update_and_qualify("standard package", "corporate_event")
    assert memory.data.get("budget_range") == "standard"


def test_budget_natural_phrase_go_with_basic(tmp_path: Path) -> None:
    """'let's go with the basic plan' must store 'basic'."""
    qa, memory = _make_qa_at_budget_step(tmp_path)
    qa.update_and_qualify("let's go with the basic plan", "corporate_event")
    assert memory.data.get("budget_range") == "basic"


def test_budget_bare_word_premium_still_works(tmp_path: Path) -> None:
    """Bare 'Premium' must still work after the refactor."""
    qa, memory = _make_qa_at_budget_step(tmp_path)
    qa.update_and_qualify("Premium", "corporate_event")
    assert memory.data.get("budget_range") == "premium"


def test_budget_stores_canonical_not_raw_utterance(tmp_path: Path) -> None:
    """
    The stored budget_range must be the canonical tier word,
    NOT the entire raw utterance.  This was the original bug:
    memory stored 'we would like to go to premium options' verbatim.
    """
    qa, memory = _make_qa_at_budget_step(tmp_path)
    qa.update_and_qualify("We would like to go to premium options", "corporate_event")
    stored = memory.data.get("budget_range", "")
    assert stored == "premium", f"Must store 'premium', not the full utterance. Got: {stored!r}"
    assert len(stored) < 20, "Stored budget should be a short canonical value, not a full sentence"


def test_budget_extract_canonical_covers_voice_phrases(tmp_path: Path) -> None:
    """Unit-test _extract_budget_canonical directly against all expected voice phrases."""
    from src.qualification_agent import QualificationAgent
    extract = QualificationAgent._extract_budget_canonical

    assert extract("Premium") == "premium"
    assert extract("premium option") == "premium"
    assert extract("premium package") == "premium"
    assert extract("premium plan") == "premium"
    assert extract("We would like to go to premium options") == "premium"
    assert extract("go with premium") == "premium"
    assert extract("Standard") == "standard"
    assert extract("standard package") == "standard"
    assert extract("regular package") == "standard"
    assert extract("Basic") == "basic"
    assert extract("basic option") == "basic"
    assert extract("something completely unrelated") == ""


def test_budget_is_valid_covers_natural_phrases(tmp_path: Path) -> None:
    """Unit-test _is_valid_budget against natural voice phrases."""
    from src.qualification_agent import QualificationAgent
    valid = QualificationAgent._is_valid_budget

    assert valid("Premium")
    assert valid("premium option")
    assert valid("We would like to go to premium options")
    assert valid("go with standard plan")
    assert valid("basic package")
    assert not valid("I don't know")
    assert not valid("yes")
    assert not valid("ok")


# ------------------------------------------------------------------ #
# 14. Regression: BUG 3 — Post-qualification state lock              #
# ------------------------------------------------------------------ #

def test_post_handoff_messages_route_to_booking_agent_not_inbound(tmp_path: Path) -> None:
    """
    After qualification completes, subsequent customer messages must go to BookingAgent,
    NOT back through InboundAgent (which would repeat the handoff message forever).
    """
    import main as main_module

    from src.inbound_agent import InboundAgent
    from src.knowledge_loader import KnowledgeLoader

    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = make_memory(tmp_path, {"intent": "corporate_event"})
    inbound = InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_ollama=False,
    )

    # Simulate the handoff turn
    mock_handoff = AgentResponse(
        response="I've captured all the information.",
        intent="corporate_event",
        next_agent="corporate_events_agent",
        should_handoff=True,
        state=memory.as_state(),
    )
    inbound.handle_message = MagicMock(return_value=mock_handoff)

    # Handoff fires — active_agent becomes "booking_agent"
    _, booking_inst, active = main_module.dispatch("9983340357", inbound, None, "inbound_agent")
    assert active == "booking_agent"

    # Second customer message — MUST go to BookingAgent, not InboundAgent
    inbound.handle_message.reset_mock()
    result, _, active2 = main_module.dispatch("10 AM", inbound, booking_inst, "booking_agent")

    inbound.handle_message.assert_not_called(), \
        "InboundAgent must NOT handle messages after booking_agent is active"
    assert active2 == "booking_agent"
    # Response should be booking confirmation (not the repeated handoff message)
    assert "I've captured all the information" not in result.response, \
        f"Repeated handoff message detected: {result.response}"

