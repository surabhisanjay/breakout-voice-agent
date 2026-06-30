from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any

class EventIngestPayload(BaseModel):
    session_id: str
    event_type: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    agent_id: str = "inbound_agent"
    team_id: str = "front_desk"

class GlobalFiltersSchema(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    agent_id: Optional[str] = None
    team_id: Optional[str] = None
    location: Optional[str] = None
    room: Optional[str] = None
    sentiment: Optional[str] = None
    priority: Optional[str] = None
    channel: Optional[str] = None
