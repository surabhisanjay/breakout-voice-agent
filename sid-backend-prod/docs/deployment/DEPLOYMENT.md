# Breakout Agent — Deployment Guide

**Version:** 1.1.0

---

## Table of Contents

1. [Local Development](#1-local-development)
2. [Environment Variables Reference](#2-environment-variables-reference)
3. [VPS Deployment](#3-vps-deployment)
4. [Redis (Future)](#4-redis-future)
5. [PostgreSQL (Future)](#5-postgresql-future)
6. [WATI Configuration](#6-wati-configuration)
7. [Vapi Configuration](#7-vapi-configuration)
8. [Kreeda Configuration](#8-kreeda-configuration)
9. [Closiro Configuration](#9-closiro-configuration)
10. [Webhook Configuration](#10-webhook-configuration)
11. [Production Deployment Checklist](#11-production-deployment-checklist)
12. [Monitoring](#12-monitoring)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Local Development

### Prerequisites

- Python 3.9+
- macOS / Linux (Windows via WSL)
- `brew install portaudio` (macOS, for voice mode only)

### Setup

```bash
# 1. Clone and enter the repository
cd Breakout-Agent

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env with your credentials

# 5. Create runtime directories
mkdir -p memory/api_sessions logs/conversations

# 6. Start development server
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

### Verify Startup

```bash
curl http://localhost:8000/health
```

Expected:
```json
{"status": "ok", "booking_provider": "live-configured", "version": "1.1.0"}
```

### Run Tests

```bash
python -m pytest tests -q
```

### Text Mode (CLI)

```bash
python main.py --debug
```

### Voice Mode (Local)

```bash
python main.py --voice
```

### Flags

| Flag | Description |
|---|---|
| `--debug` | Print full debug state each turn |
| `--no-openai` | Use deterministic fallback, skip OpenAI |
| `--reset-memory` | Clear session memory at startup |
| `--voice` | Use Whisper STT + pyttsx3 TTS |
| `--model MODEL` | Override default OpenAI model |

---

## 2. Environment Variables Reference

Copy `.env.example` to `.env` and fill in all values.

### OpenAI

```bash
OPENAI_API_KEY=<openai-api-key>  # Required for AI responses
OPENAI_MODEL=gpt-4.1-mini        # Override model (optional)
```

> **Note:** If `OPENAI_API_KEY` is not set, the backend uses deterministic fallback responses. All conversation logic still works.

### Kreeda (Booking)

```bash
BOOKING_BASE_URL=https://bs.kreeda.icu   # Kreeda API base URL
BOOKING_API_KEY=<kreeda-api-key>         # Kreeda authentication key
BOOKING_PROVIDER=auto                    # auto | simulator
```

`BOOKING_PROVIDER=auto` → Uses live Kreeda if credentials are set; falls back to simulator.
`BOOKING_PROVIDER=simulator` → Always uses offline simulator (for development).

### WATI (WhatsApp)

```bash
WATI_BASE_URL=https://live-server.wati.io   # WATI API URL
API_VERSION=v1
WATI_ACCESS_TOKEN=<wati-access-token>
WATI_TIMEOUT_SECONDS=10
WATI_MAX_ATTEMPTS=3
WATI_SEND_PATH=/api/{api_version}/sendSessionMessage/{phone}
WATI_TEMPLATE_ID=your-template-id
WATI_TEMPLATE_PATH=/api/v2/sendTemplateMessage
WATI_BROADCAST_NAME=booking_payment
WATI_SENDER_NUMBER=91XXXXXXXXXX
```

### Closiro CRM

```bash
CLOSIRO_DEFAULT_ORG_ID=org_test        # Organization ID
CLOSIRO_DEFAULT_AGENT_ID=1             # Default agent assignment
```

### Demo / Scripts

```bash
DEMO_CUSTOMER_NAME=                    # Used by scripts/demo_runner.py only
DEMO_CUSTOMER_PHONE=                   # Used by scripts/demo_runner.py only
DEMO_MODE=false                        # Set true for demo mode (disables premature booking)
```

### Logging

```bash
BREAKOUT_LOG_LEVEL=INFO               # DEBUG | INFO | WARNING | ERROR
```

---

## 3. VPS Deployment

### Recommended Specs

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 1 vCPU | 2 vCPU |
| RAM | 512 MB | 1 GB |
| Disk | 5 GB | 20 GB |
| OS | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |

### Setup on Ubuntu VPS

```bash
# 1. Update system
sudo apt update && sudo apt upgrade -y

# 2. Install Python 3.11
sudo apt install -y python3.11 python3.11-venv python3-pip git

# 3. Clone repository
git clone https://github.com/your-org/breakout-agent.git
cd breakout-agent/Breakout-Agent

# 4. Create virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 5. Install dependencies
pip install -r requirements.txt

# 6. Configure environment
cp .env.example .env
nano .env   # Add all credentials

# 7. Create runtime directories
mkdir -p memory/api_sessions logs/conversations

# 8. Run tests
python -m pytest tests -q

# 9. Start with uvicorn (development)
uvicorn app:app --host 0.0.0.0 --port 8000
```

### Production with Gunicorn + Nginx

```bash
# Install gunicorn
pip install gunicorn

# Start with gunicorn (4 workers)
gunicorn app:app \
  -w 4 \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --access-logfile - \
  --error-logfile -
```

### Systemd Service

Create `/etc/systemd/system/breakout-agent.service`:

```ini
[Unit]
Description=Breakout Agent API
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/breakout-agent/Breakout-Agent
Environment=PATH=/home/ubuntu/breakout-agent/Breakout-Agent/.venv/bin
EnvironmentFile=/home/ubuntu/breakout-agent/Breakout-Agent/.env
ExecStart=/home/ubuntu/breakout-agent/Breakout-Agent/.venv/bin/gunicorn app:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable breakout-agent
sudo systemctl start breakout-agent
sudo systemctl status breakout-agent
```

### Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection keep-alive;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }
}
```

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

---

## 4. Redis (Future)

Redis is not currently required. It is planned for:
- Distributed session management (multi-instance deployment)
- Rate limiting
- Task queue for async processing

When ready, add:
```bash
REDIS_URL=<redis-url>
```

---

## 5. PostgreSQL (Future)

PostgreSQL is not currently required. The current MVP uses:
- File-based JSON memory (`memory/api_sessions/`)
- In-memory Closiro CRM store (`ClosiraStore` in `app.py`)

When ready to migrate:
```bash
DATABASE_URL=<database-url>
```

---

## 6. WATI Configuration

### Step 1: Get WATI Credentials

1. Sign up at [wati.io](https://wati.io)
2. Complete WhatsApp Business verification
3. Get your API access token from WATI dashboard
4. Get your WATI API URL (e.g., `https://live-server.wati.io`)

### Step 2: Set Environment Variables

```bash
WATI_BASE_URL=https://live-server.wati.io
WATI_ACCESS_TOKEN=<wati-access-token>
WATI_SENDER_NUMBER=91XXXXXXXXXX
```

### Step 3: Configure Webhook

In WATI dashboard:
- Go to Settings → Webhooks
- Set Webhook URL to: `https://your-domain.com/webhooks/wati`
- Enable: Message Received events

### Step 4: Create Payment Template

Create a WhatsApp message template in WATI for payment links:
- Template name: `booking_payment`
- Set `WATI_TEMPLATE_ID` and `WATI_BROADCAST_NAME` in `.env`

### Step 5: Test

```bash
# Send a test message to your WhatsApp number
# Verify the backend receives it and responds
```

---

## 7. Vapi Configuration

### Voice Integration

Vapi handles STT (speech-to-text) and TTS (text-to-speech) for voice calls. The backend is stateless from Vapi's perspective.

### Step 1: Configure Vapi Assistant

In Vapi dashboard:
1. Create a new assistant
2. Set "Server URL" to: `https://your-domain.com/chat`
3. Set the message format to: `{ "session_id": "{call_id}", "message": "{transcript}" }`

### Step 2: Voice Persona

The voice persona is defined in `prompts/breakout_personality_prompt.txt`. Adjust tone and style there.

### Step 3: Test

```bash
# Make a test call through Vapi
# Verify /chat endpoint receives transcripts
# Verify responses are returned
```

---

## 8. Kreeda Configuration

### Step 1: Get API Credentials

Contact the Kreeda team at `bs.kreeda.icu` for:
- API key
- Allowed endpoint list
- Webhook configuration (for payment confirmation)

### Step 2: Configure

```bash
BOOKING_BASE_URL=https://bs.kreeda.icu
BOOKING_API_KEY=<kreeda-api-key>
BOOKING_PROVIDER=auto
```

### Step 3: Verify Connection

```bash
# Startup log should show:
# BOOKING_PROVIDER=live-configured
# Kreeda provider initialized successfully
```

### Step 4: Test Booking

1. Start a web chat session
2. Ask to book an escape room
3. Provide all required details
4. Confirm a slot is returned from Kreeda
5. Verify booking appears in Kreeda dashboard

---

## 9. Closiro Configuration

```bash
CLOSIRO_DEFAULT_ORG_ID=org_your_id     # Match your Closiro org
CLOSIRO_DEFAULT_AGENT_ID=1              # Default agent for assignments
```

### JWT Generation (for testing)

Create a test JWT payload:

```json
{
  "sub": 1,
  "user_id": 1,
  "agent_id": 1,
  "org_id": "org_your_id",
  "role": "sales_manager",
  "name": "Test Manager"
}
```

Base64url-encode each part and join with dots. The signature part can be empty for testing:

```python
import base64, json
payload = {"sub": 1, "user_id": 1, "agent_id": 1, "org_id": "org_test", "role": "sales_manager"}
encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
token = f"eyJhbGciOiJub25lIn0.{encoded}."
```

---

## 10. Webhook Configuration

### WATI Webhook

| Setting | Value |
|---|---|
| URL | `https://your-domain.com/webhooks/wati` |
| Method | POST |
| Events | Message received |
| Format | JSON |

### Vapi Webhook

| Setting | Value |
|---|---|
| URL | `https://your-domain.com/chat` |
| Method | POST |
| Format | `{"session_id": "{call_id}", "message": "{transcript}"}` |

### Kreeda Payment Webhook (External — Kreeda Side)

Kreeda should be configured to POST payment confirmations to your backend. This webhook is managed by Kreeda, not this backend. Confirm setup with the Kreeda team.

---

## 11. Production Deployment Checklist

### Environment Setup

- [ ] `OPENAI_API_KEY` set and tested
- [ ] `BOOKING_BASE_URL` set to `https://bs.kreeda.icu`
- [ ] `BOOKING_API_KEY` set and tested
- [ ] `BOOKING_PROVIDER=auto` set
- [ ] `WATI_BASE_URL` set
- [ ] `WATI_ACCESS_TOKEN` set
- [ ] `WATI_SENDER_NUMBER` set
- [ ] `WATI_TEMPLATE_ID` set
- [ ] `CLOSIRO_DEFAULT_ORG_ID` set
- [ ] `CLOSIRO_DEFAULT_AGENT_ID` set
- [ ] `BREAKOUT_LOG_LEVEL=INFO`

### Infrastructure

- [ ] HTTPS enabled (SSL certificate)
- [ ] `memory/api_sessions/` directory created and writable
- [ ] `logs/conversations/` directory created and writable
- [ ] Systemd service created and enabled
- [ ] Nginx configured and running
- [ ] Firewall allows ports 80, 443

### Webhook Setup

- [ ] WATI webhook configured: `https://your-domain.com/webhooks/wati`
- [ ] Vapi server URL configured: `https://your-domain.com/chat`
- [ ] Kreeda payment webhook configured (with Kreeda team)

### Testing

- [ ] `python -m pytest tests -q` — all pass
- [ ] Health check: `curl https://your-domain.com/health`
- [ ] Web chat end-to-end test (inquiry → recommendation → booking → payment link)
- [ ] WhatsApp message test (send message → verify response received)
- [ ] Kreeda booking test (verify in Kreeda dashboard)
- [ ] Escalation test (verify escalation triggers correctly)
- [ ] Closiro CRM test (verify call records appear in `/api/v1/calls`)

---

## 12. Monitoring

### Health Endpoint

```bash
curl https://your-domain.com/health
```

Poll every 60 seconds. Alert if status is not `ok` or if response time exceeds 5 seconds.

### Log Monitoring

```bash
# Watch service logs
sudo journalctl -u breakout-agent -f

# Filter for errors
sudo journalctl -u breakout-agent | grep ERROR

# Monitor booking events
sudo journalctl -u breakout-agent | grep "BOOKING_"
```

### Key Log Events

| Log Key | Description |
|---|---|
| `BOOKING_PROVIDER=` | Startup: booking provider mode |
| `BOOKING_AGENT_SELECTED` | Booking flow started |
| `BOOKING_STARTED=true` | Booking workflow active |
| `WATI_INBOUND_WEBHOOK=` | WhatsApp message received |
| `WATI_WEBHOOK_RESPONSE=` | WhatsApp response sent |
| `RAW_REQUEST=` | Raw chat request received |
| `CHAT_RESPONSE=` | Chat response sent |

---

## 13. Troubleshooting

### "BOOKING_PROVIDER=simulator" in startup log

**Cause:** `BOOKING_API_KEY` or `BOOKING_BASE_URL` not set.
**Fix:** Add both to `.env`.

### OpenAI not responding

**Symptom:** Responses are deterministic/flat, no personality.
**Fix:** Check `OPENAI_API_KEY`. The agent falls back gracefully, so functionality is not broken.

### WATI webhook not receiving messages

**Check:**
1. Is the webhook URL accessible from the internet? (`curl https://your-domain.com/webhooks/wati`)
2. Is HTTPS configured? (WATI requires HTTPS)
3. Is the event type correct in WATI settings?

### Tests failing after cleanup

**Fix:** Run `python -m pytest tests -q --tb=short` to identify specific failures.
Common cause: import paths expecting files that have moved. Check that no `src/` files were moved.

### Memory not persisting between server restarts

**Cause:** `memory/api_sessions/` directory doesn't exist or isn't writable.
**Fix:** `mkdir -p memory/api_sessions && chmod 755 memory/api_sessions`

### Session isolation issues (multiple users sharing state)

**Check:** Each client must send a unique `session_id` per user/session.

### Port 8000 already in use

```bash
lsof -i :8000
kill -9 <PID>
```
