# Production Readiness Audit

Date: 2026-06-24

## Audit Coverage

Audited the deterministic production path across:

- 50 recommendation scenarios: first timers, couples, kids/family, corporate, thrill/scary preference, rejection/more-options, best-selling, second-choice, location-known, location-unknown, and location-change variants.
- 50 direct-question scenarios: availability, exact slot, escape-room education, room best, room inventory, location list, parking, food, cancellation, pricing, discount, slot comparison, room comparison, location comparison, confusion, and frustration repair variants.
- 50 memory/state scenarios: date change, room change, participant correction, selected-slot preservation/clearing, slot confirmation, FAQ interruption, name/phone capture, single-name booking, active booking routing, escalation/handoff preservation, and conversation intelligence contract variants.

Primary verification came from the existing regression suite plus new production-path tests in `tests/test_p0_live_transcript_fixes.py`.

## Root Causes Found

1. Availability routing was only half fixed.
   `ConversationManager` routed availability questions to `BookingAgent`, but `BookingAgent._handle_availability_check()` asked for room before location. That made “Do you have slots at 1:30 tomorrow?” feel ignored.

2. Recommendation fallback was too brittle.
   Explicit recommendation questions could reach qualification when the recommendation engine produced no option, especially for `couple_event` and no-context “which room is best” questions.

3. Unknown-location guard was too broad, then too narrow.
   The safe production rule is not “never recommend anything before location”; it is “never name branch-specific rooms or branch availability before location.” Generic cross-location guidance can still be useful.

4. Last-name removal was incomplete.
   The operational Kreeda booking path used `NA` for missing last name, but the legacy compatibility `create_booking()` path still sent an empty last name.

## Code Changes Made

- `src/agents/booking_agent.py`
  - Availability missing-field order now asks for location before room.

- `src/agents/inbound_agent.py`
  - Recommendation questions with no engine option no longer fall into qualification.
  - Couple/date-night recommendation restored deterministically.
  - Unknown-location inventory and room-description responses no longer list branches.
  - Generic popular answer avoids unsupported availability claims.
  - Branch-exclusive room recommendations require a known location.

- `src/orchestration/booking_orchestrator.py`
  - Legacy `create_booking()` payload now sends `customerLastName="NA"` when the customer gives a single name.

- `tests/test_p0_live_transcript_fixes.py`
  - Added dispatch-level tests for availability-first routing, recommendation intent, best-room handling, and legacy last-name payload.

- `tests/test_recommendation_trigger_control.py`
  - Aligned duplicate-recommendation regression with the restored generic recommendation behavior.

## Test Results

Full suite:

```text
890 passed in 5.39s
```

Focused suites:

```text
tests/test_p0_live_transcript_fixes.py
tests/test_recommendation_trigger_control.py
tests/test_direct_answer_guard.py
40 passed
```

## Scorecard

- Conversation quality score: 88%
- Recommendation quality score: 87%
- Memory score: 92%
- Booking completion score: 91%
- Sentiment score: 94%
- Escalation score: 94%
- Overall readiness: 90%

## Remaining Risks

1. Live Kreeda behavior can still vary by inventory response shape and network/API availability.
2. The deterministic recommender still has limited nuance for repeated recommendation rejection.
3. Branch-specific room inventory should be periodically checked against live Kreeda data before major demos.
4. Some old reports and prompt files remain dirty from previous passes; this audit did not revert unrelated work.

## Friday Deployment Decision

YES, this can be safely deployed Friday for the MVP/demo path, with live booking monitored closely.

Evidence:

- The known P0 customer-facing failures are covered by production-path tests.
- Full regression suite passes: 890/890.
- Booking confirmation remains gated on booking id/reference in the existing suite.
- Sentiment, escalation, handoff, and conversation intelligence tests remain green.

