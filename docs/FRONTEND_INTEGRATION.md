# Breakout Agent — Frontend Integration Guide

**Version:** 1.1.0
**For:** Frontend Engineering Team
**Backend Base URL:** `http://your-domain.com` (local: `http://localhost:8000`)

---

## Table of Contents

1. [Overview](#1-overview)
2. [Authentication](#2-authentication)
3. [Web Chat Integration](#3-web-chat-integration)
4. [ChatResponse — Full Object Reference](#4-chatresponse--full-object-reference)
5. [Conversation Summary Object](#5-conversation-summary-object)
6. [Conversation Metrics Object](#6-conversation-metrics-object)
7. [Evaluation Object](#7-evaluation-object)
8. [CSAT Object](#8-csat-object)
9. [Sentiment Timeline Object](#9-sentiment-timeline-object)
10. [Sentiment Graph JSON](#10-sentiment-graph-json)
11. [Booking Object](#11-booking-object)
12. [Payment Object](#12-payment-object)
13. [Escalation Object](#13-escalation-object)
14. [Handoff Summary Object](#14-handoff-summary-object)
15. [Learning / Follow-up Object](#15-learning--follow-up-object)
16. [Transcript Object](#16-transcript-object)
17. [Customer Profile Object](#17-customer-profile-object)
18. [Session Management](#18-session-management)
19. [Error Handling](#19-error-handling)
20. [Closiro CRM API Reference (Frontend Dashboard)](#20-closiro-crm-api-reference-frontend-dashboard)
21. [Rendering Guidelines](#21-rendering-guidelines)

---

## 1. Overview

The backend exposes two API layers:

| Layer | Purpose | Auth |
|---|---|---|
| **Core Chat API** (`/chat`, `/reset`, `/memory`) | Conversation engine | None required |
| **Closiro CRM API** (`/api/v1/...`) | Sales dashboard, analytics, CRM | Bearer JWT |

All responses are JSON. The primary endpoint for conversation is `POST /chat`.

---

## 2. Authentication

### Core Chat API

No authentication required. Use a client-generated `session_id` (see §18).

### Closiro CRM API

All `/api/v1/` routes require:

```
Authorization: Bearer <jwt_token>
```

The JWT must be a base64url-encoded JSON payload containing:

```json
{
  "sub": 1,
  "user_id": 1,
  "agent_id": 1,
  "org_id": "org_test",
  "role": "sales_agent",
  "name": "Agent Name"
}
```

Valid roles: `sales_agent`, `sales_manager`, `admin`

**Token format:** Standard JWT (3 dot-separated base64url parts). The backend validates only the payload — signature validation is not enforced in the current MVP.

---

## 3. Web Chat Integration

### Send a Message

```
POST /chat
Content-Type: application/json
```

**Request:**
```json
{
  "session_id": "user-abc-123",
  "message": "Hi, I want to book a birthday party"
}
```

**Request Schema:**

| Field | Type | Required | Constraints |
|---|---|---|---|
| `session_id` | string | ✅ | 1–80 chars, `[A-Za-z0-9_.:-]` only |
| `message` | string | ✅ | min 1 char |

---

## 4. ChatResponse — Full Object Reference

Every `POST /chat` returns this object:

```json
{
  "response": "Happy to help! For a birthday party of 10, I'd recommend...",
  "next_agent": "inbound_agent",
  "media": [],
  "booking": {},
  "payment": {},
  "escalation": {},
  "handoff_summary": null,
  "sentiment_analysis": {
    "sentiment": "positive",
    "confidence": 0.88,
    "escalation_recommended": false,
    "reason": "Customer expressed enthusiasm about birthday party",
    "stage": "discovery"
  },
  "call_intelligence": { ... },
  "ai_summary": { ... },
  "customer_profile": { ... },
  "timeline_events": [ ... ],
  "follow_up_recommendations": [ ... ],
  "transcript": [ ... ],
  "recording": { "url": "", "available": false }
}
```

### Field Descriptions

| Field | Type | Description |
|---|---|---|
| `response` | string | Agent's text response — always present |
| `next_agent` | string | `inbound_agent` / `booking_agent` / `escalation_agent` |
| `media` | array | Media attachments (images, videos, links) |
| `booking` | object | Booking details (empty `{}` if no active booking) |
| `payment` | object | Payment details (empty `{}` if no payment) |
| `escalation` | object | Escalation assessment |
| `handoff_summary` | object\|null | Handoff summary (only if `next_agent == escalation_agent`) |
| `sentiment_analysis` | object | Customer sentiment for this turn |
| `call_intelligence` | object | Full conversation intelligence report |
| `ai_summary` | object | AI-generated conversation summary |
| `customer_profile` | object | Extracted customer profile |
| `timeline_events` | array | Key conversation events |
| `follow_up_recommendations` | array | Recommended next actions |
| `transcript` | array | Full conversation transcript |
| `recording` | object | Recording URL (if available) |

---

## 5. Conversation Summary Object

Returned in `ai_summary`:

```json
{
  "summary": "Customer Priya Mehta inquired about a birthday party for 10 adults at Whitefield. Agent recommended Murder Mystery. Customer accepted and booking was completed. Payment link sent via WhatsApp.",
  "intent": "birthday_party",
  "outcome": "booking_completed",
  "action_items": [
    "Send payment reminder if unpaid after 2 hours",
    "Confirm booking details via WhatsApp"
  ],
  "key_facts": {
    "customer_name": "Priya Mehta",
    "location": "Whitefield",
    "group_size": 10,
    "room": "Murder Mystery",
    "date": "2026-07-10",
    "slot": "2:30 PM"
  }
}
```

### How to Render

- Display `summary` as the conversation summary card
- Use `outcome` to set the card's status badge color:
  - `booking_completed` → green
  - `escalation_required` → red
  - `inquiry_only` → grey
- Render `action_items` as a task list
- Use `key_facts` to populate a customer summary panel

---

## 6. Conversation Metrics Object

Available in `call_intelligence`:

```json
{
  "metrics": {
    "total_turns": 12,
    "agent_turns": 6,
    "customer_turns": 6,
    "duration_estimate_seconds": 144,
    "qualification_completion": 0.88,
    "booking_conversion": true,
    "escalation_triggered": false
  }
}
```

---

## 7. Evaluation Object

Part of `call_intelligence`:

```json
{
  "evaluation": {
    "qualification_score": 8.5,
    "recommendation_quality": 9.0,
    "booking_completion": true,
    "escalation_required": false,
    "csat_estimate": 4.2,
    "agent_performance": "excellent",
    "summary": "Agent efficiently qualified customer, provided relevant recommendation, and completed booking."
  }
}
```

### Rendering

| Field | Display |
|---|---|
| `qualification_score` | Progress bar (0–10) |
| `recommendation_quality` | Star rating (0–10 → 5 stars) |
| `csat_estimate` | CSAT score badge (1–5) |
| `agent_performance` | Badge: excellent / good / needs_improvement |

---

## 8. CSAT Object

Frontend can submit a CSAT score per call via:

```
POST /api/v1/calls/{call_id}/csat
Authorization: Bearer <token>
Content-Type: application/json
```

**Request:**
```json
{
  "score": 5,
  "comment": "Great experience, very helpful agent"
}
```

**Response:**
```json
{ "saved": true, "call_id": 1 }
```

---

## 9. Sentiment Timeline Object

Available in `timeline_events` within `call_intelligence`:

```json
[
  {
    "sequence": 1,
    "event": "inquiry",
    "description": "Customer inquired about birthday party options",
    "sentiment": "neutral",
    "timestamp": "2026-07-01T09:00:00Z"
  },
  {
    "sequence": 2,
    "event": "recommendation",
    "description": "Agent recommended Murder Mystery",
    "sentiment": "positive"
  },
  {
    "sequence": 3,
    "event": "booking_completed",
    "description": "Booking confirmed, payment link sent",
    "sentiment": "excited"
  }
]
```

### Rendering

Render as a vertical timeline with colored sentiment indicators:
- `positive` / `excited` → green dot
- `neutral` → grey dot
- `confused` → yellow dot
- `negative` / `frustrated` → red dot

---

## 10. Sentiment Graph JSON

Available in `sentiment_analysis` within `call_intelligence`:

```json
{
  "overall": "positive",
  "confidence": 0.88,
  "trajectory": "improving",
  "history": [
    { "turn": 1, "sentiment": "neutral", "confidence": 0.72 },
    { "turn": 2, "sentiment": "neutral", "confidence": 0.68 },
    { "turn": 3, "sentiment": "positive", "confidence": 0.84 },
    { "turn": 4, "sentiment": "excited", "confidence": 0.91 }
  ]
}
```

### Rendering

Plot `history` as a line chart:
- X axis: turn number
- Y axis: sentiment score (map: frustrated=-2, negative=-1, neutral=0, positive=1, excited=2)
- Color the trajectory line by `trajectory` (green = improving, red = declining)

---

## 11. Booking Object

Returned in `booking` field of `ChatResponse`. Empty `{}` if no booking in progress.

```json
{
  "booking_id": "KRD-20260701-0042",
  "reference": "BKG-942",
  "order_id": "ORD-1847",
  "status": "BOOKED",
  "room": "Murder Mystery",
  "location": "Whitefield",
  "date": "2026-07-10",
  "time": "2:30 PM",
  "participants": 10
}
```

### Field Reference

| Field | Type | Description |
|---|---|---|
| `booking_id` | string | Kreeda booking ID |
| `reference` | string | Kreeda booking reference |
| `order_id` | string | Kreeda order ID |
| `status` | string | `BOOKED` / `PENDING` / `CANCELLED` |
| `room` | string | Room/game name |
| `location` | string | Branch name |
| `date` | string | Booking date |
| `time` | string | Selected time slot |
| `participants` | int/string | Group size |

### Rendering

Show as a booking confirmation card with all fields. If `status == "BOOKED"`, show a green confirmation banner. If `status == "PENDING"`, show pending with a payment prompt.

---

## 12. Payment Object

Returned in `payment` field of `ChatResponse`. Empty `{}` if no payment context.

```json
{
  "status": "PAYMENT_PENDING",
  "payment_url": "https://bs.kreeda.icu/checkout/eyJhb...",
  "payment_deadline": "2026-07-01T20:00:00+05:30",
  "booking_status": "BOOKED"
}
```

### Field Reference

| Field | Type | Description |
|---|---|---|
| `status` | string | `PAYMENT_PENDING` / `PAID` / `UNPAID` / `FAILED` |
| `payment_url` | string | Kreeda checkout URL (may be long) |
| `payment_deadline` | string | ISO 8601 deadline |
| `booking_status` | string | Booking status at time of payment creation |

### Rendering

If `payment_url` is present, show a prominent CTA button:
- **Label:** "Complete Payment"
- **Link:** `payment_url`
- **Open in:** new tab
- **Urgency:** Show countdown to `payment_deadline`

If `status == "PAID"`, show green confirmation. If `status == "FAILED"`, show error with retry option.

---

## 13. Escalation Object

Always present in `escalation` field. `escalate: false` is the normal case.

```json
{
  "escalate": false,
  "reason": "",
  "summary": ""
}
```

When escalation is triggered:

```json
{
  "escalate": true,
  "reason": "Customer requested human representative",
  "summary": "Customer was frustrated with payment issues and explicitly requested to speak with a human agent. All booking details collected.",
  "urgency": "medium",
  "recommended_action": "Call customer within 30 minutes"
}
```

### Rendering

If `escalate == true`:
- Show a red alert banner: "Customer needs human assistance"
- Display `reason` as the escalation cause
- Show `summary` in the case detail
- Show `recommended_action` as a task for the agent
- Update `next_agent` indicator to "Escalation Required"

---

## 14. Handoff Summary Object

Present in `handoff_summary` only when `next_agent == "escalation_agent"`. Otherwise `null`.

```json
{
  "customer_name": "Siddharth Khandelwal",
  "phone": "9982151357",
  "location": "Koramangala",
  "intent": "escape_room_inquiry",
  "event_type": "Escape Room",
  "participants": 4,
  "recommended_option": "Classified",
  "escalation_reason": "Customer requested human representative",
  "summary": "Customer Siddharth Khandelwal is looking to book an escape room for 4 adults at Koramangala. Agent recommended Classified. Customer requested to speak with a human before confirming.",
  "action_items": [
    "Call customer to confirm booking",
    "Offer 10% group discount if applicable"
  ]
}
```

### Rendering

Show as a structured handoff card visible to the human agent picking up the escalation. Include all collected customer details and action items.

---

## 15. Learning / Follow-up Object

Returned in `follow_up_recommendations` as a string array:

```json
{
  "follow_up_recommendations": [
    "Send payment reminder via WhatsApp within 2 hours.",
    "Call customer to confirm booking details.",
    "Offer group discount if party size exceeds 8."
  ]
}
```

### Rendering

Show as a checklist in the agent's task panel. Each item can be marked complete.

---

## 16. Transcript Object

Returned in `transcript` as an array of turns:

```json
[
  {
    "sequence": 1,
    "speaker_type": "customer",
    "text": "Hi, I want to book a birthday party for 10 people",
    "spoken_at_second": 0.0
  },
  {
    "sequence": 2,
    "speaker_type": "agent",
    "text": "Happy to help! For a birthday party of 10, I'd recommend...",
    "spoken_at_second": 3.2
  }
]
```

### Field Reference

| Field | Type | Description |
|---|---|---|
| `sequence` | int | Turn sequence number (1-indexed) |
| `speaker_type` | string | `customer` / `agent` / `unknown` |
| `text` | string | Transcript text |
| `spoken_at_second` | float | Estimated time offset in seconds |

### Rendering

Render as a chat bubble UI:
- `customer` turns → left-aligned grey bubble
- `agent` turns → right-aligned brand-color bubble
- Show `spoken_at_second` as timestamp offset if recording is available

---

## 17. Customer Profile Object

Available in `customer_profile`:

```json
{
  "name": "Priya Mehta",
  "phone": "9845012367",
  "group_size": 10,
  "location": "Whitefield",
  "event_type": "Birthday Party",
  "age_group": "adults",
  "experience_level": "first_time",
  "intent": "birthday_party",
  "preferred_date": "2026-07-10",
  "sentiment_trend": "positive",
  "channel": "web_chat"
}
```

---

## 18. Session Management

### Session ID Rules

```
- 1–80 characters
- Allowed: A-Z, a-z, 0-9, underscore, dash, dot, colon
- Example: "user-abc-123", "session:uuid:v4"
```

### Web Chat

Generate a UUID per browser session:

```javascript
const sessionId = 'wc-' + crypto.randomUUID();
```

Store in `sessionStorage` (not `localStorage`) so each tab gets a fresh session.

### WhatsApp

Session IDs are derived automatically: `whatsapp:{phone_digits}`

### Reset a Session

```
POST /reset
Content-Type: application/json

{ "session_id": "user-abc-123" }
```

**Response:**
```json
{ "success": true }
```

### Inspect Session Memory

```
GET /memory/{session_id}
```

**Response:**
```json
{
  "session_id": "user-abc-123",
  "memory": {
    "customer_name": "Priya",
    "location": "Whitefield",
    "participants": 10,
    ...
  }
}
```

---

## 19. Error Handling

### HTTP Status Codes

| Code | Meaning |
|---|---|
| `200` | Success |
| `400` | Bad request (invalid session_id, empty message) |
| `401` | Missing or invalid bearer token (CRM API) |
| `403` | Insufficient role permissions |
| `404` | Resource not found |
| `422` | Request validation error (missing required fields) |
| `500` | Internal server error |

### 400 Example

```json
{
  "detail": "session_id may contain only letters, numbers, underscore, dash, dot, and colon."
}
```

### 422 Example

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "session_id"],
      "msg": "Field required",
      "input": {}
    }
  ]
}
```

---

## 20. Closiro CRM API Reference (Frontend Dashboard)

Base path: `/api/v1/`
All routes require: `Authorization: Bearer <jwt>`

### Contacts

| Method | Path | Description |
|---|---|---|
| `GET` | `/contacts` | List contacts (paginated) |
| `POST` | `/contacts` | Create contact |
| `GET` | `/contacts/{id}` | Get contact |
| `PATCH` | `/contacts/{id}` | Update contact |
| `GET` | `/contacts/{id}/leads` | Contact's leads |
| `GET` | `/contacts/{id}/calls` | Contact's call history |
| `GET` | `/contacts/{id}/bookings` | Contact's bookings |
| `GET` | `/contacts/{id}/activity` | Contact's activity log |
| `GET` | `/contacts/{id}/stats` | Contact's stats |
| `GET` | `/contacts/{id}/notes` | Notes on contact |
| `POST` | `/contacts/{id}/notes` | Add note |
| `PATCH` | `/contacts/{id}/notes/{note_id}` | Edit note |
| `DELETE` | `/contacts/{id}/notes/{note_id}` | Delete note |

### Leads

| Method | Path | Description |
|---|---|---|
| `GET` | `/leads` | List leads |
| `POST` | `/leads` | Create lead |
| `GET` | `/leads/{id}` | Get lead |
| `PATCH` | `/leads/{id}` | Update lead |
| `PATCH` | `/leads/{id}/stage` | Update lead pipeline stage |
| `GET` | `/leads/lost` | Lost leads (manager only) |
| `POST` | `/leads/lost` | Mark lead as lost |
| `DELETE` | `/leads/lost/{id}` | Remove lost lead record |

### Calls

| Method | Path | Description |
|---|---|---|
| `GET` | `/calls` | List calls |
| `GET` | `/calls/{id}` | Get call |
| `GET` | `/calls/{id}/summary` | Conversation summary |
| `GET` | `/calls/{id}/transcript` | Full transcript |
| `GET` | `/calls/{id}/transcript/live` | Live transcript |
| `GET` | `/calls/{id}/recording` | Recording URL |
| `POST` | `/calls/{id}/csat` | Submit CSAT rating |
| `GET` | `/calls/live` | Active calls (manager) |

### Bookings

| Method | Path | Description |
|---|---|---|
| `GET` | `/bookings` | List bookings |
| `POST` | `/bookings` | Create booking |
| `GET` | `/bookings/{id}` | Get booking |
| `PATCH` | `/bookings/{id}` | Update booking |

### Escalations

| Method | Path | Description |
|---|---|---|
| `GET` | `/escalations` | List escalations |
| `GET` | `/escalations/{id}` | Get escalation |
| `PATCH` | `/escalations/{id}` | Update escalation status |

### Pipeline

| Method | Path | Description |
|---|---|---|
| `GET` | `/pipeline/stages` | List pipeline stages |
| `POST` | `/pipeline/stages` | Create stage (manager) |
| `PATCH` | `/pipeline/stages/reorder` | Reorder stages (manager) |
| `PATCH` | `/pipeline/stages/{id}` | Update stage (manager) |
| `DELETE` | `/pipeline/stages/{id}` | Delete stage (manager) |

### Agents & Teams

| Method | Path | Description |
|---|---|---|
| `GET` | `/agents` | List agents (manager) |
| `POST` | `/agents` | Create agent (manager) |
| `GET` | `/agents/{id}` | Get agent |
| `PATCH` | `/agents/{id}` | Update agent (manager) |
| `GET` | `/agents/assignable` | Assignable agents list |
| `GET` | `/teams` | List teams (manager) |
| `POST` | `/teams` | Create team (manager) |
| `GET` | `/teams/{id}` | Get team (manager) |
| `PATCH` | `/teams/{id}` | Update team (manager) |

### Analytics

| Method | Path | Description |
|---|---|---|
| `GET` | `/analytics/dashboard` | Role-specific dashboard KPIs |
| `GET` | `/analytics/data` | Raw call data |
| `GET` | `/analytics/live` | Active calls count |
| `GET` | `/analytics/agent/{id}` | Per-agent metrics |
| `GET` | `/analytics/team/{id}` | Per-team metrics |
| `GET` | `/analytics/team-performance` | Team performance |
| `GET` | `/analytics/information` | Analytics metadata |

### Other

| Method | Path | Description |
|---|---|---|
| `GET` | `/me` | Current user info |
| `GET` | `/me/layout/{screen}` | Get saved UI layout |
| `PUT` | `/me/layout/{screen}` | Save UI layout |
| `GET` | `/conversations/{id}` | Get conversation |
| `POST` | `/conversations/{id}/messages` | Post message |
| `GET` | `/message-templates` | WhatsApp templates |
| `GET` | `/follow-ups` | List follow-ups |
| `POST` | `/follow-ups` | Create follow-up |
| `GET` | `/notifications` | User notifications |
| `PATCH` | `/notifications/{id}/read` | Mark notification read |
| `GET` | `/search` | Global search |
| `GET` | `/export/calls` | Export calls CSV |
| `GET` | `/export/contacts` | Export contacts CSV |
| `GET` | `/export/lost-leads` | Export lost leads CSV |

### Pagination

All list endpoints support:

```
?page=1&limit=25&sort=created_at&order=desc
```

| Param | Default | Description |
|---|---|---|
| `page` | 1 | Page number (1-indexed) |
| `limit` | 25 | Items per page (max 100) |
| `sort` | varies | Sort field |
| `order` | `desc` | `asc` or `desc` |

**Paginated response shape:**
```json
{
  "items": [ ... ],
  "page": 1,
  "limit": 25,
  "total": 142
}
```

---

## 21. Rendering Guidelines

### Response Text

Always display `response` as the primary agent message. Never modify or truncate.

### Agent State Indicator

Use `next_agent` to show the current conversation state:
- `inbound_agent` → "Inquiry" mode
- `booking_agent` → "Booking" mode (show booking progress UI)
- `escalation_agent` → "Escalation Required" (show red alert)

### Progressive Enhancement

Build the UI to gracefully handle empty objects. Fields like `booking`, `payment`, and `escalation` return `{}` when not active — do not assume they will always be populated.

### Real-time Updates

The backend does not use WebSockets. Poll or use the `transcript` field in each response to maintain a live-updating transcript display.

### Media Attachments

If `media` array is non-empty, render each item:

```json
[
  {
    "type": "image",
    "url": "https://example.com/room.jpg",
    "title": "Murder Mystery Room",
    "thumbnail_url": "",
    "mime_type": ""
  }
]
```

Media types: `image`, `video`, `document`, `link`

### Payment CTA Timing

Show the payment CTA immediately when `payment.payment_url` appears in the response. Do not wait for a follow-up turn. Use countdown to `payment.payment_deadline` for urgency.
