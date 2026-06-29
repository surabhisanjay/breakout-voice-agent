# QA Verification Report: Breakout Agent MVP

This report evaluates the production readiness of the Breakout Inbound & Booking Agent MVP by mentally executing 15 key conversation scenarios against the system architecture, memory manager, routing logic, and recent production blocker fixes.

---

## 1. Scenario Execution Analysis

### Scenario 1: FAQ Interruption during booking
* **Expected Behavior:** While in the active booking flow (`booking_started=True`), the agent answers the FAQ (e.g., parking, duration) from verified knowledge and immediately resumes by asking for the next missing booking detail (e.g., slot choice, date, name).
* **Potential Failure Points:** 
  1. The user asks an FAQ using words that do not match the narrow regex in `BookingAgent._is_booking_faq()`. This causes the routing to fall through to `InboundAgent` which might reset the workflow or lose context.
  2. The `ResponseComposer` (using OpenAI) rewrites the prompt too broadly and drops the resume question.
* **Severity:** Medium
* **Confidence:** High

### Scenario 2: Date change during booking
* **Expected Behavior:** The customer changes the date mid-booking. The agent clears any previously selected slot/cart state from memory, registers the new date, checks live availability, and prompts the user to select from the new slots.
* **Potential Failure Points:**
  1. If the date parser fails to normalize the input date, the update won't occur.
  2. If the provider availability tool check fails or times out, the agent gets stuck in `_STATE_WAITING_FOR_ALT_DATE` or fallback mode.
* **Severity:** Low
* **Confidence:** High

### Scenario 3: Room change during booking
* **Expected Behavior:** The customer changes the room mid-booking. The agent clears the selected slot and cart, updates the room in memory, calls availability, and prompts for slot selection.
* **Potential Failure Points:**
  1. If the room name doesn't match the hardcoded `rooms` mapping in `BookingAgent.handle_message()`, the agent won't detect the change.
* **Severity:** Low
* **Confidence:** High

### Scenario 4: Human escalation
* **Expected Behavior:** The customer explicitly asks to speak to a person. The agent immediately triggers escalation via `EscalationAgent.evaluate()`, sets `should_handoff=True` and `next_agent="escalation_agent"`, returns a polite handoff response, and generates a structured summary with all collected fields.
* **Potential Failure Points:**
  1. A spelling error in the user's request (e.g., "human" transcribed incorrectly or minor typos like "talk to a preson") failing to match `HUMAN_REQUEST` regex.
* **Severity:** Low
* **Confidence:** High

### Scenario 5: Refund escalation
* **Expected Behavior:** The customer asks for a refund. The agent detects this via `REFUND_REQUEST` regex, explains that it cannot approve refunds directly, sets `next_agent="escalation_agent"` and `should_handoff=True`, and populates a handoff summary.
* **Potential Failure Points:**
  1. Customer uses phrasing not captured by the `REFUND_REQUEST` regex (e.g., "I want my transaction reversed" or "charge back my card").
* **Severity:** Low
* **Confidence:** High

### Scenario 6: Angry customer
* **Expected Behavior:** The customer shows anger/frustration (e.g. using profanity, complaining about repeating themselves). The `SentimentAgent` detects "angry" sentiment or repeated frustration, which `EscalationAgent` evaluates as an escalation trigger, leading to an immediate human handoff.
* **Potential Failure Points:**
  1. Sarcastic or passive-aggressive anger misclassified as "neutral" by `SentimentAgent`, failing to escalate immediately.
* **Severity:** Medium
* **Confidence:** High

### Scenario 7: Name extraction edge cases
* **Expected Behavior:** Rejection of complaints/sentiment words from name field. If a single name is provided, the agent prompts for the last name to complete booking.
* **Potential Failure Points:**
  1. Real names that are also common dictionary words (e.g. "Friday", "June", "Joy") will be rejected by the plausibility list.
  2. Multi-word names (e.g., "John De Souza") might get truncated or rejected.
* **Severity:** Medium
* **Confidence:** High

### Scenario 8: ASR location variations
* **Expected Behavior:** Fuzzy location matching maps misheard names like "JP nuggets" or "White shield" to canonical locations ("JP Nagar", "Whitefield", "Koramangala").
* **Potential Failure Points:**
  1. Extreme phonetic distortions that fall below the difflib confidence thresholds (0.55/0.65) and trigger repeat location questions.
* **Severity:** Low
* **Confidence:** High

### Scenario 9: Show all available slots
* **Expected Behavior:** The agent formats the slots in a clean, list format (e.g., "10:00 AM, 12:00 PM, and 3:00 PM") and prompts the user to select one.
* **Potential Failure Points:**
  1. Empty slots cache causing the agent to state no slots are available.
* **Severity:** Low
* **Confidence:** High

### Scenario 10: First available slot
* **Expected Behavior:** The customer says "book the first available slot". The agent sorts the slots by time, selects the earliest slot, stores it in memory, and prompts for the next detail.
* **Potential Failure Points:**
  1. "First slot" trigger phrase not in `first_slot_phrases`, or slots list is empty.
* **Severity:** Medium
* **Confidence:** High

### Scenario 11: Large-group booking
* **Expected Behavior:** The customer has a large group (e.g., 15 players) that exceeds room capacity. The agent detects capacity is unsupported, transitions to `_STATE_COORDINATION` (workflow: `large_group_coordination`), explains the capacity limitation, collects name and phone, sets `coordination_ready=True`, and hands off to `events_team`.
* **Potential Failure Points:**
  1. If the customer refuses to provide contact info, or if the agent incorrectly routes them to `booking_agent` instead of `events_team`.
* **Severity:** Low
* **Confidence:** High

### Scenario 12: Corporate event
* **Expected Behavior:** Qualifies corporate event intent, collects requirements (company size, location, preferred date), identifies it as a package inquiry, routes to `events_team` with a structured summary.
* **Potential Failure Points:**
  1. Customer demands exact pricing/discounts or custom quotes mid-call.
* **Severity:** Low
* **Confidence:** High

### Scenario 13: Booking confirmation safety
* **Expected Behavior:** Explicitly states that booking is not confirmed until a reference ID is returned from the provider.
* **Potential Failure Points:**
  1. OpenAI response composer using overly optimistic/affirmative language before ID is persisted.
* **Severity:** High
* **Confidence:** High

### Scenario 14: Slot change
* **Expected Behavior:** Allows customer to change the selected slot mid-booking and prompts for confirmation/reruns availability.
* **Potential Failure Points:**
  1. **CRITICAL GAP:** Standalone slot changes are ignored during name/phone/age qualification states. `any_field_changed` does not check `slot_changed`, meaning `is_change_request` evaluates to False, and the agent falls back to parsing name/phone from the time change string, causing a complete conversational breakdown.
* **Severity:** Critical
* **Confidence:** High

### Scenario 15: Mixed conversation flow
* **Expected Behavior:** Smooth transitions between booking fields, FAQ interruptions, corrections, and eventual confirmation or coordination handoff.
* **Potential Failure Points:**
  1. Ignored slot changes during qualification.
  2. Ignored bare name corrections (without explicit prefixes like "I'm").
  3. Potential state loops.
* **Severity:** High
* **Confidence:** High

---

## 2. QA Scoring Report

* **Booking Score:** 7/10
  * *Rationale:* The deterministic gates, local cache checks, and provider contract acceptance work well. However, the system fails to validate standalone slot changes during the qualification phase.
* **Memory Score:** 7/10
  * *Rationale:* The state persists correctly on happy-paths. However, name corrections require strict explicit prefixes, and standalone slot changes do not clear or update slot state outside the slot selection phase.
* **Escalation Score:** 8/10
  * *Rationale:* The safety, refund, and human request regex-triggers are highly deterministic and robust. However, real-world telephony transport (Vapi transfer) is unverified.
* **Conversation Score:** 7/10
  * *Rationale:* Concierge follow-ups and FAQ answering are well handled. Conversational loop risk is high if users make non-happy-path changes during the intake process.
* **Demo Readiness Score:** 7/10
  * *Rationale:* Extremely polished for standard "happy-path" runs and explicit human handoffs. However, a tech lead or tester can easily break the flow by requesting a slot change during the intake phase.

---

## 3. Top 10 Tech Lead Break Scenarios

These are the top 10 test scenarios a Tech Lead is most likely to use to break the MVP:

1. **Standalone Slot Change Mid-Qualification:** Choose a slot, and when the agent asks "What is your first name?", reply: *"Actually, let's make it 5 PM instead."*
   * *Why it breaks:* `any_field_changed` only tracks room, location, participants, and date. The slot change will be ignored and the agent will repeat the name request.
2. **Bare Name Correction During Phone/Age Step:** When the agent asks for the phone number, reply: *"No, it's Siddharth, not Riya."*
   * *Why it breaks:* Without an explicit name prefix (e.g., "my name is"), `has_explicit_name_signal` evaluates to False, causing the agent to ignore the name correction.
3. **Fuzzy Location Mishearing Outside Dictionary:** Say a location that is phonetically similar to JP Nagar but not in the fuzzy lookup (e.g. *"Jeep Nager"* or *"J P Neighborhood"*).
   * *Why it breaks:* Difflib cutoffs are set to 0.55/0.65; highly distorted transcriptions will fail to map and trigger a re-ask loop.
4. **Real Customer Name Overlapping Rejection Set:** Tell the agent: *"My name is June."* or *"My name is Joy."*
   * *Why it breaks:* "June" and "Joy" are common dictionary/date words included in `rejected_words`, causing the agent to reject the name.
5. **Vague Large Group Range Split Request:** Say: *"We are 15 to 20 people. Can you split us across two rooms?"*
   * *Why it breaks:* The agent sets the participants to 20, triggers the events team escalation for coordination, but won't be able to suggest which rooms or split them, and might repeat questions if the user pushes for a self-serve booking.
6. **FAQ Posed with Non-Matching Keywords:** Ask an FAQ using words not matched by `_is_booking_faq`: *"Is the room air conditioned?"* or *"Do you have lockers for my bags?"*
   * *Why it breaks:* Since these don't match the hardcoded booking FAQ keywords, it will fail to answer them or route to `inbound_agent` and reset current workflow.
7. **Implicit Name Extraction with Multiword Clauses:** When asked for a name, say: *"I am the person booking this slot."*
   * *Why it breaks:* The agent might parse "Person" as the name, or if it checks plausibility it might reject it, but it could pollute memory.
8. **Interrupted Flow with Duplicate Charge Inquiry during Booking:** During the booking slot selection, say: *"Wait, I think you charged my card twice for my last game."*
   * *Why it breaks:* This triggers refund/charge escalation immediately, but the handoff summary might get messy or have a mix of new booking info and refund info.
9. **Cancellation request without a Booking Ref:** Say: *"I need to cancel my booking."* When the agent asks for the reference, reply: *"I don't have it, but it was for 3 PM tomorrow."*
   * *Why it breaks:* The agent will get stuck in a loop asking for the reference because `_handle_booking_reference` strictly checks the pattern `[A-Za-z0-9_-]{4,80}`.
10. **ASR phone number with spoken double digits:** Say: *"My phone number is double nine eight two two double five..."*
    * *Why it breaks:* ASR might transcribe "double nine" as "double 9" or "99", but if it transcribes words, the phone extractor `_extract_phone` which uses `re.sub(r"\D", "", ...)` will strip them out, leading to invalid number errors or loops.
