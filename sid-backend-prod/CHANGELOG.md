# Changelog

All notable changes to the Breakout Agent are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.1.0] — 2026-07-01

### Added
- Closiro CRM API (`/api/v1/`) — full sales CRM backend with contacts, leads, calls, bookings, escalations, pipeline, agents, teams, analytics, notifications, search, and export
- Conversation Intelligence Agent — per-turn AI summary, customer profile, timeline, follow-up recommendations
- Evaluation Agent — qualification score, recommendation quality, CSAT estimate, agent performance rating
- Follow-up Agent — next-action recommendations after each conversation turn
- Handoff Summary Agent — structured handoff documents for human agent escalations
- Sentiment Agent — per-turn sentiment analysis with escalation recommendation
- WhatsApp payment link delivery via WATI (automated on booking creation)
- Web chat static frontend (`web_chat/`)
- LangGraph booking node (`src/integrations/langgraph/`)
- `GET /intelligence/{session_id}` endpoint
- Full Closiro authentication (Bearer JWT with role-based scoping)
- CSV export endpoints for calls, contacts, and lost leads
- Analytics endpoints (dashboard, agent, team, live, data)
- Pagination and filtering for all list endpoints
- `_persist_chat_result()` — automatic CRM update on every chat turn
- `docs/` documentation suite (production handover, frontend guide, API docs, architecture, deployment)
- `docs/reports/archived/` — historical reports archive with index
- `docs/reports/production/` — final production reports
- `docs/reports/DUPLICATE_CODE_REPORT.md` — code audit report
- `CHANGELOG.md`

### Changed
- README.md — complete professional rewrite
- Booking provider abstraction: `SimulatorProvider` + `BreakoutAPIProvider`
- Session memory moved to `memory/api_sessions/{session_id}.json`
- Escalation response wording improved for safety, refund, and human-request cases

### Architecture
- `dispatch()` is the single entry point for all channels
- `ConversationManager` handles all routing decisions
- `ConversationGuard` prevents loops, spam, and off-topic escalations
- `BookingOrchestrator` manages multi-step booking state machine

---

## [1.0.0] — 2026-06-07

### Initial Release
- InboundAgent with intent detection, slot filling, recommendation, and handoff
- Deterministic recommendation engine (no OpenAI dependency)
- Knowledge base retrieval (faq.txt, games.txt, events.txt, policies.txt)
- OpenAI ResponseComposer with personality prompt + few-shot examples
- Local voice mode (Whisper STT + pyttsx3 TTS)
- FastAPI HTTP adapter (`app.py`)
- Booking provider abstraction with simulator
- File-based conversation memory
- Transcript logger
- Basic pytest suite
