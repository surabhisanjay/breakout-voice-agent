from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

from src.core.agent_response import AgentResponse
from src.agents.booking_agent import BookingAgent
from src.agents.escalation_agent import EscalationAgent
from src.agents.handoff_summary_agent import HandoffSummaryAgent
from src.memory.conversation_memory import ConversationMemory
from src.agents.inbound_agent import InboundAgent
from src.agents.sentiment_agent import SentimentAgent, SentimentResult
from src.knowledge.knowledge_loader import KnowledgeLoader
from src.logger.transcript_logger import TranscriptLogger
from src.voice.stt.voice_input import VoiceInput
from src.voice.tts.voice_output import VoiceOutput
from src.orchestration.conversation_manager import ConversationManager
from src.config.env_loader import booking_provider_label, load_project_env


BASE_DIR = Path(__file__).resolve().parent
load_project_env(BASE_DIR)
logging.basicConfig(
    level=os.environ.get("BREAKOUT_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
EXIT_COMMANDS = {"quit", "exit", "goodbye", "bye", "stop"}
POST_TTS_COOLDOWN_SECONDS = 0.75
logger = logging.getLogger(__name__)


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
    if not show_debug:
        return

    state = result.state or {}
    core_keys = [
        "customer_name", "phone", "location", "participants", "age_group",
        "experience_level", "event_type", "preferred_date", "food_required", "budget_range"
    ]
    filled = [k for k in core_keys if state.get(k)]
    missing = result.missing_fields or []
    next_expected = missing[0] if missing else "none"
    
    print("\n--- Memory Validation ---")
    print(f"filled_fields: {filled}")
    print(f"missing_fields: {missing}")
    print(f"next_expected_field: {next_expected}")
    print(f"Verified Routing: {result.next_agent} | handoff={result.should_handoff}")
    print("-------------------------\n")

    if show_debug:
        debug = result.get("debug", {})
        print("--- Full Debug ---")
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
        print("-------------------\n")


def run_startup_check(agent: InboundAgent) -> None:
    """Print a pre-demo startup validation banner."""
    import os
    import time

    demo_mode = os.environ.get("DEMO_MODE", "false").lower() == "true"
    if demo_mode:
        import logging
        logging.getLogger().setLevel(logging.ERROR)

    # ── env reads ──────────────────────────────────────────────────
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    openai_enabled = bool(openai_key)

    # Do not make network calls during startup. Runtime calls fail over to the
    # approved deterministic draft and disable OpenAI after the first failure.
    openai_quota_ok = openai_enabled and not demo_mode
    if not openai_enabled or demo_mode:
        agent.use_openai = False
        agent.response_composer.enabled = False

    # ── personality prompt & examples ─────────────────────────────
    personality_loaded = bool(
        getattr(agent.response_composer, "system_prompt", "")
    )
    examples_count = len(getattr(agent.response_composer, "few_shot_examples", []))

    # ── banner (Exactly four lines, nothing else) ─────────────────
    print(f"OPENAI_ENABLED={openai_enabled and openai_quota_ok}")
    print(f"BOOKING_PROVIDER={booking_provider_label()}")
    print(f"PERSONALITY_PROMPT_LOADED={personality_loaded}")
    print(f"TRANSCRIPT_EXAMPLES_LOADED={examples_count}")


def _observe_customer_sentiment(message: str, inbound: InboundAgent) -> SentimentResult:
    stage = str(inbound.memory.data.get("conversation_mode", "discovery"))
    try:
        return SentimentAgent(inbound.memory).analyze(message, stage=stage)
    except Exception:
        return SentimentResult(
            sentiment="neutral",
            confidence=0.0,
            escalation_recommended=False,
            reason="Sentiment observer unavailable.",
            stage=stage,
        )


def _enrich_conversation_result(
    message: str,
    inbound: InboundAgent,
    result: AgentResponse,
    sentiment: SentimentResult,
) -> AgentResponse:
    result.sentiment_analysis = sentiment.to_dict()
    result.debug = dict(result.debug or {})
    result.debug["sentiment_analysis"] = result.sentiment_analysis
    try:
        # Inbound extraction has a legacy one-turn classifier. Restore the richer
        # observer result without touching any booking or qualification field.
        inbound.memory.data["sentiment"] = sentiment.sentiment
        inbound.memory.data["sentiment_confidence"] = sentiment.confidence
        inbound.memory.data["sentiment_reason"] = sentiment.reason

        escalation = EscalationAgent(inbound.memory).evaluate(message, sentiment, result)
        result.escalation = escalation.to_dict()
        result.debug["escalation"] = result.escalation
        if escalation.escalate:
            try:
                result.handoff_summary = HandoffSummaryAgent().generate(
                    inbound.memory.data,
                    escalation.to_dict(),
                )
            except Exception:
                result.handoff_summary = {
                    "escalation_reason": escalation.reason,
                    "summary": escalation.summary or "Escalation requested; detailed summary unavailable.",
                }
            result.next_agent = "escalation_agent"
            result.should_handoff = True
            if "safety concern" in escalation.reason.lower():
                result.response = "Please alert on-site staff or emergency services immediately. I'm escalating this as urgent."
            elif "refund" in escalation.reason.lower():
                result.response = "I'll connect you with our team to review the refund request and the booking details."
            elif "human representative" in escalation.reason.lower():
                result.response = "Of course. I'll connect you with our team and pass along the details already shared."
        result.state = inbound.memory.as_state()
    except Exception:
        result.escalation = {"escalate": False, "reason": "", "summary": ""}
        result.debug["escalation"] = result.escalation
    return result


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
    try:
        import os
        sentiment = _observe_customer_sentiment(message, inbound)
        demo_mode = os.environ.get("DEMO_MODE", "false").lower() == "true"
        manager = ConversationManager(inbound.memory)
        target_agent, category = manager.determine_routing(message, active_agent)
        if target_agent == "booking_agent":
            current_intent = str(inbound.memory.data.get("intent", ""))
            detected_intent = manager.intent_detector.detect(message, previous_intent=current_intent).intent
            booking_intent = current_intent
            if booking_intent in ("", "general_faq"):
                booking_intent = (
                    detected_intent
                    if detected_intent not in ("", "general_faq")
                    else "escape_room_inquiry"
                )
            inbound.memory.merge_message(message, booking_intent)
            inbound.memory.data["booking_started"] = True
            inbound.memory.data["current_workflow"] = "booking"
            inbound.memory.data["booking_consent_pending"] = False
            inbound.memory.save()
            if booking is None:
                booking = BookingAgent(inbound.memory)
            logger.info("BOOKING_AGENT_SELECTED session_intent=%s category=%s", booking_intent, category)
            logger.info("BOOKING_STARTED=true")
        preempted_active_booking = active_agent == "booking_agent" and target_agent == "inbound_agent"

        # 1. Handle preemption from booking_agent to inbound_agent (e.g., FAQ, recommendation, new inquiry)
        if preempted_active_booking:
            active_agent = "inbound_agent"
            inbound.memory.data["current_workflow"] = "general"
            inbound.memory.data["booking_consent_pending"] = False
            inbound.qualification_agent._waiting_for = ""
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
                "location", "participants", "participants_min", "participants_max", "age_group", "experience_level",
                "company_size", "event_type", "preferred_date", "food_required",
                "budget_range", "intent", "recommended_option", "room",
                "selected_slot", "booking_id", "booking_ref", "booking_order_id",
                "completed_booking", "booking_started",
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

        if target_agent == "booking_agent" and booking is not None:
            active_agent = "booking_agent"

        if active_agent == "booking_agent" and booking is not None:
            result = booking.handle_message(message)
            result = _enrich_conversation_result(message, inbound, result, sentiment)
            return result, booking, "booking_agent"

        # InboundAgent handles this turn
        result = inbound.handle_message(message)

        # A practical question or recommendation that interrupted booking must be
        # answered without immediately bouncing the same turn back into booking.
        if preempted_active_booking and category in {"faq", "recommendation"}:
            result.should_handoff = False
            result.next_agent = "inbound_agent"
            result = _enrich_conversation_result(message, inbound, result, sentiment)
            return result, booking, "inbound_agent"

        # Demo safety: keep intake ownership until the existing handoff
        # contract has date and contact details, not only recommendation data.
        if demo_mode and result.should_handoff and not inbound.memory.handoff_ready(result.intent):
            result.should_handoff = False
            result.next_agent = "inbound_agent"

        # Handoff decision — any qualified intent routes to BookingAgent.
        # The Router may name the target "corporate_events_agent", "birthday_booking_agent" etc.
        # (those are future specialist agents). For now, BookingAgent is the universal
        # post-qualification stage that checks availability and confirms the booking.
        if result.should_handoff:
            if booking is None:
                booking = BookingAgent(inbound.memory)
            # Sync response composer state
            booking.response_composer.enabled = inbound.response_composer.enabled
            # First BookingAgent turn: run availability check immediately.
            # The sentinel "ready" is ignored by BookingAgent — it reads location/date from memory.
            booking_result = booking.handle_message("ready")
            booking_result = _enrich_conversation_result(message, inbound, booking_result, sentiment)
            return booking_result, booking, "booking_agent"

        result = _enrich_conversation_result(message, inbound, result, sentiment)
        return result, booking, "inbound_agent"

    except Exception as exc:
        # Prevent any crash, stack trace, or network exception output
        import os
        from src.core.agent_response import AgentResponse
        
        # Enforce fail safe mode
        inbound.use_openai = False
        inbound.response_composer.enabled = False
        if booking is not None:
            booking.orchestrator.is_live = False
            booking.response_composer.enabled = False

        # Build a safe friendly deterministic response
        fallback_text = "I've processed your request. Let me assist you with that. Could you please confirm your name?"
        fallback_result = AgentResponse(
            response=fallback_text,
            intent=str(inbound.memory.data.get("intent", "escape_room_inquiry")),
            next_agent="inbound_agent",
            should_handoff=False,
            state=inbound.memory.as_state(),
        )
        fallback_result = _enrich_conversation_result(message, inbound, fallback_result, sentiment)
        return fallback_result, booking, "inbound_agent"




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

    demo_mode = os.environ.get("DEMO_MODE", "false").lower() == "true"
    if not demo_mode:
        print("Breakout Inbound Agent is running locally.")
        print("Type a customer message. Commands: /handoff, /show_memory, /reset, /quit")
    run_startup_check(inbound)

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

    demo_mode = os.environ.get("DEMO_MODE", "false").lower() == "true"
    if not demo_mode:
        print("Loading Whisper voice model...")
    voice_in = VoiceInput(model_name=args.whisper_model, language="en", debug=args.debug)
    # Warm up / pre-load Whisper model to avoid lag on first turn
    try:
        import whisper
        voice_in._model = whisper.load_model(args.whisper_model)
        if not demo_mode:
            print("Whisper voice model loaded.")
    except Exception as e:
        if not demo_mode:
            print(f"Warning: could not pre-load Whisper: {e}")

    voice_out = VoiceOutput(enabled=True, debug=args.debug)
    logger = TranscriptLogger(BASE_DIR / "logs" / "conversations")

    booking_agent: BookingAgent | None = None
    active_agent = "inbound_agent"

    if not demo_mode:
        print("Voice mode is running. Press Ctrl+C to stop.")
        print("Speak after the recording prompt. Say 'quit' or 'goodbye' to stop.")
    run_startup_check(inbound)
    greeting = "Hello, thank you for calling Breakout Escape Rooms. How may I help you today?"
    if not demo_mode:
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
    if parsed_args.debug:
        os.environ["BREAKOUT_DEBUG"] = "true"
    if parsed_args.voice:
        run_voice_loop(parsed_args)
    else:
        run_text_loop(parsed_args)
