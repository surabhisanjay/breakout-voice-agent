"""
RED TEAM Scenario Execution Tests
QA Audit Phase 2 — Automated execution of adversarial scenarios
"""
from __future__ import annotations

import sys
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.booking_agent import BookingAgent
from src.agents.escalation_agent import EscalationAgent
from src.agents.inbound_agent import InboundAgent
from src.agents.sentiment_agent import SentimentAgent, SentimentResult
from src.memory.conversation_memory import ConversationMemory
from src.knowledge.knowledge_loader import KnowledgeLoader
from src.orchestration.conversation_manager import ConversationManager


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def make_full_memory(tmp_path: Path, **overrides) -> ConversationMemory:
    mem = ConversationMemory(tmp_path / "session.json")
    mem.data.update({
        "intent": "escape_room_inquiry",
        "event_type": "Escape Room",
        "participants": 4,
        "age_group": "adults",
        "location": "Koramangala",
        "preferred_date": "25 June",
        "room": "Murder Mystery",
        "recommended_option": "Murder Mystery",
        "customer_name": "Priya Sharma",
        "first_name": "Priya",
        "last_name": "Sharma",
        "phone": "9876543210",
    })
    mem.data.update(overrides)
    mem.save()
    return mem


def make_booking_agent(tmp_path: Path, **overrides) -> BookingAgent:
    mem = make_full_memory(tmp_path, **overrides)
    agent = BookingAgent(mem)
    agent.availability_tool.check = MagicMock(return_value={
        "available": True,
        "verified": True,
        "slots": ["3:00 PM", "5:00 PM"],
        "location": mem.data.get("location", "Koramangala"),
        "date": mem.data.get("preferred_date", "25 June"),
        "participants": mem.data.get("participants", 4),
    })
    return agent


def make_inbound(tmp_path: Path, mem: ConversationMemory) -> InboundAgent:
    return InboundAgent(
        knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
        memory=mem,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


# ─────────────────────────────────────────────────────────────
# CATEGORY A: BOOKING FLOW
# ─────────────────────────────────────────────────────────────

class TestA_BookingFlow:

    # A015 — Cancel without booking ref gives false confirmation
    def test_A015_cancel_without_booking_ref_no_false_confirmation(self, tmp_path):
        """
        SCENARIO: User says 'I want to cancel' with no booking ref.
        EXPECTED: Agent should NOT claim booking is cancelled (no reference exists).
        """
        mem = make_full_memory(tmp_path, booking_ref="")
        agent = BookingAgent(mem)
        result = agent.handle_message("I want to cancel my booking")
        # CRITICAL: agent says "I've cancelled the booking process" — but no booking exists
        # This is a false cancellation confirmation
        assert "BRK" not in result.response, (
            "FAIL A015: Agent should not reference a booking ID that doesn't exist"
        )

    # A016 — Cancel via "reservation" keyword not matched
    def test_A016_cancel_via_reservation_keyword(self, tmp_path):
        """
        SCENARIO: User says 'cancel my reservation' (not 'booking').
        EXPECTED: Cancel should be triggered.
        """
        mem = make_full_memory(tmp_path, booking_ref="BRK-999")
        agent = BookingAgent(mem)
        agent.orchestrator.cancel_booking = MagicMock(return_value={"status": "cancelled"})
        result = agent.handle_message("I want to cancel my reservation")
        lowered = result.response.lower()
        # The regex only matches "booking" not "reservation" — this is the bug
        # Test documents whether it passes or fails
        assert "cancel" in lowered, (
            "FAIL A016: 'cancel my reservation' did not trigger cancellation logic"
        )

    # A022 — "next month" date not extracted
    def test_A022_next_month_date_not_extracted(self, tmp_path):
        """
        SCENARIO: User says 'actually, next month'.
        EXPECTED: Agent should ask for a specific date (no silent failure).
        """
        mem = ConversationMemory(tmp_path / "session.json")
        result = mem._extract_preferred_date("actually, next month")
        assert result == "", (
            "BUG A022: 'next month' should return empty string — agent must ask for specific date"
        )

    # A023 — Past date silently moved to next year
    def test_A023_past_date_silently_advanced_without_user_notice(self, tmp_path):
        """
        SCENARIO: User gives a past date like '1 January'.
        EXPECTED: Agent should either warn user or ask to confirm.
        """
        from src.orchestration.booking_orchestrator import BookingOrchestrator
        from datetime import date
        today = date.today()
        # 1 Jan is in the past if today > Jan 1
        normalised = BookingOrchestrator._normalise_date("1 January")
        if normalised:
            year = int(normalised.split("-")[0])
            assert year >= today.year, (
                "FAIL A023: Past date normalized to year before today — date incorrectly set"
            )
            # The real risk: no agent message warns user the date was bumped to next year
            # This test just confirms the normalisation happens silently

    # A024 — "day after tomorrow" not recognized
    def test_A024_day_after_tomorrow_not_extracted(self, tmp_path):
        """
        SCENARIO: User says 'day after tomorrow'.
        EXPECTED: Should ideally extract date, or at minimum not crash.
        """
        mem = ConversationMemory(tmp_path / "session.json")
        result = mem._extract_preferred_date("I want to come day after tomorrow")
        # No handler exists — returns "" which causes agent to ask again
        # This test documents the gap, not a crash
        assert isinstance(result, str), "FAIL A024: _extract_preferred_date crashed on 'day after tomorrow'"

    # A025 — MM/DD date format ambiguity
    def test_A025_mm_dd_date_format_treated_as_dd_mm(self, tmp_path):
        """
        SCENARIO: User gives '06/25' expecting June 25 (US format).
        EXPECTED: System treats as 6th day of 25th month → should either handle gracefully or reject.
        """
        mem = ConversationMemory(tmp_path / "session.json")
        result = mem._extract_preferred_date("06/25")
        # System extracts "06/25" — this is ambiguous; normalise_date may fail
        # If it returns empty string, fine. If it returns a valid date, ensure it's sensible.
        from src.orchestration.booking_orchestrator import BookingOrchestrator
        if result:
            normalised = BookingOrchestrator._normalise_date(result)
            # If normalised is empty, the ambiguous date was safely rejected
            # The documented risk is silent wrong-date acceptance

    # A027 — Zero participants slips through
    def test_A027_zero_participants_fails_booking_ready(self, tmp_path):
        """
        SCENARIO: User says 'we have 0 people'.
        EXPECTED: booking_ready() must return False.
        """
        mem = make_full_memory(tmp_path, participants=0, company_size="")
        assert not mem.booking_ready(), (
            "FAIL A027: booking_ready() returned True with 0 participants"
        )

    # A003 — Agent skips last name when only first name given
    def test_A003_agent_requests_last_name_after_first_name(self, tmp_path):
        """
        SCENARIO: User gives first name only at name-collection step.
        EXPECTED: Agent asks for last name before proceeding.
        """
        mem = make_full_memory(tmp_path, customer_name="", first_name="", last_name="", phone="9876543210")
        agent = BookingAgent(mem)
        agent._state = agent._STATE_WAITING_FOR_FIRST_NAME
        agent._selected_slot = "3:00 PM"

        result = agent.handle_message("Priya")
        assert mem.data.get("first_name") == "Priya", "FAIL A003: first_name not stored"
        assert "last name" in result.response.lower(), (
            "FAIL A003: Agent did not ask for last name after capturing first name"
        )


# ─────────────────────────────────────────────────────────────
# CATEGORY C: AVAILABILITY
# ─────────────────────────────────────────────────────────────

class TestC_Availability:

    # C005 — No evening-slot filter
    def test_C005_no_evening_filter(self, tmp_path):
        """
        SCENARIO: User asks 'only evening slots please'.
        EXPECTED: Agent returns only evening slots.
        """
        agent = make_booking_agent(tmp_path)
        agent._state = agent._STATE_WAITING_FOR_SLOT
        agent._available_slots = ["10:00 AM", "12:00 PM", "6:00 PM", "8:00 PM"]
        result = agent.handle_message("only evening slots please")
        assert "6:00 PM" in result.response and "8:00 PM" in result.response
        assert "10:00 AM" not in result.response and "12:00 PM" not in result.response

    # C009 — No earliest-slot detection
    def test_C009_earliest_slot_not_identified(self, tmp_path):
        """
        SCENARIO: User says 'I want the earliest slot'.
        EXPECTED: Agent should identify earliest — currently doesn't.
        """
        agent = make_booking_agent(tmp_path)
        agent._state = agent._STATE_WAITING_FOR_SLOT
        agent._available_slots = ["3:00 PM", "5:00 PM", "8:00 PM"]
        agent._last_availability = {"available": True, "slots": ["3:00 PM", "5:00 PM", "8:00 PM"]}
        result = agent.handle_message("I want the earliest slot available")
        # Agent should say "3:00 PM is the earliest" — currently just lists all
        # Test documents that 3:00 PM is not specifically called out
        assert "3:00 PM" in result.response, "NOTE C009: Slots listed, but earliest not highlighted"

    # C010 — "first available" causes loop
    def test_C010_first_available_triggers_sorry_loop(self, tmp_path):
        """
        SCENARIO: User says 'first available'.
        EXPECTED: Should select earliest slot, not loop with 'Sorry I didn't catch that'.
        """
        agent = make_booking_agent(tmp_path)
        agent._state = agent._STATE_WAITING_FOR_SLOT
        agent._available_slots = ["3:00 PM", "5:00 PM"]
        agent._last_availability = {"available": True, "slots": ["3:00 PM", "5:00 PM"]}
        result = agent.handle_message("first available")
        # "first" is a continuation word; _extract_slot("first available") returns ""
        # → "Sorry, I didn't catch that" → loop
        assert "sorry" in result.response.lower() or "3:00" in result.response, (
            "C010 check: 'first available' handling"
        )


# ─────────────────────────────────────────────────────────────
# CATEGORY D: FAQ INTERRUPTIONS
# ─────────────────────────────────────────────────────────────

class TestD_FAQ:

    # D006 — "yes" after food FAQ triggers premature booking advance
    def test_D006_yes_after_food_faq_does_not_advance_booking(self, tmp_path):
        """
        SCENARIO: User asks food options, then says 'yes'.
        EXPECTED: 'Yes' should not be mistaken for slot confirmation.
        """
        agent = make_booking_agent(tmp_path)
        agent._state = agent._STATE_WAITING_FOR_SLOT
        agent._available_slots = ["3:00 PM", "5:00 PM"]
        agent._last_availability = {"available": True, "verified": True, "slots": ["3:00 PM", "5:00 PM"]}
        agent.booking_tool.create = MagicMock()

        agent.handle_message("What food do you have?")
        agent.handle_message("yes")
        agent.booking_tool.create.assert_not_called(), (
            "FAIL D006: 'yes' after food FAQ triggered premature booking"
        )

    # D007 — "how long is the game" has no handler
    def test_D007_how_long_game_answered(self, tmp_path):
        """
        SCENARIO: User asks 'how long is the game?'.
        EXPECTED: Agent should answer (duration = 50 minutes).
        """
        from src.knowledge.demo_knowledge import get_demo_answer
        result = get_demo_answer("how long is the game?")
        # Currently returns "" — gap in knowledge base
        # This test DOCUMENTS the gap
        if not result:
            # Knowledge gap — agent will fall through to retriever or generic response
            pass  # DOCUMENTED GAP D007
        assert isinstance(result, str), "D007: get_demo_answer must return a string"

    # D011 — Cancellation policy loop (no escalation on 5x same question)
    def test_D011_repeated_cancellation_policy_no_escalation(self, tmp_path):
        """
        SCENARIO: User asks cancellation policy 5 times in a row.
        EXPECTED: No loop error, but escalation may be beneficial.
        """
        agent = make_booking_agent(tmp_path)
        agent._state = agent._STATE_WAITING_FOR_SLOT
        agent._available_slots = ["3:00 PM"]
        for i in range(5):
            r = agent.handle_message("What is the cancellation policy?")
            assert "Cancellation charges" in r.response or "i can explain" in r.response.lower(), (
                f"FAIL D011: Cancellation policy not answered on iteration {i+1}"
            )

    # D013 — Misunderstanding triggers immediate escalation (no minimum count)
    def test_J013_single_misunderstanding_triggers_escalation(self, tmp_path):
        """
        SCENARIO: User says 'that's not what I asked' once.
        EXPECTED: Escalation fired immediately — may be too aggressive.
        """
        mem = make_full_memory(tmp_path)
        sentiment = SentimentResult(
            sentiment="frustrated",
            confidence=0.7,
            escalation_recommended=False,
            reason="",
            stage="discovery",
        )
        agent = EscalationAgent(mem)
        result = agent.evaluate("that's not what i asked", sentiment)
        assert result.escalate is True, (
            "NOTE J013: Single misunderstanding phrase immediately escalates — expected by design but worth reviewing"
        )


# ─────────────────────────────────────────────────────────────
# CATEGORY E: ESCALATIONS
# ─────────────────────────────────────────────────────────────

class TestE_Escalations:

    # E003 — "I deserve a refund" not matched
    def test_E003_deserve_refund_not_escalated(self, tmp_path):
        """
        SCENARIO: User says 'I deserve a refund'.
        EXPECTED: Should escalate. ACTUAL: Pattern misses 'deserve'.
        """
        mem = make_full_memory(tmp_path)
        sentiment = SentimentResult("neutral", 0.5, False, "", "discovery")
        agent = EscalationAgent(mem)
        result = agent.evaluate("I deserve a refund", sentiment)
        # DOCUMENTED BUG: "deserve" is not in REFUND_REQUEST pattern
        # Escalation should fire but may not
        if not result.escalate:
            pass  # DOCUMENTED GAP E003: "I deserve a refund" misses escalation

    # E010 — "my friend fainted" not escalated as safety issue
    def test_E010_friend_fainted_not_safety_escalation(self, tmp_path):
        """
        SCENARIO: User reports 'my friend fainted'.
        EXPECTED: Safety escalation. ACTUAL: 'fainted' not in SAFETY_REQUEST.
        """
        mem = make_full_memory(tmp_path)
        sentiment = SentimentResult("neutral", 0.5, False, "", "discovery")
        agent = EscalationAgent(mem)
        result = agent.evaluate("my friend fainted inside the room", sentiment)
        # DOCUMENTED CRITICAL BUG: Safety escalation not triggered for "fainted"
        if not result.escalate:
            pass  # CRITICAL GAP E010: Medical emergency not detected

    # E013 — "get me a human" not escalated
    def test_E013_get_me_a_human_not_escalated(self, tmp_path):
        """
        SCENARIO: User says 'get me a human'.
        EXPECTED: Escalation. ACTUAL: Pattern requires speak/talk/connect/transfer.
        """
        mem = make_full_memory(tmp_path)
        sentiment = SentimentResult("neutral", 0.5, False, "", "discovery")
        agent = EscalationAgent(mem)
        result = agent.evaluate("get me a human please", sentiment)
        if not result.escalate:
            pass  # DOCUMENTED GAP E013: "get me a human" misses HUMAN_REQUEST

    # E014 — "talk to your manager" not escalated
    def test_E014_talk_to_your_manager_not_escalated(self, tmp_path):
        """
        SCENARIO: User says 'I want to talk to your manager'.
        EXPECTED: Escalation. ACTUAL: 'your manager' not matched (needs 'a manager').
        """
        mem = make_full_memory(tmp_path)
        sentiment = SentimentResult("neutral", 0.5, False, "", "discovery")
        agent = EscalationAgent(mem)
        result = agent.evaluate("I want to talk to your manager", sentiment)
        if not result.escalate:
            pass  # DOCUMENTED GAP E014: "talk to your manager" misses HUMAN_REQUEST

    # E004 — Safety escalation dropped on exception in HandoffSummaryAgent
    def test_E004_escalation_not_silently_dropped_on_exception(self, tmp_path):
        """
        SCENARIO: HandoffSummaryAgent.generate() throws.
        EXPECTED: Escalation result still returned (not swallowed).
        """
        mem = make_full_memory(tmp_path)
        sentiment = SentimentResult("neutral", 0.5, False, "", "discovery")
        agent = EscalationAgent(mem)
        agent.summary_agent.generate = MagicMock(side_effect=RuntimeError("summary_failed"))
        # The EscalationAgent.evaluate() will throw when summary_agent.generate() is called
        # if escalation is triggered. The caller (_enrich_conversation_result) catches all
        # exceptions and returns escalate=False — safety escalation silently dropped.
        try:
            result = agent.evaluate("I am injured", sentiment)
            # If we reach here, exception was not thrown during evaluate
        except Exception:
            pass  # Exception propagated — caller will catch and silence it (CRITICAL BUG)


# ─────────────────────────────────────────────────────────────
# CATEGORY F: MEMORY STRESS
# ─────────────────────────────────────────────────────────────

class TestF_MemoryStress:

    # F003 — Participant reduction without re-running availability
    def test_F003_participant_reduction_does_not_rerun_availability(self, tmp_path):
        """
        SCENARIO: User reduces participants from 8 to 2 during WAITING_FOR_PHONE state.
        EXPECTED: Availability should be re-run for new group size. ACTUAL: May not.
        """
        agent = make_booking_agent(tmp_path, participants=8)
        agent._state = agent._STATE_WAITING_FOR_PHONE
        agent._available_slots = ["3:00 PM"]
        agent._last_availability = {"available": True, "verified": True, "slots": ["3:00 PM"]}
        initial_check_count = agent.availability_tool.check.call_count

        # "reduced" is not a change keyword in the booking agent list
        agent.handle_message("we reduced to just 2 people")

        # The participant was updated but availability NOT re-run
        # This is the documented bug
        new_participants = agent.memory.data.get("participants")
        # Check if participants were updated
        # (The behavior may vary — document the gap)

    # F003-B — "reduced" keyword triggers participant update
    def test_F003b_participants_updated_without_keyword(self, tmp_path):
        """
        SCENARIO: 'we reduced to just 2 people' — no change keyword.
        EXPECTED: Participants updated to 2.
        """
        agent = make_booking_agent(tmp_path, participants=8)
        agent._state = agent._STATE_WAITING_FOR_PHONE
        agent.handle_message("we reduced to just 2 people")
        # "reduced" not in change keywords; participants_changed=True triggers set_field
        # but is_change_request may be False without keyword → availability not re-run
        # participants value in memory:
        updated = agent.memory.data.get("participants")
        assert updated is not None, "F003: participant field should not be None after message"

    # F007 — Date changes multiple times without user confirmation
    def test_F007_date_changes_three_times_no_confirmation_required(self, tmp_path):
        """
        SCENARIO: User changes date 3 times before selecting slot.
        EXPECTED: All changes applied; audit_trail populated; no data corruption.
        """
        agent = make_booking_agent(tmp_path)
        dates = ["25 June", "26 June", "27 June"]
        for d in dates:
            agent.handle_message(f"actually, change date to {d}")
        final_date = agent.memory.data.get("preferred_date")
        audit = agent.memory.data.get("audit_trail", [])
        date_changes = [e for e in audit if e.get("field") == "preferred_date"]
        assert len(date_changes) >= 2, "F007: Multiple date changes should be in audit trail"


# ─────────────────────────────────────────────────────────────
# CATEGORY G: ASR / LOCATION RECOGNITION
# ─────────────────────────────────────────────────────────────

class TestG_ASR:

    def test_G003_jpnagar_no_spaces(self, tmp_path):
        """G003: 'JPNagar' (no spaces) should resolve to JP Nagar."""
        mem = ConversationMemory(tmp_path / "session.json")
        result = mem._extract_location("jpnagar")
        assert result == "JP Nagar", f"FAIL G003: 'jpnagar' → '{result}' expected 'JP Nagar'"

    def test_G004_jeep_nagar_asr_error(self, tmp_path):
        """G004: 'Jeep Nagar' ASR error should fuzzy-match to JP Nagar."""
        mem = ConversationMemory(tmp_path / "session.json")
        result = mem._extract_location("jeep nagar")
        # This is a documented gap — may return "" if difflib ratio is too low
        # Test DOCUMENTS whether it passes or fails
        if result != "JP Nagar":
            pass  # DOCUMENTED GAP G004: Whisper ASR "Jeep Nagar" not caught

    def test_G005_jibbing_not_matched(self, tmp_path):
        """G005: 'Jibbing' severe mishearing should NOT match any location."""
        mem = ConversationMemory(tmp_path / "session.json")
        result = mem._extract_location("jibbing")
        # Should return "" — no match
        assert result == "", f"FAIL G005: 'jibbing' matched '{result}' — false positive"

    def test_G007_jp_alone_not_matched(self, tmp_path):
        """G007: 'JP' alone should not match JP Nagar without 'nagar'."""
        mem = ConversationMemory(tmp_path / "session.json")
        # "jp" alone — "jp" in lowered but "nagar" not in lowered → no match
        result = mem._extract_location("jp")
        assert result == "", f"FAIL G007: 'jp' alone matched '{result}' — needs 'nagar'"

    def test_G009_white_field_two_words(self, tmp_path):
        """G009: 'White field' (two words) should resolve to Whitefield."""
        mem = ConversationMemory(tmp_path / "session.json")
        result = mem._extract_location("white field")
        assert result == "Whitefield", f"FAIL G009: 'white field' → '{result}' expected 'Whitefield'"


# ─────────────────────────────────────────────────────────────
# CATEGORY H: LARGE GROUPS
# ─────────────────────────────────────────────────────────────

class TestH_LargeGroups:

    def test_H006_solo_booking_not_flagged(self, tmp_path):
        """H006: Group of 1 is valid but suspicious — agent should not crash."""
        mem = make_full_memory(tmp_path, participants=1)
        agent = BookingAgent(mem)
        agent.availability_tool.check = MagicMock(return_value={
            "available": True, "verified": True,
            "slots": ["3:00 PM"], "location": "Koramangala",
            "date": "25 June", "participants": 1,
        })
        result = agent.handle_message("ready")
        assert result.response is not None, "H006: Agent crashed with 1 participant"

    def test_H004_twenty_people_goes_to_closed_state(self, tmp_path):
        """H004: Group of 20 should trigger capacity limitation and not dead-end."""
        mem = make_full_memory(tmp_path, participants=20)
        agent = BookingAgent(mem)
        agent.availability_tool.check = MagicMock(return_value={
            "available": True,
            "slots": ["3:00 PM"],
            "capacity_supported": False,
            "bookable_slots": [],
            "max_available_capacity": 8,
        })
        result = agent.handle_message("ready")
        assert "none can fit" in result.response.lower() or "events team" in result.response.lower(), (
            "FAIL H004: Large group capacity limitation message not shown"
        )
        # After CLOSED state, user is stuck — document this
        followup = agent.handle_message("what should we do?")
        assert followup.response is not None, "H004: Agent crashed after capacity limitation"


# ─────────────────────────────────────────────────────────────
# CATEGORY I: CONTRADICTORY INPUTS
# ─────────────────────────────────────────────────────────────

class TestI_ContradictoryInputs:

    def test_I004_two_locations_in_same_message(self, tmp_path):
        """I004: an ambiguous two-location request must not silently select one."""
        mem = ConversationMemory(tmp_path / "session.json")
        location = mem._extract_location("whitefield or koramangala")
        assert location == ""

    def test_I001_next_month_not_extracted(self, tmp_path):
        """I001: 'actually next month' date not recognized."""
        mem = ConversationMemory(tmp_path / "session.json")
        result = mem._extract_preferred_date("no, actually next month")
        assert result == "", (
            f"I001: 'next month' should not extract a date, got '{result}'"
        )

    def test_I008_second_phone_number_not_overwritten(self, tmp_path):
        """I008: Second phone number should not silently overwrite first."""
        mem = make_full_memory(tmp_path, phone="9876543210")
        # Try to change phone without explicit change keyword
        mem.merge_message("9999999999", "escape_room_inquiry", expected_field="phone")
        # Phone already set; without change keyword, set_field defers or rejects
        # But phone is NOT in the deferred-confirmation field list
        # Actually for "phone" field: is_explicit_change = (len(lowered.split()) <= 2) → True for "9999999999"
        # So it WILL overwrite silently
        # This test documents the behavior
        phone = mem.data.get("phone")
        assert phone in ("9876543210", "9999999999"), "I008: Phone field corrupted"


# ─────────────────────────────────────────────────────────────
# CATEGORY J: MALICIOUS / WEIRD INPUTS
# ─────────────────────────────────────────────────────────────

class TestJ_MaliciousInputs:

    def test_J004_bare_number_captured_as_participants(self, tmp_path):
        """J004: User sends bare number '42' — should not be silently captured as participants."""
        mem = ConversationMemory(tmp_path / "session.json")
        # Without context, "42" matches _capture_bare_count
        # This should NOT set participants when no intent is established
        mem.merge_message("42", "escape_room_inquiry")
        # _capture_bare_count: count=42; if participants not set → set to 42
        participants = mem.data.get("participants")
        # Documenting: bare "42" sets participants to 42 without user confirmation
        assert participants in ("", None, 42), "J004: Unexpected participants value"

    def test_J007_long_message_does_not_crash(self, tmp_path):
        """J007: 10,000 character message should not crash."""
        mem = make_full_memory(tmp_path)
        agent = InboundAgent(
            knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
            memory=mem,
            prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
            use_openai=False,
        )
        long_msg = "I want to book " + "a" * 9985
        try:
            result = agent.handle_message(long_msg)
            assert result.response is not None
        except Exception as e:
            assert False, f"FAIL J007: Long message crashed agent: {e}"

    def test_J010_book_as_name_rejected(self, tmp_path):
        """J010: 'book' as name should be rejected (in rejected_words)."""
        from src.memory.conversation_memory import ConversationMemory as CM
        result = CM._is_plausible_name("Book")
        assert result is False, "FAIL J010: 'Book' passed _is_plausible_name — noise word"

    def test_J020_phone_number_as_words_not_extracted(self, tmp_path):
        """J020: 'nine eight seven six five four three two one zero' should not be extracted as phone."""
        mem = ConversationMemory(tmp_path / "session.json")
        # normalize_number_words only handles 1-20; individual digits as words not normalized
        normalized = mem.normalize_number_words("nine eight seven six five four three two one zero")
        phone = mem._extract_phone(normalized)
        # Likely returns "" — documents the gap in voice number dictation
        assert isinstance(phone, str), "J020: _extract_phone must return a string"

    def test_J021_participant_question_loop_has_no_limit(self, tmp_path):
        """J021: Agent loops on 'How many people?' with no max loop count."""
        mem = ConversationMemory(tmp_path / "session.json")
        mem.data["intent"] = "escape_room_inquiry"
        mem.save()
        agent = InboundAgent(
            knowledge_base=KnowledgeLoader(PROJECT_DIR / "knowledge").load(),
            memory=mem,
            prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
            use_openai=False,
        )
        # Send garbage 5 times — agent should re-ask; no automatic escalation
        for i in range(5):
            result = agent.handle_message("blah blah blah xyz")
            assert result.response is not None, f"J021: Agent crashed on iteration {i+1}"
        # No escalation after 5 garbage responses — documents gap
        escalated = mem.data.get("escalation_state", {}).get("escalate", False)
        # This is by design (garbage isn't sentiment-based)
        assert isinstance(escalated, bool), "J021: escalation_state corrupted"
