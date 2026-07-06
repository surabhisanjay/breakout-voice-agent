# Breakout Agent — Production Handover Document

**Version:** 1.1.0
**Date:** 2026-07-01
**Status:** Production Ready
**Prepared for:** Frontend Engineering Team / External Handover

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Conversation Flow](#3-conversation-flow)
4. [dispatch() — Central Router](#4-dispatch--central-router)
5. [Recommendation Engine](#5-recommendation-engine)
6. [Booking Flow](#6-booking-flow)
7. [Payment Lifecycle](#7-payment-lifecycle)
8. [Memory System](#8-memory-system)
9. [Escalation](#9-escalation)
10. [Sentiment Agent](#10-sentiment-agent)
11. [Evaluation Agent](#11-evaluation-agent)
12. [Follow-up Agent](#12-follow-up-agent)
13. [Conversation Intelligence](#13-conversation-intelligence)
14. [Web Chat](#14-web-chat)
15. [Voice (Vapi)](#15-voice-vapi)
16. [WhatsApp (WATI)](#16-whatsapp-wati)
17. [Kreeda Integration](#17-kreeda-integration)
18. [Closiro CRM Integration](#18-closiro-crm-integration)
19. [Analytics](#19-analytics)
20. [Testing Summary](#20-testing-summary)
21. [Production Readiness](#21-production-readiness)
22. [Known External Dependencies](#22-known-external-dependencies)
23. [Deployment Checklist](#23-deployment-checklist)
24. [Remaining Product Decisions](#24-remaining-product-decisions)

---

## 1. Project Overview

The **Breakout Agent** is an AI-powered inbound sales and booking assistant for Breakout Escape Rooms. It handles multi-channel customer conversations (web chat, WhatsApp, voice) across the full customer journey: inquiry → recommendation → qualification → booking → payment.

### What the Agent Does

- Greets customers and detects intent (escape room, birthday, corporate, etc.)
- Answers FAQ questions from a structured knowledge base
- Recommends suitable escape rooms or packages based on group profile
- Qualifies leads (collects name, phone, location, group size, date, event type)
- Manages the full booking flow against the Kreeda API
- Processes payment via Kreeda checkout links sent over WhatsApp (WATI)
- Escalates to human staff when required
- Records full conversation intelligence for the Closiro CRM

### What the Agent Does NOT Do

- Approve refunds autonomously
- Confirm pricing changes
- Override Kreeda availability decisions
- Make business decisions outside the defined scope

### Stack

| Component | Technology |
|---|---|
| API Framework | FastAPI |
| AI / LLM | OpenAI GPT-4.1 mini (response composition) |
| Voice STT | Vapi (webhook-based) / Whisper local |
| Voice TTS | Vapi / pyttsx3 local |
| WhatsApp | WATI |
| Booking Backend | Kreeda API (`bs.kreeda.icu`) |
| CRM | Closiro (embedded in `app.py`) |
| Memory | File-based JSON (`memory/api_sessions/`) |
| Conversation State | In-process Python dict + JSON persistence |

---

## 2. Architecture

### Component Map

```
┌─────────────────────────────────────────────────┐
│                 Transport Layer                  │
│  ┌──────────┐  ┌───────────┐  ┌──────────────┐  │
│  │ Web Chat │  │ WhatsApp  │  │  Voice/Vapi  │  │
│  │ /chat    │  │ /webhooks │  │  webhook     │  │
│  └────┬─────┘  └─────┬─────┘  └──────┬───────┘  │
└───────┼──────────────┼───────────────┼───────────┘
        │              │               │
        └──────────────┴───────────────┘
                       │
                  app.py dispatch()
                       │
        ┌──────────────┴───────────────┐
        │                              │
  ConversationGuard              ConversationManager
  (loop/spam/safety)             (routing decision)
        │                              │
        └──────────────┬───────────────┘
                       │
           ┌───────────┴───────────┐
           │                       │
      InboundAgent            BookingAgent
    (recommendation,          (slot selection,
     qualification,            confirmation,
     FAQ, intake)              payment link)
           │                       │
           └───────────┬───────────┘
                       │
              ┌────────┴─────────┐
              │                  │
       SentimentAgent      EscalationAgent
              │                  │
              └────────┬─────────┘
                       │
          ConversationIntelligenceAgent
                       │
               ┌───────┴────────┐
               │                │
        EvaluationAgent    FollowUpAgent
               │
         Closiro CRM Store
```

### File Map

| Concern | File |
|---|---|
| HTTP API + CRM | `app.py` |
| CLI + dispatch() | `main.py` |
| Inbound Sales Agent | `src/agents/inbound_agent.py` |
| Booking Agent | `src/agents/booking_agent.py` |
| Escalation Agent | `src/agents/escalation_agent.py` |
| Sentiment Agent | `src/agents/sentiment_agent.py` |
| Evaluation Agent | `src/agents/evaluation_agent.py` |
| Follow-up Agent | `src/agents/follow_up_agent.py` |
| Conversation Intelligence | `src/agents/conversation_intelligence_agent.py` |
| Handoff Summary | `src/agents/handoff_summary_agent.py` |
| Conversation Manager (routing) | `src/orchestration/conversation_manager.py` |
| Booking Orchestrator | `src/orchestration/booking_orchestrator.py` |
| Conversation Guard | `src/services/conversation_guard.py` |
| Recommendation Engine | `src/services/recommendation_engine.py` |
| Intent Detector | `src/services/intent_detector.py` |
| Slot Filler | `src/services/slot_filler.py` |
| Conversation Memory | `src/memory/conversation_memory.py` |
| Session Manager | `src/memory/session_manager.py` |
| Kreeda API Client | `src/integrations/kreeda/breakout_api.py` |
| WATI WhatsApp Client | `src/services/wati_client.py` |
| LangGraph Booking Node | `src/integrations/langgraph/booking_node.py` |
| Knowledge Loader | `src/knowledge/knowledge_loader.py` |
| Response Composer | `src/response_composer.py` |

---

## 3. Conversation Flow

### High-Level Flow

```
Customer Message
      │
      ▼
normalize_entity_aliases()        ← alias normalization
      │
      ▼
Payment link recovery check?      ← if booking is in-flight
      │
      ▼
Additional booking request?       ← new booking after completed one
      │
      ▼
SentimentAgent.analyze()          ← run in parallel
      │
      ▼
ConversationGuard.evaluate()      ← loop/spam/abuse detection
      │ (if not state-changing booking turn)
      ▼
ConversationManager.determine_routing()
      │
      ├─ "inbound_agent" → InboundAgent.handle_message()
      │       ├─ FAQ
      │       ├─ Recommendation
      │       └─ Qualification (slot filling)
      │
      └─ "booking_agent" → BookingAgent.handle_message()
              ├─ Availability check (Kreeda)
              ├─ Slot selection
              ├─ Booking creation (Kreeda)
              └─ Payment link (WATI)
      │
      ▼
_enrich_conversation_result()
      ├─ EscalationAgent.evaluate()
      ├─ HandoffSummaryAgent.generate()  (if escalation)
      └─ ConversationIntelligenceAgent.analyze()
      │
      ▼
_persist_chat_result()            ← Closiro CRM update
      │
      ▼
ChatResponse returned to transport
```

### Intent Categories

| Intent | Handler |
|---|---|
| `escape_room_inquiry` | InboundAgent → BookingAgent |
| `birthday_party` | InboundAgent → BookingAgent |
| `bachelor_party` | InboundAgent → BookingAgent |
| `farewell_party` | InboundAgent → BookingAgent |
| `couple_event` | InboundAgent → BookingAgent |
| `corporate_event` | InboundAgent → BookingAgent |
| `virtual_event` | InboundAgent → BookingAgent |
| `cancellation_request` | InboundAgent → EscalationAgent |
| `general_faq` | InboundAgent (stays) |

### Routing Categories (ConversationManager)

| Category | Meaning |
|---|---|
| `continuing_workflow` | Stay on current agent |
| `faq` | Route to InboundAgent for FAQ |
| `recommendation` | Route to InboundAgent for recommendation |
| `new_booking` | Route to BookingAgent for new booking |
| `new_corporate` | Reset to corporate inquiry |
| `new_birthday` | Reset to birthday inquiry |
| `new_escape_room` | Reset to escape room inquiry |

---

## 4. dispatch() — Central Router

**Location:** `main.py:316`

`dispatch()` is the single entry point for all messages in all sessions. It is called by:
- `app.py /chat` endpoint
- `app.py /webhooks/wati` endpoint
- `main.py` CLI text and voice loops

**Signature:**
```python
def dispatch(
    message: str,
    inbound: InboundAgent,
    booking: BookingAgent | None,
    active_agent: str,
) -> tuple[AgentResponse, BookingAgent | None, str]:
```

**Returns:** `(agent_response, booking_agent_instance, active_agent_name)`

### dispatch() Decision Tree

1. Normalize entity aliases (city names, room names)
2. If payment link recovery turn → route to BookingAgent
3. If additional booking request after completed booking → reset to InboundAgent
4. Run SentimentAgent in parallel
5. If NOT a state-changing booking turn → run ConversationGuard
6. Run ConversationManager.determine_routing()
7. If DEMO_MODE and premature booking → hold in InboundAgent
8. Route to selected agent
9. Enrich result (escalation, intelligence, timeline)
10. Return result

---

## 5. Recommendation Engine

**Location:** `src/services/recommendation_engine.py`

The recommendation engine is fully deterministic. It does not call OpenAI.

### Inputs

- `group_size` (participants)
- `age_group` (`children`, `teens`, `adults`, `mixed`, `seniors`)
- `experience_level` (`first_time`, `returning`, `enthusiast`)
- `event_type` (birthday, corporate, escape_room_inquiry, etc.)
- `location`

### Logic

1. Filter available rooms by location
2. Apply age group rules (e.g., children → beginner-friendly rooms)
3. Apply experience level filters (e.g., enthusiast → difficulty 4-5)
4. Apply event type preferences (e.g., corporate → team rooms)
5. Return top 2 options with rationale

### Output

```python
{
    "option": "Murder Mystery",
    "rationale": "Investigation-based, suitable for first-timers",
    "alternatives": ["Hostage"],
    "confidence": 0.9
}
```

---

## 6. Booking Flow

### State Machine

```
[inquiry]
    │
    ▼
[recommendation_made]  ← InboundAgent recommends room
    │
    ▼
[room_selected]        ← Customer accepts recommendation
    │
    ▼
[slots_displayed]      ← BookingAgent fetches availability from Kreeda
    │
    ▼
[slot_confirmed]       ← Customer selects a slot
    │
    ▼
[booking_created]      ← Kreeda returns booking_id + payment_url
    │
    ▼
[payment_link_sent]    ← WATI sends WhatsApp message with payment link
    │
    ▼
[payment_pending]      ← Waiting for customer payment
    │
    ▼
[booking_confirmed]    ← Kreeda confirms payment received
```

### Required Slot Fields (before booking)

| Field | Description |
|---|---|
| `customer_name` | Customer's full name |
| `phone` | 10-digit Indian mobile number |
| `location` | Branch (Whitefield / Koramangala / JP Nagar) |
| `participants` | Group size (integer) |
| `age_group` | `children` / `teens` / `adults` / `mixed` |
| `preferred_date` | Date string (YYYY-MM-DD or natural language) |
| `selected_slot` | Time slot selected from Kreeda availability |
| `room` | Room/game name |

### Booking API Call Sequence

1. `GET /api/locations` → get location IDs
2. `GET /api/games?location={id}` → get available rooms
3. `GET /api/slots?game={id}&date={date}` → get available times
4. `POST /api/prepare-booking` → create booking, receive `booking_id` and `paymentUrl`

### BookingAgent Provider Abstraction

```python
class BookingProvider(Protocol):
    def get_locations(self) -> list[dict]
    def get_games(self, location_id: str) -> list[dict]
    def get_slots(self, game_id: str, date: str) -> list[dict]
    def prepare_booking(self, payload: dict) -> dict
```

- `BreakoutAPIProvider` — live Kreeda integration
- `SimulatorProvider` — offline simulator for testing

Auto-selection: If `BOOKING_API_KEY` and `BOOKING_BASE_URL` are set → live provider. Otherwise → simulator.

---

## 7. Payment Lifecycle

### Flow

1. BookingAgent calls `kreeda.prepare_booking()`
2. Kreeda returns `{ "booking_id": "...", "paymentUrl": "https://..." }`
3. BookingAgent passes `paymentUrl` to `app.py`
4. `app.py` extracts `payment_url` via `_extract_payment_payload()`
5. WatiClient sends payment link to customer's WhatsApp number
6. Customer completes payment on Kreeda checkout page
7. Kreeda webhook (external, not in this backend) confirms payment
8. BookingAgent detects payment confirmation on next customer message

### Payment Object Returned to Frontend

```json
{
  "status": "PAYMENT_PENDING",
  "payment_url": "https://bs.kreeda.icu/checkout/...",
  "payment_deadline": "2026-07-02T18:00:00+05:30",
  "booking_status": "BOOKED"
}
```

### Payment Status Values

| Status | Meaning |
|---|---|
| `PAYMENT_PENDING` | Link sent, awaiting payment |
| `PAID` | Payment confirmed by Kreeda |
| `UNPAID` | Not yet initiated |
| `FAILED` | Payment attempt failed |

---

## 8. Memory System

**Location:** `src/memory/conversation_memory.py`

Memory is file-based JSON, one file per session, stored under `memory/api_sessions/{session_id}.json`.

### Key Memory Fields

| Field | Type | Description |
|---|---|---|
| `customer_name` | str | Customer's name |
| `phone` | str | Customer's phone number |
| `location` | str | Preferred branch |
| `participants` | int | Group size |
| `age_group` | str | Age group classification |
| `experience_level` | str | Booking experience level |
| `intent` | str | Detected intent category |
| `event_type` | str | Human-readable event type |
| `preferred_date` | str | Requested date |
| `selected_slot` | str | Confirmed time slot |
| `room` | str | Selected room/game |
| `recommended_option` | str | Last recommendation made |
| `booking_id` | str | Kreeda booking ID |
| `booking_ref` | str | Kreeda booking reference |
| `payment_url` | str | Kreeda payment link |
| `booking_status` | str | Current booking status |
| `sentiment` | str | Last sentiment reading |
| `conversation` | list | Full conversation history |
| `current_workflow` | str | `general` / `booking` |
| `booking_started` | bool | Booking flow active |
| `channel` | str | `web_chat` / `whatsapp` / `voice` |

### Memory Operations

```python
memory.data           # dict — raw memory state
memory.as_state()     # serialized dict for API response
memory.save()         # persist to disk
memory.reset()        # clear session
memory.merge_message()  # extract entities from message
memory.set_field()    # set a field with provenance tracking
memory.handoff_ready()  # check if all required fields filled
```

---

## 9. Escalation

**Location:** `src/agents/escalation_agent.py`

EscalationAgent runs on every turn after the primary agent responds. It evaluates whether to escalate to human staff.

### Escalation Triggers

| Trigger | Condition |
|---|---|
| Safety concern | Medical emergency, violence, security threat |
| Refund request | Customer explicitly requests refund |
| Human representative | Customer requests human agent |
| Anger / Frustration | Detected high-frustration sentiment |
| Misunderstanding loop | Repeated clarification failures |
| Technical failure | System error detected |

### Escalation Object

```json
{
  "escalate": true,
  "reason": "Customer requested human representative",
  "summary": "Customer was frustrated with payment link not arriving and requested to speak to a human agent.",
  "urgency": "medium",
  "recommended_action": "Call customer within 30 minutes",
  "sentiment": { "sentiment": "frustrated", "confidence": 0.87 }
}
```

### Escalation Handoff Summary

If escalation is triggered, `HandoffSummaryAgent` generates a structured summary including:
- All collected customer data
- Full escalation context
- Recommended action items
- Sentiment timeline

---

## 10. Sentiment Agent

**Location:** `src/agents/sentiment_agent.py`

Runs deterministically on every message. No OpenAI call.

### Sentiment Values

`positive` | `neutral` | `negative` | `frustrated` | `excited` | `confused`

### SentimentResult Object

```python
@dataclass
class SentimentResult:
    sentiment: str
    confidence: float        # 0.0 - 1.0
    escalation_recommended: bool
    reason: str
    stage: str               # conversation stage context
```

### Sentiment Timeline

Accumulated in `ConversationIntelligenceAgent` as a series of per-turn readings stored in `timeline_events`.

---

## 11. Evaluation Agent

**Location:** `src/agents/evaluation_agent.py`

EvaluationAgent generates a post-conversation quality score. Called by `ConversationIntelligenceAgent`.

### Evaluation Metrics

| Metric | Description |
|---|---|
| `qualification_score` | How completely lead was qualified (0-10) |
| `recommendation_quality` | Relevance of recommendation (0-10) |
| `booking_completion` | Whether booking was completed |
| `escalation_required` | Whether escalation occurred |
| `csat_estimate` | Estimated customer satisfaction (1-5) |
| `agent_performance` | Overall agent performance score |

### Evaluation Object (sample)

```json
{
  "qualification_score": 8.5,
  "recommendation_quality": 9.0,
  "booking_completion": true,
  "escalation_required": false,
  "csat_estimate": 4.2,
  "agent_performance": "excellent",
  "summary": "Customer qualified successfully, booking completed, payment link delivered."
}
```

---

## 12. Follow-up Agent

**Location:** `src/agents/follow_up_agent.py`

Generates follow-up action recommendations after a conversation. Output is included in every `ChatResponse` as `follow_up_recommendations`.

### Follow-up Types

- Payment reminder (if payment pending)
- Confirmation call (if booking made)
- Re-engagement (if conversation ended without booking)
- Escalation follow-up (if human handoff required)
- Post-experience survey prompt

### Follow-up Object (sample)

```json
{
  "follow_up_recommendations": [
    "Send payment reminder via WhatsApp within 2 hours.",
    "Call customer to confirm booking details.",
    "Offer group discount if party size exceeds 8."
  ]
}
```

---

## 13. Conversation Intelligence

**Location:** `src/agents/conversation_intelligence_agent.py`

Aggregates all per-turn analysis into a full conversation report. Called once per turn, after the primary agent responds.

### Intelligence Object Structure

```json
{
  "ai_summary": {
    "summary": "Customer inquired about birthday party for 10 adults at Whitefield...",
    "intent": "birthday_party",
    "outcome": "booking_completed",
    "action_items": ["Send payment reminder", "Confirm venue details"]
  },
  "customer_profile": {
    "name": "Priya Mehta",
    "phone": "9845012367",
    "group_size": 10,
    "location": "Whitefield",
    "event_type": "Birthday Party",
    "experience_level": "first_time",
    "sentiment_trend": "positive"
  },
  "sentiment_analysis": {
    "overall": "positive",
    "confidence": 0.88,
    "trajectory": "improving"
  },
  "timeline_events": [
    { "sequence": 1, "event": "inquiry", "description": "Customer inquired about birthday options" },
    { "sequence": 2, "event": "recommendation", "description": "Agent recommended Murder Mystery" },
    { "sequence": 3, "event": "booking_started", "description": "Customer accepted recommendation" },
    { "sequence": 4, "event": "booking_completed", "description": "Booking confirmed, payment link sent" }
  ],
  "transcript": [
    { "sequence": 1, "speaker_type": "customer", "text": "Hi, I want to book a birthday party.", "spoken_at_second": 0.0 },
    { "sequence": 2, "speaker_type": "agent", "text": "Happy to help! ...", "spoken_at_second": 3.0 }
  ],
  "follow_up_recommendations": ["Send payment reminder", "Confirm booking details"],
  "recording": { "url": "", "available": false }
}
```

---

## 14. Web Chat

**Location:** `web_chat/` (static frontend), `app.py /chat` (backend)

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serve web chat UI |
| `GET` | `/web-chat` | Serve web chat UI |
| `POST` | `/chat` | Send a message, get agent response |
| `POST` | `/reset` | Reset a session |
| `GET` | `/memory/{session_id}` | Inspect session memory |
| `GET` | `/intelligence/{session_id}` | Get conversation intelligence |

### Web Chat Frontend

The frontend (`web_chat/`) is a vanilla HTML/JS/CSS single-page app. It communicates with the `/chat` endpoint using `fetch`.

### Session Management

Each browser session should generate a unique `session_id` (e.g., UUID). Sessions persist until `/reset` is called.

---

## 15. Voice (Vapi)

Vapi integration handles the voice channel. Vapi sends ASR-transcribed text to the backend as HTTP webhook calls.

### Vapi Webhook Flow

```
Customer speaks
      │
  Vapi ASR (transcription)
      │
  POST /chat  ← Vapi sends text to backend
      │
  Agent responds (text)
      │
  Vapi TTS (speaks response)
      │
  Customer hears response
```

### Local Voice Mode

```bash
python main.py --voice
```

Uses Whisper (local STT) + pyttsx3 (local TTS). For development only.

---

## 16. WhatsApp (WATI)

**Location:** `src/services/wati_client.py`, `app.py /webhooks/wati`

WATI is the WhatsApp Business API provider. Inbound messages arrive as webhooks; outbound messages are sent via the WATI REST API.

### Webhook Endpoint

```
POST /webhooks/wati
POST /api/v1/whatsapp/wati/webhook
```

Both paths are equivalent. Configure WATI to send webhooks to either path.

### Message Handling

1. WATI sends webhook with sender phone and message text
2. Backend extracts `phone` and `text` from payload
3. Session ID is derived as `whatsapp:{phone_digits}`
4. Message is dispatched through the same `dispatch()` function
5. Response is sent back via `WatiClient.send_session_message()`

### Payment Link Delivery

When a booking is created, the backend sends a WhatsApp message with the payment link:

```
Here is your payment link to confirm your booking:
https://bs.kreeda.icu/checkout/...
Please complete payment within 2 hours.
```

### WATI Environment Variables

```bash
WATI_BASE_URL=               # WATI API base URL
WATI_ACCESS_TOKEN=           # WATI bearer token
API_VERSION=v1
WATI_TIMEOUT_SECONDS=10
WATI_MAX_ATTEMPTS=3
WATI_SEND_PATH=/api/{api_version}/sendSessionMessage/{phone}
WATI_TEMPLATE_ID=            # Template ID for payment messages
WATI_TEMPLATE_PATH=/api/v2/sendTemplateMessage
WATI_BROADCAST_NAME=booking_payment
WATI_SENDER_NUMBER=          # Sender WhatsApp number
```

---

## 17. Kreeda Integration

**Location:** `src/integrations/kreeda/`

Kreeda is the escape room booking management system. The Breakout Agent integrates with Kreeda for availability and booking.

### API Endpoints Used

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/locations` | List available branches |
| `GET` | `/api/games` | List games/rooms by location |
| `GET` | `/api/slots` | Get available time slots |
| `POST` | `/api/prepare-booking` | Create a booking and get payment URL |

### Provider Selection

```bash
BOOKING_PROVIDER=auto       # auto-detect (live if credentials present)
BOOKING_PROVIDER=simulator  # force offline simulator
```

### Kreeda Environment Variables

```bash
BOOKING_BASE_URL=https://bs.kreeda.icu
BOOKING_API_KEY=            # Kreeda API key
BOOKING_PROVIDER=auto
```

---

## 18. Closiro CRM Integration

**Location:** `app.py` (`ClosiraStore` class, `/api/v1/` routes)

Closiro is the CRM frontend that the Breakout sales team uses to view conversations, bookings, leads, and escalations.

### Data Persisted Per Session

- **Contact**: Created/updated on first message
- **Call**: One record per session, updated on every turn
- **Transcript**: Full conversation transcript
- **Booking**: Created when Kreeda booking completes
- **Escalation**: Created when EscalationAgent triggers

### Authentication

All `/api/v1/` routes require a Bearer JWT token. The JWT must contain:
- `org_id` (string)
- `role` (`sales_agent` / `sales_manager` / `admin`)
- `sub` or `user_id` (integer)
- `agent_id` (integer, optional)

---

## 19. Analytics

**Location:** `app.py /api/v1/analytics/`

| Endpoint | Audience | Description |
|---|---|---|
| `GET /api/v1/analytics/dashboard` | Agent + Manager | Role-specific KPI dashboard |
| `GET /api/v1/analytics/data` | Manager | Raw call data by tab |
| `GET /api/v1/analytics/live` | Manager | Active call count |
| `GET /api/v1/analytics/agent/{id}` | Agent + Manager | Per-agent metrics |
| `GET /api/v1/analytics/team/{id}` | Manager | Per-team metrics |
| `GET /api/v1/analytics/team-performance` | Manager | Team performance summary |

---

## 20. Testing Summary

### Test Coverage

The test suite contains **45 test files** covering:

- Inbound agent conversation scenarios
- Booking flow (unit and integration)
- Escalation agent
- Sentiment agent
- Conversation intelligence
- Recommendation engine hardening
- WATI integration
- Web chat transport
- Closira API contract
- Red team / adversarial scenarios
- Golden demo regressions
- Production hotfix validations

### Running Tests

```bash
python -m pytest tests -q
```

### Test Categories

| Category | Files |
|---|---|
| Core Agent | `test_inbound_agent.py`, `test_booking_agent.py` |
| Booking Flow | `test_booking_orchestration.py`, `test_langgraph_booking_node.py` |
| Intelligence | `test_conversation_intelligence_agents.py`, `test_call_intelligence_contract.py` |
| Regression | `test_final_release_regressions.py`, `test_golden_demo_regressions.py` |
| Red Team | `test_red_team_execution.py`, `test_final_backend_red_team_validation.py` |
| Integration | `test_wati_integration.py`, `test_web_chat_transport.py`, `test_closira_api_contract.py` |
| Quality | `test_p1_conversation_quality.py`, `test_conversation_humanization.py` |
| Hardening | `test_hardening_pass.py`, `test_final_production_stabilization.py` |

---

## 21. Production Readiness

### Confirmed Working

| Feature | Status |
|---|---|
| Web chat conversation | ✅ Production |
| WhatsApp via WATI | ✅ Production |
| Kreeda live booking | ✅ Production |
| Payment link delivery | ✅ Production |
| Escalation detection | ✅ Production |
| Sentiment analysis | ✅ Production |
| Conversation intelligence | ✅ Production |
| Closiro CRM API | ✅ Production |
| OpenAI fallback | ✅ Production |
| Session isolation | ✅ Production |

### Known Limitations

| Limitation | Impact | Mitigation |
|---|---|---|
| In-memory CRM store | Data lost on restart | Use Closiro's production database when available |
| No database persistence | Sessions file-based | Acceptable for MVP; add PostgreSQL for scale |
| No Redis | No distributed session support | Add Redis for multi-instance deployment |
| Voice via Vapi webhook | Requires ngrok / public URL for local dev | Use VPS or tunnel for voice testing |
| Kreeda API rate limits | Unknown | Monitor and add retry backoff |

---

## 22. Known External Dependencies

| Dependency | Purpose | Required |
|---|---|---|
| OpenAI API | Response composition (personality voice) | Optional (graceful fallback) |
| Kreeda API | Availability + booking creation | Required for live bookings |
| WATI API | WhatsApp message delivery + payment links | Required for WhatsApp channel |
| Vapi | Voice STT/TTS | Required for voice channel |
| Closiro | CRM frontend | Required for sales team dashboard |

---

## 23. Deployment Checklist

- [ ] Set `OPENAI_API_KEY`
- [ ] Set `BOOKING_BASE_URL=https://bs.kreeda.icu`
- [ ] Set `BOOKING_API_KEY`
- [ ] Set `BOOKING_PROVIDER=auto`
- [ ] Set all WATI credentials
- [ ] Configure WATI webhook URL: `https://your-domain.com/webhooks/wati`
- [ ] Configure Vapi webhook URL: `https://your-domain.com/chat`
- [ ] Set `CLOSIRO_DEFAULT_ORG_ID`
- [ ] Set `CLOSIRO_DEFAULT_AGENT_ID`
- [ ] Create `memory/api_sessions/` directory (writable)
- [ ] Create `logs/conversations/` directory (writable)
- [ ] Run `python -m pytest tests -q` and verify all pass
- [ ] Start server: `uvicorn app:app --host 0.0.0.0 --port 8000`
- [ ] Verify: `curl http://your-domain.com/health`
- [ ] Perform end-to-end test booking through web chat
- [ ] Verify Kreeda booking appears in Kreeda dashboard
- [ ] Verify WATI payment link delivered on WhatsApp

---

## 24. Remaining Product Decisions

| Decision | Owner | Description |
|---|---|---|
| PostgreSQL migration | Engineering | Replace file-based memory with database |
| Redis session store | Engineering | Enable horizontal scaling |
| Closiro production DB | Closiro team | Replace in-memory store with real DB |
| Vapi production config | Product | Finalize voice persona and language settings |
| WhatsApp number verification | Ops | Complete WATI business verification |
| Kreeda webhook (payment confirmation) | Kreeda team | Implement payment confirmation webhook |
| Multi-location pricing | Product | Add branch-specific pricing to knowledge base |
| CSAT survey integration | Product | Decide on post-call survey mechanism |
| Analytics persistence | Engineering | Add time-series analytics storage |
| Human handoff SLA | Ops | Define SLA for escalation response times |
