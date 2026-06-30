import datetime
from sqlalchemy import Column, String, Integer, Float, DateTime, Date
from backend.app.db.session import Base

class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"
    
    event_id = Column(String, primary_key=True)
    session_id = Column(String, index=True, nullable=False)
    event_type = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    agent_id = Column(String, default="inbound_agent")
    team_id = Column(String, default="front_desk")
    metadata = Column(String, nullable=False)  # JSON-serialized string

class Call(Base):
    __tablename__ = "calls"
    
    session_id = Column(String, primary_key=True)
    direction = Column(String, nullable=False)
    status = Column(String, nullable=False)
    started_at = Column(DateTime)
    ended_at = Column(DateTime)
    duration = Column(Integer, default=0)
    latency = Column(Float, default=0.0)
    ttfr = Column(Float, default=0.0)
    silence_percent = Column(Float, default=0.0)
    interruptions = Column(Integer, default=0)
    speech_speed = Column(Float, default=0.0)
    speaking_ratio = Column(Float, default=0.0)

class BookingAnalytics(Base):
    __tablename__ = "bookings_analytics"
    
    booking_id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False)
    booking_reference = Column(String, nullable=False)
    status = Column(String, nullable=False)
    room = Column(String)
    location = Column(String)
    booking_date = Column(String)
    slot = Column(String)
    participants = Column(Integer, default=0)
    event_type = Column(String)
    revenue = Column(Float, default=0.0)
    payment_received = Column(Integer, default=0)

class AgentPerformance(Base):
    __tablename__ = "agent_performance"
    
    session_id = Column(String, primary_key=True)
    agent_id = Column(String, nullable=False)
    team_id = Column(String, nullable=False)
    call_date = Column(Date, nullable=False)
    handle_time = Column(Integer, default=0)
    talk_time = Column(Integer, default=0)
    talk_listen_ratio = Column(Float, default=0.5)
    response_time = Column(Float, default=0.0)
    quality_score = Column(Integer, default=0)
    csat = Column(Integer, default=0)
    is_missed = Column(Integer, default=0)
    idle_time = Column(Integer, default=0)
    occupancy = Column(Float, default=0.0)

class LLMMetric(Base):
    __tablename__ = "llm_metrics"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    cost = Column(Float, default=0.0)
    latency = Column(Float, default=0.0)
    first_token_time = Column(Float, default=0.0)
    tool_call_latency = Column(Float, default=0.0)
    json_success = Column(Integer, default=1)
    function_success = Column(Integer, default=1)

class KnowledgeRetrieval(Base):
    __tablename__ = "knowledge_retrieval"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    success = Column(Integer, default=1)
    latency = Column(Float, default=0.0)
    citation_accuracy = Column(Float, default=1.0)
    faq_hit = Column(Integer, default=0)
    hallucination_reduced = Column(Integer, default=1)

class Escalation(Base):
    __tablename__ = "escalations"
    
    session_id = Column(String, primary_key=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    reason = Column(String, nullable=False)
    priority = Column(String, nullable=False)
    status = Column(String, nullable=False)
    resolved_at = Column(DateTime)
    handoff_to = Column(String, nullable=False)

class SentimentTimeline(Base):
    __tablename__ = "sentiment_timeline"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    score = Column(Integer, nullable=False)
    label = Column(String, nullable=False)

class Lead(Base):
    __tablename__ = "leads"
    
    lead_id = Column(String, primary_key=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    company_name = Column(String)
    location = Column(String)
    escape_room = Column(String)
    value = Column(Float, default=0.0)
    group_size = Column(Integer, default=1)
    stage = Column(String, default="New") # New, Qualified, Booking, Payment
    source = Column(String)
    channel = Column(String)
    priority = Column(String, default="Medium")
    booking_date = Column(String)
    booking_time = Column(String)
    notes = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Agent(Base):
    __tablename__ = "agents"
    
    agent_id = Column(String, primary_key=True)
    agent_name = Column(String, nullable=False)
    description = Column(String)
    department = Column(String)
    role = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="agent")

class Team(Base):
    __tablename__ = "teams"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_name = Column(String, unique=True, nullable=False)
    description = Column(String)

class Customer(Base):
    __tablename__ = "customers"
    
    id = Column(String, primary_key=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    company = Column(String)
    lead_source = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Task(Base):
    __tablename__ = "tasks"
    
    id = Column(String, primary_key=True)
    session_id = Column(String, index=True)
    description = Column(String, nullable=False)
    status = Column(String, default="Pending") # Pending, Completed
    priority = Column(String, default="Medium")
    due_date = Column(String)

class Note(Base):
    __tablename__ = "notes"
    
    id = Column(String, primary_key=True)
    session_id = Column(String, index=True)
    content = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Activity(Base):
    __tablename__ = "activities"
    
    id = Column(String, primary_key=True)
    session_id = Column(String, index=True)
    type = Column(String, nullable=False) # call, email, sms, task, update
    description = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

class TranscriptTurn(Base):
    __tablename__ = "transcripts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, index=True, nullable=False)
    speaker = Column(String, nullable=False) # Agent, Customer
    text = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
