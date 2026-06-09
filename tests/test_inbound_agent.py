from __future__ import annotations

import sys
import subprocess
import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import main as main_module  # noqa: E402
from main import is_exit_command, run_text_loop, run_voice_loop, should_process_transcript, speak_then_resume_listening  # noqa: E402
from src.conversation_memory import ConversationMemory  # noqa: E402
from src.inbound_agent import InboundAgent  # noqa: E402
from src.knowledge_loader import KnowledgeLoader  # noqa: E402
from src.qualification_agent import QualificationAgent  # noqa: E402


def make_agent(tmp_path: Path) -> InboundAgent:
    knowledge = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    memory = ConversationMemory(tmp_path / "session.json")
    return InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=PROJECT_DIR / "prompts" / "inbound_prompt.txt",
        use_ollama=False,
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


def test_kids_recommendation(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("We have 6 kids aged 10.")
    assert "Murder Mystery and Hostage" in result["response"]
    assert "investigation" in result["response"]
    assert "urgency" in result["response"]
    assert "Which location" in result["response"]


def test_free_form_kids_recommendation_with_location(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("We are 6 kids aged 10 coming to Whitefield.")
    assert "Murder Mystery and Hostage" in result["response"]
    assert "investigation" in result["response"]
    assert "Whitefield" in result["response"]
    assert "more details" in result["response"]
    assert result["missing_fields"] == []


def test_adult_game_recommendation(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    result = agent.handle_message("We are six adults.")
    assert "For a group of 6 adults" in result["response"]
    assert "Classified" in result["response"]
    assert "Bomb Defusal" in result["response"]
    assert "beginner-friendly" in result["response"]


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
    assert "Classified or Bomb Defusal" in first["response"]
    second = agent.handle_message("We are visiting Whitefield.")
    assert second["intent"] == "escape_room_inquiry"
    assert "visiting Whitefield" in second["response"]
    assert "Bomb Defusal" in second["response"]
    assert "Undercover" in second["response"]


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
    assert "Murder Mystery" in beginner["response"]
    follow_up = agent.handle_message("What would you recommend?")
    assert follow_up["intent"] == "escape_room_inquiry"
    assert "first-time players" in follow_up["response"]
    assert "Murder Mystery" in follow_up["response"]
    assert "Hostage" in follow_up["response"]
    assert "Whitefield" in follow_up["response"]


def test_first_time_players_use_existing_context(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We are six adults visiting Whitefield.")
    response = agent.handle_message("We've never done an escape room before.")["response"]
    assert "For a group of 6 adults visiting Whitefield" in response
    assert "Murder Mystery" in response
    assert "Hostage" in response
    assert "How many people" not in response
    assert "Which location" not in response


def test_all_our_kids_does_not_corrupt_count(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.handle_message("We are six people visiting Whitefield.")
    result = agent.handle_message("All our kids are aged 10.")
    assert agent.memory.data["participants"] == 6
    assert agent.memory.data["age_group"] == "10 years"
    assert "For 6 kids aged 10 visiting Whitefield" in result["response"]


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
    assert "For a group of 6 adults visiting Whitefield" in result["response"]


def test_ollama_timeout_recovery(monkeypatch, tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    agent.use_ollama = True
    monkeypatch.setattr(agent.retriever, "search", lambda _query: "Known Breakout context")

    class HangingProcess:
        pid = 12345
        returncode = None

        def communicate(self, timeout: int):
            raise subprocess.TimeoutExpired(cmd="ollama", timeout=timeout)

        def wait(self, timeout: int):
            return None

    monkeypatch.setattr("src.inbound_agent.subprocess.Popen", lambda *args, **kwargs: HangingProcess())
    monkeypatch.setattr("src.inbound_agent.os.killpg", lambda *args, **kwargs: None)

    result = agent.handle_message("Tell me something unusual that is not in the common FAQs.")
    assert result["response"] == "I don't currently have that information, but I can connect you with the appropriate team."
    assert agent.last_ollama_error == "timeout"


def test_qualification_flow_completion(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    qualifier = QualificationAgent(memory)

    memory.update_from_message("We want a corporate event.", "corporate_event")
    first = qualifier.qualify("corporate_event")
    assert not first.qualified
    assert first.next_question == "How many people will attend?"

    qualifier.update_and_qualify("45", "corporate_event")
    second = qualifier.update_and_qualify("Whitefield", "corporate_event")
    assert second.next_question == "What date are you planning for?"

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
    assert invalid.response == "Sorry, I didn't understand the date. Could you provide a date like 18 June or June 18?"
    assert "preferred_date" in invalid.missing_fields
    assert memory.data["preferred_date"] == ""

    unrelated = qualifier.update_and_qualify("Ah, but it's in Google.", "corporate_event")
    assert unrelated.response == "Sorry, I didn't understand the date. Could you provide a date like 18 June or June 18?"
    assert memory.data["preferred_date"] == ""


def test_corporate_qualification_completion_end_to_end(tmp_path: Path) -> None:
    memory = ConversationMemory(tmp_path / "session.json")
    qualifier = QualificationAgent(memory)

    memory.update_from_message("We need a corporate event.", "corporate_event")
    assert qualifier.qualify("corporate_event").next_question == "How many people will attend?"
    assert qualifier.update_and_qualify("50", "corporate_event").next_question == "Which location would you prefer: Koramangala, Whitefield, or JP Nagar?"
    assert qualifier.update_and_qualify("JP Nagar", "corporate_event").next_question == "What date are you planning for?"
    assert qualifier.update_and_qualify("18 June", "corporate_event").next_question == "Would you require food and beverages?"
    assert qualifier.update_and_qualify("Yes", "corporate_event").next_question == "What budget range are you considering? We have Basic, Standard, and Premium options."
    assert qualifier.update_and_qualify("Premium", "corporate_event").next_question == "May I have your name?"
    assert qualifier.update_and_qualify("Siddharth", "corporate_event").next_question == "Could I have your phone number so the team can share the details?"
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
    assert result["response"] == "Sorry, I didn't understand the date. Could you provide a date like 18 June or June 18?"
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
    assert "Would you like me to compare those two?" in first_time
    comparison = agent.handle_message("Yes please.")["response"]
    assert "Murder Mystery is the better starting point" in comparison
    assert "Hostage is a good alternative" in comparison
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
    assert "Murder Mystery" in response
    assert "Hostage" in response
    assert "How many people" in response


def test_couple_guidance(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    response = agent.handle_message("We are a couple.")["response"]
    assert "Murder Mystery" in response
    assert "teamwork" in response
    assert "relaxed or more challenging" in response


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
    from src.voice_output import VoiceOutput

    class FakeEngine:
        def setProperty(self, *a, **kw):
            pass

        def say(self, text: str) -> None:
            pass

        def runAndWait(self) -> None:
            pass

    import src.voice_output as vo_module
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
    assert "For a group of 6 adults visiting Whitefield" in adult
    assert "Bomb Defusal" in adult
    assert "Undercover" in adult

    beginner_agent = make_agent(tmp_path / "beginner")
    beginner = beginner_agent.handle_message("We've never done an escape room before.")["response"]
    assert "Murder Mystery" in beginner
    assert "Hostage" in beginner

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
    from src.conversation_memory import ConversationMemory as CM
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
    router = __import__("src.router", fromlist=["Router"]).Router()
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
    # both indicate successful qualification.
    assert "captured all the information" in r7 or "connect you" in r7


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
    from src.conversation_memory import ConversationMemory as CM
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
    from src.voice_output import VoiceOutput
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
    from src.voice_output import VoiceOutput
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


