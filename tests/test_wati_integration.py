from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import requests


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.services.wati_client import WatiClient, WatiConfig, WatiSendResult  # noqa: E402
from src.services.whatsapp_payload import build_wati_booking_payload  # noqa: E402
from src.agents.booking_agent import BookingAgent  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402


def _payload() -> dict:
    return build_wati_booking_payload(
        {
            "customer_name": "Priya Mehta",
            "phone": "9845012367",
            "room": "Murder Mystery",
            "location": "Whitefield",
            "preferred_date": "Tomorrow",
            "selected_slot": "6:15 PM",
        },
        {
            "booking_reference": "or_123",
            "payment_url": "https://pay.example/link",
            "status": "PAYMENT_PENDING",
        },
    )


def test_whatsapp_payload_includes_payment_expiry_and_stays_compatible() -> None:
    payload = _payload()

    assert payload["provider"] == "wati"
    assert payload["send"] is False
    assert payload["payment_expiry"] == "15 minutes"
    assert payload["payment_expiry_minutes"] == 15
    assert payload["payment_link"] == "https://pay.example/link"


def test_whatsapp_payload_prefers_booking_id_over_order_reference() -> None:
    payload = build_wati_booking_payload(
        {"phone": "9845012367"},
        {"booking_id": "bk_123", "booking_reference": "or_123"},
    )

    assert payload["booking_id"] == "bk_123"


def test_wati_client_skips_when_env_not_configured(monkeypatch) -> None:
    for key in (
        "WATI_ACCESS_TOKEN",
        "WATI_API_KEY",
        "WATI_BASE_URL",
        "WATI_API_VERSION",
        "WHATSAPP_ACCESS_TOKEN",
        "WHATSAPP_API_ENDPOINT",
        "WHATSAPP_API_VERSION",
    ):
        monkeypatch.delenv(key, raising=False)

    assert WatiConfig.from_env() is None

    result = WatiClient().send_booking_payment_link(_payload())

    assert result.attempted is False
    assert result.sent is False
    assert result.error == "missing_configuration"


def test_wati_config_accepts_whatsapp_aliases_and_strips_bearer(monkeypatch) -> None:
    monkeypatch.delenv("WATI_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("WATI_BASE_URL", raising=False)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "Bearer alias-token")
    monkeypatch.setenv("WHATSAPP_API_ENDPOINT", "https://wati.example/tenant/")
    monkeypatch.setenv("WHATSAPP_API_VERSION", "Version 1")

    config = WatiConfig.from_env()

    assert config is not None
    assert config.access_token == "alias-token"
    assert config.base_url == "https://wati.example/tenant"
    assert config.api_version == "v1"


def test_wati_client_sends_session_message_with_env_credentials(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeResponse:
        status_code = 200
        text = '{"result":true,"messageId":"wamid.123"}'

    def fake_post(url, headers, timeout):
        calls.append(
            {
                "url": url,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return FakeResponse()

    monkeypatch.setattr("src.services.wati_client.requests.post", fake_post)
    client = WatiClient(
        WatiConfig(
            access_token="test-token",
            base_url="https://wati.example",
            api_version="v1",
            timeout_seconds=3,
            max_attempts=1,
        )
    )

    result = client.send_booking_payment_link(_payload())

    assert result.attempted is True
    assert result.sent is True
    assert result.provider == "WATI"
    assert result.message_id == "wamid.123"
    assert calls[0]["url"].startswith("https://wati.example/api/v1/sendSessionMessage/919845012367?messageText=")
    assert calls[0]["headers"]["Authorization"] == "Bearer test-token"
    assert calls[0]["timeout"] == 3
    assert "Booking+ID%3A+or_123" in calls[0]["url"]
    assert "Payment+Link%3A+https%3A%2F%2Fpay.example%2Flink" in calls[0]["url"]
    assert "Please+complete+payment+within+15+minutes." in calls[0]["url"]


def test_wati_client_retries_transient_errors(monkeypatch) -> None:
    attempts = {"count": 0}

    class FakeResponse:
        status_code = 200
        text = '{"ok":true}'

    def flaky_post(_url, headers=None, timeout=None):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise requests.ConnectionError("temporary")
        return FakeResponse()

    monkeypatch.setattr("src.services.wati_client.requests.post", flaky_post)
    monkeypatch.setattr("src.services.wati_client.time.sleep", lambda _seconds: None)

    result = WatiClient(
        WatiConfig(
            access_token="test-token",
            base_url="https://wati.example",
            timeout_seconds=10,
            max_attempts=3,
        )
    ).send_booking_payment_link(_payload())

    assert attempts["count"] == 3
    assert result.sent is True


def test_wati_http_200_with_rejected_body_is_not_marked_sent(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        text = '{"result":false,"message":"Ticket has been expired."}'

    monkeypatch.setattr(
        "src.services.wati_client.requests.post",
        lambda _url, headers, timeout: FakeResponse(),
    )

    result = WatiClient(
        WatiConfig(access_token="test-token", base_url="https://wati.example")
    ).send_booking_payment_link(_payload())

    assert result.attempted is True
    assert result.sent is False
    assert result.status_code == 200
    assert result.reason == "wati_rejected: Ticket has been expired."


def test_wati_uses_approved_template_for_closed_sessions(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeResponse:
        status_code = 200
        text = (
            '{"result":true,"receivers":[{"localMessageId":"local-123",'
            '"isValidWhatsAppNumber":true,"errors":[]}]}'
        )

    def fake_post(url, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("src.services.wati_client.requests.post", fake_post)
    client = WatiClient(
        WatiConfig(
            access_token="test-token",
            base_url="https://wati.example",
            timeout_seconds=10,
            max_attempts=3,
            template_id="order_pending",
        )
    )

    result = client.send_booking_payment_link(_payload())

    assert result.sent is True
    assert result.message_id == "local-123"
    assert len(calls) == 1
    assert calls[0]["url"] == (
        "https://wati.example/api/v2/sendTemplateMessage?whatsappNumber=919845012367"
    )
    assert calls[0]["headers"]["Authorization"] == "Bearer test-token"
    assert calls[0]["timeout"] == 10
    body = calls[0]["json"]
    assert body["template_name"] == "order_pending"
    parameters = {item["name"]: item["value"] for item in body["parameters"]}
    assert parameters["name"] == "Priya Mehta"
    assert parameters["shop_name"] == "Breakout Whitefield"
    assert "Booking ID: or_123" in parameters["product_details"]
    assert "Room: Murder Mystery" in parameters["product_details"]
    assert "Date: Tomorrow" in parameters["product_details"]
    assert "Time: 6:15 PM" in parameters["product_details"]
    assert "within 15 minutes" in parameters["product_details"]
    assert parameters["order_status_url"] == "https://pay.example/link"


def test_wati_send_invoked_after_booking(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict] = []

    class FakeResponse:
        status_code = 200
        text = '{"result":true,"messageId":"wamid.booking"}'

    def fake_post(url, headers=None, timeout=None):
        calls.append({"url": url})
        return FakeResponse()

    monkeypatch.setenv("WATI_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("WATI_BASE_URL", "https://wati.example")
    monkeypatch.setenv("API_VERSION", "v1")
    monkeypatch.setenv("WATI_MAX_ATTEMPTS", "1")
    monkeypatch.setattr("src.services.wati_client.requests.post", fake_post)

    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "participants": 4,
            "age_group": "adults",
            "location": "Whitefield",
            "preferred_date": "Tomorrow",
            "room": "Murder Mystery",
            "selected_slot": "6:15 PM",
            "customer_name": "Priya Mehta",
            "phone": "9845012367",
        }
    )
    memory.save()
    agent = BookingAgent(memory)
    agent._available_slots = ["6:15 PM"]
    agent._last_availability = {"available": True, "verified": True, "slots": ["6:15 PM"]}
    agent.booking_tool.create = MagicMock(
        return_value={
            "booking_id": "bk_123",
            "booking_reference": "or_123",
            "order_id": "or_123",
            "payment_url": "https://pay.example/link",
            "status": "PAYMENT_PENDING",
            "confirmed": True,
            "location": "Whitefield",
            "date": "Tomorrow",
            "slot": "6:15 PM",
            "participants": 4,
            "customer_name": "Priya Mehta",
            "phone": "9845012367",
        }
    )

    response, booking_result = agent._prepare_selected_booking("6:15 PM")

    assert booking_result["booking_id"] == "bk_123"
    assert "Sending the payment link" in response
    assert memory.data["whatsapp_payload"]["send"] is True
    assert memory.data["whatsapp_payload"]["wati_send"]["attempted"] is True
    assert len(calls) == 1
    assert calls[0]["url"].startswith("https://wati.example/api/v1/sendSessionMessage/919845012367?messageText=")
    assert "Payment+Link%3A+https%3A%2F%2Fpay.example%2Flink" in calls[0]["url"]
    assert "Please+complete+payment+within+15+minutes." in calls[0]["url"]


def test_booking_response_contains_whatsapp_delivery_status(monkeypatch, tmp_path: Path) -> None:
    memory = _booking_memory(tmp_path)
    agent = _booking_agent_with_result(memory)
    monkeypatch.setattr(
        "src.agents.booking_agent.WatiClient.send_booking_payment_link",
        lambda _client, _payload: WatiSendResult(
            attempted=True,
            sent=True,
            status_code=200,
            response={"result": True, "messageId": "wamid.response"},
            message_id="wamid.response",
        ),
    )

    _response, booking_result = agent._prepare_selected_booking("6:15 PM")

    delivery = booking_result["whatsapp"]
    assert delivery["attempted"] is True
    assert delivery["sent"] is True
    assert delivery["provider"] == "WATI"
    assert delivery["status_code"] == 200
    assert delivery["response"]["result"] is True
    assert delivery["message_id"] == "wamid.response"
    assert delivery["timestamp"]


def test_booking_succeeds_even_if_wati_temporarily_fails(monkeypatch, tmp_path: Path) -> None:
    memory = _booking_memory(tmp_path)
    agent = _booking_agent_with_result(memory)
    monkeypatch.setattr(
        "src.agents.booking_agent.WatiClient.send_booking_payment_link",
        lambda _client, _payload: WatiSendResult(
            attempted=True,
            sent=False,
            status_code=503,
            reason="http_503: temporary upstream failure",
            response={"error": "temporary upstream failure"},
        ),
    )

    response, booking_result = agent._prepare_selected_booking("6:15 PM")

    assert booking_result["booking_id"] == "bk_123"
    assert booking_result["confirmed"] is True
    assert booking_result["status"] == "PAYMENT_PENDING"
    assert "reserved your slot" in response.lower()
    assert booking_result["whatsapp"] == memory.data["whatsapp_delivery"]
    assert booking_result["whatsapp"]["attempted"] is True
    assert booking_result["whatsapp"]["sent"] is False
    assert booking_result["whatsapp"]["status_code"] == 503
    assert booking_result["whatsapp"]["reason"]


def _booking_memory(tmp_path: Path) -> ConversationMemory:
    memory = ConversationMemory(tmp_path / "session.json")
    memory.data.update(
        {
            "intent": "escape_room_inquiry",
            "participants": 4,
            "age_group": "adults",
            "location": "Whitefield",
            "preferred_date": "Tomorrow",
            "room": "Murder Mystery",
            "selected_slot": "6:15 PM",
            "customer_name": "Priya Mehta",
            "phone": "9845012367",
        }
    )
    memory.save()
    return memory


def _booking_agent_with_result(memory: ConversationMemory) -> BookingAgent:
    agent = BookingAgent(memory)
    agent._available_slots = ["6:15 PM"]
    agent._last_availability = {"available": True, "verified": True, "slots": ["6:15 PM"]}
    agent.booking_tool.create = MagicMock(
        return_value={
            "booking_id": "bk_123",
            "booking_reference": "or_123",
            "order_id": "or_123",
            "payment_url": "https://pay.example/link",
            "status": "PAYMENT_PENDING",
            "confirmed": True,
            "location": "Whitefield",
            "date": "Tomorrow",
            "slot": "6:15 PM",
            "participants": 4,
            "customer_name": "Priya Mehta",
            "phone": "9845012367",
        }
    )
    return agent
