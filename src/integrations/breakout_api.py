from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://bs.kreeda.icu"


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
        timeout: float = 10.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("BOOKING_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("BOOKING_API_KEY", "")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def get_locations(self) -> list[dict[str, Any]]:
        return self._request("GET", "/book/v1.0/locations")

    def get_games(self, location_id: str) -> list[dict[str, Any]]:
        return self._request("GET", "/book/v1.0/games", query={"locationId": location_id})

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
        return self._request("GET", "/book/v1.0/slots", query=query)

    def prepare_booking(self, payload: dict[str, Any]) -> dict[str, Any]:
        required = ("locationId", "gameId", "slotId")
        missing = [field for field in required if not payload.get(field)]
        if missing:
            raise BreakoutAPIError(
                f"Missing required booking fields: {', '.join(missing)}",
                code="MISSING_PARAM",
            )
        return self._request("POST", "/book/v1.0/prepare-booking", payload=payload)

    def _request(
        self,
        method: str,
        path: str,
        query: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        if not self.api_key:
            raise BreakoutAPIError("BOOKING_API_KEY is not configured.", code="MISSING_API_KEY")

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
                message = error.get("message", str(exc))
                code = error.get("code", "HTTP_ERROR")
            except json.JSONDecodeError:
                message, code = raw or str(exc), "HTTP_ERROR"
            raise BreakoutAPIError(message, code=code, status=exc.code) from exc
        except (URLError, TimeoutError) as exc:
            raise BreakoutAPIError(f"Booking API is unavailable: {exc}", code="NETWORK_ERROR") from exc

