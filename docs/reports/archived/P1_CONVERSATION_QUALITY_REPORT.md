# P1 Conversation Quality Report

Date: 2026-06-24

## Scope

Implemented only the accepted P1 deterministic conversation-context fixes:

1. Room change acknowledgement
2. Date change acknowledgement
3. Location-specific parking answers
4. Context-aware food/package answers
5. Recommendation consistency after location selection

No prompts were modified.

No new agents were added.

No sentiment, escalation, handoff, conversation intelligence, memory module, or provider integration code was modified.

## Files Changed

- `src/agents/booking_agent.py`
- `src/agents/inbound_agent.py`
- `tests/test_p1_conversation_quality.py`
- `tests/test_hardening_pass.py`
- `tests/test_production_blocker_fixes.py`

## Root Causes

### Room And Date Change Acknowledgement

`main.dispatch()` pre-merges room/date entities into memory before `BookingAgent` handles the turn. That meant `BookingAgent` could see the new value as already current and miss the visible spoken acknowledgement.

Fix:

- added explicit change-turn detection in `BookingAgent` based on the customer message
- room/date changes now clear `selected_slot`
- the response acknowledges the change before the next booking step

### Parking Answers

Parking FAQ paths returned all branch parking information even when the customer named a branch.

Fix:

- added location-specific parking answers in both inbound and active-booking FAQ paths

### Food/Package Answers

Food answers used generic or corporate-oriented wording even for birthday questions.

Fix:

- added context-aware food answers for birthday, corporate, and general room/event contexts
- preserved the legacy `Food options include...` wording so existing FAQ tests remain meaningful

### Recommendation Consistency

After a customer mentioned thrill/adventure and then selected a location, later recommendation turns could drift back to cross-location options.

Fix:

- `InboundAgent` now uses recent customer-turn context for location and challenge preference when recommendation memory is thin
- recommendations after JP Nagar selection no longer drift to `Classified` or `Bomb Defusal`

## Before vs After Examples

### Room Change

Before:

`Sorry, I didn't catch that. The available slots are 10:00 AM, 12:00 PM, and 3:00 PM. Which time would you prefer?`

After:

`Got it. I've switched the room to Hostage. Would you like to keep Tomorrow?`

If a time is already selected, the acknowledgement asks whether to keep the same date and time.

### Date Change

Before:

`Sorry, I didn't catch that. The available slots are 10:00 AM, 12:00 PM, and 3:00 PM. Which time would you prefer?`

After:

`No problem. I'll check 26 June instead. Would you like the same room?`

### Whitefield Parking

Before:

`Koramangala has basement and street parking, Whitefield has basement car parking with dedicated spots and general parking, and JP Nagar has street parking.`

After:

`Whitefield has basement parking available.`

### Birthday Food

Before:

`Food options include continental food, build-your-menu options, mix snack boxes, hi-tea options, and Indian buffet options for corporate events.`

After:

`Food options for birthday parties include choices that can be coordinated with the package; the team can confirm the available menu for your guest count.`

### Recommendation After Location

Sequence:

1. `We are first timers but want something thrilling and adventurous.`
2. `JP Nagar`
3. `Four adults`

After:

`For a group of 4 adults visiting JP Nagar, since you're first-time players, I'd probably start with Murder Mystery. It's easier for first-timers; Hostage is the more urgent option. Want me to compare them?`

It no longer recommends `Classified` or `Bomb Defusal` after JP Nagar has been selected.

## New Tests

Added `tests/test_p1_conversation_quality.py`:

- `test_room_change_is_acknowledged_and_clears_selected_slot`
- `test_date_change_is_acknowledged_and_clears_selected_slot`
- `test_location_specific_parking_answer_for_inbound`
- `test_location_specific_parking_answer_for_active_booking`
- `test_food_answer_uses_birthday_context`
- `test_recommendation_stays_consistent_after_location_selection`

Updated older tests that expected immediate slot lookup after room/date changes:

- `tests/test_hardening_pass.py::test_scenario_g_room_modification`
- `tests/test_production_blocker_fixes.py::test_acceptance_a_book_room_then_change_date_clears_slot`

The new expected behavior is acknowledgement first, with stale slot state cleared.

## Validation

Focused P1 run:

```bash
.venv/bin/pytest -q tests/test_p1_conversation_quality.py
```

Result:

```text
6 passed
```

Full suite:

```bash
.venv/bin/pytest -q
```

Result:

```text
862 passed in 5.52s
```

## Recommendation Quality Impact

Improved:

- Location context is respected more consistently after a branch is selected.
- Thrill/adventure context is retained for later recommendation turns.
- The system avoids recommending clearly cross-location options after a location-specific flow.

Not changed:

- The underlying room ranking model.
- Recommendation content/prompt wording.
- Provider inventory integration.

## Remaining Gaps

- JP Nagar recommendation content still leans on the existing deterministic room set and does not introduce new room-ranking logic.
- Parking answers are now branch-specific when branch context exists, but compound multi-topic questions still use broader summary wording.
- Food/package answers are safer and more contextual, but still do not quote package menus or prices.
- Room/date changes now acknowledge first; the subsequent confirmation/availability step may still need a later UX pass for richer “yes, keep it” handling.

## Production Path Status

P1 conversation context fixes are implemented in deterministic backend code.

No OpenAI prompt composition is required.
