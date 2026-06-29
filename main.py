from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path

from src.core.agent_response import AgentResponse
from src.agents.booking_agent import BookingAgent
from src.agents.conversation_intelligence_agent import ConversationIntelligenceAgent
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
from src.services.conversation_guard import ConversationGuard
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
    _repair_repeated_question(message, inbound, result)
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
                result.response = "I'll connect you with our team and pass along the details already shared."
        result.state = inbound.memory.as_state()
    except Exception:
        result.escalation = {"escalate": False, "reason": "", "summary": ""}
        result.debug["escalation"] = result.escalation
    try:
        intelligence = ConversationIntelligenceAgent().analyze(
            inbound.memory.data,
            result.escalation,
        )
        inbound.memory.data["conversation_intelligence"] = intelligence
        inbound.memory.save()
        result.call_intelligence = intelligence
        result.ai_summary = intelligence.get("ai_summary", {})
        result.customer_profile = intelligence.get("customer_profile", {})
        result.timeline_events = intelligence.get("timeline_events", [])
        result.follow_up_recommendations = intelligence.get("follow_up_recommendations", [])
        result.transcript = intelligence.get("transcript", [])
        result.recording = intelligence.get("recording", {})
        result.sentiment_analysis = intelligence.get("sentiment_analysis", result.sentiment_analysis)
        result.debug["conversation_intelligence"] = {
            "timeline_events": len(result.timeline_events),
            "follow_up_recommendations": len(result.follow_up_recommendations),
        }
        result.state = inbound.memory.as_state()
    except Exception:
        result.call_intelligence = {}
    return result


def _repair_repeated_question(message: str, inbound: InboundAgent, result: AgentResponse) -> None:
    current = re.sub(r"\s+", " ", str(result.response or "")).strip()
    if not current.endswith("?"):
        return
    agent_turns = [
        turn
        for turn in inbound.memory.data.get("conversation", [])
        if turn.get("role") == "agent" and turn.get("content")
    ]
    if len(agent_turns) < 2:
        return
    previous = re.sub(r"\s+", " ", str(agent_turns[-2].get("content", ""))).strip()
    current_core = _question_core(current)
    if not current_core:
        return
    if _has_substantive_answer_before_question(current):
        return
    previous_core = _question_core(previous)
    recent_core_repeated = bool(
        current_core
        and any(
            _question_core(str(turn.get("content", ""))) == current_core
            for turn in agent_turns[-5:-1]
        )
    )
    if previous.lower() != current.lower() and not recent_core_repeated:
        return

    lowered = message.lower()
    if current_core == "location":
        if re.search(r"\b(?:first\s+time|first-time|beginner|never\s+done)\b", lowered):
            repaired = "Got it, first time. I'll keep the recommendation beginner-friendly; share the branch when you're ready."
        elif re.search(r"\b(?:returning|played\s+before|already\s+played|been\s+there|third\s+time|second\s+time)\b", lowered):
            if not inbound.memory.data.get("participants"):
                repaired = "Welcome back. Share the group size when you're ready."
            else:
                repaired = "Welcome back. Share the branch when you're ready."
        elif re.search(r"\b(?:adult|adults|kids|children|family|couple|people|players)\b", lowered):
            repaired = "Got it. Share the branch when you're ready." if recent_core_repeated else "Got it. Which branch works best: Whitefield, Koramangala, or JP Nagar?"
        else:
            repaired = "Which branch works best: Whitefield, Koramangala, or JP Nagar?"
    else:
        repaired = f"Got it. {current}"

    result.response = repaired
    agent_turns[-1]["content"] = repaired
    inbound.memory.save()


def _has_substantive_answer_before_question(response: str) -> bool:
    lowered = response.lower()
    if lowered.startswith(("i've noted", "got it. i've noted")):
        return False
    substantive_markers = (
        "food options", "rooms", "murder mystery", "hostage", "locked",
        "50 minutes", "experience", "cancellation", "refund", "parking",
        "available room", "escape room", "detail to hand", "call you back",
    )
    return any(marker in lowered for marker in substantive_markers)


def _question_core(response: str) -> str:
    lowered = response.lower()
    if "which location" in lowered or "which branch" in lowered or "location would you prefer" in lowered:
        return "location"
    if "how many" in lowered or "players are joining" in lowered or "people are joining" in lowered:
        return "participants"
    if "what date" in lowered:
        return "date"
    if "which time" in lowered or "time works" in lowered:
        return "slot"
    if "phone" in lowered:
        return "phone"
    if "name" in lowered:
        return "name"
    return ""


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
        message = inbound.memory.normalize_entity_aliases(message)
        if _is_additional_booking_request(message) and (
            inbound.memory.data.get("completed_booking")
            or inbound.memory.data.get("booking_id")
            or inbound.memory.data.get("payment_link")
            or inbound.memory.data.get("booking_started")
        ):
            _start_additional_booking(inbound)
            booking = None
            active_agent = "inbound_agent"
        sentiment = _observe_customer_sentiment(message, inbound)
        demo_mode = os.environ.get("DEMO_MODE", "false").lower() == "true"
        guard_result = None
        if not _is_state_changing_booking_turn(message, inbound.memory):
            guard_result = ConversationGuard(inbound.memory).evaluate(message, active_agent)
        if guard_result is not None:
            guard_result = _enrich_conversation_result(message, inbound, guard_result, sentiment)
            return guard_result, booking, guard_result.next_agent
        manager = ConversationManager(inbound.memory)
        target_agent, category = manager.determine_routing(message, active_agent)
        if (
            demo_mode
            and target_agent == "booking_agent"
            and active_agent != "booking_agent"
            and not inbound.memory.handoff_ready(str(inbound.memory.data.get("intent", "escape_room_inquiry")))
        ):
            target_agent = "inbound_agent"
            category = "continuing_workflow"
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
            if (
                booking_intent == "escape_room_inquiry"
                and not inbound.memory.data.get("room")
                and inbound.memory.data.get("recommended_option")
            ):
                recommended = str(inbound.memory.data.get("recommended_option") or "").strip()
                room_names = [
                    room
                    for room in inbound.recommender.available_options(inbound.memory.data, limit=10)
                    if room.lower() in recommended.lower()
                ]
                inbound.memory.set_field(
                    "room",
                    room_names[0] if room_names else recommended,
                    message,
                    expected_field="room",
                )
                inbound.memory.data["recommended_option"] = inbound.memory.data.get("room")
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
        # General FAQ is the normal entry state. Moving from it into the first
        # booking flow must retain facts already volunteered during intake.
        is_actual_topic_switch = bool(
            requested_intent
            and current_intent
            and current_intent != "general_faq"
            and requested_intent != current_intent
        )
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
            continuity = {
                "customer_name": inbound.memory.data.get("customer_name", ""),
                "first_name": inbound.memory.data.get("first_name", ""),
                "last_name": inbound.memory.data.get("last_name", ""),
                "phone": inbound.memory.data.get("phone", ""),
                "email": inbound.memory.data.get("email", ""),
                "age_group": inbound.memory.data.get("age_group", ""),
                "experience_level": inbound.memory.data.get("experience_level", ""),
            }
            fields_to_clear = [
                "location", "participants", "participants_min", "participants_max", "relationship", "age_group", "experience_level",
                "company_size", "event_type", "preferred_date", "food_required",
                "preferred_time", "preferred_period", "budget_range", "intent", "recommended_option", "room",
                "selected_slot", "booking_id", "booking_ref", "booking_order_id",
                "payment_link", "whatsapp_payload", "completed_booking", "booking_started",
            ]
            for field in fields_to_clear:
                inbound.memory.data[field] = ""
            inbound.memory.data["discussed_options"] = []
            inbound.memory.data["customer_preferences"] = []
            inbound.memory.data["concerns"] = []
            inbound.memory.data["current_workflow"] = "general"
            inbound.memory.data["booking_consent_pending"] = False
            if inbound.memory.data.get("previous_bookings"):
                inbound.memory.data.update({key: value for key, value in continuity.items() if value})
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


def _is_additional_booking_request(message: str) -> bool:
    lowered = message.lower()
    return bool(
        re.search(
            r"\b(?:another|second|new|also)\s+(?:booking|room|game|slot|reservation)\b|"
            r"\bi\s+also\s+want\s+(?:to\s+book|another)|\bbook\s+another\b",
            lowered,
        )
    )


def _is_state_changing_booking_turn(message: str, memory: ConversationMemory) -> bool:
    lowered = message.lower()
    if _is_additional_booking_request(message):
        return True
    has_change_word = bool(
        re.search(r"\b(?:actually|change|switch|instead|make it|update|modify|reschedule|use)\b", lowered)
    )
    if not has_change_word:
        return False
    normalized = memory.normalize_number_words(memory.normalize_entity_aliases(message)).lower()
    return bool(
        memory._extract_room(normalized)
        or memory._extract_location(normalized)
        or memory._extract_preferred_date(message)
        or memory._extract_time(message)
        or memory._extract_participants(normalized)
        or memory._extract_participant_range(normalized)
    )


def _start_additional_booking(inbound: InboundAgent) -> None:
    memory = inbound.memory
    previous = {
        field: memory.data.get(field, "")
        for field in (
            "booking_id", "booking_ref", "booking_order_id", "location", "room",
            "preferred_date", "selected_slot", "participants", "customer_name", "phone",
            "payment_link",
        )
        if memory.data.get(field)
    }
    if previous:
        memory.data.setdefault("previous_bookings", [])
        if isinstance(memory.data["previous_bookings"], list):
            memory.data["previous_bookings"].append(previous)
            memory.data["previous_bookings"] = memory.data["previous_bookings"][-5:]

    preserve = {
        "customer_name": memory.data.get("customer_name", ""),
        "first_name": memory.data.get("first_name", ""),
        "last_name": memory.data.get("last_name", ""),
        "phone": memory.data.get("phone", ""),
        "email": memory.data.get("email", ""),
        "age_group": memory.data.get("age_group", ""),
        "experience_level": memory.data.get("experience_level", ""),
    }
    for field in [
        "location", "participants", "participants_min", "participants_max", "relationship",
        "age_group", "age_detail", "experience_level", "challenge_preference",
        "company_size", "event_type", "preferred_date", "preferred_time", "preferred_period",
        "food_required", "budget_range", "room", "intent", "recommended_option",
        "selected_slot", "booking_id", "booking_ref", "booking_order_id",
        "payment_link", "whatsapp_payload",
        "completed_booking", "booking_started", "booking_consent_pending",
    ]:
        memory.data[field] = "" if field not in {"booking_consent_pending", "completed_booking", "booking_started"} else False
    memory.data.update(preserve)
    memory.data["current_workflow"] = "general"
    memory.data["discussed_options"] = []
    memory.data["customer_preferences"] = []
    memory.data["concerns"] = []
    inbound.qualification_agent._waiting_for = ""
    memory.save()




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
