from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.config.env_loader import booking_provider_label, should_use_live_booking  # noqa: E402
from src.integrations.kreeda.agent_contract_provider import AgentContractProvider  # noqa: E402
from src.integrations.kreeda.breakout_booking_provider import BreakoutBookingProvider  # noqa: E402
from src.orchestration.booking_orchestrator import BookingOrchestrator  # noqa: E402
from src.integrations.kreeda.breakout_api import BreakoutAPI  # noqa: E402


def test_booking_credentials_activate_live_even_in_demo_mode(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.delenv("BOOKING_PROVIDER", raising=False)

    assert booking_provider_label() == "live-configured"
    assert should_use_live_booking() is True


def test_booking_provider_simulator_override_wins(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.setenv("BOOKING_PROVIDER", "simulator")

    assert booking_provider_label() == "simulator"
    assert should_use_live_booking() is False


def test_orchestrator_uses_live_provider_when_credentials_exist_in_demo(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.delenv("BOOKING_PROVIDER", raising=False)

    booking_provider = MagicMock(spec=BreakoutBookingProvider)
    contract_provider = MagicMock(spec=AgentContractProvider)

    orchestrator = BookingOrchestrator(
        booking_provider=booking_provider,
        contract_provider=contract_provider,
    )

    assert orchestrator.is_live is True
    assert orchestrator.booking_provider is booking_provider


def test_orchestrator_logs_simulator_override_reason(monkeypatch, caplog) -> None:
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.setenv("BOOKING_PROVIDER", "simulator")

    orchestrator = BookingOrchestrator()

    assert orchestrator.is_live is False
    assert "BOOKING_PROVIDER=simulator" in orchestrator.fallback_reason


def test_breakout_api_unwraps_common_response_shapes() -> None:
    assert BreakoutAPI._as_list([{"id": "1"}]) == [{"id": "1"}]
    assert BreakoutAPI._as_list({"data": [{"id": "1"}]}) == [{"id": "1"}]
    assert BreakoutAPI._as_list({"response": {"slots": [{"id": "slot-1"}]}}) == [{"id": "slot-1"}]
    assert BreakoutAPI._as_list({"seats": [{"eventId": "event-1"}]}) == [{"eventId": "event-1"}]


def test_breakout_api_uses_discovered_tool_routes(monkeypatch) -> None:
    client = BreakoutAPI(base_url="https://test.api", api_key="test-key")
    calls: list[tuple[str, str, dict]] = []

    def fake_request(method: str, path: str, query=None, payload=None):
        calls.append((method, path, payload or {}))
        if path.endswith("get_venues"):
            return {"venues": [{"venueId": "venue-1"}]}
        if path.endswith("create_instant_cart"):
            return {"cartId": "cart-1"}
        return {"bookingId": "bk-1", "status": "CONFIRMED"}

    monkeypatch.setattr(client, "_request", fake_request)
    assert client.get_booking_venues() == [{"venueId": "venue-1"}]
    assert client.create_instant_cart({"venueId": "venue-1", "slots": [{}]})["cartId"] == "cart-1"
    assert client.create_confirmed_booking({"venueId": "venue-1", "cartId": "cart-1"})["bookingId"] == "bk-1"
    assert calls == [
        ("POST", "/v1.0/tools/get_venues", {}),
        ("POST", "/v1.0/tools/create_instant_cart", {"venueId": "venue-1", "slots": [{}]}),
        ("POST", "/v1.0/tools/create_booking", {"venueId": "venue-1", "cartId": "cart-1"}),
    ]


def test_breakout_api_rejects_non_iso_dates_before_request(monkeypatch) -> None:
    client = BreakoutAPI(base_url="https://test.api", api_key="test-key")
    request = MagicMock()
    monkeypatch.setattr(client, "_request", request)

    try:
        client.search_booking_slots("venue-1", "game-1", "Tomorrow", "Tomorrow")
    except Exception as exc:
        assert getattr(exc, "code", "") == "INVALID_DATE_FORMAT"
    else:
        raise AssertionError("Expected non-ISO dates to be rejected")
    request.assert_not_called()


def test_relative_date_normalization_is_deterministic() -> None:
    reference = date(2026, 6, 19)
    assert BookingOrchestrator._normalise_date("Tomorrow", reference) == "2026-06-20"
    assert BookingOrchestrator._normalise_date("Today", reference) == "2026-06-19"
    assert BookingOrchestrator._normalise_date("next Monday", reference) == "2026-06-29"


def test_live_mapping_whitefield_bomb_defusal_july_12(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.delenv("BOOKING_PROVIDER", raising=False)

    booking_provider = MagicMock(spec=BreakoutBookingProvider)
    booking_provider.get_booking_venues.return_value = [
        {"venueId": "loc-whitefield", "name": "Whitefield"},
        {"venueId": "loc-jp", "name": "JP Nagar"},
    ]
    booking_provider.get_booking_games.return_value = [
        {"id": "game-bomb", "name": "Bomb Diffusal", "privateEnabled": True},
        {"id": "game-undercover", "name": "Undercover"},
    ]
    booking_provider.search_booking_slots.return_value = [
        {"eventId": "slot-3pm", "time": "15:00:00", "available": 8, "isAvailable": True},
        {"eventId": "slot-5pm", "slotTime": "17:00", "available": 8, "isAvailable": True},
    ]

    orchestrator = BookingOrchestrator(
        booking_provider=booking_provider,
        contract_provider=MagicMock(spec=AgentContractProvider),
    )
    availability = orchestrator.check_availability("Whitefield", "12 July", 6, "Bomb Defusal")

    assert availability["available"] is True
    assert availability["slots"] == ["3:00 PM", "5:00 PM"]
    booking_provider.get_booking_games.assert_called_once_with("loc-whitefield")
    booking_provider.search_booking_slots.assert_called_once_with(
        "loc-whitefield",
        "game-bomb",
        "2026-07-12",
        "2026-07-12",
    )


def test_live_mapping_jp_nagar_bomb_defusal_returns_empty_without_slot_call(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.delenv("BOOKING_PROVIDER", raising=False)

    booking_provider = MagicMock(spec=BreakoutBookingProvider)
    booking_provider.get_booking_venues.return_value = [
        {"venueId": "loc-whitefield", "locationName": "Whitefield"},
        {"venueId": "loc-jp", "locationName": "JP Nagar"},
    ]
    booking_provider.get_booking_games.return_value = [
        {"gameId": "game-prison", "gameName": "Prison Break"},
        {"gameId": "game-murder", "gameName": "Murder Mystery"},
    ]

    orchestrator = BookingOrchestrator(
        booking_provider=booking_provider,
        contract_provider=MagicMock(spec=AgentContractProvider),
    )
    availability = orchestrator.check_availability("JP Nagar", "12 July", 6, "Bomb Defusal")

    assert availability["available"] is False
    assert availability["slots"] == []
    booking_provider.get_booking_games.assert_called_once_with("loc-jp")
    booking_provider.search_booking_slots.assert_not_called()


def test_live_prepare_booking_uses_alias_ids(monkeypatch, caplog) -> None:
    caplog.set_level("INFO")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.delenv("BOOKING_PROVIDER", raising=False)

    booking_provider = MagicMock(spec=BreakoutBookingProvider)
    booking_provider.get_booking_venues.return_value = [{"venueId": "loc-whitefield", "name": "Whitefield"}]
    booking_provider.get_booking_games.return_value = [{
        "id": "game-bomb",
        "name": "Bomb Defusal",
        "peopleCategories": [{"categoryId": "adult", "categoryName": "Adults", "max": 8}],
    }]
    booking_provider.search_booking_slots.return_value = [{
        "eventId": "slot-3pm", "gameId": "game-bomb", "date": "2026-07-12",
        "time": "15:00:00", "available": 8, "isAvailable": True,
    }]
    booking_provider.create_instant_cart.return_value = {"cartId": "cart-live-123"}
    booking_provider.create_confirmed_booking.return_value = {
        "bookingId": "bk-live-123", "orderId": "or-live-123", "status": "CONFIRMED"
    }

    orchestrator = BookingOrchestrator(
        booking_provider=booking_provider,
        contract_provider=MagicMock(spec=AgentContractProvider),
    )
    orchestrator.check_availability("Whitefield", "12 July", 6, "Bomb Defusal")
    booking = orchestrator.prepare_booking(
        {
            "customer_name": "Demo User",
            "phone": "9876543210",
            "location": "Whitefield",
            "preferred_date": "12 July",
            "participants": 6,
            "age_group": "adults",
        },
        "3 PM",
    )

    assert booking["booking_id"] == "bk-live-123"
    assert booking["booking_reference"] == "or-live-123"
    assert booking["confirmed"] is True
    booking_provider.create_instant_cart.assert_called_once()
    booking_provider.create_confirmed_booking.assert_called_once()
    payload = booking_provider.create_confirmed_booking.call_args.args[0]
    assert payload["venueId"] == "loc-whitefield"
    assert payload["cartId"] == "cart-live-123"
    assert payload["slots"][0]["gameId"] == "game-bomb"
    assert payload["slots"][0]["people"] == [{"categoryId": "adult", "number": 6}]
    assert payload["customer"]["phone"] == "+919876543210"
    assert payload["idempotencyKey"]
    messages = [record.getMessage() for record in caplog.records]
    assert any("KREEDA_AVAILABILITY_SUCCESS" in message for message in messages)
    assert any("KREEDA_PREPARE_BOOKING" in message for message in messages)
    assert any("KREEDA_CREATE_CART_REQUEST" in message for message in messages)
    assert any("KREEDA_CREATE_CART_SUCCESS" in message for message in messages)
    assert any("KREEDA_CREATE_BOOKING_REQUEST" in message for message in messages)
    assert any("KREEDA_CREATE_BOOKING_SUCCESS" in message for message in messages)
    assert any("KREEDA_BOOKING_CREATED" in message for message in messages)
    assert any("BOOKING_ID=bk-live-123" in message for message in messages)
    assert any("BOOKING_REFERENCE=or-live-123" in message for message in messages)


def test_live_booking_failure_emits_explicit_failure_marker(monkeypatch, caplog) -> None:
    caplog.set_level("INFO")
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.delenv("BOOKING_PROVIDER", raising=False)
    provider = MagicMock(spec=BreakoutBookingProvider)
    provider.get_booking_venues.return_value = [{"venueId": "venue-1", "name": "Whitefield"}]
    provider.get_booking_games.return_value = [{
        "gameId": "game-1", "name": "Undercover", "peopleMin": 3, "peopleMax": 8,
        "peopleCategories": [{"categoryId": "adult", "categoryName": "Adults", "max": 8}],
    }]
    provider.search_booking_slots.return_value = [{
        "eventId": "event-1", "time": "20:20", "available": 8, "isAvailable": True,
    }]
    provider.create_instant_cart.side_effect = RuntimeError("cart rejected")
    orchestrator = BookingOrchestrator(
        booking_provider=provider,
        contract_provider=MagicMock(spec=AgentContractProvider),
    )
    orchestrator.check_availability("Whitefield", "Tomorrow", 3, "Undercover")

    result = orchestrator.prepare_booking({
        "customer_name": "Sadart Rao", "phone": "9983340357", "location": "Whitefield",
        "preferred_date": "Tomorrow", "participants": 3, "age_group": "adults",
    }, "8:20 PM")

    assert result["confirmed"] is False
    assert any("KREEDA_CREATE_CART_FAILURE" in record.getMessage() for record in caplog.records)
    assert any("KREEDA_CREATE_BOOKING_FAILURE" in record.getMessage() for record in caplog.records)


def test_live_availability_preserves_slots_but_marks_group_capacity_unsupported(monkeypatch) -> None:
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.delenv("BOOKING_PROVIDER", raising=False)
    provider = MagicMock(spec=BreakoutBookingProvider)
    provider.get_booking_venues.return_value = [{"venueId": "venue-1", "name": "Whitefield"}]
    provider.get_booking_games.return_value = [{"gameId": "game-1", "name": "Undercover"}]
    provider.search_booking_slots.return_value = [
        {"eventId": "event-1", "time": "15:00", "available": 8, "isAvailable": True}
    ]
    orchestrator = BookingOrchestrator(
        booking_provider=provider,
        contract_provider=MagicMock(spec=AgentContractProvider),
    )

    result = orchestrator.check_availability("Whitefield", "2027-06-25", 11, "Undercover")

    assert result["available"] is True
    assert result["slots"] == ["3:00 PM"]
    assert result["capacity_supported"] is False
    assert result["bookable_slots"] == []
    assert result["max_available_capacity"] == 8


def test_tomorrow_is_iso_normalized_before_kreeda_slot_request(monkeypatch) -> None:
    monkeypatch.setenv("BOOKING_API_KEY", "test-key")
    monkeypatch.setenv("BOOKING_BASE_URL", "https://test.api")
    monkeypatch.delenv("BOOKING_PROVIDER", raising=False)
    provider = MagicMock(spec=BreakoutBookingProvider)
    provider.get_booking_venues.return_value = [{"venueId": "venue-1", "name": "JP Nagar"}]
    provider.get_booking_games.return_value = [{"gameId": "game-1", "name": "Murder Mystery"}]
    provider.search_booking_slots.return_value = []
    orchestrator = BookingOrchestrator(
        booking_provider=provider,
        contract_provider=MagicMock(spec=AgentContractProvider),
    )

    orchestrator.check_availability("JP Nagar", "Tomorrow", 6, "Murder Mystery")

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    provider.search_booking_slots.assert_called_once_with("venue-1", "game-1", tomorrow, tomorrow)
