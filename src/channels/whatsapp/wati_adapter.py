from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import requests

from .models import WatiDeliveryResult


@dataclass(frozen=True)
class WatiChannelConfig:
    access_token: str
    base_url: str
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "WatiChannelConfig | None":
        token = (
            os.environ.get("WATI_ACCESS_TOKEN", "").strip()
            or os.environ.get("WATI_API_KEY", "").strip()
            or os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
        )
        base_url = (
            os.environ.get("WATI_BASE_URL", "").strip()
            or os.environ.get("WHATSAPP_API_ENDPOINT", "").strip()
        ).rstrip("/")
        token = re.sub(r"^Bearer\s+", "", token, flags=re.IGNORECASE).strip()
        if not token or not base_url:
            return None
        try:
            timeout = float(os.environ.get("WATI_TIMEOUT_SECONDS", "10"))
        except ValueError:
            timeout = 10.0
        return cls(access_token=token, base_url=base_url, timeout_seconds=timeout)


class WatiChannelClient:
    """Transport-only WATI client for replies in an open WhatsApp session."""

    def __init__(self, config: WatiChannelConfig | None = None) -> None:
        self.config = config if config is not None else WatiChannelConfig.from_env()

    @property
    def configured(self) -> bool:
        return self.config is not None

    def send_text(self, phone: str, text: str) -> WatiDeliveryResult:
        if self.config is None:
            return WatiDeliveryResult(False, False, reason="missing_wati_configuration")
        recipient = self.normalise_phone(phone)
        if not recipient:
            return WatiDeliveryResult(False, False, reason="invalid_phone")
        if not text.strip():
            return WatiDeliveryResult(False, False, reason="empty_message")

        url = f"{self.config.base_url}/api/v1/sendSessionMessage/{recipient}"
        try:
            response = requests.post(
                url,
                params={"messageText": text.strip()},
                headers={
                    "Authorization": f"Bearer {self.config.access_token}",
                    "Accept": "application/json",
                },
                timeout=self.config.timeout_seconds,
            )
        except requests.RequestException as exc:
            return WatiDeliveryResult(
                attempted=True,
                sent=False,
                reason=f"{type(exc).__name__}: {exc}",
            )

        try:
            payload: dict[str, Any] | str = response.json()
        except ValueError:
            payload = response.text[:500]
        sent, reason = self._accepted(response.status_code, payload)
        return WatiDeliveryResult(
            attempted=True,
            sent=sent,
            status_code=response.status_code,
            message_id=self._message_id(payload),
            reason=reason,
            response=payload,
        )

    @staticmethod
    def normalise_phone(phone: str) -> str:
        digits = re.sub(r"\D", "", phone)
        if len(digits) == 10 and digits[0] in "6789":
            return f"91{digits}"
        return digits if 11 <= len(digits) <= 15 else ""

    @staticmethod
    def _accepted(status_code: int, payload: dict[str, Any] | str) -> tuple[bool, str]:
        if not 200 <= status_code < 300:
            return False, f"http_{status_code}"
        if not isinstance(payload, dict):
            return True, ""
        if payload.get("result") is False:
            return False, str(payload.get("info") or payload.get("message") or payload.get("error") or "wati_rejected")
        receivers = payload.get("receivers")
        if isinstance(receivers, list):
            for receiver in receivers:
                if not isinstance(receiver, dict):
                    continue
                if receiver.get("isValidWhatsAppNumber") is False:
                    return False, "invalid_whatsapp_number"
                errors = receiver.get("errors")
                if errors:
                    return False, str(errors)[:500]
        return True, ""

    @staticmethod
    def _message_id(payload: dict[str, Any] | str) -> str:
        if not isinstance(payload, dict):
            return ""
        for key in ("messageId", "localMessageId", "id"):
            if payload.get(key):
                return str(payload[key])
        receivers = payload.get("receivers")
        if isinstance(receivers, list) and receivers and isinstance(receivers[0], dict):
            return str(receivers[0].get("localMessageId") or receivers[0].get("id") or "")
        return ""
