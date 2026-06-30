from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any
from backend.app.db.session import get_db
from backend.app.schemas.analytics import EventIngestPayload
from src.analytics.analytics_service import AnalyticsService
from src.analytics.websocket_server import ws_server

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

def _get_filters(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    agent_id: Optional[str] = None,
    team_id: Optional[str] = None,
    location: Optional[str] = None,
    room: Optional[str] = None,
    sentiment: Optional[str] = None,
    priority: Optional[str] = None,
    channel: Optional[str] = None
) -> dict:
    filters = {}
    if start_date and end_date:
        filters["date_range"] = (start_date, end_date)
    
    for key, val in [
        ("agent_id", agent_id),
        ("team_id", team_id),
        ("location", location),
        ("room", room),
        ("sentiment", sentiment),
        ("priority", priority),
        ("channel", channel)
    ]:
        if val:
            filters[key] = val
    return filters

@router.get("/full")
def get_full_dashboard(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    agent_id: Optional[str] = None,
    team_id: Optional[str] = None,
    location: Optional[str] = None,
    room: Optional[str] = None,
    sentiment: Optional[str] = None,
    priority: Optional[str] = None,
    channel: Optional[str] = None,
    db: Session = Depends(get_db)
):
    filters = _get_filters(start_date, end_date, agent_id, team_id, location, room, sentiment, priority, channel)
    return AnalyticsService.get_full_dashboard_analytics(filters)

@router.get("/kpis")
def get_kpis(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    agent_id: Optional[str] = None,
    team_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    filters = _get_filters(start_date, end_date, agent_id, team_id)
    return AnalyticsService.get_executive_kpis(filters)

@router.get("/funnel")
def get_funnel(db: Session = Depends(get_db)):
    return AnalyticsService.get_sales_funnel({})

@router.get("/agents")
def get_agents(db: Session = Depends(get_db)):
    return AnalyticsService.get_agent_performance({})

@router.post("/ingest")
def ingest_event(payload: EventIngestPayload, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    try:
        # Ingest dynamically to SQLite/Postgres
        AnalyticsService.ingest_event(
            session_id=payload.session_id,
            event_type=payload.event_type,
            metadata=payload.metadata,
            agent_id=payload.agent_id,
            team_id=payload.team_id
        )
        
        # Dispatch socket payload in background task to avoid blocking HTTP turn responses
        background_tasks.add_task(
            ws_server.broadcast,
            payload.event_type,
            {"session_id": payload.session_id, **payload.metadata}
        )
        
        return {"status": "success", "event_type": payload.event_type}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/leads")
def get_leads(db: Session = Depends(get_db)):
    return AnalyticsService.get_leads()

@router.post("/leads")
def create_lead(payload: dict, db: Session = Depends(get_db)):
    return AnalyticsService.save_lead(payload)

@router.get("/agents/list")
def get_agents_list(db: Session = Depends(get_db)):
    return AnalyticsService.get_agents_list()

@router.post("/agents/create")
def create_agent(payload: dict, db: Session = Depends(get_db)):
    return AnalyticsService.save_agent(payload)

@router.get("/sessions")
def get_sessions(db: Session = Depends(get_db)):
    return AnalyticsService.get_sessions_list()

@router.get("/sessions/{session_id}")
def get_session_detail(session_id: str, db: Session = Depends(get_db)):
    return AnalyticsService.get_session_detail(session_id)

@router.get("/sessions/{session_id}/tasks")
def get_tasks(session_id: str):
    return AnalyticsService.get_tasks(session_id)

@router.post("/sessions/{session_id}/tasks")
def create_task(session_id: str, payload: dict):
    return AnalyticsService.save_task(
        session_id=session_id,
        description=payload.get("description", ""),
        priority=payload.get("priority", "Medium"),
        due_date=payload.get("due_date", "")
    )

@router.get("/sessions/{session_id}/notes")
def get_notes(session_id: str):
    return AnalyticsService.get_notes(session_id)

@router.post("/sessions/{session_id}/notes")
def create_note(session_id: str, payload: dict):
    return AnalyticsService.save_note(
        session_id=session_id,
        content=payload.get("content", "")
    )

@router.get("/sessions/{session_id}/activities")
def get_activities(session_id: str):
    return AnalyticsService.get_activities(session_id)

@router.get("/sessions/{session_id}/transcripts")
def get_transcripts(session_id: str):
    return AnalyticsService.get_transcripts(session_id)

@router.post("/sessions/{session_id}/transcripts")
def create_transcript_turn(session_id: str, payload: dict):
    return AnalyticsService.save_transcript_turn(
        session_id=session_id,
        speaker=payload.get("speaker", "User"),
        text=payload.get("text", "")
    )

@router.post("/sessions/{session_id}/escalate")
def manual_escalate(session_id: str, payload: dict):
    return AnalyticsService.trigger_escalation(
        session_id=session_id,
        reason=payload.get("reason", "Manual Trigger Override")
    )
