from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from src.agent_response import AgentResponse
from src.booking_agent import BookingAgent
from src.conversation_memory import ConversationMemory
from src.inbound_agent import InboundAgent
from src.knowledge_loader import KnowledgeLoader
from src.transcript_logger import TranscriptLogger
from src.voice_input import VoiceInput
from src.voice_output import VoiceOutput
from src.conversation_manager import ConversationManager


BASE_DIR = Path(__file__).resolve().parent
EXIT_COMMANDS = {"quit", "exit", "goodbye", "bye", "stop"}
POST_TTS_COOLDOWN_SECONDS = 0.75


def is_exit_command(message: str) -> bool:
    return message.lower().strip().rstrip(".!?") in EXIT_COMMANDS


def should_process_transcript(message: str) -> bool:
    return bool(message.strip())


def speak_then_resume_listening(voice_out: VoiceOutput, text: str, debug: bool = False) -> None:
    try:
        voice_out.speak(text)
    except Exception as exc:
        if debug:
            print(f"TTS ERROR: {exc}")
    time.sleep(POST_TTS_COOLDOWN_SECONDS)


def build_inbound_agent(args: argparse.Namespace, memory: ConversationMemory) -> InboundAgent:
    knowledge = KnowledgeLoader(BASE_DIR / "knowledge").load()
    return InboundAgent(
        knowledge_base=knowledge,
        memory=memory,
        prompt_path=BASE_DIR / "prompts" / "inbound_prompt.txt",
        model=args.model,
        use_openai=not args.no_openai,
    )


def build_agent(args: argparse.Namespace) -> InboundAgent:
    """
    Compatibility shim used by tests that monkeypatch `main_module.build_agent`.
    Also used by run_text_loop / run_voice_loop so the monkeypatch intercepts
    correctly in test mode.
    """
    memory = ConversationMemory(BASE_DIR / "memory" / "session.json")
    return build_inbound_agent(args, memory)


def print_result(result: AgentResponse, show_debug: bool) -> None:
    print(f"\nAgent: {result.response}")
    if show_debug:
        debug = result.get("debug", {})
        print("\n--- Debug ---")
        print(f"Intent: {result.intent} ({result.intent_confidence:.2f})")
        print(f"Recommendation: {result.recommendation.get('option', '')}")
        print(f"Missing fields: {', '.join(result.missing_fields) or 'none'}")
        print(f"Route: {result.next_agent} | handoff={result.should_handoff}")
        print(f"Previous state: {json.dumps(debug.get('previous_state', {}), ensure_ascii=False)}")
        print(f"Extracted entities: {json.dumps(debug.get('extracted_entities', {}), ensure_ascii=False)}")
        print(f"Updated state: {json.dumps(debug.get('updated_state', {}), ensure_ascii=False)}")
        print(f"Missing slots: {', '.join(debug.get('missing_slots', [])) or 'none'}")
        if result.booking_result:
            print(f"Booking: {json.dumps(result.booking_result, indent=2)}")
        elif result.booking:
            print(f"Booking: {json.dumps(result.booking, indent=2)}")
        else:
            print(f"Handoff summary: {result.handoff_summary}")


# ------------------------------------------------------------------ #
# Single dispatcher — routes turns to the right agent each loop      #
# ------------------------------------------------------------------ #

def dispatch(
    message: str,
    inbound: InboundAgent,
    booking: BookingAgent | None,
    active_agent: str,
) -> tuple[AgentResponse, BookingAgent | None, str]:
    """
    Route a message to the correct agent and return:
        (response, booking_agent_instance, active_agent_name)

    The booking_agent instance is created lazily on first handoff.
    Once created it persists for the session (owns the conversation).
    """
    manager = ConversationManager(inbound.memory)
    target_agent, category = manager.determine_routing(message, active_agent)
    preempted_active_booking = active_agent == "booking_agent" and target_agent == "inbound_agent"

    # 1. Handle preemption from booking_agent to inbound_agent (e.g., FAQ, recommendation, new inquiry)
    if preempted_active_booking:
        active_agent = "inbound_agent"
        inbound.memory.data["current_workflow"] = "general"
        inbound.memory.data["booking_consent_pending"] = False
        inbound.qualification_agent._waiting_for = ""
        if booking is not None:
            # Reset booking agent internal state so it restarts fresh next time
            booking._state = booking._STATE_CHECKING_AVAILABILITY
            booking._available_slots = []
            booking._last_availability = {}
            booking._booking_result = None
        inbound.memory.save()

    # 2. Handle new inquiry topic switch (reset qualification memory)
    category_intents = {
        "new_corporate": "corporate_event",
        "new_birthday": "birthday_party",
        "new_escape_room": "escape_room_inquiry",
    }
    current_intent = str(inbound.memory.data.get("intent", ""))
    requested_intent = category_intents.get(category, "")
    if category == "new_booking":
        detected = manager.intent_detector.detect(message, previous_intent="").intent
        if detected in {"bachelor_party", "farewell_party", "couple_event", "virtual_event"}:
            requested_intent = detected
    is_actual_topic_switch = bool(requested_intent and current_intent and requested_intent != current_intent)
    extracted_participants = inbound.memory._extract_participants(
        inbound.memory.normalize_number_words(message).lower()
    )
    stored_participants = inbound.memory.data.get("participants")
    is_replacement_group = bool(
        requested_intent
        and requested_intent == current_intent
        and extracted_participants
        and stored_participants
        and extracted_participants != stored_participants
    )

    if is_actual_topic_switch or is_replacement_group:
        fields_to_clear = [
            "location", "participants", "age_group", "experience_level",
            "company_size", "event_type", "preferred_date", "food_required",
            "budget_range", "intent", "recommended_option"
        ]
        for field in fields_to_clear:
            inbound.memory.data[field] = ""
        inbound.memory.data["discussed_options"] = []
        inbound.memory.data["customer_preferences"] = []
        inbound.memory.data["concerns"] = []
        inbound.memory.data["current_workflow"] = "general"
        inbound.memory.data["booking_consent_pending"] = False
        inbound.qualification_agent._waiting_for = ""
        inbound.memory.save()

        # Ensure we route to inbound_agent for the new qualification
        active_agent = "inbound_agent"
        if booking is not None:
            booking._state = booking._STATE_CHECKING_AVAILABILITY
            booking._available_slots = []
            booking._last_availability = {}
            booking._booking_result = None

    if active_agent == "booking_agent" and booking is not None:
        result = booking.handle_message(message)
        return result, booking, "booking_agent"

    # InboundAgent handles this turn
    result = inbound.handle_message(message)

    # A practical question or recommendation that interrupted booking must be
    # answered without immediately bouncing the same turn back into booking.
    if preempted_active_booking and category in {"faq", "recommendation"}:
        result.should_handoff = False
        result.next_agent = "inbound_agent"
        return result, booking, "inbound_agent"

    # Handoff decision — any qualified intent routes to BookingAgent.
    # The Router may name the target "corporate_events_agent", "birthday_booking_agent" etc.
    # (those are future specialist agents). For now, BookingAgent is the universal
    # post-qualification stage that checks availability and confirms the booking.
    if result.should_handoff:
        if booking is None:
            booking = BookingAgent(inbound.memory)
        # First BookingAgent turn: run availability check immediately.
        # The sentinel "ready" is ignored by BookingAgent — it reads location/date from memory.
        booking_result = booking.handle_message("ready")
        return booking_result, booking, "booking_agent"

    return result, booking, "inbound_agent"



# ------------------------------------------------------------------ #
# Text loop                                                           #
# ------------------------------------------------------------------ #

def run_text_loop(args: argparse.Namespace) -> None:
    inbound = build_agent(args)          # patchable by tests
    memory = inbound.memory
    if getattr(args, "reset_memory", False):
        memory.reset()
    voice = VoiceOutput(enabled=args.speak)
    logger = TranscriptLogger(BASE_DIR / "logs" / "conversations")

    booking_agent: BookingAgent | None = None
    active_agent = "inbound_agent"

    print("Breakout Inbound Agent is running locally.")
    print("Type a customer message. Commands: /handoff, /show_memory, /reset, /quit")

    while True:
        try:
            message = input("\nCustomer: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not message:
            continue
        if message == "/quit":
            print("Goodbye.")
            break
        if message == "/reset":
            memory.reset()
            booking_agent = None
            active_agent = "inbound_agent"
            print("Session memory reset.")
            continue
        if message == "/handoff":
            print(inbound.handoff_generator.to_json(memory.data))
            continue
        if message == "/show_memory":
            print(json.dumps(memory.data, indent=2, ensure_ascii=False))
            continue

        logger.start_turn()
        result, booking_agent, active_agent = dispatch(
            message, inbound, booking_agent, active_agent
        )
        logger.log_turn(message, result.__dict__, memory.data, active_agent=active_agent)
        print_result(result, args.debug)
        voice.speak(result.response)


# ------------------------------------------------------------------ #
# Voice loop                                                          #
# ------------------------------------------------------------------ #

def run_voice_loop(args: argparse.Namespace) -> None:
    inbound = build_agent(args)          # patchable by tests
    memory = inbound.memory
    memory.reset()

    print("Loading Whisper voice model...")
    voice_in = VoiceInput(model_name=args.whisper_model, language="en", debug=args.debug)
    # Warm up / pre-load Whisper model to avoid lag on first turn
    try:
        import whisper
        voice_in._model = whisper.load_model(args.whisper_model)
        print("Whisper voice model loaded.")
    except Exception as e:
        print(f"Warning: could not pre-load Whisper: {e}")

    voice_out = VoiceOutput(enabled=True, debug=args.debug)
    logger = TranscriptLogger(BASE_DIR / "logs" / "conversations")

    booking_agent: BookingAgent | None = None
    active_agent = "inbound_agent"

    print("Voice mode is running. Press Ctrl+C to stop.")
    print("Speak after the recording prompt. Say 'quit' or 'goodbye' to stop.")
    greeting = "Hello, thank you for calling Breakout Escape Rooms. How may I help you today?"
    print(f"\nAgent: {greeting}")
    speak_then_resume_listening(voice_out, greeting, debug=args.debug)

    while True:
        try:
            message = voice_in.record_and_transcribe(seconds=args.record_seconds)
        except KeyboardInterrupt:
            print("\nGoodbye.")
            break

        if args.debug and message:
            print(f"WHISPER TRANSCRIPT: {message}")

        if not should_process_transcript(message):
            if voice_in.last_status == "unclear":
                print("\nAgent: Sorry, I didn't catch that. Could you repeat it?")
                speak_then_resume_listening(
                    voice_out,
                    "Sorry, I didn't catch that. Could you repeat it?",
                    debug=args.debug,
                )
            continue

        print(f"\nCustomer: {message}")

        if message == "/show_memory":
            print(json.dumps(memory.data, indent=2, ensure_ascii=False))
            continue

        if is_exit_command(message):
            farewell = "Thank you for contacting Breakout. Have a great day."
            print(farewell)
            speak_then_resume_listening(voice_out, farewell, debug=args.debug)
            break

        logger.start_turn()
        result, booking_agent, active_agent = dispatch(
            message, inbound, booking_agent, active_agent
        )
        logger.log_turn(message, result.__dict__, memory.data, active_agent=active_agent)
        print_result(result, args.debug)
        speak_then_resume_listening(voice_out, result.response, debug=args.debug)


# ------------------------------------------------------------------ #
# CLI                                                                 #
# ------------------------------------------------------------------ #

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Breakout Escape Rooms OpenAI inbound agent")
    parser.add_argument("--model", help="OpenAI model name (default: OPENAI_MODEL or gpt-4.1-mini)")
    parser.add_argument("--no-openai", action="store_true", help="Use deterministic fallback responses only")
    parser.add_argument("--reset-memory", action="store_true", help="Clear memory/session.json at startup")
    parser.add_argument("--debug", action="store_true", help="Print intent, route, and handoff details")
    parser.add_argument("--speak", action="store_true", help="Enable pyttsx3 text-to-speech in text mode")
    parser.add_argument("--voice", action="store_true", help="Enable Whisper microphone input and pyttsx3 output")
    parser.add_argument("--whisper-model", default="small", help="Whisper model for voice mode")
    parser.add_argument("--record-seconds", type=int, default=12, help="Seconds to record per voice turn")
    return parser.parse_args()


if __name__ == "__main__":
    parsed_args = parse_args()
    if parsed_args.voice:
        run_voice_loop(parsed_args)
    else:
        run_text_loop(parsed_args)
