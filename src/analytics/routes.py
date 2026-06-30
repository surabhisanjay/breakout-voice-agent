from flask import Blueprint, jsonify, request
from datetime import datetime
from .analytics_service import AnalyticsService
from .websocket_server import ws_server

analytics_bp = Blueprint("analytics", __name__)

def _parse_global_filters() -> dict:
    """Helper to extract common query filters from API requests."""
    filters = {}
    
    # Parse date range: e.g. ?start_date=2026-06-01&end_date=2026-06-30
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    if start_date and end_date:
        filters["date_range"] = (start_date, end_date)
        
    # Additional global filters
    for field in ("agent_id", "team_id", "location", "room", "sentiment", "priority", "channel"):
        val = request.args.get(field)
        if val:
            filters[field] = val
            
    return filters

@analytics_bp.route("/api/analytics/full", methods=["GET"])
def get_full_dashboard():
    """Returns all 18 metric categories matching global filter queries."""
    filters = _parse_global_filters()
    data = AnalyticsService.get_full_dashboard_analytics(filters)
    return jsonify(data)

@analytics_bp.route("/api/analytics/kpis", methods=["GET"])
def get_kpis():
    filters = _parse_global_filters()
    return jsonify(AnalyticsService.get_executive_kpis(filters))

@analytics_bp.route("/api/analytics/funnel", methods=["GET"])
def get_funnel():
    filters = _parse_global_filters()
    return jsonify(AnalyticsService.get_sales_funnel(filters))

@analytics_bp.route("/api/analytics/agents", methods=["GET"])
def get_agents():
    filters = _parse_global_filters()
    return jsonify(AnalyticsService.get_agent_performance(filters))

@analytics_bp.route("/api/analytics/ingest", methods=["POST"])
def ingest_event():
    """Allows manual event ingestion to trigger live stats recalculations."""
    payload = request.json or {}
    session_id = payload.get("session_id")
    event_type = payload.get("event_type")
    metadata = payload.get("metadata", {})
    agent_id = payload.get("agent_id", "inbound_agent")
    team_id = payload.get("team_id", "front_desk")
    
    if not session_id or not event_type:
        return jsonify({"error": "Missing session_id or event_type"}), 400
        
    try:
        # Ingest to SQLite
        AnalyticsService.ingest_event(session_id, event_type, metadata, agent_id, team_id)
        
        # Realtime dispatch to WS clients
        ws_server.broadcast(event_type, {"session_id": session_id, **metadata})
        
        return jsonify({"status": "success", "event_type": event_type, "session_id": session_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
