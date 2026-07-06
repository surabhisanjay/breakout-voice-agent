# Fix Report

Scope guard: this pass touched routing, memory, qualification, loop detection, and booking-state preservation only. No production prompt/personality/transcript-example files were modified by this pass.

## Files Changed

- `src/memory/conversation_memory.py`
- `src/agents/qualification_agent.py`
- `src/agents/booking_agent.py`
- `src/agents/escalation_agent.py`
- `src/agents/inbound_agent.py`
- `tests/test_conversation_state_hardening.py`
- `tests/test_mvp_stabilization.py`

## Fixes Implemented

1. Persist valid fields immediately even when another required field is missing.
   - `update_from_message()` now returns extracted fields.
   - Qualification treats extracted side-channel fields as valid progress and asks only the next missing field.
   - Booking stores date/time before availability checks.

2. Added `preferred_time` memory.
   - Stores unverified time requests like `7 PM` before availability can be fetched.
   - Reuses the preferred time after location/date become available.

3. Suppressed false loop escalation for active booking updates.
   - Date, room, slot, participant, phone, name, and location corrections do not count as repeated-loop escalation triggers.

4. Expanded FAQ interruption handling during qualification/booking.
   - Parking, cancellation, food, arrival, directions, dress code, age restrictions, and duration now answer and resume the active missing-field question.
   - Cancellation policy/refund policy wording stays FAQ-like during qualification instead of overwriting the active booking intent.

5. Added spoken phone parsing.
   - Supports `double`, `triple`, `oh`, and `zero`.
   - Converts spoken phone utterances into digit strings before validation.

6. Added name correction support.
   - Latest explicit correction overwrites previous `customer_name`, `first_name`, and `last_name`.

7. Kept audit trail focused on customer/business state.
   - Recommendation metadata no longer appends audit entries after participant corrections, preserving the last meaningful state change.

8. Updated stale acceptance fixtures.
   - `tests/test_mvp_stabilization.py` used `22 June`, which is in the past as of `2026-06-23`; fixtures were moved to `25 June` without weakening production date validation.

## Before / After

Before:
- `Tomorrow` while waiting for location was treated as an invalid location answer.
- `7 PM` before location had nowhere to persist.
- Repeated update turns could be escalated as loops.
- Second FAQ interruptions could be answered without resuming qualification.
- Spoken phone numbers were rejected.
- Name corrections did not overwrite reliably.

After:
- Valid fields persist immediately and are acknowledged.
- `preferred_time` stores requested times before slot validation.
- Corrections are treated as active state changes, not loops.
- FAQ answers resume the active missing-field question.
- Spoken phone numbers parse to digits.
- Name corrections overwrite immediately.

