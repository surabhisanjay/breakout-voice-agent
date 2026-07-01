from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import requests


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WatiConfig:
    access_token: str
    base_url: str
    api_version: str = "v1"
    timeout_seconds: float = 10.0
    max_attempts: int = 3
    send_path: str = "/api/{api_version}/sendSessionMessage/{phone}"
    template_id: str = ""
    template_path: str = "/api/v2/sendTemplateMessage"
    broadcast_name: str = "booking_payment"
    sender_number: str = ""

    @classmethod
    def from_env(cls) -> "WatiConfig | None":
        access_token = (
            os.environ.get("WATI_ACCESS_TOKEN", "").strip()
            or os.environ.get("WATI_API_KEY", "").strip()
            or os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
        )
        access_token = re.sub(r"^Bearer\s+", "", access_token, flags=re.IGNORECASE).strip()
        base_url = (
            os.environ.get("WATI_BASE_URL", "").strip()
            or os.environ.get("WHATSAPP_API_ENDPOINT", "").strip()
        ).rstrip("/")
        api_version = (
            os.environ.get("WATI_API_VERSION", "").strip()
            or os.environ.get("WHATSAPP_API_VERSION", "").strip()
            or "v1"
        )
        api_version = "v1" if api_version.lower() in {"1", "version 1"} else api_version
        if not access_token or not base_url:
            return None
        return cls(
            access_token=access_token,
            base_url=base_url,
            api_version=api_version,
            timeout_seconds=cls._float_env("WATI_TIMEOUT_SECONDS", 10.0),
            max_attempts=max(cls._int_env("WATI_MAX_ATTEMPTS", 3), 1),
            send_path=os.environ.get(
                "WATI_SEND_PATH",
                "/api/{api_version}/sendSessionMessage/{phone}",
            ).strip() or "/api/{api_version}/sendSessionMessage/{phone}",
            template_id=os.environ.get("WATI_TEMPLATE_ID", "").strip(),
            template_path=os.environ.get(
                "WATI_TEMPLATE_PATH", "/api/v2/sendTemplateMessage"
            ).strip() or "/api/v2/sendTemplateMessage",
            broadcast_name=os.environ.get(
                "WATI_BROADCAST_NAME", "booking_payment"
            ).strip() or "booking_payment",
            sender_number=os.environ.get("WATI_SENDER_NUMBER", "").strip(),
        )

    @staticmethod
    def _float_env(key: str, default: float) -> float:
        try:
            return float(os.environ.get(key, str(default)))
        except (TypeError, ValueError):
            logger.warning("WATI_CONFIG_INVALID key=%s using_default=%s", key, default)
            return default

    @staticmethod
    def _int_env(key: str, default: int) -> int:
        try:
            return int(os.environ.get(key, str(default)))
        except (TypeError, ValueError):
            logger.warning("WATI_CONFIG_INVALID key=%s using_default=%s", key, default)
            return default


@dataclass(frozen=True)
class WatiSendResult:
    attempted: bool
    sent: bool
    provider: str = "WATI"
    status_code: int | None = None
    reason: str = ""
    response: dict[str, Any] | str | None = None
    message_id: str | None = None
    timestamp: str = ""

    @property
    def error(self) -> str:
        """Backward-compatible alias for callers that still read ``error``."""
        return self.reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "sent": self.sent,
            "provider": self.provider,
            "status_code": self.status_code,
            "reason": self.reason,
            "response": self.response,
            "message_id": self.message_id,
            "timestamp": self.timestamp or datetime.now(timezone.utc).isoformat(),
        }


class WatiClient:
    def __init__(self, config: WatiConfig | None = None) -> None:
        self.config = config if config is not None else WatiConfig.from_env()

    @property
    def configured(self) -> bool:
        return self.config is not None

    def send_booking_payment_link(self, payload: dict[str, Any]) -> WatiSendResult:
        if self.config is None:
            logger.info("WATI_SEND_SKIPPED reason=missing_configuration")
            return WatiSendResult(attempted=False, sent=False, reason="missing_configuration")

        phone = self._normalise_phone(str(payload.get("phone") or ""))
        if not phone:
            logger.warning("WATI_SEND_SKIPPED reason=missing_phone booking_id=%s", payload.get("booking_id", ""))
            return WatiSendResult(attempted=False, sent=False, reason="missing_phone")

        if self.config.template_id:
            return self._send_template_message(phone, payload)

        message_text = self._message_text(payload)
        url = self._send_url(phone, message_text)
        headers = {
            "Authorization": f"Bearer {self.config.access_token}",
            "Accept": "application/json",
        }

        attempts = self.config.max_attempts
        last_error = ""
        last_status: int | None = None
        last_response: dict[str, Any] | str | None = None
        for attempt in range(1, attempts + 1):
            try:
                logger.info(
                    "WATI_SEND_ATTEMPT attempt=%s booking_id=%s phone=%s endpoint=%s",
                    attempt,
                    payload.get("booking_id", ""),
                    self._mask_phone(phone),
                    self._safe_endpoint(url),
                )
                response = requests.post(
                    url,
                    headers=headers,
                    timeout=self.config.timeout_seconds,
                )
                raw = response.text
                parsed = self._json_or_text(raw)
                status = int(response.status_code)
                logger.info(
                    "WATI_SEND_RESPONSE attempt=%s status=%s booking_id=%s response=%s",
                    attempt,
                    status,
                    payload.get("booking_id", ""),
                    self._log_value(parsed),
                )
                sent = self._response_accepted(status, parsed)
                if sent:
                    logger.info(
                        "WATI_SEND_SUCCESS status=%s booking_id=%s phone=%s",
                        status,
                        payload.get("booking_id", ""),
                        self._mask_phone(phone),
                    )
                    return WatiSendResult(
                        attempted=True,
                        sent=True,
                        status_code=status,
                        response=parsed,
                        message_id=self._message_id(parsed),
                    )
                if 200 <= status < 300:
                    return WatiSendResult(
                        attempted=True,
                        sent=False,
                        status_code=status,
                        reason=self._response_reason(status, parsed, raw),
                        response=parsed,
                        message_id=self._message_id(parsed),
                    )
                last_error = f"http_{status}: {raw[:500]}"
                last_status = status
                last_response = parsed
                logger.warning(
                    "WATI_SEND_HTTP_ERROR attempt=%s status=%s booking_id=%s reason=%s response=%s",
                    attempt,
                    status,
                    payload.get("booking_id", ""),
                    last_error,
                    self._log_value(last_response),
                )
                if 400 <= status < 500:
                    return WatiSendResult(
                        attempted=True,
                        sent=False,
                        status_code=status,
                        reason=last_error,
                        response=last_response,
                    )
            except (requests.Timeout, requests.RequestException, OSError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "WATI_SEND_ERROR attempt=%s booking_id=%s error=%s",
                    attempt,
                    payload.get("booking_id", ""),
                    last_error,
                )
            if attempt < attempts:
                time.sleep(min(0.5 * attempt, 2.0))

        logger.error("WATI_SEND_FAILED booking_id=%s error=%s", payload.get("booking_id", ""), last_error)
        return WatiSendResult(
            attempted=True,
            sent=False,
            status_code=last_status,
            reason=last_error,
            response=last_response,
        )

    def _send_template_message(self, phone: str, payload: dict[str, Any]) -> WatiSendResult:
        assert self.config is not None
        endpoint = f"{self.config.base_url}{self.config.template_path}"
        url = f"{endpoint}?{urlencode({'whatsappNumber': phone})}"
        booking_summary = "; ".join(
            [
                f"Booking ID: {payload.get('booking_id') or ''}",
                f"Room: {payload.get('room') or ''}",
                f"Location: {payload.get('location') or ''}",
                f"Date: {payload.get('date') or ''}",
                f"Time: {payload.get('time') or ''}",
                "Please complete payment within 15 minutes.",
            ]
        )
        request_body: dict[str, Any] = {
            "template_name": self.config.template_id,
            "broadcast_name": self.config.broadcast_name,
            "parameters": [
                {"name": "name", "value": str(payload.get("customer_name") or "there")},
                {
                    "name": "shop_name",
                    "value": f"Breakout {payload.get('location') or ''}".strip(),
                },
                {"name": "product_details", "value": booking_summary},
                {"name": "order_status_url", "value": str(payload.get("payment_link") or "")},
            ],
        }
        if self.config.sender_number:
            request_body["channel_number"] = self._normalise_phone(self.config.sender_number)
        headers = {
            "Authorization": f"Bearer {self.config.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        last_error = ""
        last_status: int | None = None
        last_response: dict[str, Any] | str | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                logger.info(
                    "WATI_TEMPLATE_SEND_ATTEMPT attempt=%s booking_id=%s phone=%s endpoint=%s template=%s",
                    attempt,
                    payload.get("booking_id", ""),
                    self._mask_phone(phone),
                    endpoint,
                    self.config.template_id,
                )
                response = requests.post(
                    url,
                    headers=headers,
                    json=request_body,
                    timeout=self.config.timeout_seconds,
                )
                raw = response.text
                parsed = self._json_or_text(raw)
                status = int(response.status_code)
                logger.info(
                    "WATI_TEMPLATE_SEND_RESPONSE attempt=%s status=%s booking_id=%s response=%s",
                    attempt,
                    status,
                    payload.get("booking_id", ""),
                    self._log_value(parsed),
                )
                if self._response_accepted(status, parsed):
                    logger.info(
                        "WATI_SEND_SUCCESS status=%s booking_id=%s phone=%s transport=template",
                        status,
                        payload.get("booking_id", ""),
                        self._mask_phone(phone),
                    )
                    return WatiSendResult(
                        attempted=True,
                        sent=True,
                        status_code=status,
                        response=parsed,
                        message_id=self._message_id(parsed),
                    )
                last_error = self._response_reason(status, parsed, raw)
                last_status = status
                last_response = parsed
                logger.warning(
                    "WATI_TEMPLATE_SEND_REJECTED attempt=%s status=%s booking_id=%s reason=%s response=%s",
                    attempt,
                    status,
                    payload.get("booking_id", ""),
                    last_error,
                    self._log_value(parsed),
                )
                if status < 500:
                    break
            except (requests.Timeout, requests.RequestException, OSError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "WATI_TEMPLATE_SEND_ERROR attempt=%s booking_id=%s error=%s",
                    attempt,
                    payload.get("booking_id", ""),
                    last_error,
                )
            if attempt < self.config.max_attempts:
                time.sleep(min(0.5 * attempt, 2.0))
        logger.error(
            "WATI_SEND_FAILED booking_id=%s transport=template error=%s",
            payload.get("booking_id", ""),
            last_error,
        )
        return WatiSendResult(
            attempted=True,
            sent=False,
            status_code=last_status,
            reason=last_error,
            response=last_response,
            message_id=self._message_id(last_response),
        )

    def _send_url(self, phone: str, message_text: str) -> str:
        assert self.config is not None
        path = self.config.send_path.format(
            api_version=self.config.api_version.strip("/"),
            phone=phone,
        )
        query = urlencode({"messageText": message_text})
        if "{phone}" in path:
            path = path.replace("{phone}", phone)
        separator = "&" if "?" in path else "?"
        return f"{self.config.base_url}{path}{separator}{query}"

    @staticmethod
    def _message_text(payload: dict[str, Any]) -> str:
        return "\n".join(
            [
                f"Hi {payload.get('customer_name') or 'there'}, your Breakout booking is reserved.",
                f"Booking ID: {payload.get('booking_id') or ''}",
                f"Room: {payload.get('room') or ''}",
                f"Location: {payload.get('location') or ''}",
                f"Date: {payload.get('date') or ''}",
                f"Time: {payload.get('time') or ''}",
                f"Payment Link: {payload.get('payment_link') or ''}",
                "Please complete payment within 15 minutes.",
            ]
        )

    @staticmethod
    def _normalise_phone(phone: str) -> str:
        digits = "".join(ch for ch in phone if ch.isdigit())
        if len(digits) == 10:
            return f"91{digits}"
        if digits.startswith("91") and len(digits) == 12:
            return digits
        return digits

    @staticmethod
    def _mask_phone(phone: str) -> str:
        return f"***{phone[-4:]}" if phone else ""

    @staticmethod
    def _safe_endpoint(url: str) -> str:
        return url.split("?", 1)[0]

    @staticmethod
    def _json_or_text(raw: str) -> dict[str, Any] | str:
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return raw[:500]

    @classmethod
    def _message_id(cls, response: Any) -> str | None:
        if isinstance(response, list):
            for value in response:
                message_id = cls._message_id(value)
                if message_id:
                    return message_id
            return None
        if not isinstance(response, dict):
            return None
        for key in (
            "messageId", "message_id", "whatsappMessageId", "waMessageId", "localMessageId"
        ):
            value = response.get(key)
            if value not in (None, ""):
                return str(value)
        for value in response.values():
            if isinstance(value, (dict, list)):
                message_id = cls._message_id(value)
                if message_id:
                    return message_id
        return None

    @staticmethod
    def _response_accepted(status: int, response: dict[str, Any] | str | None) -> bool:
        if not 200 <= status < 300:
            return False
        if isinstance(response, dict):
            if response.get("result") is False or response.get("success") is False:
                return False
            receivers = response.get("receivers")
            if isinstance(receivers, list) and receivers:
                return any(
                    isinstance(receiver, dict)
                    and receiver.get("isValidWhatsAppNumber") is not False
                    and not receiver.get("errors")
                    for receiver in receivers
                )
        return True

    @staticmethod
    def _response_reason(
        status: int,
        response: dict[str, Any] | str | None,
        raw: str,
    ) -> str:
        if isinstance(response, dict):
            reason = response.get("message") or response.get("error") or response.get("reason")
            if reason:
                return f"wati_rejected: {reason}"
        return f"http_{status}: {raw[:500]}"

    @staticmethod
    def _log_value(value: dict[str, Any] | str | None) -> str:
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=True, default=str)[:1000]
        return str(value or "")[:1000]
