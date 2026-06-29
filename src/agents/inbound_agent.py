from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..core.agent_response import AgentResponse
from ..memory.conversation_memory import ConversationMemory
from ..core.handoff_generator import HandoffGenerator
from ..services.intent_detector import IntentDetector, IntentResult, _BOOKING_TRIGGERS
from ..knowledge.knowledge_loader import KnowledgeBase
from ..knowledge.knowledge_retriever import KnowledgeRetriever
from .qualification_agent import QualificationAgent
from ..services.recommendation_engine import RecommendationEngine
from ..services.gpt_reasoner import GPTReasoner, ReasonerDecision
from ..orchestration.router import Router, RouteDecision
from ..services.conversation_intelligence import ConversationIntelligenceLayer
from ..services.venue_policy import get_venue_policy
from ..core.conversation_modes import ConversationModeDetector
from ..response_composer import ResponseComposer

class InboundAgent:
    def __init__(
        self,
        knowledge_base: KnowledgeBase,
        memory: ConversationMemory,
        prompt_path: str | Path,
        model: str | None = None,
        use_openai: bool | None = None,
        use_ollama: bool | None = None,
        use_reasoner: bool | None = None,
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
        self.reasoner = GPTReasoner(
            model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
            enabled=(bool(os.environ.get("OPENAI_API_KEY")) and os.environ.get("BREAKOUT_GPT_REASONER", "false").lower() == "true")
            if use_reasoner is None
            else use_reasoner,
        )
        self.last_openai_error = ""
        self.last_ollama_error = ""
        self.openai_latency = 0.0

    def _print_instrumentation(self, start_turn: float, source: str = None) -> None:
        import time
        if os.environ.get("BREAKOUT_DEBUG", "false").lower() != "true":
            return
        filled = [k for k, v in self.memory.data.items() if v and k in ("participants", "location", "preferred_date", "customer_name", "phone", "email")]
        missing = [k for k in ("participants", "location", "preferred_date", "customer_name", "phone", "email") if k not in filled]
        next_field = self.qualification_agent._waiting_for or "intent"
        print(f"filled_fields: {filled}")
        print(f"missing_fields: {missing}")
        print(f"next_expected_field: {next_field}")

        if source is not None:
            resolved_source = source
        else:
            if ("openai_failure" in getattr(self, "last_openai_error", "")) or \
               ("openai_failure" in getattr(self.response_composer, "last_error", "")):
                resolved_source = "fallback"
            elif getattr(self, "_last_resolved_source", None) is not None:
                resolved_source = self._last_resolved_source
            elif self.use_openai:
                resolved_source = "openai"
            else:
                resolved_source = "deterministic_template"

        print(f"response_source: {resolved_source}")

        openai_latency = self.openai_latency + getattr(self.response_composer, "last_latency", 0.0)
        print(f"OpenAI latency: {openai_latency:.4f}s")
        print(f"openai_latency_ms: {openai_latency * 1000:.1f}")

        total_latency = time.time() - start_turn
        print(f"total response latency: {total_latency:.4f}s")
        print(f"total_latency_ms: {total_latency * 1000:.1f}")

    def handle_message(self, message: str) -> AgentResponse:
        import time
        message = self.memory.normalize_entity_aliases(message)
        self.start_turn = time.time()
        self.openai_latency = 0.0
        resolved_message = self.conversation_intelligence.process_utterance(message)
        normalized_message = self.memory.normalize_number_words(resolved_message)
        is_education_query = self._is_escape_room_education_query(normalized_message)
        previous_state = self._state_snapshot()
        pending_offer = self._pending_offer()
        waiting_before = self.qualification_agent._waiting_for
        active_flow_intent = self._active_flow_intent()

        if self._should_reset_booking_context(normalized_message, pending_offer):
            self.memory.reset_booking_fields()

        if self._is_partial_transcript(normalized_message):
            response = "Sorry, I didn't catch that. Could you repeat it?"
            self._print_instrumentation(self.start_turn, source="deterministic_template")
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

        if self.memory.data.get("completed_booking"):
            faq_answer = self._get_faq_or_knowledge_answer(normalized_message, "general_faq", None)
            response = faq_answer or "Great. Thanks for choosing Breakout. Have a wonderful day."
            response = self._clean_response(response)
            self.memory.add_turn("agent", response)
            self._print_instrumentation(self.start_turn, source="deterministic_template")
            return AgentResponse(
                response=response,
                intent="general_faq",
                intent_confidence=1.0,
                next_agent="booking_agent",
                should_handoff=False,
                state=self.memory.as_state(),
                missing_fields=[],
                recommendation={"option": "", "reason": ""},
                qualification={"qualified": True, "missing_fields": [], "next_question": "", "summary": {}},
                handoff_summary=None,
                booking=None,
                debug={
                    "previous_state": previous_state,
                    "extracted_entities": {},
                    "updated_state": self._state_snapshot(),
                    "missing_slots": [],
                }
            )

        # Handle pending confirmation response (yes/no to a deferred low-confidence update)
        pending_conf = self.memory.data.get("pending_confirmation")
        if pending_conf:
            lowered = normalized_message.lower().strip(" .!?")
            yes_terms = {"yes", "yeah", "yep", "sure", "ok", "okay", "go ahead", "please do", "please", 
                         "yes please", "yes, please", "correct", "that's correct", "that's right", "right",
                         "indeed", "absolutely", "definitely", "perfect"}
            no_terms = {"no", "keep it", "no that's not right", "nope", "nah", "incorrect", "wrong", "not right"}
            
            pending_field = str(pending_conf.get("field", ""))
            pending_new = str(pending_conf.get("new_value", "")).strip().lower()
            restated_value = ""
            if pending_field == "preferred_date":
                restated_value = self.memory._extract_preferred_date(normalized_message).strip().lower()
            elif pending_field == "age_group":
                restated_value = self.memory._extract_age_group(normalized_message.lower())[0].strip().lower()
            elif pending_field == "location":
                restated_value = self.memory._extract_location(normalized_message.lower()).strip().lower()
            elif pending_field == "room":
                restated_value = self.memory._extract_room(normalized_message.lower()).strip().lower()
            elif pending_field == "participants":
                restated_value = str(self.memory._extract_participants(normalized_message.lower())).strip().lower()
            has_affirmative_prefix = bool(
                re.match(r"^(?:yes|yeah|yep|sure|correct|right|okay|ok|please)\b", lowered)
            )
            is_yes = (
                any(term == lowered or (term in lowered and len(lowered.split()) <= 3) for term in yes_terms)
                or (has_affirmative_prefix and bool(restated_value) and restated_value == pending_new)
            )
            is_no = any(term == lowered or (term in lowered and len(lowered.split()) <= 3) for term in no_terms)
            
            if is_yes or is_no:
                if is_yes:
                    field = pending_conf["field"]
                    new_val = pending_conf["new_value"]
                    old_val = pending_conf["old_value"]
                    update_type = pending_conf["type"]
                    self.memory.data[field] = new_val
                    if "audit_trail" not in self.memory.data or not isinstance(self.memory.data["audit_trail"], list):
                        self.memory.data["audit_trail"] = []
                    self.memory.data["audit_trail"].append({
                        "field": field,
                        "old_value": old_val,
                        "new_value": new_val,
                        "type": update_type
                    })
                    prefix = f"Great, I've updated that to {new_val}."
                else:
                    prefix = "No problem, keeping it as before."
                
                self.memory.data["pending_confirmation"] = None
                self.memory.save()
                
                active_intent = self.memory.data.get("intent", "escape_room_inquiry")
                qual_result = self.qualification_agent.qualify(active_intent)
                handoff_ready = self.memory.handoff_ready(active_intent)
                
                if handoff_ready:
                    self.memory.data["current_workflow"] = "awaiting_booking"
                    self.memory.data["booking_consent_pending"] = True
                    self.memory.save()
                    response = f"{prefix} {self._booking_consent_response()}"
                else:
                    response = f"{prefix} {qual_result.next_question}"
                
                response = self._clean_response(response)
                self.memory.add_turn("agent", response)
                self._print_instrumentation(self.start_turn, source="deterministic_template")
                
                return AgentResponse(
                    response=response,
                    intent=active_intent,
                    intent_confidence=1.0,
                    next_agent="booking_agent" if handoff_ready else "qualification_agent",
                    should_handoff=False,
                    state=self.memory.as_state(),
                    missing_fields=qual_result.missing_fields,
                    recommendation={"option": "", "reason": ""},
                    qualification={
                        "qualified": qual_result.qualified,
                        "missing_fields": qual_result.missing_fields,
                        "next_question": qual_result.next_question,
                        "summary": qual_result.summary,
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
            else:
                # User typed something else - discard confirmation request and continue normal turn processing
                self.memory.data["pending_confirmation"] = None
                self.memory.save()

        core_fields_before = {
            "intent": self.memory.data.get("intent", ""),
            "location": self.memory.data.get("location", ""),
            "room": self.memory.data.get("room", ""),
            "participants": self.memory.data.get("participants", ""),
            "age_group": self.memory.data.get("age_group", ""),
            "experience_level": self.memory.data.get("experience_level", ""),
            "company_size": self.memory.data.get("company_size", ""),
        }

        # Determine active flow intent and check for interruption
        active_intent_stored = self.memory.data.get("intent", "")

        is_interruption = False
        if waiting_before and active_intent_stored and active_intent_stored != "general_faq":
            is_interruption = (
                any(
                    phrase in normalized_message.lower()
                    for phrase in (
                        "cancellation policy", "cancel policy", "cancellation charges",
                        "refund policy", "what if i cancel", "how does cancellation",
                    )
                ) or
                self._is_rules_or_rooms_faq(normalized_message) or
                self._is_qualification_interruption(normalized_message, waiting_before, None)
            )

        if is_interruption:
            intent_result = self.intent_detector.detect(
                normalized_message,
                active_intent_stored,
                pending_offer=pending_offer if not waiting_before else None,
            )
            # Check if there is an explicit clear topic switch (e.g. to a different qualification intent)
            is_switch = False
            if (
                intent_result.intent not in ("general_faq", active_intent_stored)
                and self.intent_detector._is_clear_topic_switch(normalized_message, intent_result.intent)
            ):
                specials = {"corporate_event", "birthday_party", "bachelor_party", "farewell_party", "couple_event", "virtual_event"}
                if active_intent_stored in specials and intent_result.intent == "escape_room_inquiry":
                    is_switch = False
                else:
                    is_switch = True

            if is_switch:
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

        if (
            waiting_before
            and intent_result.intent == "cancellation_request"
            and active_intent_stored
            and any(
                phrase in normalized_message.lower()
                for phrase in (
                    "cancellation policy", "cancel policy", "cancellation charges",
                    "refund policy", "what if i cancel", "how does cancellation",
                )
            )
        ):
            is_interruption = True
            active_intent = active_intent_stored

        if is_interruption:
            intent_result_final = IntentResult("general_faq", 0.72, "faq interruption")
            extracted_entities = self.memory.merge_message(normalized_message, "")
        else:
            intent_result_final = intent_result
            extracted_entities = dict(intent_result.entities)
            extracted_entities.update(self.memory.merge_message(normalized_message, active_intent))

        recommendation = self._resolve_recommendation(normalized_message)
        explicit_recommendation_request = self._is_explicit_recommendation_request(normalized_message.lower())
        recommendation_rejection = (
            self._is_recommendation_rejection(message.lower())
            or self._is_recommendation_rejection(normalized_message.lower())
        )
        switched_branch_after_recommendation = bool(
            (pending_offer or {}).get("recommended_option")
            and core_fields_before.get("location")
            and self.memory.data.get("location") != core_fields_before.get("location")
            and re.match(r"^\s*(?:no|nah|nope)\b", normalized_message, re.IGNORECASE)
        )
        if switched_branch_after_recommendation:
            rejected = self.memory.data.setdefault("rejected_options", [])
            previous_option = str((pending_offer or {}).get("recommended_option") or "")
            if isinstance(rejected, list) and previous_option and previous_option not in rejected:
                rejected.append(previous_option)
                self.memory.data["rejected_options"] = rejected[-8:]
                self.memory.save()
        if recommendation_rejection:
            previous_room = core_fields_before.get("room", "")
            if self.memory.data.get("room") != previous_room:
                self.memory.data["room"] = previous_room
                extracted_entities.pop("room", None)
                self.memory.save()
        if recommendation.option and not is_interruption and explicit_recommendation_request and not recommendation_rejection:
            extracted_entities.update(
                self.memory.merge_message(normalized_message, active_intent, recommendation.option)
            )
        accepted_recommendation = self._commit_recommended_room_if_accepted(normalized_message, active_intent)
        if accepted_recommendation:
            active_intent = "escape_room_inquiry"
            self.memory.data["intent"] = active_intent
            self.memory.data["booking_consent_pending"] = False
            self.memory.save()
        if self.memory.data.get("room"):
            self.memory.data["recommended_option"] = self.memory.data["room"]
            self.memory.save()

        explicit_room_change = any(
            term in normalized_message.lower()
            for term in ("instead", "switch", "change", "i want", "i prefer", "rather")
        )
        selected_room = str(self.memory.data.get("room", ""))
        if explicit_room_change and selected_room:
            self.memory.data["recommended_option"] = selected_room
            unique_room_locations = {
                "Classified": "Koramangala",
                "Bomb Defusal": "Whitefield",
                "Prison Break": "JP Nagar",
                "The Wizarding Championship": "Koramangala",
                "The Forbidden Forest": "Koramangala",
                "Curse of the Pharaoh": "Koramangala",
            }
            if not self.memory.data.get("location") and selected_room in unique_room_locations:
                self.memory.data["location"] = unique_room_locations[selected_room]
            self.memory.save()

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
                draft_resp = "Perfect, I'll check that now."
                response = self._compose_customer_response(
                    draft_resp, normalized_message, intent_result_final.intent
                )
                if "openai_failure" in self.response_composer.last_error:
                    source = "fallback"
                    response = draft_resp
                elif response != draft_resp:
                    source = "openai"
                else:
                    source = "deterministic_template"
                response = self._clean_response(response)
                self._update_discussed_options(response)
                self.memory.add_turn("agent", response)
                
                handoff = self.handoff_generator.generate(self.memory.data)
                
                # Check for LangGraph booking node handler
                booking_result = None
                try:
                    from ..integrations.langgraph.booking_node import booking_node_handler
                    booking_result = booking_node_handler(handoff, require_payment=False)
                except Exception as exc:
                    booking_result = {"status": "error", "reason": str(exc)}
                
                self._print_instrumentation(self.start_turn, source=source)
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
                draft_resp = "No problem at all. Feel free to call us when you're ready to book, or I can answer any other questions you might have."
                response = self._compose_customer_response(
                    draft_resp, normalized_message, intent_result_final.intent
                )
                if "openai_failure" in self.response_composer.last_error:
                    source = "fallback"
                    response = draft_resp
                elif response != draft_resp:
                    source = "openai"
                else:
                    source = "deterministic_template"
                response = self._clean_response(response)
                self._update_discussed_options(response)
                self.memory.add_turn("agent", response)
                self._print_instrumentation(self.start_turn, source=source)
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
        if is_education_query:
            # Education is a complete FAQ turn. Do not create qualification
            # ownership or a hidden waiting field before the customer asks to proceed.
            qual_result = self.qualification_agent.qualify(active_intent)
            self.qualification_agent._waiting_for = waiting_before
        elif not is_interruption:
            qual_result = self.qualification_agent.update_and_qualify(normalized_message, active_intent)
        else:
            qual_result = self.qualification_agent.qualify(active_intent)
            self.qualification_agent._waiting_for = waiting_before

        handoff_ready = self.memory.handoff_ready(active_intent)

        core_fields_after = {
            "intent": self.memory.data.get("intent", ""),
            "location": self.memory.data.get("location", ""),
            "room": self.memory.data.get("room", ""),
            "participants": self.memory.data.get("participants", ""),
            "age_group": self.memory.data.get("age_group", ""),
            "experience_level": self.memory.data.get("experience_level", ""),
            "company_size": self.memory.data.get("company_size", ""),
        }
        core_changed = any(core_fields_before[k] != core_fields_after[k] for k in core_fields_before)

        # Check if a new pending confirmation was created during this turn
        new_pending_conf = self.memory.data.get("pending_confirmation")
        if new_pending_conf:
            field = new_pending_conf["field"]
            new_val = new_pending_conf["new_value"]
            old_val = new_pending_conf["old_value"]
            
            field_names = {
                "room": "room",
                "location": "location",
                "participants": "player count",
                "preferred_date": "date",
                "age_group": "age group",
            }
            friendly_field = field_names.get(field, field)
            
            if field == "participants":
                response = f"I noticed you mentioned {new_val} players instead of {old_val}. Did you want to update the player count?"
            elif field == "preferred_date":
                response = f"I noticed you mentioned {new_val} instead of {old_val} for the date. Did you want to change the date?"
            elif field == "room":
                response = f"I noticed you mentioned {new_val} instead of {old_val} for the game. Did you want to switch to {new_val}?"
            else:
                response = f"I noticed you mentioned {new_val} instead of {old_val} for the {friendly_field}. Did you want to change that?"
                
            response = self._clean_response(response)
            self.memory.add_turn("agent", response)
            self._print_instrumentation(self.start_turn, source="deterministic_template")
            
            return AgentResponse(
                response=response,
                intent=active_intent,
                intent_confidence=1.0,
                next_agent="inbound_agent",
                should_handoff=False,
                state=self.memory.as_state(),
                missing_fields=qual_result.missing_fields,
                recommendation={"option": "", "reason": ""},
                qualification={
                    "qualified": qual_result.qualified,
                    "missing_fields": qual_result.missing_fields,
                    "next_question": qual_result.next_question,
                    "summary": qual_result.summary,
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

        route = self.router.route(active_intent, qual_result.qualified)
        if route.should_handoff and not handoff_ready:
            route = RouteDecision(
                "inbound_agent",
                "Booking prerequisites are incomplete.",
                False,
            )
        updated_state = self._state_snapshot()
        direct_answer_handled = False
        direct_answer = self._direct_answer_guard(
            normalized_message,
            active_intent,
            recommendation,
            waiting_before,
        )
        if direct_answer:
            reasoner_decision = None
            response = direct_answer
            route = RouteDecision("inbound_agent", "Direct question answered before qualification.", False)
            self.memory.data["conversation_mode"] = "faq"
            self.memory.data["last_discussed_topic"] = self._direct_answer_topic(normalized_message)
            self.memory.save()
            direct_answer_handled = True
        elif is_education_query:
            reasoner_decision = None
            response = self._escape_room_education_response(normalized_message)
            route = RouteDecision("inbound_agent", "Escape-room education handled before qualification.", False)
            self.memory.data["conversation_mode"] = "faq"
            self.memory.data["last_discussed_topic"] = "escape_room_education"
            self.memory.save()
        else:
            reasoner_decision = self._reasoner_decision(
                normalized_message,
                active_intent,
                qual_result,
                waiting_before,
            )
            response = self._response_from_reasoner_decision(
                reasoner_decision,
                normalized_message,
                active_intent,
                recommendation,
                qual_result,
                waiting_before,
                handoff_ready,
            )
            if not response:
                response = self._generate_response(
                    normalized_message, active_intent, recommendation, handoff_ready, qual_result, waiting_before, core_changed=core_changed
                )

        if self.memory.data.get("booking_consent_pending"):
            route = RouteDecision("inbound_agent", "Booking consent pending.", False)

        response = self._clean_response(response)
        if recommendation_rejection and self.memory.data.get("room") != core_fields_before.get("room", ""):
            self.memory.data["room"] = core_fields_before.get("room", "")
            self.memory.save()

        response_mode = str(self.memory.data.get("conversation_mode", ""))
        if response_mode == "faq" and not waiting_before:
            route = RouteDecision("inbound_agent", "Direct FAQ handled by inbound agent.", False)

        # Auto-route budget/pricing queries to booking_agent when user opted-in for booking flow
        handoff = self.handoff_generator.generate(self.memory.data) if handoff_ready else None
        if (
            not direct_answer_handled
            and
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
                    from ..integrations.langgraph.booking_node import booking_node_handler
                    # Require payment could be decided from intent or memory; default False here.
                    booking_result = booking_node_handler(handoff, require_payment=False)
                except Exception as exc:
                    booking_result = {"status": "error", "reason": str(exc)}
        except Exception:
            # Defensive: do not let booking integration break the main flow
            booking_result = {"status": "error", "reason": "booking_integration_failure"}

        self._update_discussed_options(response)
        self.memory.add_turn("agent", response)

        self._print_instrumentation(self.start_turn)

        return AgentResponse(
            response=response,
            intent=active_intent,
            intent_confidence=intent_result_final.confidence,
            next_agent=route.next_agent,
            should_handoff=route.should_handoff,
            state=self.memory.as_state(),
            missing_fields=qual_result.missing_fields,
            recommendation={
                "option": recommendation.option if explicit_recommendation_request else "",
                "reason": recommendation.reason if explicit_recommendation_request else "",
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
                "reasoner_decision": reasoner_decision.__dict__ if reasoner_decision else {},
            },
        )

    def _reasoner_decision(self, message: str, intent: str, qual_result, waiting_before: str) -> ReasonerDecision | None:
        pending_question = ""
        if waiting_before:
            pending_question = self._ask_for_field(intent, waiting_before)
        elif qual_result is not None:
            pending_question = getattr(qual_result, "next_question", "") or getattr(qual_result, "response", "")
        try:
            decision = self.reasoner.decide(
                message=message,
                memory=self.memory.data,
                pending_question=pending_question,
                available_actions=self._reasoner_available_actions(),
            )
        except Exception as exc:
            if hasattr(self.reasoner, "last_error"):
                self.reasoner.last_error = f"reasoner_failure: {exc}"
            return None
        if decision and getattr(decision, "protected_updates", None):
            decision.protected_updates = {}
        return decision

    @staticmethod
    def _reasoner_available_actions() -> list[str]:
        return [
            "answer_faq",
            "answer_rules",
            "answer_policy",
            "answer_parking",
            "answer_food",
            "answer_location",
            "answer_discount",
            "explain_room",
            "compare_rooms",
            "recommend_room",
            "recommend_location",
            "ask_missing_information",
            "check_availability",
            "prepare_booking",
            "complete_booking",
            "resume_qualification",
            "end_conversation",
            "repeat_previous_question",
        ]

    def _response_from_reasoner_decision(
        self,
        decision: ReasonerDecision | None,
        message: str,
        intent: str,
        recommendation,
        qual_result,
        waiting_before: str,
        handoff_ready: bool,
    ) -> str:
        if not decision:
            return ""
        action = decision.action
        if action in {"prepare_booking", "complete_booking"}:
            return ""
        if action == "check_availability":
            return self._booking_consent_response() if handoff_ready else ""
        if action == "end_conversation":
            return "Thank you for contacting Breakout. Have a great day."
        if action == "repeat_previous_question":
            return self._repeat_previous_question(intent, waiting_before, qual_result)
        if action in {
            "answer_faq",
            "answer_rules",
            "answer_policy",
            "answer_parking",
            "answer_food",
            "answer_location",
            "answer_discount",
        }:
            answer = self._answer_from_reasoner_topic(action, decision.topic or message, intent, recommendation)
            return self._append_reasoner_resume(answer, decision, waiting_before) if answer else ""
        if action == "answer_unknown_question":
            # The customer asked something we don't have in knowledge.
            # Be honest rather than ignoring or hallucinating.
            resume = self._resume_qualification_phrase(waiting_before) if waiting_before else ""
            uncertain = "I'm not sure about that — our team would be the best people to help."
            if resume:
                return f"{uncertain} {resume}"
            return uncertain
        if action == "explain_room":
            room = decision.room or self.memory.data.get("room") or self.memory.data.get("recommended_option", "")
            answer = self._answer_room_name(str(room).lower()) or self._room_details_response(str(room))
            return self._append_reasoner_resume(answer, decision, waiting_before) if answer else ""
        if action == "compare_rooms":
            option = self.memory.data.get("recommended_option") or decision.room or "Murder Mystery and Hostage"
            answer = self._comparison_response(str(option)) or self._contextual_best_response()
            return self._append_reasoner_resume(answer, decision, waiting_before) if answer else ""
        if action == "recommend_room":
            option = decision.room or recommendation.option or self.memory.data.get("recommended_option", "")
            if not option:
                return ""
            answer = self._recommendation_response(intent, str(option), decision.reason or getattr(recommendation, "reason", ""), message.lower())
            return self._append_reasoner_resume(answer, decision, waiting_before) if answer else ""
        if action == "recommend_location":
            previous_topic = self.memory.data.get("last_discussed_topic", "")
            self.memory.data["last_discussed_topic"] = "locations"
            answer = self._contextual_best_response()
            self.memory.data["last_discussed_topic"] = previous_topic
            return self._append_reasoner_resume(answer, decision, waiting_before) if answer else ""
        if action in {"ask_missing_information", "resume_qualification"}:
            return self._repeat_previous_question(intent, waiting_before, qual_result)
        return ""

    def _answer_from_reasoner_topic(self, action: str, topic: str, intent: str, recommendation) -> str:
        topic_text = topic.lower()
        query_by_action = {
            "answer_rules": "how does it work",
            "answer_policy": "cancellation policy",
            "answer_parking": "parking",
            "answer_food": "food options",
            "answer_location": "what locations do you have",
            "answer_discount": "discount",
        }
        query = query_by_action.get(action, topic_text)
        answer = self._get_faq_or_knowledge_answer(query, intent, recommendation)
        if answer:
            return answer
        if action == "answer_discount":
            return "I don't currently have discount information, but I can connect you with the appropriate team."
        return ""

    def _append_reasoner_resume(self, answer: str, decision: ReasonerDecision, waiting_before: str) -> str:
        if not answer:
            return ""
        if decision.should_resume_qualification and waiting_before:
            resume = self._resume_qualification_phrase(waiting_before)
            if resume and resume.lower() not in answer.lower():
                return f"{answer} {resume}"
        return answer

    def _repeat_previous_question(self, intent: str, waiting_before: str, qual_result) -> str:
        if waiting_before:
            return f"No worries. {self._ask_for_field(intent, waiting_before)}"
        if qual_result is not None:
            question = getattr(qual_result, "next_question", "") or getattr(qual_result, "response", "")
            if question:
                return f"No worries. {question}"
        for turn in reversed(self.memory.data.get("conversation", [])):
            if turn.get("role") == "agent" and turn.get("content"):
                return f"No worries. {turn['content']}"
        return "No worries. Could you repeat that?"

    def _generate_response(self, message: str, intent: str, recommendation, should_handoff: bool, qual_result=None, waiting_before: str = "", core_changed: bool = False) -> str:
        if (
            (intent == "escape_room_inquiry" or self.memory.data.get("intent") == "escape_room_inquiry")
            and self._is_booking_acceptance_followup(message)
            and not self.memory.data.get("room")
            and not self.memory.data.get("recommended_option")
        ):
            return "I haven't picked a room yet. Do you already have a room in mind, or would you like a recommendation?"

        response = self._fallback_response(message, intent, recommendation, should_handoff, qual_result, waiting_before, core_changed=core_changed)
        if response:
            composed = self._compose_customer_response(response, message, intent)
            if "openai_failure" in self.response_composer.last_error:
                self._last_resolved_source = "fallback"
                return response
            elif composed != response:
                self._last_resolved_source = "openai"
                return composed
            else:
                self._last_resolved_source = "deterministic_template"
                return response

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
                if "openai_failure" in self.last_openai_error:
                    self._last_resolved_source = "fallback"
                    deterministic = self._fallback_response(
                        message, intent, recommendation, should_handoff, qual_result, waiting_before, core_changed=core_changed
                    )
                    return deterministic or "I don't have that detail to hand right now, but our team can help. May I take your name and number so someone can call you back?"
                elif llm_response:
                    self._last_resolved_source = "openai"
                    return llm_response
                else:
                    self._last_resolved_source = "deterministic_template"
            elif self.use_ollama:
                llm_response = self._call_ollama(prompt)
                if "timeout" in self.last_ollama_error or self.last_ollama_error:
                    self._last_resolved_source = "fallback"
                    deterministic = self._fallback_response(
                        message, intent, recommendation, should_handoff, qual_result, waiting_before, core_changed=core_changed
                    )
                    return deterministic or "I don't have that detail to hand right now, but our team can help. May I take your name and number so someone can call you back?"
                elif llm_response:
                    self._last_resolved_source = "openai"
                    return llm_response
                else:
                    self._last_resolved_source = "deterministic_template"

        fallback = "I don't have that detail to hand right now, but our team can help. May I take your name and number so someone can call you back?"
        composed = self._compose_customer_response(fallback, message, intent)
        if "openai_failure" in self.response_composer.last_error:
            self._last_resolved_source = "fallback"
            return fallback
        elif composed != fallback:
            self._last_resolved_source = "openai"
            return composed
        else:
            self._last_resolved_source = "deterministic_template"
            return fallback

    def _resolve_recommendation(self, message: str):
        """Keep recommendation turns deterministic even if an engine call is unavailable."""
        try:
            recommendation = self.recommender.recommend(message, self.memory.data)
        except Exception:
            recommendation = None
        if getattr(recommendation, "option", ""):
            return recommendation
        return RecommendationEngine.deterministic_fallback(message, self.memory.data)

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
        import time as _time
        api_key = os.environ.get("OPENAI_API_KEY")
        enabled = bool(api_key) and self.use_openai
        debug = os.environ.get("BREAKOUT_DEBUG", "false").lower() == "true"
        if debug:
            print(f"OPENAI_ENABLED={enabled}")
            print(f"MODEL={self.model}")

        if not enabled:
            self.last_openai_error = "missing_api_key"
            self.openai_latency = 0.0
            return ""

        start_time = _time.perf_counter()
        try:
            try:
                import openai
                OpenAI = getattr(openai, "OpenAI", None)
                APITimeoutError = getattr(openai, "APITimeoutError", Exception)
                APIConnectionError = getattr(openai, "APIConnectionError", Exception)
                RateLimitError = getattr(openai, "RateLimitError", Exception)
                AuthenticationError = getattr(openai, "AuthenticationError", Exception)
            except (ImportError, AttributeError):
                OpenAI = None
                APITimeoutError = Exception
                APIConnectionError = Exception
                RateLimitError = Exception
                AuthenticationError = Exception

            if OpenAI is None:
                raise RuntimeError("openai package not installed")

            client = OpenAI(api_key=api_key, timeout=2.0, max_retries=0)
            if hasattr(client, "chat") and hasattr(client.chat, "completions"):
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=150,
                )
                text = response.choices[0].message.content or ""
            elif hasattr(client, "responses"):
                response = client.responses.create(
                    model=self.model,
                    input=prompt,
                )
                text = getattr(response, "output_text", "")
            else:
                raise AttributeError("Mock OpenAI client has neither chat.completions nor responses.")
            self.last_openai_error = ""
            self.openai_latency = _time.perf_counter() - start_time
            if debug:
                print(f"OpenAI latency per request: {self.openai_latency:.4f}s")
                print("response_source=openai")
            return text.strip()
        except (APITimeoutError, APIConnectionError, RateLimitError, AuthenticationError) as exc:
            self.openai_latency = _time.perf_counter() - start_time
            self.last_openai_error = f"openai_failure: {exc}"
            self.use_openai = False
            self.response_composer.enabled = False
            if debug:
                print(f"OpenAI error: {type(exc).__name__}: {exc}")
                print(f"OpenAI latency per request: {self.openai_latency:.4f}s")
                print("response_source=fallback")
            return ""
        except Exception as exc:
            self.openai_latency = _time.perf_counter() - start_time
            self.last_openai_error = f"openai_failure: {exc}"
            self.use_openai = False
            self.response_composer.enabled = False
            if debug:
                print(f"OpenAI error: {type(exc).__name__}: {exc}")
                print(f"OpenAI latency per request: {self.openai_latency:.4f}s")
                print("response_source=fallback")
            return ""

    def _should_reset_booking_context(self, message: str, pending_offer: dict | None = None) -> bool:
        if self._is_booking_acceptance_followup(message):
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

    def _direct_answer_guard(self, message: str, intent: str, recommendation, waiting_before: str = "") -> str:
        lowered = message.lower()
        location_known = bool(str(self.memory.data.get("location", "")).strip())

        if self._is_booking_acceptance_followup(message):
            return ""

        experience = str(self.memory.data.get("experience_level") or "")
        challenge = str(self.memory.data.get("challenge_preference") or "")
        if intent == "escape_room_inquiry" and experience == "experienced" and re.search(
            r"\b(?:played before|done (?:one|escape rooms?) before|experienced)\b", lowered
        ):
            if not location_known:
                return "Got it, I'll skip the first-timer filter. Which branch works best for you?"
        if intent == "escape_room_inquiry" and challenge == "challenging" and not location_known:
            return "I'll prioritize a more difficult room. Which branch works best for you?"

        mentioned_location = self.memory._extract_location(lowered)
        if (
            intent == "escape_room_inquiry"
            and location_known
            and mentioned_location
            and (experience == "experienced" or challenge == "challenging")
            and getattr(recommendation, "option", "")
        ):
            option = str(recommendation.option)
            self.memory.set_field(
                "recommended_option",
                option,
                message,
                expected_field="recommended_option",
            )
            self._update_discussed_options(option)
            self.memory.save()
            return f"At {self.memory.data['location']}, I'd recommend {option}. {recommendation.reason}"

        # Bug 1 & 3: Availability questions must be answered before any qualification.
        from ..orchestration.conversation_manager import ConversationManager
        if ConversationManager._is_availability_question(message):
            if not location_known:
                return self._append_direct_answer_resume(
                    "Sure. Which location would you like me to check?", ""
                )
            # Location known → routing should handle it, but acknowledge here
            return self._append_direct_answer_resume(
                "Let me check that. Which location are you looking at?", ""
            )

        # Bug 2: Room comparison questions must not name specific rooms without location.
        answer = ""
        if self._is_customer_frustration_repair(lowered):
            answer = (
                "You're right, I missed what you were asking. Tell me the specific part you want fixed, "
                "and I'll stay with that."
            )
        elif self._is_slot_comparison_question(lowered):
            answer = self._slot_comparison_response(message)
        elif self._is_room_comparison_question(lowered):
            answer = self._direct_room_comparison_response(lowered)
        elif self._is_location_comparison_question(lowered):
            answer = self._direct_location_recommendation_response(lowered, intent)
        elif self._is_horror_or_thrill_question(lowered):
            answer = self._horror_or_thrill_response(lowered)
        elif self._is_budget_or_price_question(lowered):
            answer = self._budget_or_price_response(lowered, intent)
        elif self._is_confusion_or_escape_room_education_question(lowered):
            answer = (
                "An escape room is a themed team game. You enter a mission, search for clues, "
                "solve puzzles together, and the staff brief you before the game and monitor it if you need help."
            )

        if not answer:
            # Couples are standard two-player escape-room bookings. Give the
            # useful room guidance immediately only when this was not a direct FAQ.
            if self.memory.data.get("relationship") == "couple":
                if location_known:
                    return (
                        "Nice. I'd start with Murder Mystery for story and teamwork. "
                        "Hostage is the more urgent option if you want extra pressure."
                    )
                return (
                    "Nice. I'd start with Murder Mystery for story and teamwork. "
                    "Hostage is the more urgent option if you want extra pressure. Which location would you like to visit?"
                )
            return ""
        return self._append_direct_answer_resume(answer, waiting_before)

    def _append_direct_answer_resume(self, answer: str, waiting_before: str) -> str:
        if not waiting_before:
            return answer
        resume = self._resume_qualification_phrase(waiting_before)
        if not resume or resume.lower() in answer.lower():
            return answer
        return f"{answer} {resume}"

    @staticmethod
    def _direct_answer_topic(message: str) -> str:
        lowered = message.lower()
        if any(term in lowered for term in ("scary", "horror", "spooky", "thrill", "intense")):
            return "thrill_preference"
        if any(term in lowered for term in ("budget", "price", "cost", "discount", "coupon", "promo", "offer", "expensive")):
            return "pricing"
        if any(term in lowered for term in ("slot", "3:15", "5:30")) or re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", lowered):
            return "slot_comparison"
        if "location" in lowered or "branch" in lowered:
            return "location_recommendation"
        if "confused" in lowered or "escape room" in lowered:
            return "escape_room_education"
        if any(term in lowered for term in ("not what i asked", "not understanding", "no no no")):
            return "repair"
        return "direct_answer"

    @staticmethod
    def _is_customer_frustration_repair(lowered: str) -> bool:
        return bool(
            re.search(
                r"\b(?:no\s+no\s+no|not\s+what\s+i\s+asked|you(?:'re| are)?\s+not\s+understanding|"
                r"you\s+don't\s+understand|you\s+are\s+not\s+listening|stop\s+asking)\b",
                lowered,
            )
        )

    @staticmethod
    def _is_confusion_or_escape_room_education_question(lowered: str) -> bool:
        return bool(
            ("confused" in lowered and ("escape room" in lowered or "inside" in lowered or "happens" in lowered))
            or re.search(r"\bwhat\s+(?:exactly\s+)?happens\s+(?:inside|in)\b", lowered)
            or re.search(r"\bwhat\s+do\s+we\s+(?:actually\s+)?do\s+(?:inside|there)\b", lowered)
            or "how does an escape room work" in lowered
            or "how do escape rooms work" in lowered
            or "what is an escape room" in lowered
            or "what are escape rooms" in lowered
        )

    @staticmethod
    def _is_horror_or_thrill_question(lowered: str) -> bool:
        has_preference = bool(re.search(r"\b(?:scary|horror|spooky|thrill|thrilling|intense|adrenaline|not too easy)\b", lowered))
        is_question_or_request = "?" in lowered or bool(
            re.search(r"\b(?:do you have|which|what|recommend|suggest|want|looking for|need)\b", lowered)
        )
        return has_preference and is_question_or_request

    def _horror_or_thrill_response(self, lowered: str) -> str:
        location = str(self.memory.data.get("location", "")).strip().lower()
        if "horror" in lowered or "scary" in lowered or "spooky" in lowered:
            base = "We don't position the rooms as horror or jump-scare experiences."
        else:
            base = "If you want more thrill, I'd steer you toward suspense and pressure rather than the easiest room."

        if location == "whitefield":
            option = "At Whitefield, Bomb Defusal is the stronger pressure option, and Undercover is more story-led suspense."
        elif location == "koramangala":
            option = "At Koramangala, Classified is the sharper challenge, and Undercover is stronger for story-led suspense."
        elif location == "jp nagar":
            option = "At JP Nagar, Missile Attack is the stronger mission-style option, while Hostage adds urgency."
        else:
            option = "Once I know the location, I can point you to the more suspenseful or higher-pressure options there."
        return f"{base} {option}"

    @staticmethod
    def _is_budget_or_price_question(lowered: str) -> bool:
        return bool(
            re.search(
                r"\b(?:budget|price|pricing|cost|costs|rate|rates|how much|expensive|discount|discounts|"
                r"coupon|promo|deal|student discount|group discount)\b",
                lowered,
            )
        ) and ("?" in lowered or re.search(r"\b(?:explain|what|any|is there|do you have|worried|concern|too)\b", lowered))

    @staticmethod
    def _budget_or_price_response(lowered: str, intent: str) -> str:
        if "discount" in lowered or "coupon" in lowered or "promo" in lowered or "offer" in lowered or "deal" in lowered:
            return (
                "I don't have confirmed discount information from the booking system right now, "
                "so I don't want to promise an offer. I can still help narrow the room, date, and location, "
                "and the team can confirm the final amount."
            )
        if "birthday" in lowered or intent == "birthday_party":
            return (
                "Totally fair to watch the budget. The simplest path is escape-room only; the fuller birthday path "
                "can add food or activity support. The final amount depends on guest count, location, date, and package."
            )
        return (
            "Pricing depends on location, date, room, and group size. I don't want to guess the final amount, "
            "but I can help narrow the booking details and the team can confirm the exact price."
        )

    @staticmethod
    def _is_slot_comparison_question(lowered: str) -> bool:
        slot_mentions = re.findall(r"\b\d{1,2}:\d{2}\s*(?:am|pm)?\b|\b\d{1,2}\s*(?:am|pm)\b", lowered)
        has_two_times = len(slot_mentions) >= 2
        has_comparison_language = bool(
            "?" in lowered
            or re.search(r"\b(?:should|choose|take|better|safer|compare|which)\b", lowered)
        )
        return has_two_times and has_comparison_language

    @staticmethod
    def _slot_comparison_response(message: str) -> str:
        raw_slots = re.findall(r"\b\d{1,2}:\d{2}\s*(?:am|pm)?\b|\b\d{1,2}\s*(?:am|pm)\b", message.lower())
        slots = [slot.strip().upper().replace(" ", " ") for slot in raw_slots[:2]]
        if len(slots) < 2:
            return "If timing is tight, choose the later slot so you have buffer for arrival and briefing."

        def slot_key(slot: str) -> tuple[int, int]:
            match = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?", slot, re.IGNORECASE)
            if not match:
                return (0, 0)
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            period = (match.group(3) or "").upper()
            if period == "PM" and hour != 12:
                hour += 12
            if period == "AM" and hour == 12:
                hour = 0
            return (hour, minute)

        earlier, later = sorted(slots, key=slot_key)
        lowered = message.lower()
        if "late" in lowered or "safer" in lowered or "buffer" in lowered:
            return f"Choose {later}. If you might be late, the later slot gives you more buffer for arrival and briefing."
        return f"{earlier} is better if you want to finish earlier; {later} is safer if you want more arrival buffer."

    def _is_room_comparison_question(self, lowered: str) -> bool:
        room_count = len(self._room_names_in_text(lowered))
        return room_count >= 2 and bool(
            "?" in lowered
            or re.search(r"\b(?:better|compare|difference|which|first timer|first timers|beginner|suggest|recommend)\b", lowered)
        )

    def _direct_room_comparison_response(self, lowered: str) -> str:
        rooms = self._room_names_in_text(lowered)
        option_text = " and ".join(rooms[:2])
        comparison = self._comparison_response(option_text)
        if comparison:
            return comparison
        if "murder mystery" in lowered and "hostage" in lowered:
            return (
                "For first timers, I'd choose Murder Mystery. It is calmer and investigation-led. "
                "Hostage is better if you want more urgency and pressure."
            )
        return f"{rooms[0]} and {rooms[1]} are different styles. Tell me whether you prefer story, pressure, or difficulty, and I'll narrow it down."

    @staticmethod
    def _room_names_in_text(lowered: str) -> list[str]:
        room_patterns = (
            ("Murder Mystery", "murder mystery"),
            ("Hostage", "hostage"),
            ("Classified", "classified"),
            ("Bomb Defusal", "bomb defusal"),
            ("Bomb Defusal", "bomb diffusal"),
            ("Undercover", "undercover"),
            ("Prison Break", "prison break"),
            ("Missile Attack", "missile attack"),
            ("Curse of the Pharaoh", "curse of the pharaoh"),
            ("The Wizarding Championship", "wizarding championship"),
            ("The Forbidden Forest", "forbidden forest"),
        )
        rooms: list[str] = []
        for name, pattern in room_patterns:
            if pattern in lowered and name not in rooms:
                rooms.append(name)
        return rooms

    @staticmethod
    def _is_location_comparison_question(lowered: str) -> bool:
        return bool(
            re.search(r"\b(?:which|what)\s+(?:branch|location)\b", lowered)
            or re.search(r"\b(?:near|from|closer|closest|better location|which location should)\b", lowered)
        ) and ("location" in lowered or "branch" in lowered or any(area in lowered for area in ("marathahalli", "brookefield", "hoodi", "itpl", "indiranagar", "hsr", "jayanagar")))

    def _direct_location_recommendation_response(self, lowered: str, intent: str = "") -> str:
        room = str(self.memory.data.get("room", "")).strip()
        if (
            "kids" in lowered
            or "children" in lowered
            or (intent != "corporate_event" and self.memory.data.get("age_group") == "kids")
        ):
            return (
                "For younger children and family groups, I'd usually recommend Whitefield because it has fantastic family-friendly "
                "experiences and family groups often enjoy it."
            )
        if "marathahalli" in lowered or "brookefield" in lowered or "hoodi" in lowered or "itpl" in lowered:
            recommended = "Whitefield"
            reason = "it is usually the first branch I would check from that side of Bangalore"
        elif "jayanagar" in lowered or "banashankari" in lowered or "south bangalore" in lowered:
            recommended = "JP Nagar"
            reason = "it is the practical south Bangalore branch"
        elif "indiranagar" in lowered or "mg road" in lowered or "hsr" in lowered or "koramangala" in lowered:
            recommended = "Koramangala"
            reason = "it is usually the practical central/south-east branch"
        else:
            return "Koramangala, Whitefield, and JP Nagar are the main branches. Tell me where you're coming from and I'll narrow it down."

        room_phrase = f" for {room}" if room else ""
        return f"I'd check {recommended} first{room_phrase}; {reason}."

    def _fallback_response(self, message: str, intent: str, recommendation, should_handoff: bool, qual_result=None, waiting_before: str = "", core_changed: bool = False) -> str:
        lowered = message.lower()
        _flow_fields = set(self.memory.FLOW_FIELDS.get(intent, []))
        location_known = bool(str(self.memory.data.get("location", "")).strip())

        # ------------------------------------------------------------------ #
        # BUG 1 & 3: AVAILABILITY-FIRST GUARD                                 #
        # If the customer asks about slot availability, we must answer that   #
        # question FIRST, not run the qualification chain.                    #
        # If location is unknown → ask for location only (not full qual).     #
        # If location is known → this should have been routed to              #
        # booking_agent by ConversationManager, but guard here as backstop.  #
        # ------------------------------------------------------------------ #
        from ..orchestration.conversation_manager import ConversationManager
        if ConversationManager._is_availability_question(message):
            if not location_known:
                return "Sure. Which location would you like me to check availability for?"
            # Location known — booking agent should handle this; return empty to let router decide
            # but provide a friendly acknowledgement
            return "Let me check that for you. Which location are you looking at?"

        # ------------------------------------------------------------------ #
        # BUG 2: LOCATION GUARD for room inventory / recommendation queries   #
        # Never list specific room names or branches until location is known. #
        # ------------------------------------------------------------------ #
        if self._is_room_inventory_query(lowered):
            options = self.recommender.available_options(self.memory.data)
            location = str(self.memory.data.get("location", ""))
            if not location_known:
                return "Which location would you like to visit? I can give you the exact room options for that branch."
            if not options:
                return "I don't have a single room that supports the current group size. We would need to arrange multiple rooms with the events team."
            scope = f" at {location}" if location else " across our Bangalore locations"
            return f"The rooms currently matching your group{scope} are {self._spoken_list(options)}."

        rejection_answer = self._recommendation_rejection_response(lowered)
        if rejection_answer:
            return rejection_answer

        if self._asks_for_more_options(lowered):
            options = self.recommender.available_options(self.memory.data, limit=5)
            if not options:
                return "A single room cannot hold the current group, so the events team would need to coordinate multiple rooms."
            if not location_known:
                return "Which location would you like to visit? I can narrow the options properly once I know the branch."
            context = "For first-time players, Murder Mystery and Hostage are the easiest starting points. "
            if self.memory.data.get("experience_level") != "beginner":
                context = ""
            return f"{context}Other matching options are {self._spoken_list(options)}. Which style sounds best?"

        # ================================================================== #
        # QUESTION-FIRST GUARD                                                #
        #                                                                     #
        # Before any qualification logic runs, check whether the customer    #
        # asked a question. If yes:                                           #
        #   1. Try all knowledge sources (FAQ, rooms, policy, recommendation) #
        #   2. If found → answer + optional resume phrase                    #
        #   3. If not found → say "I'm not sure" + resume phrase             #
        # Only after this chain completes does qualification resume.          #
        # ================================================================== #

        # Special cases that must run BEFORE the generic question-first guard
        # to avoid the classifier intercepting them as "unknown" questions.
        if lowered.strip(" .!?") in {"my name", "my name is what", "what is my name", "what's my name"}:
            stored_name = str(self.memory.data.get("customer_name", "")).strip()
            return f"You're booked under {stored_name}." if stored_name else "I don't have your name yet."

        if waiting_before and ("cancellation" in lowered or "cancel policy" in lowered or "refund policy" in lowered):
            faq = self._cancellation_policy_explanation()
            resume = self._resume_qualification_phrase(waiting_before)
            return f"{faq} {resume}" if resume else faq

        # Intercept kids location inquiries first
        is_kids_inquiry = ("kids" in lowered or "children" in lowered) or (
            self.memory.data.get("age_group") == "kids" and (self.qualification_agent._waiting_for == "location" or waiting_before == "location")
        )
        if is_kids_inquiry and ("location" in lowered or "which" in lowered or "better" in lowered):
            faq = (
                "For younger children and family groups, I'd usually recommend Whitefield because it has fantastic family-friendly "
                "experiences and family groups often enjoy it."
            )
            if waiting_before:
                resume = self._resume_qualification_phrase(waiting_before)
                if resume:
                    return f"{faq} {resume}"
            return f"{faq} Which date were you thinking of visiting?"

        q_analysis = self.conversation_intelligence.classify_question(message)
        if q_analysis.asked_question and q_analysis.question_type not in ("booking_signal",):
            # Helper: compute resume phrase even when waiting_before is empty
            # (for first-turn recommendation questions where no field was pending yet).
            def _get_resume(answer_text: str = "") -> str:
                phrase = self._resume_qualification_phrase(waiting_before) if waiting_before else ""
                if not phrase:
                    return ""
                # Never append a qualification question if the answer already ends with "?"
                if answer_text.rstrip().endswith("?"):
                    return ""
                if phrase.lower() in answer_text.lower():
                    return ""
                return phrase

            # 1. Try existing knowledge/FAQ engine
            knowledge_answer = self._get_faq_or_knowledge_answer(lowered, intent, recommendation)
            if knowledge_answer:
                # If we are resuming qualification, do not ask "How late do you expect to be?" to avoid blocking resume
                if (waiting_before or self.qualification_agent._waiting_for) and knowledge_answer.strip().endswith("How late do you expect to be?"):
                    knowledge_answer = knowledge_answer.replace(" How late do you expect to be?", "").replace("How late do you expect to be?", "")
                resume = _get_resume(knowledge_answer)
                if resume:
                    return f"{knowledge_answer} {resume}"
                return knowledge_answer

            # 2. For recommendation questions specifically, try contextual/recommendation engine
            if q_analysis.question_type == "recommendation":
                rec_answer = self._recommendation_rejection_response(lowered)
                if rec_answer:
                    resume = _get_resume(rec_answer)
                    if resume:
                        return f"{rec_answer} {resume}"
                    return rec_answer
                rec_answer = self._contextual_best_response()
                if not rec_answer and intent == "couple_event":
                    rec_answer = (
                        "For a couple, I'd start with Murder Mystery because the investigation style gives both of you plenty of teamwork and clues to solve together. "
                        "Hostage is the more urgent option if you want extra pressure. Which location would you like to visit?"
                    )
                if not rec_answer and recommendation.option:
                    rec_answer = self._recommendation_response(intent, recommendation.option, recommendation.reason, lowered)
                if rec_answer:
                    resume = _get_resume(rec_answer)
                    if resume:
                        return f"{rec_answer} {resume}"
                    return rec_answer
                return "Which location would you like to visit? Once I know that, I can narrow down the best room options for your group."

            # 3. For repair questions, repeat the previous turn
            if q_analysis.question_type == "repair":
                return self._repeat_previous_question(intent, waiting_before, qual_result)

            # 4. Unknown question — admit uncertainty rather than ignoring it
            if q_analysis.question_type == "unknown" and q_analysis.asked_question:
                uncertain = "I'm not sure about that — our team would be the best people to help."
                resume = _get_resume()
                if resume:
                    return f"{uncertain} {resume}"
                return uncertain
            # For faq/policy with no answer found, fall through to further checks below
            # (existing _answer_faq may still catch it via the lowered path).

        if lowered.strip(" .!?") in {"my name", "my name is what", "what is my name", "what's my name"}:
            stored_name = str(self.memory.data.get("customer_name", "")).strip()
            return f"You're booked under {stored_name}." if stored_name else "I don't have your name yet."

        if self.memory.data.get("pending_policy_explanation") and self._is_affirmative_follow_up(lowered):
            self.memory.data["pending_policy_explanation"] = False
            self.memory.save()
            return self._cancellation_policy_explanation()

        if self._is_contextual_best_question(lowered):
            contextual_answer = self._contextual_best_response()
            if contextual_answer:
                return contextual_answer

        if self._is_contextual_recommendation_question(lowered):
            contextual_answer = self._contextual_best_response()
            if contextual_answer:
                return contextual_answer

        explicit_room_choice = any(
            term in lowered for term in ("instead", "switch", "change", "i want", "i prefer", "rather")
        ) and bool(self.memory.data.get("room"))
        if explicit_room_choice:
            self.qualification_agent._waiting_for = "preferred_date"
            room = str(self.memory.data["room"])
            if room == "Classified":
                room_answer = "It is a tense investigation room with cryptic puzzles and story-driven clues at Koramangala."
            else:
                room_answer = self._answer_room_name(lowered)
            return f"Perfect, I've noted {room} for your group. {room_answer} What date are you planning for?"

        supplied_name = (
            self.memory._extract_name(message)
            if any(term in lowered for term in ("my name is", "this is"))
            else ""
        )
        supplied_phone = self.memory._extract_phone(message)
        if supplied_name and not self.memory.data.get("phone"):
            if waiting_before:
                resume = self._resume_qualification_phrase(waiting_before)
                if resume:
                    return f"Perfect, {self.memory.data.get('customer_name', supplied_name)}. {resume}"
            return f"Perfect, {self.memory.data.get('customer_name', supplied_name)}. Could I get your phone number so we can keep the details together?"
        if supplied_phone and not self.memory.data.get("preferred_date"):
            if waiting_before:
                resume = self._resume_qualification_phrase(waiting_before)
                if resume:
                    return f"Thank you. {resume}"
            self.qualification_agent._waiting_for = "preferred_date"
            return "Thank you. What date are you planning for?"

        # Intercept kids location inquiries first
        is_kids_inquiry = ("kids" in lowered or "children" in lowered) or (
            self.memory.data.get("age_group") == "kids" and (self.qualification_agent._waiting_for == "location" or waiting_before == "location")
        )
        if is_kids_inquiry and ("location" in lowered or "which" in lowered or "better" in lowered):
            faq = (
                "For younger children and family groups, I'd usually recommend Whitefield because it has fantastic family-friendly "
                "experiences and family groups often enjoy it."
            )
            if waiting_before:
                resume = self._resume_qualification_phrase(waiting_before)
                if resume:
                    return f"{faq} {resume}"
            return f"{faq} Which date were you thinking of visiting?"

        if self._should_accept_current_recommendation(message, intent):
            chosen_option = self.memory.data.get("room") or self.memory.data.get("recommended_option", "that option")
            if intent == "escape_room_inquiry":
                room_names = [
                    room for room in self.recommender.available_options(self.memory.data)
                    if room.lower() in str(chosen_option).lower()
                ]
                if not self.memory.data.get("room") and room_names:
                    # Recommendations are ranked. An acceptance commits the
                    # first compatible room instead of restarting room choice.
                    self.memory.set_field("room", room_names[0], message, expected_field="room")
                    chosen_option = room_names[0]
                if not self.memory.data.get("room"):
                    return "I don't have a bookable room recommendation yet. Which room would you like to choose?"
                for field in ("participants", "age_group", "location", "preferred_date"):
                    if not self.memory.data.get(field):
                        return self._ask_for_field(intent, field)
                return self._booking_consent_response()

            contact_missing = self.memory.missing_fields(intent=intent, include_contact=True)
            contact_missing = [field for field in contact_missing if field in self.memory.CONTACT_FIELDS]
            if contact_missing:
                next_field = contact_missing[0]
                prompt = self._ask_for_field(intent, next_field)
                return f"Perfect. I'll go ahead with {chosen_option}. {prompt}"
            name = str(self.memory.data.get("customer_name", ""))
            if name:
                return f"Perfect. Thank you, {name}. I've captured all the information I need. Someone from our team will reach out to you shortly."
            return "Perfect. I've captured all the information I need. Someone from our team will reach out to you shortly."

        if (
            intent == "escape_room_inquiry"
            and self.memory.data.get("preferred_date")
            and self.memory.data.get("recommended_option")
            and not self.memory.data.get("room")
            and not self._is_explicit_recommendation_request(message.lower())
        ):
            option = str(self.memory.data.get("recommended_option"))
            date = str(self.memory.data.get("preferred_date"))
            return f"Great, {date} works. Would you like to go ahead with {option}?"

        if (
            intent == "escape_room_inquiry"
            and self._is_booking_acceptance_followup(message)
            and not self.memory.data.get("room")
            and not self.memory.data.get("recommended_option")
        ):
            return "I haven't picked a room yet. Do you already have a room in mind, or would you like a recommendation?"

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
                    if os.environ.get("DEMO_MODE", "false").lower() == "true":
                        if self._is_rules_or_rooms_faq(message) and resume:
                            return f"{faq_answer} {resume}"
                        self.qualification_agent._waiting_for = waiting_before
                        return faq_answer
                    if resume:
                        return f"{faq_answer} {resume}"
                    else:
                        handoff_ready = self.memory.handoff_ready(intent)
                        if handoff_ready:
                            self.memory.data["current_workflow"] = "awaiting_booking"
                            self.memory.data["booking_consent_pending"] = True
                            self.memory.save()
                            return f"{faq_answer} {self._booking_consent_response()}"
                        return faq_answer
                # No FAQ match — resume qualification naturally without robotic filler
                if resume:
                    return resume
                else:
                    handoff_ready = self.memory.handoff_ready(intent)
                    if handoff_ready:
                        self.memory.data["current_workflow"] = "awaiting_booking"
                        self.memory.data["booking_consent_pending"] = True
                        self.memory.save()
                        return self._booking_consent_response()
                    return ""

            # Not an interruption — if it's NOT a flow field, qualification owns this turn
            if waiting_before not in _flow_fields:
                route_check = self.router.route(intent, False)
                if route_check.next_agent == "qualification_agent" and not should_handoff:
                    if qual_result is not None and qual_result.response:
                        resp = qual_result.response
                        return resp

        # Case 5 pronominal suggest check first to prevent recommendation preemption
        is_pronominal_suggest = ("suggest" in lowered or "recommend" in lowered or "lean" in lowered) and (
            ("murder mystery" in lowered and "hostage" in lowered) or 
            (
                "which" in lowered and 
                "Murder Mystery" in self.memory.data.get("discussed_options", []) and 
                "Hostage" in self.memory.data.get("discussed_options", [])
            )
        )
        if is_pronominal_suggest:
            return (
                "I lean toward Murder Mystery for a first visit. "
                "It's easier to get into without intense time pressure. Want to go with that?"
            )

        if self._is_breakout_greeting(lowered):
            return "Yes, this is Breakout Escape Rooms. How may I help you today?"

        if self._is_plain_greeting(lowered) and intent == "general_faq":
            return "Hi, this is Breakout Escape Rooms. How may I help you today?"

        compound_answer = self._answer_multiple_questions(lowered)
        if compound_answer:
            return compound_answer

        # Check demo knowledge FAQ / explanations first
        from ..knowledge.demo_knowledge import get_demo_answer
        demo_ans = get_demo_answer(message)
        if demo_ans:
            return demo_ans

        # Check FAQ / direct questions first
        faq_answer = self._answer_faq(lowered)
        if faq_answer and (intent == "general_faq" or self._is_direct_question(lowered)):
            return faq_answer

        explicit_answer = self._answer_explicit_intent_question(lowered, intent)
        if explicit_answer:
            return explicit_answer

        # Check explicit recommendation queries
        is_asking_rec = self._is_explicit_recommendation_request(lowered)
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

        if re.search(
            r"\b(i want to book|want to book|can i book|book it|book now|book a|book an|book for|reserve a|i want to reserve|how do i book|how can i book|how to book|how do you book|how do we book)\b",
            lowered,
        ) and "escape room" in lowered and self.memory.data.get("participants") and not self.memory.data.get("experience_level"):
            return "Have you done an escape room before, or is this your first one?"

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
        is_asking_rec = self._is_explicit_recommendation_request(lowered)
        if intent == "escape_room_inquiry" and recommendation.option:
            if is_asking_rec:
                if not self.memory.data.get("discussed_options") or core_changed:
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
                if is_asking_rec:
                    return self._recommendation_response(intent, recommendation.option, recommendation.reason, lowered)
                if not self.memory.data.get("room"):
                    return "Do you already have a room in mind, or would you like a recommendation?"

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
        if intent == "escape_room_inquiry":
            if not self.memory.data.get("room"):
                return "Which room would you like to choose?"
            return "What date would you like to visit?" if not self.memory.data.get("preferred_date") else ""
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
        # Case 5: "Which of Murder Mystery or Hostage do you suggest/recommend" (including pronominal reference check)
        is_pronominal_suggest = ("suggest" in lowered or "recommend" in lowered or "lean" in lowered) and (
            ("murder mystery" in lowered and "hostage" in lowered) or 
            (
                "which" in lowered and 
                "Murder Mystery" in self.memory.data.get("discussed_options", []) and 
                "Hostage" in self.memory.data.get("discussed_options", [])
            )
        )
        if is_pronominal_suggest:
            return (
                "I lean toward Murder Mystery for a first visit. "
                "It's easier to get into without intense time pressure. Want to go with that?"
            )


        # Case 4: Popular / best-selling experience.
        if "best selling" in lowered or "bestselling" in lowered or "popular" in lowered or "most popular" in lowered:
            if "whitefield" in lowered:
                return "Murder Mystery is usually the most popular choice at Whitefield."
            if "jp nagar" in lowered or re.search(r"\bjp\b", lowered):
                return "Murder Mystery is usually the most popular choice at JP Nagar."
            if "koramangala" in lowered:
                return "Murder Mystery is usually the most popular choice at Koramangala."
            date_phrase = "for next weekend" if "next weekend" in lowered or "weekend" in lowered else "for your group"
            return (
                f"Since you're planning a visit {date_phrase}, Murder Mystery is usually the safest popular pick, "
                "with Hostage as the more urgent alternative. Which location would you like to visit?"
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
            r"\b(i want to book|want to book|can i book|book it|book now|book a|book an|book for|reserve a|i want to reserve|how do i book|how can i book|how to book|how do you book|how do we book)\b",
            lowered,
        ):
            if self.memory.data.get("participants") and not self.memory.data.get("experience_level"):
                return "Have you done an escape room before, or is this your first one?"
            if re.search(r"\b(escape room|room|birthday|corporate|bachelor|farewell|couple|virtual|online|party)\b", lowered):
                if "escape room" in lowered and self.memory.data.get("participants") and not self.memory.data.get("experience_level"):
                    return "Have you done an escape room before, or is this your first one?"
                return "Sure. What location or date are you thinking of?"
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
                "Breakout offers live escape room experiences plus birthday, corporate, virtual, and party packages across Koramangala, Whitefield, and JP Nagar. "
                "Tell me your group size and I can narrow it down."
            )
        if re.search(r"\boff(?:er|ering|r)\b", lowered):
            return (
                "Breakout offers live escape room experiences plus birthday, corporate, virtual, and party packages across Koramangala, Whitefield, and JP Nagar. "
                "Tell me your group size and I can narrow it down."
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
            if not self._is_explicit_recommendation_request(lowered):
                if self.memory.data.get("participants") and not self.memory.data.get("age_group"):
                    return "Are the players adults, kids, or a mix?"
                if not self.memory.data.get("participants"):
                    return "How many people are joining?"
                if not self.memory.data.get("location"):
                    return "Which location would you like to visit?"
                return "Do you already have a room in mind, or would you like a recommendation?"
            return (
                "No worries. I'd probably start with Murder Mystery for a first visit. "
                "Hostage is the more urgent option if you want extra pressure. How many people are joining?"
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
            return self._food_faq_answer(lowered)
        if "dress code" in lowered or "what should i wear" in lowered or re.search(r"\bwear\b", lowered):
            return "There is no special dress code; comfortable clothing and closed footwear are a good idea."
        if "age restriction" in lowered or "age restrictions" in lowered or "minimum age" in lowered:
            return "Age suitability can vary by room, so I'll keep the age group in mind and the team can confirm the best fit."
        if "arrival" in lowered or "arrive" in lowered:
            return "Please arrive around 20 minutes before your slot so the team can brief you before the game."
        if "direction" in lowered:
            return "Breakout has locations in Koramangala, Whitefield, and JP Nagar; the team can share exact branch directions with your confirmation."
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
            return self._parking_faq_answer(lowered)
        if "where are you located" in lowered or "what are your locations" in lowered or "where is breakout" in lowered or "our locations" in lowered or "locations" in lowered or ("location" in lowered and any(w in lowered for w in ("which", "what", "better", "are", "have", "address", "list", "know", "where"))):
            return "Breakout has locations in Koramangala, Whitefield, and JP Nagar."
        if "cancel" in lowered or "refund" in lowered:
            self.memory.data["pending_policy_explanation"] = False
            self.memory.save()
            return self._cancellation_policy_explanation()
        if "walk in" in lowered:
            return "Walk-ins are allowed only if slots are available. Slots cannot be held without advance payment."
        if "advance booking" in lowered or "booking required" in lowered:
            return "Yes. All bookings are done online."
        return ""

    def _parking_faq_answer(self, lowered: str) -> str:
        location = str(self.memory.data.get("location", "")).lower()
        if "whitefield" in lowered or location == "whitefield":
            return "Whitefield has basement parking available."
        if "koramangala" in lowered or location == "koramangala":
            return "Koramangala has basement and street parking available."
        if "jp nagar" in lowered or location == "jp nagar":
            return "JP Nagar has street parking available."
        return "Koramangala has basement and street parking, Whitefield has basement parking, and JP Nagar has street parking."

    def _food_faq_answer(self, lowered: str) -> str:
        intent = str(self.memory.data.get("intent", ""))
        if "birthday" in lowered or intent == "birthday_party":
            return "Food options for birthday parties include choices that can be coordinated with the package; the team can confirm the available menu for your guest count."
        if "corporate" in lowered or intent == "corporate_event":
            return "Food options include lighter snacks, hi-tea, or meal options for corporate events depending on the package and group size."
        return "Food options include continental food, build-your-menu options, mixed snack boxes, hi-tea options, and meal options for events."

    @staticmethod
    def _is_escape_room_education_query(message: str) -> bool:
        lowered = re.sub(r"\s+", " ", message.lower()).strip(" .!?")
        education_phrases = (
            "tell me about escape rooms",
            "tell me about your escape rooms",
            "tell me more about your escape rooms",
            "how do escape rooms work",
            "how does an escape room work",
            "explain escape rooms",
            "what are escape rooms",
            "what is an escape room",
            "what is escape room",
        )
        return any(phrase in lowered for phrase in education_phrases)

    def _escape_room_education_response(self, message: str) -> str:
        lowered = message.lower()
        if "more" in lowered:
            explanation = (
                "Each themed room has a storyline and mission. Your team searches for clues, "
                "connects the evidence, solves puzzles, and can ask the game master for help if needed."
            )
        else:
            explanation = (
                "An escape room is a themed team game where you search for clues, solve puzzles, "
                "and complete a mission before time runs out."
            )
        return f"Sure. {explanation} Are you looking for a recommendation or just learning how it works?"

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
            "cancellation": self._cancellation_policy_explanation(),
        }
        ordered_topics = list(dict.fromkeys(topics))
        return "Sure. " + " ".join(answers[topic] for topic in ordered_topics)

    def _answer_room_name(self, lowered: str) -> str:
        import difflib
        import re

        # Guard: if the message is a greeting/brand check, don't match room names.
        # "is this breakout", "hi breakout", etc. should never return a room description.
        if re.search(r"\bbreakout\b", lowered) and not re.search(
            r"\b(prison break|murder mystery|hostage|classified|undercover|wizarding|pharaoh|forbidden|bomb|defusal)\b",
            lowered,
        ):
            return ""

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
                        f"At {location}, {room_name.title()} is available. {info['description']} "
                        f"Does that sound like the kind of room your group would enjoy?"
                    )
                return f"{info['description']} Which location would you like to visit?"

        normalized = re.sub(r"[^a-z ]", "", lowered).strip()
        if normalized:
            match = difflib.get_close_matches(normalized, rooms.keys(), n=1, cutoff=0.72)
            if match:
                room_name = match[0]
                info = rooms[room_name]
                location = self.memory.data.get("location", "")
                if location:
                    return (
                        f"At {location}, {room_name.title()} is available. {info['description']} "
                        f"Does that sound like the kind of room your group would enjoy?"
                    )
                return f"{info['description']} Which location would you like to visit?"

        return ""

    def _ask_for_field(self, intent: str, field: str) -> str:
        if field == "location":
            return "Which location would you like to visit?"
        if field == "participants":
            if intent == "birthday_party":
                return "Awesome, a birthday party. How many guests are you expecting?"
            return "How many people are joining?"
        if field == "experience_level":
            return "Have you done an escape room before, or is this your first one?"
        if field == "age_group":
            return "What age group are the players: adults, kids, or a mix?"
        if field == "preferred_date":
            if intent == "birthday_party":
                location = str(self.memory.data.get("location", ""))
                capacity = self._capacity_for_location(location)
                if capacity:
                    return f"Great. {location} can accommodate approximately {capacity}. What date were you thinking of visiting us for the party?"
            return "Got it. What date are you planning for?"
        if field == "company_size":
            return "Roughly how many employees are joining?"
        if field == "customer_name":
            return "Perfect. May I have your name?"
        if field == "phone":
            return "Thanks. What's the best phone number for the booking details?"
        if field == "food_required":
            return "Sounds good. Do you need food and beverages as well?"
        if field == "budget_range":
            return "Got it. What's the budget range: Basic, Standard, or Premium?"
        return "Perfect. Could you share one more detail?"

    def _get_flow_missing(self, intent: str, lowered: str) -> list[str]:
        flow_missing = self.memory.missing_fields(intent=intent, include_contact=False)
        if (
            intent == "escape_room_inquiry"
            and not self.memory.data.get("experience_level")
            and not self.memory.data.get("age_group")
        ):
            if "participants" in flow_missing:
                insert_at = flow_missing.index("participants") + 1
            else:
                insert_at = 0
            if "experience_level" not in flow_missing:
                flow_missing.insert(insert_at, "experience_level")
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

    def _recommendation_rejection_response(self, lowered: str) -> str:
        cleaned = lowered.strip(" .!?")
        is_rejection = self._is_recommendation_rejection(cleaned)
        if not is_rejection:
            return ""

        options = self.recommender.available_options(self.memory.data, limit=8)
        if not options:
            if not self.memory.data.get("location"):
                return "Which location would you like to visit? Once I know the branch, I can suggest a better-fit option."
            return "I don't have another single-room option that fits the current group size. We may need event coordination for multiple rooms."

        discussed = [
            str(option)
            for option in self.memory.data.get("discussed_options", [])
            if str(option) in options
        ]
        current = str(self.memory.data.get("recommended_option") or self.memory.data.get("room") or "")
        rejected = [
            str(option)
            for option in self.memory.data.get("rejected_options", [])
            if str(option) in options
        ]
        if current and current not in rejected:
            self.memory.data.setdefault("rejected_options", [])
            if isinstance(self.memory.data["rejected_options"], list):
                self.memory.data["rejected_options"].append(current)
                self.memory.data["rejected_options"] = self.memory.data["rejected_options"][-8:]
            rejected.append(current)
        if "harder" in cleaned or "challenging" in cleaned:
            priority = ["Bomb Defusal", "Classified", "Undercover", "Missile Attack", "Hostage", "Murder Mystery"]
        elif "easier" in cleaned or "beginner" in cleaned:
            priority = ["Murder Mystery", "Hostage", "Undercover", "Classified", "Bomb Defusal", "Missile Attack"]
        elif "couple" in cleaned:
            priority = ["Murder Mystery", "Undercover", "Hostage", "Classified", "Bomb Defusal"]
        else:
            priority = [
                option
                for option in ["Hostage", "Murder Mystery", "Undercover", "Classified", "Bomb Defusal", "Missile Attack"]
                if option != current
            ]

        excluded = set(discussed) | set(rejected)
        if current:
            excluded.add(current)
        ranked = [option for option in priority if option in options and option not in excluded]
        if not ranked:
            ranked = [option for option in options if option not in excluded] or [option for option in options if option != current] or options

        next_option = ranked[0]
        self.memory.set_field("recommended_option", next_option, cleaned, expected_field="recommended_option")
        self._update_discussed_options(next_option)
        reason = "It gives you a different style from the first suggestion."
        if "harder" in cleaned or "challenging" in cleaned:
            reason = "It is a stronger challenge fit for your branch and group size."
        elif "easier" in cleaned or "beginner" in cleaned:
            reason = "It is the easier, more approachable fit."
        elif "couple" in cleaned:
            reason = "It works well when two players need steady teamwork and clear clue-sharing."
        return f"Then I'd suggest {next_option}. {reason} Would you like to go with {next_option}, or compare it with another room?"

    @staticmethod
    def _is_recommendation_rejection(lowered: str) -> bool:
        cleaned = lowered.strip(" .!?")
        return bool(
            re.search(
                r"\b(?:don't\s+(?:like|want)\s+(?:that|this|one|murder mystery|hostage|classified|undercover|bomb defusal)|dont\s+(?:like|want)\s+(?:that|this|one|murder mystery|hostage|classified|undercover|bomb defusal)|"
                r"do\s+not\s+(?:like|want)\s+(?:that|this|one|murder mystery|hostage|classified|undercover|bomb defusal)|not\s+that\s+one|"
                r"don't\s+like\s+any\s+of\s+(?:these|them)|dont\s+like\s+any\s+of\s+(?:these|them)|same\s+things|"
                r"already\s+played\s+(?:that|this|it|one)|played\s+that\s+already|"
                r"any\s+other\s+(?:option|room|game)|another\s+(?:option|room|game)|"
                r"other\s+options?|different\s+(?:option|room|game)|second\s+(?:best|recommendation|option)|"
                r"something\s+(?:harder|easier|scarier|for\s+couples?|for\s+kids?|for\s+adults?))\b",
                cleaned,
            )
        )

    def _recommendation_response(self, intent: str, option: str, reason: str, lowered: str = "") -> str:
        # Bug 2 guard: NEVER mention a specific room or branch name without a known location.
        # The personality prompt also enforces this, but we add a deterministic backstop here.
        location_known = bool(str(self.memory.data.get("location", "")).strip())
        if intent == "escape_room_inquiry" and not location_known:
            return "Which location would you like to visit? Once I know that, I can narrow down the best room options for your group."

        if intent == "birthday_party":
            location = self.memory.data.get("location", "")
            if str(location).lower() == "whitefield":
                return "Nice. Whitefield can host around 35-40 guests, with Escape Rooms plus activities like Karaoke or Scavenger Hunt. What date are you considering?"
            return "Absolutely. Birthday packages can mix Escape Rooms with Karaoke, Scavenger Hunt, or Showstopper. What date are you considering?"
        if intent == "corporate_event":
            if any(term in lowered for term in ("recommend", "suggest", "which room", "what room")):
                size = self.memory.data.get("company_size") or self.memory.data.get("participants")
                return (
                    f"For a corporate group of {size}, I would use coordinated Escape Rooms and Scavenger Hunt rather than force everyone into one room. "
                    "Which location are you considering?"
                )
            return "Nice. I'd probably combine Escape Rooms and Scavenger Hunt so the whole team stays involved. Which location are you considering?"
        if intent == "escape_room_inquiry":
            location_known = bool(str(self.memory.data.get("location", "")).strip())
            globally_safe = any(
                safe in option
                for safe in ("Murder Mystery", "Hostage", "Murder Mystery and Hostage", "Murder Mystery or Hostage")
            )
            unique_branch_options = (
                "Classified", "Bomb Defusal", "Undercover", "Prison Break",
                "Curse of the Pharaoh", "The Wizarding Championship", "The Forbidden Forest",
            )
            if not location_known and any(unique in option for unique in unique_branch_options):
                return "Which location would you like to visit? Once I know that, I can narrow down the best room options for your group."
            flow_missing = self._get_flow_missing(intent, lowered)
            if not flow_missing:
                follow_up = "Want me to compare them?"
            elif flow_missing[0] == "participants":
                follow_up = "How many players will be joining the fun?"
            elif flow_missing[0] == "age_group":
                follow_up = "What is the age group of the players?"
            elif flow_missing[0] == "location":
                follow_up = "Which location would you like to visit?"
            elif flow_missing[0] == "preferred_date":
                follow_up = "What date are you thinking of visiting us?"
            else:
                follow_up = "Want me to compare them?"
 
            if option == "The Wizarding Championship":
                prefix = self._escape_room_context_prefix("children aged 5 to 8")
                actual_follow = follow_up if "location" in flow_missing else "Want the room details?"
                return f"{prefix}I'd probably go with The Wizarding Championship. It's designed for younger players. {actual_follow}"
            if option == "Murder Mystery and Hostage":
                prefix = self._escape_room_context_prefix("that age group")
                actual_follow = follow_up if "location" in flow_missing else "Want me to compare them?"
                return f"{prefix}Murder Mystery and Hostage are the two I'd consider. I'd start with Murder Mystery for investigation; Hostage adds more urgency. {actual_follow}"
            if option == "Murder Mystery or Hostage":
                prefix = self._beginner_context_prefix()
                return f"{prefix}I'd probably start with Murder Mystery. It's easier for first-timers; Hostage is the more urgent option. {follow_up}"
            
            adult_resp = self._adult_recommendation_response() if (location_known or globally_safe) else ""
            if adult_resp:
                return adult_resp
        return f"Sure. {option} would be a good fit. {reason}"

    def _guided_response(self, message: str, intent: str, recommendation, waiting_before: str = "", core_changed: bool = False) -> str:
        lowered = message.lower()
 
        if intent == "birthday_party":
            if "birthday" in lowered and not self._message_has_count(lowered):
                return "Absolutely. Birthday packages can mix Escape Rooms with Karaoke, Scavenger Hunt, or Showstopper. Approximately how many guests are you expecting?"
            if not self.memory.data.get("participants"):
                return "Absolutely. Birthday packages can mix Escape Rooms with Karaoke, Scavenger Hunt, or Showstopper. Approximately how many guests are you expecting?"
            if not self.memory.data.get("location"):
                participants = self.memory.data.get("participants")
                return f"Got it, {participants} guests. Which location would you like to visit?"
 
        if intent == "corporate_event":
            if self.memory.data.get("company_size") and any(
                term in lowered for term in ("recommend", "suggest", "which room", "what room")
            ):
                size = self.memory.data.get("company_size")
                return (
                    f"For a corporate group of {size}, I would use coordinated multi-room activities rather than force everyone into one room. "
                    "Which location are you considering?"
                )
            if self.memory.data.get("company_size") and (
                not self.memory.data.get("location") or self._is_fresh_corporate_inquiry_without_location(lowered)
            ):
                size = self.memory.data.get("company_size")
                return f"Nice. For a team of {size}, I'd probably combine Escape Rooms and Scavenger Hunt for teamwork and full-group involvement. Which location are you considering?"
            if not self.memory.data.get("company_size"):
                return "Absolutely. We can build the event around Escape Rooms, Scavenger Hunt, or Let Loose. How many employees are you planning for?"
 
        if intent == "couple_event":
            if self.memory.data.get("room"):
                return ""
            history = self.memory.data.get("conversation", [])
            already_asked = False
            for turn in reversed(history):
                if turn.get("role") == "agent":
                    content = turn.get("content", "")
                    if "relaxed or challenging" in content or "relaxed or more challenging" in content:
                        already_asked = True
                        break
            is_asking_rec = self._is_explicit_recommendation_request(lowered)
            if already_asked and not is_asking_rec:
                return ""
            return "Nice. I'd probably go with Murder Mystery for the story and teamwork. Hostage is the more urgent option. Are you looking for something relaxed or more challenging?"
 
        if intent == "escape_room_inquiry":
            has_beginner_kw = any(term in lowered for term in ("first time", "never done", "beginner"))
            is_beginner = (self.memory.data.get("experience_level") == "beginner" or has_beginner_kw)
            if is_beginner and not self.memory.data.get("room") and not any(r in self.memory.data.get("discussed_options", []) for r in ["Murder Mystery", "Hostage"]):
                participants = self.memory.data.get("participants")
                location = self.memory.data.get("location")
                age_group = self.memory.data.get("age_group")
                if participants and location and age_group:
                    return "Do you already have a room in mind, or would you like a recommendation?"
                if participants and age_group:
                    return "Which location would you like to visit?"
                if participants:
                    return "What age group are the players: adults, kids, or a mix?"
                return "How many people are joining?"
            if recommendation.option:
                is_asking_rec = self._is_explicit_recommendation_request(lowered)
                if is_asking_rec:
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
        location = str(self.memory.data.get("location", "")) or self._recent_location_context()
        challenge_preference = str(self.memory.data.get("challenge_preference", "")).lower()
        if not challenge_preference and self._recent_customer_mentions(("thrill", "thrilling", "adventurous", "intense", "pressure")):
            challenge_preference = "challenging"
        experience_level = str(self.memory.data.get("experience_level", "")).lower()

        group_phrase = f"For a group of {participants} adults" if participants else "For adults"
        if location:
            group_phrase += f" visiting {location}"

        # If age group is missing, we must follow up about it
        flow_missing = self._get_flow_missing("escape_room_inquiry", "")
        if "age_group" in flow_missing:
            follow_up = "What's the age group: adults, kids, or a mix?"
        else:
            follow_up = ""

        if experience_level == "beginner" or challenge_preference == "beginner":
            return (
                f"{group_phrase}, I'd probably start with Murder Mystery. "
                "Hostage is the more urgent alternative."
            )
        if location.lower() == "whitefield" and challenge_preference == "challenging":
            return (
                f"{group_phrase}, I'd recommend Bomb Defusal. "
                "It is the strongest fit if you want something intense, time-pressured, and more challenging."
            )
        if location.lower() == "whitefield" and challenge_preference == "story":
            return (
                f"{group_phrase}, I'd recommend Undercover. "
                "It's a more story-driven mystery with a stronger investigation feel."
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
                f"{group_phrase}, I'd recommend Missile Attack. "
                "It is the stronger mission-style challenge at that location."
            )
        if location.lower() == "jp nagar" and challenge_preference == "story":
            return (
                f"{group_phrase}, I'd recommend Murder Mystery. "
                "It is lighter and more clue-driven if you want more story than pressure."
            )

        if location.lower() == "whitefield":
            q = follow_up or "Are you looking for something challenging or more story-driven?"
            try:
                count = int(participants or 0)
            except (TypeError, ValueError):
                count = 0
            if count and count < 4:
                self.memory.set_field("recommended_option", "Undercover", expected_field="recommended_option")
                return f"{group_phrase}, I'd probably go with Undercover for story and teamwork. Murder Mystery is the easier alternative. {q}"
            self.memory.set_field("recommended_option", "Bomb Defusal", expected_field="recommended_option")
            return f"{group_phrase}, I'd probably go with Bomb Defusal for intensity. Undercover is the more story-led option. {q}"
        if location.lower() == "koramangala":
            q = follow_up or "Are you looking for something challenging or more beginner-friendly?"
            return f"{group_phrase}, I'd probably go with Classified for the challenge. Undercover is more story-led. {q}"
        if location.lower() == "jp nagar":
            q = follow_up or "Are you looking for something intense or more relaxed?"
            return f"{group_phrase}, I'd probably go with Missile Attack for the mission feel. Murder Mystery is the lighter option. {q}"

        return f"{group_phrase}, I'd probably choose Classified or Bomb Defusal: mystery for the first, intensity for the second. {follow_up or 'Which location would you like to visit?'}"

    def _recent_customer_mentions(self, terms: tuple[str, ...]) -> bool:
        for turn in reversed(self.memory.data.get("conversation", [])[-6:]):
            if turn.get("role") != "customer":
                continue
            content = str(turn.get("content", "")).lower()
            if any(term in content for term in terms):
                return True
        return False

    def _recent_location_context(self) -> str:
        for turn in reversed(self.memory.data.get("conversation", [])[-6:]):
            if turn.get("role") != "customer":
                continue
            content = str(turn.get("content", "")).lower()
            if "whitefield" in content:
                return "Whitefield"
            if "koramangala" in content:
                return "Koramangala"
            if "jp nagar" in content or re.search(r"\bjp\b", content):
                return "JP Nagar"
        return ""

    def _kids_recommendation_response(self) -> str:
        """Recommendation for kids age group."""
        participants = self.memory.data.get("participants")
        location = str(self.memory.data.get("location", ""))

        group_phrase = f"For a group of {participants} kids" if participants else "For kids"
        if location:
            group_phrase += f" visiting {location}"

        return f"{group_phrase}, Murder Mystery and Hostage are the two I'd consider. I'd start with Murder Mystery for investigation; Hostage adds more urgency. Which sounds better?"

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
                "Want me to compare them?"
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
                age_phrase = f"aged {age_match.group(0)}"
                last_customer = next(
                    (
                        str(turn.get("content", "")).lower()
                        for turn in reversed(self.memory.data.get("conversation", []))
                        if turn.get("role") == "customer"
                    ),
                    "",
                )
                if re.search(r"\bkids?\b", last_customer):
                    group_word = "kids"
        elif stored_age == "kids" and age_phrase == "that age group":
            age_phrase = "kids"
            group_word = "kids"
        if participants and location:
            if age_phrase.startswith("aged "):
                return f"For {participants} {group_word} {age_phrase} visiting {location}, "
            return f"For {participants} {group_word} in {age_phrase} visiting {location}, "
        if participants:
            if age_phrase.startswith("aged "):
                return f"For {participants} {group_word} {age_phrase}, "
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
            return "I lean toward Murder Mystery for a first visit. It's easier to get into without intense time pressure. Hostage is the more urgent option. Want to go with Murder Mystery?"
        if "bomb defusal" in option and "undercover" in option:
            return "Sure. Bomb Defusal is stronger if you want pressure and a faster-paced challenge. Undercover is better if you prefer a mystery-style investigation. Are you leaning toward intensity or story?"
        if "classified" in option and "undercover" in option:
            return "Sure. Classified is better for investigation-style challenges, while Undercover has a tense bunker-style mystery. Are you looking for difficult puzzles or a stronger story setup?"
        return ""

    def _answer_explicit_intent_question(self, lowered: str, intent: str) -> str:
        if intent == "corporate_event" and self._asks_for_room_suggestion(lowered):
            size = self.memory.data.get("company_size") or self.memory.data.get("participants")
            group = f" for a team of {size}" if size else ""
            return f"Nice. For a corporate group{group}, I'd probably combine Escape Rooms and Scavenger Hunt so everyone stays involved. Which location are you considering?"
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
            "none of us has played", "new to escape", "none of us has ever done",
            "none of us have ever done"
        )
        return any(term in lowered for term in beginner_terms)

    @staticmethod
    def _is_rules_or_rooms_faq(message: str) -> bool:
        lowered = message.lower().strip()
        rules_patterns = [
            r"\bexplain\s+(?:the\s+)?rules\b",
            r"\bwhat\s+are\s+the\s+rules\b",
            r"\bhow\s+does\s+it\s+work\b",
            r"\bwhat\s+happens\s+inside\b",
            r"\bfirst\s+time\s+here\b",
        ]
        rooms_patterns = [
            r"\bexplain\s+(?:the\s+)?rooms\b",
            r"\bcan\s+(?:you\s+)?explain\s+(?:the\s+)?rooms\b",
            r"\bwhat\s+rooms\b",
            r"\btell\s+me\s+about\s+(?:the\s+)?rooms\b",
            r"\broom\s+options\b",
        ]
        extra_faq_phrases = ("can explain rooms", "explain rooms", "tell me about rooms", "explain rules", "how does it work")
        
        for pat in rules_patterns + rooms_patterns:
            if re.search(pat, lowered):
                return True
        if any(phrase in lowered for phrase in extra_faq_phrases):
            return True
        return False

    @staticmethod
    def _is_direct_question(lowered: str) -> bool:
        stripped = lowered.strip()
        if stripped.endswith("?"):
            return True
        question_words = (
            "what ", "where ", "which ", "how ",
            "tell me ", "parking ", "location ", "locations ", "should we "
        )
        if any(word in lowered for word in question_words):
            return True
        if stripped.startswith(("do ", "does ", "can ")) or any(
            phrase in lowered
            for phrase in (
                "do you", "do we", "do they", "does it", "does the", "does anyone",
                "can you", "can we", "can i ", "can one", "can anyone"
            )
        ):
            return True
        # ---- Implicit question forms (no "?") --------------------------------
        # Phrases that signal a question even without an explicit question mark.
        implicit_starters = (
            "explain ",
            "explain more",
            "tell me more",
            "before that",
            "by the way",
            "quick question",
        )
        if any(stripped.startswith(s) or s in lowered for s in implicit_starters):
            return True
        # Standalone "why" or "why [verb]"
        if re.match(r"^why\b", stripped):
            return True
        # Recommendation signals without "?"
        implicit_rec = (
            "explain more",
            "tell me more",
            "more details",
            "more information",
            "more info",
            "your favorite",
            "your favourite",
            "your pick",
            "your recommendation",
            "best room",
            "best option",
            "most popular room",
            "suggest a room",
            "recommend",
            "suggest",
        )
        if any(phrase in lowered for phrase in implicit_rec):
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

    def _commit_recommended_room_if_accepted(self, message: str, intent: str) -> bool:
        if not self._should_accept_current_recommendation(message, intent):
            return False
        if self.memory.data.get("room"):
            return True
        recommended = str(self.memory.data.get("recommended_option") or "").strip()
        if not recommended:
            return False
        room_names = [
            room
            for room in self.recommender.available_options(self.memory.data, limit=10)
            if room.lower() in recommended.lower()
        ]
        selected = room_names[0] if room_names else recommended
        self.memory.set_field("room", selected, message, expected_field="room")
        self.memory.data["recommended_option"] = selected
        self.memory.save()
        return True

    @staticmethod
    def _is_booking_acceptance_followup(message: str) -> bool:
        lowered = message.lower().strip().rstrip(".!?")
        lowered = re.sub(r"^(?:okay|ok|yeah|yes|alright|all right)[,\s]+", "", lowered)
        phrases = (
            "book it",
            "reserve it",
            "let's do that",
            "lets do that",
            "let us do that",
            "go ahead",
            "sounds good",
            "that works",
            "do it",
            "book that",
            "let's go with that",
            "lets go with that",
            "let's book that",
            "lets book that",
            "i'll take that one",
            "ill take that one",
            "let's continue",
            "lets continue",
            "yes",
        )
        return lowered in phrases or bool(
            re.match(r"^(?:i'?ll\s+take|let'?s\s+(?:book|continue)|let\s+us\s+(?:book|continue|do\s+that))\b", lowered)
        )

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
        if any(term in lowered for term in ("first time", "first-time", "never done", "beginner")):
            return False

        # ------------------------------------------------------------------ #
        # FAST-EXIT: customer answered the expected field — not an interruption #
        # These run first so valid field answers are never blocked.            #
        # ------------------------------------------------------------------ #
        if waiting_before == "participants" and self.qualification_agent._is_valid_count(lowered):
            return False
        if waiting_before == "location" and self.memory._extract_location(lowered):
            return False
        if waiting_before == "preferred_date" and self.memory._extract_preferred_date(lowered):
            return False
        if waiting_before == "age_group" and self.memory._extract_age_group(lowered, allow_bare_range=True)[0]:
            return False
        if waiting_before == "food_required" and self.memory._extract_food_required(lowered) != "":
            return False
        if waiting_before == "budget_range" and self.qualification_agent._is_valid_budget(lowered):
            return False
        if waiting_before == "customer_name" and self.qualification_agent._extract_bare_name(message):
            return False
        if waiting_before == "phone" and self.memory._extract_phone(lowered):
            return False
        if waiting_before == "email" and self.memory._extract_email(lowered):
            return False

        # Valid side-channel fields should still be stored while another
        # field is missing. Let QualificationAgent handle the persistence and
        # then ask the next missing question instead of treating the turn as
        # an invalid answer to the current prompt.
        if (
            self.memory._extract_preferred_date(message)
            or self.memory._extract_time(message)
            or self.memory._extract_phone(message)
            or self.memory._extract_participants(self.memory.normalize_number_words(message).lower())
            or self.memory._extract_room(lowered)
            or self.memory._extract_location(lowered)
        ):
            return False

        # ------------------------------------------------------------------ #
        # PRIMARY CHECK: use QuestionClassifier for broad, robust detection.  #
        # Any message that classifies as a question is an interruption.       #
        # This replaces the old brittle keyword list and catches:             #
        #   - "explain more", "tell me more", "why", "before that..."         #
        #   - "your favorite", "best room", "most popular room"               #
        #   - "how does it work", "are we locked in", implicit questions       #
        # ------------------------------------------------------------------ #
        q_analysis = self.conversation_intelligence.classify_question(message)
        if q_analysis.asked_question and q_analysis.question_type not in ("booking_signal",):
            return True

        # ------------------------------------------------------------------ #
        # LEGACY CHECKS: kept as belt-and-braces for edge cases not caught    #
        # by the classifier (e.g. location-specific FAQ during location Q).   #
        # ------------------------------------------------------------------ #
        if self._is_rules_or_rooms_faq(message):
            return True
        if self._is_direct_question(lowered):
            return True

        if waiting_before == "food_required" and "food" in lowered:
            return self.memory._extract_food_required(lowered) == ""
        if waiting_before == "age_group" and any(k in lowered for k in ["kids", "children", "age"]):
            return not self.memory._extract_age_group(lowered, allow_bare_range=True)[0]
        if waiting_before == "location" and ("location" in lowered or any(loc in lowered for loc in ["koramangala", "whitefield", "jp nagar"])):
            return not self.memory._extract_location(lowered)

        if waiting_before == "preferred_date" and not self.memory._extract_preferred_date(lowered):
            return True
        if waiting_before == "location" and not self.memory._extract_location(lowered):
            return True
        if waiting_before == "participants" and not self.qualification_agent._is_valid_count(lowered):
            return True
        if waiting_before == "age_group" and not self.memory._extract_age_group(lowered, allow_bare_range=True)[0]:
            return True
        if waiting_before == "food_required" and self.memory._extract_food_required(lowered) == "":
            return True
        if waiting_before == "budget_range" and not self.qualification_agent._is_valid_budget(lowered):
            return True
        if waiting_before == "customer_name" and not self.qualification_agent._extract_bare_name(message):
            return True
        if waiting_before == "phone" and not self.memory._extract_phone(lowered):
            return True
        if waiting_before == "email" and not self.memory._extract_email(lowered):
            return True
        return False

    def _get_faq_or_knowledge_answer(self, message: str, intent: str, recommendation) -> str:
        """Search all knowledge sources in priority order.

        Order:
          1. Demo knowledge (locked answers for the demo suite)
          2. Consultative selling advice (experience/concerns)
          3. For recommendation questions: recommendation engine first, then FAQ
             For non-recommendation questions: FAQ first, then recommendation engine
          4. Room name explanation
        """
        multiple = self._answer_multiple_questions(message.lower())
        if multiple:
            return multiple

        lowered = message.lower()
        if "parking" in lowered or re.search(r"\bpark\b", lowered):
            return self._parking_faq_answer(lowered)
        if "food" in lowered or "menu" in lowered:
            return self._food_faq_answer(lowered)

        from ..knowledge.demo_knowledge import get_demo_answer
        demo_ans = get_demo_answer(message)
        if demo_ans:
            return demo_ans

        consultative = (
            ""
            if self._is_explicit_recommendation_request(lowered)
            else self.conversation_intelligence.get_consultative_selling_reply(lowered)
        )
        if consultative:
            return consultative

        q_analysis = self.conversation_intelligence.classify_question(message)

        # --- For recommendation questions, try the recommendation engine BEFORE generic FAQ
        # so that context-aware answers (kids/location/room discussions) win.
        if q_analysis.question_type == "recommendation":
            # _contextual_best_response() uses last_discussed_topic AND discussed_options.
            contextual = self._contextual_best_response(message)
            if contextual:
                return contextual
            # Check FAQ first for explicit recommendation questions like "popular"
            faq_answer = self._answer_faq(lowered)
            if faq_answer:
                return faq_answer
            # Fall back to recommendation engine
            if recommendation and getattr(recommendation, "option", ""):
                return self._recommendation_response(intent, recommendation.option, recommendation.reason, lowered)
            # If no recommendation available yet, fall through to FAQ below

        # --- FAQ / direct question answers (runs for FAQ/policy/unknown questions,
        # and as a fallback for recommendation questions with no recommendation yet)
        faq_answer = self._answer_faq(lowered)
        if faq_answer:
            return faq_answer
        explicit_answer = self._answer_explicit_intent_question(lowered, intent)
        if explicit_answer:
            return explicit_answer

        # --- Room name explanation path: fires for "tell me about Murder Mystery" etc.
        room_answer = self._answer_room_name(lowered)
        if room_answer:
            return room_answer

        return ""

    def _resume_qualification_phrase(self, waiting_before: str) -> str:
        intent = self.memory.data.get("intent", "")
        field_to_ask = self.qualification_agent.next_missing_field(intent)
        if not field_to_ask:
            return ""

        self.qualification_agent._waiting_for = field_to_ask

        question = self.qualification_agent.QUESTIONS.get(field_to_ask, "")
        if not question:
            question = self._ask_for_field(intent, field_to_ask)

        return question.strip()

    def _booking_consent_response(self) -> str:
        name = self.memory.data.get("customer_name", "")
        location = self.memory.data.get("location", "")
        date = self.memory.data.get("preferred_date", "")
        name_phrase = f", {name}" if name else ""
        loc_phrase = f" at {location}" if location else ""
        date_phrase = f" for {date}" if date else ""
        return f"Perfect. I've got everything I need{name_phrase}. Shall I check availability{date_phrase}{loc_phrase}?"

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
        lowered = response_text.lower()
        if any(room.lower() in lowered for room in rooms):
            self.memory.data["last_discussed_topic"] = "rooms"
        elif any(term in lowered for term in ("food options", "indian buffet", "hi-tea", "snack box", "continental food")):
            self.memory.data["last_discussed_topic"] = "food"
        elif any(term in lowered for term in ("birthday package", "corporate package", "event package", "packages")):
            self.memory.data["last_discussed_topic"] = "packages"
        elif (
            "locations" in lowered
            or "which location" in lowered
            or all(location in lowered for location in ("koramangala", "whitefield", "jp nagar"))
        ):
            self.memory.data["last_discussed_topic"] = "locations"
        self.memory.save()

    @staticmethod
    def _is_affirmative_follow_up(lowered: str) -> bool:
        return lowered.strip(" .!?") in {"yes", "yes please", "sure", "okay", "ok", "please do"}

    @staticmethod
    def _is_contextual_best_question(lowered: str) -> bool:
        cleaned = lowered.strip(" .!?")
        return cleaned in {"which is best", "which one is best", "what is best", "what's best"}

    @staticmethod
    def _is_contextual_recommendation_question(lowered: str) -> bool:
        cleaned = lowered.strip(" .!?")
        return cleaned in {
            "which one would you recommend",
            "which would you recommend",
            "which would you choose",
            "which one would you choose",
            "what would you recommend",
            "what do you recommend",
            "what about this one",
            "what about that one",
            "is that better",
            "is this better",
        }

    @staticmethod
    def _is_explicit_recommendation_request(lowered: str) -> bool:
        cleaned = lowered.strip(" .!?")
        if re.search(r"\b(recommend|recommendation|suggest)\b", cleaned):
            return True
        if re.search(r"\b(best|better|popular|best\s*selling|bestselling|favorite|favourite|pick|choose)\b", cleaned):
            return True
        if re.search(r"\bwhat\s+should\s+(?:we|i)\s+(?:play|choose|book|try)\b", cleaned):
            return True
        if re.search(r"\bsomething\s+for\s+\d+\s+(?:people|players|adults|kids|children|of us)\b", cleaned):
            return True
        if re.search(r"\bwhich\s+room\s+should\s+(?:we|i)\b", cleaned):
            return True
        if re.search(r"\b(first[- ]?time|beginner)\s+(?:room|recommendation|option|game)\b", cleaned):
            return True
        if re.search(r"\b(compare|difference between|versus| vs )\b", cleaned) and "room" in cleaned:
            return True
        return cleaned in {
            "which one would you recommend",
            "which would you recommend",
            "which would you choose",
            "which one would you choose",
            "what would you recommend",
            "what do you recommend",
            "what should we play",
            "what should i play",
        }

    @staticmethod
    def _is_room_inventory_query(lowered: str) -> bool:
        cleaned = lowered.strip(" .!?")
        return bool(
            re.search(r"\bwhat\s+rooms?\s+(?:do|are)\b", cleaned)
            or re.search(r"\b(?:all|rooms?|games?)\s+(?:are\s+)?available\b", cleaned)
            or cleaned in {"what are all available", "what do you have", "show me all rooms"}
        )

    @staticmethod
    def _asks_for_more_options(lowered: str) -> bool:
        return bool(
            re.search(r"\b(?:more|other|different|additional)\s+(?:room\s+)?options?\b", lowered)
            or re.search(r"\bwhat\s+else\b", lowered)
        )

    @staticmethod
    def _spoken_list(options: list[str]) -> str:
        if len(options) <= 1:
            return options[0] if options else ""
        return f"{', '.join(options[:-1])}, and {options[-1]}"

    def _cancellation_policy_explanation(self, *, include_reschedule: bool = False) -> str:
        policy = get_venue_policy(str(self.memory.data.get("location") or ""))
        cancellation = str(policy.get("cancellationPolicy") or "").strip()
        reschedule = str(policy.get("reschedulePolicy") or "").strip()
        if include_reschedule and reschedule:
            policy_text = f"{cancellation} Rescheduling: {reschedule}".strip()
        else:
            policy_text = cancellation or reschedule
        return f"{policy_text} The appropriate team can help with a cancellation or refund request.".strip()

    def _contextual_best_response(self, message: str = "") -> str:
        topic = self.memory.data.get("last_discussed_topic", "")
        lowered_msg = message.lower() if message else ""

        # If the user message is explicitly asking about rooms/games/packages, override locations topic
        if topic == "locations" and any(w in lowered_msg for w in ("room", "game", "experience", "suggest", "recommend")):
            topic = ""

        # Context-aware paths that fire even when last_discussed_topic is not explicitly set:
        # Kids + location question → always recommend Whitefield for family groups.
        if (
            self.memory.data.get("age_group") == "kids"
            and not topic
            and any(w in lowered_msg for w in ("location", "where", "which location", "better location", "good location"))
        ):
            return (
                "For groups with kids, I'd usually recommend Whitefield. "
                "It's the most family-friendly of our locations — family groups often enjoy it. "
                "Does Whitefield work for you?"
            )

        if topic == "food":
            return (
                "If you want a full meal, I'd narrow it to the Indian buffet. "
                "For lighter refreshments, hi-tea or the mixed snack boxes are a better fit. "
                "Are you planning a full meal or something lighter?"
            )
        if topic == "locations":
            if self.memory.data.get("experience_level") == "beginner":
                return (
                    "For a first visit, I'd choose Murder Mystery because it is easier to settle into; "
                    "Hostage is the more urgent alternative. Which location is easiest for your group?"
                )
            if self.memory.data.get("age_group") == "kids":
                return "For kids or family groups, I'd probably choose Whitefield. It works nicely for a family-friendly visit. Does Whitefield work for you?"
            return "I'd choose whichever location is easiest for your group to reach: Koramangala, Whitefield, or JP Nagar. Which area is closest for you?"
        if topic == "packages":
            if self.memory.data.get("intent") == "birthday_party":
                return (
                    "For a birthday, I'd start with a package that combines an Escape Room with an additional activity. "
                    "Karaoke is a lively alternative, while Scavenger Hunt works well for a more active group. "
                    "Approximately how many guests are you planning for?"
                )
            return (
                "For a group event, I'd start with an Escape Room plus Scavenger Hunt because the format gives more people a role. "
                "Let Loose is another option for a more relaxed activity mix. How large is the group?"
            )
        if topic == "rooms" or (not topic and self.memory.data.get("discussed_options")):
            # Also handle the case where last_discussed_topic is not set but rooms have been discussed
            discussed = self.memory.data.get("discussed_options", [])
            if "Murder Mystery" in discussed and "Hostage" in discussed:
                if message and "which" in message.lower() and any(w in message.lower() for w in ("suggest", "recommend", "choose", "lean")):
                    return (
                        "I lean toward Murder Mystery for a first visit. "
                        "It's easier to get into without intense time pressure. Want to go with that?"
                    )
                location = str(self.memory.data.get("location", "")).strip()
                location_phrase = f" at {location}" if location else ""
                return (
                    f"I lean toward Murder Mystery{location_phrase} if you want a more investigation-led experience, especially for first-time players. "
                    "Hostage is the alternative if your group wants more urgency and excitement. "
                    "Would you prefer mystery-solving or a faster-paced challenge?"
                )
            if "Bomb Defusal" in discussed and "Undercover" in discussed:
                return (
                    "I'd lean toward Bomb Defusal if your group wants an intense, time-pressured challenge. "
                    "Undercover is the alternative for a more investigation-style experience. "
                    "Would you prefer intensity or story-led problem solving?"
                )
            if discussed:
                return f"I'd narrow it to {discussed[-1]}. Want the room details before deciding?"

        if self.memory.data.get("experience_level") == "beginner":
            return (
                "I lean toward Murder Mystery for a first visit. "
                "It's easier to get into without losing the full escape-room feel; Hostage is the more urgent alternative. "
                "Want to go with Murder Mystery?"
            )
        return ""

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
