# Conversation Intelligence Upgrade Release Notes

## Summary

This release adds transcript-driven conversation intelligence around the existing Breakout agent architecture. Booking, Kreeda, qualification, memory extraction, GPT reasoning, FastAPI, and Vapi contracts remain intact.

## New Agents

- `SentimentAgent`: tracks current sentiment and a bounded per-session history across conversation stages.
- `EscalationAgent`: detects human requests, repeated frustration, misunderstanding, booking/API failures, and policy disputes.
- `HandoffSummaryAgent`: creates concise internal takeover summaries with context, booking status, and outstanding questions.

## Prompt Improvements

- Added examples for late-night birthday requests, non-horror preferences, timing objections, booking hesitation, and package comparison.
- Added closest-alternative guidance when a requested option is unavailable.
- Added pressure-free handling when a customer needs time to decide.
- Reinforced answer-first behavior, one-question turns, and catalogue avoidance.

## Integration

The new intelligence layer is attached to the existing `main.dispatch()` boundary:

1. Customer sentiment is observed before existing routing.
2. Existing inbound or booking logic handles the turn unchanged.
3. Escalation is evaluated after the result.
4. Sentiment and escalation metadata are attached to `AgentResponse` and transcript logs.
5. A human-readable handoff summary is generated when escalation is recommended.

No customer-facing response is replaced by the new layer, and escalation metadata does not override deterministic routing.

## Memory Additions

The existing session JSON now persists:

- sentiment confidence and reason;
- bounded sentiment history;
- current escalation state;
- bounded escalation history;
- consecutive failure count.

Existing booking and qualification fields are not overwritten.

## Tests Added

- Sentiment tracking across recommendation and booking stages.
- Repeated-frustration escalation.
- Explicit human-request escalation.
- Booking/API failure escalation.
- Concise handoff summaries and outstanding fields.
- Dispatch metadata integration without route changes.
- Spoken-response preservation during escalation detection.

## Remaining Limitations

- Rule-based sentiment is intentionally conservative and English-first.
- LLM assistance is supported through an injected classifier but is not called automatically, avoiding extra latency and cost.
- Escalation is an internal signal only; transport to a contact-center queue still requires a destination integration.
- The supplied analysis covers seven attached transcript files, not additional calls that were referenced but not attached.
- Vapi call IDs and backend session IDs are not yet stored together.

## Recommended Next Improvements

1. Label a larger set of Breakout calls for sentiment calibration.
2. Connect escalation output to the selected human-support queue.
3. Add multilingual sentiment and handoff phrase coverage.
4. Add end-to-end telemetry correlating Vapi, FastAPI, and Kreeda request IDs.
