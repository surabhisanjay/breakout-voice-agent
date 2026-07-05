from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict
from typing import Any

import requests

from .models import ClosiroSyncResult, ClosiroWebhookPayload
from .signer import sign_payload


logger = logging.getLogger(__name__)

TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class ClosiroClient:
    def __init__(
        self,
        *,
        base_url: str,
        webhook_secret: str,
        timeout: float = 5.0,
        max_retries: int = 2,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.webhook_secret = webhook_secret
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.webhook_secret)

    def endpoint_url(self, endpoint: str) -> str:
        endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        if self.base_url.endswith("/api/v1") and endpoint.startswith("/api/v1/"):
            endpoint = endpoint[len("/api/v1") :]
        return f"{self.base_url}{endpoint}"

    def send(self, payload: ClosiroWebhookPayload) -> ClosiroSyncResult:
        if not self.configured:
            return ClosiroSyncResult(
                attempted=False,
                sent=False,
                reason="closiro_not_configured",
                endpoint=payload.endpoint,
                event_type=payload.event_type,
            )
        if not isinstance(payload.body, dict):
            return ClosiroSyncResult(
                attempted=False,
                sent=False,
                reason="invalid_payload_body",
                endpoint=payload.endpoint,
                event_type=payload.event_type,
            )

        body = json.dumps(payload.body, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        signature = sign_payload(body, self.webhook_secret)
        headers = {
            "Content-Type": "application/json",
            "X-Vapi-Signature": signature,
        }
        url = self.endpoint_url(payload.endpoint)
        response_body: Any = None
        status_code: int | None = None
        reason = ""
        start = time.perf_counter()
        retry_count = 0

        for attempt in range(self.max_retries + 1):
            retry_count = attempt
            try:
                response = self.session.post(url, data=body, headers=headers, timeout=self.timeout)
                status_code = response.status_code
                try:
                    response_body = response.json()
                except ValueError:
                    response_body = response.text[:1000]
                if response.status_code < 400:
                    latency_ms = (time.perf_counter() - start) * 1000
                    self._log(payload, status_code, latency_ms, retry_count)
                    return ClosiroSyncResult(
                        attempted=True,
                        sent=True,
                        status_code=status_code,
                        response=response_body,
                        endpoint=payload.endpoint,
                        event_type=payload.event_type,
                        latency_ms=round(latency_ms, 1),
                        retry_count=retry_count,
                        headers={"X-Vapi-Signature": signature},
                    )
                reason = f"http_{response.status_code}"
                if response.status_code not in TRANSIENT_STATUS_CODES:
                    break
            except requests.Timeout:
                reason = "timeout"
            except requests.RequestException as exc:
                reason = exc.__class__.__name__

            if attempt < self.max_retries:
                time.sleep(min(0.25 * (2**attempt), 1.0))

        latency_ms = (time.perf_counter() - start) * 1000
        self._log(payload, status_code, latency_ms, retry_count, reason=reason)
        return ClosiroSyncResult(
            attempted=True,
            sent=False,
            status_code=status_code,
            response=response_body,
            reason=reason,
            endpoint=payload.endpoint,
            event_type=payload.event_type,
            latency_ms=round(latency_ms, 1),
            retry_count=retry_count,
            headers={"X-Vapi-Signature": signature},
        )

    def _log(
        self,
        payload: ClosiroWebhookPayload,
        status_code: int | None,
        latency_ms: float,
        retry_count: int,
        *,
        reason: str = "",
    ) -> None:
        logger.info(
            "CLOSIRO_WEBHOOK event=%s endpoint=%s status=%s latency_ms=%.1f retries=%s reason=%s",
            payload.event_type,
            payload.endpoint,
            status_code,
            latency_ms,
            retry_count,
            reason,
        )


def get_default_closiro_client() -> ClosiroClient:
    timeout_raw = os.environ.get("CLOSIRO_TIMEOUT", "5")
    try:
        timeout = float(timeout_raw)
    except (TypeError, ValueError):
        timeout = 5.0
    return ClosiroClient(
        base_url=os.environ.get("CLOSIRO_BASE_URL", ""),
        webhook_secret=os.environ.get("VAPI_WEBHOOK_SECRET", ""),
        timeout=timeout,
    )


def result_to_dict(result: ClosiroSyncResult) -> dict[str, Any]:
    return asdict(result)
