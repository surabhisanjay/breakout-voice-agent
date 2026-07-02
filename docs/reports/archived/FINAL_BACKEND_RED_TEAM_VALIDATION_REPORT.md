# Final Backend Red-Team Validation Report

Date: 2026-06-26

## Scope

Validated the production backend conversation path only:

Customer message -> memory extraction -> Conversation Guard -> deterministic routing -> recommendation / knowledge / booking / escalation / intelligence enrichment.

OpenAI usage, Vapi prompt behavior, frontend behavior, personality prompts, and transcript prompt examples were intentionally excluded.

## Scenario Coverage

- Deterministic direct-question scenarios: 100
- Randomized multi-turn conversations: 200
- Voice-style / ASR conversations: 200
- FAQ and recommendation interruptions during booking: 100
- Recommendation / rejection scenarios: 100
- Booking state-change scenarios: 100
- Escalation, handoff, and conversation-intelligence contract scenario: 1

Total new production-path scenarios executed: 801

All scenarios were executed through `dispatch()`.

## Failures Found

1. Plural discount wording bypassed the Conversation Guard.
   - Example: "Do you have discounts?"
   - Actual failure: routed onward into availability/booking instead of answering pricing/discount directly.
   - Root cause: guard matched `discount` but not plural `discounts`.

2. Alternate recommendation wording missed the guard.
   - Example: "Any other option?"
   - Actual failure: not consistently handled by the deterministic guard because only `another option` was matched.
   - Root cause: incomplete alternate-option phrase coverage.

3. Bestseller wording missed the guard.
   - Example: "What do most people play?"
   - Actual failure: handled downstream instead of the high-confidence recommendation guard.
   - Root cause: missing `most people play` / `most people` trigger phrase.

4. Explicit slot change did not persist during active booking.
   - Example: "Use 7 PM."
   - Actual failure: existing `selected_slot` remained unchanged.
   - Root cause: slot-only changes were not treated as explicit booking state changes unless the booking agent was already in a slot-selection substate with cached availability.

## Deterministic Fixes Applied

- Expanded pricing/discount guard recognition to plural discount, coupon, offer, deal, cost, and rate wording.
- Expanded recommendation guard recognition for `most people play`, `most people`, `popular room`, `any other option`, and `other option`.
- Added explicit slot-change handling in `BookingAgent` for utterances such as "use 7 PM", preserving booking ownership and immediately updating `selected_slot`.
- Added a red-team regression suite in `tests/test_final_backend_red_team_validation.py`.

## Test Results

- Red-team suite: 7 passed, covering 801 generated production-path scenarios.
- Neighboring routing/guard regression subset: 429 passed.
- Full backend suite: 1314 passed.

## Remaining Production Risks

- Live Kreeda API availability and booking confirmation still depend on external service health and credentials.
- Deterministic ASR normalization covers common observed variants, but new venue/room misrecognitions should be added as they appear in live calls.
- Large-group and package bookings still require human/event coordination when capacity exceeds single-room limits.
- Policy answers remain high-level unless backed by a live policy source or booking reference.

## Readiness Score

Production readiness score: 92 / 100.

No deterministic routing, memory, recommendation, booking-state, escalation, or conversation-intelligence blocker remains from this pass.
