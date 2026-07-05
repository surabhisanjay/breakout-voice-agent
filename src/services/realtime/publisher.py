from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from typing import Any

from .configuration import RealtimeConfig
from .event_models import RealtimeEventEnvelope, live_call_channel, utc_now_iso
from .redis import RedisEventTransport, RedisPublishResult


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscriptChunk:
    timestamp: str
    speaker: str
    text: str
    event_type: str = "final"


class RealtimeEventPublisher:
    def __init__(self, transport: RedisEventTransport | None = None) -> None:
        self.transport = transport or RedisEventTransport()

    def publish(
        self,
        event: str,
        call_id: str,
        payload: dict[str, Any] | None = None,
        *,
        timestamp: str | None = None,
    ) -> RedisPublishResult:
        body = RealtimeEventEnvelope(
            event=event,
            call_id=call_id,
            timestamp=timestamp or utc_now_iso(),
            payload=payload or {},
        )
        channel = live_call_channel(call_id)
        encoded = json.dumps(body.to_dict(), ensure_ascii=False, separators=(",", ":"), default=str)
        result = self.transport.publish(channel, encoded)
        logger.info(
            "REALTIME_EVENT_PUBLISH event=%s channel=%s sent=%s status=%s latency_ms=%.1f retry_count=%s",
            event,
            channel,
            result.sent,
            result.reason or "ok",
            result.latency_ms,
            result.retry_count,
        )
        return result

    def publish_transcript_chunk(self, call_id: str, chunk: TranscriptChunk) -> RedisPublishResult:
        return self.publish("transcript.chunk", call_id, asdict(chunk), timestamp=chunk.timestamp)


_default_lock = threading.Lock()
_default_publisher: RealtimeEventPublisher | None = None


def get_default_realtime_publisher() -> RealtimeEventPublisher:
    global _default_publisher
    with _default_lock:
        if _default_publisher is None:
            _default_publisher = RealtimeEventPublisher(RedisEventTransport(RealtimeConfig.from_env()))
        return _default_publisher


def set_default_realtime_publisher(publisher: RealtimeEventPublisher | None) -> None:
    global _default_publisher
    with _default_lock:
        _default_publisher = publisher


def transcript_chunk_from_turn(turn: dict[str, Any]) -> TranscriptChunk | None:
    text = str(turn.get("text") or turn.get("content") or turn.get("message") or "").strip()
    if not text:
        return None

    speaker = str(turn.get("speaker") or turn.get("speaker_type") or turn.get("role") or "unknown").strip().lower()
    if speaker in {"agent", "assistant_agent", "bot"}:
        speaker = "assistant"
    elif speaker in {"user", "human"}:
        speaker = "customer"
    event_type = str(turn.get("event_type") or "final").strip().lower() or "final"
    timestamp = str(turn.get("timestamp") or turn.get("created_at") or utc_now_iso()).strip()
    return TranscriptChunk(timestamp=timestamp, speaker=speaker or "unknown", text=text, event_type=event_type)
