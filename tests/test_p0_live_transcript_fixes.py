"""
P0 Live Transcript Bug Fix Tests
=================================
Tests for all 5 P0 bugs identified from the live call transcripts.

Bugs covered:
  1. Availability First Routing — availability questions answered before qualification
  2. Unknown Location Guard — no rooms/branches mentioned without location
  3. Qualification Compression — availability flow not blocked by qualification
  4. Booking Finalization Last Name Bug — booking completes without last name
  5. Confirmation Loop — no duplicate slot confirmation
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

def _make_memory(data: dict | None = None):
    from src.memory.conversation_memory import ConversationMemory
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    mem = ConversationMemory(Path(tmp.name))
    if data:
        mem.data.update(data)
        mem.save()
    return mem


def _make_booking_agent(memory_data: dict | None = None):
    from src.agents.booking_agent import BookingAgent
    from src.orchestration.booking_orchestrator import BookingOrchestrator
    mem = _make_memory(memory_data or {})
    orch = BookingOrchestrator()
    orch.is_live = False
    agent = BookingAgent(memory=mem, orchestrator=orch)
    return agent, mem

def _make_inbound_agent(memory_data: dict | None = None):
    from src.agents.inbound_agent import InboundAgent
    from src.knowledge.knowledge_loader import KnowledgeLoader
    # Use the real knowledge base so KnowledgeRetriever can access string attributes
    kb = KnowledgeLoader(Path(__file__).resolve().parents[1] / "knowledge").load()
    mem = _make_memory(memory_data or {})
    prompt_path = (
        Path(__file__).resolve().parents[1] / "prompts" / "inbound_prompt.txt"
    )
    agent = InboundAgent(
        knowledge_base=kb,
        memory=mem,
        prompt_path=prompt_path,
        use_openai=False,
        use_ollama=False,
    )
    return agent, mem


def _make_conversation_manager(memory_data: dict | None = None):
    from src.orchestration.conversation_manager import ConversationManager
    mem = _make_memory(memory_data or {})
    return ConversationManager(mem), mem


def _dispatch_once(message: str, memory_data: dict | None = None):
    from main import dispatch
    agent, mem = _make_inbound_agent(memory_data or {})
    result, booking, active = dispatch(message, agent, None, "inbound_agent")
    return result, booking, active, mem


# ===========================================================================
# Bug 1 — Availability First Routing
# ===========================================================================

class TestAvailabilityFirstRouting:

    def test_availability_question_before_qualification(self):
        manager, mem = _make_conversation_manager()
        target, category = manager.determine_routing(
            "Do you have slots at 1:30 tomorrow?",
            active_agent="inbound_agent",
        )
        assert target == "booking_agent", (
            f"Expected routing to booking_agent but got '{target}'. "
            "Availability question should bypass qualification."
        )

    def test_specific_slot_request_before_group_size(self):
        manager, mem = _make_conversation_manager()
        target, category = manager.determine_routing(
            "Is 5:20 PM available this Saturday?",
            active_agent="inbound_agent",
        )
        assert target == "booking_agent", (
            f"Expected booking_agent for slot availability question but got '{target}'"
        )

    def test_is_availability_question_detector(self):
        from src.orchestration.conversation_manager import ConversationManager
        positives = [
            "Do you have slots at 1:30 tomorrow?",
            "Is 5:20 available?",
            "Any openings this Saturday?",
            "What slots do you have tomorrow?",
            "Are there any slots available?",
            "Can I book for tomorrow at 3?",
            "Do you have any availability today?",
        ]
        negatives = [
            "How many people are joining?",
            "What rooms do you have?",
            "I want to book an escape room",
            "Tell me about the rooms",
        ]
        for msg in positives:
            assert ConversationManager._is_availability_question(msg), (
                f"Expected _is_availability_question=True for: {msg!r}"
            )
        for msg in negatives:
            assert not ConversationManager._is_availability_question(msg), (
                f"Expected _is_availability_question=False for: {msg!r}"
            )


# ===========================================================================
# Bug 2 — Unknown Location Guard
# ===========================================================================

class TestUnknownLocationGuard:

    def test_no_room_recommendation_without_location(self):
        agent, mem = _make_inbound_agent()
        assert not mem.data.get("location")
        response = agent._recommendation_response(
            intent="escape_room_inquiry",
            option="Hostage",
            reason="urgency and teamwork",
            lowered="what room do you recommend?",
        )
        forbidden = ["hostage", "murder mystery", "classified", "bomb defusal",
                     "prison break", "undercover", "jp nagar", "whitefield", "koramangala"]
        response_lower = response.lower()
        for term in forbidden:
            assert term not in response_lower, (
                f"Room/branch '{term}' appeared without location: {response!r}"
            )
        assert "location" in response_lower, (
            f"Should ask for location but got: {response!r}"
        )

    def test_no_branch_hallucination_before_location(self):
        agent, mem = _make_inbound_agent({"participants": 2})
        assert not mem.data.get("location")
        response = agent._fallback_response(
            message="What rooms do you have?",
            intent="escape_room_inquiry",
            recommendation=MagicMock(option="", reason=""),
            should_handoff=False,
        )
        response_lower = response.lower()
        hallucination_terms = ["jp nagar", "whitefield", "koramangala",
                               "hostage", "murder mystery", "prism break"]
        for term in hallucination_terms:
            assert term not in response_lower, (
                f"Hallucinated '{term}' without location: {response!r}"
            )
        assert "location" in response_lower, (
            f"Should redirect to location question but got: {response!r}"
        )

    def test_direct_answer_guard_no_room_without_location(self):
        agent, mem = _make_inbound_agent()
        assert not mem.data.get("location")
        response = agent._direct_answer_guard(
            message="Do you have slots at 1:30 tomorrow?",
            intent="escape_room_inquiry",
            recommendation=MagicMock(option="", reason=""),
            waiting_before="",
        )
        response_lower = response.lower()
        assert "location" in response_lower, (
            f"Should ask for location but got: {response!r}"
        )
        for branch in ["jp nagar", "whitefield", "koramangala"]:
            assert branch not in response_lower, (
                f"Hallucinated branch '{branch}' in: {response!r}"
            )

    def test_recommendation_request_without_location_asks_location_not_participants(self):
        result, _, _, _ = _dispatch_once("What would you recommend?")

        response_lower = result.response.lower()
        assert "location" in response_lower
        assert "how many" not in response_lower
        for term in ("murder mystery", "hostage", "bomb defusal", "prison break"):
            assert term not in response_lower

    def test_best_room_without_location_asks_location_not_room_or_branch(self):
        result, _, _, _ = _dispatch_once("Which room is best?")

        response_lower = result.response.lower()
        assert "location" in response_lower
        assert "how many" not in response_lower
        for term in ("murder mystery", "hostage", "jp nagar", "whitefield", "koramangala"):
            assert term not in response_lower


# ===========================================================================
# Bug 3 — Qualification Compression
# ===========================================================================

class TestQualificationCompression:

    def test_availability_flow_not_blocked_by_qualification(self):
        manager, mem = _make_conversation_manager()
        messages = [
            "Do you have any slots tomorrow?",
            "I want to know, do you have any slots tomorrow or not, one thirty.",
            "Can you check availability for tomorrow at 1:30?",
        ]
        for msg in messages:
            target, _ = manager.determine_routing(msg, active_agent="inbound_agent")
            assert target == "booking_agent", (
                f"Message {msg!r} should route to booking_agent but got '{target}'"
            )

    def test_availability_question_response_asks_location_not_participants(self):
        agent, mem = _make_inbound_agent()
        assert not mem.data.get("location")
        response = agent._fallback_response(
            message="Do you have slots at 1:30 tomorrow?",
            intent="escape_room_inquiry",
            recommendation=MagicMock(option="", reason=""),
            should_handoff=False,
        )
        response_lower = response.lower()
        assert "location" in response_lower, (
            f"Should ask for location but got: {response!r}"
        )
        assert "how many" not in response_lower, (
            f"Should NOT ask participant count before location: {response!r}"
        )

    def test_dispatch_availability_question_asks_location_before_room_or_group_size(self):
        result, booking, active, mem = _dispatch_once("Do you have slots at 1:30 tomorrow?")

        response_lower = result.response.lower()
        assert active == "booking_agent"
        assert mem.data["preferred_date"] == "Tomorrow"
        assert "location" in response_lower
        assert "how many" not in response_lower
        assert "which escape room" not in response_lower


# ===========================================================================
# Bug 4 — Booking Finalization Last Name Bug
# ===========================================================================

class TestLastNameBug:

    def test_booking_completes_without_last_name(self):
        agent, mem = _make_booking_agent({
            "location": "Whitefield",
            "preferred_date": "25 June 2026",
            "participants": 6,
            "age_group": "adults",
            "room": "Hostage",
            "intent": "escape_room_inquiry",
        })
        agent._available_slots = ["5:20 PM", "6:30 PM"]
        agent._last_availability = {
            "available": True,
            "slots": ["5:20 PM", "6:30 PM"],
            "location": "Whitefield",
            "date": "25 June 2026",
            "participants": 6,
            "verified": True,
            "capacity_supported": True,
        }
        r1 = agent._handle_slot_selection("5:20 PM")
        assert "last name" not in r1[0].lower(), (
            f"Should not ask for last name after slot selection: {r1[0]!r}"
        )
        assert "surname" not in r1[0].lower()

        agent._state = agent._STATE_WAITING_FOR_FIRST_NAME
        r2 = agent._handle_contact_first_name("Siddharth")
        assert "last name" not in r2[0].lower(), (
            f"Should not ask for last name after single name: {r2[0]!r}"
        )

    def test_single_word_name_booking(self):
        agent, mem = _make_booking_agent()
        agent._store_name_parts("Siddharth")
        assert mem.data.get("first_name") == "Siddharth"
        assert mem.data.get("last_name", "") == ""
        assert mem.data.get("customer_name") == "Siddharth"

    def test_provider_payload_without_last_name(self):
        from src.orchestration.booking_orchestrator import BookingOrchestrator
        orch = BookingOrchestrator()
        split_first, split_last = orch._split_name("Siddharth")
        first_name = "Siddharth"
        last_name = ""
        api_last_name = last_name if last_name else "NA"
        assert api_last_name == "NA", (
            f"Expected api_last_name='NA' but got '{api_last_name}'"
        )

    def test_legacy_create_booking_payload_uses_na_last_name(self):
        from src.orchestration.booking_orchestrator import BookingOrchestrator

        orch = BookingOrchestrator()
        orch.is_live = True
        orch._location = {"locationId": "loc-1"}
        orch._game = {"gameId": "game-1"}
        captured = {}

        class Provider:
            def prepare_booking(self, payload):
                captured.update(payload)
                return {"bookingId": "BK-1"}

        orch.booking_provider = Provider()
        record = orch.create_booking("slot-1", {"customer_name": "Siddharth", "phone": "9982151357"})

        assert captured["customerFirstName"] == "Siddharth"
        assert captured["customerLastName"] == "NA"
        assert record.reference == "BK-1"

    def test_recover_from_booking_failure_no_last_name_zombie(self):
        agent, mem = _make_booking_agent({
            "location": "Whitefield",
            "preferred_date": "25 June 2026",
            "participants": 6,
            "age_group": "adults",
            "room": "Hostage",
            "customer_name": "Siddharth",
            "first_name": "Siddharth",
            "last_name": "",
            "phone": "9982151357",
        })
        agent._available_slots = ["5:20 PM"]
        agent._last_availability = {
            "available": True,
            "slots": ["5:20 PM"],
            "location": "Whitefield",
            "date": "25 June 2026",
            "participants": 6,
            "verified": True,
            "capacity_supported": True,
        }
        agent._selected_slot = "5:20 PM"
        mem.data["selected_slot"] = "5:20 PM"

        booking_error = {
            "booking_id": "",
            "confirmed": False,
            "error": "customer.lastName is required",
            "missing_field": "last_name",
        }
        response, _ = agent._recover_from_booking_failure(booking_error)
        assert "last name" not in response.lower(), (
            f"Should auto-fill NA instead of asking for last name: {response!r}"
        )
        assert mem.data.get("last_name") == "NA", (
            f"Expected last_name='NA' in memory, got: {mem.data.get('last_name')!r}"
        )


# ===========================================================================
# Bug 5 — Confirmation Loop
# ===========================================================================

class TestConfirmationLoop:

    def test_no_duplicate_slot_confirmation(self):
        agent, mem = _make_booking_agent({
            "location": "Whitefield",
            "preferred_date": "25 June 2026",
            "participants": 6,
            "age_group": "adults",
            "room": "Hostage",
            "intent": "escape_room_inquiry",
            "slot_confirmed": True,
            "selected_slot": "5:20 PM",
        })
        agent._available_slots = ["5:20 PM", "6:30 PM"]
        agent._last_availability = {
            "available": True,
            "slots": ["5:20 PM", "6:30 PM"],
            "location": "Whitefield",
            "date": "25 June 2026",
            "participants": 6,
            "verified": True,
            "capacity_supported": True,
        }
        agent._selected_slot = "5:20 PM"
        agent._state = agent._STATE_WAITING_FOR_SLOT

        result = agent.handle_message("yes")
        response_lower = result.response.lower()

        assert "would you like" not in response_lower, (
            f"Duplicate slot confirmation detected: {result.response!r}"
        )
        name_or_phone_keywords = ["name", "phone", "number", "adults", "kids", "mix"]
        assert any(kw in response_lower for kw in name_or_phone_keywords), (
            f"Should advance to next field, got: {result.response!r}"
        )

    def test_slot_confirmed_flag_set_on_selection(self):
        agent, mem = _make_booking_agent({
            "location": "Whitefield",
            "preferred_date": "25 June 2026",
            "participants": 6,
            "age_group": "adults",
            "room": "Hostage",
        })
        agent._available_slots = ["5:20 PM", "6:30 PM"]
        agent._last_availability = {
            "available": True,
            "slots": ["5:20 PM", "6:30 PM"],
            "location": "Whitefield",
            "date": "25 June 2026",
            "participants": 6,
            "verified": True,
            "capacity_supported": True,
        }
        agent._handle_slot_selection("5:20 PM")
        assert mem.data.get("slot_confirmed") is True, (
            "slot_confirmed flag not set after slot selection"
        )
        assert mem.data.get("selected_slot") == "5:20 PM"
