from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.core.booking_provider import (  # noqa: E402
    BreakoutAPIProvider,
    SimulatorProvider,
    build_booking_provider,
)
from src.core.conversation_modes import ConversationMode, ConversationModeDetector  # noqa: E402
from src.integrations.kreeda.breakout_api import BreakoutAPI, BreakoutAPIError  # noqa: E402
from src.response_composer import ResponseComposer  # noqa: E402


PROMPT_PATH = PROJECT_DIR / "prompts" / "breakout_personality_prompt.txt"


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_personality_prompt_preserves_hospitality_and_safety_rules() -> None:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    assert "hospitality" in prompt.lower()
    assert "Recommend first" in prompt
    assert "Ask no more than one question" in prompt
    assert "Do not add prices" in prompt
    assert "Don't worry" in prompt


def test_response_composer_loads_transcript_driven_playbook() -> None:
    composer = ResponseComposer(PROMPT_PATH, enabled=False)
    assert "BREAKOUT CONVERSATION PLAYBOOK" in composer.system_prompt
    assert "Acknowledge, then help" in composer.system_prompt
    assert "Empathy and rescue pattern" in composer.system_prompt


def test_response_composer_returns_approved_draft_when_disabled() -> None:
    composer = ResponseComposer(PROMPT_PATH, enabled=False)
    draft = "Murder Mystery is a good starting point. How many people are joining?"
    assert composer.compose(draft, "What do you recommend?", {}, "escape_room_inquiry", ConversationMode.RECOMMENDATION) == draft


def test_response_composer_uses_system_prompt_and_structured_state(monkeypatch) -> None:
    captured = {}

    class FakeResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_text="Beautiful. Murder Mystery is a lovely place to start. How many people are joining?")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.responses = FakeResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    composer = ResponseComposer(PROMPT_PATH, model="test-model", enabled=True)
    result = composer.compose(
        "Murder Mystery is a good starting point. How many people are joining?",
        "What do you recommend?",
        {"location": "Whitefield", "participants": 6},
        "escape_room_inquiry",
        ConversationMode.RECOMMENDATION,
        "Murder Mystery is an investigation-style room.",
    )

    assert result.startswith("Beautiful")
    assert captured["model"] == "test-model"
    assert "voice of Breakout" in captured["instructions"]
    assert '"location": "Whitefield"' in captured["input"]
    assert captured["client"]["timeout"] == 2.0
    assert captured["client"]["max_retries"] == 0


def test_response_composer_falls_back_when_openai_fails(monkeypatch) -> None:
    class FailingOpenAI:
        def __init__(self, **kwargs):
            raise TimeoutError("timed out")

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FailingOpenAI))
    composer = ResponseComposer(PROMPT_PATH, enabled=True)
    draft = "The approved deterministic answer."
    assert composer.compose(draft, "Hello", {}, "general_faq", ConversationMode.FAQ) == draft
    assert "timed out" in composer.last_error


def test_conversation_modes_cover_demo_paths() -> None:
    detector = ConversationModeDetector()
    assert detector.detect("We are running late", {}, "general_faq") == ConversationMode.RESCUE
    assert detector.detect("What would you recommend?", {}, "escape_room_inquiry") == ConversationMode.RECOMMENDATION
    assert detector.detect("I want to book it", {}, "escape_room_inquiry") == ConversationMode.BOOKING
    assert detector.detect("What happens if we don't escape?", {}, "general_faq") == ConversationMode.FAQ
    assert detector.detect("We are planning a birthday", {}, "birthday_party") == ConversationMode.SALES


def test_explicit_faq_preempts_active_booking_mode() -> None:
    detector = ConversationModeDetector()
    state = {"current_workflow": "booking"}
    assert detector.detect("What happens if we don't escape?", state, "escape_room_inquiry") == ConversationMode.FAQ


def test_breakout_api_uses_documented_endpoints_and_api_key() -> None:
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        if request.full_url.endswith("/locations"):
            return FakeHTTPResponse([{"locationId": "LOC1", "locationName": "Whitefield"}])
        if "/games?" in request.full_url:
            return FakeHTTPResponse([{"gameId": "G1", "gameName": "Murder Mystery"}])
        if "/slots?" in request.full_url:
            return FakeHTTPResponse([{"slotId": "S1", "time": "16:00", "isAvailable": True}])
        if request.full_url.endswith("/release-slots"):
            return FakeHTTPResponse({"status": "released"})
        return FakeHTTPResponse({"bookingId": "bk_demo"})

    client = BreakoutAPI(base_url="https://example.test", api_key="secret", timeout=4)
    with patch("src.integrations.kreeda.breakout_api.urlopen", side_effect=fake_urlopen):
        assert client.get_locations()[0]["locationName"] == "Whitefield"
        assert client.get_games("LOC1")[0]["gameId"] == "G1"
        assert client.get_available_slots("LOC1", ["G1"], "2026-06-18", "2026-06-18")[0]["slotId"] == "S1"
        assert client.prepare_booking({"locationId": "LOC1", "gameId": "G1", "slotId": "S1"})["bookingId"] == "bk_demo"
        assert client.release_slots(["S1"])["status"] == "released"

    assert [request.get_method() for request, _ in calls] == ["GET", "GET", "GET", "POST", "POST"]
    assert all(request.get_header("X-api-key") == "secret" for request, _ in calls)
    assert all(timeout == 4 for _, timeout in calls)
    assert "gameIds=G1" in calls[2][0].full_url
    assert calls[4][0].full_url == "https://example.test/book/v1.0/release-slots"
    assert json.loads(calls[4][0].data.decode("utf-8")) == {"slotIds": ["S1"]}


def test_production_code_never_uses_release_seats_endpoint() -> None:
    deprecated_endpoint = "release" + "-seats"
    production_files = list((PROJECT_DIR / "src").rglob("*.py")) + [PROJECT_DIR / "main.py"]
    offenders = [
        str(path.relative_to(PROJECT_DIR))
        for path in production_files
        if deprecated_endpoint in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_breakout_api_requires_credentials() -> None:
    client = BreakoutAPI(base_url="https://example.test", api_key="")
    try:
        client.get_locations()
    except BreakoutAPIError as exc:
        assert exc.code == "MISSING_API_KEY"
    else:
        raise AssertionError("Expected a missing-key error")


def test_booking_provider_falls_back_to_simulator_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("BOOKING_API_KEY", raising=False)
    monkeypatch.delenv("BOOKING_BASE_URL", raising=False)
    assert isinstance(build_booking_provider(), SimulatorProvider)


def test_booking_provider_uses_real_api_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("BOOKING_API_KEY", "demo-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://example.test")
    assert isinstance(build_booking_provider(), BreakoutAPIProvider)


def test_openai_timeout_reliability(monkeypatch, tmp_path) -> None:
    import httpx
    import openai
    from src.agents.inbound_agent import InboundAgent
    from src.memory.conversation_memory import ConversationMemory
    from src.knowledge.knowledge_loader import KnowledgeLoader

    class TimeoutOpenAI:
        def __init__(self, **kwargs):
            class FakeResponses:
                def create(self, **kwargs):
                    raise openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))
            self.responses = FakeResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    mock_openai_module = SimpleNamespace(
        OpenAI=TimeoutOpenAI,
        APITimeoutError=openai.APITimeoutError,
        APIConnectionError=openai.APIConnectionError,
        AuthenticationError=openai.AuthenticationError,
        RateLimitError=openai.RateLimitError,
    )
    monkeypatch.setitem(sys.modules, "openai", mock_openai_module)

    kb = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    mem = ConversationMemory(tmp_path / "session.json")
    agent = InboundAgent(kb, mem, PROMPT_PATH, use_openai=True)

    result = agent.handle_message("We are seven friends and none of us have done an escape room before.")
    assert result.response
    assert "Murder Mystery" in result.response or "Hostage" in result.response
    assert "openai_failure" in agent.response_composer.last_error or "openai_failure" in agent.last_openai_error

    result2 = agent.handle_message("Koramangala")
    assert result2.response


def test_openai_connection_failure_reliability(monkeypatch, tmp_path) -> None:
    import httpx
    import openai
    from src.agents.inbound_agent import InboundAgent
    from src.memory.conversation_memory import ConversationMemory
    from src.knowledge.knowledge_loader import KnowledgeLoader

    class ConnectionFailOpenAI:
        def __init__(self, **kwargs):
            class FakeResponses:
                def create(self, **kwargs):
                    raise openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))
            self.responses = FakeResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    mock_openai_module = SimpleNamespace(
        OpenAI=ConnectionFailOpenAI,
        APITimeoutError=openai.APITimeoutError,
        APIConnectionError=openai.APIConnectionError,
        AuthenticationError=openai.AuthenticationError,
        RateLimitError=openai.RateLimitError,
    )
    monkeypatch.setitem(sys.modules, "openai", mock_openai_module)

    kb = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    mem = ConversationMemory(tmp_path / "session.json")
    agent = InboundAgent(kb, mem, PROMPT_PATH, use_openai=True)

    result = agent.handle_message("We are seven friends and none of us have done an escape room before.")
    assert result.response
    assert "Murder Mystery" in result.response or "Hostage" in result.response
    assert "openai_failure" in agent.response_composer.last_error or "openai_failure" in agent.last_openai_error

    result2 = agent.handle_message("JP Nagar")
    assert result2.response


def test_openai_auth_failure_reliability(monkeypatch, tmp_path) -> None:
    import httpx
    import openai
    from src.agents.inbound_agent import InboundAgent
    from src.memory.conversation_memory import ConversationMemory
    from src.knowledge.knowledge_loader import KnowledgeLoader

    class AuthFailOpenAI:
        def __init__(self, **kwargs):
            class FakeResponses:
                def create(self, **kwargs):
                    req = httpx.Request("POST", "https://api.openai.com")
                    res = httpx.Response(401, request=req)
                    raise openai.AuthenticationError(message="invalid api key", response=res, body=None)
            self.responses = FakeResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    mock_openai_module = SimpleNamespace(
        OpenAI=AuthFailOpenAI,
        APITimeoutError=openai.APITimeoutError,
        APIConnectionError=openai.APIConnectionError,
        AuthenticationError=openai.AuthenticationError,
        RateLimitError=openai.RateLimitError,
    )
    monkeypatch.setitem(sys.modules, "openai", mock_openai_module)

    kb = KnowledgeLoader(PROJECT_DIR / "knowledge").load()
    mem = ConversationMemory(tmp_path / "session.json")
    agent = InboundAgent(kb, mem, PROMPT_PATH, use_openai=True)

    result = agent.handle_message("We are seven friends and none of us have done an escape room before.")
    assert result.response
    assert "Murder Mystery" in result.response or "Hostage" in result.response
    assert "openai_failure" in agent.response_composer.last_error or "openai_failure" in agent.last_openai_error

    result2 = agent.handle_message("Whitefield")
    assert result2.response
