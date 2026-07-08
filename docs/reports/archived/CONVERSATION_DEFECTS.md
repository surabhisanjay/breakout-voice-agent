# Conversation Defects

Only defects demonstrated by current execution or persisted real-session transcripts are included.

## 1. Safety Request Continues Qualification

**Transcript example:** `Someone cannot breathe in the room.` -> `Got it. To suggest the perfect game, how many people are joining?`  
**Root cause:** No safety trigger exists in `SentimentAgent._rule_based()` or `EscalationAgent.evaluate()`; normal inbound fallback runs.  
**Recommended fix:** Add deterministic P0 safety routing that stops all business flows and invokes the handoff transport.  
**Severity:** Critical

## 2. Human Request Does Not Transfer

**Transcript example:** `Please connect me to a human agent.` produced escalation metadata but responded by asking for name/number; active agent remained `inbound_agent`.  
**Root cause:** `_enrich_conversation_result()` attaches metadata after routing; `EscalationAgent` intentionally does not change routing; `ChatResponse` omits handoff data.  
**Recommended fix:** Add an explicit terminal/takeover route and a tested transport acknowledgment.  
**Severity:** Critical

## 3. Complaint Text Becomes Customer Name

**Transcript example:** `This is ridiculous and unacceptable.` -> memory name `Ridiculous And Unacceptable`; `I am frustrated because this is still not working.` -> `Still Not Working`. Persisted Vapi session also contains `Their First Escape Room`.  
**Root cause:** `ConversationMemory._extract_name()` accepts generic `this is`/`I am` clauses, while `_is_plausible_name()` only uses a short word denylist.  
**Recommended fix:** Restrict implicit name extraction to a name-expected state or explicit name label and add negative semantic patterns.  
**Severity:** High

## 4. Refund Request Is Treated As Policy FAQ

**Transcript example:** `I want a refund.` -> cancellation-policy response, no escalation or summary.  
**Root cause:** `EscalationAgent.evaluate()` recognizes only explicit dispute phrases; inbound intent routing maps the request to cancellation flow.  
**Recommended fix:** Distinguish informational policy questions from refund actions and route actions to verified human handling.  
**Severity:** High

## 5. FAQ During Booking Is Ignored

**Transcript example:** `Where do we park?` during slot selection returns available-slot progression; the current regression test asserts `parking` is absent.  
**Root cause:** `BookingAgent.handle_message()` sets `interruption_answer=None` whenever `booking_started` is true, except for hard-coded cancellation, price, discount, and slot branches.  
**Recommended fix:** Answer grounded FAQs inside BookingAgent, preserve the state machine, then append only the pending field.  
**Severity:** High

## 6. Stale Slot Survives Room And Date Changes

**Transcript example:** With `selected_slot=3:00 PM`, `Change the room to Hostage` leaves both memory and internal selected slot at 3 PM; changing the date similarly leaves the previous slot persisted while presenting new slots.  
**Root cause:** The modification branch updates room/date and rechecks availability but never clears `memory.selected_slot` or `_selected_slot`.  
**Recommended fix:** Invalidate slot, cart signature/ID, and relevant cached availability whenever room, date, or location changes.  
**Severity:** High

## 7. Room-Change Acknowledgment Is Rewritten Away

**Transcript example:** `Change the room to Hostage` produced `What is your first name?` despite changing memory to Hostage.  
**Root cause:** The deterministic draft includes a room description; `ResponseComposer.compose()` sees booking lock plus description and replaces the entire draft with `_booking_progress_response()`.  
**Recommended fix:** Keep modification acknowledgment separate from optional descriptions and test the final composed response.  
**Severity:** Medium

## 8. Repeated Room Descriptions And Recommendation Loops

**Transcript example:** Session `019eee3c-f56c-7000-9608-06b09590b011` repeats the same Murder Mystery description at least five times, including after a confirmation question. Other persisted sessions repeatedly ask age group after adults are stored.  
**Root cause:** Historical inbound FAQ/recommendation generation used static `get_demo_answer()` and qualification state instead of booking state. Current booking guards reduce this path but no current transcript-replay monitor proves elimination in production.  
**Recommended fix:** Add automated production-session assertions for repeated normalized response/question patterns; retain deterministic booking-state priority.  
**Severity:** Medium

## 9. Overlong Slot And Room Lists

**Transcript example:** Persisted Vapi sessions speak 18 slot times in one response and dump six room descriptions before asking age group.  
**Root cause:** `BookingAgent._handle_availability_check()` formats every slot; static FAQ answers can return the full catalog.  
**Recommended fix:** Page or summarize slot choices and require explicit full-list intent for catalog dumps.  
**Severity:** Medium

## 10. Robotic Generic Wording

**Transcript example:** Current fallback paths include `I've processed your request. Let me assist you with that. Could you please confirm your name?`; repeated sessions use `Perfect` and `Got it` even after failures.  
**Root cause:** Deterministic exception/fallback templates in `main.dispatch()` and inbound fallback generation are not context-sensitive.  
**Recommended fix:** Correct the source routing/failure state and use concise deterministic error responses; no personality rewrite is required.  
**Severity:** Medium
