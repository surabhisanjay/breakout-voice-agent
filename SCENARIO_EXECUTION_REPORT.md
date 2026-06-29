# SCENARIO EXECUTION REPORT
**QA Audit Phase 2 — Automated Execution Results**  
**Date:** 2026-06-22  
**Baseline:** 352 tests passing (100%)  
**Red Team Tests Run:** 38 automated + 18 escalation probes = 56 total  
**Source Code Version:** MVP (main.py + app.py, pre-fix)

---

## Executive Summary

| Category | Tests Run | PASS | FAIL | Critical Bugs |
|----------|-----------|------|------|---------------|
| A. Booking Flow | 8 | 8 | 0 | 3 (documented behaviours) |
| C. Availability | 3 | 3 | 0 | 2 (gaps, not crashes) |
| D. FAQ Interruptions | 4 | 4 | 0 | 1 (gap) |
| E. Escalations | 18 | 11 | **7** | **7 (real failures)** |
| F. Memory Stress | 3 | 3 | 0 | 1 (gap) |
| G. ASR/Location | 5 | 5 | 0 | 1 (gap) |
| H. Large Groups | 2 | 2 | 0 | 1 (dead-end) |
| I. Contradictory | 3 | 3 | 0 | 1 (silent bias) |
| J. Malicious | 5 | 4 | **1** | **2 (real failures)** |
| **TOTAL** | **56** | **48** | **8** | **18 documented** |

> **8 automated test failures confirmed.** Additional behavioural gaps documented from source code analysis.

---

## Confirmed Bugs (Automated Test Failures)

### BUG-001 — `"Book"` accepted as a valid customer name
- **Scenario:** J010  
- **Input:** `_is_plausible_name("Book")`  
- **Expected:** `False` (noise/booking-intent word)  
- **Actual:** `True` (passes plausibility check)  
- **Severity:** HIGH — Agent stores "Book" as customer name; booking API call fails with malformed first name  
- **Source:** `conversation_memory.py` → `_is_plausible_name()` — "book" not in rejected word list  

### BUG-002 — `"get me a human"` does not trigger escalation
- **Scenario:** E013  
- **Input:** `"get me a human please"`  
- **Expected:** `escalate=True`  
- **Actual:** `escalate=False`  
- **Severity:** CRITICAL — Customers requesting human agent are silently ignored  
- **Source:** `escalation_agent.py` HUMAN_REQUEST pattern requires `speak|talk|connect|transfer` verbs  

### BUG-003 — `"talk to your manager"` does not trigger escalation  
- **Scenario:** E014  
- **Input:** `"I want to talk to your manager"`  
- **Expected:** `escalate=True`  
- **Actual:** `escalate=False`  
- **Severity:** CRITICAL — "your manager" not matched; requires "a manager/the manager"  
- **Source:** `escalation_agent.py` HUMAN_REQUEST pattern  

### BUG-004 — `"connect me to an agent"` does not trigger escalation
- **Scenario:** E_std2  
- **Input:** `"connect me to an agent"`  
- **Expected:** `escalate=True`  
- **Actual:** `escalate=False`  
- **Severity:** CRITICAL — "agent" not in human-request synonym list (person/human/manager/supervisor only)  
- **Source:** `escalation_agent.py` HUMAN_REQUEST pattern  

### BUG-005 — `"my friend fainted"` does not trigger safety escalation
- **Scenario:** E010  
- **Input:** `"my friend fainted inside the room"`  
- **Expected:** `escalate=True` (medical emergency)  
- **Actual:** `escalate=False`  
- **Severity:** CRITICAL — Medical emergency not detected; customer left without urgent help  
- **Source:** `escalation_agent.py` SAFETY_REQUEST pattern: `hurt|injured|emergency|fire|stuck|locked|smoke|bleed` — "fainted" missing  

### BUG-006 — `"I deserve a refund"` does not trigger escalation
- **Scenario:** E003  
- **Input:** `"I deserve a refund"`  
- **Expected:** `escalate=True`  
- **Actual:** `escalate=False`  
- **Severity:** HIGH — "deserve" not in REFUND_REQUEST pattern; customer grievance unescalated  
- **Source:** `escalation_agent.py` REFUND_REQUEST: matches `want|need|request|demand|expecting|asking for` — "deserve" missing  

### BUG-007 — `"give me my money back"` does not trigger escalation
- **Scenario:** E_ref2  
- **Input:** `"give me my money back"`  
- **Expected:** `escalate=True`  
- **Actual:** `escalate=False`  
- **Severity:** HIGH — "money back" phrase not in REFUND_REQUEST pattern  
- **Source:** `escalation_agent.py` REFUND_REQUEST  

### BUG-008 — `"please connect me with someone"` does not trigger escalation
- **Scenario:** E015  
- **Input:** `"please connect me with someone"`  
- **Expected:** `escalate=True`  
- **Actual:** `escalate=False`  
- **Severity:** CRITICAL — "someone" not in human-request synonyms; customer abandoned  
- **Source:** `escalation_agent.py` HUMAN_REQUEST pattern  

---

## Behavioural Gaps (Source Code Analysis — Not Crashes)

### GAP-001 — `"day after tomorrow"` incorrectly resolves to `"Tomorrow"`
- **Input:** `"day after tomorrow"` / `"the day after tomorrow"`
- **Actual extracted:** `"Tomorrow"` (substring match on "tomorrow")
- **Correct:** Should ask user for specific date or return empty
- **Risk:** User booked for wrong day silently; booking confirmed for +1 instead of +2
- **Severity:** HIGH

### GAP-002 — `"Game"` accepted as valid customer name
- **Input:** `_is_plausible_name("Game")`
- **Actual:** `True`
- **Severity:** MEDIUM — Less likely than "Book" but still problematic

### GAP-003 — Ambiguous location `"whitefield or koramangala"` silently picks Koramangala
- **Input:** `"whitefield or koramangala"`
- **Actual extracted:** `"Koramangala"` (first in LOCATIONS tuple)
- **Risk:** User's preferred location (Whitefield, listed first) is ignored without notification
- **Severity:** MEDIUM

### GAP-004 — `"jeep nagar"` (Whisper ASR error for "JP Nagar") actually resolves correctly
- **Input:** `"jeep nagar"`
- **Actual:** `"JP Nagar"` ✓ (fuzzy match works)
- **Status:** Better than expected — NOT a bug

### GAP-005 — `"jpnagar"` (no spaces) resolves correctly to `"JP Nagar"`
- **Input:** `"jpnagar"`
- **Actual:** `"JP Nagar"` ✓
- **Status:** Works — NOT a bug

### GAP-006 — Duration FAQ (`"how long is the game?"`) not in `demo_knowledge.py`
- **Input:** `"how long is the game?"`
- **`get_demo_answer()` returns:** `""` (empty)
- **Risk:** Agent falls through to generic/hallucinated response when OpenAI enabled
- **Severity:** MEDIUM

### GAP-007 — No evening/morning slot filter
- **User says:** `"only evening slots please"`
- **Actual:** All slots returned (10:00 AM, 12:00 PM, 6:00 PM, 8:00 PM)
- **Severity:** LOW (UX gap, not a data bug)

### GAP-008 — `"first available"` triggers slot-selection loop
- **Input:** `"first available"` during WAITING_FOR_SLOT
- **Actual:** `_extract_slot("first available")` returns `""` → "Sorry, I didn't catch that"
- **Severity:** MEDIUM — Unexpected loop for common customer phrasing

### GAP-009 — Phone numbers starting with 5 silently rejected
- **Input:** `"5876543210"`
- **Actual extracted:** `""` (phone regex: `[6-9]\d{9}` requires 6-9 start)
- **Risk:** International or non-standard Indian numbers silently fail; booking proceeds with empty phone
- **Severity:** HIGH — The booking API call is made with empty phone if phone field later passes `booking_gate_missing_fields` (it won't — but user is looped on phone question with no explanation)

### GAP-010 — Large group dead-end after capacity limitation
- **Scenario:** Group of 20 → `capacity_supported=False` → `_STATE_CLOSED`
- **Risk:** After CLOSED, all follow-up messages hit `_handle_post_booking` which returns concierge_follow_up() and closes again — user cannot restart flow without restarting session
- **Severity:** HIGH

### GAP-011 — `"1 January"` silently bumped to next year (2027) without user notification
- **Input:** `_normalise_date("1 January")`
- **Actual:** `"2027-01-01"` (today is June 2026, so Jan 2026 < today → advanced to 2027)
- **Risk:** User books a different year without knowing
- **Severity:** HIGH

### GAP-012 — Cancellation of non-existent booking gives misleading response
- **Scenario A015:** No booking_ref; user says "I want to cancel my booking"
- **Actual response:** `"No problem. I've cancelled the booking process for you."`
- **Risk:** False confirmation — agent claims to cancel a booking that was never made
- **Severity:** CRITICAL

### GAP-013 — Safety escalation silently dropped on exception
- **Path:** `_enrich_conversation_result()` → `HandoffSummaryAgent.generate()` throws → exception caught → `escalate=False`
- **Risk:** A safety escalation (fire/injury) is silently swallowed if summary generation fails
- **Severity:** CRITICAL

---

## Escalation Pattern Test Results (18 inputs)

| Scenario | Input | Expected | Actual | Status |
|----------|-------|----------|--------|--------|
| E013 | "get me a human please" | True | False | ❌ FAIL |
| E014 | "talk to your manager" | True | False | ❌ FAIL |
| E015 | "connect me with someone" | True | False | ❌ FAIL |
| E016 | "I need human help right now" | True | True | ✅ PASS |
| E_std | "I want to speak to a human" | True | True | ✅ PASS |
| E_std2 | "connect me to an agent" | True | False | ❌ FAIL |
| E009 | "someone is hurt inside" | True | True | ✅ PASS |
| E010 | "my friend fainted" | True | False | ❌ FAIL |
| E011 | "there is smoke in the room" | True | True | ✅ PASS |
| E_safe2 | "there is a fire" | True | True | ✅ PASS |
| E_safe3 | "someone cannot breathe" | True | True | ✅ PASS |
| E001 | "I need a refund" | True | True | ✅ PASS |
| E003 | "I deserve a refund" | True | False | ❌ FAIL |
| E_ref2 | "give me my money back" | True | False | ❌ FAIL |
| E_ref3 | "I want compensation" | False | False | ✅ PASS |
| J013 | "that's not what I asked" | True | True | ✅ PASS |
| J014 | "you keep asking the same thing" | True | True | ✅ PASS |
| J018 | "I already told you" | True | True | ✅ PASS |

**Score: 11/18 PASS, 7/18 FAIL (39% escalation pattern failure rate)**

---

## Name Plausibility Test Results

| Name | `_is_plausible_name()` | Expected | Status |
|------|------------------------|----------|--------|
| Book | True | False | ❌ **BUG** |
| Game | True | False | ❌ **BUG** |
| Escape | False | False | ✅ |
| Room | False | False | ✅ |
| Adults | False | False | ✅ |
| Ready | False | False | ✅ |
| Booking | False | False | ✅ |

---

## Date/Location Extraction Results

| Input | Extracted | Correct? | Notes |
|-------|-----------|----------|-------|
| "day after tomorrow" | "Tomorrow" | ❌ NO | Substring match bug |
| "next month" | "" | ✅ YES | No handler, safely empty |
| "06/25" | "06/25" | ⚠️ AMBIGUOUS | normalise_date → "" (safe) |
| "25/13" | "25/13" | ⚠️ AMBIGUOUS | normalise_date → "" (safe) |
| "1 January" | "2027-01-01" | ⚠️ SILENT | Year bumped without notice |
| "whitefield or koramangala" | "Koramangala" | ⚠️ SILENT | First tuple match wins |
| "jpnagar" | "JP Nagar" | ✅ YES | Fuzzy match works |
| "jeep nagar" | "JP Nagar" | ✅ YES | Fuzzy match works |
| "5876543210" | "" | ✅ SAFE | Rejected per India mobile rules |

---

## Full Test Suite Status (Post Red-Team)

```
352 existing tests    → 352 PASS (100%) — unchanged
38 red-team tests     → 37 PASS, 1 FAIL (97%)
18 escalation probes  → 11 PASS, 7 FAIL (61%)
──────────────────────────────────────────────
TOTAL: 408 tests, 400 PASS, 8 FAIL
```

---

*Report generated by Principal QA Engineer — Phase 2 Execution, 2026-06-22*
