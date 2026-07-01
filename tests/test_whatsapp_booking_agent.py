from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import app as api_app
from src.channels.whatsapp.models import (
    WhatsAppAgentResult,
    WhatsAppInboundMessage,
    WhatsAppIntent,
    WatiDeliveryResult,
)
from src.channels.whatsapp.service import (
    WhatsAppAgentService,
    WhatsAppEventStore,
    WhatsAppResponseFormatter,
    parse_wati_webhook,
)
from src.channels.whatsapp.wati_adapter import WatiChannelClient, WatiChannelConfig
from src.core.agent_response import AgentResponse


def _agent_response(response: str = "Got it. Which location works best?") -> AgentResponse:
    return AgentResponse(
        response=response,
        intent="escape_room_inquiry",
        next_agent="qualification_agent",
        should_handoff=False,
        state={"participants": 4},
    )


def test_parse_wati_webhook_classic_payload() -> None:
    inbound = parse_wati_webhook(
        {
            "eventType": "message",
            "id": "wamid-1",
            "waId": "919876543210",
            "senderName": "Priya",
            "text": "I want to book",
        }
    )

    assert inbound == WhatsAppInboundMessage(
        phone="919876543210",
        text="I want to book",
        message_id="wamid-1",
        contact_name="Priya",
        event_type="message",
    )


def test_parse_wati_webhook_nested_payload_and_ignore_outbound() -> None:
    inbound = parse_wati_webhook(
        {
            "type": "message",
            "data": {
                "message": {
                    "id": "wamid-2",
                    "from": "919876543210",
                    "text": {"body": "What are your locations?"},
                }
            },
        }
    )

    assert inbound is not None
    assert inbound.text == "What are your locations?"
    assert parse_wati_webhook({"eventType": "message", "owner": True, "waId": "91", "text": "sent"}) is None
    assert parse_wati_webhook({"eventType": "delivery_status", "waId": "91", "text": "read"}) is None


@pytest.mark.parametrize(
    ("message", "intent"),
    [
        ("I want to book", WhatsAppIntent.NEW_BOOKING),
        ("How much does it cost?", WhatsAppIntent.PRICE_ENQUIRY),
        ("This is for a birthday", WhatsAppIntent.BIRTHDAY_CELEBRATION),
        ("We need a corporate team outing", WhatsAppIntent.CORPORATE_OUTING),
        ("What room themes do you have?", WhatsAppIntent.ROOM_ENQUIRY),
        ("Where is the Whitefield branch?", WhatsAppIntent.LOCATION_ENQUIRY),
        ("I need to reschedule my booking", WhatsAppIntent.EXISTING_BOOKING),
        ("We are running late", WhatsAppIntent.LATE_ARRIVAL),
        ("I paid but need confirmation", WhatsAppIntent.PAYMENT_CONFIRMATION),
        ("What is an escape room?", WhatsAppIntent.GENERAL_FAQ),
    ],
)
def test_whatsapp_intent_detection(message: str, intent: WhatsAppIntent) -> None:
    assert WhatsAppAgentService.detect_intent(message) == intent


def test_formatter_uses_calm_opener_and_at_most_two_questions() -> None:
    formatted = WhatsAppResponseFormatter().format(
        "Awesome! Which date works? Which location works? Do you want food?",
        {},
    )

    assert formatted.startswith("Got it.")
    assert formatted.count("?") == 2
    assert "Awesome" not in formatted


def test_formatter_builds_booking_readiness_summary() -> None:
    formatted = WhatsAppResponseFormatter().format(
        "Let me check.",
        {
            "location": "Whitefield",
            "preferred_date": "12 July",
            "preferred_period": "evening",
            "participants": 6,
            "recommended_option": "Murder Mystery",
            "customer_name": "Priya",
            "experience_level": "beginner",
        },
    )

    assert formatted.startswith("Got it. Here's what I have:")
    assert "Location: Whitefield" in formatted
    assert "Time: evening" in formatted
    assert formatted.endswith("Should I go ahead and check availability?")


def test_service_preserves_session_and_deduplicates_message(tmp_path: Path) -> None:
    dispatcher = MagicMock(return_value=_agent_response())
    sender = MagicMock()
    sender.send_text.return_value = WatiDeliveryResult(True, True, 200, "wati-1")
    service = WhatsAppAgentService(
        dispatcher=dispatcher,
        sender=sender,
        event_store=WhatsAppEventStore(tmp_path / "events"),
    )
    inbound = WhatsAppInboundMessage("9876543210", "I want to book", "wamid-1")

    first = service.handle(inbound)
    duplicate = service.handle(inbound)

    assert first.session_id == WhatsAppAgentService.session_id("919876543210")
    assert first.intent == WhatsAppIntent.NEW_BOOKING.value
    assert first.delivery.sent is True
    assert duplicate.duplicate is True
    dispatcher.assert_called_once()
    sender.send_text.assert_called_once_with("9876543210", first.response)


def test_wati_transport_uses_existing_env_aliases_and_real_recipient(monkeypatch) -> None:
    monkeypatch.delenv("WATI_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("WATI_BASE_URL", raising=False)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "Bearer configured-token")
    monkeypatch.setenv("WHATSAPP_API_ENDPOINT", "https://wati.example/tenant")
    posted: dict = {}

    class FakeResponse:
        status_code = 200
        text = '{"result":true,"messageId":"wati-123"}'

        @staticmethod
        def json():
            return {"result": True, "messageId": "wati-123"}

    def fake_post(url, params, headers, timeout):
        posted.update(url=url, params=params, headers=headers, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr("src.channels.whatsapp.wati_adapter.requests.post", fake_post)

    result = WatiChannelClient().send_text("9876543210", "Got it. Which date works?")

    assert result.sent is True
    assert result.message_id == "wati-123"
    assert posted["url"].endswith("/api/v1/sendSessionMessage/919876543210")
    assert posted["params"] == {"messageText": "Got it. Which date works?"}
    assert posted["headers"]["Authorization"] == "Bearer configured-token"


def test_wati_transport_does_not_treat_http_200_rejection_as_sent(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        text = '{"result":false,"info":"Contact not found"}'

        @staticmethod
        def json():
            return {"result": False, "info": "Contact not found"}

    monkeypatch.setattr(
        "src.channels.whatsapp.wati_adapter.requests.post",
        lambda *args, **kwargs: FakeResponse(),
    )
    client = WatiChannelClient(WatiChannelConfig("token", "https://wati.example"))

    result = client.send_text("9876543210", "Hello")

    assert result.attempted is True
    assert result.sent is False
    assert result.reason == "Contact not found"


def test_fastapi_wati_webhook_dispatches_and_returns_delivery(monkeypatch) -> None:
    fake_service = MagicMock()
    fake_service.handle.return_value = WhatsAppAgentResult(
        session_id="wa-session",
        intent=WhatsAppIntent.LOCATION_ENQUIRY.value,
        agent_intent="general_faq",
        response="Breakout has locations in Koramangala, Whitefield, and JP Nagar.",
        delivery=WatiDeliveryResult(True, True, 200, "wati-123"),
    )
    monkeypatch.setattr(api_app, "_whatsapp_service_instance", fake_service)
    monkeypatch.delenv("WHATSAPP_WEBHOOK_TOKEN", raising=False)

    response = TestClient(api_app.app).post(
        "/wati/webhook",
        json={
            "eventType": "message",
            "id": "wamid-api",
            "waId": "919876543210",
            "text": "What locations do you have?",
        },
    )

    assert response.status_code == 200
    assert response.json()["delivery"]["sent"] is True
    fake_service.handle.assert_called_once()


def test_whatsapp_service_uses_real_breakout_dispatcher(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NO_OPENAI", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(api_app, "API_MEMORY_DIR", tmp_path / "api_sessions")
    api_app._sessions.clear()
    sender = MagicMock()
    sender.send_text.return_value = WatiDeliveryResult(True, True, 200, "wati-real")
    service = WhatsAppAgentService(
        dispatcher=api_app._dispatch_chat_message,
        sender=sender,
        event_store=WhatsAppEventStore(tmp_path / "events"),
    )

    first = service.handle(WhatsAppInboundMessage("9876543210", "I want to book", "real-1"))
    second = service.handle(WhatsAppInboundMessage("9876543210", "Just an escape room", "real-2"))
    third = service.handle(WhatsAppInboundMessage("9876543210", "What locations do you have?", "real-3"))

    assert first.session_id == second.session_id == third.session_id
    assert "escape room" in first.response.lower()
    assert "how many" in second.response.lower()
    assert "Koramangala" in third.response
    assert "Whitefield" in third.response
    assert "JP Nagar" in third.response
    assert "Mumbai" not in third.response


def test_direct_message_endpoint_requires_internal_api_key(monkeypatch) -> None:
    monkeypatch.delenv("WHATSAPP_INTERNAL_API_KEY", raising=False)
    response = TestClient(api_app.app).post(
        "/whatsapp/messages",
        json={"phone": "9876543210", "message": "Hello"},
    )

    assert response.status_code == 503
