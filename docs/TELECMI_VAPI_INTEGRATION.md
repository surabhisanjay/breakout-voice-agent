# TeleCMI + Vapi Live Voice Testing Integration Guide

This guide outlines how to integrate **TeleCMI** with **Vapi** using **SIP Bring Your Own Carrier (BYOC)**. This allows you to route live telephone calls to your TeleCMI business number directly to the Breakout Voice Agent backend.

---

## Architecture Overview

```
[Customer Phone]
      │
      ▼ (PSTN Call)
  [TeleCMI]
      │
      ▼ (SIP BYOC Trunk)
   [Vapi] (Handles Audio Streaming, STT, and TTS)
      │
      ▼ (HTTP POST /chat Webhook)
[FastAPI Backend] (Processes agent logic/state)
```

---

## Step-by-Step Configuration

### Step 1: Set Up SIP Credentials in TeleCMI
1. Log in to your **TeleCMI CHUB Dashboard**.
2. Create a new **SIP Endpoint/Trunk** to route incoming calls to Vapi.
3. Obtain your connection details:
   - **SBC URL / SIP Domain** (e.g., `sbc.telecmi.com` or similar gateway).
   - **SIP Username** and **Password** (used for authentication).

### Step 2: Configure the SIP Trunk in Vapi
1. Log in to the **Vapi Dashboard** (https://dashboard.vapi.ai).
2. Go to **SIP Trunks** or **Integrations** in the left menu.
3. Click **Add Trunk** (or "Configure New SIP Trunk").
4. Enter the details gathered from TeleCMI:
   - **SBC Gateway / SIP URI**: Your TeleCMI SIP domain.
   - **Authentication Type**: Username/Password.
   - **Username**: Your TeleCMI SIP username.
   - **Password**: Your TeleCMI SIP password.
5. Save the SIP Trunk.

### Step 3: Map your TeleCMI Phone Number to Vapi
1. Go to **Phone Numbers** in the Vapi dashboard.
2. Click **Import** (or "Add BYO Number").
3. Select the TeleCMI SIP Trunk you configured.
4. Enter your TeleCMI DID (Phone Number) in standard E.164 format (e.g., `+91XXXXXXXXXX`).
5. Select the target **Vapi Assistant** that represents your Breakout Voice Agent.

### Step 4: Expose and Connect Your Local Backend
Vapi must be able to communicate with your FastAPI backend `/chat` endpoint.
1. Run **ngrok** to create a public HTTPS tunnel to your local FastAPI server (default port `8000`):
   ```bash
   ngrok http 8000
   ```
2. Copy the forwarding URL (e.g., `https://xxxx-xxxx.ngrok-free.app`).
3. In Vapi, go to your **Assistant Settings**.
4. Set the **Server URL** (Webhook) to:
   ```
   https://xxxx-xxxx.ngrok-free.app/chat
   ```
5. Ensure your local backend is running:
   ```bash
   .venv/bin/python main.py
   ```

### Step 5: Test the Live Call
1. Dial your TeleCMI phone number from any mobile device.
2. The call will route through TeleCMI to Vapi.
3. Vapi will invoke your local backend `/chat` endpoint to generate dynamic agent responses in real time!
