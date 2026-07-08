"""
Test suite for the Redis-based real-time LLM streaming pipeline.

Covers:
1. ResponseComposer streaming + Redis publish
2. Graceful fallback when Redis is unavailable
3. Session isolation (no cross-talk between sessions)
4. Raw Redis PubSub infrastructure
5. dispatch() session_id wiring
"""
from __future__ import annotations

import json
import os
import sys
import time
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import redis as real_redis
from src.core.conversation_modes import ConversationMode
from src.response_composer import ResponseComposer

# ---------------------------------------------------------------------------
# Mock OpenAI streaming
# ---------------------------------------------------------------------------

class MockDelta:
    def __init__(self, content):
        self.content = content

class MockChoice:
    def __init__(self, content):
        self.delta = MockDelta(content)

class MockStreamChunk:
    def __init__(self, content):
        self.choices = [MockChoice(content)]


def make_stream_chunks(text):
    words = text.split()
    chunks = []
    for i, word in enumerate(words):
        token = word if i == 0 else f" {word}"
        chunks.append(MockStreamChunk(token))
    chunks.append(MockStreamChunk(None))
    return chunks


class MockChatCompletions:
    def __init__(self, text):
        self.text = text

    def create(self, **kwargs):
        assert kwargs.get("stream") is True, "Expected stream=True"
        return iter(make_stream_chunks(self.text))


class MockChat:
    def __init__(self, text):
        self.completions = MockChatCompletions(text)


class MockOpenAIClient:
    def __init__(self, text="Great choice!", **kwargs):
        self.chat = MockChat(text)


# ---------------------------------------------------------------------------
# Redis message collector
# ---------------------------------------------------------------------------

class RedisCollector:
    def __init__(self, channel):
        self.channel = channel
        self.messages = []
        self._stop = threading.Event()
        self._client = real_redis.Redis(host="localhost", port=6379, decode_responses=True)
        self._pubsub = self._client.pubsub()
        self._thread = None

    def start(self):
        self._pubsub.subscribe(self.channel)
        time.sleep(0.3)
        self._pubsub.get_message(timeout=1.0)  # drain subscribe confirmation
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def _listen(self):
        while not self._stop.is_set():
            msg = self._pubsub.get_message(timeout=0.5)
            if msg and msg["type"] == "message":
                try:
                    self.messages.append(json.loads(msg["data"]))
                except Exception:
                    pass

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        try:
            self._pubsub.unsubscribe()
            self._client.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Redis availability
# ---------------------------------------------------------------------------

def _redis_available():
    try:
        r = real_redis.Redis(host="localhost", port=6379, socket_connect_timeout=1)
        r.ping()
        r.close()
        return True
    except Exception:
        return False

REDIS_AVAILABLE = _redis_available()
requires_redis = pytest.mark.skipif(not REDIS_AVAILABLE, reason="Redis not running")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

MOCK_STATE = {
    "customer_name": "Ravi",
    "location": "Koramangala",
    "participants": "6",
    "age_group": "adults",
    "intent": "escape_room_inquiry",
    "conversation_mode": "recommendation",
}

# IMPORTANT: draft must be > 3 words to avoid the fast-path bypass in compose()
LONG_DRAFT = "I would recommend the Mystery Mansion escape room for your group of six adults"


def _make_composer():
    prompt_path = PROJECT_DIR / "prompts" / "breakout_personality_prompt.txt"
    if not prompt_path.exists():
        prompt_path = PROJECT_DIR / "tests" / "_test_prompt.txt"
        prompt_path.write_text("You are a helpful assistant.", encoding="utf-8")
    rc = ResponseComposer(prompt_path=str(prompt_path), model="gpt-4o-mini")
    rc.enabled = True
    return rc


def _run_compose(composer, expected_text, session_id="", extra_env=None):
    """Run compose with a mocked OpenAI client."""
    env = {"OPENAI_API_KEY": "test-key-fake-123"}
    if extra_env:
        env.update(extra_env)

    with patch.dict(os.environ, env, clear=False):
        composer.enabled = True
        with patch("openai.OpenAI", return_value=MockOpenAIClient(expected_text)):
            result = composer.compose(
                draft=LONG_DRAFT,
                message="We are six adults looking for a fun escape room experience",
                state=MOCK_STATE,
                intent="escape_room_inquiry",
                mode=ConversationMode.RECOMMENDATION,
                session_id=session_id,
            )
    return result


# ===========================================================================
# TEST 1: ResponseComposer streaming
# ===========================================================================

class TestResponseComposerStreaming:

    def test_compose_streams_and_returns_full_text(self):
        """compose() should use stream=True and return the full accumulated text."""
        composer = _make_composer()
        result = _run_compose(composer, "Great choice for your team of six!", session_id="unit-001")
        assert result == "Great choice for your team of six!"
        assert composer.last_error == ""

    def test_compose_returns_draft_when_no_api_key(self):
        """Without an API key, compose() falls back to draft."""
        composer = _make_composer()
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            composer.enabled = True
            result = composer.compose(
                draft=LONG_DRAFT,
                message="hello there how are you doing today",
                state=MOCK_STATE,
                intent="escape_room_inquiry",
                mode=ConversationMode.RECOMMENDATION,
            )
        # enabled becomes False because api_key is empty (line 57: enabled = bool(api_key) and self.enabled)
        assert result == LONG_DRAFT

    def test_compose_without_session_id_skips_redis(self):
        """When session_id is empty, Redis should not be touched."""
        composer = _make_composer()
        expected = "No redis needed for this response at all!"
        with patch("src.response_composer.redis") as mock_redis_mod:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-123"}, clear=False):
                composer.enabled = True
                with patch("openai.OpenAI", return_value=MockOpenAIClient(expected)):
                    result = composer.compose(
                        draft=LONG_DRAFT,
                        message="We are six adults looking for fun",
                        state=MOCK_STATE,
                        intent="escape_room_inquiry",
                        mode=ConversationMode.RECOMMENDATION,
                        session_id="",
                    )
            mock_redis_mod.Redis.from_url.assert_not_called()
        assert result == expected


# ===========================================================================
# TEST 2: Redis PubSub publish (requires live Redis)
# ===========================================================================

@requires_redis
class TestRedisPublish:

    def test_compose_publishes_chunks_to_redis(self):
        """Streaming tokens should be published to live_call:{session_id}."""
        composer = _make_composer()
        session_id = f"redis-pub-{int(time.time())}"
        expected_text = "Hello world from the streaming escape room agent!"
        channel = f"live_call:{session_id}"

        collector = RedisCollector(channel)
        collector.start()

        try:
            result = _run_compose(composer, expected_text, session_id=session_id)
            time.sleep(0.8)
        finally:
            collector.stop()

        assert result == expected_text
        assert len(collector.messages) > 0, "No messages received on Redis channel"

        for msg in collector.messages:
            assert msg["event"] == "transcript.chunk"
            assert msg["payload"]["speaker"] == "assistant"
            assert msg["payload"]["event_type"] == "interim"

        last_text = collector.messages[-1]["payload"]["text"]
        assert last_text == expected_text

    def test_chunks_are_incrementally_accumulated(self):
        """Each successive chunk should contain progressively more text."""
        composer = _make_composer()
        session_id = f"incr-{int(time.time())}"
        expected_text = "one two three four five six seven eight"
        channel = f"live_call:{session_id}"

        collector = RedisCollector(channel)
        collector.start()

        try:
            _run_compose(composer, expected_text, session_id=session_id)
            time.sleep(0.8)
        finally:
            collector.stop()

        texts = [m["payload"]["text"] for m in collector.messages]
        assert len(texts) >= 2, f"Expected multiple chunks, got {len(texts)}"
        for i in range(1, len(texts)):
            assert len(texts[i]) > len(texts[i - 1]), (
                f"Chunk {i} ({texts[i]!r}) not longer than chunk {i-1} ({texts[i-1]!r})"
            )


# ===========================================================================
# TEST 3: Session isolation
# ===========================================================================

@requires_redis
class TestSessionIsolation:

    def test_no_cross_session_leakage(self):
        """Messages for session A must not appear on session B."""
        composer = _make_composer()
        ts = int(time.time())
        session_a = f"iso-A-{ts}"
        session_b = f"iso-B-{ts}"

        collector_a = RedisCollector(f"live_call:{session_a}")
        collector_b = RedisCollector(f"live_call:{session_b}")
        collector_a.start()
        collector_b.start()

        try:
            _run_compose(composer, "message for session A only delivered here", session_id=session_a)
            time.sleep(0.8)
        finally:
            collector_a.stop()
            collector_b.stop()

        assert len(collector_a.messages) > 0, "Session A should have received messages"
        assert len(collector_b.messages) == 0, "Session B should NOT have received any messages"


# ===========================================================================
# TEST 4: Graceful fallback when Redis is down
# ===========================================================================

class TestRedisFailureFallback:

    def test_compose_works_without_redis(self):
        """compose() should return LLM text even when Redis is unreachable."""
        composer = _make_composer()
        result = _run_compose(
            composer,
            "Works perfectly without Redis connection available!",
            session_id="no-redis-session",
            extra_env={"REDIS_URL": "redis://bad-host:9999"},
        )
        assert result == "Works perfectly without Redis connection available!"
        assert composer.last_error == ""


# ===========================================================================
# TEST 5: Raw Redis PubSub infrastructure
# ===========================================================================

@requires_redis
class TestRawRedisPubSub:

    def test_publish_and_subscribe_round_trip(self):
        """Publish a message and verify the subscriber receives it."""
        channel = f"test_roundtrip_{int(time.time())}"

        sub = real_redis.Redis(host="localhost", port=6379, decode_responses=True)
        pub = real_redis.Redis(host="localhost", port=6379, decode_responses=True)

        ps = sub.pubsub()
        ps.subscribe(channel)
        time.sleep(0.3)
        ps.get_message(timeout=1.0)  # drain subscribe msg

        payload = {"event": "transcript.chunk", "payload": {"text": "hello", "speaker": "assistant"}}
        pub.publish(channel, json.dumps(payload))
        time.sleep(0.5)

        msg = ps.get_message(timeout=1.0)

        ps.unsubscribe()
        sub.close()
        pub.close()

        assert msg is not None, "No message received from Redis PubSub"
        assert msg["type"] == "message"
        parsed = json.loads(msg["data"])
        assert parsed["event"] == "transcript.chunk"
        assert parsed["payload"]["text"] == "hello"

    def test_multiple_messages_arrive_in_order(self):
        """Multiple publishes should arrive in FIFO order."""
        channel = f"order_test_{int(time.time())}"

        sub = real_redis.Redis(host="localhost", port=6379, decode_responses=True)
        pub = real_redis.Redis(host="localhost", port=6379, decode_responses=True)

        ps = sub.pubsub()
        ps.subscribe(channel)
        time.sleep(0.3)
        ps.get_message(timeout=1.0)  # drain subscribe msg

        words = ["alpha", "bravo", "charlie", "delta"]
        for word in words:
            pub.publish(channel, json.dumps({"word": word}))

        time.sleep(0.5)

        received = []
        for _ in range(10):
            msg = ps.get_message(timeout=0.5)
            if msg is None:
                break
            if msg["type"] == "message":
                parsed = json.loads(msg["data"])
                received.append(parsed["word"])

        ps.unsubscribe()
        sub.close()
        pub.close()

        assert received == words, f"Expected {words}, got {received}"


# ===========================================================================
# TEST 6: dispatch() wires session_id onto agents
# ===========================================================================

class TestDispatchSessionId:

    def test_dispatch_sets_session_id_on_inbound(self, tmp_path, monkeypatch):
        """dispatch() should set session_id on the inbound agent."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("BREAKOUT_GPT_REASONER", "false")
        monkeypatch.setenv("DEMO_MODE", "true")

        from src.memory.conversation_memory import ConversationMemory
        from main import dispatch, build_inbound_agent

        class FakeArgs:
            debug = False
            personality = True
            language = "en"
            provider = "simulator"
            model = "gpt-4o-mini"
            no_openai = True

        memory = ConversationMemory(tmp_path / "test_memory.json")
        inbound = build_inbound_agent(FakeArgs(), memory)

        result, booking, agent = dispatch(
            "hello",
            inbound,
            None,
            "inbound_agent",
            session_id="dispatch-test-123",
        )

        assert hasattr(inbound, "session_id")
        assert inbound.session_id == "dispatch-test-123"
        assert result.response
