# Verified Issues

## Classification

| Finding | Reproduction result | Final status | Evidence |
|---|---|---|---|
| Confirmation allowed without booking ID | Not reproducible | FIXED_ALREADY | Booking gate and composer reject missing ID |
| Confirmation allowed without booking reference | VERIFIED | FIXED | Orchestrator, BookingAgent, composer, and regression require both identifiers |
| Booking state lost after runtime reconstruction | VERIFIED | FIXED | Reconstructed agent revalidates persisted slot and completes booking |
| Date change retained slot/cart | Not reproducible | FIXED_ALREADY | Existing blocker regressions pass |
| Room change retained slot/cart | Not reproducible | FIXED_ALREADY | Existing blocker regressions pass |
| `day after tomorrow` became tomorrow/unusable | VERIFIED | FIXED | Extracts and normalizes to reference date +2 |
| Past month/day silently rolled to next year | VERIFIED | FIXED | Ambiguous past date now returns empty and cannot reach Kreeda |
| Ambiguous two-location message silently chose one | VERIFIED | FIXED | Extractor returns empty and forces clarification |
| JP Nagar/Whitefield/Koramangala ASR variants failed | Not reproducible | FIXED_ALREADY | 50 ASR scenarios pass |
| Evening slot request returned all slots | VERIFIED | FIXED | Period filter returns verified evening slots only |
| Earliest/first-available request looped | Not reproducible | FIXED_ALREADY | Earliest selection existed; summary behavior now also covered |
| Show-all-slots failed | VERIFIED | FIXED | Slot-summary branch returns every cached verified slot |
| Large group entered closed dead-end | VERIFIED | FIXED | Dedicated coordination state collects contact and selects events team |
| Corporate event entered single-room booking | VERIFIED | FIXED | Corporate recommendation and BookingAgent use event coordination |
| Birthday/package names reached Kreeda | Not reproducible | FIXED_ALREADY | Package guard bypasses single-room availability |
| Participant correction failed to rerun availability | Not reproducible | FIXED_ALREADY | Existing correction/capacity regressions pass |
| Cancellation without reference claimed success | Not reproducible | FIXED_ALREADY | Current code requested a reference; final flow now verifies provider status |
| Cancellation reference follow-up dead-ended | VERIFIED | FIXED | Dedicated reference-waiting state performs validated cancellation |
| Human phrase `connect me to an agent` failed | VERIFIED | FIXED | Expanded human-request article/role matching |
| Human/refund/safety handoff metadata hidden from Vapi response | VERIFIED | FIXED | `/chat` exposes next agent, escalation, and summary |
| Safety escalation lost when summary generation failed | VERIFIED | FIXED | Safe fallback summary preserves escalation selection |
| Fainted/collapsed/passed-out safety phrases failed | Not reproducible | FIXED_ALREADY | Current detector and 50 escalation scenarios pass |
| Refund variants failed | Not reproducible | FIXED_ALREADY | Deserve/money-back variants and matrix pass |
| Complaint text became customer name | Not reproducible | FIXED_ALREADY | Existing negative extraction regressions pass |
| `Book`/`Game` became customer name | Not reproducible | FIXED_ALREADY | Plausibility checks reject both |
| Booking FAQ interruption ignored question | Not reproducible for parking; VERIFIED for food/rules variants | FIXED | Grounded FAQ variants answer and preserve booking state |
| Compound FAQ answered only food | VERIFIED | FIXED | Compound-question handler now precedes single-topic demo answer |
| Repeated qualification question had no termination | VERIFIED | FIXED | Three identical agent turns select escalation/handoff |
| Recommendation ignored location capacity | Not reproducible | FIXED_ALREADY | 50 inventory/capacity scenarios pass |
| OpenAI failure caused false booking | Not reproducible | FIXED_ALREADY | Deterministic draft and dual-identifier confirmation guard remain active |
| Phone beginning with 5 caused empty-phone booking | NOT_REPRODUCIBLE | No change | Indian mobile validation rejects it and booking gate blocks empty phone |

## Final Reproduction Result

Every VERIFIED in-repository issue is fixed and has regression coverage. Remaining items are the external proofs listed in `FINAL_KNOWN_ISSUES.md`.
