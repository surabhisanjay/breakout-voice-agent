from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import app as api_app  # noqa: E402
from main import build_inbound_agent, dispatch  # noqa: E402
from src.agents.booking_agent import BookingAgent  # noqa: E402
from src.integrations.whatsapp import WhatsAppClient  # noqa: E402
from src.memory.conversation_memory import ConversationMemory  # noqa: E402


def test_dispatch_adds_sentiment_scoring_and_learning_metrics(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("BREAKOUT_GPT_REASONER", "false")
    monkeypatch.setenv("DEMO_MODE", "true")
    memory = ConversationMemory(tmp_path / "session.json")
    inbound = build_inbound_agent(Namespace(model=None, no_openai=True), memory)

    result, _, _ = dispatch("We are six adults visiting Whitefield.", inbound, None, "inbound_agent")

    assert result.sentiment_analysis["sentiment"] == "neutral"
    assert 0 <= result.scoring["lead_score"] <= 100
    assert result.scoring["booking_readiness"] > 0
    assert result.learning_metrics["turns"] == 1
    assert result.metrics_report["summary"]["turns"] == 1
    assert result.metrics_report["funnel"]["location"] == "Whitefield"


def test_metrics_endpoint_returns_session_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("BREAKOUT_GPT_REASONER", "false")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setattr(api_app, "API_MEMORY_DIR", tmp_path / "api_sessions")
    api_app._sessions.clear()
    client = TestClient(api_app.app)

    chat = client.post(
        "/chat",
        json={"session_id": "metrics-demo", "message": "We are six adults visiting Whitefield."},
    )
    report = client.get("/metrics/metrics-demo")

    assert chat.status_code == 200
    assert "scoring" in chat.json()
    assert report.status_code == 200
    assert report.json()["report"]["summary"]["turns"] >= 1
    assert report.json()["report"]["funnel"]["location"] == "Whitefield"


def test_whatsapp_client_sends_booking_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "phone-id")
    posted: dict = {}

    class FakeResponse:
        status_code = 200
        content = b"{}"

        @staticmethod
        def json():
            return {"messages": [{"id": "wamid.test"}]}

    def fake_post(url, headers, json, timeout):
        posted.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("src.integrations.whatsapp.requests.post", fake_post)

    result = WhatsAppClient().send_booking_confirmation(
        "9876543210",
        {
            "booking_reference": "BK-123",
            "location": "Whitefield",
            "date": "25 June",
            "slot": "7:00 PM",
            "participants": 4,
        },
    )

    assert result.sent is True
    assert result.message_id == "wamid.test"
    assert posted["json"]["to"] == "919876543210"
    assert "BK-123" in posted["json"]["text"]["body"]


def test_whatsapp_client_sends_wati_session_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_PROVIDER", "wati")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "Bearer token123")
    monkeypatch.setenv("WHATSAPP_API_ENDPOINT", "https://live-mt-server.wati.io/1070847")
    posted: dict = {}

    class FakeResponse:
        status_code = 200
        content = b"{}"

        @staticmethod
        def json():
            return {"messageId": "wati.session.test"}

    def fake_post(url, headers, data, timeout):
        posted.update({"url": url, "headers": headers, "data": data, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("src.integrations.whatsapp.requests.post", fake_post)

    result = WhatsAppClient().send_booking_confirmation(
        "9876543210",
        {
            "booking_reference": "BK-123",
            "location": "Whitefield",
            "date": "25 June",
            "slot": "7:00 PM",
            "participants": 4,
        },
    )

    assert result.sent is True
    assert result.message_id == "wati.session.test"
    assert posted["url"] == "https://live-mt-server.wati.io/1070847/api/v1/sendSessionMessage/919876543210"
    assert posted["headers"]["Authorization"] == "Bearer token123"
    assert "BK-123" in posted["data"]["messageText"]


def test_whatsapp_client_sends_wati_template_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_PROVIDER", "wati")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "token123")
    monkeypatch.setenv("WHATSAPP_API_ENDPOINT", "https://live-mt-server.wati.io/1070847")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_NAME", "booking_conf_temp")
    posted: dict = {}

    class FakeResponse:
        status_code = 200
        content = b"{}"

        @staticmethod
        def json():
            return {
                "result": True,
                "receivers": [{"localMessageId": "wati.template.test", "isValidWhatsAppNumber": True}]
            }

    def fake_post(url, headers, json, timeout):
        posted.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("src.integrations.whatsapp.requests.post", fake_post)

    result = WhatsAppClient().send_booking_confirmation(
        "9876543210",
        {
            "booking_reference": "BK-123",
            "location": "Whitefield",
            "date": "25 June",
            "slot": "7:00 PM",
            "participants": 4,
        },
    )

    assert result.sent is True
    assert result.message_id == "wati.template.test"
    assert posted["url"] == "https://live-mt-server.wati.io/1070847/api/v1/sendTemplateMessage?whatsappNumber=919876543210"
    assert posted["headers"]["Authorization"] == "Bearer token123"
    assert posted["json"]["template_name"] == "booking_conf_temp"
    assert posted["json"]["parameters"][0]["value"] == "BK-123"


def test_whatsapp_client_rejects_invalid_wati_receiver(monkeypatch) -> None:
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_PROVIDER", "wati")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "token123")
    monkeypatch.setenv("WHATSAPP_API_ENDPOINT", "https://wati.example")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_NAME", "booking_conf_temp")

    class FakeResponse:
        status_code = 200
        content = b"{}"

        @staticmethod
        def json():
            return {
                "result": True,
                "receivers": [
                    {
                        "localMessageId": "wati.invalid",
                        "isValidWhatsAppNumber": False,
                        "errors": [],
                    }
                ],
            }

    monkeypatch.setattr(
        "src.integrations.whatsapp.requests.post",
        lambda *args, **kwargs: FakeResponse(),
    )

    result = WhatsAppClient().send_booking_confirmation("9876543210", {})

    assert result.sent is False
    assert result.message_id == "wati.invalid"
    assert result.error == "invalid_whatsapp_number"


def test_booking_confirmation_records_whatsapp_status(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    memory = ConversationMemory(tmp_path / "booking.json")
    memory.data.update({
        "intent": "escape_room_inquiry",
        "event_type": "Escape Room",
        "location": "Whitefield",
        "preferred_date": "25 June",
        "participants": 4,
        "age_group": "adults",
        "room": "Murder Mystery",
        "customer_name": "Siddharth Rao",
        "first_name": "Siddharth",
        "last_name": "Rao",
        "phone": "9876543210",
        "selected_slot": "7:00 PM",
    })
    memory.save()
    agent = BookingAgent(memory)
    agent._available_slots = ["7:00 PM"]
    agent._last_availability = {"available": True, "verified": True, "slots": ["7:00 PM"]}
    agent.booking_tool.create = MagicMock(return_value={
        "booking_id": "booking-123",
        "booking_reference": "reference-123",
        "confirmed": True,
        "location": "Whitefield",
        "date": "25 June",
        "slot": "7:00 PM",
        "participants": 4,
    })

    result = agent._prepare_selected_booking("7:00 PM")[1]

    assert result["whatsapp_confirmation"]["sent"] is False
    assert result["whatsapp_confirmation"]["error"] == "whatsapp_disabled"
    assert memory.data["whatsapp_confirmation"]["enabled"] is False
