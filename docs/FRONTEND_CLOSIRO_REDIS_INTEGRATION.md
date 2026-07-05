# Frontend/Closiro Redis Integration

## Architecture

```mermaid
flowchart LR
    A[Breakout Backend] -->|Publish events| R[(Redis Pub/Sub)]
    R -->|Subscribe| C[Closiro Backend]
    C -->|SSE| F[React Frontend]
```

The Breakout Backend is the event publisher. Closiro subscribes to Redis and
exposes the frontend-facing SSE connection. Redis supplements the existing
Closiro REST webhooks; it does not replace them.

## Redis Connection

Set the Redis connection URL in the Breakout Backend environment:

```env
REDIS_URL=redis://user:password@redis-host:6379/0
```

`REDIS_URL` is preferred. `REALTIME_REDIS_URL` and `CLOSIRO_REDIS_URL` are
supported aliases. Publishing is enabled automatically when a URL is present.

Optional settings:

```env
REALTIME_REDIS_ENABLED=true
REALTIME_REDIS_TIMEOUT=1.5
REALTIME_REDIS_RETRIES=1
```

## Channel Naming

All realtime events for one call use a single Redis channel:

```text
live_call:{call_id}
```

Example: `live_call:abc123`

Closiro should subscribe to the exact call channel needed by the frontend.

## Event Envelope

Every published Redis message is compact JSON using this envelope:

```json
{
  "version": 1,
  "event": "...",
  "call_id": "...",
  "timestamp": "2026-07-04T10:30:00+00:00",
  "payload": {}
}
```

Timestamps are UTC ISO 8601 values. Event-specific data is always inside
`payload`.

## Published Events

| Event | Emitted when |
|---|---|
| `call.started` | The first processed turn for a call; emitted once. |
| `agent.state` | Each processed turn, with the active agent, next agent, and intent. |
| `call.updated` | Each processed turn, with turn count, active agent, and latency. |
| `transcript.chunk` | A new customer or assistant turn is added to conversation memory. |
| `sentiment.updated` | The analyzed sentiment changes. |
| `intent.updated` | The detected conversation intent changes. |
| `timeline.event` | A new timeline milestone is generated, such as qualification, recommendation, slot selection, booking, payment, or completion. |
| `booking.created` | A booking ID is created for the first time; emitted once per booking. |
| `booking.updated` | Relevant booking, payment, slot, price, room, location, date, or participant state changes. |
| `escalation.created` | Escalation becomes active or the escalation agent is selected; emitted once. |
| `summary.ready` | A completed booking response contains the final summary, handoff, evaluation, metrics, and CSAT; emitted once. |
| `call.ended` | A completed booking response is finalized; emitted once after `summary.ready`. |

## Example Events

### Transcript Chunk

```json
{
  "version": 1,
  "event": "transcript.chunk",
  "call_id": "abc123",
  "timestamp": "2026-07-04T10:30:04+00:00",
  "payload": {
    "speaker": "customer",
    "text": "I need a room tomorrow.",
    "timestamp": "2026-07-04T10:30:04+00:00",
    "event_type": "final",
    "sequence": 1
  }
}
```

### Booking Updated

```json
{
  "version": 1,
  "event": "booking.updated",
  "call_id": "abc123",
  "timestamp": "2026-07-04T10:34:20+00:00",
  "payload": {
    "booking": {
      "booking_id": "bk_example",
      "status": "RESERVED"
    },
    "payment": {
      "payment_url": "https://payments.example/checkout/example"
    },
    "price_breakdown": {
      "base_price": 3000,
      "discount": 0,
      "final_price": 3000,
      "currency": "INR"
    },
    "state": {
      "location": "Whitefield",
      "room": "Murder Mystery",
      "participants": 4,
      "selected_slot": "11:30 AM"
    }
  }
}
```

### Summary Ready

```json
{
  "version": 1,
  "event": "summary.ready",
  "call_id": "abc123",
  "timestamp": "2026-07-04T10:34:21+00:00",
  "payload": {
    "session_id": "session-123",
    "conversation_summary": {"outcome": "booking_reserved"},
    "handoff_summary": {},
    "evaluation": {},
    "metrics": {},
    "csat": {}
  }
}
```

### Call Ended

```json
{
  "version": 1,
  "event": "call.ended",
  "call_id": "abc123",
  "timestamp": "2026-07-04T10:34:21+00:00",
  "payload": {
    "session_id": "session-123",
    "outcome": "booking_reserved",
    "booking": {"booking_id": "bk_example"},
    "payment": {"payment_url": "https://payments.example/checkout/example"}
  }
}
```

### Escalation Created

```json
{
  "version": 1,
  "event": "escalation.created",
  "call_id": "abc123",
  "timestamp": "2026-07-04T10:32:10+00:00",
  "payload": {
    "session_id": "session-123",
    "escalation": {"required": true, "reason": "customer_requested_human"},
    "handoff_summary": {}
  }
}
```

## Ordering Guarantees

Events are synchronously published to the call's single Redis channel in
conversation order. Redis Pub/Sub preserves message order for a subscriber on
that connection. The `sequence` field additionally orders transcript turns.

Redis Pub/Sub is transient and provides no replay for disconnected consumers.
Closiro should connect before the call events it needs to relay and preserve
the received order when forwarding them over SSE.

## Failure Behavior

Realtime publishing is best-effort. If Redis is disabled, unavailable, or
times out, the failure is logged and the conversation continues. Booking,
payment, memory, WATI, and Closiro REST webhook behavior are unaffected.

## Deployment Prerequisites

- A Redis server reachable from both the Breakout and Closiro backends.
- `REDIS_URL` configured for the Breakout Backend.
- Closiro subscribed to `live_call:{call_id}` and relaying events over SSE.
- Network and Redis ACL permissions allowing publish/subscribe access.
- No other Breakout Backend changes are required.
