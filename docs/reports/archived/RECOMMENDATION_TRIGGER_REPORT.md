# Recommendation Trigger Control Report

## Scope

Implemented deterministic conversation-quality fixes only. No prompts, personality files, sentiment, escalation, handoff, memory extraction, provider integrations, or recommendation content were changed.

## Root Causes

1. `RecommendationEngine.recommend()` could return a room option from context alone, such as `beginner + adults`.
2. `InboundAgent` persisted and surfaced `recommended_option` even when the customer had not asked for advice.
3. Several fallback branches auto-rendered recommendation responses after age/location capture.
4. Location prompts listed all branches by default.
5. The intent detector treated bare `date` as a `couple_event` signal, allowing booking intent drift.
6. Booking acceptance phrases such as `Book that` could reset context when no pending offer existed.

## Fixes

- Added an explicit recommendation-request guard in `InboundAgent`.
- Recommendation is now persisted only when the user asks for recommendation, best room, comparison, popular/best-selling room, or what to play.
- Removed automatic beginner/adult recommendation branches.
- Replaced unprompted location listing with `Which location would you like to visit?`.
- Kept location listing only for explicit location-list questions.
- Preserved direct answers for best-selling questions, e.g. Whitefield popular room.
- Removed bare `date` from `couple_event` intent triggers.
- Prevented booking-context reset on booking acceptance follow-ups.

## Before vs After

### No Recommendation Without Request

Before:
Customer: We are all adults.  
Agent: I'd recommend Murder Mystery...

After:
Customer: We are all adults.  
Agent: Which location would you like to visit?

### Location Before Recommendation

After:
Customer: Book for five people.  
Agent: Have you done an escape room before, or is this your first one?

Customer: First time.  
Agent: What age group are the players: adults, kids, or a mix?

Customer: Adults.  
Agent: Which location would you like to visit?

Customer: Whitefield.  
Agent: Do you already have a room in mind, or would you like a recommendation?

Customer: Recommend one.  
Agent: I lean toward Murder Mystery for a first visit... Hostage is the more urgent alternative.

### Explicit Location List

Customer: What locations do you have?  
Agent: Breakout has locations in Koramangala, Whitefield, and JP Nagar.

### Best-Selling Room

Customer: Which is the best selling room at Whitefield?  
Agent: Murder Mystery is usually the most popular choice at Whitefield.

### Date Acceptance

Customer: Tomorrow  
Result: `preferred_date=Tomorrow`; no "didn't catch the date" response.

## Tests Added

Added `tests/test_recommendation_trigger_control.py` covering:

- `test_no_recommendation_without_request`
- `test_locations_not_listed_unprompted`
- `test_location_list_only_after_explicit_request`
- `test_recommendation_only_after_request`
- `test_no_duplicate_recommendation_after_location`
- `test_best_selling_room_question`
- `test_tomorrow_is_accepted`
- `test_booking_intent_stable_after_date`
- `test_booking_intent_stable_after_location`
- `test_booking_intent_stable_after_recommendation`

Updated older tests that encoded the previous auto-recommendation behavior.

## Validation

Full suite:

```text
872 passed in 5.07s
```

## Remaining Risk

Some older scenario names still mention recommendation-before-location, but their assertions now enforce the accepted trigger-control behavior. No production prompt files were changed.
