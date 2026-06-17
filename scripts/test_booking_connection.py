#!/usr/bin/env python3
"""
scripts/test_booking_connection.py
------------------------------------
Verifies Booking API connectivity independently of the main agent.

Tests (each with 3-second timeout):
  1. DNS resolution for bs.kreeda.icu
  2. TCP handshake to bs.kreeda.icu:443
  3. GET /book/v1.0/locations
  4. GET /agent/v1.0/tools  (discovery endpoint)

Prints per-step latency.

Run:
    python scripts/test_booking_connection.py
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from urllib.request import Request, urlopen

__test__ = False

TIMEOUT = 3.0


def _load_env() -> None:
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())


def _banner(msg: str) -> None:
    print(f"\n{'─' * 55}")
    print(f"  {msg}")
    print(f"{'─' * 55}")


def test_dns(hostname: str) -> bool:
    _banner(f"TEST 1: DNS — {hostname}")
    t0 = time.perf_counter()
    try:
        addrs = socket.getaddrinfo(hostname, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
        ms = (time.perf_counter() - t0) * 1000
        ip = addrs[0][4][0] if addrs else "?"
        print(f"  ✓  PASS   {ms:.0f} ms  →  {ip}")
        return True
    except socket.gaierror as exc:
        ms = (time.perf_counter() - t0) * 1000
        print(f"  ✗  FAIL   {ms:.0f} ms  →  {exc}")
        return False


def test_tcp(hostname: str) -> bool:
    _banner(f"TEST 2: TCP — {hostname}:443")
    t0 = time.perf_counter()
    try:
        with socket.create_connection((hostname, 443), timeout=TIMEOUT):
            ms = (time.perf_counter() - t0) * 1000
            print(f"  ✓  PASS   {ms:.0f} ms")
            return True
    except (socket.timeout, OSError) as exc:
        ms = (time.perf_counter() - t0) * 1000
        print(f"  ✗  FAIL   {ms:.0f} ms  →  {exc}")
        return False


def test_endpoint(label: str, url: str, api_key: str) -> bool:
    req = Request(url, headers={"X-API-Key": api_key, "Accept": "application/json"})
    t0 = time.perf_counter()
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            ms = (time.perf_counter() - t0) * 1000
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            if isinstance(data, list):
                print(f"  ✓  PASS   {ms:.0f} ms  →  {len(data)} items  status={resp.status}")
            elif isinstance(data, dict):
                keys = list(data.keys())[:4]
                print(f"  ✓  PASS   {ms:.0f} ms  →  keys={keys}  status={resp.status}")
            else:
                print(f"  ✓  PASS   {ms:.0f} ms  status={resp.status}")
            return True
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        print(f"  ✗  FAIL   {ms:.0f} ms  →  {type(exc).__name__}: {exc}")
        return False


if __name__ == "__main__":
    _load_env()

    base_url = os.environ.get("BOOKING_BASE_URL", "https://bs.kreeda.icu").rstrip("/")
    api_key = os.environ.get("BOOKING_API_KEY", "")
    hostname = base_url.removeprefix("https://").removeprefix("http://").split("/")[0]

    print("\n" + "█" * 55)
    print("  BREAKOUT AGENT — BOOKING API CONNECTION TEST")
    print("█" * 55)
    print(f"  Base URL : {base_url}")
    print(f"  API Key  : {'SET (' + api_key[:8] + '…)' if api_key else 'NOT SET'}")
    print(f"  Timeout  : {TIMEOUT}s per test")

    if not api_key:
        print("\n  ✗  BOOKING_API_KEY is not set. Cannot run tests.")
        sys.exit(1)

    results: dict[str, bool] = {}
    results["dns"] = test_dns(hostname)
    results["tcp"] = test_tcp(hostname)

    _banner("TEST 3: GET /book/v1.0/locations")
    results["locations"] = test_endpoint(
        "locations", f"{base_url}/book/v1.0/locations", api_key
    )

    _banner("TEST 4: GET /agent/v1.0/tools  (discovery)")
    results["discovery"] = test_endpoint(
        "discovery", f"{base_url}/agent/v1.0/tools", api_key
    )

    _banner("RESULT SUMMARY")
    all_pass = all(results.values())
    for label, passed in results.items():
        icon = "✓" if passed else "✗"
        status = "PASS" if passed else "FAIL"
        print(f"  {icon}  {label.upper():<15} {status}")

    if all_pass:
        print("\n  ✓  Booking API is fully reachable.")
    else:
        first_failure = next(k for k, v in results.items() if not v)
        print(f"\n  ✗  First failure at: {first_failure.upper()}")
        if first_failure in ("dns", "tcp"):
            print("     Booking API host is unreachable — network/DNS issue.")
        else:
            print("     Network is reachable but the API returned an error.")
            print("     Check BOOKING_API_KEY and API endpoint paths.")
