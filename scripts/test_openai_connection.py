#!/usr/bin/env python3
"""
scripts/test_openai_connection.py
-----------------------------------
Verifies OpenAI connectivity independently of the main agent.

Tests (each with 3-second timeout):
  1. DNS resolution for api.openai.com
  2. TCP handshake to api.openai.com:443
  3. HTTPS /v1/models via urllib (no SDK)
  4. OpenAI SDK chat completion (gpt-4.1-mini, 1 token)

Prints per-step latency so you can see exactly where time is spent.

Run:
    python scripts/test_openai_connection.py
"""

from __future__ import annotations

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


def test_dns() -> bool:
    _banner("TEST 1: DNS — api.openai.com")
    t0 = time.perf_counter()
    try:
        addrs = socket.getaddrinfo("api.openai.com", 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
        ms = (time.perf_counter() - t0) * 1000
        ip = addrs[0][4][0] if addrs else "?"
        print(f"  ✓  PASS   {ms:.0f} ms  →  {ip}")
        return True
    except socket.gaierror as exc:
        ms = (time.perf_counter() - t0) * 1000
        print(f"  ✗  FAIL   {ms:.0f} ms  →  {exc}")
        print("""
  This is your root cause.
  DNS cannot resolve api.openai.com.
  Fix DNS first — see scripts/test_dns.py.
""")
        return False


def test_tcp() -> bool:
    _banner("TEST 2: TCP — api.openai.com:443")
    t0 = time.perf_counter()
    try:
        with socket.create_connection(("api.openai.com", 443), timeout=TIMEOUT):
            ms = (time.perf_counter() - t0) * 1000
            print(f"  ✓  PASS   {ms:.0f} ms")
            return True
    except (socket.timeout, OSError) as exc:
        ms = (time.perf_counter() - t0) * 1000
        print(f"  ✗  FAIL   {ms:.0f} ms  →  {exc}")
        return False


def test_https_no_sdk(api_key: str) -> bool:
    _banner("TEST 3: HTTPS GET /v1/models (no SDK)")
    url = "https://api.openai.com/v1/models"
    req = Request(url, headers={"Authorization": f"Bearer {api_key}"})
    t0 = time.perf_counter()
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            ms = (time.perf_counter() - t0) * 1000
            print(f"  ✓  PASS   {ms:.0f} ms  status={resp.status}")
            return True
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        print(f"  ✗  FAIL   {ms:.0f} ms  →  {exc}")
        return False


def test_sdk_chat(api_key: str, model: str) -> bool:
    _banner(f"TEST 4: OpenAI SDK chat  (model={model}  timeout={TIMEOUT}s)")
    try:
        import openai
    except ImportError:
        print("  ✗  SKIP   openai package not installed")
        return False

    t0 = time.perf_counter()
    try:
        client = openai.OpenAI(api_key=api_key, timeout=TIMEOUT)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with: ONLINE"}],
            max_tokens=5,
        )
        ms = (time.perf_counter() - t0) * 1000
        reply = response.choices[0].message.content.strip() if response.choices else "?"
        print(f"  ✓  PASS   {ms:.0f} ms  →  \"{reply}\"")
        return True
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        print(f"  ✗  FAIL   {ms:.0f} ms  →  {type(exc).__name__}: {exc}")
        return False


if __name__ == "__main__":
    _load_env()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

    print("\n" + "█" * 55)
    print("  BREAKOUT AGENT — OPENAI CONNECTION TEST")
    print("█" * 55)
    print(f"  API Key : {'SET (' + api_key[:8] + '…)' if api_key else 'NOT SET'}")
    print(f"  Model   : {model}")
    print(f"  Timeout : {TIMEOUT}s per test")

    if not api_key:
        print("\n  ✗  OPENAI_API_KEY is not set. Cannot run tests.")
        sys.exit(1)

    results = {
        "dns":   test_dns(),
        "tcp":   test_tcp(),
        "https": test_https_no_sdk(api_key),
        "sdk":   test_sdk_chat(api_key, model),
    }

    _banner("RESULT SUMMARY")
    all_pass = all(results.values())
    for label, passed in results.items():
        icon = "✓" if passed else "✗"
        status = "PASS" if passed else "FAIL"
        print(f"  {icon}  {label.upper():<10} {status}")

    if all_pass:
        print("\n  ✓  OpenAI connectivity is fully functional.")
        print("     If the agent still hangs, the issue is elsewhere (e.g., Booking API).")
    else:
        first_failure = next(k for k, v in results.items() if not v)
        print(f"\n  ✗  First failure at: {first_failure.upper()}")
        print("     Fix this step before diagnosing further.")
