# End-To-End Validation

## Test Results

### Test A: Full Booking

**Input:** 4 adults, JP Nagar, Murder Mystery, tomorrow, 7 PM, first/last name, phone.  
**Mock-provider result:** PASS. `BOOKING_AGENT_SELECTED`, normalized tomorrow, selected slot, first name, last name, phone, cart call, booking call, `booking_id`, `booking_ref`, and confirmation are asserted by `test_booking_routing_acceptance_a_through_f()`.  
**Live result:** NOT EXECUTED TO COMPLETION. Provider initialization hung for over 90 seconds and was terminated. No authorized real name/phone was supplied for a side-effecting reservation.  
**Release result:** FAIL for production evidence.

### Test B: Human Escalation

**Input:** `Please connect me to a human agent.`  
**Observed:** `escalate=true`; summary generated; active agent remains `inbound_agent`; no transfer transport; spoken response continues intake.  
**Result:** FAIL.

### Test C: Angry Customer

**Input:** `This is ridiculous and unacceptable.`  
**Observed:** sentiment `angry`; escalation evaluation true; summary generated. Message was incorrectly persisted as customer name. No handoff transport occurred.  
**Result:** PARTIAL/FAIL for end-to-end escalation.

### Test D: Date Change

**Input:** Change date while a slot is selected.  
**Observed:** New date persists and availability reruns. Previous `selected_slot` remains in memory/internal state.  
**Result:** FAIL state-consistency requirement.

### Test E: Room Change

**Input:** Change Murder Mystery to Hostage while a slot is selected.  
**Observed:** `room=Hostage`; prior room is overwritten. Previous slot remains stored and final response can omit the change acknowledgment.  
**Result:** PARTIAL/FAIL for complete booking-state correctness.

### Test F: FAQ During Booking

**Input:** `Where do we park?` during slot selection.  
**Observed:** BookingAgent remains selected and slot state survives, but the FAQ is not answered.  
**Result:** FAIL.

## Test Commands And Evidence

- Targeted acceptance suite: **8 passed**. These tests prove mocked booking, retry, cart reuse, summary generation, memory overwrite, and state preservation currently asserted by the suite.
- Full suite before report generation: **335 passed**.
- Direct dispatch diagnostics prove the untested escalation and conversation failures above.
- Direct state-machine diagnostics prove stale slot retention after room/date changes.

## Acceptance Summary

| Test | Result |
|---|---|
| A: Real booking | FAIL (mock passes; live unproven/blocked) |
| B: Human escalation | FAIL |
| C: Angry customer | FAIL end to end |
| D: Date change | FAIL due stale slot |
| E: Room change | FAIL due stale slot/composer behavior |
| F: FAQ interruption | FAIL because question is ignored |

The existing passing suite does not cover these release blockers.
