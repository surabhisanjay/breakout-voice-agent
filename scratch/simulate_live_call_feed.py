import time
import httpx
import random
import hmac
import hashlib
import json
from datetime import datetime, timezone

def sign_payload(payload: dict, secret: str = "iamthegreatestofall707") -> tuple[bytes, str]:
    payload_bytes = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    signature = hmac.new(secret.encode('utf-8'), payload_bytes, hashlib.sha256).hexdigest()
    return payload_bytes, signature

def simulate():
    base_url = "http://localhost:8082/api/v1"
    
    # 1. Login to get token
    print("1. Logging in as agent...")
    login_resp = httpx.post(
        f"{base_url}/auth/login",
        json={"email": "agent@closiro.com", "password": "Agent@Secure123!"}
    )
    if login_resp.status_code != 200:
        print(f"Login failed: {login_resp.text}")
        return
    token = login_resp.json()["data"]["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    
    # Extract agent ID from JWT token payload using base64
    import base64
    payload_part = token.split(".")[1]
    payload_part += "=" * ((4 - len(payload_part) % 4) % 4)
    decoded_payload = json.loads(base64.urlsafe_b64decode(payload_part).decode("utf-8"))
    agent_user_id = decoded_payload.get("nameid") or decoded_payload.get("sub") or "2"
    print(f"Decoded agent user ID: {agent_user_id}")
    
    # 2. Create a contact and a lead to associate with the call
    print("2. Setting up contact and lead...")
    suffix = random.randint(1000, 9999)
    contact_resp = httpx.post(
        f"{base_url}/contacts",
        json={
            "first_name": "LiveStream",
            "last_name": f"User-{suffix}",
            "phone": f"888888{suffix}",
            "email": f"livestream.user{suffix}@example.com",
            "type": "contact"
        },
        headers=headers
    )
    contact_id = contact_resp.json()["data"]["id"]
    
    # 3. Create the call record (started)
    print("3. Triggering call-started...")
    session_id = f"session_live_{suffix}"
    
    start_payload = {
        "call_id": suffix,
        "session_id": session_id,
        "phone_number": f"888888{suffix}",
        "agent_id": str(agent_user_id),
        "customer_id": contact_id,
        "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "org_id": 1
    }
    
    payload_bytes, signature = sign_payload(start_payload)
    
    webhook_headers = {
        "X-Vapi-Signature": signature,
        "Content-Type": "application/json"
    }
    
    start_resp = httpx.post(
        f"{base_url}/webhooks/vapi/call-started",
        content=payload_bytes,
        headers=webhook_headers
    )
    if start_resp.status_code != 200:
        print(f"Call start failed: {start_resp.text}")
        return
        
    call_id = suffix
    print(f"\n=======================================================")
    print(f"  LIVE SIMULATION STARTED!")
    print(f"  - Call ID: {call_id}")
    print(f"  - Open your CRM dashboard at http://139.84.146.101/ ")
    print(f"    and check the live calls list/active agents module!")
    print(f"=======================================================\n")
    
    # 4. Feed transcript turns in real-time with delays
    turns = [
        {"role": "user", "text": "Hello, is this Breakout Escape Rooms?"},
        {"role": "assistant", "text": "Yes it is! I can help you book an escape room. Which branch would you prefer?"},
        {"role": "user", "text": "I'd like to book for Whitefield. But your website is really slow, I am getting frustrated and going in circles."},
        {"role": "assistant", "text": "I'm very sorry about the delay. Let me check the slots for you right now."},
        {"role": "user", "text": "Actually, it just loaded. Thank you, that is perfect! Let's book the 6 PM slot."}
    ]
    
    for idx, turn in enumerate(turns):
        print(f"Feeding turn {idx+1}/{len(turns)} ({turn['role']}): '{turn['text']}'")
        
        realtime_payload = {
            "message": {
                "type": "transcript",
                "role": turn["role"],
                "transcript": turn["text"],
                "call": {
                    "id": str(call_id),
                    "sessionId": session_id
                }
            }
        }
        
        turn_bytes, turn_sig = sign_payload(realtime_payload)
        turn_headers = {
            "X-Vapi-Signature": turn_sig,
            "Content-Type": "application/json"
        }
        
        resp = httpx.post(
            f"{base_url}/webhooks/vapi/realtime",
            content=turn_bytes,
            headers=turn_headers
        )
        if resp.status_code != 200:
            print(f"  ✗ Failed to send turn: {resp.text}")
        time.sleep(5.0)  # Wait 5 seconds so they can see it populate
        
    # 5. End the call
    print("\n5. Ending call...")
    end_payload = {
        "call_id": call_id,
        "session_id": session_id,
        "ended_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "duration_seconds": 30,
        "outcome": "completed",
        "org_id": 1
    }
    end_bytes, end_sig = sign_payload(end_payload)
    end_headers = {
        "X-Vapi-Signature": end_sig,
        "Content-Type": "application/json"
    }
    
    httpx.post(
        f"{base_url}/webhooks/vapi/call-ended",
        content=end_bytes,
        headers=end_headers
    )
    print("Simulation completed successfully!")

if __name__ == "__main__":
    simulate()
