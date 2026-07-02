# Breakout Agent — System Architecture

**Version:** 1.1.0
**Updated:** 2026-07-01

---

## 1. Transport Layer

The backend accepts customer messages from three channels. All three funnel into the same `dispatch()` function.

```
┌─────────────────────────────────────────────────────────────────────┐
│                         TRANSPORT LAYER                              │
│                                                                      │
│  ┌───────────────┐    ┌─────────────────┐    ┌───────────────────┐  │
│  │   Web Chat    │    │    WhatsApp      │    │   Voice (Vapi)    │  │
│  │               │    │    (WATI)        │    │                   │  │
│  │  POST /chat   │    │ POST /webhooks/  │    │  POST /chat       │  │
│  │               │    │ wati             │    │  (webhook)        │  │
│  └───────┬───────┘    └────────┬─────────┘    └─────────┬─────────┘  │
│          │                     │                         │            │
│          └─────────────────────┴─────────────────────────┘            │
│                                │                                      │
│                         dispatch(message)                             │
└─────────────────────────────────────────────────────────────────────┘
```

### Transport Characteristics

| Channel | Endpoint | Session ID Format | Sync/Async |
|---|---|---|---|
| Web Chat | `POST /chat` | Client-generated UUID | Synchronous |
| WhatsApp | `POST /webhooks/wati` | `whatsapp:{digits}` | Synchronous; WATI delivers async |
| Voice (Vapi) | `POST /chat` | Vapi call ID | Synchronous per turn |
| Local CLI | stdin | `memory/session.json` | Interactive |

---

## 2. Core Processing Pipeline

```
Customer Message
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│                        dispatch()  [main.py]                      │
│                                                                    │
│  1. normalize_entity_aliases()                                    │
│     (Normalize city/room name variants)                           │
│                                                                    │
│  2. Payment Link Recovery Check                                   │
│     Is this a response about a pending payment?                   │
│     └─ Yes → route to BookingAgent directly                       │
│                                                                    │
│  3. Additional Booking Request Check                              │
│     Did customer request a NEW booking after one was completed?   │
│     └─ Yes → reset to InboundAgent                               │
│                                                                    │
│  4. SentimentAgent.analyze()                                      │
│     (Runs every turn, deterministic)                              │
│                                                                    │
│  5. ConversationGuard.evaluate()                                  │
│     Skip if: state-changing booking turn                          │
│     (Loop detection, spam, safety, off-topic)                     │
│     └─ Guard triggers → return guard response                     │
│                                                                    │
│  6. ConversationManager.determine_routing()                       │
│     (7 categories → 2 agents)                                     │
│     └─ Returns (target_agent, category)                           │
│                                                                    │
│  7. DEMO_MODE gate (optional)                                     │
│                                                                    │
│  8. Route to target agent                                         │
│     ├─ InboundAgent.handle_message()                             │
│     └─ BookingAgent.handle_message()                             │
│                                                                    │
│  9. _enrich_conversation_result()                                 │
│     ├─ EscalationAgent.evaluate()                                │
│     ├─ HandoffSummaryAgent.generate()  [if escalation]           │
│     └─ ConversationIntelligenceAgent.analyze()                   │
│                                                                    │
│ 10. _persist_chat_result()                                        │
│     (Update Closiro CRM store)                                    │
└──────────────────────────────────────────────────────────────────┘
       │
       ▼
  ChatResponse → Transport Layer
```

---

## 3. ConversationManager — Routing Logic

```
ConversationManager.determine_routing(message, active_agent)
       │
       ├─ Is this a positive response to a recommendation?
       │   └─ Yes → ("inbound_agent", "recommendation")
       │
       ├─ Is this an availability question?
       │   └─ Yes → ("booking_agent", "continuing_workflow")
       │
       ├─ Is this a bare booking field answer (while in booking mode)?
       │   └─ Yes → ("booking_agent", "continuing_workflow")
       │
       ├─ Is this an explicit booking change?
       │   └─ Yes → ("booking_agent", "continuing_workflow")
       │
       ├─ Is this a recommendation or rejection?
       │   └─ Yes → ("inbound_agent", "recommendation")
       │
       ├─ Is this a knowledge or comparison question?
       │   └─ Yes → ("inbound_agent", "faq")
       │
       ├─ Is this an exploration context?
       │   └─ Yes → ("inbound_agent", "recommendation")
       │
       └─ QuestionClassifier.classify() → (agent, category)
```

### Routing Preemption Priority

1. **Payment recovery** — Highest priority (handled before ConversationManager)
2. **Availability question** — Forces booking_agent regardless of active agent
3. **Bare booking field** — Keeps booking_agent while in booking workflow
4. **Explicit booking change** — Sends to booking_agent
5. **Recommendation/rejection** — Routes to inbound_agent
6. **Knowledge question** — Routes to inbound_agent for FAQ
7. **QuestionClassifier** — General-purpose fallback classifier

---

## 4. InboundAgent — Qualification & Recommendation

```
InboundAgent.handle_message(message)
       │
       ▼
IntentDetector.detect()
       │
       ▼
SlotFiller.extract()
  (Extract entities from message: name, phone, location,
   participants, age_group, date, event_type)
       │
       ▼
ConversationMemory.merge_message()
  (Update session state with extracted entities)
       │
       ▼
QualificationAgent.next_question()
  (Determine what slot to collect next)
       │
       ├─ All slots filled → handoff_ready() → route to BookingAgent
       │
       └─ Missing slots → ask for next slot
               │
               ▼
   Should we recommend first?
       │
       ├─ Yes → RecommendationEngine.recommend()
       │         │
       │         └─ Return top 2 options with rationale
       │
       └─ No → Ask qualification question
               │
               ▼
       ResponseComposer.compose()
       (Apply Breakout personality voice via OpenAI)
       (Deterministic fallback if OpenAI unavailable)
```

---

## 5. BookingAgent — Availability & Confirmation

```
BookingAgent.handle_message(message)
       │
       ▼
BookingOrchestrator.step()
       │
       ├─ AVAILABILITY STATE
       │     │
       │     ▼
       │   KreedaProvider.get_slots(location, game, date)
       │     │
       │     └─ Display available slots to customer
       │
       ├─ SLOT_SELECTION STATE
       │     │
       │     ▼
       │   Parse customer's slot choice
       │     │
       │     └─ Move to confirmation
       │
       ├─ CONFIRMATION STATE
       │     │
       │     ▼
       │   KreedaProvider.prepare_booking(payload)
       │     │
       │     ├─ Returns: booking_id, paymentUrl
       │     │
       │     └─ WatiClient.send_session_message(payment_url)
       │           (Send WhatsApp payment link)
       │
       └─ PAYMENT_PENDING STATE
             │
             ▼
           Wait for customer payment confirmation
           │
           └─ KreedaProvider confirms → BOOKING_CONFIRMED
```

### Booking State Values

| State | Description |
|---|---|
| `idle` | No booking in progress |
| `collecting_slots` | Asking for date/time/location |
| `displaying_availability` | Showing Kreeda slots |
| `awaiting_slot_selection` | Customer choosing a slot |
| `confirming` | Booking being created |
| `payment_pending` | Awaiting customer payment |
| `completed` | Booking confirmed |

---

## 6. Recommendation Engine

```
RecommendationEngine.recommend(memory_state)
       │
       ▼
  Load available_options() from knowledge base
       │
       ▼
  Filter by location (if specified)
       │
       ▼
  Apply age_group rules:
  ┌─────────────────────────────────────────────────────┐
  │  children → beginner rooms (difficulty 1-2)         │
  │  teens → moderate (difficulty 2-3)                  │
  │  adults → full range (difficulty 2-5)               │
  │  seniors → non-physical, investigation-based        │
  │  mixed → accessible, low intensity                  │
  └─────────────────────────────────────────────────────┘
       │
       ▼
  Apply experience_level rules:
  ┌─────────────────────────────────────────────────────┐
  │  first_time → beginner-friendly, clear narrative    │
  │  returning → moderate-to-hard                       │
  │  enthusiast → difficulty 4-5 only                  │
  └─────────────────────────────────────────────────────┘
       │
       ▼
  Apply event_type preferences:
  ┌─────────────────────────────────────────────────────┐
  │  birthday → themed rooms                            │
  │  corporate → team-challenge rooms                   │
  │  couple_event → intimate 2-player rooms             │
  └─────────────────────────────────────────────────────┘
       │
       ▼
  Score and rank
       │
       ▼
  Return top 2 with rationale
```

---

## 7. Escalation Flow

```
EscalationAgent.evaluate(message, sentiment, result)
       │
       ▼
  Check escalation triggers:
  ┌────────────────────────────────────────────────────────┐
  │  1. Safety keyword detection                           │
  │     (emergency, hurt, danger, threat)                  │
  │                                                        │
  │  2. Refund/complaint keywords                          │
  │     (refund, cancel, complaint, terrible)              │
  │                                                        │
  │  3. Human agent request                               │
  │     (speak to someone, human, manager, supervisor)     │
  │                                                        │
  │  4. SentimentAgent: frustrated + high confidence      │
  │                                                        │
  │  5. Repeated failure detection                        │
  │     (same question asked 3+ times)                    │
  └────────────────────────────────────────────────────────┘
       │
       ├─ No triggers → escalate: false
       │
       └─ Trigger found → escalate: true
               │
               ▼
       HandoffSummaryAgent.generate()
       (Create structured handoff for human agent)
               │
               ▼
       Set next_agent = "escalation_agent"
       Override response with appropriate message
```

---

## 8. Conversation Intelligence Pipeline

```
ConversationIntelligenceAgent.analyze(memory, escalation)
       │
       ├─ Compile transcript from conversation history
       │
       ├─ Build customer profile from memory fields
       │
       ├─ EvaluationAgent.evaluate()
       │   (Compute qualification score, recommendation quality, CSAT)
       │
       ├─ FollowUpAgent.recommend()
       │   (Generate next-action recommendations)
       │
       ├─ Build timeline_events from conversation stages
       │
       ├─ Compute sentiment trajectory from history
       │
       └─ Return full intelligence dict
```

---

## 9. Booking State Flow (Detailed)

```
                    ┌─────────────────────────┐
                    │      INQUIRY STATE       │
                    │  (InboundAgent active)   │
                    └────────────┬────────────┘
                                 │
                    Customer accepts recommendation
                                 │
                    ┌────────────▼────────────┐
                    │   QUALIFICATION STATE    │
                    │  Collect missing slots:  │
                    │  - name                  │
                    │  - phone                 │
                    │  - location              │
                    │  - participants          │
                    │  - date                  │
                    └────────────┬────────────┘
                                 │
                       All slots filled
                                 │
                    ┌────────────▼────────────┐
                    │  AVAILABILITY CHECK      │
                    │  Kreeda: get_slots()     │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   SLOT SELECTION         │
                    │  Customer picks time     │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   BOOKING CREATION       │
                    │  Kreeda: prepare_booking │
                    │  → booking_id            │
                    │  → paymentUrl            │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   PAYMENT DELIVERY       │
                    │  WATI: send payment URL  │
                    │  via WhatsApp            │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   PAYMENT PENDING        │
                    │  Waiting for customer    │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   BOOKING CONFIRMED      │
                    │  Kreeda confirms payment │
                    └─────────────────────────┘
```

---

## 10. Payment Lifecycle

```
┌─────────────────────────────────────────────────────┐
│                  PAYMENT LIFECYCLE                   │
│                                                      │
│  1. BookingAgent creates booking via Kreeda API      │
│     POST /api/prepare-booking                        │
│     └─ Returns: { booking_id, paymentUrl }           │
│                                                      │
│  2. Backend extracts paymentUrl                      │
│     _extract_payment_payload(result)                 │
│                                                      │
│  3. WatiClient sends WhatsApp message                │
│     "Here is your payment link: {paymentUrl}"        │
│                                                      │
│  4. ChatResponse.payment populated:                  │
│     {                                                │
│       "status": "PAYMENT_PENDING",                   │
│       "payment_url": "https://bs.kreeda.icu/...",    │
│       "payment_deadline": "...",                     │
│       "booking_status": "BOOKED"                     │
│     }                                                │
│                                                      │
│  5. Customer completes payment on Kreeda page        │
│     (External flow — Kreeda handles checkout)        │
│                                                      │
│  6. Kreeda sends confirmation                        │
│     (Via Kreeda's own webhook — external system)     │
│                                                      │
│  7. On next customer message, BookingAgent           │
│     detects payment confirmation keywords            │
│     → Updates booking status to CONFIRMED            │
└─────────────────────────────────────────────────────┘
```

---

## 11. Memory System

```
ConversationMemory  [src/memory/conversation_memory.py]
       │
       ├─ Storage: JSON file per session
       │   memory/api_sessions/{session_id}.json
       │
       ├─ Key operations:
       │   ├─ merge_message() — NER + slot extraction
       │   ├─ set_field()     — typed field assignment
       │   ├─ as_state()      — serialize for API
       │   ├─ save()          — persist to disk
       │   ├─ reset()         — clear session
       │   └─ handoff_ready() — check qualification complete
       │
       └─ Entity extraction:
           ├─ _extract_name()
           ├─ _extract_phone()
           ├─ _extract_location()
           ├─ _extract_participants()
           ├─ _extract_date()
           └─ _extract_age_group()
```

---

## 12. Closiro CRM Integration

```
app.py: _persist_chat_result()
       │
       ├─ _find_or_create_contact()
       │   Match by phone → update
       │   Not found → create new contact
       │
       ├─ Update or create Call record
       │   ├─ transcript
       │   ├─ summary
       │   ├─ sentiment
       │   └─ intent
       │
       ├─ If booking completed → create Booking record
       │
       └─ If escalation triggered → create Escalation record
```

---

## 13. External Dependencies Map

```
Breakout Agent Backend
       │
       ├─ OpenAI API (optional)
       │   Response composition (GPT-4.1 mini)
       │   Graceful fallback if unavailable
       │
       ├─ Kreeda API (bs.kreeda.icu)
       │   GET /api/locations
       │   GET /api/games
       │   GET /api/slots
       │   POST /api/prepare-booking
       │
       ├─ WATI API
       │   POST /api/{version}/sendSessionMessage/{phone}
       │   POST /api/v2/sendTemplateMessage
       │
       ├─ Vapi (voice channel)
       │   Receives ASR text via POST /chat
       │   Backend returns text, Vapi handles TTS
       │
       └─ Closiro CRM
           In-memory store (ClosiraStore in app.py)
           Exposed via /api/v1/ routes
           (Connect to production DB when ready)
```
