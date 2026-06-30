import pytest
import sqlite3
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.analytics.db import init_db, get_db_connection
from src.analytics.analytics_service import AnalyticsService

def test_analytics_database_initialization() -> None:
    """Verifies tables are created correctly during init_db."""
    init_db()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row["name"] for row in cursor.fetchall()]
    
    assert "analytics_events" in tables
    assert "calls" in tables
    assert "bookings_analytics" in tables
    assert "agent_performance" in tables
    assert "llm_metrics" in tables
    assert "knowledge_retrieval" in tables
    assert "escalations" in tables
    assert "sentiment_timeline" in tables
    conn.close()

def test_analytics_event_ingestion_and_kpis() -> None:
    """Verifies that ingesting events updates metrics tables and filters yield correct results."""
    init_db()
    session_id = "test_sess_999"
    
    # 1. Ingest call started event
    AnalyticsService.ingest_event(
        session_id=session_id,
        event_type="call_started",
        metadata={"direction": "inbound"},
        agent_id="test_agent_1",
        team_id="test_team_1"
    )
    
    # 2. Ingest booking created event
    AnalyticsService.ingest_event(
        session_id=session_id,
        event_type="booking_created",
        metadata={
            "booking_id": "test_bk_999",
            "booking_reference": "test_ref_999",
            "status": "completed",
            "room": "Pharaoh",
            "location": "Indiranagar",
            "date": "2026-06-29",
            "slot": "5:00 PM",
            "participants": 6,
            "event_type": "friends",
            "revenue": 300.00,
            "payment_received": 1
        },
        agent_id="test_agent_1",
        team_id="test_team_1"
    )
    
    # Verify booking exists
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bookings_analytics WHERE booking_id='test_bk_999'")
    booking = cursor.fetchone()
    assert booking is not None
    assert booking["revenue"] == 300.00
    assert booking["participants"] == 6
    conn.close()
    
    # Fetch filtered KPIs
    kpis = AnalyticsService.get_executive_kpis({"agent_id": "test_agent_1"})
    assert kpis["revenue"] >= 300.00
