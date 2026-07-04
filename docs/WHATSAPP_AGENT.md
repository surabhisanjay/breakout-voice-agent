# Breakout WhatsApp Booking Agent

The WhatsApp agent is a channel adapter around the existing Breakout dispatcher. It does not maintain a second booking flow or knowledge base.

## Runtime flow

```text
Customer WhatsApp message
  -> WATI received-message webhook
  -> POST /wati/webhook
  -> WhatsAppAgentService
  -> existing InboundAgent / BookingAgent dispatcher
  -> WhatsAppResponseFormatter
  -> WATI sendSessionMessage
  -> customer
```

Each phone number maps to a stable, privacy-safe session ID. WATI message IDs are persisted under `memory/whatsapp_events` so webhook retries do not create duplicate replies.

## WATI configuration

Configure WATI to send inbound message events to:

```text
https://<public-host>/wati/webhook
```

Required environment variables:

```text
WHATSAPP_ENABLED=true
WHATSAPP_PROVIDER=wati
WHATSAPP_ACCESS_TOKEN=<WATI bearer token>
WHATSAPP_API_ENDPOINT=https://live-mt-server.wati.io/<tenant-id>
WHATSAPP_API_VERSION=v1
```

Recommended security variables:

```text
WHATSAPP_WEBHOOK_TOKEN=<shared webhook secret>
WHATSAPP_INTERNAL_API_KEY=<internal direct-message API key>
```

When `WHATSAPP_INTERNAL_API_KEY` is absent, `POST /whatsapp/messages` is disabled. The public WATI webhook remains available for configured inbound events.

## Endpoints

- `GET /whatsapp/health`: configuration status without exposing credentials.
- `POST /wati/webhook`: WATI inbound event receiver.
- `POST /whatsapp/messages`: protected internal message entry point.

The agent never confirms availability, payment, or booking from conversation state alone. Those statements are emitted only when the existing booking and payment integrations return the corresponding verified status.
