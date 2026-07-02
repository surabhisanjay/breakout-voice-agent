# Debugging the Vapi Integration

Start the API with `uvicorn app:app --host 0.0.0.0 --port 8000`, then run these checks.

## Health

```bash
curl -sS http://127.0.0.1:8000/health
```

Expected (the provider value reflects the environment):

```json
{"status":"ok","booking_provider":"live-configured","version":"1.1.0"}
```

## Payload capture

Point Vapi temporarily at `POST /debug`, make one call, and inspect the `DEBUG_REQUEST=` log line. The endpoint accepts and echoes any valid JSON.

```bash
curl -sS -X POST http://127.0.0.1:8000/debug \
  -H 'Content-Type: application/json' \
  -d '{"probe":{"nested":true}}'
```

Expected:

```json
{"received":{"probe":{"nested":true}}}
```

## Chat

```bash
curl -sS -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"test","message":"hello"}'
```

Expected shape:

```json
{"response":"<agent response text>"}
```

The server emits `RAW_REQUEST=`, `VALIDATION_ERROR=`, `PARSED_REQUEST=`, and `CHAT_RESPONSE=` diagnostics. A rejected call has both the submitted JSON and Pydantic's exact field locations in adjacent log lines.

## One-command verification

```bash
python scripts/verify_vapi_integration.py
```

Use `--base-url https://your-ngrok-host` to exercise the public route. Success ends with `SUMMARY: 3 passed, 0 failed`.
