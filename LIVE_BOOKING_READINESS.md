# Live Booking Readiness

## Decision

**Can a real customer successfully complete a booking? NO, not yet proven or releasable.**

The deterministic path works with a mocked Kreeda provider. The live path did not complete validation.

## Code-Path Audit

| Stage | Implementation | Validation result |
|---|---|---|
| Availability | `BookingOrchestrator._check_availability_operational()` | Normalizes dates, maps venue/game, caches bookable slots, and returns `verified`; live validation attempt hung during provider setup |
| Slot selection | `BookingAgent._handle_slot_selection()` | Requires a cached matching slot and persists `selected_slot`; mocked acceptance passes |
| Cart creation | `BookingOrchestrator.prepare_booking()` | Requires customer fields, verified slot lookup, people category, and non-empty `cartId`; mocked test passes |
| Booking creation | Same function | Calls `create_booking` with venue, cart, slots, first/last name, phone, and idempotency key; mocked test passes |
| Response validation | Same function | Requires top-level `bookingId` and status `CONFIRMED`/`BOOKED`; does not require an independent reference or verify response correlation |
| ID persistence | `BookingAgent._prepare_selected_booking()` | Requires `booking_id` and `confirmed`, saves `booking_id`; passes |
| Reference persistence | Same function | Saves provider reference or falls back to booking ID; does not prove a separate reference was returned |
| Confirmation | Same function plus `ResponseComposer` | Spoken confirmation requires saved ID/reference; unverified composer claims are blocked |

## Required Identifier Answers

- **Is `booking_id` required?** Yes. Missing ID invokes failure recovery and cannot confirm.
- **Is an independent `booking_ref` required?** No. Current code synthesizes it from `booking_id` when `orderId` is absent.
- **Is `create_booking` response validated?** Partially. ID and status are validated; independent reference and request/response correlation are not.
- **Does failure recovery work?** Yes in mocked tests. Room, location, date, selected slot, cart ID, and signature survive a missing-last-name error.
- **Does retry work?** Yes in mocked tests. Cart is reused and `create_booking` is retried.
- **Does memory persist correctly?** IDs and customer booking fields persist. Internal slot/availability/state-machine data do not hydrate after runtime reconstruction.

## Live Validation Evidence

1. Credentials are present and `should_use_live_booking()` returns true.
2. A non-destructive live availability attempt inside the restricted environment failed DNS quickly.
3. The approved unrestricted attempt hung for more than 90 seconds and was terminated during eager agent-contract discovery/provider construction.
4. Repository logs contain no `KREEDA_CREATE_CART_SUCCESS`, `KREEDA_CREATE_BOOKING_SUCCESS`, `BOOKING_ID`, or `BOOKING_REF` from a real call.
5. Persisted API sessions contain no real booking ID/reference.
6. Test A passes only with a mocked provider returning `booking-routing` and `reference-routing`.

## Exact Blocking Issues

1. `AgentContractProvider._dns_check()` can exceed its timeout because executor shutdown waits for the DNS worker.
2. No safe live Test A could be completed: the requested scenario supplies no real customer name/phone, and creating a real reservation is a side effect that must use authorized customer data.
3. No provider-issued booking/reference pair has been observed.
4. The API cannot reliably resume the final booking state after process reconstruction without rebuilding live slot context.

Until these are resolved and a controlled live booking record is verified, the answer remains **NO**.
