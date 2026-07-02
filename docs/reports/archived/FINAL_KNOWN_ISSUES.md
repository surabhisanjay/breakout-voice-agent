# Final Known Issues

Only unresolved items are listed. All previously reported in-repository behavioral defects were either fixed or could not be reproduced against the final code.

## 1. Authorized Real Kreeda Creation Smoke Test

The live-mode code path, payload validation, cart creation, booking creation, retry, ID/reference persistence, and confirmation gates are covered with controlled provider-contract tests. No real customer reservation was created during this pass because no authorized customer identity and disposable slot were supplied.

**Impact:** External production proof remains outstanding; demo operation is covered by deterministic/provider-contract simulation.  
**Owner/action:** Run one authorized live `availability -> cart -> booking -> lookup` smoke test and cancel the disposable booking through normal operations.

## 2. Downstream Human Transfer Consumer

`/chat` now returns `next_agent`, `escalation`, and `handoff_summary`, and every human/refund/safety path produces a complete payload. The repository does not contain the external Vapi/telephony queue or CRM consumer that performs the physical transfer.

**Impact:** The demo can show and assert handoff selection/payload; real transfer completion depends on external configuration.  
**Owner/action:** Bind `next_agent=escalation_agent` to the production Vapi transfer destination and verify receipt.

## 3. Live Inventory Freshness

Availability and booking use live Kreeda data. Pre-booking recommendation filtering still has a static verified inventory map as its deterministic fallback.

**Impact:** A newly added/removed room can make a fallback recommendation stale, though booking cannot confirm it without live inventory/availability.  
**Owner/action:** Refresh the fallback inventory during releases or hydrate recommendations from the live inventory cache.

## Release Relevance

None of these items blocks a controlled MVP demo. Items 1 and 2 must be completed before claiming fully autonomous production operation.
