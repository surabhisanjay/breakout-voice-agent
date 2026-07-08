#!/usr/bin/env python3
"""
scripts/system_diagnostics.py
------------------------------
Standalone diagnostics for the Breakout Agent environment.

Checks:
  - Python version & package versions
  - Environment variables (masked)
  - DNS resolution for all external hosts
  - TCP connectivity (port 443)
  - OpenAI API reachability
  - Booking API reachability
  - Per-call latency breakdown

IMPORTANT: socket.getaddrinfo() ignores Python timeout arguments at the OS
level. This script uses thread-based DNS checks to enforce real wall-clock
limits.

Run from the Breakout-Agent directory:
    python scripts/system_diagnostics.py
"""

from __future__ import annotations

import concurrent.futures
import os
import platform
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

# ── helpers ─────────────────────────────────────────────────────────────────

TIMEOUT = 3.0  # hard limit for every network probe


def _resolve_with_timeout(hostname: str, timeout: float = TIMEOUT) -> str | None:
    """
    Resolve hostname in a thread so we get a real wall-clock timeout.
    socket.getaddrinfo() ignores Python's timeout= argument.
    Returns the IP string or None on failure/timeout.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(socket.getaddrinfo, hostname, 443,
                             socket.AF_UNSPEC, socket.SOCK_STREAM)
        try:
            addrs = future.result(timeout=timeout)
            return addrs[0][4][0] if addrs else "?"
        except (concurrent.futures.TimeoutError, socket.gaierror, OSError):
            return None


def _section(title: str) -> None:
    width = 60
    print(f"\n{'═' * width}")
    print(f"  {title}")
    print(f"{'═' * width}")


def _ok(label: str, value: str) -> None:
    print(f"  ✓  {label:<35} {value}")


def _warn(label: str, value: str) -> None:
    print(f"  ⚠  {label:<35} {value}")


def _fail(label: str, value: str) -> None:
    print(f"  ✗  {label:<35} {value}")


def _mask(value: str) -> str:
    if not value:
        return "<not set>"
    return value[:8] + "…" + value[-4:] if len(value) > 12 else "****"


# ── 1. Environment ───────────────────────────────────────────────────────────

def check_environment() -> None:
    _section("1. ENVIRONMENT")

    _ok("Python", sys.version.replace("\n", " "))
    _ok("Platform", platform.platform())

    # Package versions
    packages = ["openai", "httpx", "httpcore"]
    for pkg in packages:
        try:
            import importlib.metadata as meta
            ver = meta.version(pkg)
            _ok(f"{pkg} version", ver)
        except Exception:
            _warn(f"{pkg} version", "not installed")

    # Env vars (masked)
    env_vars = {
        "OPENAI_API_KEY":   os.environ.get("OPENAI_API_KEY", ""),
        "OPENAI_MODEL":     os.environ.get("OPENAI_MODEL", ""),
        "BOOKING_API_KEY":  os.environ.get("BOOKING_API_KEY", ""),
        "BOOKING_BASE_URL": os.environ.get("BOOKING_BASE_URL", ""),
    }
    for var, val in env_vars.items():
        if var.endswith("KEY"):
            display = _mask(val)
        else:
            display = val or "<not set>"
        fn = _ok if val else _warn
        fn(var, display)


# ── 2. DNS ───────────────────────────────────────────────────────────────────

def check_dns() -> dict[str, float | None]:
    _section("2. DNS RESOLUTION")

    hosts = [
        "api.openai.com",
        "bs.kreeda.icu",
        "8.8.8.8",  # baseline — should always succeed
    ]

    results: dict[str, float | None] = {}
    for host in hosts:
        t0 = time.perf_counter()
        try:
            addrs = socket.getaddrinfo(host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            ip = addrs[0][4][0] if addrs else "?"
            _ok(f"DNS {host}", f"{elapsed_ms:.0f} ms  →  {ip}")
            results[host] = elapsed_ms
        except socket.gaierror as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            _fail(f"DNS {host}", f"FAILED after {elapsed_ms:.0f} ms — {exc}")
            results[host] = None

    return results


# ── 3. TCP connectivity ───────────────────────────────────────────────────────

def check_tcp() -> None:
    _section("3. TCP CONNECTIVITY (port 443)")

    targets = [
        ("api.openai.com", 443),
        ("bs.kreeda.icu", 443),
    ]

    for host, port in targets:
        t0 = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=TIMEOUT):
                elapsed_ms = (time.perf_counter() - t0) * 1000
                _ok(f"TCP {host}:{port}", f"{elapsed_ms:.0f} ms")
        except (socket.timeout, OSError) as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            _fail(f"TCP {host}:{port}", f"FAILED after {elapsed_ms:.0f} ms — {exc}")


# ── 4. OpenAI HTTP probe ──────────────────────────────────────────────────────

def check_openai_http() -> None:
    _section("4. OPENAI HTTP PROBE")

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        _warn("OpenAI probe", "Skipped — OPENAI_API_KEY not set")
        return

    url = "https://api.openai.com/v1/models"
    req = Request(url, headers={"Authorization": f"Bearer {api_key}"})
    t0 = time.perf_counter()
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            _ok("GET /v1/models", f"{elapsed_ms:.0f} ms  status={resp.status}")
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _fail("GET /v1/models", f"FAILED after {elapsed_ms:.0f} ms — {exc}")


# ── 5. Booking API HTTP probe ─────────────────────────────────────────────────

def check_booking_http() -> None:
    _section("5. BOOKING API HTTP PROBE")

    base_url = os.environ.get("BOOKING_BASE_URL", "https://bs.kreeda.icu")
    api_key = os.environ.get("BOOKING_API_KEY", "")

    if not api_key:
        _warn("Booking API probe", "Skipped — BOOKING_API_KEY not set")
        return

    url = f"{base_url.rstrip('/')}/book/v1.0/locations"
    req = Request(url, headers={"X-API-Key": api_key, "Accept": "application/json"})
    t0 = time.perf_counter()
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            _ok("GET /book/v1.0/locations", f"{elapsed_ms:.0f} ms  status={resp.status}")
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _fail("GET /book/v1.0/locations", f"FAILED after {elapsed_ms:.0f} ms — {exc}")

    url2 = f"{base_url.rstrip('/')}/agent/v1.0/tools"
    req2 = Request(url2, headers={"X-API-Key": api_key, "Accept": "application/json"})
    t0 = time.perf_counter()
    try:
        with urlopen(req2, timeout=TIMEOUT) as resp:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            _ok("GET /agent/v1.0/tools", f"{elapsed_ms:.0f} ms  status={resp.status}")
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _fail("GET /agent/v1.0/tools", f"FAILED after {elapsed_ms:.0f} ms — {exc}")


# ── 6. OpenAI SDK smoke test ──────────────────────────────────────────────────

def check_openai_sdk() -> None:
    _section("6. OPENAI SDK SMOKE TEST")

    try:
        import openai
    except ImportError:
        _fail("openai import", "Package not installed")
        return

    api_key = os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
    if not api_key:
        _warn("OpenAI SDK test", "Skipped — OPENAI_API_KEY not set")
        return

    t0 = time.perf_counter()
    try:
        client = openai.OpenAI(api_key=api_key, timeout=TIMEOUT)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=5,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        reply = response.choices[0].message.content.strip() if response.choices else "?"
        _ok(f"SDK chat ({model})", f"{elapsed_ms:.0f} ms  →  \"{reply}\"")
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _fail(f"SDK chat ({model})", f"FAILED after {elapsed_ms:.0f} ms — {exc}")


# ── 7. Summary ────────────────────────────────────────────────────────────────

def summarize(dns_results: dict[str, float | None]) -> None:
    _section("SUMMARY")

    failures = [host for host, ms in dns_results.items() if ms is None]
    if failures:
        _fail("DNS failures detected", ", ".join(failures))
        print("""
  DIAGNOSIS:
    DNS resolution is failing for one or more external hosts.
    This is likely the root cause of the 33-second OpenAI hang.

  NEXT STEPS:
    1. Check your internet connection.
    2. Try: nslookup api.openai.com
    3. If on a corporate network, check VPN / proxy settings.
    4. Check /etc/resolv.conf or System Preferences → Network → DNS.
    5. Run:  python scripts/test_dns.py  for a deeper DNS investigation.
""")
    else:
        _ok("DNS", "All hosts resolved successfully")
        print("""
  If DNS is OK but latency is still high:
    - Run: python scripts/test_openai_connection.py
    - Run: python scripts/test_booking_connection.py
""")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Load .env if present
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

    print("\n" + "█" * 60)
    print("  BREAKOUT AGENT — SYSTEM DIAGNOSTICS")
    print("█" * 60)
    print(f"  Timeout limit per probe: {TIMEOUT}s")

    check_environment()
    dns_results = check_dns()
    check_tcp()
    check_openai_http()
    check_booking_http()
    check_openai_sdk()
    summarize(dns_results)
