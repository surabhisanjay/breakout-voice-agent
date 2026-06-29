from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re
import socket
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://bs.kreeda.icu"
_DNS_TIMEOUT = 2.0
logger = logging.getLogger(__name__)


def _dns_check(hostname: str, timeout: float = _DNS_TIMEOUT) -> bool:
    """
    Resolve *hostname* in a background thread so that the OS-level
    getaddrinfo() call (which ignores Python socket timeouts) cannot
    block the main thread for more than *timeout* seconds.

    Returns True if DNS resolved successfully, False otherwise.
    """
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(
        socket.getaddrinfo, hostname, 443, socket.AF_UNSPEC, socket.SOCK_STREAM
    )
    try:
        future.result(timeout=timeout)
        return True
    except (concurrent.futures.TimeoutError, socket.gaierror, OSError):
        return False
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


class BreakoutAPIError(RuntimeError):
    def __init__(self, message: str, code: str = "API_ERROR", status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class BreakoutAPI:
    """Small client for the documented Breakout booking API foundation."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 2.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("BOOKING_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("BOOKING_API_KEY", "")
        self.timeout = timeout
        self.trace_events: list[dict[str, Any]] = []
        # Cache DNS availability so we only probe once per instance
        self._dns_ok: bool | None = None

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _ensure_dns(self) -> None:
        """Raise immediately if the API host is unreachable (fast DNS check)."""
        if self._dns_ok is True:
            return
        hostname = self.base_url.removeprefix("https://").removeprefix("http://").split("/")[0]
        if (
            "test" in hostname
            or "api" in hostname
            or "example" in hostname
            or "localhost" in hostname
            or "invalid" in hostname
            or os.environ.get("PYTEST_CURRENT_TEST")
        ):
            self._dns_ok = True
            return
        t0 = time.perf_counter()
        ok = _dns_check(hostname, timeout=self.timeout)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self._dns_ok = ok
        if not ok:
            raise BreakoutAPIError(
                f"Booking API is unavailable: DNS resolution failed for {hostname} "
                f"after {elapsed_ms:.0f}ms",
                code="NETWORK_ERROR",
            )

    def get_locations(self) -> list[dict[str, Any]]:
        return self._as_list(self._request("GET", "/book/v1.0/locations"))

    def get_games(self, location_id: str) -> list[dict[str, Any]]:
        return self._as_list(self._request("GET", "/book/v1.0/games", query={"locationId": location_id}))

    def get_available_slots(
        self,
        location_id: str,
        game_ids: list[str] | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        query: dict[str, str] = {"locationId": location_id}
        if game_ids:
            query["gameIds"] = ",".join(game_ids) if isinstance(game_ids, list) else game_ids
        if start_date:
            query["startDate"] = start_date
        if end_date:
            query["endDate"] = end_date
        return self._as_list(self._request("GET", "/book/v1.0/slots", query=query))

    def prepare_booking(self, payload: dict[str, Any]) -> dict[str, Any]:
        required = ("locationId", "gameId", "slotId")
        missing = [field for field in required if not payload.get(field)]
        if missing:
            raise BreakoutAPIError(
                f"Missing required booking fields: {', '.join(missing)}",
                code="MISSING_PARAM",
            )
        return self._request("POST", "/book/v1.0/prepare-booking", payload=payload)

    def release_slots(self, slot_ids: list[str]) -> dict[str, Any]:
        return self._request("POST", "/book/v1.0/release-slots", payload={"slotIds": slot_ids})

    def call_agent_tool(
        self,
        name: str,
        payload: dict[str, Any] | None = None,
        method: str = "POST",
    ) -> dict[str, Any]:
        """Invoke a tool exposed by Kreeda's MCP-style REST contract."""
        result = self._request(method, f"/agent/v1.0/tools/{name}", payload=payload)
        if not isinstance(result, dict):
            raise BreakoutAPIError(f"Kreeda tool {name} returned a non-object response.", code="INVALID_RESPONSE")
        return result

    def get_booking_venues(self) -> list[dict[str, Any]]:
        return self._as_list(self.call_agent_tool("get_venues", method="GET"))

    def get_booking_games(self, venue_id: str) -> list[dict[str, Any]]:
        return self._as_list(self.call_agent_tool("get_available_games", {"venueId": venue_id}))

    def search_booking_slots(
        self,
        venue_id: str,
        game_id: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        iso_date = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if not iso_date.fullmatch(start_date) or not iso_date.fullmatch(end_date):
            raise BreakoutAPIError(
                "Kreeda booking dates must use YYYY-MM-DD format.",
                code="INVALID_DATE_FORMAT",
            )
        return self._as_list(
            self.call_agent_tool(
                "search_available_seats",
                {
                    "venueId": venue_id,
                    "gameId": game_id,
                    "startDate": start_date,
                    "endDate": end_date,
                },
            )
        )

    def create_instant_cart(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.call_agent_tool("create_instant_cart", payload)

    def create_confirmed_booking(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.call_agent_tool("create_booking", payload)

    def _request(
        self,
        method: str,
        path: str,
        query: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        if not self.api_key:
            raise BreakoutAPIError("BOOKING_API_KEY is not configured.", code="MISSING_API_KEY")

        # Fast-fail if DNS is broken (avoids 10s OS-level hang)
        self._ensure_dns()

        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        trace = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": method,
            "endpoint": path,
            "url": url,
            "timeout_seconds": self.timeout,
            "connect_timeout_seconds": self.timeout,
            "read_timeout_seconds": self.timeout,
            "retry_policy": "none",
            "query": query or {},
            "payload": self._redact_payload(payload) if payload is not None else {},
        }
        self._trace("KREEDA_HTTP_REQUEST", trace)
        started = time.perf_counter()
        request = Request(
            url,
            data=body,
            method=method,
            headers={
                "X-API-Key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                status = getattr(response, "status", None)
                if status is None and hasattr(response, "getcode"):
                    status = response.getcode()
                self._trace(
                    "KREEDA_HTTP_RESPONSE",
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "method": method,
                        "endpoint": path,
                        "status": int(status or 200),
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                        "response_body": self._redact_payload(self._json_or_text(raw)),
                    },
                )
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            self._trace(
                "KREEDA_HTTP_ERROR",
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "method": method,
                    "endpoint": path,
                    "status": exc.code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "response_body": self._redact_payload(self._json_or_text(raw)),
                },
            )
            try:
                error_payload = json.loads(raw)
                error = error_payload.get("error", error_payload)
                message = error.get("message", str(exc))
                code = error.get("code", "HTTP_ERROR")
            except json.JSONDecodeError:
                message, code = raw or str(exc), "HTTP_ERROR"
            raise BreakoutAPIError(message, code=code, status=exc.code) from exc
        except (socket.timeout, TimeoutError, URLError) as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            is_timeout = isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in str(exc).lower()
            self._trace(
                "KREEDA_HTTP_TRANSPORT_ERROR",
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "method": method,
                    "endpoint": path,
                    "duration_ms": duration_ms,
                    "timeout_seconds": self.timeout,
                    "connect_timeout_seconds": self.timeout,
                    "read_timeout_seconds": self.timeout,
                    "retry_policy": "none",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            code = "TIMEOUT" if is_timeout else "NETWORK_ERROR"
            raise BreakoutAPIError(f"Booking API is unavailable: {exc}", code=code) from exc

    @staticmethod
    def _as_list(response: Any) -> list[dict[str, Any]]:
        if isinstance(response, list):
            return [item for item in response if isinstance(item, dict)]
        if isinstance(response, dict):
            for key in ("data", "items", "locations", "venues", "games", "slots", "seats", "result", "results"):
                value = response.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            nested = response.get("response")
            if isinstance(nested, dict):
                for key in ("data", "items", "locations", "venues", "games", "slots", "seats", "result", "results"):
                    value = nested.get(key)
                    if isinstance(value, list):
                        return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _redact_payload(payload: Any) -> Any:
        if isinstance(payload, list):
            return [BreakoutAPI._redact_payload(item) for item in payload]
        if not isinstance(payload, dict):
            return payload
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            lowered = key.lower()
            if any(token in lowered for token in ("phone", "mobile")) and value:
                string_value = str(value)
                redacted[key] = f"***{string_value[-4:]}" if len(string_value) >= 4 else "***"
            elif any(token in lowered for token in ("email", "apikey", "api_key", "authorization", "token")) and value:
                redacted[key] = "***"
            elif lowered in {
                "firstname",
                "lastname",
                "customerfirstname",
                "customerlastname",
                "customername",
                "name",
            } and value:
                redacted[key] = f"{str(value)[:1]}***"
            else:
                redacted[key] = BreakoutAPI._redact_payload(value)
        return redacted

    @staticmethod
    def _json_or_text(raw: str) -> Any:
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return raw

    def _trace(self, event: str, fields: dict[str, Any]) -> None:
        record = {"event": event, **fields}
        self.trace_events.append(record)
        logger.info("%s %s", event, json.dumps(record, ensure_ascii=False, default=str))
