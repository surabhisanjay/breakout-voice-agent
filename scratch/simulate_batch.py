import os
import sys
import time
import json
import random
import requests
from datetime import datetime, timezone

# Add parent directory to path so we can import src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.services.wati_client import WatiClient

from dotenv import load_dotenv
load_dotenv()

BASE_URL = os.environ.get("CLOSIRA_BASE_URL", "http://139.84.146.101/api/v1")
AGENT_EMAIL = os.environ.get("CLOSIRA_EMAIL", "agent@closiro.com")
AGENT_PASSWORD = os.environ.get("CLOSIRA_PASSWORD", "Agent@Secure123!")
SECRET = os.environ.get("CLOSIRA_WEBHOOK_SECRET", "iamthegreatestofall707")

import hmac
import hashlib

def _sign(payload_bytes: bytes) -> str:
    h = hmac.new(SECRET.encode(), payload_bytes, hashlib.sha256)
    return "sha256=" + h.hexdigest()

def post_webhook(path: str, body: dict) -> requests.Response:
    payload_bytes = json.dumps(body, default=str).encode()
    ts = int(time.time())
    url = f"{BASE_URL}/webhooks/vapi{path}"
    return requests.post(
        url,
        data=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Vapi-Signature": _sign(payload_bytes),
            "X-Timestamp": str(ts),
        },
    )

def simulate_call(i: int):
    print(f"\n--- Simulating Call {i}/10 ---")
    uid = random.randint(1000, 9999)
    call_id = f"vapi-sim-batch-{uid}"
    session_id = f"session-sim-batch-{uid}"
    
    is_booking = (i % 2 == 0)  # Every even call is a booking (5 bookings out of 10)
    
    if is_booking:
        phone = "8217008407"
        first_name = "BookingTest"
        last_name = f"User{i}"
        intent = "booking"
        outcome = "booked"
        res_status = "resolved"
        
        conversation_history = [
            {"speaker": "assistant", "text": "Hello! Welcome to Breakout Escape Rooms.", "sequence": 1, "spoken_at_second": 0.0, "sentiment_score": 0.5},
            {"speaker": "customer",  "text": f"Hi, I'm {first_name} {last_name}. My number is {phone}. I want to book a room.", "sequence": 2, "spoken_at_second": 10.0, "sentiment_score": 0.6},
            {"speaker": "assistant", "text": "Great! Let's book the 6 PM slot for you.", "sequence": 3, "spoken_at_second": 20.0, "sentiment_score": 0.8},
        ]
        conversation_summary = {
            "one_line_summary": "Customer successfully booked.",
            "full_summary": f"Customer {first_name} {last_name} booked a room successfully.",
            "key_takeaways": ["Booking confirmed"],
            "action_items": ["Send confirmation"],
            "resolution_status": res_status,
            "intent": intent,
            "customer_frustration": False,
            "overall_sentiment": "positive",
            "sentiment_journey": [
                {"turn": 1, "sentiment": "Neutral", "stage": "discovery", "reason": "No strong sentiment signal detected."},
                {"turn": 2, "sentiment": "Positive", "stage": "booking", "reason": "Customer is ready to book."},
            ]
        }
        booking_payload = {
            "booking_id": f"BKG-SIM-{i}",
            "reference": f"REF-{uid}",
            "order_id": f"ORD-{uid}",
            "status": "reserved",
            "room": "Escape Room",
            "location": "Whitefield",
            "date": "Saturday",
            "time": "6 PM",
            "participants": 4,
            "price_breakdown": {}
        }
    else:
        phone = "8217008407"
        first_name = "GeneralTest"
        last_name = f"User{i}"
        intent = "general_inquiry"
        outcome = "inquiry"
        res_status = "in_progress"
        
        conversation_history = [
            {"speaker": "assistant", "text": "Hello! Welcome to Breakout Escape Rooms.", "sequence": 1, "spoken_at_second": 0.0, "sentiment_score": 0.5},
            {"speaker": "customer",  "text": "Hi, what are your timings?", "sequence": 2, "spoken_at_second": 10.0, "sentiment_score": 0.5},
            {"speaker": "assistant", "text": "We are open from 10 AM to 10 PM.", "sequence": 3, "spoken_at_second": 20.0, "sentiment_score": 0.5},
        ]
        conversation_summary = {
            "one_line_summary": "Customer asked for timings.",
            "full_summary": "Customer asked for timings and was informed.",
            "key_takeaways": ["Timing inquiry"],
            "action_items": [],
            "resolution_status": res_status,
            "intent": intent,
            "customer_frustration": False,
            "overall_sentiment": "neutral",
            "sentiment_journey": [
                {"turn": 1, "sentiment": "Neutral", "stage": "discovery", "reason": "No strong sentiment signal detected."},
            ]
        }
        booking_payload = {}
        
    print("1. Logging in...")
    r = requests.post(f"{BASE_URL}/auth/login", json={"email": AGENT_EMAIL, "password": AGENT_PASSWORD})
    if not r.ok:
        print(f"Login failed: {r.text}"); return
    token = r.json()["data"]["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    print("2. Creating contact and lead...")
    r = requests.post(f"{BASE_URL}/contacts", json={"first_name": first_name, "last_name": last_name, "phone_number": phone, "email": f"test{uid}@test.com", "contact_type": "individual"}, headers=headers)
    customer_id = r.json().get("data", {}).get("id") if r.ok else None
    
    r = requests.post(f"{BASE_URL}/leads", json={"customer_id": customer_id, "source": "ai_inbound", "title": f"Batch Lead {uid}"}, headers=headers)
    
    print("3. call-started...")
    post_webhook("/call-started", {
        "call_id": call_id,
        "session_id": session_id,
        "timestamp": int(time.time()),
        "direction": "inbound",
        "phone_number": phone,
        "caller_phone": phone,
        "org_id": 1,
    })
    
    print("4. call-ended...")
    post_webhook("/call-ended", {
        "call_id": call_id,
        "session_id": session_id,
        "timestamp": int(time.time()),
        "ended_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_seconds": 60,
        "outcome": outcome,
        "org_id": 1,
        "conversation_summary": conversation_summary,
        "conversation_history": conversation_history,
        "call_recording_reference": "",
        "booking": booking_payload,
        "payment": {},
    })
    
    if is_booking:
        print("5. Triggering WATI WhatsApp Message...")
        whatsapp_payload = {
            "customer_name": f"{first_name} {last_name}",
            "phone": phone,
            "booking_id": booking_payload["booking_id"],
            "room": booking_payload["room"],
            "location": booking_payload["location"],
            "date": booking_payload["date"],
            "time": booking_payload["time"],
            "payment_link": "https://kreeda.icu/pay/sim_batch",
            "booking_status": "reserved",
        }
        client = WatiClient()
        res = client.send_booking_payment_link(whatsapp_payload)
        print(f"WATI Result: Attempted={res.attempted}, Sent={res.sent}, Reason={res.reason}")

if __name__ == "__main__":
    for i in range(1, 11):
        simulate_call(i)
        time.sleep(1)
