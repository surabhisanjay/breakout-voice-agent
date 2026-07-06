# Vapi Integration Root Cause

## Confirmed backend contract

`POST /chat` accepts exactly one JSON object with two required, non-null string fields:

```json
{
  "session_id": "test",
  "message": "hello"
}
```

`session_id` must contain 1-80 characters and, after schema validation, is restricted to letters, numbers, underscore, dash, dot, and colon. `message` must contain at least one character and cannot be whitespace-only. Pydantic ignores additional fields; tests pin that compatibility behavior.

Use this JSON Schema for the Vapi request body:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["session_id", "message"],
  "properties": {
    "session_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 80,
      "pattern": "^[A-Za-z0-9_.:-]{1,80}$"
    },
    "message": {"type": "string", "minLength": 1}
  }
}
```

The tool must send the arguments as the top-level HTTP JSON body, with `Content-Type: application/json`. Do not wrap them in `message`, `arguments`, `function`, or `toolCallList`.

## Actual Vapi payload and 422

The prior request body was not retained in this repository, and neither the ngrok inspector nor the backend terminal was available during this change. Therefore its exact shape cannot honestly be reconstructed after the fact. The confirmed cause category is request-body schema validation: FastAPI returned 422 before `chat()` ran because the received body did not satisfy `ChatRequest`.

The precise mismatched fields are now self-diagnosing. Send one Vapi request to `/chat` and compare the adjacent `RAW_REQUEST=` and `VALIDATION_ERROR=` lines, or point it once at `/debug` and inspect `DEBUG_REQUEST=`. Paste that captured body into this section if a permanent incident record is required.

## Fix applied

- Raw `/chat` JSON is logged before model validation.
- Exact validation errors, the parsed request, and the response are logged.
- `/debug` accepts and echoes arbitrary JSON for Vapi inspection.
- `/health` reports status, booking provider, and API version.
- Startup diagnostics print the OpenAI state, provider, prompt/example load state, and generated `ChatRequest` schema.
- Contract tests pin missing, null, and extra-field behavior.
- `scripts/verify_vapi_integration.py` verifies all public integration endpoints.

No booking, recommendation, qualification, memory, session, sentiment, escalation, handoff, or prompt behavior was changed.

## Working response

The exact text depends on conversation state, but the response contract is:

```json
{
  "response": "<agent response text>"
}
```

Run `python scripts/verify_vapi_integration.py --base-url https://your-ngrok-host` after configuring Vapi. A successful call logs `PARSED_REQUEST=` followed by `CHAT_RESPONSE=` and does not emit `VALIDATION_ERROR=`.
