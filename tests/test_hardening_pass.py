from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.agents.inbound_agent import InboundAgent
from src.agents.booking_agent import BookingAgent
from src.memory.conversation_memory import ConversationMemory
from src.knowledge.knowledge_loader import KnowledgeLoader
from src.orchestration.booking_orchestrator import BookingOrchestrator


def make_agent(tmp_path: Path) -> InboundAgent:
    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = ConversationMemory(tmp_path / "session.json")
    return InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


def test_scenario_a_first_time_friends(tmp_path: Path) -> None:
    """A. First-time friends (beginner recommendation, location, details, confirmation)"""
    agent = make_agent(tmp_path)
    
    # 1. Start conversation with group size and beginner indication
    r1 = agent.handle_message("We are a group of 7 friends and we have never done an escape room.")
    
    assert agent.memory.data["participants"] == 7
    assert agent.memory.data["experience_level"] == "beginner"
    # New flow: age_group question comes first (no recommendation without age_group)
    assert "age group" in r1["response"].lower() or "adults, kids" in r1["response"].lower() or "adult" in r1["response"].lower()
    
    # 2. Provide age group — location comes before recommendation.
    r2 = agent.handle_message("mostly adults")
    assert agent.memory.data["age_group"] == "adults"
    assert r2["response"] == "Which location would you like to visit?"
    
    # 3. Provide location
    r3 = agent.handle_message("Koramangala")
    assert agent.memory.data["location"] == "Koramangala"
    assert "room in mind" in r3["response"] or "recommendation" in r3["response"]
    
    # 4. Choose a concrete room and provide date
    agent.handle_message("Murder Mystery")
    r4 = agent.handle_message("18 June")
    assert agent.memory.data["preferred_date"] == "18 June"
    assert "check availability" in r4["response"].lower()
    # Contact is collected by BookingAgent after a verified slot.
    assert agent.memory.handoff_ready("escape_room_inquiry")
    assert not agent.memory.data["customer_name"]
    assert not agent.memory.data["phone"]


def test_scenario_b_couple_booking(tmp_path: Path) -> None:
    """B. Couple booking (couple detection, room comparison, confirmation)"""
    agent = make_agent(tmp_path)
    
    # 1. Relationship context is a standard two-player escape-room booking.
    r1 = agent.handle_message("I want to book for a couple on 18 June.")
    assert agent.memory.data["intent"] == "escape_room_inquiry"
    assert agent.memory.data["participants"] == 2
    assert agent.memory.data["relationship"] == "couple"
    assert agent.memory.data["preferred_date"] == "18 June"
    assert "murder mystery" in r1["response"].lower()
    
    # 2. Provide location
    r2 = agent.handle_message("Koramangala")
    assert agent.memory.data["location"] == "Koramangala"
    
    # 3. Contact can be captured without changing the selected booking facts.
    r3 = agent.handle_message("Fantastic, this is Siddharth")
    assert agent.memory.data["customer_name"] == "Siddharth"
    
    r4 = agent.handle_message("My phone number is 9876543210")
    assert agent.memory.data["phone"] == "9876543210"
    
    assert agent.memory.data["phone"] == "9876543210"


def test_scenario_c_family_with_children(tmp_path: Path) -> None:
    """C. Family with children (kids age group, Whitefield recommendation, confirmation)"""
    agent = make_agent(tmp_path)
    
    # 1. Indicate family/kids and date
    r1 = agent.handle_message("We want to book for 6 kids on 18 June.")
    assert agent.memory.data["participants"] == 6
    assert agent.memory.data["age_group"] == "kids"
    assert "location" in r1["response"].lower()
    
    # 2. Ask which location is better for kids
    r2 = agent.handle_message("Which location is better for kids?")
    assert "Whitefield" in r2["response"]
    
    # 3. Choose Whitefield
    r3 = agent.handle_message("Let's do Whitefield.")
    assert agent.memory.data["location"] == "Whitefield"
    assert "room in mind" in r3["response"] or "recommendation" in r3["response"]
    
    # 4. Choose a room; availability precedes contact collection.
    agent.handle_message("Murder Mystery")
    r4 = agent.handle_message("18 June")
    assert "check availability" in r4["response"].lower()
    assert not agent.memory.data["customer_name"]
    assert not agent.memory.data["phone"]


def test_scenario_d_corporate_event(tmp_path: Path) -> None:
    """D. Corporate event (team size, food, budget standard/premium, confirmation)"""
    agent = make_agent(tmp_path)
    
    # 1. Indicate corporate event
    r1 = agent.handle_message("We need to book a corporate event.")
    assert agent.memory.data["intent"] == "corporate_event"
    assert "employees" in r1["response"].lower() or "how many" in r1["response"].lower()
    
    # 2. Provide team size
    r2 = agent.handle_message("We are 45 people.")
    assert agent.memory.data["participants"] == 45
    
    # 3. Provide location
    r3 = agent.handle_message("JP Nagar")
    assert agent.memory.data["location"] == "JP Nagar"
    
    # 4. Provide date
    r4 = agent.handle_message("18 June")
    assert agent.memory.data["preferred_date"] == "18 June"
    
    # 5. Food request
    r5 = agent.handle_message("Yes, we need food.")
    assert agent.memory.data["food_required"] is True
    
    # 6. Budget package
    r6 = agent.handle_message("Standard package.")
    assert agent.memory.data["budget_range"] == "standard"
    
    # 7. Contact details
    r7 = agent.handle_message("My name is Siddharth")
    r8 = agent.handle_message("Phone is 9876543210")
    assert "check availability" in r8["response"].lower()


def test_scenario_e_running_late_interruption(tmp_path: Path) -> None:
    """E. Running late interruption (faq query mid-flow, rescue response, resume)"""
    agent = make_agent(tmp_path)
    
    agent.handle_message("We want an escape room.")
    
    # Interrupted with FAQ: We are running late
    r2 = agent.handle_message("We are running late for our game.")
    assert "arriving late" in r2["response"].lower() or "scheduled time" in r2["response"].lower()
    # Resume qualification question
    assert "players" in r2["response"].lower() or "joining" in r2["response"].lower()


def test_scenario_f_explain_all_rooms_interruption(tmp_path: Path) -> None:
    """F. Explain all rooms interruption (explain mid-flow, resume)"""
    agent = make_agent(tmp_path)
    
    agent.handle_message("We want an escape room for 7 friends in Koramangala.")
    
    # Interrupted with rooms explanation request
    r2 = agent.handle_message("What rooms do you have?")
    assert "Murder Mystery" in r2["response"] or "Hostage" in r2["response"]
    # Answer the FAQ first; preserve the missing age field for the next turn.
    assert "age group" not in r2["response"].lower()
    assert agent.memory.data["age_group"] == ""


def test_scenario_g_room_modification(tmp_path: Path) -> None:
    """G. Room modification (room change request during booking, availability update)"""
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update({
        "intent": "escape_room_inquiry",
        "location": "Koramangala",
        "preferred_date": "18 June",
        "participants": 6,
        "age_group": "adults",
        "customer_name": "Siddharth",
        "phone": "9876543210",
        "room": "Murder Mystery",
    })
    memory.save()
    
    booking_agent = BookingAgent(memory)
    res = booking_agent.handle_message("Actually, we want Classified instead.")
    
    assert booking_agent.memory.data["room"] == "Classified"
    assert "Classified" in res.response
    assert "switched the room to Classified" in res.response
    assert "keep 18 June" in res.response


def test_scenario_h_booking_cancellation(tmp_path: Path) -> None:
    """H. Booking cancellation (cancel request on existing booking)"""
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update({
        "intent": "escape_room_inquiry",
        "location": "Koramangala",
        "preferred_date": "18 June",
        "participants": 6,
        "booking_ref": "BRK-MOCK1234",
    })
    memory.save()
    
    booking_agent = BookingAgent(memory)
    booking_agent.orchestrator.simulator.bookings["BRK-MOCK1234"] = {
        "booking_id": "BRK-MOCK1234",
        "location": "Koramangala",
        "date": "18 June",
        "slot": "12:00 PM",
        "participants": 6,
        "status": "confirmed",
    }
    res = booking_agent.handle_message("Please cancel my booking.")
    
    assert "cancelled" in res.response.lower()
    # Stateful check in simulator
    booking = booking_agent.orchestrator.find_booking("BRK-MOCK1234")
    assert booking.get("status") == "cancelled"


def test_scenario_i_booking_rescheduling(tmp_path: Path) -> None:
    """I. Booking rescheduling (date/slot change on existing booking reference)"""
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update({
        "intent": "escape_room_inquiry",
        "booking_ref": "BRK-MOCK5678",
    })
    memory.save()
    
    orchestrator = BookingOrchestrator()
    # Pre-seed the booking statefully in the simulator
    orchestrator.simulator.bookings["BRK-MOCK5678"] = {
        "booking_id": "BRK-MOCK5678",
        "location": "Koramangala",
        "date": "18 June",
        "slot": "12:00 PM",
        "participants": 6,
        "status": "confirmed",
    }
    
    res = orchestrator.reschedule_booking("BRK-MOCK5678", "3:00 PM")
    assert res["status"] == "rescheduled"
    
    booking = orchestrator.find_booking("BRK-MOCK5678")
    assert booking["slot"] == "3:00 PM"


def test_scenario_j_name_and_phone_collection(tmp_path: Path) -> None:
    """J. Name and phone collection (validation on names and phone numbers)"""
    agent = make_agent(tmp_path)
    
    # 1. Feed details
    agent.handle_message("We want to book a corporate event for 20 players on 18 June at JP Nagar.")
    
    # 2. Feed food and budget
    agent.handle_message("No food.")
    agent.handle_message("Basic package.")
    
    # 3. Collect name - invalid name rejection check
    r1 = agent.handle_message("Siddharth")
    assert agent.memory.data["customer_name"] == "Siddharth"
    
    # 4. Collect phone number
    r2 = agent.handle_message("Phone is 9876543210")
    assert agent.memory.data["phone"] == "9876543210"
    assert "captured" in r2["response"].lower() or "reach out" in r2["response"].lower() or "perfect" in r2["response"].lower()


def test_regression_modifications_and_audit_trail(tmp_path: Path) -> None:
    """Regression test for modifications, low-confidence overrides, and audit trails"""
    agent = make_agent(tmp_path)
    
    # 1. Add new information
    agent.handle_message("We want an escape room at JP Nagar.")
    agent.handle_message("We are 6 people.")
    assert agent.memory.data["participants"] == 6
    assert agent.memory.data["location"] == "JP Nagar"
    
    # 2. High-confidence modification: "We are actually 10 now."
    agent.handle_message("We are actually 10 now.")
    assert agent.memory.data["participants"] == 10
    
    # Verify modification logged in audit trail
    audit = agent.memory.data["audit_trail"]
    assert len(audit) >= 2
    assert audit[-1]["field"] == "participants"
    assert audit[-1]["new_value"] == 10
    assert audit[-1]["type"] == "modification"
    
    # 3. Low-confidence contradict correction (not expected, no indicator)
    # The qualification flow is waiting for age_group. If they say "Koramangala" out of blue:
    res = agent.handle_message("I think we will go to Koramangala")
    # Should ask for confirmation instead of updating location directly
    assert "pending_confirmation" in agent.memory.data
    assert agent.memory.data["pending_confirmation"]["field"] == "location"
    assert agent.memory.data["pending_confirmation"]["new_value"] == "Koramangala"
    assert "I noticed you mentioned Koramangala" in res["response"]
    
    # 4. Customer says "yes, that's right" to confirm
    res_confirm = agent.handle_message("Yes, that's right")
    assert agent.memory.data["location"] == "Koramangala"
    assert agent.memory.data["pending_confirmation"] is None
    assert "updated that to Koramangala" in res_confirm["response"]


def test_voice_faq_interruption_rules_rooms_bug_text_and_voice(tmp_path: Path, monkeypatch) -> None:
    """Regression test for Voice FAQ Interruption Bug: rules and rooms queries must trigger FAQ handling and resume pending qualification in both text and voice modes."""
    import main as main_module

    # Test phrases from user request
    phrases = ["can explain rooms", "explain rooms", "tell me about rooms", "explain rules", "how does it work"]

    for phrase in phrases:
        # --- TEST TEXT MODE (DEMO_MODE=false) ---
        monkeypatch.setenv("DEMO_MODE", "false")
        agent = make_agent(tmp_path / f"text_{phrase.replace(' ', '_')}")
        
        # 1. Start flow: set participants=7
        agent.handle_message("We want to book an escape room for 7 friends.")
        assert agent.memory.data["participants"] == 7
        assert agent.qualification_agent._waiting_for == "age_group"
        
        # 2. Provide age group: all adults
        agent.handle_message("All adults.")
        assert agent.memory.data["age_group"] == "adults"
        assert agent.qualification_agent._waiting_for == "location"
        
        # 3. Interruption with rules/rooms query
        res = agent.handle_message(phrase)
        
        # Must answer FAQ (contain rules or rooms info)
        assert ("experience" in res["response"] or "rooms" in res["response"] or "Murder Mystery" in res["response"] or "Hostage" in res["response"] or "locked" in res["response"] or "50 minutes" in res["response"])
        
        # Must preserve memory & qualification state
        assert agent.memory.data["participants"] == 7
        assert agent.memory.data["age_group"] == "adults"
        assert agent.qualification_agent._waiting_for == "location"
        
        # Must resume the pending qualification question (should ask for location)
        assert ("location" in res["response"].lower() or "koramangala" in res["response"].lower() or "whitefield" in res["response"].lower() or "jp nagar" in res["response"].lower())

        # --- TEST VOICE MODE / DEMO MODE (DEMO_MODE=true) ---
        monkeypatch.setenv("DEMO_MODE", "true")
        agent_voice = make_agent(tmp_path / f"voice_{phrase.replace(' ', '_')}")
        
        # 1. Start flow: set participants=7
        booking_agent = None
        active = "inbound_agent"
        r1, booking_agent, active = main_module.dispatch("We want to book an escape room for 7 friends.", agent_voice, booking_agent, active)
        assert agent_voice.memory.data["participants"] == 7
        assert agent_voice.qualification_agent._waiting_for == "age_group"
        
        # 2. Provide age group: all adults
        r2, booking_agent, active = main_module.dispatch("All adults.", agent_voice, booking_agent, active)
        assert agent_voice.memory.data["age_group"] == "adults"
        assert agent_voice.qualification_agent._waiting_for == "location"
        
        # 3. Voice-mode interruption with rules/rooms query
        r3, booking_agent, active = main_module.dispatch(phrase, agent_voice, booking_agent, active)
        
        # Must answer FAQ completely
        assert ("experience" in r3.response or "rooms" in r3.response or "Murder Mystery" in r3.response or "Hostage" in r3.response or "locked" in r3.response or "50 minutes" in r3.response)
        
        # Must preserve memory & qualification state
        assert agent_voice.memory.data["participants"] == 7
        assert agent_voice.memory.data["age_group"] == "adults"
        assert agent_voice.qualification_agent._waiting_for == "location"
        
        # Must resume the pending qualification question (should ask for location)
        assert ("location" in r3.response.lower() or "koramangala" in r3.response.lower() or "whitefield" in r3.response.lower() or "jp nagar" in r3.response.lower())


def test_final_live_test_bugs_regression(tmp_path: Path, monkeypatch) -> None:
    """Regression tests for FINAL LIVE TEST BUGS 1, 2, 3, and 4."""
    import main as main_module
    from src.core.handoff_generator import HandoffGenerator
    from src.agents.booking_agent import BookingAgent

    # ==========================================
    # BUG 1 & 2: Qualification State Regression & Customer Name False Extraction
    # ==========================================
    agent = make_agent(tmp_path / "bug1_2")
    booking_agent = None
    active = "inbound_agent"
    
    # 1. Couple context starts a standard escape-room booking.
    r1, booking_agent, active = main_module.dispatch(
        "We are a couple and we've never done an escape room before.", agent, booking_agent, active
    )
    assert agent.memory.data["intent"] == "escape_room_inquiry"
    assert agent.memory.data["relationship"] == "couple"
    assert "murder mystery" in r1.response.lower()
    
    # 2. Provide location: jp nagar
    r2, booking_agent, active = main_module.dispatch("jp nagar", agent, booking_agent, active)
    assert agent.memory.data["location"] == "JP Nagar"
    # Qualification should advance to next question, not repeat recommendation question
    assert "nice. i'd probably go with murder mystery" not in r2.response.lower()
    
    # 3. Test Bug 2 Name Extraction Prevention
    noise_words = ["challenging", "relaxed", "adults", "kids", "hostage"]
    for word in noise_words:
        prev_name = agent.memory.data.get("customer_name", "")
        agent.qualification_agent._waiting_for = "customer_name"
        agent.handle_message(word)
        assert agent.memory.data.get("customer_name", "") != word.title(), f"Word '{word}' was incorrectly extracted as customer_name!"
        agent.memory.data["customer_name"] = prev_name
        agent.memory.save()

    # ==========================================
    # BUG 3: Phone Persistence in Handoff Payload
    # ==========================================
    generator = HandoffGenerator()
    agent_phone = make_agent(tmp_path / "bug3")
    agent_phone.memory.data.update({
        "intent": "escape_room_inquiry",
        "location": "Whitefield",
        "participants": 6,
        "age_group": "adults",
        "customer_name": "Siddharth",
        "phone": "9876543210",
        "preferred_date": "18 June",
    })
    agent_phone.memory.save()
    
    payload = generator.generate(agent_phone.memory.data)
    assert "phone" in payload
    assert payload["phone"] == "9876543210"
    assert "preferred_date" in payload
    assert payload["preferred_date"] == "18 June"

    # ==========================================
    # BUG 4: Terminal Conversation State
    # ==========================================
    agent_terminal = make_agent(tmp_path / "bug4")
    agent_terminal.memory.data.update({
        "intent": "escape_room_inquiry",
        "location": "JP Nagar",
        "participants": 2,
        "age_group": "adults",
        "customer_name": "Siddharth",
        "phone": "9876543210",
        "preferred_date": "18 June",
        "room": "Prison Break",
    })
    agent_terminal.memory.save()
    
    booking_agent = BookingAgent(agent_terminal.memory)
    booking_agent._state = booking_agent._STATE_BOOKING_CONFIRMED
    booking_agent._booking_result = {
        "booking_id": "BRK-MOCK999",
        "confirmed": True,
        "location": "JP Nagar",
        "date": "18 June",
        "slot": "7:00 PM",
        "participants": 2,
    }
    
    res1 = booking_agent.handle_message("no")
    assert "thanks for choosing" in res1.response.lower() or "have a wonderful day" in res1.response.lower() or "great day" in res1.response.lower() or "great" in res1.response.lower()
    assert agent_terminal.memory.data.get("completed_booking") is True
    
    subsequent_inputs = ["bye", "thanks", "ok", "okay", "no"]
    for msg in subsequent_inputs:
        res_sub = booking_agent.handle_message(msg)
        assert "great" in res_sub.response.lower() or "thanks for choosing" in res_sub.response.lower() or "have a wonderful day" in res_sub.response.lower()
        assert "anything you'd like to know" not in res_sub.response.lower()
        assert "food options" not in res_sub.response.lower()
