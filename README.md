# Breakout Agent

> AI-powered inbound sales and booking assistant for Breakout Escape Rooms.

Multi-channel (web chat, WhatsApp, voice) conversation agent that handles the full customer journey — from first inquiry to confirmed booking and payment — using OpenAI, Kreeda, WATI, and Vapi.

---

## Quick Start

```bash
git clone https://github.com/your-org/breakout-agent
cd breakout-agent/Breakout-Agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # fill in credentials
uvicorn app:app --reload
```

Health check:
```bash
curl http://localhost:8000/health
```

---

## Documentation

| Document | Description |
|---|---|
| [docs/PRODUCTION_HANDOVER.md](docs/PRODUCTION_HANDOVER.md) | Master handover — full system reference |
| [docs/FRONTEND_INTEGRATION.md](docs/FRONTEND_INTEGRATION.md) | Frontend integration guide — all response objects |
| [docs/API_DOCUMENTATION.md](docs/API_DOCUMENTATION.md) | Full API endpoint reference |
| [docs/deployment/DEPLOYMENT.md](docs/deployment/DEPLOYMENT.md) | Deployment guide (local + VPS + all integrations) |
| [docs/architecture/SYSTEM_ARCHITECTURE.md](docs/architecture/SYSTEM_ARCHITECTURE.md) | System architecture diagrams |
| [docs/architecture/CONVERSATION_FLOW.md](docs/architecture/CONVERSATION_FLOW.md) | Conversation flow diagrams |
| [docs/architecture/PROJECT_STRUCTURE.md](docs/architecture/PROJECT_STRUCTURE.md) | Detailed project structure |

---

## Project Overview

The Breakout Agent is not a chatbot. It is a production-grade inbound AI agent that:

- **Detects customer intent** (escape room, birthday, corporate, etc.)
- **Qualifies leads** (collects name, phone, location, group size, date)
- **Recommends rooms** based on group profile using a deterministic engine
- **Books via Kreeda API** (real-time availability, slot selection, booking creation)
- **Sends payment links** via WhatsApp (WATI)
- **Escalates to human** staff when needed
- **Records conversation intelligence** to the Closiro CRM

---

## Architecture

```
Transport Layer: Web Chat | WhatsApp (WATI) | Voice (Vapi)
                              ↓
                         dispatch()
                              ↓
              ConversationGuard → ConversationManager
                    ↓                      ↓
             InboundAgent            BookingAgent
          (FAQ, qualification,     (Kreeda availability,
           recommendation)          slot selection, payment)
                              ↓
              SentimentAgent + EscalationAgent
                              ↓
              ConversationIntelligenceAgent
                              ↓
                     Closiro CRM Store
```

---

## Folder Structure

```
Breakout-Agent/
├── README.md                     ← This file
├── CHANGELOG.md                  ← Version history
├── .env.example                  ← Environment template
├── .gitignore
├── requirements.txt
├── app.py                        ← FastAPI app + Closiro CRM API
├── main.py                       ← CLI entry point + dispatch()
│
├── docs/
│   ├── PRODUCTION_HANDOVER.md    ← Master handover document
│   ├── FRONTEND_INTEGRATION.md   ← Frontend guide
│   ├── API_DOCUMENTATION.md      ← API reference
│   ├── architecture/
│   │   ├── SYSTEM_ARCHITECTURE.md
│   │   ├── CONVERSATION_FLOW.md
│   │   └── PROJECT_STRUCTURE.md
│   ├── deployment/
│   │   └── DEPLOYMENT.md
│   └── reports/
│       ├── DUPLICATE_CODE_REPORT.md
│       ├── production/           ← Active production reports
│       └── archived/             ← Historical dev/QA reports
│
├── src/
│   ├── agents/
│   │   ├── inbound_agent.py      ← Primary sales agent
│   │   ├── booking_agent.py      ← Booking state machine
│   │   ├── escalation_agent.py
│   │   ├── sentiment_agent.py
│   │   ├── evaluation_agent.py
│   │   ├── follow_up_agent.py
│   │   ├── conversation_intelligence_agent.py
│   │   └── handoff_summary_agent.py
│   ├── orchestration/
│   │   ├── conversation_manager.py  ← Message routing
│   │   ├── booking_orchestrator.py  ← Booking state machine
│   │   └── router.py
│   ├── services/
│   │   ├── recommendation_engine.py
│   │   ├── intent_detector.py
│   │   ├── slot_filler.py
│   │   ├── question_classifier.py
│   │   ├── conversation_guard.py
│   │   ├── conversation_intelligence.py
│   │   ├── gpt_reasoner.py
│   │   ├── wati_client.py
│   │   └── venue_policy.py
│   ├── integrations/
│   │   ├── kreeda/               ← Kreeda booking API client
│   │   └── langgraph/            ← LangGraph booking node
│   ├── memory/
│   │   ├── conversation_memory.py ← Session state + slot filling
│   │   └── session_manager.py
│   ├── core/
│   │   ├── agent_response.py
│   │   ├── conversation_modes.py
│   │   └── handoff_generator.py
│   ├── config/
│   │   ├── env_loader.py
│   │   ├── settings.py
│   │   └── constants.py
│   ├── knowledge/
│   │   ├── knowledge_loader.py
│   │   └── knowledge_retriever.py
│   ├── logger/
│   │   └── transcript_logger.py
│   ├── voice/
│   │   ├── stt/voice_input.py    ← Whisper STT (local)
│   │   └── tts/voice_output.py   ← pyttsx3 TTS (local)
│   └── response_composer.py      ← OpenAI personality layer
│
├── integrations/
│   └── langgraph_booking_node.py  ← Root-level shim (used by tests)
│
├── knowledge/                    ← Runtime knowledge base
│   ├── faq.txt
│   ├── games.txt
│   ├── events.txt
│   └── policies.txt
│
├── prompts/                      ← Active system prompts
│   ├── breakout_personality_prompt.txt
│   ├── conversation_playbook.txt
│   ├── inbound_prompt.txt
│   └── transcript_examples.json
│
├── tests/                        ← 45 test files
├── scripts/                      ← Dev + demo scripts
├── web_chat/                     ← Embedded web chat frontend
├── memory/                       ← Runtime session files (gitignored)
├── logs/                         ← Conversation logs (gitignored)
└── scratch/
    └── archived/                 ← Historical probe outputs
```

---

## Setup

### Prerequisites

- Python 3.9+
- macOS or Linux (Windows via WSL)
- `brew install portaudio` (macOS, for voice mode only)

### Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Environment Variables

```bash
cp .env.example .env
```

Required variables:

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key (optional — fallback works without it) |
| `BOOKING_BASE_URL` | Kreeda API URL (`https://bs.kreeda.icu`) |
| `BOOKING_API_KEY` | Kreeda authentication key |
| `BOOKING_PROVIDER` | `auto` (recommended) or `simulator` |
| `WATI_BASE_URL` | WATI WhatsApp API URL |
| `WATI_ACCESS_TOKEN` | WATI access token |
| `WATI_SENDER_NUMBER` | WhatsApp sender number (E.164 format) |

Full variable reference: [docs/deployment/DEPLOYMENT.md](docs/deployment/DEPLOYMENT.md#2-environment-variables-reference)

---

## Running Locally

### API Server

```bash
uvicorn app:app --reload
```

### Text Mode (CLI)

```bash
python main.py --debug
```

### Voice Mode (Local)

```bash
python main.py --voice
```

---

## Running Tests

```bash
python -m pytest tests -q
```

Run with output:
```bash
python -m pytest tests -v --tb=short
```

The suite covers: conversation scenarios, booking flow, escalation, sentiment, recommendation engine, WATI, web chat, Closiro API contract, red team validation, and production regressions.

---

## Conversation Pipeline

```
Customer Message
  → normalize_entity_aliases()
  → SentimentAgent
  → ConversationGuard (loop/spam detection)
  → ConversationManager (routing)
  → InboundAgent OR BookingAgent
  → EscalationAgent
  → ConversationIntelligenceAgent
  → Closiro CRM
  → ChatResponse
```

Full flow: [docs/architecture/CONVERSATION_FLOW.md](docs/architecture/CONVERSATION_FLOW.md)

---

## Booking Flow

1. InboundAgent recommends a room
2. Customer accepts → routed to BookingAgent
3. Remaining slots collected (name, phone, date, participants)
4. Kreeda API returns available time slots
5. Customer selects a slot
6. Kreeda creates booking → returns `booking_id` + `paymentUrl`
7. WATI sends payment link via WhatsApp
8. Customer completes payment on Kreeda checkout page

Full flow: [docs/PRODUCTION_HANDOVER.md#6-booking-flow](docs/PRODUCTION_HANDOVER.md#6-booking-flow)

---

## Payment Flow

1. Booking created via `KreedaAPI.prepare_booking()`
2. `paymentUrl` received in response
3. `WatiClient.send_session_message()` delivers link over WhatsApp
4. `ChatResponse.payment` contains payment URL + deadline
5. Customer pays externally on Kreeda checkout page

Full lifecycle: [docs/PRODUCTION_HANDOVER.md#7-payment-lifecycle](docs/PRODUCTION_HANDOVER.md#7-payment-lifecycle)

---

## Deployment

See [docs/deployment/DEPLOYMENT.md](docs/deployment/DEPLOYMENT.md) for:

- Local development setup
- VPS deployment with Gunicorn + Nginx
- Systemd service configuration
- WATI webhook setup
- Vapi integration
- Kreeda API configuration
- Production checklist

---

## Troubleshooting

| Issue | Fix |
|---|---|
| `booking_provider: simulator` in health | Set `BOOKING_API_KEY` + `BOOKING_BASE_URL` |
| Responses are flat / no personality | Check `OPENAI_API_KEY` |
| WhatsApp messages not received | Check WATI webhook URL + HTTPS |
| Tests failing after cleanup | No `src/` files moved — imports should be intact |
| `memory/api_sessions/` error | `mkdir -p memory/api_sessions` |
| Port 8000 in use | `lsof -i :8000 && kill -9 <PID>` |

---

## Developer Workflow

### Adding Knowledge

Edit files in `knowledge/`:
- `faq.txt` — frequently asked questions
- `games.txt` — room/game descriptions
- `events.txt` — event types and packages
- `policies.txt` — cancellation, payment, group policies

### Updating Prompts

Edit files in `prompts/`:
- `breakout_personality_prompt.txt` — agent personality and tone
- `conversation_playbook.txt` — response patterns
- `inbound_prompt.txt` — inbound agent system prompt

> **Do not** modify `src/agents/`, `src/services/`, `src/orchestration/`, or `src/integrations/` unless you intend to change business logic.

### Running Scripts

```bash
# Demo runner
python scripts/demo_runner.py

# Pre-deployment stress test
python scripts/predeployment_stress_test.py

# System diagnostics
python scripts/system_diagnostics.py

# Probe escalation
python scripts/probe_escalation.py
```

### API Documentation

All API endpoints are documented in [docs/API_DOCUMENTATION.md](docs/API_DOCUMENTATION.md).

Interactive docs are auto-generated by FastAPI:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
