# TOP 20 BREAKAGES — Breakout Agent MVP

This report compiles the top 20 architectural and conversational breakages identified during the Red-Team audit of 50 adversarial customer conversations against the live Breakout Escape Rooms booking backend.

---

## Severity Legend

| Level | Definition |
|---|---|
| 🔴 CRITICAL | Bypasses safety/legal filters, causes critical data/state synchronization loss, or loops the user indefinitely. |
| 🟠 HIGH | Causes operational dead-ends, ignores user input (names/phone/dates), or fails to support standard changes midway. |
| 🟡 MEDIUM | Functional gaps or UX limitations that degrade conversation quality. |
| 🟢 LOW | Minor edge-case gaps with workarounds. |

---

## 1. Critical Breakages (🔴 Critical)

### #1 — Memory Pre-Merge Disables Change Detection (Architectural Bug)
* **Scenario ID:** SCEN-006, SCEN-007, SCEN-016, SCEN-017
* **Source Code:** `main.py` → `dispatch()` line 237 vs `booking_agent.py` line 273
* **Trigger:** Customer says: *"Actually, let's change the date to June 25."* or *"I want Hostage instead."*
* **Root Cause:** The `dispatch()` routing loop calls `inbound.memory.merge_message(message, booking_intent)` **before** invoking `booking.handle_message(message)`. Because the memory already contains the updated value when `BookingAgent` checks `date_changed` (`target_date != preferred_date`) or `room_changed`, the diff evaluates to `False`. The change request is ignored, and the agent repeats the slot selection warning or current qualification question.
* **Business Impact:** Customer changes their date or room mid-booking, but the agent ignores the change, forcing them to book the wrong date/room or causing them to hang/loop.

### #2 — Safety Emergency Requests Bypassed ("panic attack", "breathing problem")
* **Scenario ID:** SCEN-043, SCEN-044, SCEN-045
* **Source Code:** `src/agents/escalation_agent.py` → `SAFETY_REQUEST` regex
* **Trigger:** Customer says: *"I am having a panic attack and cannot breathe"* or *"Someone has a breathing problem."*
* **Root Cause:** The `SAFETY_REQUEST` regular expression lacks matches for `"panic attack"`, `"breathing problem"`, or `"emergency help"`.
* **Business Impact:** The agent fails to escalate a medical emergency and instead responds with: *"Got it. How many people are joining?"*, creating a massive safety and liability issue.

### #3 — Ignored Name/Phone/Age Corrections during Slot Selection
* **Scenario ID:** SCEN-001, SCEN-011, SCEN-012, SCEN-046
* **Source Code:** `src/agents/booking_agent.py` → `_STATE_WAITING_FOR_SLOT`
* **Trigger:** When the agent prompts for slot selection, the user responds: *"My name is Siddharth Patel."* or *"My phone is 9876543210"* instead of choosing a slot.
* **Root Cause:** In the `_STATE_WAITING_FOR_SLOT` state, the agent's state machine expects slot selection first. If slot extraction fails, it rejects the turn and loops on slot selection, ignoring the name or phone provided (even though they are successfully parsed and saved in memory).
* **Business Impact:** Customer tries to correct details or give contact info during slot selection, but the agent ignores them and repeats: *"Sorry, I didn't catch that. Which time would you prefer?"*

### #4 — Standalone Participant count changes ignored during Slot Selection
* **Scenario ID:** SCEN-021, SCEN-022, SCEN-023, SCEN-024
* **Source Code:** `src/agents/booking_agent.py` → `_STATE_WAITING_FOR_SLOT`
* **Trigger:** Customer says: *"We are 15 people."* during slot selection.
* **Root Cause:** The `is_change_request` check only evaluates to True if `any_field_changed` is detected. While the participant count is updated in memory via the pre-merge dispatcher, the state machine does not trigger a capacity check or re-run availability because the slots list is not refreshed.
* **Business Impact:** Customer increases group size mid-booking (e.g. from 2 to 15), but the agent continues to prompt for slots for the old group size, resulting in failed bookings at checkout.

### #5 — Telephony Transfer Transport is Mock-Only
* **Scenario ID:** SCEN-039, SCEN-040
* **Source Code:** `main.py` → `_enrich_conversation_result()`
* **Trigger:** Customer says: *"Get me a human"* or *"I want to talk to your manager."*
* **Root Cause:** The code sets `next_agent="escalation_agent"` and `should_handoff=True`, but there is no actual telephony protocol (e.g. Vapi transfer dial payload) connected, so Vapi telephony integrations will hang or loop.
* **Business Impact:** Telephony calls requesting a human will stay connected to the bot instead of transferring to the call center queue.

---

## 2. High Breakages (🟠 High)

### #6 — Spelled-Out phone digits fail validation
* **Scenario ID:** SCEN-047
* **Source Code:** `src/memory/conversation_memory.py` → `_extract_phone()`
* **Trigger:** Customer dictates phone number as words: *"double nine eight two two five..."*
* **Root Cause:** The phone regex strips non-digits, but does not translate spoken numbers like "double nine" into digits ("99"), causing phone validation to fail and loop.
* **Business Impact:** Voice callers dictating phone numbers naturally will get stuck in an infinite loops.

### #7 — Alternative Date Loop Dead-End
* **Scenario ID:** SCEN-008, SCEN-009
* **Source Code:** `src/agents/booking_agent.py` → `_STATE_WAITING_FOR_ALT_DATE`
* **Trigger:** User chooses an unavailable date twice.
* **Root Cause:** After the second check, the agent transitions to `_STATE_CLOSED` and shuts down the session. Any subsequent customer messages hit `_handle_post_booking()` which only replies with the concierge greeting, blocking the user from starting a new booking.
* **Business Impact:** Customers are abandoned without a restart option, losing high-value sales.

### #8 — Ambiguous Multi-Location inputs silently choose first tuple match
* **Scenario ID:** SCEN-048
* **Source Code:** `src/memory/conversation_memory.py` → `_extract_location()`
* **Trigger:** Customer says: *"JP Nagar or Whitefield."*
* **Root Cause:** The location extractor loops through the `LOCATIONS` tuple in order. If multiple locations are found, it silently matches the first one in the tuple list rather than prompting the user for clarification.
* **Business Impact:** Wrong branch booked without customer consent.

### #9 — Honorific Name Extraction Pollution
* **Scenario ID:** SCEN-007 (Variant)
* **Source Code:** `src/memory/conversation_memory.py` → `_extract_name()`
* **Trigger:** Customer says: *"My name is Dr. Siddharth"*
* **Root Cause:** The regex captures "Dr" as first name and "Siddharth" as last name, which will cause Kreeda checkout API calls to fail due to name mismatches.
* **Business Impact:** Customer fails to check out because name is malformed.

### #10 — GST Invoice FAQ Gap
* **Scenario ID:** SCEN-029
* **Source Code:** `src/knowledge/demo_knowledge.py`
* **Trigger:** Customer asks: *"Can we get a GST invoice?"*
* **Root Cause:** There is no GST invoice entry in the knowledge base, leading the agent to say *"I don't have a verified answer for that..."* and breaking the corporate qualification flow.
* **Business Impact:** Corporate customers fail to qualify and drop out of the funnel.

---

## 3. Medium Breakages (🟡 Medium)

### #11 — Qualification loops on Expected Fields
* **Scenario ID:** SCEN-026, SCEN-027, SCEN-028, SCEN-031
* **Source Code:** `src/agents/qualification_agent.py`
* **Trigger:** The qualification agent asks for one field (e.g., age group), and the user answers with another (e.g., location, name).
* **Root Cause:** The qualification agent successfully updates the other fields in memory, but repeats the age group question because it is still the next expected field, causing a repetitive conversational loop that triggers the loop detector.
* **Business Impact:** High frustration, leading to premature human handoff.

### #12 — Cancellation Loop on missing reference
* **Scenario ID:** SCEN-013, SCEN-014
* **Source Code:** `src/agents/booking_agent.py` → `_handle_booking_reference`
* **Trigger:** User says *"I want to cancel my booking"* but has no reference.
* **Root Cause:** The agent enters `_STATE_WAITING_FOR_BOOKING_REF` and loops on asking for the reference number, with no option to exit or look up by name.
* **Business Impact:** Customer gets stuck in an infinite cancellation loop.

### #13 — Spelled-out ordinal dates fail to parse
* **Scenario ID:** SCEN-047
* **Source Code:** `src/memory/conversation_memory.py` → `_extract_preferred_date()`
* **Trigger:** Customer says: *"June twenty-fifth"*
* **Root Cause:** Spelled-out ordinals are not resolved by the date parser, leading to empty values or incorrect relative dates.
* **Business Impact:** Conversation loop on date collection.

### #14 — No Evening/Morning Slot Filtering
* **Scenario ID:** SCEN-003
* **Source Code:** `src/agents/booking_agent.py` → `_slot_summary_response`
* **Trigger:** Customer asks for: *"only evening slots"*
* **Root Cause:** If slots are not cached, it returns: *"I don't have any verified slots to show yet."* instead of querying availability and then filtering.
* **Business Impact:** Customer gets confused and drops out.

### #15 — Earliest Slot selection loop
* **Scenario ID:** SCEN-002
* **Source Code:** `src/agents/booking_agent.py` → `_slot_summary_response`
* **Trigger:** Customer says: *"first available"*
* **Root Cause:** If slots cache is empty, it fails to resolve.
* **Business Impact:** Loop on slot selection.

### #16 — Bare Name Corrections Ignored
* **Scenario ID:** SCEN-001 (Variant)
* **Source Code:** `src/memory/conversation_memory.py` → `_extract_name()`
* **Trigger:** Customer says: *"No, Siddharth"* during phone collection.
* **Root Cause:** `has_explicit_name_signal` is False because there is no name prefix (e.g. "I am"), so the name correction is ignored.
* **Business Impact:** Stale/wrong name remains in memory.

---

## 4. Low Breakages (🟢 Low)

### #17 — Duplicate Charge Inquiries during Booking Muddy Handoff Summary
* **Scenario ID:** SCEN-038
* **Root Cause:** Complaining about charges mid-booking triggers immediate escalation but includes partially completed booking details in the handoff.
* **Business Impact:** Human agent gets a confusing summary.

### #18 — Vegan Food options FAQ Gap
* **Scenario ID:** SCEN-004
* **Root Cause:** "Vegan" is not in the knowledge base, leading to generic or empty responses.
* **Business Impact:** Minor customer dissatisfaction.

### #19 — Solo Booking (1 player) Capacity Warnings
* **Scenario ID:** SCEN-020
* **Root Cause:** Solo bookings are accepted but don't warn the user that some rooms require a minimum of 2 players.
* **Business Impact:** Customer might book a room they cannot play.

### #20 — Excessive Participant Count (e.g., "1000 people") checks
* **Scenario ID:** SCEN-025
* **Root Cause:** Accepts large numbers and runs availability instead of immediately rejecting.
* **Business Impact:** Performance lag during unnecessary API checks.

---

## 5. Top 10 Tech Lead Break Scenarios (For Demo Tomorrow)

A Tech Lead or Tester can easily break the live demo by executing these 10 conversational sequences:

### 1. Standalone Slot Change Mid-Qualification
* **Sequence:**
  1. Cust: *"Book Murder Mystery at Koramangala for tomorrow."*
  2. Agent: *[Lists slots: 11:30 AM, 12:50 PM, 2:10 PM...]*
  3. Cust: *"Actually, let's make it 3:30 PM."*
  4. Agent: *"Perfect, I've found that slot. Before I lock it in, may I get your first name?"*
  5. Cust: *"Actually, let's switch the slot to 6:10 PM."*
* **Why it breaks:** The slot change will be ignored because the agent is in `_STATE_WAITING_FOR_FIRST_NAME` and only parses name inputs, repeating the name question.

### 2. Date Change Mid-Booking (via Dispatcher)
* **Sequence:**
  1. Cust: *"Book Murder Mystery at Koramangala for tomorrow."*
  2. Cust: *"Actually, change the date to June 25."*
* **Why it breaks:** The dispatcher pre-merges `"25 June"` into memory before calling `booking.handle_message()`. The agent sees that memory matches the input, detects no change, and repeats the old slot list for tomorrow.

### 3. Panic Attack / Safety Emergency Request Bypassed
* **Sequence:**
  1. Cust: *"Book Hostage at Whitefield."*
  2. Agent: *"What date would you like to visit?"*
  3. Cust: *"I am having a panic attack and cannot breathe."*
* **Why it breaks:** The agent will not escalate, but instead say: *"Got it. How many people are joining?"* because `"panic attack"` is not in the safety regex.

### 4. Spelled-out phone digits loop
* **Sequence:**
  1. Cust: *"Book Hostage at Whitefield for June 25 at 3:00 PM."*
  2. Agent: *"Perfect. What is your first name?"*
  3. Cust: *"Riya."*
  4. Agent: *"What is your last name?"*
  5. Cust: *"Patel."*
  6. Agent: *"What is the phone number for the booking?"*
  7. Cust: *"My phone is double nine eight two two five..."*
* **Why it breaks:** Spelled-out phone numbers fail validation, and the agent loops on the phone question.

### 5. Multi-Location Ambiguity Silently Chosen
* **Sequence:**
  1. Cust: *"I want to book Prison Break at Koramangala or Whitefield."*
* **Why it breaks:** The agent will silently select Koramangala without asking for clarification, because it matches first in the `LOCATIONS` tuple.

### 6. GST Invoice FAQ Gap during Corporate Qualification
* **Sequence:**
  1. Cust: *"I want to book a corporate event."*
  2. Agent: *"How many employees are joining?"*
  3. Cust: *"We are 30 people. Can we get a GST invoice for the event?"*
* **Why it breaks:** The agent replies: *"I don't have a verified answer for that..."* because GST invoice policy is missing from the knowledge base, stalling the qualification.

### 7. Name Rejection loop with "Joy" or "June"
* **Sequence:**
  1. Cust: *"Book Murder Mystery at Koramangala for June 25 at 3:00 PM."*
  2. Agent: *"Perfect. Before I lock it in, may I get your first name?"*
  3. Cust: *"My name is June."*
* **Why it breaks:** "June" is in `rejected_words`, so it rejects the name and loops.

### 8. Alternative Date loop CLOSED dead-end
* **Sequence:**
  1. Cust: *"Book Prison Break at Koramangala for June 24."* (Unavailable date)
  2. Agent: *"We don't have availability on 24 June. What alternative date?"*
  3. Cust: *"June 25."* (Also unavailable)
  4. Agent: *"We don't have availability on June 25 either. Shall I arrange callback?"*
  5. Cust: *"Wait, check June 26 instead."*
* **Why it breaks:** The session transitions to `_STATE_CLOSED`. Any new date check will be ignored, repeating the concierge greeting.

### 9. Cancellation request without reference loop
* **Sequence:**
  1. Cust: *"I need to cancel my booking."*
  2. Agent: *"What is your booking reference?"*
  3. Cust: *"I don't have it."*
* **Why it breaks:** The agent loops indefinitely asking for the reference number.

### 10. Room Change Mid-Booking (via Dispatcher)
* **Sequence:**
  1. Cust: *"Book Murder Mystery at JP Nagar for June 25 at 3:15 PM."*
  2. Cust: *"Actually, I want Hostage instead."*
* **Why it breaks:** The dispatcher pre-merges room into memory. The agent thinks room has not changed, and loops on slot selection for Murder Mystery.
