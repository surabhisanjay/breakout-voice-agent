#!/usr/bin/env python3
import json
import os
import sys
import urllib.request
from urllib.error import HTTPError

def _load_env() -> None:
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

def main() -> None:
    _load_env()

    vapi_key = os.environ.get("VAPI_API_KEY")
    assistant_id = os.environ.get("VAPI_ASSISTANT_ID")

    print("=====================================================")
    print("  UPDATE VAPI ASSISTANT SERVER URL")
    print("=====================================================\n")

    if not vapi_key:
        print("Error: VAPI_API_KEY is not set in .env")
        sys.exit(1)
    if not assistant_id:
        print("Error: VAPI_ASSISTANT_ID is not set in .env")
        sys.exit(1)

    print(f"Loaded Vapi Assistant ID: {assistant_id}")
    print(f"Vapi API Key: {vapi_key[:8]}...\n")

    # Prompt user for the VPS base URL
    vps_url = input("Enter your VPS base URL (e.g., https://your-domain.com or http://IP:8000): ").strip()
    if not vps_url:
        print("VPS URL is required.")
        sys.exit(1)

    # Standardize the webhook URL
    vps_url = vps_url.rstrip("/")
    if not vps_url.endswith("/chat"):
        webhook_url = f"{vps_url}/chat"
    else:
        webhook_url = vps_url

    print(f"\nUpdating Vapi Assistant serverUrl to: {webhook_url} ...")

    # Call Vapi API to update assistant configuration
    payload = {
        "serverUrl": webhook_url
    }

    req = urllib.request.Request(
        f"https://api.vapi.ai/assistant/{assistant_id}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {vapi_key}",
            "Content-Type": "application/json"
        },
        method="PATCH"
    )

    try:
        with urllib.request.urlopen(req) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            print("✓ Success! Vapi Assistant Server URL has been updated.")
            print(f"  New Server URL: {res.get('serverUrl')}")
    except HTTPError as e:
        err_msg = e.read().decode("utf-8")
        print(f"✗ Failed to update assistant: {e.code} - {err_msg}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
