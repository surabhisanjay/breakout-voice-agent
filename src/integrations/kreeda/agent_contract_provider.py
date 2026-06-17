from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import socket
import time
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def _dns_check(hostname: str, timeout: float = 3.0) -> bool:
    """
    Resolve *hostname* in a background thread so the OS-level getaddrinfo()
    call (which ignores Python socket timeouts) cannot block the main thread
    for more than *timeout* seconds.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(socket.getaddrinfo, hostname, 443,
                             socket.AF_UNSPEC, socket.SOCK_STREAM)
        try:
            future.result(timeout=timeout)
            return True
        except (concurrent.futures.TimeoutError, socket.gaierror, OSError):
            return False


class AgentContractAPIError(RuntimeError):
    def __init__(self, message: str, code: str = "API_ERROR", status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class AgentContractProvider:
    """
    Provider for the Discovery-based Agent Contract API.
    Loads and caches tool schemas from /agent/v1.0/tools and exposes strongly-typed operations.
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 3.0) -> None:
        self.base_url = (base_url or os.environ.get("BOOKING_BASE_URL") or "https://bs.kreeda.icu").rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("BOOKING_API_KEY", "")
        self.timeout = timeout
        self.cached_tools: Dict[str, Any] = {}
        self._dns_ok: bool | None = None

        # Load and cache tools from discovery endpoint (skip if DNS is broken)
        self.load_tools()

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def load_tools(self) -> None:
        """Fetch all tool definitions from the discovery endpoint and cache them."""
        if os.environ.get("DEMO_MODE", "false").lower() == "true":
            logger.info("Demo mode active. Bypassing tool discovery.")
            return

        if not self.api_key:
            logger.warning("BOOKING_API_KEY not configured. Cannot load agent contract tools.")
            return


        try:
            response = self._request("GET", "/agent/v1.0/tools")
            # Cache schemas
            if isinstance(response, dict) and "tools" in response:
                self.cached_tools = {tool.get("name"): tool for tool in response["tools"]}
            elif isinstance(response, list):
                self.cached_tools = {tool.get("name"): tool for tool in response if "name" in tool}
            else:
                self.cached_tools = {}
            logger.info(f"Loaded {len(self.cached_tools)} tools from contract API.")
        except Exception as exc:
            logger.error(f"Failed to load tools from discovery endpoint: {exc}")
            self.cached_tools = {}

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
            raise AgentContractAPIError(
                f"Agent Contract API is unavailable: DNS resolution failed for {hostname} "
                f"after {elapsed_ms:.0f}ms",
                code="NETWORK_ERROR",
            )

    def get_venues(self) -> list[dict[str, Any]]:
        """Retrieve list of venues from the contract tools family."""
        return self._request("GET", "/agent/v1.0/venues")

    def get_available_games(self, location_id: str | None = None) -> list[dict[str, Any]]:
        """Retrieve list of available games, optionally filtered by location."""
        query = {"locationId": location_id} if location_id else None
        return self._request("GET", "/agent/v1.0/games", query=query)

    def search_available_seats(
        self, location_id: str, game_id: str, date: str, participants: int
    ) -> list[dict[str, Any]]:
        """Search available seats for a location, game, date, and party size."""
        query = {
            "locationId": location_id,
            "gameId": game_id,
            "date": date,
            "participants": str(participants),
        }
        return self._request("GET", "/agent/v1.0/seats", query=query)

    def create_booking(
        self, slot_id: str, customer: dict[str, Any], require_payment: bool = False
    ) -> dict[str, Any]:
        """Create a booking reservation."""
        payload = {
            "slotId": slot_id,
            "customer": customer,
            "requirePayment": require_payment,
        }
        return self._request("POST", "/agent/v1.0/bookings", payload=payload)

    def cancel_booking(self, booking_ref: str, reason: str | None = None) -> dict[str, Any]:
        """Cancel an existing booking reservation."""
        payload = {
            "bookingRef": booking_ref,
            "reason": reason or "customer_request",
        }
        return self._request("POST", "/agent/v1.0/bookings/cancel", payload=payload)

    def reschedule_booking(self, booking_ref: str, new_slot_id: str) -> dict[str, Any]:
        """Reschedule a booking to a new slot."""
        payload = {
            "bookingRef": booking_ref,
            "newSlotId": new_slot_id,
        }
        return self._request("POST", "/agent/v1.0/bookings/reschedule", payload=payload)

    def find_booking(self, booking_ref: str) -> dict[str, Any]:
        """Look up details for an existing booking reference."""
        query = {"bookingRef": booking_ref}
        return self._request("GET", "/agent/v1.0/bookings", query=query)

    def _request(
        self,
        method: str,
        path: str,
        query: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        if not self.api_key:
            raise AgentContractAPIError("BOOKING_API_KEY is not configured.", code="MISSING_API_KEY")

        # Fast-fail if DNS is broken (avoids 10s OS-level hang)
        self._ensure_dns()

        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
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
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                error_payload = json.loads(raw)
                error = error_payload.get("error", error_payload)
                if isinstance(error, dict):
                    message = error.get("message", str(exc))
                    code = error.get("code", "HTTP_ERROR")
                else:
                    message = str(error)
                    code = "HTTP_ERROR"
            except json.JSONDecodeError:
                message, code = raw or str(exc), "HTTP_ERROR"
            raise AgentContractAPIError(message, code=code, status=exc.code) from exc
        except (URLError, TimeoutError) as exc:
            raise AgentContractAPIError(f"Agent Contract API is unavailable: {exc}", code="NETWORK_ERROR") from exc
