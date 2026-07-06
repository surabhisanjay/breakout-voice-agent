# NOT READY FOR PRODUCTION

| Remaining blocker | Risk level | Fix estimate | Test required |
|---|---|---:|---|
| No completed live Kreeda booking/reference evidence; live provider construction can hang | Critical | 1-2 days | Credential-gated live availability/cart/create/lookup smoke test with latency bound |
| Escalation is metadata-only; no Vapi/human transfer transport | Critical | 2-4 days | Human-request integration test proving delivery and takeover |
| Safety and plain refund requests do not escalate | Critical | 1 day | Safety/refund trigger matrix through `/chat` and handoff transport |
| Room/date/location changes retain stale selected slot/cart context | High | 1 day | State invalidation and retry tests for every booking modification |
| Booking FAQ interruptions are ignored | High | 1 day | Grounded FAQ-answer-then-resume tests inside active booking |
| Complaint language is persisted as customer name | High | 1 day | Negative name-extraction corpus plus angry-customer replay |
| Runtime reconstruction does not hydrate booking state | High | 1-2 days | Restart session after slot, name, last name, and phone stages |
| Independent provider booking reference is not validated | High | 0.5-1 day | Reject confirmed response lacking required reference; persist both identifiers |
