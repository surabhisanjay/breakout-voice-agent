import os, sys, time, random, hmac, hashlib, json, requests

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL   = "http://139.84.146.101/api/v1"
SECRET     = "iamthegreatestofall707"
AGENT_EMAIL    = "manager@closiro.com"
AGENT_PASSWORD = "Manager@Secure123!"

def sign(payload_bytes: bytes) -> str:
    return hmac.new(SECRET.encode(), payload_bytes, hashlib.sha256).hexdigest()

def post_webhook(path: str, body: dict) -> requests.Response:
    payload_bytes = json.dumps(body).encode()
    sig = sign(payload_bytes)
    ts  = int(time.time())
    return requests.post(
        f"{BASE_URL}/webhooks/vapi{path}",
        data=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Vapi-Signature": sig,
            "X-Timestamp": str(ts),
        },
    )

def simulate():
    uid = random.randint(1000, 9999)
    call_id = f"vapi-sim-{uid}"
    session_id = f"session-sim-{uid}"
    phone = "8217008407"

    # ── 1. Login ──────────────────────────────────────────────────────────────
    print("1. Logging in...")
    r = requests.post(f"{BASE_URL}/auth/login",
                      json={"email": AGENT_EMAIL, "password": AGENT_PASSWORD})
    if not r.ok:
        print(f"Login failed: {r.text}"); return
    token = r.json()["data"]["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print(f"   Logged in ✓")

    # ── 2. Create contact + lead ───────────────────────────────────────────────
    print("2. Creating contact and lead...")
    r = requests.post(f"{BASE_URL}/contacts",
                      json={"first_name": f"Sim", "last_name": f"User{uid}",
                            "phone": phone, "email": f"sim{uid}@test.com",
                            "contact_type": "individual"},
                      headers=headers)
    print(f"   Contact response ({r.status_code}): {r.text[:300]}")
    customer_id = r.json().get("data", {}).get("id") if r.ok else None

    r = requests.post(f"{BASE_URL}/leads",
                      json={"customer_id": customer_id, "source": "ai_inbound",
                            "title": f"Sim Lead {uid}"},
                      headers=headers)
    print(f"   Lead response ({r.status_code}): {r.text[:300]}")
    lead_id = r.json().get("data", {}).get("id") if r.ok else None
    print(f"   Contact ID: {customer_id}, Lead ID: {lead_id}")


    # ── 3. call-started ────────────────────────────────────────────────────────
    print("3. Triggering call-started...")
    r = post_webhook("/call-started", {
        "call_id": call_id,
        "session_id": session_id,
        "timestamp": int(time.time()),
        "direction": "inbound",
        "phone_number": phone,
        "caller_phone": phone,
        "agent_id": "1",
        "org_id": 1,
        "customer_id": customer_id,
        "lead_id": lead_id,
    })
    if not r.ok:
        print(f"   call-started failed: {r.text}"); return
    print(f"   call-started ✓  →  {r.json()}")

    # ── 4. Simulate call happening (sleep) ─────────────────────────────────────
    print("4. Simulating conversation (3s)...")
    time.sleep(3)

    # ── 5. call-ended with full consolidated payload ───────────────────────────
    print("5. Triggering call-ended with full sentiment + transcript...")

    conversation_history = [
        {"speaker": "assistant", "text": "Hello! Welcome to Breakout Escape Rooms. How can I help you today?", "sequence": 1, "spoken_at_second": 0.0, "sentiment_score": 0.5},
        {"speaker": "customer",  "text": "Hi, I'd like to book an escape room for this weekend.", "sequence": 2, "spoken_at_second": 8.0, "sentiment_score": 0.6},
        {"speaker": "assistant", "text": "Absolutely! Which branch would you prefer — Whitefield or Koramangala?", "sequence": 3, "spoken_at_second": 16.0, "sentiment_score": 0.5},
        {"speaker": "customer",  "text": "Your website is really slow, I've been going in circles and I'm getting frustrated!", "sequence": 4, "spoken_at_second": 24.0, "sentiment_score": 0.2},
        {"speaker": "assistant", "text": "I'm very sorry about that. Let me check availability for you right now.", "sequence": 5, "spoken_at_second": 32.0, "sentiment_score": 0.5},
        {"speaker": "customer",  "text": "Actually it just loaded. That's perfect! Let's book the 6 PM slot for 4 people.", "sequence": 6, "spoken_at_second": 40.0, "sentiment_score": 0.8},
        {"speaker": "assistant", "text": "Great! I've reserved the 6 PM slot for 4 people at Whitefield this Saturday.", "sequence": 7, "spoken_at_second": 48.0, "sentiment_score": 0.5},
    ]

    conversation_summary = {
        "one_line_summary": "Customer booked 6 PM escape room slot for 4 people at Whitefield.",
        "full_summary": "Customer called to book an escape room for the weekend. Expressed frustration with website speed mid-call but recovered. Successfully booked the 6 PM slot for 4 people at Whitefield branch.",
        "key_takeaways": ["00:08 Booking confirmed for Whitefield 6 PM Saturday", "00:32 4 people", "00:24 Website UX issue flagged"],
        "action_items": ["Send booking confirmation SMS", "Flag website issue to tech team"],
        "resolution_status": "resolved",
        "intent": "booking",
        "customer_frustration": True,
        "overall_sentiment": "negative",
        "follow_up_recommendation": "Send confirmation and payment link within 10 minutes.",
        "sentiment_journey": [
            {"turn": 1, "sentiment": "Neutral", "stage": "discovery", "reason": "No strong sentiment signal detected."},
            {"turn": 2, "sentiment": "Neutral", "stage": "booking", "reason": "No strong sentiment signal detected."},
            {"turn": 3, "sentiment": "Frustrated", "stage": "booking", "reason": "Customer reported repeated misunderstanding."},
            {"turn": 4, "sentiment": "Positive", "stage": "booking", "reason": "Customer is ready to book."},
            {"turn": 5, "sentiment": "Positive", "stage": "booking", "reason": "No strong sentiment signal detected."}
        ]
    }

    r = post_webhook("/call-ended", {
        "call_id": call_id,
        "session_id": session_id,
        "timestamp": int(time.time()),
        "ended_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_seconds": 95,
        "outcome": "booked",
        "org_id": 1,
        "conversation_summary": conversation_summary,
        "conversation_history": conversation_history,
        "call_recording_reference": "",
        "booking": {
            "booking_id": "BKG-9999",
            "reference": "REF-ABC12",
            "order_id": "ORD-555",
            "status": "reserved",
            "room": "Escape Room",
            "location": "Whitefield",
            "date": "Saturday",
            "time": "6 PM",
            "participants": 4,
            "price_breakdown": {}
        },
        "payment": {},
    })

    if r.ok:
        data = r.json()
        print(f"\n{'='*60}")
        print(f"  ✅ SIMULATION COMPLETE!")
        print(f"  Call ID (VAPI): {call_id}")
        print(f"  Internal Call ID: {data.get('id')}")
        print(f"  Summary persisted: {data.get('summary_persisted')}")
        print(f"  Transcript lines: {data.get('transcript_lines')}")
        print(f"\n  👉 Open http://139.84.146.101/ → Calls")
        print(f"     Find call with phone: {phone}")
        print(f"     Check the Summary tab for sentiment + frustration badge!")
        print(f"{'='*60}\n")
    else:
        print(f"   call-ended failed ({r.status_code}): {r.text}")

if __name__ == "__main__":
    simulate()
