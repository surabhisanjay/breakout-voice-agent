from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import main as main_module  # noqa: E402
from main import is_exit_command, run_text_loop, run_voice_loop, should_process_transcript, speak_then_resume_listening  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402
from src.agents.inbound_agent import InboundAgent  # noqa: E402
from src.knowledge.knowledge_loader import KnowledgeLoader  # noqa: E402
from src.agents.qualification_agent import QualificationAgent  # noqa: E402


def make_agent(tmp_path: Path) -> InboundAgent:
    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = ConversationMemory(tmp_path / "session.json")
    return InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_openai=False,
    )


def test_greeting(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("Hi, is this Breakout?")
    assert result["response"] == "Yes, this is Breakout Escape Rooms. How may I help you today?"


def test_locations(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("What locations do you have?")
    assert "Koramangala" in result["response"]
    assert "Whitefield" in result["response"]
    assert "JP Nagar" in result["response"]


def test_location_name_followup_returns_details(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("Koramangala")
    assert "Koramangala" in result["response"]
    assert "Murder Mystery" in result["response"]
    assert "Hostage" in result["response"]


def test_parking_available(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("Is parking available?")
    assert "Koramangala" in result["response"]
    assert "Whitefield" in result["response"]
    assert "JP Nagar" in result["response"]


def test_game_availability_by_location(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("What games are available in Whitefield?")
    assert "Murder Mystery" in result["response"]
    assert "Hostage" in result["response"]
    assert "Bomb Defusal" in result["response"]
    assert "Undercover" in result["response"]


def test_room_name_followup_with_location_returns_details(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("What locations do you have?")
    result = agent.handle_message("Murder Mystery")
    assert "Murder Mystery" in result["response"]
    assert "Koramangala" in result["response"] or "Whitefield" in result["response"] or "JP Nagar" in result["response"]


def test_room_name_typo_fallback(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("Murder mysteru")
    assert "Murder Mystery" in result["response"]
    assert "investigation-style" in result["response"]


def test_generic_booking_asks_event_type(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("I want to book")
    assert "type of event" in result["response"].lower()
    assert "escape room" in result["response"].lower()
    assert agent.memory.data["intent"] == "general_faq"


def test_direct_escape_room_booking_asks_participants_first(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("I want to book an escape room")
    assert "how many" in result["response"].lower() or "people will attend" in result["response"].lower()
    assert agent.memory.data["intent"] == "escape_room_inquiry"


def test_escape_room_inquiry_bare_number_captures_participants(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("I want an escape room")
    result = agent.handle_message("10")

    assert agent.memory.data["participants"] == 10
    assert result["intent"] == "escape_room_inquiry"
    assert "escape room before" in result["response"].lower() or "first one" in result["response"].lower()


def test_escape_room_inquiry_approx_number_captures_participants(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("I want an escape room")
    result = agent.handle_message("around 10")

    assert agent.memory.data["participants"] == 10
    assert result["intent"] == "escape_room_inquiry"
    assert "escape room before" in result["response"].lower() or "first one" in result["response"].lower()


def test_active_escape_room_flow_preserves_intent_on_noisy_whisper_transcript(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("I want an escape room")
    result = agent.handle_message("people will be joining I want to book")

    assert result["intent"] == "escape_room_inquiry"
    assert agent.memory.data["intent"] == "escape_room_inquiry"
    assert agent.memory.data["event_type"] == "Escape Room"


def test_location_answer_updates_memory_during_active_escape_room_flow(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("I want an escape room")
    agent.handle_message("10")
    result = agent.handle_message("Whitefield")

    assert agent.memory.data["location"] == "Whitefield"
    assert result["intent"] == "escape_room_inquiry"
    assert "escape room before" in result["response"].lower() or "first one" in result["response"].lower()


def test_age_answer_updates_memory_during_active_escape_room_flow(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("I want an escape room")
    agent.handle_message("10")
    agent.handle_message("Whitefield")
    result = agent.handle_message("20-25")

    assert agent.memory.data["age_group"] == "adults"
    assert agent.memory.data["age_detail"] == "20-25"
    assert result["intent"] == "escape_room_inquiry"


def test_kids_recommendation(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("We have 6 kids aged 10.")
    assert result["response"] == "Which location would you like to visit?"


def test_free_form_kids_recommendation_with_location(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("We are 6 kids aged 10 coming to Whitefield.")
    assert "room in mind" in result["response"] or "recommendation" in result["response"]
    assert result["missing_fields"] == []


def test_age_range_recognized_as_age_group(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "participants": 5,
            "location": "Koramangala",
        }
    )
    agent.memory.save()

    result = agent.handle_message("10-15 years")

    assert agent.memory.data["age_group"] == "teens"
    assert "room in mind" in result["response"] or "recommendation" in result["response"]
    assert result["missing_fields"] == []


def test_explicit_booking_clear_stale_memory(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.memory.data.update(
        {
            "location": "Koramangala",
            "participants": 5,
            "age_group": "kids",
            "intent": "escape_room_inquiry",
        }
    )
    agent.memory.save()

    result = agent.handle_message("I want to book")

    assert "type of event" in result["response"].lower()
    assert agent.memory.data["location"] == ""
    assert agent.memory.data["participants"] == ""
    assert agent.memory.data["age_group"] == ""


def test_adult_game_recommendation(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("We are six adults.")
    assert result["response"] == "Which location would you like to visit?"


def test_birthday_flow(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    first = agent.handle_message("I want a birthday party.")["response"]
    assert "Absolutely" in first
    assert "Karaoke" in first
    assert "Approximately how many guests" in first
    assert "Which location" in agent.handle_message("35 people.")["response"]
    response = agent.handle_message("Whitefield.")["response"]
    assert "Whitefield can accommodate approximately 35-40 guests" in response
    assert "date" in response.lower()


def test_free_form_birthday_with_guest_count_and_location(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    response = agent.handle_message("I want a birthday party for 35 guests in Whitefield.")["response"]
    assert "Whitefield can accommodate approximately 35-40 guests" in response
    assert "date" in response.lower()


def test_corporate_flow(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    first = agent.handle_message("We need a corporate event for fifteen employees.")["response"]
    assert "Escape Rooms and Scavenger Hunt" in first
    assert "teamwork" in first
    assert "Which location" in first
    assert "date" in agent.handle_message("Koramangala.")["response"].lower()


def test_corporate_intent_persists_after_follow_up(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    first = agent.handle_message("We need an event for fifteen employees.")
    assert first["intent"] == "corporate_event"
    assert first["route"]["next_agent"] == "qualification_agent"
    second = agent.handle_message("Whitefield.")
    assert second["intent"] == "corporate_event"
    assert "date" in second["response"].lower()


def test_adult_recommendation_refines_with_location(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    first = agent.handle_message("We are six adults.")
    assert first["response"] == "Which location would you like to visit?"
    second = agent.handle_message("We are visiting Whitefield.")
    assert second["intent"] == "escape_room_inquiry"
    assert "room in mind" in second["response"] or "recommendation" in second["response"]


def test_six_people_visiting_whitefield_remembers_count(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("We are six people visiting Whitefield.")
    assert agent.memory.data["participants"] == 6
    assert agent.memory.data["location"] == "Whitefield"
    assert "How many players" not in result["response"]


def test_recommendation_uses_prior_context_and_experience_level(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We are six adults visiting Whitefield.")
    beginner = agent.handle_message("We've never done an escape room before.")
    assert "room in mind" in beginner["response"] or "recommendation" in beginner["response"]
    follow_up = agent.handle_message("What would you recommend?")
    assert follow_up["intent"] == "escape_room_inquiry"
    assert "Murder Mystery" in follow_up["response"]
    assert "Hostage" in follow_up["response"]
    assert "first visit" in follow_up["response"] or "escape-room feel" in follow_up["response"]


def test_challenge_preference_advances_instead_of_repeating(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We are six people visiting Whitefield.")
    age_turn = agent.handle_message("10-15")
    first = agent.handle_message("challenging")
    second = agent.handle_message("challenging")

    assert agent.memory.data["age_group"] == "teens"
    assert "room in mind" in age_turn["response"] or "recommendation" in age_turn["response"]
    assert agent.memory.data["challenge_preference"] == "challenging"
    assert "room in mind" in first["response"] or "recommendation" in first["response"]
    assert second["response"] == first["response"]


def test_debug_state_flow_contains_previous_extracted_and_updated_state(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("We are six adults visiting Whitefield.")

    assert result["debug"]["previous_state"]["participants"] == ""
    assert result["debug"]["extracted_entities"]["participants"] == 6
    assert result["debug"]["extracted_entities"]["age_group"] == "adults"
    assert result["debug"]["updated_state"]["participants"] == 6
    assert result["debug"]["missing_slots"] == []


def test_first_time_players_use_existing_context(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We are six adults visiting Whitefield.")
    response = agent.handle_message("We've never done an escape room before.")["response"]
    assert "room in mind" in response or "recommendation" in response
    assert "How many people" not in response
    assert "Which location" not in response


def test_all_our_kids_does_not_corrupt_count(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We are six people visiting Whitefield.")
    result = agent.handle_message("All our kids are aged 10.")
    assert agent.memory.data["participants"] == 6
    assert agent.memory.data["age_group"] == "teens"
    assert agent.memory.data["age_detail"] == "10 years"
    assert "room in mind" in result["response"] or "recommendation" in result["response"]


def test_compare_these_two_does_not_become_two_participants(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We are six adults visiting Whitefield.")
    agent.handle_message("We've never done an escape room before.")
    result = agent.handle_message("Can you compare these two?")
    assert agent.memory.data["participants"] == 6
    assert "Murder Mystery" in result["response"]
    assert "Hostage" in result["response"]


def test_all_are_18_plus_sets_adults_without_changing_count(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We are six people visiting Whitefield.")
    result = agent.handle_message("All are 18 plus.")
    assert agent.memory.data["participants"] == 6
    assert agent.memory.data["age_group"] == "adults"
    assert "room in mind" in result["response"] or "recommendation" in result["response"]


def test_openai_response_is_used(monkeypatch, tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.use_openai = True
    agent.model = "gpt-5-mini"
    monkeypatch.setattr(agent.retriever, "search", lambda _query: "Known Breakout context")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    class FakeResponses:
        def create(self, **kwargs):
            assert kwargs["model"] == "gpt-5-mini"
            assert "Known Breakout context" in kwargs["input"]
            return SimpleNamespace(output_text="OpenAI response")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "test-key"
            self.responses = FakeResponses()

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))

    result = agent.handle_message("Tell me something unusual that is not in the common FAQs.")
    assert result["response"] == "OpenAI response"
    assert agent.last_openai_error == ""


def test_qualification_flow_completion(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    qualifier = QualificationAgent(memory)

    memory.update_from_message("We want a corporate event.", "corporate_event")
    first = qualifier.qualify("corporate_event")
    assert not first.qualified
    assert first.next_question == "How many people are joining?"

    qualifier.update_and_qualify("45", "corporate_event")
    second = qualifier.update_and_qualify("Whitefield", "corporate_event")
    assert second.next_question == "Got it. What date are you planning for?"

    qualifier.update_and_qualify("15 June", "corporate_event")
    qualifier.update_and_qualify("Yes, include food", "corporate_event")
    qualifier.update_and_qualify("Budget around 50000", "corporate_event")
    qualifier.update_and_qualify("Siddharth", "corporate_event")
    final = qualifier.update_and_qualify("9876543210", "corporate_event")

    assert final.qualified
    assert final.summary["event_type"] == "corporate"
    assert final.summary["participants"] == 45
    assert final.summary["location"] == "Whitefield"
    assert final.summary["preferred_date"] == "15 June"
    assert final.summary["food_required"] is True
    assert final.summary["budget_range"] == "50000"
    assert final.summary["customer_name"] == "Siddharth"
    assert final.summary["phone"] == "9876543210"


def test_date_extraction_formats(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    samples = {
        "18 June": "18 June",
        "18th June": "18 June",
        "June 18": "18 June",
        "20th June": "20 June",
        "next Friday": "Next Friday",
        "next Monday": "Next Monday",
    }
    for phrase, expected in samples.items():
        assert memory._extract_preferred_date(phrase) == expected


def test_invalid_date_rejection(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    qualifier = QualificationAgent(memory)
    memory.update_from_message("We need a corporate event.", "corporate_event")
    qualifier.update_and_qualify("50", "corporate_event")
    qualifier.update_and_qualify("JP Nagar", "corporate_event")

    invalid = qualifier.update_and_qualify("W2", "corporate_event")
    assert invalid.response == "Sorry, I didn't quite catch the date. Could you say something like 18 June or June 18?"
    assert "preferred_date" in invalid.missing_fields
    assert memory.data["preferred_date"] == ""

    unrelated = qualifier.update_and_qualify("Ah, but it's in Google.", "corporate_event")
    assert unrelated.response == "Sorry, I didn't quite catch the date. Could you say something like 18 June or June 18?"
    assert memory.data["preferred_date"] == ""


def test_corporate_qualification_completion_end_to_end(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    qualifier = QualificationAgent(memory)

    memory.update_from_message("We need a corporate event.", "corporate_event")
    assert qualifier.qualify("corporate_event").next_question == "How many people are joining?"
    assert qualifier.update_and_qualify("50", "corporate_event").next_question == "Which location would you like to visit?"
    assert qualifier.update_and_qualify("JP Nagar", "corporate_event").next_question == "Got it. What date are you planning for?"
    assert qualifier.update_and_qualify("18 June", "corporate_event").next_question == "Sounds good. Do you need food and beverages as well?"
    assert qualifier.update_and_qualify("Yes", "corporate_event").next_question == "Got it. What's the budget range: Basic, Standard, or Premium?"
    assert qualifier.update_and_qualify("Premium", "corporate_event").next_question == "Perfect. What's your name?"
    assert qualifier.update_and_qualify("Siddharth", "corporate_event").next_question == "Thanks. What's the best phone number for the booking details?"
    final = qualifier.update_and_qualify("9876543210", "corporate_event")

    assert final.qualified
    assert "captured all the information" in final.response
    assert final.summary == {
        "qualified": True,
        "event_type": "corporate",
        "participants": 50,
        "location": "JP Nagar",
        "preferred_date": "18 June",
        "age_group": "",
        "food_required": True,
        "budget_range": "premium",
        "customer_name": "Siddharth",
        "phone": "9876543210",
    }


def test_route_leaves_qualification_when_missing_fields_empty(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("We are 6 kids aged 10 coming to Whitefield.")
    assert result["missing_fields"] == []
    assert result["route"]["next_agent"] != "qualification_agent"


def test_inbound_invalid_date_does_not_repeat_same_prompt(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We need a corporate event for 50 employees.")
    agent.handle_message("JP Nagar")
    result = agent.handle_message("W2")
    assert result["response"] == "Sorry, I didn't quite catch the date. Could you say something like 18 June or June 18?"
    assert agent.memory.data["preferred_date"] == ""


def test_garbage_transcript_handling_for_qualification(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    qualifier = QualificationAgent(memory)
    memory.update_from_message("We need a corporate event.", "corporate_event")
    # Prime _waiting_for by calling qualify() — this tells the QA it is about to
    # ask for 'participants', so the next update_and_qualify call will validate.
    qualifier.qualify("corporate_event")
    qualifier._waiting_for = "participants"  # simulate the agent having asked
    result = qualifier.update_and_qualify("W2", "corporate_event")
    assert result.response == "Sorry, I didn't catch that. Could you repeat it?"
    assert memory.data["participants"] == ""


def test_show_memory_command_outputs_current_memory(monkeypatch, capsys, tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.memory.data["participants"] = 50
    agent.memory.data["location"] = "JP Nagar"
    agent.memory.data["preferred_date"] = "18 June"
    agent.memory.data["food_required"] = True
    agent.memory.data["budget_range"] = "premium"

    class Args:
        speak = False
        debug = False

    inputs = iter(["/show_memory", "/quit"])
    monkeypatch.setattr(main_module, "build_agent", lambda _args: agent)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    run_text_loop(Args())
    output = capsys.readouterr().out
    start = output.index("{")
    end = output.index("\n}", start) + 2
    memory_json = json.loads(output[start:end])
    assert memory_json["participants"] == 50
    assert memory_json["location"] == "JP Nagar"
    assert memory_json["preferred_date"] == "18 June"
    assert memory_json["food_required"] is True
    assert memory_json["budget_range"] == "premium"


def test_cancellation_faq(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("What are your cancellation policies?")
    assert "Cancellation charges" in result["response"]
    assert "appropriate team" in result["response"]


def test_booking_phrase_triggers_help_prompt(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("I want to book")
    assert result["response"].lower().startswith("sure")

def test_booking_phrase_with_variation(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("Can I book a slot?")
    assert result["response"].lower().startswith("sure")


def test_booking_it_triggers_help_prompt(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("Book it")
    assert result["response"].lower().startswith("sure")


def test_booking_after_location_and_room_starts_booking_flow(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("I want to book")
    agent.handle_message("Koramangala")
    agent.handle_message("Murder Mystery")
    result = agent.handle_message("Book it")

    assert result["intent"] == "escape_room_inquiry"
    assert result["route"]["next_agent"] == "qualification_agent"
    assert agent.memory.data["room"] == "Murder Mystery"
    assert "type of event" not in result["response"].lower()
    assert "how many" in result["response"].lower()
    assert not agent.memory.handoff_ready("escape_room_inquiry")


def test_booking_acceptance_after_escape_room_recommendation_moves_to_contact_collection(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We are six adults visiting Whitefield.")
    recommendation = agent.handle_message("We've never done an escape room before.")
    assert "room in mind" in recommendation["response"] or "recommendation" in recommendation["response"]
    agent.handle_message("What would you recommend?")
    assert agent.memory.data["recommended_option"]

    result = agent.handle_message("Book it")

    assert result["intent"] == "escape_room_inquiry"
    assert result["route"]["next_agent"] == "inbound_agent"
    assert agent.memory.data["participants"] == 6
    assert agent.memory.data["location"] == "Whitefield"
    assert agent.memory.data["age_group"] == "adults"
    assert "type of event" not in result["response"].lower()
    assert agent.memory.data["room"]
    assert "date" in result["response"].lower()
    assert "name" not in result["response"].lower()


def test_booking_acceptance_variations_reuse_current_recommendation(tmp_path: Path) -> None:
    for acceptance in ("Reserve it", "Let's do that", "Go ahead", "Sounds good"):
        agent = make_agent(tmp_path)
        agent.handle_message("We are six adults visiting Whitefield.")
        agent.handle_message("We've never done an escape room before.")
        agent.handle_message("What would you recommend?")

        result = agent.handle_message(acceptance)

        assert result["intent"] == "escape_room_inquiry"
        assert agent.memory.data["recommended_option"]
        assert "type of event" not in result["response"].lower()
        assert agent.memory.data["room"]
        assert "date" in result["response"].lower()
        assert result["route"]["next_agent"] == "inbound_agent"


def test_typo_kidss_corrects_adult_recommendation(tmp_path: Path) -> None:
    """Bug: 'kidss' typo should override 'adults' age group and recommend kid-friendly rooms."""
    agent = make_agent(tmp_path)
    # Establish escape_room_inquiry first
    agent.handle_message("I want to book an escape room")
    adult_result = agent.handle_message("We are 5 adults visiting Koramangala")
    assert agent.memory.data["age_group"] == "adults"
    assert agent.memory.data["participants"] == 5
    # Should have adult recommendation or be asking for more info
    assert "room in mind" in adult_result["response"] or "recommendation" in adult_result["response"]

    kids_result = agent.handle_message("kidss")
    assert agent.memory.data["age_group"] == "kids"
    assert kids_result["response"]
    # Should NOT recommend adult rooms (both should be absent)
    assert "Classified" not in kids_result["response"] and "Undercover" not in kids_result["response"]


def test_booking_question_triggers_help_prompt(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("How do I book?")
    assert result["response"].lower().startswith("sure")


def test_budget_question_triggers_sales_prompt(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("Explain the budget")
    resp = result["response"].lower()
    assert result["route"]["next_agent"] == "inbound_agent"
    assert result["route"]["should_handoff"] is False
    assert "pricing depends" in resp or "final amount" in resp


def test_new_user_query_offers(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("What do you have, I'm new to this")
    resp = result["response"].lower()
    assert "escape room" in resp or "offers" in resp


def test_offering_question_is_answered(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("What do you offer?")
    assert "offers live escape room experiences" in result["response"]
    assert "Koramangala" in result["response"]
    assert "Whitefield" in result["response"]
    assert "JP Nagar" in result["response"]


def test_offering_question_with_typo_is_answered(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("What do you offr?")
    assert "offers live escape room experiences" in result["response"]
    assert "Koramangala" in result["response"]
    assert "Whitefield" in result["response"]
    assert "JP Nagar" in result["response"]


def test_food_options(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("What food options do you have?")
    assert "continental" in result["response"].lower()
    assert "hi-tea" in result["response"].lower()


def test_food_question_overrides_stale_recommendation(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We are six adults visiting Whitefield.")
    agent.handle_message("We've never done an escape room before.")
    result = agent.handle_message("What food options are available?")
    assert "continental" in result["response"].lower()
    assert "Murder Mystery" not in result["response"]


def test_corporate_question_overrides_recommendation_flow(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We are six adults visiting Whitefield.")
    result = agent.handle_message("We need an event for 15 employees.")
    assert result["intent"] == "corporate_event"
    assert "team of 15" in result["response"]
    assert "Scavenger Hunt" in result["response"]
    assert "first-time players" not in result["response"]


def test_qualification_does_not_trap_new_question(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We need an event for 15 employees.")
    response = agent.handle_message("Which room will you suggest for 15 employees?")["response"]
    assert "corporate group" in response
    assert "Escape Rooms" in response
    assert "What date" not in response


def test_yes_after_compare_prompt_gives_comparison(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We are six adults visiting Whitefield.")
    first_time = agent.handle_message("We have never done an escape room before.")["response"]
    assert "room in mind" in first_time or "recommendation" in first_time
    comparison = agent.handle_message("What would you recommend?")["response"]
    assert "Murder Mystery" in comparison or "Bomb Defusal" in comparison
    assert "Would you like me to compare those two?" not in comparison


def test_partial_transcript_asks_for_clarification(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We are six adults visiting Whitefield.")
    before = dict(agent.memory.data)
    result = agent.handle_message("We have never")
    assert result["response"] == "Sorry, I didn't catch that. Could you repeat it?"
    assert agent.memory.data["participants"] == before["participants"]
    assert len(agent.memory.data["conversation"]) == len(before["conversation"])


def test_new_voice_call_resets_memory_before_first_listen(monkeypatch, tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.memory.data["participants"] = 2
    agent.memory.data["location"] = "Whitefield"
    agent.memory.data["experience_level"] = "beginner"
    agent.memory.save()
    calls: list[str] = []

    class FakeVoiceInput:
        last_status = "empty"

        def __init__(self, *args, **kwargs):
            pass

        def record_and_transcribe(self, seconds: int):
            calls.append("listen")
            raise KeyboardInterrupt

    class FakeVoiceOutput:
        def __init__(self, *args, **kwargs):
            pass

        def speak(self, text: str):
            calls.append(f"speak:{text}")

    class FakeLogger:
        def __init__(self, *args, **kwargs):
            pass

        def log_turn(self, *args, **kwargs):
            pass

    class Args:
        whisper_model = "small"
        debug = False
        record_seconds = 12

    monkeypatch.setattr(main_module, "build_agent", lambda _args: agent)
    monkeypatch.setattr(main_module, "VoiceInput", FakeVoiceInput)
    monkeypatch.setattr(main_module, "VoiceOutput", FakeVoiceOutput)
    monkeypatch.setattr(main_module, "TranscriptLogger", FakeLogger)
    monkeypatch.setattr(main_module.time, "sleep", lambda _seconds: None)

    run_voice_loop(Args())
    assert calls[0].startswith("speak:Hello, thank you")
    assert calls[1] == "listen"
    assert agent.memory.data["participants"] == ""
    assert agent.memory.data["location"] == ""
    assert agent.memory.data["experience_level"] == ""


def test_event_packages(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    birthday = agent.handle_message("What birthday package activities do you have?")["response"]
    assert "Showstopper" in birthday
    assert "Karaoke" in birthday


def test_beginner_guidance(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    response = agent.handle_message("We've never done an escape room before.")["response"]
    assert "How many people" in response or "How many people".lower() in response.lower()
    assert "How many people" in response


def test_couple_guidance(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    response = agent.handle_message("We are a couple.")["response"]
    assert "Murder Mystery" in response
    assert "teamwork" in response
    assert "location" in response.lower()


def test_goodbye_handling() -> None:
    for message in ["quit", "goodbye", "exit", "stop", "Bye!"]:
        assert is_exit_command(message)


def test_empty_transcripts_are_ignored() -> None:
    assert not should_process_transcript("")
    assert not should_process_transcript("   ")
    assert should_process_transcript("hello")


def test_tts_completes_before_listening_resumes(monkeypatch) -> None:
    calls: list[str] = []

    class FakeVoiceOutput:
        def speak(self, text: str) -> None:
            calls.append(f"speak:{text}")

    monkeypatch.setattr(main_module.time, "sleep", lambda seconds: calls.append(f"sleep:{seconds}"))
    speak_then_resume_listening(FakeVoiceOutput(), "Hello")
    assert calls == [f"speak:Hello", f"sleep:{main_module.POST_TTS_COOLDOWN_SECONDS}"]


def test_tts_debug_lifecycle(monkeypatch, capsys) -> None:
    """VoiceOutput.speak() must emit TTS START and TTS END when debug=True."""
    from src.voice.tts.voice_output import VoiceOutput

    class FakeEngine:
        def setProperty(self, *a, **kw):
            pass

        def say(self, text: str) -> None:
            pass

        def runAndWait(self) -> None:
            pass

    import src.voice.tts.voice_output as vo_module
    monkeypatch.setattr(main_module.time, "sleep", lambda _s: None)

    vo = VoiceOutput(enabled=True, debug=True)
    # Inject the fake engine directly to avoid importing pyttsx3
    vo._engine = FakeEngine()

    vo.speak("Hello")
    output = capsys.readouterr().out
    assert "TTS START" in output
    assert "TTS END" in output
    assert "CHARS=" in output
    assert "DURATION=" in output


def test_unclear_transcript_message_is_customer_facing() -> None:
    message = "Sorry, I didn't catch that. Could you repeat it?"
    assert should_process_transcript(message)


def test_final_demo_scenarios(tmp_path: Path) -> None:
    agent = make_agent(tmp_path / "basics")

    assert "Breakout Escape Rooms" in agent.handle_message("Hi, is this Breakout?")["response"]
    assert "Koramangala" in agent.handle_message("What locations do you have?")["response"]
    assert "parking" in agent.handle_message("Is parking available?")["response"].lower()
    assert "Cancellation charges" in agent.handle_message("What is your cancellation policy?")["response"]

    adult_agent = make_agent(tmp_path / "adult")
    adult = adult_agent.handle_message("We are six adults visiting Whitefield.")["response"]
    assert "room in mind" in adult or "recommendation" in adult

    beginner_agent = make_agent(tmp_path / "beginner")
    beginner = beginner_agent.handle_message("We've never done an escape room before.")["response"]
    assert "How many people" in beginner

    corporate_agent = make_agent(tmp_path / "corporate")
    corporate = corporate_agent.handle_message("We need an event for fifteen employees.")["response"]
    assert "team of 15" in corporate
    assert "Which location" in corporate

    birthday_agent = make_agent(tmp_path / "birthday")
    birthday = birthday_agent.handle_message("I want a birthday party for 35 guests in Whitefield.")["response"]
    assert "Whitefield can accommodate approximately 35-40 guests" in birthday

    food_agent = make_agent(tmp_path / "food")
    assert "food options" in food_agent.handle_message("What food options do you have?")["response"].lower()

    assert is_exit_command("Goodbye.")


# ===========================================================================
# NEW REGRESSION TESTS — Bugs 1-10
# ===========================================================================

def test_name_capture_bare_word(tmp_path: Path) -> None:
    """Bug 2: Bare single-word name like 'Siddharth' must be captured."""
    memory = ConversationMemory(tmp_path / "session.json")
    qualifier = QualificationAgent(memory)
    memory.update_from_message("We need a corporate event.", "corporate_event")
    # Advance to the customer_name field
    qualifier.update_and_qualify("50", "corporate_event")
    qualifier.update_and_qualify("JP Nagar", "corporate_event")
    qualifier.update_and_qualify("18 June", "corporate_event")
    qualifier.update_and_qualify("Yes", "corporate_event")
    qualifier.update_and_qualify("Premium", "corporate_event")
    # Now the QA should be asking for customer_name
    result = qualifier.update_and_qualify("Siddharth", "corporate_event")
    assert memory.data["customer_name"] == "Siddharth"
    assert "customer_name" not in result.missing_fields


def test_phone_capture(tmp_path: Path) -> None:
    """Bug 3: 10-digit phone number must be captured after name."""
    memory = ConversationMemory(tmp_path / "session.json")
    qualifier = QualificationAgent(memory)
    memory.update_from_message("We need a corporate event.", "corporate_event")
    qualifier.update_and_qualify("50", "corporate_event")
    qualifier.update_and_qualify("JP Nagar", "corporate_event")
    qualifier.update_and_qualify("18 June", "corporate_event")
    qualifier.update_and_qualify("Yes", "corporate_event")
    qualifier.update_and_qualify("Premium", "corporate_event")
    qualifier.update_and_qualify("Siddharth", "corporate_event")
    result = qualifier.update_and_qualify("9876543210", "corporate_event")
    assert memory.data["phone"] == "9876543210"
    assert result.qualified


def test_date_capture_variations(tmp_path: Path) -> None:
    """Bug 6: All date formats must parse correctly."""
    from src.memory.conversation_memory import ConversationMemory as CM
    memory = CM(tmp_path / "session.json")
    cases = {
        "18 June": "18 June",
        "18th June": "18 June",
        "June 18": "18 June",
        "20th June": "20 June",
        "next Friday": "Next Friday",
    }
    for phrase, expected in cases.items():
        result = memory._extract_preferred_date(phrase)
        assert result == expected, f"Failed for '{phrase}': got '{result}'"


def test_invalid_phone_rejected(tmp_path: Path) -> None:
    """Bug 7: Invalid phone input must be rejected with a helpful message."""
    memory = ConversationMemory(tmp_path / "session.json")
    qualifier = QualificationAgent(memory)
    memory.update_from_message("We need a corporate event.", "corporate_event")
    qualifier.update_and_qualify("50", "corporate_event")
    qualifier.update_and_qualify("JP Nagar", "corporate_event")
    qualifier.update_and_qualify("18 June", "corporate_event")
    qualifier.update_and_qualify("Yes", "corporate_event")
    qualifier.update_and_qualify("Premium", "corporate_event")
    qualifier.update_and_qualify("Siddharth", "corporate_event")
    # Now _waiting_for == "phone"; invalid input should be rejected
    result = qualifier.update_and_qualify("see you there", "corporate_event")
    assert "phone" in result.response.lower()
    assert memory.data["phone"] == ""


def test_qualification_completion_response(tmp_path: Path) -> None:
    """Bug 4: After all fields are collected, response must say 'Qualification complete'."""
    memory = ConversationMemory(tmp_path / "session.json")
    qualifier = QualificationAgent(memory)
    memory.update_from_message("We need a corporate event.", "corporate_event")
    qualifier.update_and_qualify("50", "corporate_event")
    qualifier.update_and_qualify("JP Nagar", "corporate_event")
    qualifier.update_and_qualify("18 June", "corporate_event")
    qualifier.update_and_qualify("Yes", "corporate_event")
    qualifier.update_and_qualify("Premium", "corporate_event")
    qualifier.update_and_qualify("Siddharth", "corporate_event")
    final = qualifier.update_and_qualify("9876543210", "corporate_event")
    assert final.qualified
    assert "captured all the information" in final.response


def test_no_recommendation_leak_during_qualification(tmp_path: Path) -> None:
    """Bug 1 & 5: After date is captured, response must NOT contain recommendation blurb."""
    agent = make_agent(tmp_path)
    agent.handle_message("We need an event for 15 employees.")
    agent.handle_message("JP Nagar")
    response = agent.handle_message("18 June")["response"]
    # Should NOT contain the corporate recommendation blurb
    assert "For a corporate team" not in response
    assert "Scavenger Hunt" not in response
    # Should ONLY ask for the next field
    assert "name" in response.lower() or "food" in response.lower() or "budget" in response.lower()


def test_routing_exits_after_qualification(tmp_path: Path) -> None:
    """Bug 8: Once qualification is complete, route must not be qualification_agent."""
    memory = ConversationMemory(tmp_path / "session.json")
    qualifier = QualificationAgent(memory)
    router = __import__("src.orchestration.router", fromlist=["Router"]).Router()
    memory.update_from_message("We need a corporate event.", "corporate_event")
    qualifier.update_and_qualify("50", "corporate_event")
    qualifier.update_and_qualify("JP Nagar", "corporate_event")
    qualifier.update_and_qualify("18 June", "corporate_event")
    qualifier.update_and_qualify("Yes", "corporate_event")
    qualifier.update_and_qualify("Premium", "corporate_event")
    qualifier.update_and_qualify("Siddharth", "corporate_event")
    final = qualifier.update_and_qualify("9876543210", "corporate_event")
    route = router.route("corporate_event", final.qualified)
    assert route.next_agent != "qualification_agent"
    assert route.next_agent == "corporate_events_agent"


def test_show_memory_includes_name_and_phone(monkeypatch, capsys, tmp_path: Path) -> None:
    """Bug 9: After capture, customer_name and phone must appear in /show_memory output."""
    agent = make_agent(tmp_path)
    agent.memory.data["customer_name"] = "Siddharth"
    agent.memory.data["phone"] = "9876543210"

    class Args:
        speak = False
        debug = False

    inputs = iter(["/show_memory", "/quit"])
    monkeypatch.setattr(main_module, "build_agent", lambda _args: agent)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    run_text_loop(Args())
    output = capsys.readouterr().out
    start = output.index("{")
    end = output.index("\n}", start) + 2
    memory_json = json.loads(output[start:end])
    assert memory_json["customer_name"] == "Siddharth"
    assert memory_json["phone"] == "9876543210"


def test_full_corporate_flow_end_to_end(tmp_path: Path) -> None:
    """Full 7-turn corporate qualification flow: no leaks, correct capture, clean completion."""
    agent = make_agent(tmp_path)

    r1 = agent.handle_message("We need an event for 15 employees.")["response"]
    assert "team of 15" in r1
    assert "Scavenger Hunt" in r1
    assert "Which location" in r1

    r2 = agent.handle_message("JP Nagar")["response"]
    assert "date" in r2.lower()
    assert "For a corporate team" not in r2  # no recommendation leak

    r3 = agent.handle_message("18 June")["response"]
    assert "For a corporate team" not in r3  # Bug 5: no recommendation leak after date
    # Should ask for food, budget, name, or phone — not location again
    assert "Which location" not in r3

    r4 = agent.handle_message("Yes")["response"]
    assert "budget" in r4.lower() or "name" in r4.lower()

    r5 = agent.handle_message("Premium")["response"]
    assert "name" in r5.lower()

    r6 = agent.handle_message("Siddharth")["response"]
    assert agent.memory.data["customer_name"] == "Siddharth"
    assert "phone" in r6.lower() or "number" in r6.lower()

    r7 = agent.handle_message("9876543210")["response"]
    assert agent.memory.data["phone"] == "9876543210"
    # Either the QA completion message or the handoff message is correct;
    assert "captured all the information" in r7 or "connect you" in r7 or "got everything I need" in r7 or "check availability" in r7


def test_invalid_name_input_asks_again(tmp_path: Path) -> None:
    """Bug 7 (previous): If customer says something that isn't a name when name is expected, ask again."""
    memory = ConversationMemory(tmp_path / "session.json")
    qualifier = QualificationAgent(memory)
    memory.update_from_message("We need a corporate event.", "corporate_event")
    qualifier.update_and_qualify("50", "corporate_event")
    qualifier.update_and_qualify("JP Nagar", "corporate_event")
    qualifier.update_and_qualify("18 June", "corporate_event")
    qualifier.update_and_qualify("Yes", "corporate_event")
    qualifier.update_and_qualify("Premium", "corporate_event")
    # Now _waiting_for == "customer_name"
    result = qualifier.update_and_qualify("9876543210", "corporate_event")  # phone number, not a name
    assert memory.data["customer_name"] == ""
    # The agent should ask again (garbage reply or explicit name request)
    assert result.response != "" and "customer_name" in result.missing_fields


# ===========================================================================
# NEW REGRESSION TESTS — Bugs 1-8 (this batch)
# ===========================================================================


# --- Bug 1: Date extraction voice-natural variants ---------------------------

def test_date_extraction_voice_natural(tmp_path: Path) -> None:
    """Bug 1: All real voice-spoken date phrases must be parsed to a normalized date."""
    from src.memory.conversation_memory import ConversationMemory as CM
    memory = CM(tmp_path / "session.json")
    cases = {
        # basic ordinal with "of"
        "18th of June": "18 June",
        # full sentence — "I am planning for 18th of June"
        "I am planning for 18th of June": "18 June",
        # "we are planning for 18 June"
        "We are planning for 18 June": "18 June",
        # "around 18 June"
        "Around 18 June": "18 June",
        # no ordinal, just month-day
        "18 June": "18 June",
        # Month first
        "June 18th": "18 June",
        # "I'm thinking of 20th of July"
        "I'm thinking of 20th of July": "20 July",
        # next-relative
        "Next Friday": "Next Friday",
        "I'm looking at next Saturday": "Next Saturday",
    }
    for phrase, expected in cases.items():
        result = memory._extract_preferred_date(phrase)
        assert result == expected, f"Failed for '{phrase}': got '{result}', expected '{expected}'"


def test_date_extraction_in_qualification(tmp_path: Path) -> None:
    """Bug 1: QualificationAgent must accept '18th of June' without re-asking."""
    memory = ConversationMemory(tmp_path / "session.json")
    qualifier = QualificationAgent(memory)
    memory.update_from_message("We need a corporate event.", "corporate_event")
    qualifier.update_and_qualify("50", "corporate_event")
    qualifier.update_and_qualify("JP Nagar", "corporate_event")
    # _waiting_for == "preferred_date"; send the voice-natural phrase
    result = qualifier.update_and_qualify("18th of June", "corporate_event")
    assert memory.data["preferred_date"] == "18 June"
    assert "preferred_date" not in result.missing_fields


def test_date_extraction_sentence_form(tmp_path: Path) -> None:
    """Bug 1: 'I am planning for 18th of June' must set preferred_date."""
    memory = ConversationMemory(tmp_path / "session.json")
    qualifier = QualificationAgent(memory)
    memory.update_from_message("We need a corporate event.", "corporate_event")
    qualifier.update_and_qualify("50", "corporate_event")
    qualifier.update_and_qualify("JP Nagar", "corporate_event")
    qualifier._waiting_for = "preferred_date"
    result = qualifier.update_and_qualify("I am planning for 18th of June", "corporate_event")
    assert memory.data["preferred_date"] == "18 June", (
        f"Got: {memory.data['preferred_date']!r}"
    )
    assert "preferred_date" not in result.missing_fields


# --- Bug 2: Location fuzzy matching -----------------------------------------

def test_location_fuzzy_jp_nuggets(tmp_path: Path) -> None:
    """Bug 2: Whisper mishearing 'JP Nuggets' must resolve to 'JP Nagar'."""
    memory = ConversationMemory(tmp_path / "session.json")
    assert memory._extract_location("jp nuggets") == "JP Nagar"


def test_location_fuzzy_white_shield(tmp_path: Path) -> None:
    """Bug 2: Whisper mishearing 'White Shield' must resolve to 'Whitefield'."""
    memory = ConversationMemory(tmp_path / "session.json")
    assert memory._extract_location("white shield") == "Whitefield"


def test_location_fuzzy_wide_field(tmp_path: Path) -> None:
    """Bug 2: Whisper mishearing 'Wide Field' must resolve to 'Whitefield'."""
    memory = ConversationMemory(tmp_path / "session.json")
    assert memory._extract_location("wide field") == "Whitefield"


def test_location_fuzzy_koramangla(tmp_path: Path) -> None:
    """Bug 2: Whisper mishearing 'Koramangla' must resolve to 'Koramangala'."""
    memory = ConversationMemory(tmp_path / "session.json")
    assert memory._extract_location("koramangla") == "Koramangala"


def test_location_fuzzy_accepted_by_qualification(tmp_path: Path) -> None:
    """Bug 2: QA must accept 'JP Nuggets' as a valid location and store 'JP Nagar'."""
    memory = ConversationMemory(tmp_path / "session.json")
    qualifier = QualificationAgent(memory)
    memory.update_from_message("We need a corporate event.", "corporate_event")
    qualifier.update_and_qualify("50", "corporate_event")
    qualifier._waiting_for = "location"
    result = qualifier.update_and_qualify("JP Nuggets", "corporate_event")
    assert memory.data["location"] == "JP Nagar", f"Got: {memory.data['location']!r}"
    assert "location" not in result.missing_fields


# --- Bug 3+4: VoiceOutput TTS safety ----------------------------------------

def test_voice_output_chunking_long_text() -> None:
    """Bug 3: VoiceOutput._split_into_chunks must split long text at sentence boundaries."""
    from src.voice.tts.voice_output import VoiceOutput
    text = ("This is sentence one. This is sentence two. This is sentence three. "
            "This is sentence four. This is sentence five. This is sentence six.")
    chunks = VoiceOutput._split_into_chunks(text, threshold=80)
    # Every chunk must be ≤ threshold chars (approximately)
    for chunk in chunks:
        assert len(chunk) <= 160, f"Chunk too long: {chunk!r}"
    # Reassembling must contain all words
    reassembled = " ".join(chunks)
    for sentence in ["sentence one", "sentence two", "sentence six"]:
        assert sentence in reassembled


def test_voice_output_short_text_not_chunked() -> None:
    """Bug 3: Short responses must not be chunked."""
    from src.voice.tts.voice_output import VoiceOutput
    text = "Hello!"
    assert VoiceOutput._split_into_chunks(text) == ["Hello!"]


def test_tts_runs_before_cooldown(monkeypatch) -> None:
    """Bug 4: speak() must complete BEFORE the cooldown sleep fires."""
    calls: list[str] = []

    class FakeVoiceOutput:
        def speak(self, text: str) -> None:
            calls.append(f"speak:{text}")

    monkeypatch.setattr(main_module.time, "sleep", lambda _s: calls.append("sleep"))
    speak_then_resume_listening(FakeVoiceOutput(), "Hello")
    assert calls.index("speak:Hello") < calls.index("sleep"), (
        "TTS must complete before cooldown sleep"
    )


# --- Bug 5: Qualification field completeness after capture ------------------

def test_fields_not_re_asked_after_capture(tmp_path: Path) -> None:
    """Bug 5: Once a field is captured, it must not appear in missing_fields again."""
    memory = ConversationMemory(tmp_path / "session.json")
    qualifier = QualificationAgent(memory)
    memory.update_from_message("We need a corporate event.", "corporate_event")

    steps = [
        ("50", "participants"),
        ("JP Nagar", "location"),
        ("18 June", "preferred_date"),
        ("Yes", "food_required"),
        ("Premium", "budget_range"),
        ("Siddharth", "customer_name"),
        ("9876543210", "phone"),
    ]
    captured_so_far: set[str] = set()
    for answer, field_just_captured in steps:
        result = qualifier.update_and_qualify(answer, "corporate_event")
        captured_so_far.add(field_just_captured)
        # None of the already-captured fields should appear in missing_fields
        for captured in captured_so_far:
            assert captured not in result.missing_fields, (
                f"'{captured}' reappeared in missing_fields after being captured. "
                f"Response was: {result.response!r}"
            )


# --- Bug 7: /show_memory contains all qualified fields ----------------------

def test_show_memory_all_fields_after_full_qualification(monkeypatch, capsys, tmp_path: Path) -> None:
    """Bug 7: /show_memory after full corporate qualification must include all 9 captured fields."""
    agent = make_agent(tmp_path)

    agent.handle_message("We need a corporate event for 50 employees.")
    agent.handle_message("JP Nagar")
    agent.handle_message("18 June")
    agent.handle_message("Yes")
    agent.handle_message("Premium")
    agent.handle_message("Siddharth")
    agent.handle_message("9876543210")

    class Args:
        speak = False
        debug = False

    inputs = iter(["/show_memory", "/quit"])
    monkeypatch.setattr(main_module, "build_agent", lambda _args: agent)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    run_text_loop(Args())
    output = capsys.readouterr().out
    start = output.index("{")
    end = output.index("\n}", start) + 2
    mem = json.loads(output[start:end])
    assert mem["location"] == "JP Nagar"
    assert mem["preferred_date"] == "18 June"
    assert mem["food_required"] is True
    assert mem["customer_name"] == "Siddharth"
    assert mem["phone"] == "9876543210"



# ===========================================================================
# PRODUCTION CONVERSATION UPGRADE TESTS
# ===========================================================================

def test_interruption_mid_qualification_faq_answered(tmp_path: Path) -> None:
    """Interruption mid-qualification: customer asks a FAQ, agent answers and resumes."""
    agent = make_agent(tmp_path)
    # Start corporate event flow
    agent.handle_message("We need a corporate event for 50 employees.")
    # Expecting: "Which location would you prefer: Koramangala, Whitefield, or JP Nagar?"
    # Customer asks about kids instead of answering location:
    r = agent.handle_message("Which location would be better for kids?")["response"]
    assert "Whitefield" in r
    assert "to finalize" not in r.lower()
    assert "coming back to" not in r.lower()
    assert "location" in r.lower() or "koramangala" in r.lower()
    # The qualification field should still be missing/expected next time
    assert agent.memory.data["location"] == ""


def test_interruption_mid_food_question_parking_answered(tmp_path: Path) -> None:
    """Interruption mid-qualification: customer asks about parking while food_required is expected."""
    agent = make_agent(tmp_path)
    agent.handle_message("We need a corporate event for 50 employees.")
    agent.handle_message("JP Nagar")
    agent.handle_message("18 June")
    # Expected next field is food_required
    # Interruption: ask about parking
    r = agent.handle_message("Where do we park?")["response"]
    assert "parking" in r.lower()
    assert "food" in r.lower() # resumes asking for food


def test_booking_consent_flow(tmp_path: Path) -> None:
    """Booking consent: when qualification is complete, ask for booking consent instead of instant handoff."""
    agent = make_agent(tmp_path)
    agent.handle_message("We need a corporate event for 50 employees.")
    agent.handle_message("JP Nagar")
    agent.handle_message("18 June")
    agent.handle_message("Yes") # food_required
    agent.handle_message("Premium") # budget_range
    agent.handle_message("Siddharth") # customer_name

    # Last turn of qualification: phone number
    r = agent.handle_message("9876543210")
    # Should ask for consent
    assert "Shall I check availability" in r["response"]
    assert r["route"]["next_agent"] == "inbound_agent" # not handed off yet!
    assert r["route"]["should_handoff"] is False
    assert agent.memory.data["booking_consent_pending"] is True
    assert agent.memory.data["current_workflow"] == "awaiting_booking"

    # Say no to booking consent
    r_no = agent.handle_message("No thanks")
    assert "Feel free to call us when you're ready" in r_no["response"] or "No problem" in r_no["response"]
    assert agent.memory.data["booking_consent_pending"] is False
    assert agent.memory.data["current_workflow"] == "general"


def test_booking_consent_yes_triggers_handoff(tmp_path: Path) -> None:
    """Booking consent: customer says 'Yes' to check availability, triggers handoff."""
    agent = make_agent(tmp_path)
    agent.handle_message("We need a corporate event for 50 employees.")
    agent.handle_message("JP Nagar")
    agent.handle_message("18 June")
    agent.handle_message("Yes")
    agent.handle_message("Premium")
    agent.handle_message("Siddharth")
    agent.handle_message("9876543210") # Qualification finishes, asks consent

    # Say yes to check availability
    r_yes = agent.handle_message("Sure, go ahead")
    assert r_yes["route"]["next_agent"] == "booking_agent"
    assert r_yes["route"]["should_handoff"] is True
    assert agent.memory.data["booking_consent_pending"] is False
    assert agent.memory.data["current_workflow"] == "booking"


# ===========================================================================
# PRODUCTION BLOCKERS FIXES TESTS
# ===========================================================================

def test_interruption_preserves_qualification_state_across_faq(tmp_path: Path) -> None:
    """Bug 1 Fix check: Interruption does not clear the qualification intent or expected field in memory."""
    agent = make_agent(tmp_path)
    # Start corporate flow
    agent.handle_message("We need a corporate event for 50 employees.") # location expected
    agent.handle_message("Whitefield") # date expected
    agent.handle_message("18 June") # food expected
    agent.handle_message("Yes") # budget expected

    # Expected field is now budget_range. Let's verify that
    assert agent.qualification_agent._waiting_for == "budget_range"

    # Interruption turn
    r_interruption = agent.handle_message("Which location is better for kids?")
    # Check that recommendation FAQ matched
    assert "usually recommend Whitefield" in r_interruption["response"]
    assert "budget" in r_interruption["response"]

    # Check that the intent in memory remains "corporate_event"
    assert agent.memory.data["intent"] == "corporate_event"
    assert agent.qualification_agent._waiting_for == "budget_range"

    # Second interruption turn (follow-up FAQ)
    r_interruption2 = agent.handle_message("know which location is better.")
    assert "Koramangala, Whitefield, and JP Nagar" in r_interruption2["response"]
    assert "budget" in r_interruption2["response"]

    assert agent.memory.data["intent"] == "corporate_event"
    assert agent.qualification_agent._waiting_for == "budget_range"

    # Now reply to budget
    r_budget = agent.handle_message("Premium")
    # Verify budget captured
    assert agent.memory.data["budget_range"] == "premium"
    # Verification is advanced to customer_name
    assert agent.qualification_agent._waiting_for == "customer_name"
    assert "name" in r_budget["response"]


def test_kids_location_faq_direct_recommendation(tmp_path: Path) -> None:
    """Bug 2 Fix check: Kids location question returns the direct Whitefield recommendation."""
    agent = make_agent(tmp_path)
    response = agent.handle_message("Which location is better for kids?")["response"]
    assert "usually recommend Whitefield" in response
    assert "family groups often enjoy it" in response


def test_ollama_availability_and_timeout_safety(tmp_path: Path) -> None:
    """Bug 4 & 5 Fix check: Agent handles Ollama timeouts and offline states gracefully without crashing."""
    agent = make_agent(tmp_path)
    # Set to qwen3:8b and enable use_ollama. It will run the startup check and disable it because qwen3 is not there
    agent.use_ollama = True
    avail = agent._check_ollama_availability()
    assert isinstance(avail, bool)

    # In this test, make_agent sets use_ollama=False, but if we create one with True:
    from src.knowledge.knowledge_loader import KnowledgeLoader
    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    agent_with_ollama = InboundAgent(
        knowledge_base=knowledge,
        memory=agent.memory,
        prompt_path=Path(__file__).resolve().parent.parent / "prompts" / "inbound_prompt.txt",
        use_ollama=True,
    )
    # Check that it sets use_ollama to the checked state
    assert agent_with_ollama.use_ollama == avail


# ===========================================================================
# CONVERSATION INTELLIGENCE LAYER TESTS (CASES 1 - 5)
# ===========================================================================

def test_case_1_seven_friends_first_time(tmp_path: Path) -> None:
    """Case 1: Group of 7 friends, first-timers, recommending rooms and asking for location."""
    agent = make_agent(tmp_path)

    r1 = agent.handle_message("Hi.")["response"]
    assert "help you today" in r1.lower()

    # 7 friends should set participants to 7
    r2 = agent.handle_message("We're a group of 7 friends.")["response"]
    assert agent.memory.data["participants"] == 7
    assert "escape room before" in r2.lower() or "first one" in r2.lower()

    # First time should set beginner experience level and move the conversation forward.
    # The agent either asks for the age group OR (when participants are already known)
    # skips ahead to a beginner-appropriate recommendation and asks for location.
    # Both are correct continuations — we validate the documented behaviour from the
    # test docstring: "recommending rooms and asking for location".
    r3 = agent.handle_message("None of us have done an escape room before.")["response"]
    assert agent.memory.data["experience_level"] == "beginner"
    r3_lower = r3.lower()
    # Agent must either ask for age group OR provide a recommendation + ask for location
    age_group_asked = "adults, kids, or a mix" in r3_lower or "age group" in r3_lower or "adults" in r3_lower
    recommendation_with_location = (
        any(room in r3_lower for room in ["murder mystery", "hostage", "classified", "bomb", "locked"])
        and ("location" in r3_lower or "branch" in r3_lower)
    )
    assert age_group_asked or recommendation_with_location, (
        f"Turn 3 should either ask for age group or give a recommendation with location question, got: {r3!r}"
    )


def test_first_time_friends_receive_recommendation_before_qualification(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    # New flow: with only participants + first-time, agent asks for age_group first
    response = agent.handle_message("We are 7 friends and none of us have played before.")["response"]

    assert agent.memory.data["participants"] == 7
    assert agent.memory.data["experience_level"] == "beginner"
    # Age group question should come first (recommendation comes after age_group is provided)
    assert "adults" in response.lower() or "kids" in response.lower() or "age" in response.lower()

    # After providing age_group, location comes before recommendation.
    response2 = agent.handle_message("We are all adults.")["response"]
    assert response2 == "Which location would you like to visit?"


def test_late_customer_gets_rescue_response(tmp_path: Path) -> None:
    response = make_agent(tmp_path).handle_message("We are running late.")["response"]
    assert "Don't worry" in response
    assert "back to back" in response
    assert "How late" in response


def test_customer_is_reassured_if_they_do_not_escape(tmp_path: Path) -> None:
    response = make_agent(tmp_path).handle_message("What happens if we don't escape?")["response"]
    assert "won't keep you locked forever" in response
    assert "not actually locked" in response
    assert "monitors" in response


def test_family_recommendation_is_not_intercepted_by_children_faq(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.memory.data["location"] = "Whitefield"
    response = agent.handle_message(
        "We have 5 children aged 11. What would you recommend?"
    )["response"]
    assert "Murder Mystery" in response
    assert "Hostage" in response
    assert "5 players aged 11" in response
    assert "Classified" not in response
    assert "Bomb Defusal" not in response


def test_escape_room_briefing_answer_is_grounded(tmp_path: Path) -> None:
    response = make_agent(tmp_path).handle_message("Can you explain the briefing before the game?")["response"]
    assert "brief" in response.lower()
    assert "50 minutes" in response
    assert "20 minutes before" in response


def test_case_2_kids_and_adults_location_recommendation(tmp_path: Path) -> None:
    """Case 2: 2 kids and 4 adults should sum to 6 participants and recommend Whitefield."""
    agent = make_agent(tmp_path)

    r1 = agent.handle_message("We have 2 kids and 4 adults.")["response"]
    assert agent.memory.data["participants"] == 6
    assert agent.memory.data["age_group"] == "kids"

    r2 = agent.handle_message("Which location would be better?")["response"]
    assert "Whitefield" in r2
    assert "family groups" in r2.lower()


def test_case_3_dislikes_puzzles_consultation(tmp_path: Path) -> None:
    """Case 3: Customer concern 'no puzzles' is answered consultatively and qualification is resumed."""
    agent = make_agent(tmp_path)

    # Start qualification
    agent.handle_message("We need an event for 15 employees.")
    # Expected next field: location
    assert agent.qualification_agent._waiting_for == "location"

    # Interruption turn
    r = agent.handle_message("Some people in our group don't enjoy puzzles. Would they still have fun?")["response"]
    assert "common" in r.lower()
    assert "puzzles" in r.lower()
    assert "immersive" in r.lower() or "story-driven" in r.lower()
    assert "Murder Mystery" in r
    # Ensure qualification is resumed
    assert "Which location would you like to visit?" in r


def test_case_4_bangalore_next_weekend(tmp_path: Path) -> None:
    """Case 4: Recommending popular experiences and asking location for weekend visitors."""
    agent = make_agent(tmp_path)

    r = agent.handle_message("We're coming to Bangalore next weekend. What's the most popular experience?")["response"]
    assert "Murder Mystery" in r
    assert "Hostage" in r
    assert "Which location would you like to visit?" in r
    assert agent.memory.data["preferred_date"] == "Next Weekend"


def test_case_5_pronominal_which_one_resolution(tmp_path: Path) -> None:
    """Case 5: Resolves 'Which one would you suggest?' using discussed_options history."""
    agent = make_agent(tmp_path)

    # Prime discussed options
    agent.memory.data["discussed_options"] = ["Murder Mystery", "Hostage"]
    agent.memory.data["experience_level"] = "beginner"
    agent.memory.save()

    r = agent.handle_message("Which one would you suggest?")["response"]
    assert "lean toward Murder Mystery" in r
    assert "without intense time pressure" in r


# ===========================================================================
# PREEMPTION & ROUTING OWNERSHIP TESTS
# ===========================================================================

def test_booking_agent_preemption_on_faq(tmp_path: Path) -> None:
    import main as main_module
    from src.agents.inbound_agent import InboundAgent
    from src.agents.booking_agent import BookingAgent
    from src.knowledge.knowledge_loader import KnowledgeLoader

    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = ConversationMemory(tmp_path / "session.json")

    # Qualification complete fields to start booking agent
    memory.data.update({
        "intent": "corporate_event",
        "location": "Whitefield",
        "participants": 10,
        "preferred_date": "18 June",
        "customer_name": "Siddharth",
        "phone": "9876543210",
        "food_required": True,
        "budget_range": "premium",
        "current_workflow": "booking",
    })
    memory.save()

    inbound = InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_ollama=False,
    )
    booking = BookingAgent(memory)
    booking._state = booking._STATE_WAITING_FOR_SLOT
    booking._available_slots = ["10:00 AM", "3:00 PM"]

    # Ask a FAQ: "Which location is better for kids?"
    result, booking_inst, active = main_module.dispatch(
        "Which location is better for kids?", inbound, booking, "booking_agent"
    )

    assert active == "inbound_agent", "Should preempt back to inbound agent"
    assert "Whitefield" in result.response
    assert "usually recommend Whitefield" in result.response


def test_booking_agent_preemption_on_new_escape_room_inquiry(tmp_path: Path) -> None:
    import main as main_module
    from src.agents.inbound_agent import InboundAgent
    from src.agents.booking_agent import BookingAgent
    from src.knowledge.knowledge_loader import KnowledgeLoader

    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = ConversationMemory(tmp_path / "session.json")

    # Start in booking flow with existing details
    memory.data.update({
        "intent": "corporate_event",
        "location": "Whitefield",
        "participants": 10,
        "preferred_date": "18 June",
        "customer_name": "Siddharth",
        "phone": "9876543210",
        "food_required": True,
        "budget_range": "premium",
        "current_workflow": "booking",
    })
    memory.save()

    inbound = InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_ollama=False,
    )
    booking = BookingAgent(memory)
    booking._state = booking._STATE_WAITING_FOR_SLOT

    # New escape room inquiry
    result, booking_inst, active = main_module.dispatch(
        "We're 7 friends and none of us have done an escape room before.", inbound, booking, "booking_agent"
    )

    assert active == "inbound_agent", "Should preempt to inbound agent"
    assert memory.data["intent"] == "escape_room_inquiry"
    # Qualification memory should be cleared for old fields
    assert memory.data["location"] == ""
    assert memory.data["food_required"] == ""
    # But new fields extracted
    assert memory.data["participants"] == 7
    assert memory.data["experience_level"] == "beginner"
    # Contact details preserved
    assert memory.data["customer_name"] == "Siddharth"
    assert memory.data["phone"] == "9876543210"


def test_booking_agent_preemption_on_corporate_event(tmp_path: Path) -> None:
    import main as main_module
    from src.agents.inbound_agent import InboundAgent
    from src.agents.booking_agent import BookingAgent
    from src.knowledge.knowledge_loader import KnowledgeLoader

    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = ConversationMemory(tmp_path / "session.json")

    # Start in booking flow with existing details
    memory.data.update({
        "intent": "escape_room_inquiry",
        "location": "Whitefield",
        "participants": 4,
        "age_group": "kids",
        "customer_name": "Siddharth",
        "phone": "9876543210",
        "current_workflow": "booking",
    })
    memory.save()

    inbound = InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_ollama=False,
    )
    booking = BookingAgent(memory)
    booking._state = booking._STATE_WAITING_FOR_SLOT

    # New corporate event
    result, booking_inst, active = main_module.dispatch(
        "I want a corporate event for 15 employees.", inbound, booking, "booking_agent"
    )

    assert active == "inbound_agent"
    assert memory.data["intent"] == "corporate_event"
    # Old location and age_group cleared
    assert memory.data["location"] == ""
    assert memory.data["age_group"] == ""
    # New corporate fields extracted
    assert memory.data["company_size"] == 15
    assert memory.data["participants"] == 15
    # Contact preserved
    assert memory.data["customer_name"] == "Siddharth"
    assert memory.data["phone"] == "9876543210"


def test_booking_agent_preemption_preserves_cancellation(tmp_path: Path) -> None:
    import main as main_module
    from src.agents.inbound_agent import InboundAgent
    from src.agents.booking_agent import BookingAgent
    from src.knowledge.knowledge_loader import KnowledgeLoader

    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = ConversationMemory(tmp_path / "session.json")

    # Start in booking flow
    memory.data.update({
        "intent": "corporate_event",
        "location": "Whitefield",
        "participants": 10,
        "customer_name": "Siddharth",
        "phone": "9876543210",
        "current_workflow": "booking",
    })
    memory.save()

    inbound = InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_ollama=False,
    )
    booking = BookingAgent(memory)
    booking._state = booking._STATE_WAITING_FOR_SLOT

    # Cancel this booking
    result, booking_inst, active = main_module.dispatch(
        "Cancel this booking.", inbound, booking, "booking_agent"
    )

    # Should stay on booking agent (since cancellation is handled by booking agent)
    assert active == "booking_agent"


def test_live_scenarios_from_user_request(tmp_path: Path) -> None:
    """
    Verifies the user's exact live scenario sequence:
      1. Adults and two kids visiting Whitefield. (routes to inbound)
      2. Which rooms are available? (FAQ -> routes to inbound)
      3. We're 7 friends and none of us have done an escape room before. (new inquiry -> clears and routes to inbound)
      4. I want an event for 15 employees. (new inquiry -> clears and routes to inbound)
    """
    import main as main_module
    from src.agents.inbound_agent import InboundAgent
    from src.agents.booking_agent import BookingAgent
    from src.knowledge.knowledge_loader import KnowledgeLoader

    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = ConversationMemory(tmp_path / "session.json")

    inbound = InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_ollama=False,
    )
    booking = None
    active_agent = "inbound_agent"

    # Turn 1: Adults and two kids visiting Whitefield.
    result, booking, active_agent = main_module.dispatch(
        "Adults and two kids visiting Whitefield.", inbound, booking, active_agent
    )
    assert active_agent == "inbound_agent"
    assert memory.data["location"] == "Whitefield"
    assert memory.data["age_group"] == "kids"

    # Set mock contact details to test preservation
    memory.data["customer_name"] = "Siddharth"
    memory.data["phone"] = "9876543210"
    memory.save()

    # Turn 2: Which rooms are available?
    result, booking, active_agent = main_module.dispatch(
        "Which rooms are available?", inbound, booking, active_agent
    )
    assert active_agent == "inbound_agent", "FAQ remains with inbound_agent"
    assert "Breakout has" in result.response or "Murder Mystery" in result.response

    # Turn 3: We're 7 friends and none of us have done an escape room before.
    result, booking, active_agent = main_module.dispatch(
        "We're 7 friends and none of us have done an escape room before.", inbound, booking, active_agent
    )
    assert active_agent == "inbound_agent", "Should preempt to inbound_agent for new inquiry"
    # Same-session participant/experience updates preserve the known branch unless
    # the customer explicitly changes it.
    assert memory.data["location"] == "Whitefield"
    # New participants and experience level should be set
    assert memory.data["participants"] == 7
    assert memory.data["experience_level"] == "beginner"
    # Contact details preserved
    assert memory.data["customer_name"] == "Siddharth"
    assert memory.data["phone"] == "9876543210"

    # Turn 4: I want an event for 15 employees.
    result, booking, active_agent = main_module.dispatch(
        "I want an event for 15 employees.", inbound, booking, active_agent
    )
    assert active_agent == "inbound_agent", "Should route to inbound for corporate event"
    # Old experience level cleared
    assert memory.data["experience_level"] == ""
    # New company size and participants set
    assert memory.data["company_size"] == 15
    assert memory.data["participants"] == 15
    # Contact preserved
    assert memory.data["customer_name"] == "Siddharth"
    assert memory.data["phone"] == "9876543210"
def test_production_voice_stabilization_blockers(tmp_path: Path) -> None:
    """Verifies all Phase 8 stabilization requirements."""
    import main as main_module
    from src.agents.inbound_agent import InboundAgent
    from src.agents.booking_agent import BookingAgent
    from src.knowledge.knowledge_loader import KnowledgeLoader

    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = ConversationMemory(tmp_path / "session.json")

    inbound = InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_ollama=False,
    )

    # 1. Age Group Capture
    # Test "above 18" -> adults
    inbound.handle_message("above 18")
    assert memory.data["age_group"] == "adults"

    # Test "above 20" -> adults
    memory.reset()
    inbound.handle_message("above 20")
    assert memory.data["age_group"] == "adults"

    # Test "18 plus" -> adults
    memory.reset()
    inbound.handle_message("18 plus")
    assert memory.data["age_group"] == "adults"

    # Test "all adults" -> adults
    memory.reset()
    inbound.handle_message("all adults")
    assert memory.data["age_group"] == "adults"

    # 2. Booking Entry Conditions
    # Just specifying Whitefield should NOT hand off to booking agent yet
    memory.reset()
    res1, _, active_agent = main_module.dispatch("visiting Whitefield.", inbound, None, "inbound_agent")
    assert active_agent == "inbound_agent"
    assert not res1.should_handoff

    # 3. Booking State Persistence
    # When active agent is booking agent, typo "Dupier" stays with booking agent
    booking = BookingAgent(memory)
    # Mocking slots available
    booking._state = booking._STATE_WAITING_FOR_SLOT
    booking._available_slots = ["10:00 AM", "3:00 PM"]

    res_booking, _, active_agent = main_module.dispatch("Dupier", inbound, booking, "booking_agent")
    assert active_agent == "booking_agent"
    assert "sorry" in res_booking.response.lower() or "choose" in res_booking.response.lower() or "available" in res_booking.response.lower()


def test_stabilization_voice_fix_regression(tmp_path: Path) -> None:
    """Regression test for repeated qualification questions & qualification priority."""
    agent = make_agent(tmp_path)

    # Step 1: User says: "We are a group of seven friends."
    # This should set participants = 7
    res = agent.handle_message("We are a group of seven friends.")
    assert agent.memory.data["participants"] == 7
    assert "escape room before" in res["response"].lower() or "first one" in res["response"].lower()
    assert agent.qualification_agent._waiting_for == "location"

    # Step 2: User provides location before experience; experience is still collected before recommendation.
    res2 = agent.handle_message("Whitefield")
    assert agent.memory.data["location"] == "Whitefield"
    assert "escape room before" in res2["response"].lower() or "first one" in res2["response"].lower()


def test_corporate_phrase_leakage_prevention(tmp_path: Path) -> None:
    """Issue 3: Ensure robotic resume phrases do not leak into customer-facing replies."""
    agent_corp = make_agent(tmp_path / "corp")
    agent_corp.handle_message("We need an event for 15 employees.")
    r_corp = agent_corp.handle_message("Do we need prior experience?")["response"]
    assert "to finalize the event details" not in r_corp.lower()
    assert "coming back to the event planning" not in r_corp.lower()
    assert "location" in r_corp.lower() or "koramangala" in r_corp.lower()

    agent_esc = make_agent(tmp_path / "esc")
    agent_esc.handle_message("We are a group of 7 friends.")
    r_esc = agent_esc.handle_message("Do we need prior experience?")["response"]
    assert "to finalize the details" not in r_esc.lower()
    assert "coming back to your visit" not in r_esc.lower()
    assert "location" in r_esc.lower() or "koramangala" in r_esc.lower() or "whitefield" in r_esc.lower()


def test_name_lookup_uses_conversation_memory(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    assert agent.handle_message("my name?").response == "I don't have your name yet."

    agent.memory.data["customer_name"] = "Surabhi"
    agent.memory.save()
    assert agent.handle_message("my name?").response == "You're booked under Surabhi."


def test_contextual_best_resolves_against_last_room_topic(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We've never done an escape room before.")

    response = agent.handle_message("Which is best?")["response"]

    assert "Murder Mystery" in response
    assert "Hostage" in response
    assert "escape-room feel" in response.lower() or "investigation" in response.lower()
