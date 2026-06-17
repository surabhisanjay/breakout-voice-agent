from __future__ import annotations

import re
import threading
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from main import BASE_DIR, build_inbound_agent, dispatch
from src.agents.booking_agent import BookingAgent
from src.agents.inbound_agent import InboundAgent
from src.memory.conversation_memory import ConversationMemory


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
API_MEMORY_DIR = BASE_DIR / "memory" / "api_sessions"


class HealthResponse(BaseModel):
    status: str


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=80)
    message: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    response: str


class ResetRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=80)


class ResetResponse(BaseModel):
    success: bool


class MemoryResponse(BaseModel):
    session_id: str
    memory: dict[str, Any]


@dataclass
class SessionRuntime:
    inbound: InboundAgent
    booking: BookingAgent | None = None
    active_agent: str = "inbound_agent"


app = FastAPI(title="Breakout Agent API", version="1.0.0")
_sessions: dict[str, SessionRuntime] = {}
_lock = threading.RLock()


def _validate_session_id(session_id: str) -> str:
    clean = session_id.strip()
    if not SESSION_ID_PATTERN.fullmatch(clean):
        raise HTTPException(
            status_code=400,
            detail="session_id may contain only letters, numbers, underscore, dash, dot, and colon.",
        )
    return clean


def _session_memory_path(session_id: str) -> Path:
    clean = _validate_session_id(session_id)
    API_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return API_MEMORY_DIR / f"{clean}.json"


def _api_args() -> Namespace:
    return Namespace(model=None, no_openai=False)


def _initial_active_agent(memory: ConversationMemory) -> str:
    return "booking_agent" if memory.data.get("current_workflow") == "booking" else "inbound_agent"


def _get_runtime(session_id: str) -> SessionRuntime:
    clean = _validate_session_id(session_id)
    with _lock:
        existing = _sessions.get(clean)
        if existing is not None:
            return existing

        memory = ConversationMemory(_session_memory_path(clean))
        inbound = build_inbound_agent(_api_args(), memory)
        runtime = SessionRuntime(
            inbound=inbound,
            active_agent=_initial_active_agent(memory),
        )
        if runtime.active_agent == "booking_agent":
            runtime.booking = BookingAgent(memory)
        _sessions[clean] = runtime
        return runtime


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message cannot be empty.")

    runtime = _get_runtime(request.session_id)
    with _lock:
        result, booking, active_agent = dispatch(
            message,
            runtime.inbound,
            runtime.booking,
            runtime.active_agent,
        )
        runtime.booking = booking
        runtime.active_agent = active_agent
    return ChatResponse(response=result.response)


@app.post("/reset", response_model=ResetResponse)
def reset(request: ResetRequest) -> ResetResponse:
    clean = _validate_session_id(request.session_id)
    with _lock:
        memory = ConversationMemory(_session_memory_path(clean))
        memory.reset()
        _sessions.pop(clean, None)
    return ResetResponse(success=True)


@app.get("/memory/{session_id}", response_model=MemoryResponse)
def memory(session_id: str) -> MemoryResponse:
    runtime = _get_runtime(session_id)
    return MemoryResponse(session_id=_validate_session_id(session_id), memory=runtime.inbound.memory.as_state())
