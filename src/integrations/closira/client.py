"""
Closira CRM integration client.

Handles authentication with JWT token and sends requests to Closira CRM API.
Endpoints are based on the Closira CRM AI Integration Specifications:
- http://139.84.146.101/api/v1
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional
import httpx

logger = logging.getLogger(__name__)


class ClosiraCRMClient:
    """Client for communicating with the Closira CRM API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        email: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("CLOSIRA_BASE_URL")
            or "http://139.84.146.101/api/v1"
        ).rstrip("/")
        self.email = email or os.environ.get("CLOSIRA_EMAIL") or "agent@closiro.com"
        self.password = password or os.environ.get("CLOSIRA_PASSWORD") or "Agent@Secure123!"
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

    def _login(self) -> bool:
        """Authenticate with Closira CRM and cache the JWT token."""
        url = f"{self.base_url}/auth/login"
        payload = {"email": self.email, "password": self.password}
        try:
            logger.info("Attempting to authenticate with Closira CRM at %s", url)
            response = httpx.post(url, json=payload, timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                if "data" in data and isinstance(data["data"], dict):
                    token_data = data["data"]
                    if "tokens" in token_data and isinstance(token_data["tokens"], dict):
                        self._token = token_data["tokens"].get("access_token")
                        expires_in = token_data["tokens"].get("expires_in", 3600)
                    else:
                        self._token = token_data.get("access_token")
                        expires_in = token_data.get("expires_in", 3600)
                else:
                    self._token = data.get("access_token")
                    expires_in = data.get("expires_in", 3600)
                
                self._token_expires_at = time.time() + expires_in - 60
                logger.info("Successfully authenticated with Closira CRM.")
                return True
            else:
                logger.error(
                    "Failed to authenticate with Closira CRM. Status: %d, Response: %s",
                    response.status_code,
                    response.text,
                )
                return False
        except Exception as exc:
            logger.exception("Error authenticating with Closira CRM: %s", exc)
            return False

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers with Bearer token, logging in if needed."""
        if not self._token or time.time() >= self._token_expires_at:
            self._login()
        
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def create_contact(
        self, first_name: str, last_name: str, phone: str, email: str = ""
    ) -> Optional[int]:
        """Create a Contact in Closira CRM."""
        url = f"{self.base_url}/contacts"
        payload = {
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "email": email or f"{first_name.lower()}.{last_name.lower().replace('-', '')}{phone}@example.com",
            "type": "contact",
            "priority": "medium",
            "stage": "new",
        }
        try:
            contact_email = payload["email"]
            response = httpx.post(url, json=payload, headers=self._get_headers(), timeout=10.0)
            if response.status_code in (200, 201):
                data = response.json()
                contact_data = data.get("data") if "data" in data else data
                contact_id = contact_data.get("id")
                logger.info("Created contact with ID: %s", contact_id)
                return contact_id
            elif response.status_code == 409:
                logger.info("Contact email conflict. Fetching existing contact...")
                get_url = f"{self.base_url}/contacts?email={contact_email}"
                get_resp = httpx.get(get_url, headers=self._get_headers(), timeout=10.0)
                if get_resp.status_code == 200:
                    data = get_resp.json()
                    contacts_list = data.get("items", []) or data.get("data", {}).get("items", [])
                    if contacts_list:
                        contact_id = contacts_list[0].get("id")
                        logger.info("Found existing contact ID: %s", contact_id)
                        return contact_id
            else:
                logger.error("Failed to create contact. Status: %d, Response: %s", response.status_code, response.text)
        except Exception as exc:
            logger.exception("Error creating contact: %s", exc)
        return None

    def create_lead(
        self,
        contact_id: int,
        first_name: str,
        last_name: str,
        phone: str,
        location: str,
        event_type: str,
        party_size: int,
        event_date: str = "",
    ) -> Optional[int]:
        """Create a Lead in Closira CRM."""
        # Ensure event_date is a valid ISO string
        parsed_date = ""
        if event_date:
            if event_date.lower().strip() == "tomorrow":
                from datetime import date, timedelta
                parsed_date = (date.today() + timedelta(days=1)).isoformat() + "T12:00:00Z"
            else:
                from dateutil.parser import parse
                try:
                    parsed_date = parse(event_date).isoformat() + "Z"
                except Exception:
                    pass
        
        url = f"{self.base_url}/leads"
        payload = {
            "contact_id": contact_id,
            "customer_id": contact_id,  # Requirement: Use customer_id instead of contact_id
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "location": location,
            "event_type": event_type,
            "party_size": party_size,
            "estimated_value": party_size * 1000.0,  # Estimated 1000 per head
            "lead_source": "voice",
            "channel": "voice",
            "pipeline_stage": "new",
            "stage_id": 1,
            "event_date": parsed_date or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            logger.info("Creating Closira lead for contact %d", contact_id)
            response = httpx.post(url, json=payload, headers=self._get_headers(), timeout=10.0)
            if response.status_code in (200, 201):
                data = response.json()
                lead_data = data.get("data") if "data" in data else data
                lead_id = lead_data.get("id")
                logger.info("Created lead with ID: %s", lead_id)
                return lead_id
            else:
                logger.error("Failed to create lead. Status: %d, Response: %s", response.status_code, response.text)
        except Exception as exc:
            logger.exception("Error creating lead: %s", exc)
        return None

    def update_lead_stage(self, lead_id: int, stage: str) -> bool:
        """Update a Lead's stage in Closira CRM."""
        url = f"{self.base_url}/leads/{lead_id}/stage"
        payload = {
            "pipeline_stage": stage,  # Requirement: Use pipeline_stage instead of stage
        }
        try:
            logger.info("Updating Closira lead %d to stage %s", lead_id, stage)
            response = httpx.patch(url, json=payload, headers=self._get_headers(), timeout=10.0)
            if response.status_code == 200:
                logger.info("Successfully updated lead stage.")
                return True
            else:
                logger.error("Failed to update lead stage. Status: %d, Response: %s", response.status_code, response.text)
        except Exception as exc:
            logger.exception("Error updating lead stage: %s", exc)
        return False

    def add_contact_note(self, contact_id: int, note_text: str) -> bool:
        """Add a CRM Note to a Contact in Closira CRM."""
        url = f"{self.base_url}/contacts/{contact_id}/notes"
        payload = {
            "text": note_text,  # Requirement: Use text instead of note
        }
        try:
            logger.info("Adding note to Closira contact %d: %s", contact_id, note_text[:40])
            response = httpx.post(url, json=payload, headers=self._get_headers(), timeout=10.0)
            if response.status_code in (200, 201):
                logger.info("Successfully added note.")
                return True
            else:
                logger.error("Failed to add note. Status: %d, Response: %s", response.status_code, response.text)
        except Exception as exc:
            logger.exception("Error adding note: %s", exc)
        return False

    def raise_escalation(self, session_id: str, contact_id: int, reason: str, summary: Dict[str, Any]) -> bool:
        """Raise an Escalation in Closira CRM via webhook."""
        import hmac
        import hashlib
        import json
        
        url = f"{self.base_url}/webhooks/vapi/escalation"
        payload = {
            "session_id": session_id,
            "contact_id": contact_id,
            "reason": reason,
            "handoff_summary": summary,
        }
        payload_bytes = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        
        # Calculate HMAC signature using the webhook secret
        secret = os.environ.get("VAPI_WEBHOOK_SECRET", "vapi_secret_token_1234")
        signature = hmac.new(secret.encode('utf-8'), payload_bytes, hashlib.sha256).hexdigest()
        
        headers = self._get_headers()
        headers["X-Vapi-Signature"] = signature
        
        try:
            logger.info("Raising escalation for session %s, contact %d", session_id, contact_id)
            response = httpx.post(url, data=payload_bytes, headers=headers, timeout=10.0)
            if response.status_code in (200, 201):
                logger.info("Successfully raised escalation.")
                return True
            else:
                logger.error("Failed to raise escalation. Status: %d, Response: %s", response.status_code, response.text)
        except Exception as exc:
            logger.exception("Error raising escalation: %s", exc)
        return False
