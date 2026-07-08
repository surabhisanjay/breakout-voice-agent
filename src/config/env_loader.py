from __future__ import annotations

import os
import sys
from pathlib import Path


def load_project_env(base_dir: str | Path | None = None) -> None:
    """
    Load project-level .env values into os.environ.

    Existing environment variables win so deployed values and test monkeypatches
    are not overwritten. During pytest collection we intentionally skip loading
    local secrets from .env.
    """
    if "pytest" in sys.modules:
        return

    root = Path(base_dir) if base_dir is not None else Path(__file__).resolve().parents[2]
    env_file = root / ".env"
    if not env_file.exists():
        return

    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def booking_credentials_present() -> bool:
    return bool(os.environ.get("BOOKING_API_KEY") and os.environ.get("BOOKING_BASE_URL"))


def booking_provider_label() -> str:
    provider_mode = os.environ.get("BOOKING_PROVIDER", "auto").strip().lower()
    if provider_mode in {"simulator", "mock", "offline"}:
        return "simulator"
    if booking_credentials_present():
        return "live-configured"
    return "simulator"


def should_use_live_booking() -> bool:
    provider_mode = os.environ.get("BOOKING_PROVIDER", "auto").strip().lower()
    if provider_mode in {"simulator", "mock", "offline"}:
        return False
    return booking_credentials_present()
