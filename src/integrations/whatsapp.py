from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class WhatsAppResult:
    enabled: bool
    sent: bool
    status_code: int = 0
    message_id: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WhatsAppClient:
    """WhatsApp client supporting Meta Graph API and WATI v1 API."""

    def __init__(
        self,
        access_token: str | None = None,
        phone_number_id: str | None = None,
        api_version: str | None = None,
        enabled: bool | None = None,
        timeout: float = 3.0,
    ) -> None:
        self.access_token = access_token if access_token is not None else os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
        self.phone_number_id = phone_number_id if phone_number_id is not None else os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
        self.api_version = api_version or os.environ.get("WHATSAPP_API_VERSION", "v20.0")
        self.enabled = (
            os.environ.get("WHATSAPP_ENABLED", "false").lower() == "true"
            if enabled is None
            else enabled
        )
        self.timeout = timeout
        
        # WATI specific configurations
        self.provider = os.environ.get("WHATSAPP_PROVIDER", "meta").lower().strip()
        self.api_endpoint = os.environ.get("WHATSAPP_API_ENDPOINT", "").strip()
        self.template_name = os.environ.get("WHATSAPP_TEMPLATE_NAME", "").strip()
        self.broadcast_name = os.environ.get("WHATSAPP_BROADCAST_NAME", "booking_confirmation").strip()

    def send_booking_confirmation(self, phone: str, booking: dict[str, Any]) -> WhatsAppResult:
        if not self.enabled:
            return WhatsAppResult(enabled=False, sent=False, error="whatsapp_disabled")
        if not self.access_token:
            return WhatsAppResult(enabled=True, sent=False, error="missing_whatsapp_credentials")

        recipient = self._normalise_phone(phone)
        if not recipient:
            return WhatsAppResult(enabled=True, sent=False, error="invalid_phone")

        if self.provider == "wati":
            if not self.api_endpoint:
                return WhatsAppResult(enabled=True, sent=False, error="missing_wati_endpoint")
            
            base_url = self.api_endpoint.rstrip("/")
            auth_header = self.access_token if self.access_token.startswith("Bearer ") else f"Bearer {self.access_token}"
            
            if self.template_name:
                url = f"{base_url}/api/v1/sendTemplateMessage?whatsappNumber={recipient}"
                reference = booking.get("booking_reference") or booking.get("booking_ref") or booking.get("booking_id", "")
                payload = {
                    "template_name": self.template_name,
                    "broadcast_name": self.broadcast_name,
                    "parameters": [
                        {"name": "booking_reference", "value": reference},
                        {"name": "location", "value": str(booking.get("location", ""))},
                        {"name": "date", "value": str(booking.get("date", ""))},
                        {"name": "slot", "value": str(booking.get("slot", ""))},
                        {"name": "participants", "value": str(booking.get("participants", ""))}
                    ]
                }
                try:
                    response = requests.post(
                        url,
                        headers={
                            "Authorization": auth_header,
                            "Content-Type": "application/json",
                        },
                        json=payload,
                        timeout=self.timeout
                    )
                    data = response.json() if response.content else {}
                    message_id = ""
                    receivers = data.get("receivers") if isinstance(data, dict) else None
                    if isinstance(receivers, list) and receivers:
                        message_id = str(receivers[0].get("localMessageId") or receivers[0].get("id") or "")
                    if not message_id and isinstance(data, dict):
                        message_id = str(data.get("messageId") or data.get("id") or "")
                    
                    accepted, error = self._wati_response_status(response.status_code, data)
                    if accepted:
                        return WhatsAppResult(True, True, response.status_code, message_id, "")
                    return WhatsAppResult(True, False, response.status_code, message_id, error)
                except Exception as exc:
                    return WhatsAppResult(True, False, 0, "", f"{type(exc).__name__}: {exc}")
            else:
                url = f"{base_url}/api/v1/sendSessionMessage/{recipient}"
                reference = booking.get("booking_reference") or booking.get("booking_ref") or booking.get("booking_id", "")
                text = (
                    "Your Breakout booking is confirmed.\n"
                    f"Reference: {reference}\n"
                    f"Location: {booking.get('location', '')}\n"
                    f"Date: {booking.get('date', '')}\n"
                    f"Time: {booking.get('slot', '')}\n"
                    f"Guests: {booking.get('participants', '')}"
                ).strip()
                try:
                    response = requests.post(
                        url,
                        headers={
                            "Authorization": auth_header,
                        },
                        data={"messageText": text},
                        timeout=self.timeout
                    )
                    data = response.json() if response.content else {}
                    message_id = ""
                    if isinstance(data, dict):
                        message_id = str(data.get("messageId") or data.get("id") or "")
                    
                    accepted, error = self._wati_response_status(response.status_code, data)
                    if accepted:
                        return WhatsAppResult(True, True, response.status_code, message_id, "")
                    return WhatsAppResult(True, False, response.status_code, message_id, error)
                except Exception as exc:
                    return WhatsAppResult(True, False, 0, "", f"{type(exc).__name__}: {exc}")
        else:
            # Default to Meta Graph API
            if not self.phone_number_id:
                return WhatsAppResult(enabled=True, sent=False, error="missing_whatsapp_credentials")
            
            payload = self._message_payload(recipient, booking)
            url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
            try:
                response = requests.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.access_token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )
                data = response.json() if response.content else {}
                message_id = ""
                messages = data.get("messages") if isinstance(data, dict) else None
                if isinstance(messages, list) and messages:
                    message_id = str(messages[0].get("id", ""))
                if 200 <= response.status_code < 300:
                    return WhatsAppResult(True, True, response.status_code, message_id, "")
                return WhatsAppResult(True, False, response.status_code, message_id, str(data)[:500])
            except Exception as exc:
                return WhatsAppResult(True, False, 0, "", f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _normalise_phone(phone: str) -> str:
        digits = re.sub(r"\D", "", phone)
        if len(digits) == 10 and digits[0] in "6789":
            return f"91{digits}"
        if len(digits) >= 11:
            return digits
        return ""

    @staticmethod
    def _wati_response_status(status_code: int, data: Any) -> tuple[bool, str]:
        if not 200 <= status_code < 300:
            return False, str(data)[:500]
        if not isinstance(data, dict):
            return True, ""
        if data.get("result") is False:
            return False, str(data.get("info") or data.get("message") or data.get("error") or "wati_rejected")[:500]
        receivers = data.get("receivers")
        if isinstance(receivers, list):
            for receiver in receivers:
                if not isinstance(receiver, dict):
                    continue
                if receiver.get("isValidWhatsAppNumber") is False:
                    return False, "invalid_whatsapp_number"
                if receiver.get("errors"):
                    return False, str(receiver["errors"])[:500]
        return True, ""

    @staticmethod
    def _message_payload(recipient: str, booking: dict[str, Any]) -> dict[str, Any]:
        reference = booking.get("booking_reference") or booking.get("booking_ref") or booking.get("booking_id", "")
        text = (
            "Your Breakout booking is confirmed.\n"
            f"Reference: {reference}\n"
            f"Location: {booking.get('location', '')}\n"
            f"Date: {booking.get('date', '')}\n"
            f"Time: {booking.get('slot', '')}\n"
            f"Guests: {booking.get('participants', '')}"
        ).strip()
        return {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
