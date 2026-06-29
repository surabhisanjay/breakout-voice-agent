# TOP 20 FAILURES — Ranked by Business Severity
**QA Audit Phase 3 — Failure Prioritisation**  
**Date:** 2026-06-22  
**Source:** RED_TEAM_SCENARIOS.md + SCENARIO_EXECUTION_REPORT.md

---

## Severity Legend

| Level | Definition |
|-------|-----------|
| 🔴 CRITICAL | Safety risk, legal risk, or false booking confirmation |
| 🟠 HIGH | Booking dead-end, customer data loss, or significant UX failure |
| 🟡 MEDIUM | Functional gap with a workaround path |
| 🟢 LOW | Minor UX polish gap |

---

## Ranked Failures

### #1 — Safety Escalation Silently Dropped on Exception
**Severity:** 🔴 CRITICAL  
**Scenario ID:** E004 / GAP-013  
**Source File:** `main.py` → `_enrich_conversation_result()`  
**Trigger:** `HandoffSummaryAgent.generate()` raises any exception after a safety escalation (fire, injury, medical emergency)  
**Actual Behaviour:** The `except Exception:` block catches the exception and returns `{"escalate": False}` — the safety escalation is completely silently dropped. Staff are never notified.  
**Business Impact:** Customer trapped in a room with a fire or medical emergency receives no response from the agent. Liability risk.  
**Fix Required:** Never suppress escalation on exception — return safe escalation response even without summary. Log exception but do not swallow escalate=True.

---

### #2 — False Cancellation Confirmation (No Booking Ref)
**Severity:** 🔴 CRITICAL  
**Scenario ID:** A015 / GAP-012  
**Source File:** `booking_agent.py` → `_handle_cancel_request()`  
**Trigger:** User says "I want to cancel my booking" with no `booking_ref` in memory  
**Actual Response:** `"No problem. I've cancelled the booking process for you."`  
**Business Impact:** Agent falsely confirms cancellation of a non-existent booking. If a real booking exists on the provider side (reference not stored in memory), it is NOT cancelled. Customer believes they've cancelled — they haven't. Potential no-show fee charged.  
**Fix Required:** Check `booking_ref` before claiming cancellation. If no ref: say "I don't have a booking reference on file — could you share the booking ID or reference number?"

---

### #3 — Medical Emergency Not Detected ("fainted")
**Severity:** 🔴 CRITICAL  
**Scenario ID:** E010 / BUG-005  
**Source File:** `escalation_agent.py` → `SAFETY_REQUEST` regex  
**Trigger:** `"my friend fainted inside the room"`, `"someone passed out"`, `"person collapsed"`  
**Actual Behaviour:** No escalation fired; customer receives generic response  
**Business Impact:** Medical emergency in the escape room goes unresponded. Ambulance not called. Severe liability risk.  
**Fix Required:** Add `faint|fainted|collapsed|pass.?out|unconscious|seizure|heart attack` to `SAFETY_REQUEST` pattern.

---

### #4 — "Get Me a Human" Does Not Escalate
**Severity:** 🔴 CRITICAL  
**Scenario ID:** E013 / BUG-002  
**Source File:** `escalation_agent.py` → `HUMAN_REQUEST` regex  
**Trigger:** `"get me a human"`, `"connect me to an agent"`, `"connect me with someone"`, `"talk to your manager"`  
**Actual Behaviour:** No escalation — pattern requires `speak|talk|connect|transfer` + specific role  
**Business Impact:** Frustrated customers explicitly requesting human help are ignored. Escalations to complaints/social media.  
**Fix Required:** Add `get\s+me\s+a\s+(?:human|person)`, `connect\s+me\s+(?:with\s+)?(?:someone|anyone)`, `(?:talk|speak)\s+to\s+(?:your|the)\s+manager`, and `agent` to the synonym list.

---

### #5 — "Day After Tomorrow" Extracts as "Tomorrow"
**Severity:** 🔴 CRITICAL  
**Scenario ID:** A024 / GAP-001  
**Source File:** `conversation_memory.py` → `_extract_preferred_date()`  
**Trigger:** `"day after tomorrow"` or `"the day after tomorrow"`  
**Actual Extracted:** `"Tomorrow"` (substring "tomorrow" matches in the relative_terms loop)  
**Business Impact:** Booking confirmed for the wrong day (D+1 instead of D+2). Customer arrives a day late. No-show fee charged. Slot occupied by wrong booking.  
**Fix Required:** Check `"day after tomorrow"` BEFORE checking `"tomorrow"` in the relative terms list.

---

### #6 — Large Group Dead-End After Capacity Limitation
**Severity:** 🟠 HIGH  
**Scenario ID:** H004 / GAP-010  
**Source File:** `booking_agent.py` → `_STATE_CLOSED` after capacity limitation  
**Trigger:** Group of 15–50; capacity check returns `capacity_supported=False`  
**Actual Behaviour:** Agent enters `_STATE_CLOSED`. Any follow-up message hits `_handle_post_booking()` which closes again. User cannot restart without ending the session.  
**Business Impact:** Potential corporate/birthday customers (high value) are abandoned. No callback or email capture attempted.  
**Fix Required:** After capacity limitation response, transition to `_STATE_CHECKING_AVAILABILITY` or offer callback instead of CLOSED. Capture phone for callback.

---

### #7 — Refund Phrases Missing ("deserve", "give me my money back")
**Severity:** 🟠 HIGH  
**Scenario ID:** E003, E_ref2 / BUG-006, BUG-007  
**Source File:** `escalation_agent.py` → `REFUND_REQUEST` regex  
**Trigger:** `"I deserve a refund"`, `"give me my money back"`, `"I want my money back"`  
**Actual Behaviour:** No escalation — only `want|need|request|demand|expecting|asking for` prefixes matched  
**Business Impact:** Customer with refund grievance left without escalation path. Likely to escalate via social media or dispute the charge.  
**Fix Required:** Add `deserve\s+a\s+refund`, `give\s+me\s+(?:my\s+)?money\s+back`, `want\s+(?:my\s+)?money\s+back` to REFUND_REQUEST.

---

### #8 — "Book" and "Game" Accepted as Valid Customer Names
**Severity:** 🟠 HIGH  
**Scenario ID:** J010 / BUG-001, GAP-002  
**Source File:** `conversation_memory.py` → `_is_plausible_name()` rejected word list  
**Trigger:** User says "book" or "game" when agent asks for their name  
**Actual Behaviour:** `_is_plausible_name("Book")` = True; stored as `customer_name = "Book"`  
**Business Impact:** API call to Kreeda fails with malformed first name "Book". Booking cannot be confirmed. User stuck in retry loop.  
**Fix Required:** Add `book`, `game`, `games`, `yes`, `no`, `ready`, `wait`, `okay` etc. to the rejected word list in `_is_plausible_name()`.

---

### #9 — Past Date Silently Advanced to Next Year
**Severity:** 🟠 HIGH  
**Scenario ID:** A023 / GAP-011  
**Source File:** `booking_orchestrator.py` → `_normalise_date()`  
**Trigger:** User gives a past date like `"1 January"` (in January it would still be valid, but mid-year it's past)  
**Actual Behaviour:** `_normalise_date("1 January")` → `"2027-01-01"` with no user notification  
**Business Impact:** Booking confirmed for a year later than intended. Customer doesn't notice; receives confirmation email for wrong year.  
**Fix Required:** When date is bumped to next year, the BookingAgent's response must include "I've set this for January 2027 — is that correct?" before proceeding.

---

### #10 — "Game" Keyword in Name Plausibility
**Severity:** 🟠 HIGH  
**Scenario ID:** J010-variant  
**Status:** See #8 above — same root cause.

---

### #11 — "Cancel My Reservation" Not Matched by Cancel Pattern
**Severity:** 🟠 HIGH  
**Scenario ID:** A016  
**Source File:** `booking_agent.py` → `_is_cancel_request()` / inbound_agent → cancel detection  
**Trigger:** `"I want to cancel my reservation"` (uses "reservation" not "booking")  
**Actual Behaviour:** Agent may not detect cancellation intent; proceeds with wrong flow  
**Fix Required:** Add `reservation|appointment|slot` as synonyms in cancel regex.

---

### #12 — Ambiguous Location Silently Resolved to First Tuple Match
**Severity:** 🟡 MEDIUM  
**Scenario ID:** I004 / GAP-003  
**Source File:** `conversation_memory.py` → `_extract_location()` — iterates LOCATIONS tuple in order  
**Trigger:** `"whitefield or koramangala"` → extracts `"Koramangala"` (Koramangala is first in tuple)  
**Actual Behaviour:** User who said Whitefield first gets Koramangala silently chosen  
**Business Impact:** Wrong location booked; customer arrives at wrong venue  
**Fix Required:** Detect ambiguous location (both found in same message); ask "Did you mean Whitefield or Koramangala?"

---

### #13 — Duration FAQ Not Answered ("how long is the game?")
**Severity:** 🟡 MEDIUM  
**Scenario ID:** D007 / GAP-006  
**Source File:** `demo_knowledge.py` → `get_demo_answer()`  
**Trigger:** `"how long is the game?"`, `"what is the duration?"`  
**Actual Behaviour:** `get_demo_answer()` returns `""` — no answer in knowledge base  
**Business Impact:** Common pre-booking question unanswered; customer uncertain; may not book  
**Fix Required:** Add duration handler: "Each game session is 50 minutes. We recommend arriving 15 minutes early for the briefing."

---

### #14 — No Evening/Time-of-Day Slot Filter
**Severity:** 🟡 MEDIUM  
**Scenario ID:** C005, C006  
**Source File:** `booking_agent.py` / `booking_orchestrator.py`  
**Trigger:** `"only evening slots"` / `"after 6 PM"`  
**Actual Behaviour:** All available slots returned regardless of time preference  
**Fix Required:** Parse time preference from message; filter slots before display.

---

### #15 — "First Available" Triggers Sorry Loop (No Slot Selected)
**Severity:** 🟡 MEDIUM  
**Scenario ID:** C010 / GAP-008  
**Source File:** `booking_agent.py` → `_handle_slot_selection()` → `_extract_slot()`  
**Trigger:** `"I'll take the first available"`, `"first slot please"`, `"earliest one"`  
**Actual Behaviour:** `_extract_slot("first available")` returns `""` → "Sorry, I didn't catch that" loop  
**Fix Required:** Detect ordinal/first-available phrasing; auto-select the first slot from `_available_slots`.

---

### #16 — Participant Reduction Without Re-running Availability
**Severity:** 🟡 MEDIUM  
**Scenario ID:** F003 / GAP (F003)  
**Source File:** `booking_agent.py` → `_detect_field_changes()` — "reduced" not a change keyword  
**Trigger:** `"we reduced to just 2 people"` during WAITING_FOR_PHONE state  
**Actual Behaviour:** Participants updated to 2 but availability not re-run; selected slot may have capacity issues reversed (now over-restrictive)  
**Fix Required:** Add `reduce|reduced|fewer|less|dropped|shrunk` to change keyword list; trigger availability re-run.

---

### #17 — Phone Number Rejection with No Explanation
**Severity:** 🟡 MEDIUM  
**Scenario ID:** GAP-009  
**Source File:** `conversation_memory.py` → `_extract_phone()` — requires `[6-9]\d{9}`  
**Trigger:** Phone starting with 5 (e.g., `5876543210`); also Whisper ASR may produce garbled digits  
**Actual Behaviour:** Phone silently rejected; agent asks again with "I didn't catch the phone number" — no explanation of what's wrong  
**Fix Required:** When phone fails validation, clarify: "Please share a 10-digit Indian mobile number starting with 6, 7, 8, or 9."

---

### #18 — No Loop Limit on Qualification Questions
**Severity:** 🟡 MEDIUM  
**Scenario ID:** J021 / GAP  
**Source File:** `qualification_agent.py` → no max retry count per field  
**Trigger:** 5+ garbage ASR inputs in a row; agent keeps asking "How many people are joining?"  
**Actual Behaviour:** Infinite loop; no escalation after N failures; no progressive rephrasing  
**Fix Required:** After 3 failed attempts per field, trigger escalation or offer to connect with human team.

---

### #19 — "Please Connect Me With Someone" Not Escalated
**Severity:** 🟡 MEDIUM  
**Scenario ID:** E015  
**Source:** See #4 (same root cause — HUMAN_REQUEST pattern gap)  
**Status:** Fixed as part of #4.

---

### #20 — Cancelled Intent → _STATE_CLOSED Dead-End After Room Change
**Severity:** 🟢 LOW  
**Scenario ID:** A018 + A021  
**Source File:** `booking_agent.py` → `_STATE_CLOSED` blocks all subsequent input  
**Trigger:** Booking cancelled OR capacity-limited → user tries to rebook → `_handle_post_booking()` fires → closes again  
**Actual Behaviour:** User cannot restart booking without disconnecting  
**Fix Required:** In `_handle_post_booking`, detect booking-restart intent (e.g., "book again", "start over", "new booking") and call `reset_booking_state()`.

---

## Summary Table

| Rank | ID | Severity | Fixed in Phase 4? |
|------|-----|----------|-------------------|
| 1 | GAP-013 Safety escalation dropped | 🔴 CRITICAL | Yes |
| 2 | GAP-012 False cancel confirmation | 🔴 CRITICAL | Yes |
| 3 | BUG-005 Fainted not detected | 🔴 CRITICAL | Yes |
| 4 | BUG-002/3/4 Human request patterns | 🔴 CRITICAL | Yes |
| 5 | GAP-001 Day after tomorrow | 🔴 CRITICAL | Yes |
| 6 | GAP-010 Large group dead-end | 🟠 HIGH | Yes |
| 7 | BUG-006/7 Refund phrases | 🟠 HIGH | Yes |
| 8 | BUG-001 "Book"/"Game" as name | 🟠 HIGH | Yes |
| 9 | GAP-011 Past date silent year bump | 🟠 HIGH | Yes |
| 10 | (see #8) | 🟠 HIGH | Yes |
| 11 | A016 Reservation cancel | 🟠 HIGH | Yes |
| 12 | I004 Ambiguous location | 🟡 MEDIUM | Yes |
| 13 | D007 Duration FAQ gap | 🟡 MEDIUM | Yes |
| 14 | C005/6 No evening filter | 🟡 MEDIUM | Yes |
| 15 | C010 First available loop | 🟡 MEDIUM | Yes |
| 16 | F003 Participant reduction | 🟡 MEDIUM | Yes |
| 17 | GAP-009 Phone rejection msg | 🟡 MEDIUM | Yes |
| 18 | J021 No loop limit | 🟡 MEDIUM | Partial |
| 19 | E015 (see #4) | 🟡 MEDIUM | Yes (part of #4) |
| 20 | A018/21 CLOSED dead-end | 🟢 LOW | Yes |

---

*Report generated by Principal QA Engineer — Phase 3 Ranking, 2026-06-22*
