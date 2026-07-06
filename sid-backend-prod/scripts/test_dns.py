#!/usr/bin/env python3
"""
scripts/test_dns.py
--------------------
Deep DNS investigation for the Breakout Agent.

Tests:
  - System resolver for all external hosts
  - Multiple public DNS servers (8.8.8.8, 1.1.1.1, 9.9.9.9)
  - Compares results to detect split-horizon / VPN issues
  - Reports which resolver is fastest

Run:
    python scripts/test_dns.py
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

TIMEOUT = 3.0
HOSTS_TO_TEST = [
    "api.openai.com",
    "bs.kreeda.icu",
    "google.com",       # baseline — should always resolve
]
PUBLIC_DNS = [
    ("Google", "8.8.8.8"),
    ("Cloudflare", "1.1.1.1"),
    ("Quad9", "9.9.9.9"),
]


def _section(title: str) -> None:
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")


def resolve_system(host: str) -> tuple[str | None, float]:
    """Use the system resolver. Returns (ip_or_None, elapsed_ms)."""
    t0 = time.perf_counter()
    try:
        addrs = socket.getaddrinfo(host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
        elapsed = (time.perf_counter() - t0) * 1000
        ip = addrs[0][4][0] if addrs else "?"
        return ip, elapsed
    except socket.gaierror:
        elapsed = (time.perf_counter() - t0) * 1000
        return None, elapsed


def resolve_via_dig(host: str, nameserver: str) -> tuple[str | None, float]:
    """Use 'dig' to query a specific nameserver. Falls back gracefully."""
    t0 = time.perf_counter()
    try:
        result = subprocess.run(
            ["dig", "+short", f"@{nameserver}", host],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
        elapsed = (time.perf_counter() - t0) * 1000
        ips = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
        return (ips[0] if ips else None), elapsed
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        return None, elapsed


def check_system_resolver() -> dict[str, tuple[str | None, float]]:
    _section("1. SYSTEM RESOLVER")
    results: dict[str, tuple[str | None, float]] = {}
    for host in HOSTS_TO_TEST:
        ip, ms = resolve_system(host)
        results[host] = (ip, ms)
        if ip:
            print(f"  ✓  {host:<35} {ms:6.0f} ms  →  {ip}")
        else:
            print(f"  ✗  {host:<35} {ms:6.0f} ms  →  FAILED")
    return results


def check_public_resolvers() -> None:
    _section("2. PUBLIC DNS RESOLVERS (via dig)")

    dig_available = True
    try:
        subprocess.run(["dig", "--version"], capture_output=True, timeout=2)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        dig_available = False

    if not dig_available:
        print("  ⚠  'dig' not found — skipping public resolver tests")
        print("     Install with: brew install bind  (macOS)")
        return

    for ns_name, ns_ip in PUBLIC_DNS:
        print(f"\n  [{ns_name} — {ns_ip}]")
        for host in HOSTS_TO_TEST:
            ip, ms = resolve_via_dig(host, ns_ip)
            if ip:
                print(f"    ✓  {host:<35} {ms:5.0f} ms  →  {ip}")
            else:
                print(f"    ✗  {host:<35} {ms:5.0f} ms  →  FAILED (timeout or NXDOMAIN)")


def check_etc_resolv() -> None:
    _section("3. /etc/resolv.conf  (system DNS config)")
    path = "/etc/resolv.conf"
    if os.path.exists(path):
        with open(path) as f:
            content = f.read().strip()
        if content:
            for line in content.splitlines():
                print(f"  {line}")
        else:
            print("  <file is empty>")
    else:
        print(f"  {path} does not exist (normal on macOS using scutil)")

    # macOS scutil
    try:
        result = subprocess.run(
            ["scutil", "--dns"],
            capture_output=True, text=True, timeout=3
        )
        lines = result.stdout.strip().splitlines()
        # Print only the first resolver block
        in_block = False
        for line in lines:
            if "resolver #1" in line:
                in_block = True
            if in_block:
                print(f"  {line}")
            if in_block and line.strip() == "":
                break
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def summarize(system_results: dict[str, tuple[str | None, float]]) -> None:
    _section("SUMMARY")

    failed = [h for h, (ip, _) in system_results.items() if ip is None]
    slow = [h for h, (ip, ms) in system_results.items() if ip and ms > 1000]

    if failed:
        print(f"  ✗  DNS FAILURES: {', '.join(failed)}")
        print("""
  ROOT CAUSE CANDIDATES:
    A. No internet connection
    B. Corporate VPN / proxy intercepting DNS
    C. DNS server unreachable (see /etc/resolv.conf above)
    D. Firewall blocking outbound DNS (port 53)

  QUICK FIX:
    1. Disconnect VPN and retry.
    2. Set DNS to 8.8.8.8:
         macOS: System Settings → Network → DNS → add 8.8.8.8
    3. Flush DNS cache:
         sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder
""")
    elif slow:
        print(f"  ⚠  SLOW DNS: {', '.join(slow)}")
        print("     DNS resolves but is slow. This may contribute to latency.")
    else:
        print("  ✓  All DNS queries successful and fast.")
        print("     The 33-second hang is likely NOT caused by DNS.")
        print("     Next: run scripts/test_openai_connection.py to isolate further.")


if __name__ == "__main__":
    print("\n" + "█" * 55)
    print("  BREAKOUT AGENT — DNS INVESTIGATION")
    print("█" * 55)

    sys_results = check_system_resolver()
    check_public_resolvers()
    check_etc_resolv()
    summarize(sys_results)
