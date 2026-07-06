# Production Gap Report

## Scope And Evidence

This report is limited to findings proven by current code, tests, persisted sessions, and validation runs on 2026-06-22. No production code or prompt was changed.

## Booking: PARTIALLY WORKING

### Gap 1: No verified live booking completion

1. **Root cause:** Existing end-to-end tests inject `MagicMock(spec=BreakoutBookingProvider)`. No log or persisted API session contains `KREEDA_CREATE_BOOKING_SUCCESS`, a real `booking_id`, or a real `booking_ref`.
2. **File:** `tests/test_mvp_stabilization.py`
3. **Function:** `test_booking_routing_acceptance_a_through_f()` and `test_jp_nagar_booking_executes_cart_and_booking_end_to_end()`
4. **Missing validation:** A controlled live `create_instant_cart -> create_booking` response and resulting Kreeda record lookup.
5. **Test gap:** No credential-gated live smoke test.
6. **Code change required:** Not proven for the booking transaction itself; live validation is required first.

### Gap 2: Live provider construction can hang

1. **Root cause:** `AgentContractProvider._dns_check()` uses a `ThreadPoolExecutor` context manager. On a DNS timeout, leaving the context waits for the unresolved worker, defeating the documented timeout. `BookingOrchestrator.__init__()` eagerly constructs this provider even though the operational booking path uses `BreakoutBookingProvider`.
2. **Files:** `src/integrations/kreeda/agent_contract_provider.py`, `src/orchestration/booking_orchestrator.py`
3. **Functions:** `_dns_check()`, `AgentContractProvider.__init__()`, `BookingOrchestrator.__init__()`
4. **Missing validation:** Upper-bound latency test for provider construction under stalled DNS/network conditions.
5. **Test gap:** No test simulates a DNS worker that outlives the timeout.
6. **Code change required:** Yes.

### Gap 3: Independent booking reference is not required

1. **Root cause:** `prepare_booking()` marks success from `bookingId` plus status and sets `booking_reference` to `orderId or bookingId`. `BookingAgent` then persists `booking_ref` as `booking_reference or booking_id`.
2. **Files:** `src/orchestration/booking_orchestrator.py`, `src/agents/booking_agent.py`
3. **Functions:** `BookingOrchestrator.prepare_booking()`, `BookingAgent._prepare_selected_booking()`
4. **Missing validation:** A provider response must contain the contractually required reference field if booking ID and customer reference are distinct requirements.
5. **Test gap:** No test rejects `CONFIRMED + bookingId` with a missing `orderId`/reference.
6. **Code change required:** Yes, if the release contract requires two provider-issued identifiers, as the acceptance criteria state.

### Gap 4: Runtime booking state is only partly persistent

1. **Root cause:** Memory persists `selected_slot`, but a reconstructed `BookingAgent` initializes `_state`, `_available_slots`, `_last_availability`, and `_selected_slot` to empty defaults rather than hydrating them.
2. **Files:** `app.py`, `src/agents/booking_agent.py`
3. **Functions:** `_get_runtime()`, `BookingAgent.__init__()`
4. **Missing validation:** Restart/reconstruction during slot/contact collection must revalidate and resume without losing progression.
5. **Test gap:** No API-session restart test between slot selection and contact submission.
6. **Code change required:** Yes.

## Recommendations: PARTIALLY WORKING

1. **Root cause:** Static recommendation data can diverge from live Kreeda inventory; current stored transcripts contain recommendations for rooms not valid at the selected branch.
2. **Files:** `src/services/recommendation_engine.py`, `src/knowledge/demo_knowledge.py`, `src/agents/inbound_agent.py`
3. **Functions:** `RecommendationEngine.recommend()`, `get_demo_answer()`, `InboundAgent._fallback_response()`
4. **Missing validation:** Recommendations must be intersected with current location inventory before being spoken.
5. **Test gap:** No live-inventory contract test covering every recommended room/location pair.
6. **Code change required:** Yes for strict production grounding.

## Escalation: PARTIALLY WORKING

### Gap 1: Safety and ordinary refund requests are not triggers

1. **Root cause:** `SentimentAgent.SIGNALS` contains no safety terms. `EscalationAgent.evaluate()` contains no safety branch and recognizes only dispute phrases such as `refund dispute`, not `I want a refund`.
2. **Files:** `src/agents/sentiment_agent.py`, `src/agents/escalation_agent.py`
3. **Functions:** `SentimentAgent._rule_based()`, `EscalationAgent.evaluate()`
4. **Missing validation:** Immediate escalation for active danger and refund requests.
5. **Test gap:** No tests for breathing/panic/injury/fire or plain refund language.
6. **Code change required:** Yes.

### Gap 2: Escalation does not select a handoff agent

1. **Root cause:** The escalation agent explicitly operates `without changing business routing`; `_enrich_conversation_result()` attaches metadata after routing and does not update `next_agent`, `should_handoff`, or `active_agent`.
2. **Files:** `src/agents/escalation_agent.py`, `main.py`, `src/core/agent_response.py`
3. **Functions:** `EscalationAgent.evaluate()`, `_enrich_conversation_result()`, `dispatch()`
4. **Missing validation:** An escalation must invoke an actual transfer/queue/callback transport and stop autonomous progression.
5. **Test gap:** Existing tests assert metadata only and explicitly preserve the original spoken response and route.
6. **Code change required:** Yes.

## Handoff: PARTIALLY WORKING

1. **Root cause:** `HandoffSummaryAgent` generates a dictionary, but `ChatResponse` exposes only `response`; no connector consumes `handoff_summary` or `escalation`.
2. **Files:** `src/agents/handoff_summary_agent.py`, `app.py`
3. **Functions:** `HandoffSummaryAgent.generate()`, `chat()`
4. **Missing validation:** Delivery acknowledgment from a real human destination with the generated context.
5. **Test gap:** No transport integration test; no receiving-agent/CRM/Vapi handoff assertion.
6. **Code change required:** Yes.

## Conversation Quality: PARTIALLY WORKING

1. **Root cause:** Active-booking lock suppresses general FAQ handling in `BookingAgent`; a parking question is answered with slot progression. Current regression test codifies this behavior.
2. **Files:** `src/agents/booking_agent.py`, `tests/test_mvp_stabilization.py`
3. **Functions:** `BookingAgent.handle_message()`, `test_booking_state_lock_ignores_faq_and_resumes_on_price()`
4. **Missing validation:** Answer the FAQ from grounded knowledge, preserve state, then ask only the pending booking field.
5. **Test gap:** The existing parking test asserts that `parking` is absent instead of requiring an answer.
6. **Code change required:** Yes.

## Human-Likeness: PARTIALLY WORKING

1. **Root cause:** Generic `I am`, `I'm`, and `this is` name patterns accept complaint fragments such as `Ridiculous And Unacceptable` and `Still Not Working`; downstream responses address customers by those false names.
2. **Files:** `src/memory/conversation_memory.py`, `src/agents/qualification_agent.py`
3. **Functions:** `ConversationMemory._extract_name()`, `_is_plausible_name()`, `QualificationAgent._extract_bare_name()`
4. **Missing validation:** Name extraction must be state-aware and reject complaint/sentiment clauses.
5. **Test gap:** No negative name-extraction tests for `I am frustrated...` or `This is ridiculous...`.
6. **Code change required:** Yes.

## Proven Release Blockers

The blockers are live-provider construction latency, absence of a live completed-booking proof, no real escalation transport, missing safety/refund escalation, false complaint-name extraction, and incorrect FAQ interruption behavior.
