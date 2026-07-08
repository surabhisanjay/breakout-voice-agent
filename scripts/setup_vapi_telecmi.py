#!/usr/bin/env python3
"""
scripts/setup_vapi_telecmi.py
-----------------------------
Automates the configuration of Vapi for TeleCMI SIP BYOC integration.

This script:
1. Reads VAPI_API_KEY and VAPI_ASSISTANT_ID from .env.
2. Prompts the user for their TeleCMI SIP SBC Gateway and Phone Number.
3. Call Vapi API to create a Custom SIP Trunk Credential.
4. Call Vapi API to register/import the TeleCMI Phone Number and bind it to the Assistant.
5. Outputs the final routing SIP URI for the TeleCMI dashboard.
"""

from __future__ import annotations

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
    print("  VAPI + TELECMI SIP BYOC AUTOMATED PROVISIONER")
    print("=====================================================\n")

    if not vapi_key:
        print("Error: VAPI_API_KEY is not set in .env")
        sys.exit(1)
    if not assistant_id:
        print("Error: VAPI_ASSISTANT_ID is not set in .env")
        sys.exit(1)

    print(f"Detected Vapi Assistant ID: {assistant_id}")
    print(f"Vapi API Key loaded: {vapi_key[:8]}...\n")

    # Prompt user for specific TeleCMI details
    print("Please enter your TeleCMI connection details:")
    sbc_host = input("1. TeleCMI SBC Domain / IP (e.g. sbc.telecmi.com or IP): ").strip()
    if not sbc_host:
        print("SBC Domain is required.")
        sys.exit(1)

    phone_number = input("2. Your TeleCMI Phone Number (in E.164 format, e.g. +91XXXXXXXXXX): ").strip()
    if not phone_number:
        print("Phone number is required.")
        sys.exit(1)

    sip_user = input("3. SIP Username (default: TeleCMI App ID 3f2abcac-0b4d-4a82-81de-00023773ddaf): ").strip()
    if not sip_user:
        sip_user = "3f2abcac-0b4d-4a82-81de-00023773ddaf"

    sip_pass = input("4. SIP Password (default: TeleCMI App ID 3f2abcac-0b4d-4a82-81de-00023773ddaf): ").strip()
    if not sip_pass:
        sip_pass = "3f2abcac-0b4d-4a82-81de-00023773ddaf"

    print("\n--- Creating Vapi SIP Trunk Credential ---")
    
    # Base configuration for credential creation
    cred_payload = {
        "provider": "byo-sip-trunk",
        "name": f"TeleCMI SIP Trunk ({phone_number})",
        "gateways": [
            {
                "ip": sbc_host
            }
        ],
        "outboundAuthenticationPlan": {
            "authUsername": sip_user,
            "authPassword": sip_pass
        }
    }

    req = urllib.request.Request(
        "https://api.vapi.ai/credential",
        data=json.dumps(cred_payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {vapi_key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req) as resp:
            cred_res = json.loads(resp.read().decode("utf-8"))
            cred_id = cred_res.get("id")
            print(f"✓ Created Vapi Credential! ID: {cred_id}")
    except HTTPError as e:
        err_msg = e.read().decode("utf-8")
        print(f"✗ Failed to create credential: {e.code} - {err_msg}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)

    print("\n--- Registering TeleCMI Phone Number and Mapping Assistant ---")

    phone_payload = {
        "provider": "byo-phone-number",
        "name": f"TeleCMI - {phone_number}",
        "number": phone_number,
        "credentialId": cred_id,
        "assistantId": assistant_id
    }

    req_phone = urllib.request.Request(
        "https://api.vapi.ai/phone-number",
        data=json.dumps(phone_payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {vapi_key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req_phone) as resp:
            phone_res = json.loads(resp.read().decode("utf-8"))
            print(f"✓ Imported Phone Number! Vapi Phone ID: {phone_res.get('id')}")
    except HTTPError as e:
        err_msg = e.read().decode("utf-8")
        print(f"✗ Failed to import phone number: {e.code} - {err_msg}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)

    print("\n=====================================================")
    print("  CONFIGURATION COMPLETE!")
    print("=====================================================")
    print(f"Next steps on the TeleCMI/PIOPIY portal:")
    print(f"1. Map your TeleCMI DID ({phone_number}) to forward to SIP Destination.")
    print(f"2. Route traffic to the following Vapi SIP endpoint URI:")
    print(f"   SIP URI: sip:{phone_number}@sip.vapi.ai")
    print("=====================================================\n")


if __name__ == "__main__":
    main()
