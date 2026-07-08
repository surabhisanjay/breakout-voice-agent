# Replace With Claude-Derived Material

Only the following Claude concepts are clearly superior. They should replace equivalent weak guidance after unsafe examples are removed.

1. **Escalation priority taxonomy.** Adopt P0/P1/P2 distinctions for safety/payment emergencies, immediate human requests, and non-urgent custom work.
2. **Explicit first-request human handoff.** A direct human request should not require another qualification turn.
3. **Structured handoff completeness.** Require known name, phone, intent, location, room, date, participants, sentiment, escalation reason, and an action-oriented summary; represent unknown values explicitly.
4. **Payment-dispute boundary.** A charge without a confirmed booking, duplicate charge, or amount dispute should immediately leave the autonomous flow.
5. **Safety stop rule.** Active physical danger must interrupt sales and booking immediately.
6. **Single acknowledgment during escalation.** Replace repeated apologies and explanations with one acknowledgment and a clear next action.
7. **Modification recap discipline.** After a change, repeat only the changed field rather than the whole booking.
8. **Unknown-answer handling.** State that the answer is not verified and route it; do not fill a knowledge gap conversationally.

## Excluded From Replacement

Claude's discount, price-total, slot-hold, payment-link, guaranteed callback, automatic room-split, and live-availability examples are not approved for production. Their conversational shape may be reused only with runtime-supplied facts.
