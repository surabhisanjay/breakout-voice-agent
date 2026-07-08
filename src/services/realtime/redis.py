from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .configuration import RealtimeConfig


logger = logging.getLogger(__name__)


class RedisLike(Protocol):
    def publish(self, channel: str, message: str) -> int: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class RedisPublishResult:
    attempted: bool
    sent: bool
    channel: str
    subscriber_count: int = 0
    latency_ms: float = 0.0
    retry_count: int = 0
    reason: str = ""


class RedisEventTransport:
    def __init__(self, config: RealtimeConfig | None = None, client: RedisLike | None = None) -> None:
        self.config = config or RealtimeConfig.from_env()
        self._client = client
        self._injected_client = client is not None
        self._missing_client_logged = False

    def publish(self, channel: str, message: str) -> RedisPublishResult:
        if not self.config.enabled:
            return RedisPublishResult(attempted=False, sent=False, channel=channel, reason="disabled")
        started = time.perf_counter()
        retry_count = 0
        last_reason = ""
        attempts = self.config.retry_attempts + 1
        for attempt in range(attempts):
            try:
                client = self._get_client()
                if client is None:
                    return RedisPublishResult(
                        attempted=False,
                        sent=False,
                        channel=channel,
                        reason="redis_client_unavailable",
                    )
                subscribers = int(client.publish(channel, message) or 0)
                latency_ms = round((time.perf_counter() - started) * 1000, 1)
                return RedisPublishResult(
                    attempted=True,
                    sent=True,
                    channel=channel,
                    subscriber_count=subscribers,
                    latency_ms=latency_ms,
                    retry_count=retry_count,
                )
            except Exception as exc:  # pragma: no cover - exact redis exceptions vary by version
                last_reason = exc.__class__.__name__
                if attempt < attempts - 1:
                    retry_count += 1
                    self._reset_client()
                    continue
                logger.warning(
                    "REALTIME_REDIS_PUBLISH_FAILED channel=%s reason=%s retries=%s",
                    channel,
                    last_reason,
                    retry_count,
                )
        return RedisPublishResult(
            attempted=True,
            sent=False,
            channel=channel,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            retry_count=retry_count,
            reason=last_reason or "publish_failed",
        )

    def _get_client(self) -> RedisLike | None:
        if self._client is not None:
            return self._client
        try:
            import redis  # type: ignore[import-not-found]
        except ModuleNotFoundError:
            if not self._missing_client_logged:
                logger.warning("REALTIME_REDIS_CLIENT_MISSING install redis package to enable realtime publishing")
                self._missing_client_logged = True
            return None
        self._client = redis.Redis.from_url(
            self.config.redis_url,
            socket_timeout=self.config.timeout_seconds,
            socket_connect_timeout=self.config.timeout_seconds,
            decode_responses=True,
        )
        return self._client

    def _reset_client(self) -> None:
        client = self._client
        if not self._injected_client:
            self._client = None
        if client is None:
            return
        try:
            client.close()
        except Exception:
            logger.debug("REALTIME_REDIS_CLOSE_FAILED", exc_info=True)
