#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request_json(base_url: str, method: str, path: str, payload: Any = None) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        raw_body = exc.read()
        try:
            error_body = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            error_body = raw_body.decode("utf-8", errors="replace")
        return exc.code, error_body


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the FastAPI endpoints used by Vapi.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    payload = {"session_id": "vapi-verification", "message": "hello"}
    checks = [
        ("backend/health", "GET", "/health", None, lambda status, data: status == 200 and data.get("status") == "ok"),
        ("debug", "POST", "/debug", payload, lambda status, data: status == 200 and data.get("received") == payload),
        ("chat", "POST", "/chat", payload, lambda status, data: status == 200 and isinstance(data.get("response"), str)),
    ]
    failures = 0
    for name, method, path, body, validate in checks:
        try:
            status, data = request_json(args.base_url, method, path, body)
            passed = validate(status, data)
            print(f"{'PASS' if passed else 'FAIL'} {name}: HTTP {status} {_compact(data)}")
            failures += not passed
        except (URLError, TimeoutError) as exc:
            print(f"FAIL {name}: {exc}")
            failures += 1
    print(f"SUMMARY: {len(checks) - failures} passed, {failures} failed")
    return 1 if failures else 0


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


if __name__ == "__main__":
    sys.exit(main())
