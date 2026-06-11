# Breakout Inbound Agent MVP

OpenAI-powered production-style MVP for Agent #1 in a multi-agent AI call center for Breakout Escape Rooms.

This is not a chatbot. It is an inbound intake agent that greets customers, detects intent, answers basic company questions, recommends suitable rooms or packages, collects missing details, generates a structured handoff summary, and routes to the next agent.

## Stack

- Python
- OpenAI Responses API
- GPT-5 mini by default
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
│   └── inbound_prompt.txt
├── memory/
│   └── session.json
├── src/
│   ├── knowledge_loader.py
│   ├── intent_detector.py
│   ├── recommendation_engine.py
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
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-5-mini
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

## Tests

Run the automated test suite:

```bash
python -m pytest tests -q
```

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
Sure. Murder Mystery or Hostage would be a good fit. They are suitable for children aged 9+ and work well for beginner groups. Got it. Could you share your name and a phone number?
```

### Adult Challenge Recommendation

Customer:

```text
We are adults looking for a challenging room.
```

Agent:

```text
Sure. Classified, Undercover, Prison Break, or Bomb Defusal would be a good fit. These are popular choices for adults looking for a more challenging experience. Got it. Could you share your name and a phone number?
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
