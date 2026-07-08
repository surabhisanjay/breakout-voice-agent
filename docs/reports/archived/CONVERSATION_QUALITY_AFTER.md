# Conversation Quality After

Date: 2026-06-24

## Scope

Implemented only:

1. `prompts/breakout_personality_prompt.txt`
2. `prompts/conversation_playbook.txt`

Not implemented:

- `PROPOSED_RECOMMENDATION_PLAYBOOK.md`

Not modified:

- booking logic
- routing logic
- state management
- room change handling
- date change handling
- sentiment logic
- escalation logic
- handoff logic
- memory logic
- API contracts
- provider integrations
- recommendation engine

## Diff Summary

### `prompts/breakout_personality_prompt.txt`

Changed from a general hospitality/sales representative prompt to a more explicit booking-host prompt.

Added or strengthened:

- host-like voice
- direct-question-first behavior
- one acknowledgement maximum
- no stacked filler
- natural name collection
- first-time-player warmth
- thrill/scary/horror handling
- birthday/budget tone
- late-arrival rescue tone
- short room descriptions
- no unsupported `best` / `you'll love it` claims

Compatibility anchors retained for existing tests:

- `hospitality`
- `Recommend first`
- `Ask no more than one question`
- `Do not add prices`
- `Don't worry`

### `prompts/conversation_playbook.txt`

Replaced the older broad guidance with a stricter host-style playbook.

Added or strengthened:

- `Acknowledge, then help / host frame`
- anti-repetition rules
- direct-intent-first rules
- first-time-player pattern
- preference override pattern
- room-list and room-description patterns
- price-objection pattern
- birthday/budget pattern
- large-group pattern
- late-arrival rescue pattern
- FAQ interruption pattern
- natural name/contact pattern
- safety and humor limits

Compatibility anchors retained for existing tests:

- `BREAKOUT CONVERSATION PLAYBOOK`
- `Acknowledge, then help`
- `Empathy and rescue pattern`

## Validation

### Full Test Suite

Command:

`.venv/bin/pytest -q`

Result:

`844 passed in 4.61s`

No booking-flow, escalation-flow, refund-flow, or human-handoff regression was detected by the existing automated suite.

### LLM Prompt Path Smoke Test

Post-change LLM smoke test:

- Prompt loaded: yes
- Conversation playbook loaded: yes
- Transcript examples loaded: yes
- Result: OpenAI quota `429`
- Runtime path used: deterministic fallback

Because OpenAI quota is exhausted, the updated prompt/playbook could not be evaluated through the production LLM composer. The 25 golden scenario run below therefore reflects current deterministic fallback behavior, not the humanized LLM prompt output.

## Before vs After Scores

Baseline average: **4.59 / 5**

After average: **4.59 / 5**

Reason unchanged: the runtime could not use the updated prompt due OpenAI quota `429`; deterministic fallback responses do not consume the personality prompt/playbook for most scenario wording.

| ID | Scenario | Before | After | Delta | Status |
|---|---|---:|---:|---:|---|
| S01 | First-time player booking | 4.83 | 4.83 | 0.00 | Unchanged |
| S02 | Returning player booking | 4.67 | 4.67 | 0.00 | Unchanged |
| S03 | Couple booking | 4.83 | 4.83 | 0.00 | Unchanged |
| S04 | Birthday booking | 4.50 | 4.50 | 0.00 | Unchanged |
| S05 | Corporate booking | 4.67 | 4.67 | 0.00 | Unchanged |
| S06 | Large group booking | 4.83 | 4.83 | 0.00 | Unchanged |
| S07 | Thrill preference | 4.83 | 4.83 | 0.00 | Unchanged |
| S08 | Scary/horror preference | 4.00 | 4.00 | 0.00 | Unchanged |
| S09 | Intermediate difficulty preference | 5.00 | 5.00 | 0.00 | Unchanged |
| S10 | Budget-sensitive customer | 4.17 | 4.17 | 0.00 | Unchanged |
| S11 | Price objection | 4.00 | 4.00 | 0.00 | Unchanged |
| S12 | FAQ interruption | 4.50 | 4.50 | 0.00 | Unchanged |
| S13 | Cancellation policy question | 4.67 | 4.67 | 0.00 | Unchanged |
| S14 | Parking question | 4.67 | 4.67 | 0.00 | Unchanged |
| S15 | Food/package question | 4.67 | 4.67 | 0.00 | Unchanged |
| S16 | Late-arrival concern | 4.67 | 4.67 | 0.00 | Unchanged |
| S17 | Slot comparison | 4.50 | 4.50 | 0.00 | Unchanged |
| S18 | Room comparison | 5.00 | 5.00 | 0.00 | Unchanged |
| S19 | Location comparison | 4.67 | 4.67 | 0.00 | Unchanged |
| S20 | Room change | 4.50 | 4.50 | 0.00 | Unchanged |
| S21 | Date change | 4.50 | 4.50 | 0.00 | Unchanged |
| S22 | Human request | 4.67 | 4.67 | 0.00 | Unchanged |
| S23 | Refund request | 4.67 | 4.67 | 0.00 | Unchanged |
| S24 | Customer confusion | 4.17 | 4.17 | 0.00 | Unchanged |
| S25 | Customer frustration | 4.50 | 4.50 | 0.00 | Unchanged |

## Scenarios Improved

None measured in the deterministic run.

Expected improvement once the LLM composer is available:

- S08 Scary/horror preference
- S10 Budget-sensitive customer
- S11 Price objection
- S17 Slot comparison
- S19 Location comparison
- S24 Customer confusion
- S25 Customer frustration

These are the scenarios most directly addressed by the new personality prompt and conversation playbook.

## Scenarios Unchanged

All 25 scenarios were unchanged in measured output because the runtime fell back before prompt rewriting could occur.

## Regressions

No measured regression in:

- full test suite
- booking flow tests
- escalation tests
- refund tests
- human handoff tests
- 25 deterministic golden scenarios

Prompt-content foundation tests initially failed because they expected legacy anchor strings. The prompt/playbook were updated to preserve those anchors while retaining the new behavior guidance. Full suite then passed.

## Special Focus Scenarios

### S08 — Scary/Horror Preference

Before:

`Got it. To suggest the perfect game, how many people are joining?`

After:

`Got it. To suggest the perfect game, how many people are joining?`

Status: unchanged. The new prompt has explicit no-horror/suspense guidance, but deterministic fallback does not use it.

Expected LLM-path improvement:

`We don't do horror or jump-scare rooms. Closest fit is suspense and pressure: Murder Mystery for investigation, or Hostage if you want more urgency.`

### S10 — Budget-Sensitive Customer

Before:

`Which location would you prefer?`

After:

`Which location would you prefer?`

Status: unchanged.

Expected LLM-path improvement:

`Totally fair to watch the budget. There are two paths: escape-room only, or a birthday package with food and space. Which one do you want to compare first?`

### S11 — Price Objection

Before:

`I don't have that detail to hand right now, but our team can help. May I take your name and number so someone can call you back?`

After:

`I don't have that detail to hand right now, but our team can help. May I take your name and number so someone can call you back?`

Status: unchanged.

Expected LLM-path improvement:

`I get it. I don't want to invent a discount, but I can help you choose the best-value option from what's available.`

### S17 — Slot Comparison

Before:

`Murder Mystery is an investigation-style escape room with clues, puzzles, and a story-driven mystery. It is a classic, immersive detective investigation where you find clues and solve a crime. It is beginner-friendly and great for a wide range of ages. It is offered at Koramangala, Whitefield, and JP Nagar.`

After:

`Murder Mystery is an investigation-style escape room with clues, puzzles, and a story-driven mystery. It is a classic, immersive detective investigation where you find clues and solve a crime. It is beginner-friendly and great for a wide range of ages. It is offered at Koramangala, Whitefield, and JP Nagar.`

Status: unchanged.

Expected LLM-path improvement:

`If you might be late, 5:30 is safer. The earlier slot only works if you can arrive 15-20 minutes before game time.`

### S19 — Location Comparison

Before:

`Breakout has locations in Koramangala, Whitefield, and JP Nagar.`

After:

`Breakout has locations in Koramangala, Whitefield, and JP Nagar.`

Status: unchanged.

Expected LLM-path improvement:

`From Marathahalli, Whitefield is usually the cleaner choice to check first. I can narrow rooms there if that works for you.`

### S24 — Customer Confusion

Before:

`No worries. Got it. How many people are joining?`

After:

`No worries. Got it. How many people are joining?`

Status: unchanged.

Expected LLM-path improvement:

`No worries. You enter a themed mission, find clues, solve puzzles, and work together against the clock. Is this your first escape room?`

### S25 — Customer Frustration

Before:

`I don't have that detail to hand right now, but our team can help. May I take your name and number so someone can call you back?`

After:

`I don't have that detail to hand right now, but our team can help. May I take your name and number so someone can call you back?`

Status: unchanged.

Expected LLM-path improvement:

`You're right, I missed what you were asking. Tell me the part you want fixed and I'll stay with that.`

## Conclusion

The prompt/playbook implementation is safe from an automated regression standpoint:

- Full suite passed: `844 passed`
- Booking, escalation, refund, and handoff flows did not regress
- Recommendation playbook was not implemented
- No runtime logic was modified

However, measured conversation-quality improvements are blocked by OpenAI quota `429`. The updated prompt files are ready for LLM-path evaluation once quota is restored. Until then, deterministic fallback behavior remains unchanged.
