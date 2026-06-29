# Direct Answer Guard Report

Date: 2026-06-24

## Summary

Implemented a deterministic P0 Direct Answer Guard in the current Vapi + FastAPI production path.

The fix does not rely on OpenAI prompt composition. It runs inside `InboundAgent` after memory extraction and before reasoner/fallback response generation, so clear customer questions are answered before qualification or booking prompts resume.

## Files Changed

- `src/agents/inbound_agent.py`
- `tests/test_inbound_agent.py`
- `tests/test_direct_answer_guard.py`

## Root Cause

Conversation-quality failures were happening before the prompt layer:

- Broad FAQ/recommendation classification was not enough to preserve the exact customer question.
- Room names inside comparison questions could trigger room-description output.
- Budget/price questions could be converted into booking handoff before answering.
- Confusion and frustration repair phrases could fall through to qualification or generic fallback.
- Location questions could list branches instead of giving practical guidance.

## Fix

Added `_direct_answer_guard()` in `src/agents/inbound_agent.py`.

It handles:

- confusion / escape-room education
- frustration repair
- horror/scary preference
- thrill preference
- budget concerns
- discount questions
- price objections
- slot comparisons
- room comparisons
- location comparisons

It then optionally resumes the pending qualification question using the existing qualification state.

Also changed the old budget route override so direct budget/price answers are not replaced by:

`Okay — I'll start the booking process and check availability.`

## Before vs After

| Scenario | Before | After |
| --- | --- | --- |
| S08 scary/horror | `Got it. To suggest the perfect game, how many people are joining?` | `We don't position the rooms as horror or jump-scare experiences. Closest fits are Bomb Defusal for pressure, Classified for challenge, or Undercover for story-led suspense.` |
| S10 budget concern | `Which location would you prefer?` | `Totally fair to watch the budget. The simplest path is escape-room only; the fuller birthday path can add food or activity support...` |
| S11 discount | `I don't have that detail... May I take your name and number...` | `I don't have confirmed discount information from the booking system right now, so I don't want to promise an offer...` |
| S17 slot comparison | Murder Mystery room monologue | `Choose 5:30. If you might be late, the later slot gives you more buffer for arrival and briefing.` |
| S18 room comparison | Murder Mystery room monologue | `I lean toward Murder Mystery for a first visit... Hostage is the more urgent option.` |
| S19 location comparison | `Breakout has locations in Koramangala, Whitefield, and JP Nagar.` | `I'd check Whitefield first; it is usually the first branch I would check from that side of Bangalore.` |
| S24 confusion | `No worries. Got it. How many people are joining?` | `An escape room is a themed team game. You enter a mission, search for clues, solve puzzles together...` |
| S25 frustration | Contact fallback | `You're right, I missed what you were asking. Tell me the specific part you want fixed, and I'll stay with that.` |

## Scenarios Fixed

- S07 first-turn thrill preference now reflects thrill/suspense before qualification.
- S08 scary/horror preference now answers directly.
- S10 budget-sensitive customer now gets budget framing before location/date collection.
- S11 price/discount objection no longer asks for name/phone first.
- S17 slot comparison now answers timing tradeoff.
- S18 room comparison now compares instead of describing one room.
- S19 location comparison now recommends Whitefield from Marathahalli.
- S24 confusion now explains escape rooms before qualification.
- S25 frustration now repairs before fallback/contact collection.

## Regression Caught And Fixed

Initial slot detection incorrectly treated:

`We have a 7 PM booking but we are running 30 minutes late. Can you help?`

as a slot comparison between `7 PM` and `30`.

Fix:

- slot comparison now requires two actual slot-shaped times such as `3:15` / `5:30` or `3 PM` / `5 PM`
- added regression test `test_late_arrival_duration_is_not_slot_comparison`

## Recommendation Quality Changes

Improved:

- horror/scary requests no longer invent horror rooms and instead offer suspense/pressure alternatives
- thrill requests now steer toward pressure/challenge/story suspense options
- room comparison now gives a practical recommendation instead of a long room description
- location comparison now gives practical branch guidance when the customer gives an origin area

Not changed in this P0 pass:

- broad recommendation ranking after later turns
- JP Nagar-specific recommendation quality after a thrill preference
- birthday/package option richness
- parking answer specificity by selected branch
- food/package answer quality
- room/date change wording during active booking

## 25 Golden Scenario Run

Command:

```bash
BOOKING_PROVIDER=mock OPENAI_API_KEY= .venv/bin/python <golden-scenario harness>
```

Result:

- 25 / 25 scenarios executed through `main.dispatch`
- Direct-answer scenarios improved as listed above
- No booking, refund, human escalation, or FAQ interruption regression observed in the golden run
- Remaining weak spots are outside the P0 direct-answer guard scope

## Tests Added

`tests/test_direct_answer_guard.py`

Coverage:

- confusion question
- frustration repair
- horror/scary preference
- thrill preference with location context
- birthday budget concern
- discount question
- price objection
- slot comparison
- late-arrival duration false positive
- room comparison
- location comparison
- direct answer resumes pending qualification

Updated:

- `tests/test_inbound_agent.py::test_budget_question_triggers_sales_prompt`

The expected behavior is now direct budget answering in `inbound_agent`, not forced booking handoff.

## Test Results

Targeted:

```bash
.venv/bin/pytest -q tests/test_direct_answer_guard.py
```

Result:

```text
12 passed
```

Full suite:

```bash
.venv/bin/pytest -q
```

Result:

```text
856 passed in 5.31s
```

## Regressions

Automated regression result:

- No test regressions.
- One detector overreach was found during validation and fixed before final full-suite run.

Known remaining conversation-quality gaps:

- S04 `What options do you have?` during birthday flow still gives a weak uncertainty answer.
- S07 later turn can still recommend rooms outside the already supplied JP Nagar context.
- S14 parking still answers all branches instead of only Whitefield.
- S15 food/package answer still mentions corporate food in a birthday context.
- S20/S21 room/date change spoken responses remain booking-state wording issues, not direct-answer guard issues.

## Production Path Status

The current deterministic production path now answers the P0 direct-question classes before qualification/booking continuation.

No prompt files were changed.

No OpenAI prompt composition is required for these fixes.
