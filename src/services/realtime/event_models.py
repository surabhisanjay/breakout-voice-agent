from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


SUPPORTED_REALTIME_EVENTS = (
    "call.started",
    "call.updated",
    "call.ended",
    "transcript.chunk",
    "sentiment.updated",
    "intent.updated",
    "booking.created",
    "booking.updated",
    "timeline.event",
    "summary.ready",
    "escalation.created",
    "agent.state",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class RealtimeEventEnvelope:
    event: str
    call_id: str
    payload: dict[str, Any]
    timestamp: str = field(default_factory=utc_now_iso)
    version: int = 1

    def __post_init__(self) -> None:
        if self.event not in SUPPORTED_REALTIME_EVENTS:
            raise ValueError(f"Unsupported realtime event: {self.event}")
        if not str(self.call_id).strip():
            raise ValueError("call_id is required")
        if not isinstance(self.payload, dict):
            raise TypeError("payload must be a dictionary")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "event": self.event,
            "call_id": self.call_id,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }


def live_call_channel(call_id: str) -> str:
    clean = str(call_id).strip()
    if not clean:
        raise ValueError("call_id is required")
    return f"live_call:{clean}"
