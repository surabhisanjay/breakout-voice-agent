# Closira Inbound Call Handling: Cross-Team Integration & Alignment Package

This package contains the presentation slides, detailed system architecture flows, technical integration questionnaires with architectural risk assessments, and project tracking registers required for tomorrow's Full-Stack Team Alignment Meeting.

---

## Deliverable 1: Meeting Presentation Slides (15-Minute Run)

### Slide 1: Welcome & Integration Alignment Objective
* **Time Allocation**: 1 Minute (Section 1: Introduction)
* **Slide Bullets**:
  * **Objective**: Connect the Voice AI Core with the Booking, Payment, CRM, and Messaging backend layers.
  * **The Gold Standard**: Achieve sub-1.2s conversation latency with a 100% reliable state machine.
  * **Meeting Goal**: Eliminate implementation ambiguity, finalize data ownership, and establish a unified RACI matrix.
* **Suggested Visuals**: 
  * High-level 3-tier block diagram showing **Telephony / Voice AI Interface** ➔ **Orchestration / State Gateways** ➔ **CRM & Systems of Record**.
* **Speaker Notes**:
  > "Thanks everyone for jumping on. Today, we are aligning our state-of-the-art voice agent core—which handles natural dialog, qualification, and context-based SOPs—with our backend services. We need to move from the file-based MVP simulator to a highly resilient production stack. The core goal is establishing clear interface contracts between the agent's turn loop, the booking state machine, and our external channels."
* **Questions to Ask on This Slide**:
  * *Are there any additional downstream systems or teams that need to be represented in this integration map before we lock down the scope?*

---

### Slide 2: Current Progress & Performance Metrics
* **Time Allocation**: 3 Minutes (Section 2: Project Progress Overview)
* **Slide Bullets**:
  * **Conversational AI Core**: Hardened qualification flows (Name, Phone, Location, Guests, Experience Level) in testing.
  * **Turn Scorer**: 14-Point Turn QA Rubric implemented to automatically assess dialog naturalness, empathy, and context retention.
  * **Robustness & Recovery**: Multi-turn FAQ interrupts, group-size corrections, and loop detection are fully operational.
  * **Test Coverage**: 838 unit and simulation tests executing successfully.
* **ASCII System Overview**:
  ```
  [Telephony (TeleCMI)] ➔ [Voice Stream (Vapi)] ➔ [FastAPI Webhook Handler]
                                                         │
  ┌──────────────────────────────────────────────────────┴──────────────────────────────────────────────────────┐
  ▼                                                      ▼                                                      ▼
  [LangGraph State Nodes] ➔ [Sentiment & QA Scorer] ➔ [MongoDB Logs] ➔ [Flask Admin Console (Port 8020 / trycloudflare)]
  ```
* **Speaker Notes**:
  > "We have built and verified the core conversational nodes using 838 unit tests. The agents gracefully handle interruptions, policy questions, and state shifts. Currently, session logs and transcripts write to MongoDB/JSON files, and metrics render on a 11-page Flask Admin Console. Today we will design how to scale this backend architecture."
* **Questions to Ask on This Slide**:
  * *Do we have historical logs of real client calls that we can use to run batch evaluation tests against the new 14-Point Turn Scorer?*

---

### Slide 3: Interactive Demo Walkthrough
* **Time Allocation**: 5 Minutes (Section 3: Demo of Current Work)
* **Slide Bullets**:
  * **Live Sandbox**: Access the console at our temporary public Cloudflare Tunnel.
  * **Turn Heuristics**: Observe how the agent detects price objections, applies validation cushions, and steers back to the booking path.
  * **Real-time Dashboard**: Call stats, live transcription, latency counters, and agent QA breakdown are active.
  * **Handoff Output**: Shows structured facts gathered (Sentiment: Excited, Group Size: 10, Location: Bangalore) to prepare human agents.
* **Suggested Visuals**:
  * Live screen share of the **Live Calls** monitor page or the **Conversations Timeline** showing the turn-by-turn QA scorer interface.
* **Speaker Notes**:
  > "Let's review the live dashboard. Notice how the agent tracks sentiment. If a customer gets frustrated or raises a complaint, the Escalation Agent immediately flags it, triggers an empathetic apology cushion, and halts the AI session while generating a handover summary for the human staff."
* **Questions to Ask on This Slide**:
  * *What is the maximum simultaneous call capacity our server must handle during peak hours, and how does the UI scale when monitoring 50+ concurrent active calls?*

---

### Slide 4: Key Integration Touchpoints
* **Time Allocation**: 3 Minutes (Section 4: Integration Discussion)
* **Slide Bullets**:
  * **Vapi Call Control**: Webhook endpoint must process tool calls and return JSON responses in <800ms.
  * **Post-Call Pipeline**: Call termination ➔ MongoDB updates ➔ Celery task triggers SMS/WhatsApp follow-ups.
  * **Transaction Integrity**: The booking is ONLY confirmed upon receiving a `payment_success` webhook event from the gateway.
* **ASCII Integration Touchpoints**:
  ```
  [User Call] ➔ [Vapi] ➔ [FastAPI App] ➔ [Qdrant RAG FAQ]
                             │
            ┌────────────────┴────────────────┐
            ▼                                 ▼
     [PostgreSQL DB]                  [Celery Worker]
  (Leads, Bookings Tables)       (WhatsApp templates, SMS)
  ```
* **Speaker Notes**:
  > "Here are our critical boundaries. FastAPI is the entry point. The agent checks availability against the database, locks the slot temporarily, and issues a payment link. The source of truth for confirmation is the payment gateway webhook. Once that succeeds, Celery handles post-call confirmations."
* **Questions to Ask on This Slide**:
  * *What is the expiration timeout on temporary slot reservations, and how do we notify the payment gateway to cancel links if the reservation expires?*

---

### Slide 5: Known Bottlenecks & Operational Blockers
* **Time Allocation**: 3 Minutes (Section 5: Team Updates & Blockers)
* **Slide Bullets**:
  * **Dependency Lock**: Need official WhatsApp API keys and approved message templates.
  * **Payment Callbacks**: Need sandbox API keys and endpoint structures for transaction status validations.
  * **Telephony Hook**: TeleCMI call-transfer mapping to landline/agent groups needs definition.
* **Suggested Visuals**:
  * Blockers status board showing **Red (Blocker)**, **Amber (Risk)**, **Green (Resolved)** columns.
* **Speaker Notes**:
  > "Our primary blocker is access to the production SMS/WhatsApp messaging providers and payment sandbox environments. Without these credentials, we cannot test the transition from simulation to live APIs. We also need to map the exact SIP call transfer protocol on TeleCMI."
* **Questions to Ask on This Slide**:
  * *Who owns the relationship with the telephony provider, and how quickly can we provision a test SIP Trunk for handoff testing?*

---

### Slide 6: Roadmap & Milestones
* **Time Allocation**: 3 Minutes (Section 6: Next Steps & Timeline Alignment)
* **Slide Bullets**:
  * **Phase 1 (Days 1-3)**: Expose DB Schemas and load policy data to Qdrant.
  * **Phase 2 (Days 4-7)**: Deploy LangGraph state engine and Vapi route integrations.
  * **Phase 3 (Days 8-10)**: Establish Celery tasks and Centrifugo real-time event streaming.
  * **Phase 4 (Days 11-15)**: End-to-end integration tests & Staging release.
* **Speaker Notes**:
  > "We have a 15-day timeline to transition to the enterprise architecture. We will build storage first, wire up the LangGraph state machine, integrate Celery workers, and wrap up with cross-platform testing. Let's align on these milestones."
* **Questions to Ask on This Slide**:
  * *Are there any conflicts with scheduled backend infrastructure deployments that might affect this 15-day target?*

---

## Deliverable 2: Architecture Flows

### 1. Inbound Call Flow
```
[Customer Spoken Input]
           │
           ▼
[Telephony Gateway (TeleCMI)]
           │
           ▼ (WebRTC / SIP Audio Stream)
[Voice AI Platform (Vapi)] 
           │
           ▼ (POST /vapi/tool JSON payload)
[FastAPI Backend Application] 
           │
  ┌────────┴────────┐
  ▼                 ▼
[RAG Engine]   [Intent & SOP Classifier]
  (Qdrant)       (LangGraph State Machine)
  (Retrieves     (Determines conversation step)
   FAQs/SOPs)       │
                    ▼
          [Response Generation]
                    │
                    ▼ (Returns JSON text)
[Voice AI Platform (Vapi)] ➔ (TTS Synthesis) ➔ [User Audio Output]
```

### 2. Booking & Verification Flow
```
[Customer] ➔ Speaks: "Book Murder Mystery slot"
                 │
                 ▼
          [AI Voice Agent]
                 │
                 ▼ (Calls availability API)
      [Booking API (Postgres)] (Selects & locks slot temporary)
                 │
                 ▼ (Generates Payment Link)
     [Payment Gateway (Stripe/Razorpay)]
                 │
                 ▼ (Sends Link via WhatsApp/SMS to Customer)
    [WhatsApp/SMS Integration Client]
                 │
       [Customer Pays via Link]
                 │
                 ▼ (Async POST /webhooks/payment)
    [Payment Gateway Webhook Endpoint]
                 │
                 ▼ (Verify signature & transaction state)
         [Booking Service] (Marks booking status as CONFIRMED)
                 │
                 ▼ (Triggers confirmation notification)
      [Celery Asynchronous Tasks] ➔ [WhatsApp Booking Confirmation]
```

### 3. Escalation Flow
```
[Customer] ➔ Speaks: "This is terrible service. Let me speak to someone."
                 │
                 ▼
          [AI Voice Agent]
                 │
                 ▼ (Matches COMPLAINT or HUMAN_REQUEST patterns)
         [Escalation Agent] (Flags escalation & triggers cushion response)
                 │
                 ▼ (Compiles facts gathered vs missing fields)
      [Handoff Summary Agent] 
                 │
                 ▼ (Updates session state in DB)
         [MongoDB Records]
                 │
                 ▼ (Pushes event to dashboard UI)
       [Centrifugo Server] ➔ Real-time notify ➔ [Human Agent Dashboard]
                                                    │
[Telephony Gateway (TeleCMI)] ➔ (SIP Refer Transfer) ➔ [Human Agent Call]
```

### 4. Post-Call Follow-up Flow
```
[Customer Hangs Up / Call Ends]
                 │
                 ▼ (Triggered via Vapi call-end webhook)
        [FastAPI Webhook]
                 │
                 ▼ (Aggregates dialog turns)
      [Handoff Summary Agent] (Generates outcome classification)
                 │
                 ▼ (Updates PostgreSQL and MongoDB records)
    [Database System of Record]
                 │
                 ▼ (Enqueues task with delay)
         [Celery Worker]
                 │
                 ▼ (Checks safe hours: 8 AM - 8 PM local)
       [Safe Hours Guard]
                 │
                 ▼ (Triggers outreach)
    [WhatsApp/SMS Client] ➔ (Sends personalized discount/reminder)
```

---

## Deliverable 3 & 4: Technical Integration Questionnaire & Risk Assessment

---

### Category A: Booking APIs

#### Q1: What is the base URL, authentication scheme, and endpoint path for the booking availability and scheduling services?
* **Why it matters**: The `BookingAgent` must query real-time calendar availability during calls to present matching time slots.
* **Architectural Decisions Dependent on It**: Webhook API request header construction, token renewal policies, and environment credential configurations.
* **Risks if Unanswered**: Development gets locked using local simulators; authentication expiry issues remain undetected.

#### Q2: What are the exact request and response schemas for reserving a slot, and do they support custom fields (e.g., age group, experience level)?
* **Why it matters**: We need to validate payloads using strict Pydantic structures in FastAPI.
* **Architectural Decisions Dependent on It**: `BookingAgent` data mapping definitions, validation schemas, and database entity design.
* **Risks if Unanswered**: Runtime serialization errors can crash caller sessions; custom qualification details may fail to sync to the CRM.

#### Q3: Does the slot reservation endpoint temporarily lock/hold the slot? If so, what is the lock duration, and what is the release endpoint?
* **Why it matters**: If a customer selects a slot but takes 5 minutes to pay, we must ensure another caller doesn't book the same slot.
* **Architectural Decisions Dependent on It**: Expiration timers, database status tracking states, and retry queues.
* **Risks if Unanswered**: Double-booking errors occur during concurrent payment windows.

---

### Category B: Booking State Machine

#### Q4: What is the system of record for the booking state machine, and does it align with the lifecycle schema: `STARTED` ➔ `SLOT_SELECTED` ➔ `PAYMENT_LINK_SENT` ➔ `PAYMENT_PENDING` ➔ `PAYMENT_SUCCESS` ➔ `BOOKING_CONFIRMED`?
* **Why it matters**: To prevent discrepancies, both the voice agent, backend workers, and CRM portal must read from a unified, single source of truth.
* **Architectural Decisions Dependent on It**: Database schemas (PostgreSQL) and LangGraph memory state transitions.
* **Risks if Unanswered**: Desynchronization between the dashboard view and the database (e.g., dashboard showing active slot selection when the transaction has already timed out).

#### Q5: How are booking failure states (`PAYMENT_FAILED`, `PAYMENT_EXPIRED`, `CUSTOMER_ABORTED`) handled?
* **Why it matters**: If a payment expires, we need to release the locked slot and flag the lead for outreach.
* **Architectural Decisions Dependent on It**: Celery tasks and database cleanup worker intervals.
* **Risks if Unanswered**: Locked slots remain blocked indefinitely, hurting reservation volumes.

---

### Category C: Payment System

#### Q6: Which payment gateway is used, and what are its webhook URL formats, payload schemas, and secret configurations?
* **Why it matters**: We need to declare a dedicated FastAPI endpoint (`/webhooks/payment`) to receive callbacks.
* **Architectural Decisions Dependent on It**: Webhook routing configurations and cryptographic validation utilities.
* **Risks if Unanswered**: Failed payments are marked as successful, or successful bookings are not triggered.

#### Q7: How is webhook security and verification performed by the backend?
* **Why it matters**: Attackers can spoof payment confirmations by posting raw JSON to our endpoint.
* **Architectural Decisions Dependent on It**: Middleware token structures and verification logic (e.g., Stripe signature verification libraries).
* **Risks if Unanswered**: High risk of financial fraud (people booking slots without paying).

#### Q8: Does the payment provider support idempotency keys?
* **Why it matters**: Network retries might cause duplicate payment confirmations or duplicate bookings.
* **Architectural Decisions Dependent on It**: Payment payload composition and signature verification.
* **Risks if Unanswered**: Duplicate booking records and billing exceptions.

---

### Category D: WhatsApp/SMS Messaging

#### Q9: What are the base endpoints, authentication requirements, and payload formats for the SMS/WhatsApp provider APIs?
* **Why it matters**: The WhatsApp worker client must connect to the correct messaging gateway.
* **Architectural Decisions Dependent on It**: Worker settings configurations and SDK integrations.
* **Risks if Unanswered**: Notifications are dropped or delayed during call completions.

#### Q10: How are delivery status callbacks configured and verified?
* **Why it matters**: The CRM must show whether the customer received and opened the booking confirmations.
* **Architectural Decisions Dependent on It**: Webhook routing and database field flags.
* **Risks if Unanswered**: Customer support has no visibility into delivery failures.

---

### Category E: Telephony (TeleCMI) + Voice Gateway (Vapi)

#### Q11: Who owns the active call state during an escalation/transfer, and what event payload is dispatched upon transfer success or failure?
* **Why it matters**: The voice agent must terminate its conversation context immediately when the call is successfully transferred to a human.
* **Architectural Decisions Dependent on It**: Session termination flags and webhooks.
* **Risks if Unanswered**: The AI agent continues listening and responding in the background during a live human conversation.

#### Q12: How are audio recordings and live transcripts synced back to our database?
* **Why it matters**: The dashboard must display call playbacks and historical transcripts.
* **Architectural Decisions Dependent on It**: MongoDB schemas and webhook storage logic.
* **Risks if Unanswered**: Compliance records are lost; quality control reviews cannot run.

---

### Category F: CRM Integration

#### Q13: What is the primary system of record for customer contacts: ClickUp, a custom database, or an external CRM?
* **Why it matters**: We need to know where to update contact files when a lead is captured.
* **Architectural Decisions Dependent on It**: Integration mapping layers and sync protocols.
* **Risks if Unanswered**: Lead data is split across multiple databases, leading to double data entry.

#### Q14: What fields are strictly required by the CRM, and how are validation failures resolved?
* **Why it matters**: The agent must capture every mandatory field during the call to prevent lead dropoffs.
* **Architectural Decisions Dependent on It**: Qualification flow rules and field checks.
* **Risks if Unanswered**: Leads are rejected by the CRM due to missing parameters.

---

### Category G: Session Management

#### Q15: Where are active call session states stored to support scaling across multiple servers?
* **Why it matters**: If a server crashes, active sessions must fail over instantly to keep the call alive.
* **Architectural Decisions Dependent on It**: Redis cache configuration and persistence layers.
* **Risks if Unanswered**: Mid-call server updates cause calls to drop or lose context.

#### Q16: How do we detect and handle concurrent multi-session collisions?
* **Why it matters**: If a customer calls on the phone and texts via WhatsApp simultaneously, we must prevent duplicate records.
* **Architectural Decisions Dependent on It**: Redis locks and session key validation.
* **Risks if Unanswered**: Duplicate leads and corrupted database states.

---

### Category H: Reliability & Fault Tolerance

#### Q17: What are the target rate limits and concurrency parameters for all downstream APIs?
* **Why it matters**: We must protect third-party gateways from being overloaded by concurrent webhooks.
* **Architectural Decisions Dependent on It**: Rate limit buffers and Celery worker settings.
* **Risks if Unanswered**: Downstream APIs rate-limit our servers, dropping critical requests.

#### Q18: What is the failover strategy if the LLM provider (OpenAI/Claude/Gemini) experiences an outage?
* **Why it matters**: The voice assistant cannot go silent mid-call.
* **Architectural Decisions Dependent on It**: LangChain failover providers and fallback prompt rules.
* **Risks if Unanswered**: The voice assistant stops responding, dropping the call.

---

### Category I: Observability & Monitoring

#### Q19: What correlation ID or tracing header standard is used across our services?
* **Why it matters**: We must trace a single call event across TeleCMI, Vapi, FastAPI, LangGraph, and MongoDB.
* **Architectural Decisions Dependent on It**: Middleware logging headers.
* **Risks if Unanswered**: Debugging latency or integration errors becomes difficult.

---

## Deliverable 5: Meeting Action Registers & RACI

### 1. Integration Checklist
- [ ] Deploy PostgreSQL schemas (`leads`, `bookings`, `configurations` tables).
- [ ] Deploy MongoDB collections (`transcripts`, `agent_evaluations`).
- [ ] Initialize Qdrant collection and upload embedded business policy documents.
- [ ] Configure Vapi assistant webhook to point to staging FastAPI `/vapi/tool`.
- [ ] Upload approved WhatsApp message templates to Meta Business Manager.
- [ ] Register stripe/payment gateway sandbox webhooks mapping to `/webhooks/payment`.
- [ ] Validate SIP transfer routing configurations on TeleCMI telephony panel.

### 2. Dependencies List
1. **Infrastructure**: Provisioning of PostgreSQL, MongoDB, Redis, and Qdrant staging environments.
2. **Accounts**: Meta Business account approval and WhatsApp Business API access keys.
3. **Telephony**: TeleCMI SIP Trunk configuration mapping for call forwarding.
4. **LLM Quotas**: Increasing OpenAI/Anthropic staging API request-per-minute (RPM) limits.

### 3. Blockers Register
| ID | Blocker Description | Impacted Area | Owner | Target Date | Status |
| :--- | :--- | :--- | :--- | :---: | :---: |
| **B-01** | Missing WhatsApp production credentials & template approvals | SMS/WhatsApp worker | Product / Ops | Day 3 | **HIGH** |
| **B-02** | Payment Gateway sandbox API access not configured | Payment validations | Backend Lead | Day 2 | **CRITICAL**|
| **B-03** | TeleCMI SIP Trunk mapping undefined for call handoffs | Escalations routing | Telephony Lead| Day 4 | **HIGH** |

### 4. Risk Register
| ID | Risk Description | Probability | Impact | Mitigation Strategy |
| :--- | :--- | :---: | :---: | :--- |
| **R-01** | Downstream Booking API timeout exceeds Vapi's 2-second limit | Moderate | High | Implement a Redis cache for booking slot availability queries. |
| **R-02** | Duplicate webhook callbacks from payment gateway | High | Moderate | Enforce unique transaction constraints and idempotency keys in PostgreSQL. |
| **R-03** | LLM API rate limit exceeded during peak call volumes | Low | Critical | Configure LangChain backup failover switchers to Google Gemini or Claude. |

### 5. RACI Matrix (Ownership Matrix)
* **R**: Responsible, **A**: Accountable, **C**: Consulted, **I**: Informed

| Component / Task | AI Voice Core Team | Backend API Team | DevOps / Infra | Product / QA |
| :--- | :---: | :---: | :---: | :---: |
| **LangGraph Agentic Flows** | **A** / **R** | **C** | **I** | **C** |
| **FastAPI Webhooks & Routes** | **R** | **A** / **R** | **C** | **I** |
| **PostgreSQL / MongoDB Schemas**| **C** | **A** / **R** | **R** | **I** |
| **Qdrant RAG FAQ System** | **A** / **R** | **C** | **R** | **C** |
| **Celery Tasks / Redis Cache** | **C** | **A** / **R** | **R** | **I** |
| **WhatsApp / Payment APIs** | **C** | **A** / **R** | **I** | **R** |
| **Deployment / CI-CD** | **I** | **C** | **A** / **R** | **I** |

### 6. Action Items
* **Action 1**: Provision staging database credentials (Postgres, MongoDB, Qdrant). *[Owner: DevOps Lead]*
* **Action 2**: Create sandbox payment API credentials. *[Owner: Backend Lead]*
* **Action 3**: Upload policy documents to Qdrant vector database. *[Owner: AI Voice Core Lead]*
* **Action 4**: Schedule WhatsApp template approvals with Meta. *[Owner: Product Manager]*

### 7. Decisions That Must Be Finalized Today
1. **Database Selection**: Validate using PostgreSQL for core business records and MongoDB for raw logging.
2. **State Machine System of Record**: Confirm PostgreSQL as the absolute source of truth for the transaction lifecycle.
3. **Webhook Authentication Protocol**: Decide whether to enforce token authentication or IP validation for incoming payment gateway callbacks.

### 8. Information to Collect Before Implementation
* The exact endpoint list and API key configurations for the staging booking API.
* The API payload schemas for SMS/WhatsApp messaging gateways.
* TeleCMI phone group SIP transfer numbers for human escalation routing.
