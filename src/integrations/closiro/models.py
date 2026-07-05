from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ClosiroWebhookPayload:
    event_type: str
    endpoint: str
    body: dict[str, Any]
    sync_key: str


@dataclass(frozen=True)
class ClosiroSyncResult:
    attempted: bool
    sent: bool
    status_code: int | None = None
    response: Any = None
    reason: str = ""
    endpoint: str = ""
    event_type: str = ""
    latency_ms: float = 0.0
    retry_count: int = 0
    headers: dict[str, str] = field(default_factory=dict)
