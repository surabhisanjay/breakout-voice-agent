# Escalation Readiness

## Validation Method

Each required trigger was passed through the real `dispatch()` path with OpenAI disabled. “Agent selected” is the returned active agent, not the escalation recommendation.

| Case | Trigger | Agent selected | Summary generated | Handoff generated | Result |
|---|---|---|---|---|---|
| Human request | `Please connect me to a human agent.` | `inbound_agent` | Yes | Metadata only | PARTIAL |
| Complaint | `This is ridiculous and unacceptable.` | `inbound_agent` | Yes | Metadata only | PARTIAL |
| Refund | `I want a refund.` | `inbound_agent` | No | No | FAIL |
| Safety concern | `Someone cannot breathe in the room.` | `inbound_agent` | No | No | FAIL |
| Repeated dissatisfaction, turn 1 | `I am frustrated because this is still not working.` | `inbound_agent` | No | No | Expected first warning |
| Repeated dissatisfaction, turn 2 | `You keep asking me the same thing.` | `inbound_agent` | Yes | Metadata only | PARTIAL |

## Spoken-Response Evidence

- Human request: escalation metadata was true, but the response asked for a name and number rather than saying a transfer occurred.
- Complaint: anger triggered escalation, but `This is ridiculous and unacceptable` was also stored as customer name `Ridiculous And Unacceptable`; the response then addressed the caller by that false name.
- Refund: response discussed cancellation policy and did not escalate.
- Safety: response asked how many people were joining.
- Repeated dissatisfaction: escalation metadata appeared on the second negative turn, but autonomous qualification continued.

## Component Verification

### Sentiment Agent

- Angry and repeated-frustration detection works for its configured phrases.
- Active danger has no dedicated detection.
- Generic complaint sentences can corrupt the name field before/while sentiment metadata is applied.

### Escalation Agent

- Human request, anger, repeated frustration, booking failure, and explicit dispute phrases generate an `EscalationResult`.
- Plain refund language and safety emergencies do not.
- The result is advisory and does not alter routing.

### Handoff Summary Agent

- Generates a concise dictionary from memory when escalation is true.
- It is not delivered to a human connector.
- The summary can contain corrupted facts because it trusts memory, including false extracted names.

### API/Handoff Transport

`app.ChatResponse` contains only `response`. The API drops escalation and handoff metadata from the Vapi response, and no transfer/queue/callback integration consumes it.

## Readiness Decision

**NOT READY.** Detection exists, but safety/refund coverage and actual human transfer are release blockers.
