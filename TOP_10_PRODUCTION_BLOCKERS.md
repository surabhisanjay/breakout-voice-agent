# Top 10 Production Blockers

Date: 2026-06-24

Scope: Vapi + FastAPI deterministic production path. No prompt rewrite, no new agents, no provider integration rewrite.

## P0

1. Availability questions entered booking but asked for room before location.
   - Root cause: `BookingAgent._handle_availability_check()` checked `room` before `location`.
   - Fix: missing-field order now asks for location first, then room, then date.
   - Status: fixed and covered by production-path dispatch regression.

2. Direct recommendation intent could fall into qualification.
   - Root cause: recommendation questions without a recommender option fell through to normal missing-field progression.
   - Fix: recommendation question fallback now answers the recommendation workflow first, or asks for location if the branch is needed.
   - Status: fixed.

3. Branch-specific room hallucination before location.
   - Root cause: room/inventory and room-name responses exposed branch lists or branch-exclusive rooms without a selected location.
   - Fix: unknown-location inventory/room-name responses ask for location and do not list branches; branch-exclusive recommendations require location.
   - Status: fixed.

4. Single-name booking could still fail through a legacy provider path.
   - Root cause: `BookingOrchestrator.create_booking()` still sent blank `customerLastName` while the operational path used `NA`.
   - Fix: legacy path now uses `NA` for missing last name.
   - Status: fixed.

## P1

5. Couple/date-night recommendation missing.
   - Root cause: `couple_event` intent had no recommender option, so explicit recommendation requests fell back to location collection.
   - Fix: deterministic couple-event recommendation gives Murder Mystery first, Hostage as urgent alternative, then asks location.
   - Status: fixed.

6. Popular/best-selling answer overclaimed availability across all branches.
   - Root cause: generic popular response said options were available at all locations.
   - Fix: generic popular answer now avoids branch availability claims and asks for location.
   - Status: fixed.

7. Room description leaked branch inventory before branch selection.
   - Root cause: `_answer_room_name()` returned location lists when no location was known.
   - Fix: room description now explains only the named room and asks for location.
   - Status: fixed.

8. Location-comparison questions were shadowed by generic recommendation handling.
   - Root cause: words like "choose" matched the recommendation guard before the location-comparison handler.
   - Fix: location comparison remains in the direct-answer path and is verified.
   - Status: fixed.

## P2

9. Over-eager qualification after direct questions.
   - Root cause: direct-question handling existed, but gaps remained for recommendation and availability variants.
   - Fix: added production-path regressions for availability, recommendation, best-room, and location comparison.
   - Status: fixed for known examples.

10. Test coverage was helper-heavy for live transcript failures.
    - Root cause: several tests called helper methods directly and missed `dispatch()` behavior.
    - Fix: added dispatch-level regressions for the production path.
    - Status: fixed.

