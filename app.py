from __future__ import annotations

import json
import logging
import os
import re
import threading
from argparse import Namespace
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field

from main import BASE_DIR, build_inbound_agent, dispatch
from src.config.env_loader import booking_provider_label
from src.agents.booking_agent import BookingAgent
from src.agents.inbound_agent import InboundAgent
from src.memory.conversation_memory import ConversationMemory


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
API_MEMORY_DIR = BASE_DIR / "memory" / "api_sessions"
API_VERSION = "1.1.0"


class HealthResponse(BaseModel):
    status: str
    booking_provider: str
    version: str


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=80)
    message: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    response: str
    next_agent: str = "inbound_agent"
    escalation: dict[str, Any] = Field(default_factory=dict)
    handoff_summary: Optional[dict[str, Any]] = None


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


logger = logging.getLogger(__name__)
_sessions: dict[str, SessionRuntime] = {}
_lock = threading.RLock()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    openai_enabled = bool(os.environ.get("OPENAI_API_KEY"))
    personality_path = BASE_DIR / "prompts" / "breakout_personality_prompt.txt"
    examples_path = BASE_DIR / "prompts" / "transcript_examples.json"
    personality_loaded = personality_path.is_file() and bool(personality_path.read_text(encoding="utf-8").strip())
    transcript_examples = json.loads(examples_path.read_text(encoding="utf-8")) if examples_path.is_file() else []
    logger.info("OPENAI_ENABLED=%s", openai_enabled)
    logger.info("BOOKING_PROVIDER=%s", booking_provider_label())
    logger.info("PERSONALITY_PROMPT_LOADED=%s", personality_loaded)
    logger.info("TRANSCRIPT_EXAMPLES_LOADED=%s", len(transcript_examples))
    logger.info("CHATREQUEST_SCHEMA=%s", json.dumps(ChatRequest.model_json_schema(), separators=(",", ":")))
    yield


app = FastAPI(title="Breakout Agent API", version=API_VERSION, lifespan=lifespan)


def _json_log_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)


@app.middleware("http")
async def log_chat_raw_request(request: Request, call_next):
    if request.url.path == "/chat" and request.method == "POST":
        raw_body = await request.body()
        try:
            raw_value: Any = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raw_value = raw_body.decode("utf-8", errors="replace")
        logger.info("RAW_REQUEST=%s", _json_log_value(raw_value))
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def log_request_validation_error(request: Request, exc: RequestValidationError):
    if request.url.path == "/chat":
        logger.warning("VALIDATION_ERROR=%s", _json_log_value(exc.errors()))
    from fastapi.exception_handlers import request_validation_exception_handler

    return await request_validation_exception_handler(request, exc)


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
    return HealthResponse(
        status="ok",
        booking_provider=booking_provider_label(),
        version=API_VERSION,
    )


@app.post("/debug")
async def debug(payload: Any = Body(...)) -> dict[str, Any]:
    logger.info("DEBUG_REQUEST=%s", _json_log_value(payload))
    return {"received": payload}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    logger.info("PARSED_REQUEST=%s", _json_log_value(request.model_dump()))
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
    response = ChatResponse(
        response=result.response,
        next_agent=result.next_agent,
        escalation=result.escalation,
        handoff_summary=result.handoff_summary,
    )
    logger.info("CHAT_RESPONSE=%s", _json_log_value(response.model_dump()))
    return response


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
