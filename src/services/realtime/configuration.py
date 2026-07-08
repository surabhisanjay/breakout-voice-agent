from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class RealtimeConfig:
    redis_url: str = ""
    enabled: bool = False
    timeout_seconds: float = 1.5
    retry_attempts: int = 1

    @classmethod
    def from_env(cls) -> "RealtimeConfig":
        redis_url = (
            os.environ.get("REDIS_URL")
            or os.environ.get("REALTIME_REDIS_URL")
            or os.environ.get("CLOSIRO_REDIS_URL")
            or ""
        ).strip()
        return cls(
            redis_url=redis_url,
            enabled=_env_bool("REALTIME_REDIS_ENABLED", bool(redis_url)),
            timeout_seconds=_env_float("REALTIME_REDIS_TIMEOUT", 1.5),
            retry_attempts=max(_env_int("REALTIME_REDIS_RETRIES", 1), 0),
        )
