from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .agent_response import AgentResponse
from .conversation_memory import ConversationMemory
from .handoff_generator import HandoffGenerator
from .intent_detector import IntentDetector, IntentResult, _BOOKING_TRIGGERS
from .knowledge_loader import KnowledgeBase
from .knowledge_retriever import KnowledgeRetriever
from .qualification_agent import QualificationAgent
from .recommendation_engine import RecommendationEngine
from .router import Router, RouteDecision
from .conversation_intelligence import ConversationIntelligenceLayer
from .conversation_modes import ConversationModeDetector
from .response_composer import ResponseComposer

class InboundAgent:
    def __init__(
        self,
        knowledge_base: KnowledgeBase,
        memory: ConversationMemory,
        prompt_path: str | Path,
        model: str | None = None,
        use_openai: bool | None = None,
        use_ollama: bool | None = None,
    ):
        self.knowledge_base = knowledge_base
        self.memory = memory
        self.prompt_template = Path(prompt_path).read_text(encoding="utf-8")

        # Load .env file at runtime if present so `OPENAI_API_KEY` can be read.
        try:
            repo_root = Path(__file__).resolve().parents[1]
            env_file = repo_root / ".env"
            if env_file.exists():
                for raw in env_file.read_text(encoding="utf-8").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('\"').strip("\'")
                    if k and k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass

        # If use_openai is not explicitly set, enable it when an API key is available.
        if use_openai is None:
            self.use_openai = bool(os.environ.get("OPENAI_API_KEY"))
        else:
            self.use_openai = use_openai

        # Model defaults:
        if self.use_openai:
            self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
        else:
            self.model = model or "qwen3:8b"

        # Ollama fallback config:
        if use_ollama is None:
            self.use_ollama = not self.use_openai
        else:
            self.use_ollama = use_ollama

        if self.use_ollama:
            if not self._check_ollama_availability():
                self.use_ollama = False

        self.intent_detector = IntentDetector()
        self.recommender = RecommendationEngine()
        self.router = Router()
        self.handoff_generator = HandoffGenerator()
        self.retriever = KnowledgeRetriever(knowledge_base)
        self.qualification_agent = QualificationAgent(memory)
        self.conversation_intelligence = ConversationIntelligenceLayer(memory)
        self.mode_detector = ConversationModeDetector()
        self.response_composer = ResponseComposer(
            Path(prompt_path).with_name("breakout_personality_prompt.txt"),
            model=self.model if self.use_openai else os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
            enabled=self.use_openai,
        )
        self.last_openai_error = ""
        self.last_ollama_error = ""

    def handle_message(self, message: str) -> AgentResponse:
        resolved_message = self.conversation_intelligence.process_utterance(message)
        normalized_message = self.memory.normalize_number_words(resolved_message)
        previous_state = self._state_snapshot()
        pending_offer = self._pending_offer()
        waiting_before = self.qualification_agent._waiting_for
        active_flow_intent = self._active_flow_intent()

        if self._should_reset_booking_context(normalized_message, pending_offer):
            self.memory.reset_booking_fields()

        if self._is_partial_transcript(normalized_message):
            response = "Sorry, I didn't catch that. Could you repeat it?"
            return AgentResponse(
                response=response,
                intent=self.memory.data.get("intent", "general_faq") or "general_faq",
                intent_confidence=0.2,
                recommendation={"option": "", "reason": ""},
                missing_fields=[],
                next_agent="inbound_agent",
                should_handoff=False,
                state=self.memory.as_state(),
                qualification={
                    "qualified": False,
                    "missing_fields": [],
                    "next_question": "",
                    "summary": {},
                },
                handoff_summary=None,
                booking=None,
                debug={
                    "previous_state": previous_state,
                    "extracted_entities": {},
                    "updated_state": self._state_snapshot(),
                    "missing_slots": [],
                }
            )

        self.memory.add_turn("customer", message)

        core_fields_before = {
            "intent": self.memory.data.get("intent", ""),
            "location": self.memory.data.get("location", ""),
            "participants": self.memory.data.get("participants", ""),
            "age_group": self.memory.data.get("age_group", ""),
            "experience_level": self.memory.data.get("experience_level", ""),
            "company_size": self.memory.data.get("company_size", ""),
        }

        # Determine active flow intent and check for interruption
        active_intent_stored = self.memory.data.get("intent", "")

        is_interruption = False
        if waiting_before and active_intent_stored and active_intent_stored != "general_faq":
            is_interruption = self._is_qualification_interruption(normalized_message, waiting_before, None)

        if is_interruption:
            intent_result = self.intent_detector.detect(
                normalized_message,
                active_intent_stored,
                pending_offer=pending_offer if not waiting_before else None,
            )
            # Check if there is an explicit clear topic switch (e.g. to a different qualification intent)
            if (
                intent_result.intent not in ("general_faq", active_intent_stored)
                and self.intent_detector._is_clear_topic_switch(normalized_message, intent_result.intent)
            ):
                is_interruption = False
                active_intent = intent_result.intent
            else:
                active_intent = active_intent_stored
        else:
            intent_result = self.intent_detector.detect(
                normalized_message,
                active_flow_intent or active_intent_stored or "",
                pending_offer=pending_offer if not waiting_before else None,
            )
            active_intent = intent_result.intent
            if intent_result.intent == "confirm_booking":
                active_intent = (pending_offer or {}).get("intent") or active_intent_stored or "escape_room_inquiry"
            elif active_flow_intent and self._should_preserve_active_flow_intent(intent_result.intent, waiting_before):
                active_intent = active_flow_intent

        if is_interruption:
            intent_result_final = IntentResult("general_faq", 0.72, "faq interruption")
            extracted_entities = self.memory.merge_message(normalized_message, "")
        else:
            intent_result_final = intent_result
            extracted_entities = dict(intent_result.entities)
            extracted_entities.update(self.memory.merge_message(normalized_message, active_intent))

        recommendation = self.recommender.recommend(normalized_message, self.memory.data)
        if recommendation.option and not is_interruption:
            extracted_entities.update(
                self.memory.merge_message(normalized_message, active_intent, recommendation.option)
            )

        # ------------------------------------------------------------------ #
        # Booking consent: customer said yes/no to availability check.        #
        # Handle BEFORE running qualification so the consent turn is clean.   #
        # ------------------------------------------------------------------ #
        if self.memory.data.get("booking_consent_pending"):
            if self._is_booking_consent_yes(normalized_message):
                # Customer wants availability check — activate BookingAgent
                self.memory.data["booking_consent_pending"] = False
                self.memory.data["current_workflow"] = "booking"
                self.memory.save()
                response = "Great, let me check that for you right away."
                response = self._compose_customer_response(
                    response, normalized_message, intent_result_final.intent
                )
                response = self._clean_response(response)
                self._update_discussed_options(response)
                self.memory.add_turn("agent", response)
                
                handoff = self.handoff_generator.generate(self.memory.data)
                
                # Check for LangGraph booking node handler
                booking_result = None
                try:
                    from integrations.langgraph_booking_node import booking_node_handler
                    booking_result = booking_node_handler(handoff, require_payment=False)
                except Exception as exc:
                    booking_result = {"status": "error", "reason": str(exc)}
                
                return AgentResponse(
                    response=response,
                    intent=intent_result_final.intent,
                    intent_confidence=intent_result_final.confidence,
                    next_agent="booking_agent",
                    should_handoff=True,
                    state=self.memory.as_state(),
                    missing_fields=[],
                    recommendation={"option": "", "reason": ""},
                    qualification={"qualified": True, "missing_fields": [], "next_question": "", "summary": {}},
                    handoff_summary=handoff,
                    booking=booking_result,
                    debug={
                        "previous_state": previous_state,
                        "extracted_entities": extracted_entities,
                        "updated_state": self._state_snapshot(),
                        "missing_slots": [],
                    }
                )
            elif self._is_booking_consent_no(normalized_message):
                # Customer declined — clear consent, return to conversation
                self.memory.data["booking_consent_pending"] = False
                self.memory.data["current_workflow"] = "general"
                self.memory.save()
                response = "No problem at all. Feel free to call us when you're ready to book, or I can answer any other questions you might have."
                response = self._compose_customer_response(
                    response, normalized_message, intent_result_final.intent
                )
                response = self._clean_response(response)
                self._update_discussed_options(response)
                self.memory.add_turn("agent", response)
                return AgentResponse(
                    response=response,
                    intent=intent_result_final.intent,
                    intent_confidence=intent_result_final.confidence,
                    next_agent="inbound_agent",
                    should_handoff=False,
                    state=self.memory.as_state(),
                    missing_fields=[],
                    recommendation={"option": "", "reason": ""},
                    qualification={"qualified": True, "missing_fields": [], "next_question": "", "summary": {}},
                    handoff_summary=None,
                    booking=None,
                    debug={
                        "previous_state": previous_state,
                        "extracted_entities": extracted_entities,
                        "updated_state": self._state_snapshot(),
                        "missing_slots": [],
                    }
                )

        # Run or preserve qualification agent state
        if not is_interruption:
            qual_result = self.qualification_agent.update_and_qualify(normalized_message, active_intent)
        else:
            qual_result = self.qualification_agent.qualify(active_intent)
            self.qualification_agent._waiting_for = waiting_before

        handoff_ready = self.memory.handoff_ready(active_intent)

        core_fields_after = {
            "intent": self.memory.data.get("intent", ""),
            "location": self.memory.data.get("location", ""),
            "participants": self.memory.data.get("participants", ""),
            "age_group": self.memory.data.get("age_group", ""),
            "experience_level": self.memory.data.get("experience_level", ""),
            "company_size": self.memory.data.get("company_size", ""),
        }
        core_changed = any(core_fields_before[k] != core_fields_after[k] for k in core_fields_before)

        route = self.router.route(active_intent, qual_result.qualified)
        updated_state = self._state_snapshot()
        response = self._generate_response(
            normalized_message, active_intent, recommendation, handoff_ready, qual_result, waiting_before, core_changed=core_changed
        )

        if self.memory.data.get("booking_consent_pending"):
            route = RouteDecision("inbound_agent", "Booking consent pending.", False)

        response = self._clean_response(response)

        response_mode = str(self.memory.data.get("conversation_mode", ""))
        if response_mode == "faq" and not waiting_before:
            route = RouteDecision("inbound_agent", "Direct FAQ handled by inbound agent.", False)

        # Auto-route budget/pricing queries to booking_agent when user opted-in for booking flow
        handoff = self.handoff_generator.generate(self.memory.data) if handoff_ready else None
        if (
            re.search(r"\b(budget|pricing|price|cost|how much)\b", normalized_message.lower())
        ):
            # Force route to booking agent and generate handoff if missing
            route = RouteDecision("booking_agent", "Budget/pricing routed to booking_agent.", True)
            if not handoff:
                handoff = self.handoff_generator.generate(self.memory.data)
            # Override response to indicate booking is starting
            response = "Okay — I'll start the booking process and check availability. One moment please."
            response = self._compose_customer_response(response, normalized_message, active_intent)

        # If this route hands off to booking, attempt to run the LangGraph booking node
        booking_result = None
        try:
            if handoff and route.next_agent == "booking_agent" and route.should_handoff:
                try:
                    from integrations.langgraph_booking_node import booking_node_handler
                    # Require payment could be decided from intent or memory; default False here.
                    booking_result = booking_node_handler(handoff, require_payment=False)
                except Exception as exc:
                    booking_result = {"status": "error", "reason": str(exc)}
        except Exception:
            # Defensive: do not let booking integration break the main flow
            booking_result = {"status": "error", "reason": "booking_integration_failure"}

        self._update_discussed_options(response)
        self.memory.add_turn("agent", response)

        return AgentResponse(
            response=response,
            intent=active_intent,
            intent_confidence=intent_result_final.confidence,
            next_agent=route.next_agent,
            should_handoff=route.should_handoff,
            state=self.memory.as_state(),
            missing_fields=qual_result.missing_fields,
            recommendation={
                "option": recommendation.option,
                "reason": recommendation.reason,
            },
            qualification={
                "qualified": qual_result.qualified,
                "missing_fields": qual_result.missing_fields,
                "next_question": qual_result.next_question,
                "summary": qual_result.summary,
            },
            handoff_summary=handoff,
            booking=booking_result,
            debug={
                "previous_state": previous_state,
                "extracted_entities": extracted_entities,
                "updated_state": updated_state,
                "missing_slots": qual_result.missing_fields,
            },
        )

    def _generate_response(self, message: str, intent: str, recommendation, should_handoff: bool, qual_result=None, waiting_before: str = "", core_changed: bool = False) -> str:
        response = self._fallback_response(message, intent, recommendation, should_handoff, qual_result, waiting_before, core_changed=core_changed)
        if response:
            return self._compose_customer_response(response, message, intent)

        retrieved_context = self.retriever.search(
            " ".join(
                [
                    message,
                    intent,
                    self.memory.data.get("event_type", ""),
                    self.memory.data.get("location", ""),
                    self.memory.data.get("recommended_option", ""),
                ]
            )
        )

        if (self.use_openai or self.use_ollama) and retrieved_context:
            prompt = self.prompt_template.format(
                knowledge=retrieved_context,
                memory=self.memory.as_prompt_context(),
                intent=intent,
                recommendation=recommendation.option,
                message=message,
            )
            llm_response = ""
            if self.use_openai:
                llm_response = self._call_openai(prompt)
            elif self.use_ollama:
                llm_response = self._call_ollama(prompt)

            if llm_response:
                return llm_response

            # LLM timed out or failed — attempt a deterministic fallback
            # so the conversation stays coherent rather than dead-ending.
            if (self.use_openai and self.last_openai_error) or (self.use_ollama and self.last_ollama_error):
                deterministic = self._fallback_response(
                    message, intent, recommendation, should_handoff, qual_result, waiting_before, core_changed=core_changed
                )
                if deterministic:
                    return deterministic

        fallback = "I don't have that detail to hand right now, but our team can help. May I take your name and number so someone can call you back?"
        return self._compose_customer_response(fallback, message, intent)

    def _compose_customer_response(self, draft: str, message: str, intent: str) -> str:
        mode = self.mode_detector.detect(message, self.memory.data, intent)
        self.memory.data["conversation_mode"] = mode.value
        grounded_context = self.retriever.search(
            " ".join(
                filter(
                    None,
                    (
                        message,
                        intent,
                        str(self.memory.data.get("location", "")),
                        str(self.memory.data.get("recommended_option", "")),
                    ),
                )
            ),
            limit=3,
        )
        return self.response_composer.compose(
            draft=draft,
            message=message,
            state=self.memory.data,
            intent=intent,
            mode=mode,
            grounded_context=grounded_context,
        )


    def _call_ollama(self, prompt: str) -> str:
        process = None
        try:
            process = subprocess.Popen(
                ["ollama", "run", self.model, prompt],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            stdout, _stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            self.last_ollama_error = "timeout"
            if process is not None:
                self._terminate_ollama_process(process)
            return ""
        except Exception as exc:
            self.last_ollama_error = str(exc)
            if process is not None:
                self._terminate_ollama_process(process)
            return ""

        if process.returncode != 0:
            self.last_ollama_error = f"returncode:{process.returncode}"
            return ""
        self.last_ollama_error = ""
        return stdout.strip()

    @staticmethod
    def _terminate_ollama_process(process: subprocess.Popen) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except Exception:
            pass

    def _call_openai(self, prompt: str) -> str:
        try:
            try:
                from openai import OpenAI
            except Exception as exc:
                self.last_openai_error = f"import:{exc}"
                return ""

            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                self.last_openai_error = "missing_api_key"
                return ""
            client = OpenAI(api_key=api_key, timeout=20.0)
            response = client.responses.create(
                model=self.model,
                input=prompt,
                max_output_tokens=600,
            )
            text = getattr(response, "output_text", "")
            self.last_openai_error = ""
            return (text or "").strip()
        except Exception as exc:
            self.last_openai_error = str(exc)
            return ""

    def _should_reset_booking_context(self, message: str, pending_offer: dict | None = None) -> bool:
        if pending_offer and self._is_booking_acceptance_followup(message):
            return False
        if not _BOOKING_TRIGGERS.search(message):
            return False

        lowered = message.lower()
        if any(
            [
                self.memory._extract_location(lowered),
                self.memory._extract_room(lowered),
                self.memory._extract_participants(lowered),
                self.memory._extract_age_group(lowered)[0],
                self.memory._extract_preferred_date(message),
            ]
        ):
            return False

        booking_fields = [
            "location",
            "participants",
            "age_group",
            "room",
            "event_type",
            "preferred_date",
        ]
        return any(bool(self.memory.data.get(field)) for field in booking_fields)

    def _fallback_response(self, message: str, intent: str, recommendation, should_handoff: bool, qual_result=None, waiting_before: str = "", core_changed: bool = False) -> str:
        lowered = message.lower()
        _flow_fields = set(self.memory.FLOW_FIELDS.get(intent, []))

        if self._should_accept_current_recommendation(message, intent):
            contact_missing = self.memory.missing_fields(intent=intent, include_contact=True)
            contact_missing = [field for field in contact_missing if field in self.memory.CONTACT_FIELDS]
            chosen_option = self.memory.data.get("room") or self.memory.data.get("recommended_option", "that option")
            if contact_missing:
                next_field = contact_missing[0]
                prompt = self._ask_for_field(intent, next_field)
                return f"Perfect. I'll go ahead with {chosen_option}. {prompt}"
            name = str(self.memory.data.get("customer_name", ""))
            if name:
                return f"Perfect. Thank you, {name}. I've captured all the information I need. Someone from our team will reach out to you shortly."
            return "Perfect. I've captured all the information I need. Someone from our team will reach out to you shortly."

        # ================================================================== #
        # INTERRUPTION HANDLER                                                 #
        #                                                                      #
        # When qualification is waiting for a field and the customer asks a   #
        # question that doesn't answer it, we:                                #
        #   1. Answer the question using the FAQ / knowledge engine.          #
        #   2. Append a warm transition back to the pending qualification.    #
        #                                                                      #
        # This replaces the strict ownership gate that previously blocked     #
        # all customer questions during qualification.                         #
        # ================================================================== #
        if waiting_before:
            is_interruption = self._is_qualification_interruption(message, waiting_before, qual_result)
            if is_interruption:
                faq_answer = self._get_faq_or_knowledge_answer(lowered, intent, recommendation)
                resume = self._resume_qualification_phrase(waiting_before)
                if faq_answer:
                    return f"{faq_answer} {resume}"
                # No FAQ match — answer best we can and still resume
                return f"Good question. Let me get that checked for you. {resume}"

            # Not an interruption — if it's NOT a flow field, qualification owns this turn
            if waiting_before not in _flow_fields:
                route_check = self.router.route(intent, False)
                if route_check.next_agent == "qualification_agent" and not should_handoff:
                    if qual_result is not None and qual_result.response:
                        return qual_result.response

        # ------------------------------------------------------------------ #
        # Standard conversation responses                                     #
        # ------------------------------------------------------------------ #

        if self._is_breakout_greeting(lowered):
            return "Yes, this is Breakout Escape Rooms. How may I help you today?"

        if self._is_plain_greeting(lowered) and intent == "general_faq":
            return "Hi, this is Breakout Escape Rooms. How may I help you today?"

        # Check FAQ / direct questions first
        compound_answer = self._answer_multiple_questions(lowered)
        if compound_answer:
            return compound_answer
        faq_answer = self._answer_faq(lowered)
        if faq_answer and (intent == "general_faq" or self._is_direct_question(lowered)):
            return faq_answer

        explicit_answer = self._answer_explicit_intent_question(lowered, intent)
        if explicit_answer:
            return explicit_answer

        # Check explicit recommendation queries
        is_asking_rec = any(kw in lowered for kw in ("recommend", "suggest", "popular", "most popular", "what games", "what rooms", "which room"))
        if is_asking_rec and recommendation.option:
            return self._recommendation_response(intent, recommendation.option, recommendation.reason, lowered)

        if self._is_yes_to_compare(lowered):
            comparison = self._comparison_response(self.memory.data.get("recommended_option", ""))
            if comparison:
                return comparison

        if intent == "escape_room_inquiry" and self._asks_for_more_details(lowered):
            details = self._comparison_response(self.memory.data.get("recommended_option", ""))
            if not details:
                details = self._room_details_response(self.memory.data.get("recommended_option", ""))
            if details:
                return details

        if intent == "general_faq":
            return ""

        # ------------------------------------------------------------------ #
        # BOOKING CONSENT — replaces hard termination                         #
        # Instead of "Someone from our team will reach out", we ask:          #
        # "Would you like me to check availability for [date] at [location]?" #
        # ------------------------------------------------------------------ #
        if should_handoff:
            # Update workflow state
            self.memory.data["current_workflow"] = "awaiting_booking"
            self.memory.data["booking_consent_pending"] = True
            self.memory.save()
            return self._booking_consent_response()

        guided_response = self._guided_response(message, intent, recommendation, waiting_before=waiting_before, core_changed=core_changed)
        if guided_response:
            return guided_response

        # Prompt for date if location is set but date is unknown
        if intent == "escape_room_inquiry" and self.memory.data.get("location") and not self.memory.data.get("preferred_date"):
            if self.memory.data.get("discussed_options"):
                return "Which date were you thinking of visiting?"

        # ------------------------------------------------------------------ #
        # SECONDARY QA GUARD                                                   #
        # Fires after _guided_response when _waiting_for is NOT yet set       #
        # ------------------------------------------------------------------ #
        next_expected = self.qualification_agent._waiting_for
        if (
            qual_result is not None
            and not qual_result.qualified
            and qual_result.response
            and next_expected not in _flow_fields
            and (waiting_before or self.memory.flow_complete(intent))
        ):
            route_check = self.router.route(intent, False)
            if route_check.next_agent == "qualification_agent":
                return qual_result.response

        flow_missing = self._get_flow_missing(intent, lowered)
        if intent == "escape_room_inquiry" and flow_missing == ["location"] and recommendation.option:
            has_core_fields = bool(self.memory.data.get("age_group") and self.memory.data.get("participants"))
            if is_asking_rec or (core_changed and has_core_fields) or (has_core_fields and not self.memory.data.get("discussed_options")):
                return self._recommendation_response(intent, recommendation.option, recommendation.reason, lowered)

        if flow_missing:
            if (
                flow_missing[0] == "preferred_date"
                and not self._message_supplied_location(lowered)
                and self._invalid_date_answer(lowered)
            ):
                return "Sorry, I didn't quite catch the date. Could you say something like 18 June or June 18?"
            return self._ask_for_field(intent, flow_missing[0])

        if recommendation.option:
            if intent == "escape_room_inquiry":
                has_core_fields = bool(self.memory.data.get("age_group") and self.memory.data.get("participants"))
                if is_asking_rec or (core_changed and has_core_fields) or (has_core_fields and not self.memory.data.get("discussed_options")):
                    return self._recommendation_response(intent, recommendation.option, recommendation.reason, lowered)

            next_expected = self.qualification_agent._waiting_for
            if next_expected not in _flow_fields:
                if qual_result is not None and not qual_result.qualified and qual_result.response:
                    return qual_result.response

            contact_missing = self.memory.missing_fields(intent=intent, include_contact=True)
            if contact_missing and self._message_contains_contact_detail(message):
                return self._ask_for_field(intent, contact_missing[0])

            details = self._recommendation_response(intent, recommendation.option, recommendation.reason, lowered)
            if contact_missing:
                return f"{details} {self._ask_for_field(intent, contact_missing[0])}"
            return details

        contact_missing = self.memory.missing_fields(intent=intent, include_contact=True)
        if contact_missing:
            return self._ask_for_field(intent, contact_missing[0])

        return ""

    def _answer_faq(self, lowered: str) -> str:
        if "is this" in lowered or "breakout" in lowered:
            return "Yes, this is Breakout Escape Rooms. How may I help you today?"
        if any(term in lowered for term in ("running late", "we are late", "we're late", "will be late", "arrive late")) or re.search(
            r"\b(?:running|arriving|be)\b.*\blate\b", lowered
        ):
            return (
                "Don't worry, let me help. The game starts at the scheduled time because sessions run back to back, "
                "so arriving late reduces the time available inside the room. How late do you expect to be?"
            )
        if any(
            term in lowered
            for term in (
                "don't escape", "do not escape", "cannot escape", "can't escape",
                "not escape", "fail to escape", "actually locked", "locked inside",
            )
        ):
            return (
                "Don't worry, we won't keep you locked forever. Players are not actually locked in, and our team "
                "monitors the game and can assist when needed. Would you like a quick idea of how the experience works?"
            )
        if any(term in lowered for term in ("briefing", "game rules", "rules before", "before the game")):
            return (
                "Absolutely. The team will brief you before the game, and staff monitor the experience and assist if needed. "
                "The game itself runs for around 50 minutes, so please arrive 20 minutes before your slot. "
                "Is there a particular part of the briefing you wanted to check?"
            )
        # Case 5: "Which of Murder Mystery or Hostage do you suggest/recommend"
        if ("suggest" in lowered or "recommend" in lowered or "lean" in lowered) and "murder mystery" in lowered and "hostage" in lowered:
            return (
                "For first-time players, I'd personally lean toward Murder Mystery because it focuses on a classic "
                "detective investigation without intense time pressure, making it a bit more relaxed to start with. "
                "Would you like to go with that one?"
            )

        # Case 4: Popular experience in Bangalore next weekend
        if "popular" in lowered or "most popular" in lowered:
            date_phrase = "for next weekend" if "next weekend" in lowered or "weekend" in lowered else "for your group"
            return (
                f"Since you're planning a visit {date_phrase}, our most popular experiences are Murder Mystery (a classic detective challenge) "
                "and Hostage (a high-suspense rescue mission). Both are extremely popular and available at all three of our locations. "
                "Which location would be most convenient for you — Koramangala, Whitefield, or JP Nagar?"
            )

        if lowered.strip() in {"koramangala", "whitefield", "jp nagar", "jp"}:
            if "koramangala" in lowered:
                return "Koramangala has basement and street parking, and offers Murder Mystery, Hostage, Curse of the Pharaoh, Classified, Undercover, The Wizarding Championship, and The Forbidden Forest."
            if "whitefield" in lowered:
                return "Whitefield has basement car parking with dedicated spots and offers Murder Mystery, Hostage, Bomb Defusal, and Undercover."
            if "jp nagar" in lowered or lowered.strip() == "jp":
                return "JP Nagar has street parking and offers Murder Mystery, Hostage, and Prison Break."
        # Direct booking intent (user wants to book)
        if re.search(
            r"\b(i want to book|want to book|can i book|book it|book now|book a|book for|reserve a|i want to reserve|how do i book|how can i book|how to book|how do you book|how do we book)\b",
            lowered,
        ):
            if re.search(r"\b(escape room|room|birthday|corporate|bachelor|farewell|couple|virtual|online|party)\b", lowered):
                return "Sure. I can help you book that. What location or date would you like?"
            return (
                "Sure. What type of event are you planning — an escape room, birthday party, corporate event, or something else?"
            )
        room_response = self._answer_room_name(lowered)
        if room_response:
            return room_response
        # Budget/pricing questions
        if (
            "budget" in lowered
            or "pricing" in lowered
            or "price" in lowered
            or "cost" in lowered
            or re.search(r"\bhow much\b", lowered)
        ):
            return (
                "Our pricing depends on event type and group size. I don't have exact budget details here, "
                "but I can connect you with our sales team for a quote or provide an estimated price — "
                "would you like a quote or an estimate?"
            )
        # New user / general inventory question
        if "what do you have" in lowered or ("new to this" in lowered or "i'm new" in lowered or "im new" in lowered):
            return (
                "Breakout Escape Rooms offers live escape room experiences plus birthday, corporate, virtual, "
                "and event packages across Koramangala, Whitefield, and JP Nagar. I can help you find the best room or package for your group."
            )
        if re.search(r"\boff(?:er|ering|r)\b", lowered):
            return (
                "Breakout Escape Rooms offers live escape room experiences plus birthday, corporate, virtual, "
                "and event packages across Koramangala, Whitefield, and JP Nagar. I can help you find the best room or package for your group."
            )
        if "what escape rooms" in lowered or "rooms are available" in lowered or "games are available" in lowered:
            if "whitefield" in lowered:
                return "Sure. At Whitefield, you can choose from Murder Mystery, Hostage, Bomb Defusal, and Undercover. If you want something more intense, Bomb Defusal is the one I'd narrow in on."
            if "koramangala" in lowered:
                return "Sure. Koramangala has Murder Mystery, Hostage, Curse of the Pharaoh, Classified, Undercover, The Wizarding Championship, and The Forbidden Forest. If you're new, I'd start with Murder Mystery or Hostage."
            if "jp nagar" in lowered or "jp" in lowered:
                return "Sure. JP Nagar has Murder Mystery, Hostage, and Prison Break. If you want something more mission-style, Prison Break is the stronger pick."
            return "Sure. Breakout has Murder Mystery, Hostage, Curse of the Pharaoh, Classified, Bomb Defusal, Undercover, Prison Break, The Wizarding Championship, and The Forbidden Forest. I can narrow that down based on age group and challenge level."

        if "beginner" in lowered or "first time" in lowered or "first-time" in lowered:
            return (
                "No problem at all! We love hosting first-time players. I'd usually recommend starting with "
                "Murder Mystery because it gives you investigation-style puzzles and teamwork without feeling "
                "overwhelming. If you want something slightly more exciting, Hostage is a great alternative. "
                "How many people will be joining?"
            )

        if "whitefield" in lowered and ("visiting" in lowered or "coming" in lowered):
            return "Great. At Whitefield, Murder Mystery and Hostage are better for lighter investigation-style play, while Bomb Defusal and Undercover are stronger if you want more intensity. What age group is the team?"
        if "koramangala" in lowered and ("visiting" in lowered or "coming" in lowered):
            return "Great. Koramangala gives you the widest mix, from Murder Mystery and Hostage to more challenging rooms like Classified and Undercover. What age group is the team?"
        if ("jp nagar" in lowered or "jp" in lowered) and ("visiting" in lowered or "coming" in lowered):
            return "Great. JP Nagar has Murder Mystery, Hostage, and Prison Break. Murder Mystery and Hostage are easier to start with, while Prison Break is more mission-style. What age group is the team?"
        if "hostage" in lowered and ("age" in lowered or "limit" in lowered):
            return "Hostage is listed for ages 9 and above."
        if "food" in lowered:
            return "Food options include continental food, build-your-menu options, mix snack boxes, hi-tea options, and Indian buffet options for corporate events."
        if "birthday" in lowered and ("package" in lowered or "activities" in lowered):
            return "Birthday parties include Escape Room, Showstopper, Karaoke, and Scavenger Hunt. Capacity is 50-80 people at Koramangala and 35-40 people at JP Nagar or Whitefield."
        if "corporate" in lowered and ("package" in lowered or "activities" in lowered or "event" in lowered):
            return "Corporate events include Escape Room, Let Loose, Detective Job, Scavenger Hunt Activity, The Unwind Challenge, and virtual event options."
        if "what is an escape room" in lowered or "what is escape room" in lowered or ("escape room" in lowered and ("what is" in lowered or "definition" in lowered or "define" in lowered)):
            return "Sure. An escape room is a team game where players enter a themed room, find clues, solve puzzles, and complete a mission before time runs out."
        if "how long" in lowered or "duration" in lowered:
            return "Each game runs for around 50 minutes. Players should arrive 20 minutes before the slot time."

        # Case 2: Location for kids
        is_kids_inquiry = ("kids" in lowered or "children" in lowered) or (
            self.memory.data.get("age_group") == "kids" and self.qualification_agent._waiting_for == "location"
        )
        if is_kids_inquiry and ("location" in lowered or "which" in lowered or "better" in lowered):
            return (
                "For younger children and family groups, I'd usually recommend Whitefield because it has fantastic family-friendly "
                "experiences and family groups often enjoy it. Which date were you thinking of visiting?"
            )

        recommendation_request = any(
            term in lowered
            for term in ("recommend", "suggest", "which room", "best room", "what should")
        )
        if ("children" in lowered or "kids" in lowered) and not recommendation_request:
            return "Yes. Kid-only and kid-friendly games are available with staff supervision."
        if "parking" in lowered or "park" in lowered:
            return "Koramangala has basement and street parking, Whitefield has basement car parking with dedicated car spots and general parking, and JP Nagar has street parking."
        if "where are you located" in lowered or "what are your locations" in lowered or "where is breakout" in lowered or "our locations" in lowered or "locations" in lowered or ("location" in lowered and any(w in lowered for w in ("which", "what", "better", "are", "have", "address", "list", "know", "where"))):
            return "Breakout has locations in Koramangala, Whitefield, and JP Nagar."
        if "cancel" in lowered or "refund" in lowered:
            return "Cancellation charges depend on how far in advance the cancellation is made. I can connect you with the appropriate team for cancellation or refund handling."
        if "walk in" in lowered:
            return "Walk-ins are allowed only if slots are available. Slots cannot be held without advance payment."
        if "advance booking" in lowered or "booking required" in lowered:
            return "Yes. All bookings are done online."
        return ""

    def _answer_multiple_questions(self, lowered: str) -> str:
        topics: list[str] = []
        if "location" in lowered or "where are you" in lowered:
            topics.append("locations")
        if "food" in lowered:
            topics.append("food")
        if "parking" in lowered or re.search(r"\bpark\b", lowered):
            topics.append("parking")
        if "how long" in lowered or "duration" in lowered:
            topics.append("duration")
        if "cancel" in lowered or "refund" in lowered:
            topics.append("cancellation")
        if len(set(topics)) < 2:
            return ""

        answers = {
            "locations": "Breakout has locations in Koramangala, Whitefield, and JP Nagar.",
            "food": "Food options include continental food, build-your-menu options, mix snack boxes, hi-tea options, and Indian buffet options for corporate events.",
            "parking": "Koramangala has basement and street parking, Whitefield has basement car parking with dedicated spots and general parking, and JP Nagar has street parking.",
            "duration": "Each game runs for around 50 minutes, and players should arrive 20 minutes before the slot time.",
            "cancellation": "Cancellation charges depend on how far in advance the cancellation is made, and the appropriate team handles cancellation or refund requests.",
        }
        ordered_topics = list(dict.fromkeys(topics))
        return "Sure. " + " ".join(answers[topic] for topic in ordered_topics)

    def _answer_room_name(self, lowered: str) -> str:
        import difflib
        import re

        rooms = {
            "murder mystery": {
                "description": "Murder Mystery is an investigation-style escape room with clues, puzzles, and a story-driven mystery.",
                "locations": "Koramangala, Whitefield, and JP Nagar",
            },
            "hostage": {
                "description": "Hostage is a rescue-style escape room with urgency, teamwork, and investigation elements.",
                "locations": "Koramangala, Whitefield, and JP Nagar",
            },
            "curse of the pharaoh": {
                "description": "Curse of the Pharaoh is an adventure-themed room with archaeology and mythology puzzles.",
                "locations": "Koramangala",
            },
            "classified": {
                "description": "Classified is a tense investigation room with cryptic puzzles and story-driven clues.",
                "locations": "Koramangala",
            },
            "undercover": {
                "description": "Undercover is a mystery room with action and plot twists, suitable for teams who enjoy immersive stories.",
                "locations": "Koramangala and Whitefield",
            },
            "the wizarding championship": {
                "description": "The Wizarding Championship is a magical escape room designed for younger players and families.",
                "locations": "Koramangala",
            },
            "the forbidden forest": {
                "description": "The Forbidden Forest is an adventure room with spooky elements and puzzle-solving in a forest-themed setting.",
                "locations": "Koramangala",
            },
            "bomb defusal": {
                "description": "Bomb Defusal is an intense, time-pressured room that challenges teams to work quickly and communicate clearly.",
                "locations": "Whitefield",
            },
            "prison break": {
                "description": "Prison Break is a mission-style room focused on escape tactics, teamwork, and dramatic storytelling.",
                "locations": "JP Nagar",
            },
        }
        for room_name, info in rooms.items():
            if room_name in lowered:
                location = self.memory.data.get("location", "")
                if location:
                    return (
                        f"At {location}, {room_name.title()} is available and {info['description']} "
                        f"It is offered at {info['locations']}."
                    )
                return f"{room_name.title()} is available and {info['description']} It is offered at {info['locations']}."

        normalized = re.sub(r"[^a-z ]", "", lowered).strip()
        if normalized:
            match = difflib.get_close_matches(normalized, rooms.keys(), n=1, cutoff=0.72)
            if match:
                room_name = match[0]
                info = rooms[room_name]
                location = self.memory.data.get("location", "")
                if location:
                    return (
                        f"At {location}, {room_name.title()} is available and {info['description']} "
                        f"It is offered at {info['locations']}."
                    )
                return f"{room_name.title()} is available and {info['description']} It is offered at {info['locations']}."

        return ""

    def _ask_for_field(self, intent: str, field: str) -> str:
        if field == "location":
            return "Sure. Which location would you prefer — Koramangala, Whitefield, or JP Nagar?"
        if field == "participants":
            if intent == "birthday_party":
                return "Got it. Roughly how many guests are you expecting?"
            return "Sure. How many people will be joining?"
        if field == "age_group":
            return "Sure. What age group are the players?"
        if field == "preferred_date":
            if intent == "birthday_party":
                location = str(self.memory.data.get("location", ""))
                capacity = self._capacity_for_location(location)
                if capacity:
                    return f"Great. {location} can accommodate approximately {capacity}. What date were you thinking for the party?"
            return "Sure. What date were you thinking for the event?"
        if field == "company_size":
            return "Sure. Roughly how many people are in the team?"
        if field == "customer_name":
            return "May I have your name?"
        if field == "phone":
            return "Thank you. Could I get your phone number so we can follow up with the details?"
        if field == "food_required":
            return "Would you like food and beverages for the event?"
        if field == "budget_range":
            return "What budget are you working with? We have Basic, Standard, and Premium packages."
        return "Could you share one more detail for me?"

    def _get_flow_missing(self, intent: str, lowered: str) -> list[str]:
        flow_missing = self.memory.missing_fields(intent=intent, include_contact=False)
        has_friends = "friends" in lowered
        if not has_friends:
            for turn in reversed(self.memory.data.get("conversation", [])):
                if turn.get("role") == "customer" and "friends" in turn.get("content", "").lower():
                    has_friends = True
                    break
        if has_friends and "age_group" in flow_missing:
            if "location" in flow_missing:
                flow_missing.remove("age_group")
                flow_missing.insert(flow_missing.index("location"), "age_group")
        return flow_missing

    def _recommendation_response(self, intent: str, option: str, reason: str, lowered: str = "") -> str:
        if intent == "birthday_party":
            location = self.memory.data.get("location", "")
            if str(location).lower() == "whitefield":
                return "Great. Whitefield can accommodate approximately 35-40 guests. The birthday package can combine escape room experiences with activities like Karaoke, Scavenger Hunt, and Showstopper. What date are you considering?"
            return "Absolutely. Birthday packages can combine escape room experiences with activities like Karaoke, Scavenger Hunt, and Showstopper. What date are you considering?"
        if intent == "corporate_event":
            return "For a corporate team, I'd suggest combining Escape Rooms with Scavenger Hunt or Let Loose activities. Escape Rooms work well for teamwork and problem-solving, while Scavenger Hunt helps larger groups participate together. Which location are you considering?"
        if intent == "escape_room_inquiry":
            flow_missing = self._get_flow_missing(intent, lowered)
            if "location" in flow_missing:
                follow_up = "Which location are you planning to visit?"
            elif "age_group" in flow_missing:
                follow_up = "What age group are the players?"
            elif "participants" in flow_missing:
                follow_up = "How many people will be joining?"
            elif "preferred_date" in flow_missing:
                follow_up = "Which date were you thinking of visiting?"
            else:
                follow_up = "Would you like me to compare those two?"
 
            if option == "The Wizarding Championship":
                prefix = self._escape_room_context_prefix("children aged 5 to 8")
                actual_follow = follow_up if "location" in flow_missing else "Would you like more details about that room?"
                return f"{prefix}I'd recommend The Wizarding Championship. It is designed for that age group and gives younger players a magical, activity-led escape room experience. If the group is closer to 9 and above, Murder Mystery or Hostage would be the next step up. {actual_follow}"
            if option == "Murder Mystery and Hostage":
                prefix = self._escape_room_context_prefix("that age group")
                actual_follow = follow_up if "location" in flow_missing else "Would you like more details about either room?"
                return f"{prefix}Murder Mystery and Hostage are both good choices. Murder Mystery focuses more on investigation and clue solving, while Hostage adds more urgency with a rescue-style story. {actual_follow}"
            if option == "Murder Mystery or Hostage":
                prefix = self._beginner_context_prefix()
                return f"{prefix}I'd start with Murder Mystery. It gives you investigation-style puzzles and teamwork without feeling overwhelming. If you want something slightly more exciting, Hostage is a good alternative. {follow_up}"
            
            adult_resp = self._adult_recommendation_response()
            if adult_resp:
                return adult_resp
        return f"Sure. {option} would be a good fit. {reason}"

    def _guided_response(self, message: str, intent: str, recommendation, waiting_before: str = "", core_changed: bool = False) -> str:
        lowered = message.lower()
 
        if intent == "birthday_party":
            if "birthday" in lowered and not self._message_has_count(lowered):
                return "Absolutely. Birthday packages can combine escape room experiences with activities like Karaoke, Scavenger Hunt, and Showstopper, depending on the package. Approximately how many guests are you expecting?"
            if not self.memory.data.get("participants"):
                return "Absolutely. Birthday packages can combine escape room experiences with activities like Karaoke, Scavenger Hunt, and Showstopper, depending on the package. Approximately how many guests are you expecting?"
            if not self.memory.data.get("location"):
                participants = self.memory.data.get("participants")
                return f"Great. For around {participants} guests, we can narrow the package by location first. Which location would you prefer: Koramangala, Whitefield, or JP Nagar?"
 
        if intent == "corporate_event":
            if self.memory.data.get("company_size") and (
                not self.memory.data.get("location") or self._is_fresh_corporate_inquiry_without_location(lowered)
            ):
                size = self.memory.data.get("company_size")
                return f"For a team of {size}, I'd suggest a combination of Escape Rooms and Scavenger Hunt activities. Escape Rooms help with teamwork and problem-solving, while Scavenger Hunt lets larger groups participate together. Which location are you considering?"
            if not self.memory.data.get("company_size"):
                return "Absolutely. Corporate events can include Escape Rooms, Let Loose, Detective Job, Scavenger Hunt Activity, and The Unwind Challenge. How many employees are you planning for?"
 
        if intent == "couple_event":
            return "A lot of couples choose Murder Mystery because it is focused on teamwork, clue solving, and the story. If you want something with more urgency, Hostage is a good alternative. Are you looking for something relaxed or more challenging?"
 
        if intent == "escape_room_inquiry":
            if self._is_beginner_context(lowered):
                participants = self.memory.data.get("participants")
                location = self.memory.data.get("location")
                if participants and location:
                    return self._recommendation_response(intent, "Murder Mystery or Hostage", "Murder Mystery is easier to start with, while Hostage adds a bit more urgency.", lowered)
                if participants:
                    flow_missing = self._get_flow_missing(intent, lowered)
                    follow = "Which location are you planning to visit?"
                    if "age_group" in flow_missing and (waiting_before == "age_group" or flow_missing[0] == "age_group"):
                        follow = "Are the players mostly adults, kids, or a mix?"
                    return f"That sounds like a fun group. For {participants} first-time players, I'd start with Murder Mystery. It gives you investigation-style puzzles and teamwork without feeling overwhelming. If you'd like something slightly more exciting, Hostage is another good option. {follow}"
                return "If it's your first escape room, I'd usually recommend Murder Mystery. It gives you investigation-style puzzles and teamwork without feeling too heavy. If you'd like something slightly more exciting, Hostage is another good option. How many people will be joining?"
            if recommendation.option:
                is_asking_rec = any(kw in lowered for kw in ("recommend", "suggest", "popular", "most popular", "what games", "what rooms", "which room"))
                has_core_fields = bool(self.memory.data.get("age_group") and self.memory.data.get("participants"))
                if is_asking_rec or (core_changed and has_core_fields) or (has_core_fields and not self.memory.data.get("discussed_options")):
                    return self._recommendation_response(intent, recommendation.option, recommendation.reason, lowered)
 
        return ""

    def _adult_recommendation_response(self) -> str:
        age_group = str(self.memory.data.get("age_group", "")).lower()
        # If age_group is actually kids or teens, delegate to appropriate response
        if age_group == "kids":
            return self._kids_recommendation_response()
        if "teen" in age_group:
            return self._teens_recommendation_response()

        participants = self.memory.data.get("participants")
        location = str(self.memory.data.get("location", ""))
        challenge_preference = str(self.memory.data.get("challenge_preference", "")).lower()
        experience_level = str(self.memory.data.get("experience_level", "")).lower()

        group_phrase = f"For a group of {participants} adults" if participants else "For adults"
        if location:
            group_phrase += f" visiting {location}"

        if experience_level == "beginner" or challenge_preference == "beginner":
            return (
                f"{group_phrase}, I'd recommend Murder Mystery or Hostage. "
                "Murder Mystery is the easier starting point, while Hostage adds a little more urgency without feeling too heavy."
            )
        if location.lower() == "whitefield" and challenge_preference == "challenging":
            return (
                f"{group_phrase}, I'd recommend Bomb Defusal. "
                "It is the strongest fit if you want something intense, time-pressured, and more challenging."
            )
        if location.lower() == "whitefield" and challenge_preference == "story":
            return (
                f"{group_phrase}, I'd recommend Undercover. "
                "It gives you a more story-driven mystery with a stronger investigation feel."
            )
        if location.lower() == "koramangala" and challenge_preference == "challenging":
            return (
                f"{group_phrase}, I'd recommend Classified. "
                "It is the sharper fit if you want a more challenging investigation-style room."
            )
        if location.lower() == "koramangala" and challenge_preference == "story":
            return (
                f"{group_phrase}, I'd recommend Undercover. "
                "It has the stronger story setup and a tense bunker-style mystery."
            )
        if location.lower() == "jp nagar" and challenge_preference == "challenging":
            return (
                f"{group_phrase}, I'd recommend Prison Break. "
                "It is the stronger mission-style challenge at that location."
            )
        if location.lower() == "jp nagar" and challenge_preference == "story":
            return (
                f"{group_phrase}, I'd recommend Murder Mystery. "
                "It is lighter and more clue-driven if you want more story than pressure."
            )

        if location.lower() == "whitefield":
            return f"{group_phrase}, I'd recommend Bomb Defusal or Undercover. Bomb Defusal is better if you want something intense and time-pressured, while Undercover works well if you enjoy a mystery-style investigation. Are you looking for something challenging or more story-driven?"
        if location.lower() == "koramangala":
            return f"{group_phrase}, I'd recommend Classified or Undercover. Classified is great if you enjoy investigation-style challenges, while Undercover gives you a more tense bunker-style mystery. Are you looking for something challenging or more beginner-friendly?"
        if location.lower() == "jp nagar":
            return f"{group_phrase}, I'd recommend Prison Break. It has a stronger mission-style setup, and Murder Mystery is a lighter alternative if you want more clue solving. Are you looking for something intense or more relaxed?"

        return f"{group_phrase}, I'd recommend Classified or Bomb Defusal. Classified is great if you enjoy investigation-style challenges and solving a mystery together. Bomb Defusal is better if you're looking for something more intense and fast-paced. Are you looking for something challenging or something more beginner-friendly?"

    def _kids_recommendation_response(self) -> str:
        """Recommendation for kids age group."""
        participants = self.memory.data.get("participants")
        location = str(self.memory.data.get("location", ""))

        group_phrase = f"For a group of {participants} kids" if participants else "For kids"
        if location:
            group_phrase += f" visiting {location}"

        return f"{group_phrase}, I'd recommend Murder Mystery or Hostage. They're both available at all locations and work well for younger players. Murder Mystery focuses on investigation and clue solving, while Hostage adds a bit more urgency with a rescue-style story. Which would you prefer?"

    def _teens_recommendation_response(self) -> str:
        """Recommendation for teens age group."""
        participants = self.memory.data.get("participants")
        location = str(self.memory.data.get("location", ""))
        challenge_preference = str(self.memory.data.get("challenge_preference", "")).lower()
        age_detail = str(self.memory.data.get("age_detail", ""))

        age_match = re.search(r"\d+(?:-\d+)?", age_detail)
        if participants and age_match:
            group_phrase = f"For {participants} players aged {age_match.group(0)}"
        elif participants:
            group_phrase = f"For a group of {participants} teens"
        else:
            group_phrase = "For teens"
        if location:
            group_phrase += f" visiting {location}"

        if age_match and int(age_match.group(0).split("-")[-1]) <= 13:
            follow_up = (
                "Would you like me to compare those two?"
                if location
                else "Which location are you planning to visit?"
            )
            return (
                f"{group_phrase}, I'd start with Murder Mystery. It focuses on investigation and clue solving, "
                f"which gives the group plenty to work through together. Hostage is the more urgent rescue-style "
                f"alternative. {follow_up}"
            )

        if challenge_preference == "challenging":
            if location.lower() == "whitefield":
                return f"{group_phrase}, I'd recommend Bomb Defusal. It is the stronger challenge if the team wants something more intense."
            if location.lower() == "koramangala":
                return f"{group_phrase}, I'd recommend Classified. It gives teens a more challenging investigation-style experience."
            if location.lower() == "jp nagar":
                return f"{group_phrase}, I'd recommend Prison Break. It is the more mission-style challenge at that location."
            return f"{group_phrase}, I'd recommend Classified or Bomb Defusal if the team wants the more challenging options."
        if challenge_preference in {"beginner", "story"}:
            return f"{group_phrase}, I'd recommend Murder Mystery or Hostage. They give teens a good mix of puzzle-solving, teamwork, and story without feeling too punishing."

        return f"{group_phrase}, I'd recommend Murder Mystery or Hostage to start with. They work well for your age group and give a good mix of puzzle-solving and teamwork. If you want something more challenging, try Classified or Bomb Defusal. What interests you?"

    def _escape_room_context_prefix(self, age_phrase: str) -> str:
        participants = self.memory.data.get("participants")
        location = self.memory.data.get("location")
        stored_age = str(self.memory.data.get("age_group", ""))
        age_detail = str(self.memory.data.get("age_detail", ""))
        group_word = "players"
        if age_detail and stored_age in {"kids", "teens"} and re.search(r"\d+", age_detail):
            age_match = re.search(r"\d+(?:-\d+)?", age_detail)
            if age_match:
                age_phrase = f"kids aged {age_match.group(0)}"
                group_word = "kids"
        elif stored_age == "kids" and age_phrase == "that age group":
            age_phrase = "kids"
            group_word = "kids"
        if participants and location:
            if group_word == "kids" and age_phrase.startswith("kids aged"):
                return f"For {participants} {age_phrase} visiting {location}, "
            return f"For {participants} {group_word} in {age_phrase} visiting {location}, "
        if participants:
            if group_word == "kids" and age_phrase.startswith("kids aged"):
                return f"For {participants} {age_phrase}, "
            return f"For {participants} {group_word} in {age_phrase}, "
        if location:
            return f"For {age_phrase} visiting {location}, "
        return ""

    def _beginner_context_prefix(self) -> str:
        participants = self.memory.data.get("participants")
        location = self.memory.data.get("location")
        age_group = str(self.memory.data.get("age_group", ""))
        if participants and age_group == "adults":
            prefix = f"For a group of {participants} adults"
            if location:
                prefix += f" visiting {location}"
            return f"{prefix}, since you're first-time players, "
        return self._escape_room_context_prefix("first-time players")

    def _room_details_response(self, recommended_option: str) -> str:
        option = recommended_option.lower()
        if "wizarding" in option:
            return "The Wizarding Championship is at Koramangala. It is for ages 5 to 8, with a maximum capacity of 8 players and minimum 4 players."
        if "murder mystery" in option and "hostage" in option:
            return "Murder Mystery and Hostage are available at Koramangala, Whitefield, and JP Nagar. Each has a capacity of 7 players and a minimum of 2 players."
        if "classified" in option or "undercover" in option or "prison break" in option or "bomb defusal" in option:
            return "Classified is at Koramangala, Undercover is at Koramangala and Whitefield, Prison Break is at JP Nagar, and Bomb Defusal is at Whitefield."
        return ""

    def _comparison_response(self, recommended_option: str) -> str:
        option = recommended_option.lower()
        if "murder mystery" in option and "hostage" in option:
            return "Sure. Murder Mystery is the better starting point if you want clue solving and an investigation-style story. Hostage is a good alternative if you want a little more urgency and a rescue-style setup. Would you prefer something calmer or slightly more exciting?"
        if "bomb defusal" in option and "undercover" in option:
            return "Sure. Bomb Defusal is stronger if you want pressure and a faster-paced challenge. Undercover is better if you prefer a mystery-style investigation. Are you leaning toward intensity or story?"
        if "classified" in option and "undercover" in option:
            return "Sure. Classified is better for investigation-style challenges, while Undercover has a tense bunker-style mystery. Are you looking for difficult puzzles or a stronger story setup?"
        return ""

    def _answer_explicit_intent_question(self, lowered: str, intent: str) -> str:
        if intent == "corporate_event" and self._asks_for_room_suggestion(lowered):
            size = self.memory.data.get("company_size") or self.memory.data.get("participants")
            group = f" for a team of {size}" if size else ""
            return f"For a corporate group{group}, I'd suggest combining Escape Rooms with Scavenger Hunt or Let Loose activities. Escape Rooms handle the teamwork and problem-solving part, while Scavenger Hunt helps larger groups participate together. Which location are you considering?"
        return ""

    @staticmethod
    def _capacity_for_location(location: str) -> str:
        lowered = location.lower()
        if lowered == "koramangala":
            return "50-80 guests"
        if lowered in {"whitefield", "jp nagar"}:
            return "35-40 guests"
        return ""

    @staticmethod
    def _is_breakout_greeting(lowered: str) -> bool:
        return "is this" in lowered and "breakout" in lowered

    @staticmethod
    def _is_plain_greeting(lowered: str) -> bool:
        normalized = re.sub(r"[^a-z ]", "", lowered).strip()
        return normalized in {"hi", "hello", "hey", "good morning", "good afternoon", "good evening"}

    @staticmethod
    def _asks_for_more_details(lowered: str) -> bool:
        return any(
            phrase in lowered
            for phrase in (
                "more information",
                "more info",
                "tell me more",
                "about those rooms",
                "details",
                "compare these two",
                "compare these 2",
                "compare those two",
                "compare those 2",
                "compare them",
            )
        )

    @staticmethod
    def _is_yes_to_compare(lowered: str) -> bool:
        normalized = re.sub(r"[^a-z ]", "", lowered).strip()
        return normalized in {"yes", "yes please", "yeah", "sure", "please compare", "compare"}

    @staticmethod
    def _asks_for_room_suggestion(lowered: str) -> bool:
        return any(term in lowered for term in ("which room", "what room", "suggest", "recommend"))

    @staticmethod
    def _is_fresh_corporate_inquiry_without_location(lowered: str) -> bool:
        has_corporate_signal = any(term in lowered for term in ("corporate", "employees", "team event", "event for"))
        has_location = any(term in lowered for term in ("whitefield", "koramangala", "jp nagar"))
        return has_corporate_signal and not has_location

    @staticmethod
    def _is_partial_transcript(message: str) -> bool:
        lowered = message.lower().strip(" .!?")
        partial_phrases = {
            "we have never",
            "never done",
            "event do you suggest for",
            "what if i ask people what do you suggest",
        }
        if lowered in partial_phrases:
            return True
        if len(lowered.split()) <= 4 and lowered.endswith(("never", "for", "suggest")):
            return True
        return False

    def _invalid_date_answer(self, lowered: str) -> bool:
        if self._is_direct_question(lowered):
            return False
        if self.memory._extract_preferred_date(lowered):
            return False
        if re.fullmatch(r"[a-z]\d+", lowered.strip()):
            return True
        meaningful = re.findall(r"[a-z0-9]+", lowered)
        if len(meaningful) < 2:
            return True
        return any(phrase in lowered for phrase in ("google", "don't know", "not sure"))

    @staticmethod
    def _message_supplied_location(lowered: str) -> bool:
        return any(location in lowered for location in ("whitefield", "koramangala", "jp nagar")) or ("jp" in lowered and "nagar" in lowered)

    @staticmethod
    def _is_beginner_context(lowered: str) -> bool:
        beginner_terms = (
            "never done", "first time", "first-time", "beginner", "no experience",
            "never tried", "none of us have done", "haven't done", "havent done",
            "first timer", "first-timer", "never played", "none of us have played",
            "none of us has played", "new to escape"
        )
        return any(term in lowered for term in beginner_terms)

    @staticmethod
    def _is_direct_question(lowered: str) -> bool:
        stripped = lowered.strip()
        if stripped.endswith("?"):
            return True
        question_words = (
            "what ", "where ", "which ", "how ", "can ", "do ", "does ",
            "tell me ", "parking ", "location ", "locations ", "should we "
        )
        if any(word in lowered for word in question_words):
            return True
        return False

    @staticmethod
    def _message_contains_contact_detail(message: str) -> bool:
        lowered = message.lower()
        has_name = any(phrase in lowered for phrase in ("my name is", "i am", "i'm", "this is"))
        has_phone = bool(re.search(r"(?:(?:\+91[\s-]?)|0)?[6-9]\d{9}\b", message.replace(" ", "")))
        return has_name or has_phone

    @staticmethod
    def _message_has_count(lowered: str) -> bool:
        return bool(
            re.search(
                r"\b\d{1,4}\s*(people|persons|guests|kids|children|adults|participants|players|members|employees)?\b",
                lowered,
            )
        )

    @staticmethod
    def _clean_response(response: str) -> str:
        banned = [
            "I deeply apologize",
            "I completely understand your frustration",
        ]
        cleaned = response.strip()
        cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
        cleaned = re.sub(r"(?im)^\s*thinking\s*\.{0,3}\s*$", "", cleaned).strip()
        cleaned = re.sub(r"(?im)^\s*okay,\s*the customer.*$", "", cleaned).strip()
        cleaned = re.sub(r"(?im)^\s*(analysis|reasoning)\s*:.*$", "", cleaned).strip()
        for phrase in banned:
            cleaned = cleaned.replace(phrase, "I understand")
        if cleaned.startswith("{") and cleaned.endswith("}"):
            try:
                parsed = json.loads(cleaned)
                cleaned = parsed.get("response", cleaned)
            except json.JSONDecodeError:
                pass
        cleaned = re.sub(r"(?im)^(intent|confidence|route|handoff|debug|analysis|reasoning)\s*:.*$", "", cleaned)
        cleaned = " ".join(line.strip() for line in cleaned.splitlines() if line.strip())
        return cleaned

    def _state_snapshot(self) -> dict:
        return {key: value for key, value in self.memory.data.items() if key != "conversation"}

    def _active_flow_intent(self) -> str:
        active_intent = self.memory.data.get("intent", "")
        if not active_intent or active_intent == "general_faq":
            return ""
        if self.memory.missing_fields(intent=active_intent, include_contact=False):
            return active_intent
        return ""

    @staticmethod
    def _should_preserve_active_flow_intent(detected_intent: str, waiting_for: str) -> bool:
        if detected_intent == "general_faq":
            return True
        if waiting_for and detected_intent in {"confirm_booking", "deny_booking"}:
            return False
        return False

    def _pending_offer(self) -> dict | None:
        recommended_option = self.memory.data.get("recommended_option", "")
        room = self.memory.data.get("room", "")
        location = self.memory.data.get("location", "")
        participants = self.memory.data.get("participants", "")
        age_group = self.memory.data.get("age_group", "")
        intent = self.memory.data.get("intent", "")
        if intent == "escape_room_inquiry":
            offer_intent = intent
        elif room or recommended_option or location or participants or age_group:
            offer_intent = "escape_room_inquiry"
        else:
            offer_intent = ""
        if not offer_intent or not (recommended_option or room):
            return None
        return {
            "intent": offer_intent,
            "recommended_option": recommended_option,
            "room": room,
            "location": location,
            "participants": participants,
            "age_group": age_group,
        }

    def _should_accept_current_recommendation(self, message: str, intent: str) -> bool:
        return bool(self._pending_offer()) and intent == "escape_room_inquiry" and self._is_booking_acceptance_followup(message)

    @staticmethod
    def _is_booking_acceptance_followup(message: str) -> bool:
        lowered = message.lower().strip().rstrip(".!?")
        phrases = (
            "book it",
            "reserve it",
            "let's do that",
            "lets do that",
            "go ahead",
            "sounds good",
            "that works",
            "do it",
            "book that",
            "let's go with that",
            "lets go with that",
        )
        return lowered in phrases

    def _is_direct_user_inquiry(self, message: str, intent: str) -> bool:
        lowered = message.lower()
        if intent in ("general_faq", "cancellation_request", "escape_room_inquiry"):
            return True
        if self._is_direct_question(lowered):
            return True
        if self._asks_for_room_suggestion(lowered) or self._asks_for_more_details(lowered):
            return True
        if intent == "corporate_event" and self._asks_for_room_suggestion(lowered):
            return True
        return False

    def _is_qualification_interruption(self, message: str, waiting_before: str, qual_result) -> bool:
        lowered = message.lower()
        if self.qualification_agent._is_garbage_transcript(lowered):
            return False

        if self._is_direct_question(lowered):
            return True

        faq_keywords = [
            "parking", "location", "kids", "children", "food", "cancel", "refund",
            "how long", "duration", "beginner", "first time", "wizarding", "murder",
            "hostage", "puzzle", "puzzles", "fun", "experience", "popular", "which one",
            "suggest", "recommend"
        ]
        if waiting_before == "food_required" and "food" in lowered:
            return self.memory._extract_food_required(lowered) == ""
        if waiting_before == "age_group" and any(k in lowered for k in ["kids", "children", "age"]):
            return not self.memory._extract_age_group(lowered)
        if waiting_before == "location" and ("location" in lowered or any(loc in lowered for loc in ["koramangala", "whitefield", "jp nagar"])):
            return not self.memory._extract_location(lowered)

        if any(kw in lowered for kw in faq_keywords):
            return True

        if waiting_before == "preferred_date" and not self.memory._extract_preferred_date(lowered):
            return True
        if waiting_before == "location" and not self.memory._extract_location(lowered):
            return True
        if waiting_before == "participants" and not self.qualification_agent._is_valid_count(lowered):
            return True
        if waiting_before == "age_group" and not self.memory._extract_age_group(lowered):
            return True
        if waiting_before == "food_required" and self.memory._extract_food_required(lowered) == "":
            return True
        if waiting_before == "budget_range" and not self.qualification_agent._is_valid_budget(lowered):
            return True
        if waiting_before == "customer_name" and not self.qualification_agent._extract_bare_name(message):
            return True
        if waiting_before == "phone" and not self.memory._extract_phone(lowered):
            return True
        return False

    def _get_faq_or_knowledge_answer(self, message: str, intent: str, recommendation) -> str:
        lowered = message.lower()
        consultative = self.conversation_intelligence.get_consultative_selling_reply(lowered)
        if consultative:
            return consultative
        faq_answer = self._answer_faq(lowered)
        if faq_answer:
            return faq_answer
        explicit_answer = self._answer_explicit_intent_question(lowered, intent)
        if explicit_answer:
            return explicit_answer
        return ""

    def _resume_qualification_phrase(self, waiting_before: str) -> str:
        question = self.qualification_agent.QUESTIONS.get(waiting_before, "")
        if not question:
            question = self._ask_for_field(self.memory.data.get("intent", ""), waiting_before)
        
        if waiting_before in ["customer_name", "phone"]:
            transition = "To get those details sent over, "
        else:
            transition = "Coming back to the event planning, "
        
        if question:
            first_char = question[0]
            rest = question[1:]
            if first_char.isupper() and not question.startswith(("JP Nagar", "Koramangala", "Whitefield", "I ")):
                question = first_char.lower() + rest
        
        return f"{transition}{question}"

    def _booking_consent_response(self) -> str:
        name = self.memory.data.get("customer_name", "")
        location = self.memory.data.get("location", "")
        date = self.memory.data.get("preferred_date", "")
        name_phrase = f", {name}" if name else ""
        loc_phrase = f" at {location}" if location else ""
        date_phrase = f" for {date}" if date else ""
        return f"Perfect. I've got everything I need{name_phrase}. Would you like me to check availability{date_phrase}{loc_phrase}?"

    @staticmethod
    def _is_booking_consent_yes(message: str) -> bool:
        lowered = message.lower().strip(" .!?")
        yes_terms = {
            "yes", "yeah", "yep", "sure", "ok", "okay", "go ahead", "please do", "please", 
            "yes please", "yes, please", "sure, go ahead", "do that", "check availability",
            "check", "do it"
        }
        if lowered in yes_terms:
            return True
        if any(lowered.startswith(term + " ") for term in ["yes", "yeah", "sure", "ok", "okay", "please"]):
            return True
        return False

    @staticmethod
    def _is_booking_consent_no(message: str) -> bool:
        lowered = message.lower().strip(" .!?")
        no_terms = {
            "no", "nope", "dont", "don't", "no thanks", "no, thanks", "no thank you", "no, thank you",
            "not now", "later", "not today", "cancel", "stop", "decline"
        }
        if lowered in no_terms:
            return True
        if any(lowered.startswith(term + " ") for term in ["no", "dont", "don't"]):
            return True
        return False

    def _update_discussed_options(self, response_text: str) -> None:
        rooms = [
            "Murder Mystery",
            "Hostage",
            "Prison Break",
            "Classified",
            "Undercover",
            "Bomb Defusal",
            "The Wizarding Championship",
            "Curse of the Pharaoh",
            "The Forbidden Forest",
        ]
        discussed = list(self.memory.data.get("discussed_options", []))
        for room in rooms:
            if room.lower() in response_text.lower():
                if not discussed or discussed[-1] != room:
                    if room in discussed:
                        discussed.remove(room)
                    discussed.append(room)
        self.memory.data["discussed_options"] = discussed[-5:]
        self.memory.save()

    def _check_ollama_availability(self) -> bool:
        try:
            res = subprocess.run(
                ["ollama", "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            )
            if res.returncode == 0:
                res_list = subprocess.run(
                    ["ollama", "list"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1.0,
                )
                return res_list.returncode == 0
        except Exception:
            pass
        return False
