# Breakout Inbound Agent MVP

OpenAI-powered production-style MVP for Agent #1 in a multi-agent AI call center for Breakout Escape Rooms.

This is not a chatbot. It is an inbound intake agent that greets customers, detects intent, answers basic company questions, recommends suitable rooms or packages, collects missing details, generates a structured handoff summary, and routes to the next agent.

## Stack

- Python
- OpenAI Responses API
- GPT-4.1 mini by default for customer-facing response composition
- Whisper for speech-to-text
- pyttsx3 for text-to-speech
- No Claude APIs

## Project Structure

```text
Breakout-Agent/
├── knowledge/
│   ├── faq.txt
│   ├── games.txt
│   ├── events.txt
│   └── policies.txt
├── prompts/
│   ├── inbound_prompt.txt
│   ├── breakout_personality_prompt.txt
│   └── conversation_playbook.txt
├── memory/
│   └── session.json
├── src/
│   ├── knowledge_loader.py
│   ├── intent_detector.py
│   ├── recommendation_engine.py
│   ├── response_composer.py
│   ├── conversation_modes.py
│   ├── booking_provider.py
│   ├── integrations/
│   │   └── breakout_api.py
│   ├── conversation_memory.py
│   ├── handoff_generator.py
│   ├── voice_input.py
│   ├── voice_output.py
│   ├── router.py
│   └── inbound_agent.py
├── main.py
├── requirements.txt
└── README.md
```

## Responsibilities

The inbound agent can:

- Greet customers.
- Understand customer intent.
- Answer basic company questions.
- Explain Breakout services.
- Recommend suitable rooms or packages.
- Collect name, phone, location, participants, event type, and preferred date.
- Store conversation memory.
- Generate a structured handoff summary.
- Route to the next agent.

The inbound agent must not:

- Create bookings.
- Process payments.
- Approve refunds.
- Confirm availability.
- Resolve complaints.
- Make business decisions.

## Supported Intents

- `escape_room_inquiry`
- `birthday_party`
- `bachelor_party`
- `farewell_party`
- `couple_event`
- `corporate_event`
- `virtual_event`
- `cancellation_request`
- `general_faq`

## Setup

Create a `.env` file with your OpenAI API key. You can optionally override the default model:

```dotenv
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini
BOOKING_API_KEY=
BOOKING_BASE_URL=https://bs.kreeda.icu
```

Create a virtual environment and install dependencies:

```bash
cd Breakout-Agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Some systems need PortAudio for microphone input:

```bash
brew install portaudio
```

## Run in Text Mode

```bash
python main.py --debug
```

Use deterministic fallback responses without calling OpenAI:

```bash
python main.py --no-openai --debug
```

Reset memory at startup:

```bash
python main.py --reset-memory --debug
```

Inside the app:

- `/handoff` prints the current structured handoff JSON.
- `/reset` clears the session.
- `/quit` exits.

## Run in Voice Mode

```bash
python main.py --voice
```

Voice mode uses Whisper `small`, forces English transcription, and listens with voice activity detection. It starts recording when speech is detected, stops after about 0.8 seconds of silence, and then speaks the final clean response through pyttsx3. The 12-second setting is only a maximum turn length, not a fixed wait.

Supported stop words: `quit`, `goodbye`, `exit`, `stop`, and `bye`.

The agent listens through the microphone, transcribes with Whisper, responds through the inbound agent, and speaks back through pyttsx3. Customer-facing speech contains only the final natural response.

Every customer turn is stored as JSON in `logs/conversations/`, including timestamp, customer utterance, agent response, intent, collected context, route, and handoff summary.

For development debugging only:

```bash
python main.py --voice --debug
```

## Uploaded Knowledge Source

The runtime knowledge files in `knowledge/` are generated from the uploaded Breakout source documents:

- `Escape rooms, Parties and Corporates detail.xlsx`
- `Breakout FAQs.docx`
- `Breakout Details - Sheet1.pdf`

The agent treats those generated files as the only source of truth. If a question cannot be answered from retrieved source context, it responds:

```text
I don't currently have that information, but I can connect you with the appropriate team.
```

The helper extractor is available at:

```bash
python ../work/extract_breakout_knowledge.py
```

## Retrieval

Before calling OpenAI, the agent performs lightweight keyword retrieval over `faq.txt`, `games.txt`, `events.txt`, and `policies.txt`, then injects only the relevant sections into the prompt. The full knowledge base is not sent to the model.

## Personality And Response Composition

Intent detection, slot filling, qualification, routing, recommendations, and booking decisions remain deterministic. The approved response and structured state are passed to the OpenAI Response Composer, which applies the Breakout hospitality voice from `prompts/breakout_personality_prompt.txt`. If OpenAI is unavailable, times out, or returns an empty response, the approved deterministic response is used immediately.

Conversation wording is selected using five modes: `sales`, `recommendation`, `booking`, `rescue`, and `faq`.

The transcript-derived conversation playbook operationalizes acknowledgment, recommendation, reassurance, objection handling, policy explanation, light humor, qualification, and closing patterns. The composer uses it together with the personality prompt on every dynamic Inbound Agent and Booking Agent business response.

Voice startup greetings, unclear-transcript clarification, exit farewells, and local QA commands remain deterministic so they are immediate and reliable.

## Booking Provider

The Booking Agent uses a provider abstraction without changing its state machine:

- `SimulatorProvider` uses the existing local availability and booking tools.
- `BreakoutAPIProvider` implements the documented locations, games, slots, and prepare-booking endpoints.

When `BOOKING_API_KEY` and `BOOKING_BASE_URL` are configured, the real provider is selected. Otherwise the application falls back to the simulator. A prepared API booking is not described as confirmed until checkout is completed.

## Tests

Run the automated test suite:

```bash
python -m pytest tests -q
```

The demo-readiness suite covers first-time players, couples, families, corporate groups, late arrivals, briefings, unsuccessful escapes, topic interruptions, recommendation-first behavior, compound questions, booking mode, and session isolation.

Voice architecture:

```text
Customer Voice
↓
Whisper
↓
Text
↓
Inbound Agent
↓
Knowledge Base
↓
Response
↓
pyttsx3
↓
Voice
```

## Sample Conversations

### FAQ

Customer:

```text
What is an escape room?
```

Agent:

```text
Sure. An escape room is a live adventure game where your group solves clues and puzzles to complete a mission within about 60 minutes.
```

### Kids Recommendation

Customer:

```text
We have 6 kids aged 10.
```

Agent:

```text
For 6 kids aged 10, I'd recommend Murder Mystery or Hostage. Murder Mystery focuses on investigation and clue solving, while Hostage adds more urgency with a rescue-style story. Which location are you planning to visit?
```

### Adult Challenge Recommendation

Customer:

```text
We are adults looking for a challenging room.
```

Agent:

```text
For adults looking for a challenge, I'd recommend Classified or Bomb Defusal. Classified is investigation-led, while Bomb Defusal is more intense and time-pressured. Which location are you planning to visit?
```

### Memory

Customer:

```text
My name is Rahul.
```

Customer:

```text
We want a birthday party.
```

Customer:

```text
Whitefield.
```

The agent stores the values in `memory/session.json` and uses them in future turns.

### Handoff

When enough information is collected, run:

```text
/handoff
```

Example output:

```json
{
  "customer_name": "Rahul",
  "intent": "birthday_party",
  "location": "Whitefield",
  "participants": 35,
  "event_type": "Birthday Party",
  "sentiment": "neutral",
  "recommended_option": "Birthday Party Package",
  "summary": "Customer name is Rahul. Intent is birthday_party. Customer is interested in Birthday Party. Preferred location is Whitefield. Group size is approximately 35. Recommended option: Birthday Party Package."
}
```

## Next-Agent Routing

Routing is handled by `src/router.py`:

- Birthday party -> `birthday_booking_agent`
- Corporate event -> `corporate_events_agent`
- Virtual event -> `virtual_events_agent`
- Cancellation or refund -> `refunds_or_cancellations_agent`
- Escape room inquiry -> `booking_agent`
- General FAQ -> stay with `inbound_agent`

The router avoids handoff until most required intake fields are collected.

## Notes for Production Hardening

- Replace regex extraction with validated forms or a local NLU model if needed.
- Add branch-specific opening hours, parking, pricing, and game capacity to the knowledge files.
- Add a message queue or local event bus for the full 30-agent architecture.
- Add unit tests around intent detection, memory extraction, and routing before deploying.
