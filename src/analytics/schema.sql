-- Production Analytics & Metrics Schema for Voice CRM / Sales OS

-- Raw Event Ingestion Log
CREATE TABLE IF NOT EXISTS analytics_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,       -- e.g., 'call_started', 'intent_detected', 'slot_filled', 'tool_called', 'escalated'
    timestamp DATETIME NOT NULL,
    agent_id TEXT DEFAULT 'inbound_agent',
    team_id TEXT DEFAULT 'front_desk',
    metadata TEXT NOT NULL          -- JSON payload of the event details
);

-- Index events for faster filtering
CREATE INDEX IF NOT EXISTS idx_events_session ON analytics_events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_type_timestamp ON analytics_events(event_type, timestamp);

-- Call logs (Voice AI Metrics)
CREATE TABLE IF NOT EXISTS calls (
    session_id TEXT PRIMARY KEY,
    direction TEXT NOT NULL,        -- 'inbound' or 'outbound'
    status TEXT NOT NULL,           -- 'connected', 'missed', 'dropped', 'completed'
    started_at DATETIME,
    ended_at DATETIME,
    duration INTEGER DEFAULT 0,     -- in seconds
    latency REAL DEFAULT 0.0,       -- round-trip latency in seconds
    ttfr REAL DEFAULT 0.0,          -- Time To First Response in seconds
    silence_percent REAL DEFAULT 0.0,
    interruptions INTEGER DEFAULT 0,
    speech_speed REAL DEFAULT 0.0,  -- words per minute
    speaking_ratio REAL DEFAULT 0.0 -- speaking time vs client time
);

-- Booking Analytics
CREATE TABLE IF NOT EXISTS bookings_analytics (
    booking_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    booking_reference TEXT NOT NULL,
    status TEXT NOT NULL,           -- 'pending', 'completed', 'cancelled', 'rescheduled'
    room TEXT,
    location TEXT,
    booking_date TEXT,
    slot TEXT,
    participants INTEGER DEFAULT 0,
    event_type TEXT,                -- 'friends', 'corporate', 'birthday'
    revenue REAL DEFAULT 0.0,
    payment_received INTEGER DEFAULT 0 -- boolean (0 or 1)
);

CREATE INDEX IF NOT EXISTS idx_bookings_session ON bookings_analytics(session_id);

-- Agent Performance logs
CREATE TABLE IF NOT EXISTS agent_performance (
    session_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    call_date DATE NOT NULL,
    handle_time INTEGER DEFAULT 0,
    talk_time INTEGER DEFAULT 0,
    talk_listen_ratio REAL DEFAULT 0.5,
    response_time REAL DEFAULT 0.0,
    quality_score INTEGER DEFAULT 0,
    csat INTEGER DEFAULT 0,
    is_missed INTEGER DEFAULT 0,
    idle_time INTEGER DEFAULT 0,
    occupancy REAL DEFAULT 0.0
);

-- LLM Metrics (Token consumption, pricing, function calls)
CREATE TABLE IF NOT EXISTS llm_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp DATETIME NOT NULL,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cost REAL DEFAULT 0.0,
    latency REAL DEFAULT 0.0,
    first_token_time REAL DEFAULT 0.0,
    tool_call_latency REAL DEFAULT 0.0,
    json_success INTEGER DEFAULT 1,
    function_success INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_llm_session ON llm_metrics(session_id);

-- Knowledge Base Metrics (RAG Performance)
CREATE TABLE IF NOT EXISTS knowledge_retrieval (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp DATETIME NOT NULL,
    success INTEGER DEFAULT 1,
    latency REAL DEFAULT 0.0,
    citation_accuracy REAL DEFAULT 1.0,
    faq_hit INTEGER DEFAULT 0,
    hallucination_reduced INTEGER DEFAULT 1
);

-- Escalation logs
CREATE TABLE IF NOT EXISTS escalations (
    session_id TEXT PRIMARY KEY,
    timestamp DATETIME NOT NULL,
    reason TEXT NOT NULL,           -- 'pricing', 'refund', 'complaint', 'technical', 'policy', 'sentiment_trigger'
    priority TEXT NOT NULL,         -- 'low', 'medium', 'high', 'critical'
    status TEXT NOT NULL,           -- 'pending', 'resolved', 'in_progress'
    resolved_at DATETIME,
    handoff_to TEXT NOT NULL        -- 'events_team', 'escalation_agent', 'support'
);

-- Sentiment logs over time
CREATE TABLE IF NOT EXISTS sentiment_timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp DATETIME NOT NULL,
    score INTEGER NOT NULL,         -- -2 (angry) to 2 (delighted)
    label TEXT NOT NULL             -- 'positive', 'neutral', 'negative', 'frustrated', 'angry', 'confused'
);

CREATE INDEX IF NOT EXISTS idx_sentiment_session ON sentiment_timeline(session_id);

-- Pipeline Leads table
CREATE TABLE IF NOT EXISTS leads (
    lead_id TEXT PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    company_name TEXT,
    location TEXT,
    escape_room TEXT,
    value REAL DEFAULT 0.0,
    group_size INTEGER DEFAULT 1,
    stage TEXT DEFAULT 'New',
    source TEXT,
    channel TEXT,
    priority TEXT DEFAULT 'Medium',
    booking_date TEXT,
    booking_time TEXT,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Team Agents table
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    description TEXT,
    department TEXT,
    role TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'agent'
);

-- Teams table
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_name TEXT UNIQUE NOT NULL,
    description TEXT
);

-- Customers table
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    company TEXT,
    lead_source TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Tasks table
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    description TEXT NOT NULL,
    status TEXT DEFAULT 'Pending',
    priority TEXT DEFAULT 'Medium',
    due_date TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);

-- Notes table
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    content TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_notes_session ON notes(session_id);

-- Activities table
CREATE TABLE IF NOT EXISTS activities (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    type TEXT NOT NULL,
    description TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_activities_session ON activities(session_id);

-- Transcripts table
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    speaker TEXT NOT NULL,
    text TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_transcripts_session ON transcripts(session_id);
