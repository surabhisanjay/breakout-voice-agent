# Breakout Agent — API Documentation

**Version:** 1.1.0
**Base URL:** `http://localhost:8000` (development) | `https://your-domain.com` (production)
**Format:** JSON
**Authentication:** None for core chat; Bearer JWT for `/api/v1/`

---

## Table of Contents

- [Health Check](#health-check)
- [Web Chat UI](#web-chat-ui)
- [POST /chat](#post-chat)
- [POST /reset](#post-reset)
- [GET /memory/{session_id}](#get-memorysession_id)
- [GET /intelligence/{session_id}](#get-intelligencesession_id)
- [POST /webhooks/wati](#post-webhookswati)
- [POST /debug](#post-debug)
- [Closiro CRM API — /api/v1/](#closiro-crm-api--apiv1)

---

## Health Check

### `GET /health`

**Purpose:** Confirm the server is running and check the active booking provider.

**Auth:** None

**Request:** No body required.

**Example Request:**
```bash
curl http://localhost:8000/health
```

**Example Response:**
```json
{
  "status": "ok",
  "booking_provider": "live-configured",
  "version": "1.1.0"
}
```

**`booking_provider` values:**
- `live-configured` — Kreeda API credentials present
- `simulator` — Running in offline simulator mode

**Error Responses:** None expected (500 if startup failed)

---

## Web Chat UI

### `GET /`

**Purpose:** Serve the embedded web chat HTML interface.

**Auth:** None

**Example Request:**
```bash
curl http://localhost:8000/
```

**Response:** HTML page (`text/html`)

---

### `GET /web-chat`

**Purpose:** Same as `GET /` — serves the web chat UI.

**Auth:** None

---

## POST /chat

**Purpose:** Send a customer message and receive the agent's response. This is the primary endpoint for web chat and Vapi voice integrations.

**Auth:** None

**Request:**
```
POST /chat
Content-Type: application/json
```

```json
{
  "session_id": "user-abc-123",
  "message": "Hi, I want to book a birthday party for 10 people"
}
```

**Request Schema:**

| Field | Type | Required | Constraints |
|---|---|---|---|
| `session_id` | string | ✅ | 1–80 chars; `[A-Za-z0-9_.:-]` |
| `message` | string | ✅ | min 1 char |

**Response:**
```json
{
  "response": "Happy to help! For a birthday party of 10, I'd recommend Murder Mystery at Whitefield — it's investigation-led and great for groups. Which date are you thinking?",
  "next_agent": "inbound_agent",
  "media": [],
  "booking": {},
  "payment": {},
  "escalation": {
    "escalate": false,
    "reason": "",
    "summary": ""
  },
  "handoff_summary": null,
  "sentiment_analysis": {
    "sentiment": "positive",
    "confidence": 0.88,
    "escalation_recommended": false,
    "reason": "Customer expressed enthusiasm",
    "stage": "discovery"
  },
  "call_intelligence": {
    "ai_summary": {
      "summary": "Customer inquiring about birthday party options.",
      "intent": "birthday_party",
      "outcome": "inquiry_active"
    },
    "customer_profile": {
      "name": null,
      "group_size": 10,
      "event_type": "Birthday Party"
    },
    "sentiment_analysis": { "overall": "positive", "confidence": 0.88 },
    "timeline_events": [
      { "sequence": 1, "event": "inquiry", "description": "Birthday party inquiry" }
    ],
    "transcript": [
      { "sequence": 1, "speaker_type": "customer", "text": "Hi, I want to book...", "spoken_at_second": 0.0 },
      { "sequence": 2, "speaker_type": "agent", "text": "Happy to help!...", "spoken_at_second": 3.0 }
    ],
    "follow_up_recommendations": [],
    "recording": { "url": "", "available": false }
  },
  "ai_summary": {
    "summary": "Customer inquiring about birthday party options.",
    "intent": "birthday_party",
    "outcome": "inquiry_active"
  },
  "customer_profile": {
    "name": null,
    "group_size": 10,
    "event_type": "Birthday Party"
  },
  "timeline_events": [
    { "sequence": 1, "event": "inquiry", "description": "Birthday party inquiry" }
  ],
  "follow_up_recommendations": [],
  "transcript": [
    { "sequence": 1, "speaker_type": "customer", "text": "Hi, I want to book...", "spoken_at_second": 0.0 }
  ],
  "recording": { "url": "", "available": false }
}
```

**Error Responses:**

| Status | Condition | Response |
|---|---|---|
| `400` | Empty message | `{"detail": "message cannot be empty."}` |
| `400` | Invalid session_id chars | `{"detail": "session_id may contain only..."}` |
| `422` | Missing required fields | Pydantic validation error object |
| `500` | Internal error | `{"detail": "Internal Server Error"}` |

**Notes:**
- Sessions are created automatically on first message.
- Each session maintains independent conversation memory.
- OpenAI is used for response composition; if unavailable, deterministic fallback is used.

---

## POST /reset

**Purpose:** Clear a session's conversation memory and remove it from the active session cache.

**Auth:** None

**Request:**
```
POST /reset
Content-Type: application/json
```

```json
{
  "session_id": "user-abc-123"
}
```

**Response:**
```json
{
  "success": true
}
```

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | Invalid session_id format |

---

## GET /memory/{session_id}

**Purpose:** Inspect the raw memory state for a session. Useful for debugging and development.

**Auth:** None

**Example Request:**
```bash
curl http://localhost:8000/memory/user-abc-123
```

**Example Response:**
```json
{
  "session_id": "user-abc-123",
  "memory": {
    "customer_name": "Priya",
    "phone": null,
    "location": "Whitefield",
    "participants": 10,
    "age_group": "adults",
    "intent": "birthday_party",
    "event_type": "Birthday Party",
    "preferred_date": null,
    "room": null,
    "recommended_option": "Murder Mystery",
    "booking_id": null,
    "sentiment": "positive",
    "current_workflow": "general",
    "booking_started": false,
    "channel": "web_chat",
    "conversation": [...]
  }
}
```

**Error Responses:** None (creates session if not exists)

---

## GET /intelligence/{session_id}

**Purpose:** Get the full conversation intelligence report for a session.

**Auth:** None

**Example Request:**
```bash
curl http://localhost:8000/intelligence/user-abc-123
```

**Example Response:**
```json
{
  "session_id": "user-abc-123",
  "ai_summary": { ... },
  "customer_profile": { ... },
  "sentiment_analysis": { ... },
  "timeline_events": [ ... ],
  "transcript": [ ... ],
  "follow_up_recommendations": [ ... ],
  "recording": { "url": "", "available": false }
}
```

**Notes:** If intelligence has not been computed for the session yet, it is computed on-demand and cached.

---

## POST /webhooks/wati

**Purpose:** Receive inbound WhatsApp messages from WATI and process them through the agent.

**Aliases:** `POST /api/v1/whatsapp/wati/webhook` (both paths are equivalent)

**Auth:** None (configure IP allowlist at firewall level)

**Request:** WATI webhook payload (JSON object)

The backend extracts `phone` and `text` from the WATI payload using flexible field mapping:

**Supported phone fields:** `waid`, `whatsappnumber`, `sender`, `from`, `phone`, `mobile`
**Supported text fields:** `text`, `body`, `message`, `messageText`, `textMessage`

**Example WATI Payload:**
```json
{
  "eventType": "message",
  "waid": "919845012367",
  "text": { "body": "I want to book an escape room" }
}
```

**Response:**
```json
{
  "success": true,
  "ignored": false,
  "session_id": "whatsapp:919845012367",
  "response": "Happy to help! Which location works for you?",
  "next_agent": "inbound_agent",
  "wati": {
    "success": true,
    "delivered": true,
    "message_id": "wati-msg-001"
  }
}
```

**Ignored Messages:** Outbound confirmations, template messages, and status updates are ignored:
```json
{ "success": true, "ignored": true }
```

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | Payload not a JSON object |
| `400` | No message text found |

**Setup:** Configure WATI to POST webhooks to `https://your-domain.com/webhooks/wati`

---

## POST /debug

**Purpose:** Echo a payload back — for integration debugging only.

**Auth:** None

**Request:**
```json
{ "any": "payload" }
```

**Response:**
```json
{ "received": { "any": "payload" } }
```

---

## Closiro CRM API — /api/v1/

All routes require: `Authorization: Bearer <jwt>`

JWT payload requirements:
```json
{
  "sub": 1,
  "user_id": 1,
  "agent_id": 1,
  "org_id": "org_test",
  "role": "sales_agent"
}
```

---

### `GET /api/v1/me`

**Purpose:** Get current user info from token.

**Response:**
```json
{ "id": 1, "agent_id": 1, "org_id": "org_test", "role": "sales_agent", "name": "Agent" }
```

---

### `GET /api/v1/contacts`

**Purpose:** List contacts scoped to the authenticated user.

**Query Params:** `page`, `limit`, `sort`, `order`, `stage`, `priority`

**Response:**
```json
{
  "items": [
    {
      "id": 1,
      "name": "Priya Mehta",
      "phone": "9845012367",
      "email": "priya@example.com",
      "stage": "qualified",
      "priority": "high",
      "channel": "voice",
      "last_activity": "2026-07-01T09:00:00Z"
    }
  ],
  "page": 1,
  "limit": 25,
  "total": 2
}
```

---

### `GET /api/v1/leads`

**Purpose:** List leads scoped to the authenticated user.

**Response:**
```json
{
  "items": [
    {
      "id": 1,
      "contact_id": 1,
      "first_name": "Priya",
      "last_name": "Mehta",
      "location": "Whitefield",
      "event_type": "Escape Room",
      "party_size": 4,
      "pipeline_stage": "qualified",
      "priority": "high",
      "estimated_value": 12000
    }
  ],
  "page": 1,
  "limit": 25,
  "total": 2
}
```

---

### `GET /api/v1/calls`

**Purpose:** List calls for the authenticated agent.

**Response:**
```json
{
  "items": [
    {
      "id": 1,
      "status": "answered",
      "category": "booking",
      "intent": "Booking",
      "summary": "Customer booked Murder Mystery.",
      "sentiment": "positive",
      "occurred_at": "2026-07-01T09:00:00Z",
      "live_status": "Answered",
      "live_duration_seconds": 87,
      "is_ai": true
    }
  ],
  "page": 1,
  "limit": 25,
  "total": 2
}
```

---

### `GET /api/v1/calls/{call_id}/summary`

**Purpose:** Get AI conversation summary for a specific call.

**Response:**
```json
{
  "call_id": 1,
  "summary": {
    "summary": "Customer booked Murder Mystery at Whitefield.",
    "intent": "booking",
    "outcome": "booking_completed"
  }
}
```

---

### `GET /api/v1/calls/{call_id}/transcript`

**Purpose:** Get full transcript for a call.

**Response:**
```json
{
  "call_id": 1,
  "lines": [
    { "sequence": 1, "speaker_type": "customer", "text": "I want to book.", "spoken_at_second": 1.2 },
    { "sequence": 2, "speaker_type": "agent", "text": "I can help.", "spoken_at_second": 2.4 }
  ]
}
```

---

### `GET /api/v1/calls/{call_id}/recording`

**Purpose:** Get recording URL for a call.

**Response:**
```json
{
  "call_id": 1,
  "recording_url": "",
  "available": false
}
```

---

### `GET /api/v1/escalations`

**Purpose:** List open escalations.

**Response:**
```json
{
  "items": [
    {
      "id": 1,
      "escalation_status": "open",
      "reason": "Customer requested human",
      "target_group": "sales_manager",
      "created_at": "2026-07-01T09:00:00Z",
      "handoff_summary": { "summary": "...", "action_items": ["Call customer"] }
    }
  ],
  "page": 1,
  "limit": 25,
  "total": 1
}
```

---

### `GET /api/v1/analytics/dashboard`

**Purpose:** Role-specific KPI dashboard.

**Agent Response:**
```json
{
  "calls_today": { "current": 21, "target": 25 },
  "conversion_rate": { "current": 0.42, "target": 0.50 },
  "pipeline_deals": { "current": 8, "target": 10 },
  "rank": { "position": 2, "total_agents": 8 },
  "performance_score": 87.5
}
```

**Manager Response:**
```json
{
  "booking_rate_by_room": [{ "room": "Murder Mystery", "rate": 0.45 }],
  "talk_listen_ratio": { "talk": 0.55, "listen": 0.45 },
  "customer_satisfaction": { "very_satisfied": 32, "satisfied": 28 },
  "channel_distribution": { "voice": 0.6, "whatsapp": 0.25, "email": 0.15 },
  "escalation_tickets": { "resolved": 18, "unresolved": 7 },
  "pipeline_conversion_rate": 0.38
}
```

---

### `GET /api/v1/bookings`

**Purpose:** List bookings.

**Response:**
```json
{
  "items": [
    {
      "id": 1,
      "contact_id": 1,
      "event_type": "Escape Room",
      "location": "Whitefield",
      "party_size": 4,
      "event_date": "2026-07-01T09:00:00Z",
      "total_amount": 12000.0,
      "paid_amount": 0.0,
      "balance_amount": 12000.0,
      "payment_status": "PAYMENT_PENDING",
      "external_booking_id": "KRD-20260701-0042"
    }
  ],
  "page": 1,
  "limit": 25,
  "total": 1
}
```

---

### Common Error Responses

| Status | Body | Condition |
|---|---|---|
| `401` | `{"detail": "Bearer token required."}` | Missing Authorization header |
| `401` | `{"detail": "Invalid bearer token."}` | Malformed JWT |
| `403` | `{"detail": "Token must include org_id and a supported role."}` | Invalid org_id or role |
| `403` | `{"detail": "Manager or admin role required."}` | Insufficient role |
| `403` | `{"detail": "Self access only."}` | Agent accessing another agent's resource |
| `404` | `{"detail": "Resource not found."}` | Resource not found or not in scope |
