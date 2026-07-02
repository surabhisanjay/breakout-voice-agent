# Breakout Agent — Conversation Flow

**Updated:** 2026-07-01

This document describes the conversation flow in detail, covering intent detection, routing, qualification, recommendation, and booking.

---

## 1. End-to-End Conversation Flow

```
Customer Message
      │
      ▼
normalize_entity_aliases()
  ↳ "whitefield" → "Whitefield"
  ↳ "murder" → "Murder Mystery"
      │
      ▼
─────────────────────────── PRE-CHECKS ───────────────────────────
      │
      ├─ Is this about an ongoing payment?
      │   (Customer says "I paid", "payment done", "link expired")
      │   └─ Yes → BookingAgent handles payment recovery
      │
      ├─ Is this a NEW booking request after a completed one?
      │   (Customer says "book another", "one more room")
      │   └─ Yes → Reset to InboundAgent for fresh intake
      │
      └─ Continue ──────────────────────────────────────────────────
      │
      ▼
SentimentAgent.analyze(message, stage)
  ↳ Returns: sentiment, confidence, escalation_recommended
      │
      ▼
─────────────────────────── CONVERSATION GUARD ────────────────────
      │
      ├─ Skip if: state-changing booking turn
      │   (Customer providing slot, date, name — guard would interfere)
      │
      ├─ Loop detection: Same question asked 3+ times?
      │   └─ Yes → Repair response, ask differently
      │
      ├─ Spam/abuse detection
      │   └─ Yes → Polite deflection
      │
      └─ Continue ──────────────────────────────────────────────────
      │
      ▼
─────────────────────────── ROUTING ──────────────────────────────
      │
ConversationManager.determine_routing(message, active_agent)
      │
      ├─ → InboundAgent (categories: faq, recommendation)
      └─ → BookingAgent (categories: new_booking, continuing_workflow)
      │
      ▼
─────────────────────────── AGENT EXECUTION ──────────────────────
      │
      ├─ InboundAgent path:
      │     IntentDetector → SlotFiller → Memory update
      │     → QualificationAgent → RecommendationEngine?
      │     → ResponseComposer (personality voice)
      │
      └─ BookingAgent path:
            BookingOrchestrator → KreedaAPI
            → Slot display / Booking creation / Payment link
      │
      ▼
─────────────────────────── ENRICHMENT ───────────────────────────
      │
_enrich_conversation_result(message, inbound, result, sentiment)
      │
      ├─ EscalationAgent.evaluate()
      │   └─ If escalation → HandoffSummaryAgent.generate()
      │
      └─ ConversationIntelligenceAgent.analyze()
            ├─ EvaluationAgent
            ├─ FollowUpAgent
            ├─ Transcript compilation
            └─ Sentiment trajectory
      │
      ▼
─────────────────────────── CRM PERSIST ──────────────────────────
      │
_persist_chat_result(session_id, runtime, result, channel)
      │
      ├─ Find or create Closiro contact
      ├─ Update call record
      ├─ Create booking record (if booking completed)
      └─ Create escalation record (if escalation)
      │
      ▼
ChatResponse → Client
```

---

## 2. Intent Detection

**File:** `src/services/intent_detector.py`

IntentDetector runs deterministically on every turn. It does not call OpenAI.

### Intent Hierarchy

```
Message
  │
  ▼
Keyword matching (fast path)
  │
  ├─ escape_room_inquiry      "escape room", "book a room", "want to play"
  ├─ birthday_party           "birthday", "bday", "celebrate"
  ├─ bachelor_party           "bachelor", "stag", "hen party"
  ├─ farewell_party           "farewell", "going away", "leaving party"
  ├─ couple_event             "couple", "date night", "anniversary"
  ├─ corporate_event          "corporate", "team outing", "office"
  ├─ virtual_event            "virtual", "online escape"
  ├─ cancellation_request     "cancel", "refund", "cancelled"
  └─ general_faq              fallback (no specific intent matched)
```

### Intent Persistence

Once an intent is detected, it is stored in session memory and used as context for subsequent turns. A new intent can override it when a customer switches topics.

---

## 3. Qualification (Slot Filling)

**File:** `src/services/slot_filler.py`

The QualificationAgent tracks which slots are filled and determines the next question to ask.

### Required Slots

| Slot | Question Asked |
|---|---|
| `customer_name` | "What's your name?" |
| `phone` | "What's the best number to reach you?" |
| `location` | "Which branch? (Whitefield / Koramangala / JP Nagar)" |
| `participants` | "How many people will be joining?" |
| `age_group` | "Are they adults, kids, or a mix?" |
| `preferred_date` | "What date are you thinking?" |

### Slot Priority

Slots are filled in this priority order:
1. `location` (needed for recommendation)
2. `participants` + `age_group` (needed for recommendation)
3. `customer_name`
4. `phone`
5. `preferred_date`

The recommendation is made as soon as `location` + `participants` + `age_group` are known. Remaining slots are collected after recommendation.

---

## 4. Recommendation Flow

```
QualificationAgent: Have location + participants + age_group?
       │
       ├─ No → Continue collecting slots
       │
       └─ Yes → Trigger RecommendationEngine
               │
               ▼
       RecommendationEngine.recommend()
               │
               ▼
       Present top 2 options:
       ─────────────────────────────────────────────
       "For [group_size] [age_group], I'd recommend:
        1. [Room A] — [rationale]
        2. [Room B] — [rationale]
        Which sounds more interesting to you?"
       ─────────────────────────────────────────────
               │
               ▼
       Customer responds (accept or ask more)
               │
               ├─ Accept → room_selected → route to BookingAgent
               │
               └─ Ask more → InboundAgent answers FAQ, re-offers
```

---

## 5. Booking Flow

```
Customer accepts recommendation
       │
       ▼
ConversationManager: category = "new_booking"
       │
       ▼
BookingAgent initialized (lazily, first time)
memory.data["booking_started"] = True
memory.data["current_workflow"] = "booking"
       │
       ▼
BookingOrchestrator.step(message)
       │
       ├─ STATE: collecting_missing_fields
       │   Ask for any missing: date, name, phone
       │   ─────────────────────────────────────
       │
       ├─ STATE: fetching_availability
       │   KreedaAPI.get_slots(location, game, date)
       │   Display available time slots
       │   ─────────────────────────────────────
       │
       ├─ STATE: awaiting_slot_selection
       │   Customer says "2:30 PM" or "first slot"
       │   Parse slot from response
       │   ─────────────────────────────────────
       │
       ├─ STATE: creating_booking
       │   KreedaAPI.prepare_booking({
       │     customer_name, phone, location,
       │     game, date, slot, participants
       │   })
       │   ─────────────────────────────────────
       │   Returns: {
       │     booking_id, booking_reference,
       │     paymentUrl, paymentDeadline
       │   }
       │   ─────────────────────────────────────
       │
       └─ STATE: payment_pending
           WATI sends payment link via WhatsApp
           Customer completes payment externally
           Booking confirmed when Kreeda confirms
```

---

## 6. Escalation Decision Flow

```
EscalationAgent.evaluate(message, sentiment, result)
       │
       ▼
─────────────────────────────────────────────────────────
  Check each trigger in priority order:
─────────────────────────────────────────────────────────
       │
       ├─ PRIORITY 1: Safety keyword
       │   "emergency", "hurt", "danger", "call police"
       │   → urgency: high, immediate response
       │
       ├─ PRIORITY 2: Refund/dispute
       │   "refund", "charge me wrongly", "want money back"
       │   → human agent reviews refund policy
       │
       ├─ PRIORITY 3: Explicit human request
       │   "speak to a person", "human agent", "your manager"
       │   → acknowledge and escalate
       │
       ├─ PRIORITY 4: High frustration detected
       │   sentiment == "frustrated" AND confidence > 0.75
       │   loop: same clarification asked 3+ times
       │   → acknowledge, escalate with context
       │
       └─ No triggers → escalate: false
─────────────────────────────────────────────────────────
       │
  If escalate == true:
       │
       ├─ Generate HandoffSummary
       │   (all collected data + escalation context)
       │
       ├─ Override response with escalation message
       │   "I'll connect you with our team..."
       │
       └─ next_agent = "escalation_agent"
```

---

## 7. Conversation Modes

**File:** `src/core/conversation_modes.py`

ResponseComposer applies different personality tones based on mode:

| Mode | Used For | Tone |
|---|---|---|
| `sales` | Qualification, intake | Warm, consultative |
| `recommendation` | Room suggestions | Enthusiastic, expert |
| `booking` | Slot selection, confirmation | Clear, efficient |
| `rescue` | Confusion, loops | Patient, reassuring |
| `faq` | General questions | Informative, friendly |

---

## 8. State Transition Diagram (Simplified)

```
                  START
                    │
            ┌───────▼───────┐
            │  DISCOVERY    │  ← Greeting, intent detection
            │               │
            └───────┬───────┘
                    │
            ┌───────▼───────┐
            │ QUALIFICATION │  ← Slot filling
            │               │
            └───────┬───────┘
                    │
            ┌───────▼───────┐
            │RECOMMENDATION │  ← Room suggested
            │               │
            └───────┬───────┘
                    │
                    ├──────────────── FAQ preempt (return)
                    │
            ┌───────▼───────┐
            │   BOOKING     │  ← Availability, slot, confirm
            │               │
            └───────┬───────┘
                    │
            ┌───────▼───────┐
            │   PAYMENT     │  ← Payment link sent
            │               │
            └───────┬───────┘
                    │
            ┌───────▼───────┐
            │   COMPLETE    │  ← Booking confirmed
            └───────────────┘

                    │
              At any point:
            ┌───────▼───────┐
            │  ESCALATION   │  ← Human handoff
            └───────────────┘
```
