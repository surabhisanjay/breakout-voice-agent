from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import json
import os
from unittest.mock import MagicMock, patch
import pytest

from src.integrations.kreeda.agent_contract_provider import AgentContractProvider, AgentContractAPIError
from src.integrations.kreeda.breakout_booking_provider import BreakoutBookingProvider
from src.orchestration.booking_orchestrator import BookingOrchestrator
from integrations.langgraph_booking_node import booking_node_handler, run_booking_workflow


class MockHTTPResponse:
    def __init__(self, data: bytes, code: int = 200) -> None:
        self.data = data
        self.code = code

    def read(self) -> bytes:
        return self.data

    def __enter__(self) -> MockHTTPResponse:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


@pytest.fixture
def mock_tools_response() -> bytes:
    return json.dumps({
        "tools": [
            {"name": "get_venues", "description": "Get venues"},
            {"name": "get_available_games", "description": "Get games"},
            {"name": "search_available_seats", "description": "Search seats"},
            {"name": "create_booking", "description": "Create booking"},
            {"name": "cancel_booking", "description": "Cancel booking"},
            {"name": "reschedule_booking", "description": "Reschedule booking"},
            {"name": "find_booking", "description": "Find booking"}
        ]
    }).encode("utf-8")


@pytest.fixture
def mock_locations_response() -> bytes:
    return json.dumps([
        {"locationId": "loc-1", "locationName": "Koramangala"},
        {"locationId": "loc-2", "locationName": "Whitefield"}
    ]).encode("utf-8")


@pytest.fixture
def mock_games_response() -> bytes:
    return json.dumps([
        {"gameId": "game-1", "gameName": "Murder Mystery"},
        {"gameId": "game-2", "gameName": "Hostage"}
    ]).encode("utf-8")


@pytest.fixture
def mock_slots_response() -> bytes:
    return json.dumps([
        {"slotId": "slot-1", "time": "18:00", "isAvailable": True, "date": "2026-07-01"},
        {"slotId": "slot-2", "time": "20:00", "isAvailable": True, "date": "2026-07-01"}
    ]).encode("utf-8")


# ---------------------------------------------------------------------------
# Phase 1 Tests: AgentContractProvider
# ---------------------------------------------------------------------------

def test_agent_contract_provider_initialization_and_discovery(mock_tools_response) -> None:
    with patch("src.integrations.kreeda.agent_contract_provider.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockHTTPResponse(mock_tools_response)
        
        provider = AgentContractProvider(base_url="https://test.api", api_key="test-key")
        
        assert provider.configured is True
        assert "get_venues" in provider.cached_tools
        assert provider.cached_tools["get_venues"]["description"] == "Get venues"


def test_agent_contract_provider_methods() -> None:
    # Set up mock tools response for init and mock response for methods
    with patch("src.integrations.kreeda.agent_contract_provider.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            MockHTTPResponse(json.dumps({"tools": []}).encode("utf-8")),  # Init tools lookup
            MockHTTPResponse(json.dumps([{"venueId": "v1"}]).encode("utf-8")),  # get_venues
            MockHTTPResponse(json.dumps({"bookingId": "bk-99", "status": "cancelled"}).encode("utf-8")),  # cancel_booking
        ]
        
        provider = AgentContractProvider(base_url="https://test.api", api_key="test-key")
        
        # Test get_venues
        venues = provider.get_venues()
        assert len(venues) == 1
        assert venues[0]["venueId"] == "v1"

        # Test cancel_booking
        cancel_res = provider.cancel_booking(booking_ref="bk-99", reason="customer wanted cancellation")
        assert cancel_res["bookingId"] == "bk-99"
        assert cancel_res["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Phase 2 Tests: BreakoutBookingProvider
# ---------------------------------------------------------------------------

def test_breakout_booking_provider_methods(mock_locations_response, mock_slots_response) -> None:
    with patch("src.integrations.kreeda.breakout_api.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            MockHTTPResponse(mock_locations_response),
            MockHTTPResponse(mock_slots_response),
            MockHTTPResponse(json.dumps({"status": "released"}).encode("utf-8")) # release_slots
        ]
        
        provider = BreakoutBookingProvider()
        # Temporarily force api_key/base_url config
        provider.client.api_key = "test-key"
        provider.client.base_url = "https://test.api"

        locs = provider.get_locations()
        assert len(locs) == 2
        assert locs[0]["locationName"] == "Koramangala"

        slots = provider.get_slots(location_id="loc-1")
        assert len(slots) == 2
        assert slots[0]["slotId"] == "slot-1"

        release_res = provider.release_slots(["slot-1"])
        assert release_res["status"] == "released"


# ---------------------------------------------------------------------------
# Phase 3 Tests: BookingOrchestrator Routing Rules
# ---------------------------------------------------------------------------

def test_booking_orchestrator_routing_live(mock_tools_response, mock_locations_response, mock_games_response, mock_slots_response) -> None:
    # In live mode, verify that methods are delegated to correct providers
    mock_bp = MagicMock(spec=BreakoutBookingProvider)
    mock_bp.get_booking_venues.return_value = json.loads(mock_locations_response.decode("utf-8"))
    mock_bp.get_booking_games.return_value = [
        {
            "gameId": "game-1",
            "gameName": "Murder Mystery",
            "peopleCategories": [{"categoryId": "adult", "categoryName": "Adults", "max": 10}],
        }
    ]
    mock_bp.search_booking_slots.return_value = json.loads(mock_slots_response.decode("utf-8"))
    mock_bp.create_instant_cart.return_value = {"cartId": "cart-live-1"}
    mock_bp.create_confirmed_booking.return_value = {
        "bookingId": "bk-live-1",
        "orderId": "or-live-1",
        "status": "CONFIRMED",
    }
    mock_bp.release_slots.return_value = {"status": "success"}

    mock_cp = MagicMock(spec=AgentContractProvider)
    mock_cp.cancel_booking.return_value = {"bookingId": "bk-cancelled", "status": "cancelled"}
    mock_cp.reschedule_booking.return_value = {"bookingId": "bk-rescheduled", "status": "rescheduled"}

    with patch.dict(os.environ, {"BOOKING_API_KEY": "test-key", "BOOKING_BASE_URL": "https://test.api"}):
        orchestrator = BookingOrchestrator(booking_provider=mock_bp, contract_provider=mock_cp)
        assert orchestrator.is_live is True

        # Test operational flow routing (goes to BreakoutBookingProvider)
        avail = orchestrator.check_availability(location="Koramangala", date="2026-07-01", participants=2)
        assert avail["available"] is True
        assert "6:00 PM" in avail["slots"]  # 18:00 display format
        assert mock_bp.get_booking_venues.call_count == 1

        prep = orchestrator.prepare_booking(
            {
                "customer_name": "Alice Bob",
                "phone": "1234",
                "location": "Koramangala",
                    "preferred_date": "2026-07-01",
                    "participants": 2,
                    "age_group": "adults",
                },
            "6:00 PM",
        )
        assert prep["booking_id"] == "bk-live-1"
        mock_bp.create_instant_cart.assert_called_once()
        mock_bp.create_confirmed_booking.assert_called_once()

        orchestrator.release_slots(["slot-1"])
        mock_bp.release_slots.assert_called_once_with(["slot-1"])

        # Test advanced operations routing (goes to AgentContractProvider)
        orchestrator.cancel_booking("bk-1", "user requested")
        mock_cp.cancel_booking.assert_called_once_with("bk-1", "user requested")

        orchestrator.reschedule_booking("bk-1", "slot-new")
        mock_cp.reschedule_booking.assert_called_once_with("bk-1", "slot-new")


def test_booking_orchestrator_routing_simulator() -> None:
    # In simulator mode, verify it delegates to the SimulatorProvider / mock DB
    orchestrator = BookingOrchestrator()
    assert orchestrator.is_live is False

    # Standard availability check
    avail = orchestrator.check_availability("Koramangala", "18 June", 2)
    assert avail["available"] is True
    assert "10:00 AM" in avail["slots"]

    # Advanced operations return mock successes safely
    cancel = orchestrator.cancel_booking("BK-999")
    assert cancel["status"] == "cancelled"
    assert cancel["booking_id"] == "BK-999"


# ---------------------------------------------------------------------------
# Phase 4 Tests: LangGraph Integration Path
# ---------------------------------------------------------------------------

def test_langgraph_booking_node_integration() -> None:
    # Test that run_booking_workflow works with BookingOrchestrator in simulator mode
    handoff = {
        "customer_name": "Priya",
        "phone": "9876543210",
        "participants": 4,
        "intent": "birthday_party",
        "preferred_date": "2026-07-01",
    }
    
    # Run the top-level handler
    result = booking_node_handler(handoff, require_payment=False)
    
    assert result["status"] == "booked"
    assert result["booking_ref"].startswith("BK-")
    assert result["payment_required"] is False
    assert result["error"] is None
    assert isinstance(result["log"], list)
