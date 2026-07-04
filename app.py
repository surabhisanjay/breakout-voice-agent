from __future__ import annotations
import psutil
import json
import hmac
import asyncio
import urllib.request
import logging
import os
import re
import threading
import base64
import csv
import io
import time
import uuid
from argparse import Namespace
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from main import BASE_DIR, build_inbound_agent, dispatch
from src.config.env_loader import booking_provider_label
from src.agents.booking_agent import BookingAgent
from src.agents.inbound_agent import InboundAgent
from src.agents.learning_agent import LearningAgent
from src.memory.conversation_memory import ConversationMemory
from src.agents.sentiment_agent import SentimentAgent
from src.agents.escalation_agent import EscalationAgent
from src.agents.handoff_summary_agent import HandoffSummaryAgent
from src.agents.conversation_intelligence_agent import ConversationIntelligenceAgent


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
API_MEMORY_DIR = BASE_DIR / "memory" / "api_sessions"
VAPI_EVENT_LOG_DIR = BASE_DIR / "logs" / "vapi_events"
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
    sentiment_analysis: dict[str, Any] = Field(default_factory=dict)
    scoring: dict[str, Any] = Field(default_factory=dict)
    learning_metrics: dict[str, Any] = Field(default_factory=dict)
    metrics_report: dict[str, Any] = Field(default_factory=dict)
    handoff_summary: Optional[dict[str, Any]] = None
    call_intelligence: dict[str, Any] = Field(default_factory=dict)
    ai_summary: dict[str, Any] = Field(default_factory=dict)
    customer_profile: dict[str, Any] = Field(default_factory=dict)
    timeline_events: list[dict[str, Any]] = Field(default_factory=list)
    follow_up_recommendations: list[Any] = Field(default_factory=list)
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    recording: dict[str, Any] = Field(default_factory=dict)


class ResetRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=80)


class ResetResponse(BaseModel):
    success: bool


class MemoryResponse(BaseModel):
    session_id: str
    memory: dict[str, Any]


class MetricsResponse(BaseModel):
    session_id: str
    report: dict[str, Any]


class VapiToolResponse(BaseModel):
    results: list[dict[str, Any]]
    response: str = ""
    session_id: str = ""
    escalation: dict[str, Any] = Field(default_factory=dict)
    handoff_summary: Optional[dict[str, Any]] = None
    transfer: dict[str, Any] = Field(default_factory=dict)


class WhatsAppMessageRequest(BaseModel):
    phone: str = Field(..., min_length=10, max_length=20)
    message: str = Field(..., min_length=1, max_length=4096)
    message_id: str = Field(default="", max_length=160)
    contact_name: str = Field(default="", max_length=160)


class WhatsAppWebhookResponse(BaseModel):
    success: bool
    ignored: bool = False
    session_id: str = ""
    response: str = ""
    next_agent: str = "inbound_agent"
    wati: dict[str, Any] = Field(default_factory=dict)


def _openai_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(part.strip() for part in parts if part.strip()).strip()
    return ""


def _vapi_custom_llm_turn(payload: dict[str, Any]) -> tuple[str, str]:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="messages must be an array.")

    user_message = ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            user_message = _openai_message_text(message.get("content"))
            if user_message:
                break
    if not user_message:
        raise HTTPException(status_code=400, detail="A user message is required.")

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    call = payload.get("call") if isinstance(payload.get("call"), dict) else {}
    if not call and isinstance(metadata.get("call"), dict):
        call = metadata["call"]
    session_id = str(
        call.get("id")
        or payload.get("callId")
        or payload.get("call_id")
        or metadata.get("callId")
        or metadata.get("call_id")
        or payload.get("session_id")
        or payload.get("user")
        or "vapi-custom-llm"
    )
    return _validate_session_id(session_id), user_message


def _openai_completion_payload(response_text: str, model: str, completion_id: str) -> dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }
        ],
    }


def _openai_completion_stream(response_text: str, model: str, completion_id: str):
    created = int(time.time())
    chunks = (
        {"role": "assistant", "content": ""},
        {"content": response_text},
    )
    for delta in chunks:
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        yield f"data: {json.dumps(payload)}\n\n"
    final = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


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
    logger.info(
        "VAPI_CONFIG api_key=%s assistant_id=%s phone_number_id=%s webhook_token=%s environment=%s region=%s",
        bool(os.environ.get("VAPI_API_KEY")),
        bool(os.environ.get("VAPI_ASSISTANT_ID")),
        bool(os.environ.get("VAPI_PHONE_NUMBER_ID")),
        bool(os.environ.get("VAPI_WEBHOOK_TOKEN")),
        os.environ.get("VAPI_ENVIRONMENT", "unset"),
        os.environ.get("VAPI_REGION", "unset"),
    )
    logger.info("CHATREQUEST_SCHEMA=%s", json.dumps(ChatRequest.model_json_schema(), separators=(",", ":")))
    yield


app = FastAPI(title="Breakout Agent API", version=API_VERSION, lifespan=lifespan)


def _json_log_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)


@app.middleware("http")
async def log_chat_raw_request(request: Request, call_next):
    if request.url.path == "/chat" and request.method == "POST":
        raw_body = await request.body()

        async def receive_body_again() -> dict[str, Any]:
            return {"type": "http.request", "body": raw_body, "more_body": False}

        request._receive = receive_body_again
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _vapi_message(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    message = payload.get("message")
    return message if isinstance(message, dict) else payload


def _vapi_event_type(payload: Any, request: Request | None = None) -> str:
    message = _vapi_message(payload)
    raw = str(message.get("type") or "").strip().lower()
    if not raw and request is not None:
        raw = str(request.headers.get("x-webhook-event") or "").strip().lower()
    return raw


def _vapi_call(payload: Any) -> dict[str, Any]:
    message = _vapi_message(payload)
    call = message.get("call")
    if isinstance(call, dict):
        return call
    if isinstance(payload, dict) and isinstance(payload.get("call"), dict):
        return payload["call"]
    return {}


def _vapi_call_id(payload: Any) -> str:
    message = _vapi_message(payload)
    call = _vapi_call(payload)
    return str(
        call.get("id")
        or message.get("callId")
        or (payload.get("callId") if isinstance(payload, dict) else "")
        or ""
    ).strip()


def _vapi_status(event_type: str, payload: Any) -> str:
    message = _vapi_message(payload)
    call = _vapi_call(payload)
    status = str(message.get("status") or call.get("status") or "").strip().lower()
    aliases = {
        "call-started": "in-progress",
        "call.started": "in-progress",
        "call-ended": "ended",
        "call.ended": "ended",
        "call-failed": "ended",
        "call.failed": "ended",
        "end-of-call-report": "ended",
    }
    return aliases.get(event_type, status or "unknown")


def _verify_vapi_webhook(request: Request) -> None:
    expected = os.environ.get("VAPI_WEBHOOK_TOKEN", "").strip()
    if not expected:
        return
    header_name = os.environ.get("VAPI_WEBHOOK_AUTH_HEADER", "x-vapi-secret").strip()
    supplied = str(request.headers.get(header_name) or "").strip()
    authorization = str(request.headers.get("authorization") or "").strip()
    candidates = {supplied, authorization}
    if authorization.lower().startswith("bearer "):
        candidates.add(authorization[7:].strip())
    if not any(value and hmac.compare_digest(value, expected) for value in candidates):
        raise HTTPException(status_code=401, detail="Invalid Vapi webhook credential.")


def _append_vapi_event_log(event: dict[str, Any]) -> None:
    VAPI_EVENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = VAPI_EVENT_LOG_DIR / f"{datetime.now(timezone.utc).date().isoformat()}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=True, separators=(",", ":"), default=str) + "\n")


def _public_vapi_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in event.items()
        if key not in {"transcript", "raw_payload", "remote_ip"}
    }


def _persist_vapi_event(payload: Any, request: Request) -> dict[str, Any]:
    event_type = _vapi_event_type(payload, request)
    call_id = _vapi_call_id(payload)
    if not call_id:
        raise HTTPException(status_code=400, detail="Vapi event did not include call.id or callId.")
    call_id = _validate_session_id(call_id)
    message = _vapi_message(payload)
    call = _vapi_call(payload)
    now = _utc_now()
    status = _vapi_status(event_type, payload)
    assistant = message.get("assistant") if isinstance(message.get("assistant"), dict) else {}
    phone_number = message.get("phoneNumber") if isinstance(message.get("phoneNumber"), dict) else {}
    customer = call.get("customer") if isinstance(call.get("customer"), dict) else {}
    ended_reason = str(message.get("endedReason") or call.get("endedReason") or "")
    error_obj = message.get("error")
    error_text = ""
    if error_obj:
        error_text = json.dumps(error_obj, ensure_ascii=True, default=str) if isinstance(error_obj, (dict, list)) else str(error_obj)

    event_record = {
        "received_at": now,
        "type": event_type or "unknown",
        "call_id": call_id,
        "status": status,
        "assistant_id": str(call.get("assistantId") or assistant.get("id") or ""),
        "phone_number_id": str(call.get("phoneNumberId") or phone_number.get("id") or ""),
        "ended_reason": ended_reason,
        "remote_ip": request.client.host if request.client else "",
    }
    transcript = str(message.get("transcript") or "").strip()
    if transcript:
        event_record["role"] = str(message.get("role") or "")
        event_record["transcript_type"] = str(message.get("transcriptType") or "")
        event_record["transcript"] = transcript
    if error_text:
        event_record["error"] = error_text
    if os.environ.get("VAPI_LOG_RAW_PAYLOADS", "false").lower() == "true":
        event_record["raw_payload"] = payload

    with _lock:
        memory = ConversationMemory(_session_memory_path(call_id))
        memory.data["call_id"] = call_id
        if status != "unknown":
            memory.data["call_status"] = status
        memory.data["call_status_source"] = "webhook"
        memory.data["last_call_activity_at"] = now
        memory.data["vapi_event_count"] = int(memory.data.get("vapi_event_count", 0)) + 1
        memory.data["vapi_assistant_id"] = event_record["assistant_id"] or memory.data.get("vapi_assistant_id", "")
        memory.data["vapi_phone_number_id"] = event_record["phone_number_id"] or memory.data.get("vapi_phone_number_id", "")
        monitor = call.get("monitor") if isinstance(call.get("monitor"), dict) else {}
        memory.data["vapi_monitor_listen_url"] = str(monitor.get("listenUrl") or memory.data.get("vapi_monitor_listen_url", ""))
        memory.data["vapi_monitor_control_url"] = str(monitor.get("controlUrl") or memory.data.get("vapi_monitor_control_url", ""))
        memory.data["vapi_provider"] = str(call.get("phoneCallProvider") or call.get("type") or memory.data.get("vapi_provider", ""))
        memory.data["vapi_environment"] = os.environ.get("VAPI_ENVIRONMENT", memory.data.get("vapi_environment", ""))
        memory.data["vapi_region"] = os.environ.get("VAPI_REGION", memory.data.get("vapi_region", ""))
        caller_phone = _normalize_inbound_phone(customer.get("number"))
        if caller_phone and not memory.data.get("phone"):
            memory.data["phone"] = caller_phone
            memory.data["has_phone"] = True
            memory.data["phone_source"] = "vapi_caller_id"
        if status == "in-progress" and not memory.data.get("call_started_at"):
            memory.data["call_started_at"] = str(call.get("startedAt") or message.get("startedAt") or now)
        if status == "ended":
            memory.data["call_ended_at"] = str(call.get("endedAt") or message.get("endedAt") or now)
            memory.data["call_ended_reason"] = ended_reason or ("call-failed" if "failed" in event_type else "")
        events = memory.data.setdefault("vapi_events", [])
        events.append(event_record)
        memory.data["vapi_events"] = events[-100:]
        if transcript:
            transcripts = memory.data.setdefault("vapi_transcript_events", [])
            transcripts.append({
                "timestamp": now,
                "role": event_record.get("role", ""),
                "type": event_record.get("transcript_type", ""),
                "text": transcript,
            })
            memory.data["vapi_transcript_events"] = transcripts[-100:]
        if error_text or event_type in {"error", "call-failed", "call.failed"}:
            errors = memory.data.setdefault("vapi_errors", [])
            errors.append({"timestamp": now, "type": event_type, "error": error_text or ended_reason or "Unknown Vapi error"})
            memory.data["vapi_errors"] = errors[-30:]
        memory.save()
        runtime = _sessions.get(call_id)
        if runtime is not None:
            runtime.inbound.memory.data.update(memory.data)
        if status == "ended":
            _trigger_whatsapp_followup(memory)

        # Database and real-time updates for Vapi active calls pipeline
        try:
            from src.analytics.db import get_db_connection
            from src.analytics.websocket_server import ws_server
            
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM calls WHERE session_id = ?", (call_id,))
            exists = cursor.fetchone()[0] > 0
            
            if not exists:
                call_status = 'completed' if status == 'ended' else 'connected'
                ended_at = (call.get("endedAt") or message.get("endedAt") or now) if status == 'ended' else None
                dur = int(call.get("duration") or 0) if status == 'ended' else 0
                cursor.execute(
                    """
                    INSERT INTO calls (session_id, direction, status, started_at, ended_at, duration, latency, ttfr)
                    VALUES (?, 'inbound', ?, ?, ?, ?, 0.45, 1.2)
                    """,
                    (call_id, call_status, now, ended_at, dur)
                )
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO agent_performance (session_id, agent_id, team_id, call_date)
                    VALUES (?, 'inbound_agent', 'front_desk', ?)
                    """,
                    (call_id, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
                )
                conn.commit()
                # Broadcast real-time call event
                event_name = "call_ended" if status == 'ended' else "call_started"
                ws_server.broadcast(event_name, {"session_id": call_id, "status": call_status})
                
            else:
                if status == "ended":
                    ended_at = call.get("endedAt") or message.get("endedAt") or now
                    dur = int(call.get("duration") or 0)
                    cursor.execute(
                        """
                        UPDATE calls
                        SET status = 'completed', ended_at = ?, duration = ?, latency = ?, ttfr = ?
                        WHERE session_id = ?
                        """,
                        (ended_at, dur, 0.45, 1.2, call_id)
                    )
                    conn.commit()
                    # Broadcast call ended event
                    ws_server.broadcast("call_ended", {"session_id": call_id, "status": "completed"})
                else:
                    cursor.execute(
                        """
                        UPDATE calls
                        SET status = 'connected'
                        WHERE session_id = ?
                        """,
                        (call_id,)
                    )
                    conn.commit()
                    # Broadcast status update
                    ws_server.broadcast("call_updated", {"session_id": call_id, "status": "connected"})

            # --- conversation-update: extract new turns from the conversation array ---
            if event_type == "conversation-update":
                conv_turns = message.get("conversation") or []
                if not conv_turns and isinstance(payload, dict):
                    conv_turns = payload.get("conversation") or []
                memory_cvu = ConversationMemory(_session_memory_path(call_id))
                seen_count = int(memory_cvu.data.get("_conv_update_seen_turns", 0))
                new_turns = conv_turns[seen_count:]
                for turn in new_turns:
                    role = str(turn.get("role") or "").lower()
                    turn_text = str(turn.get("content") or turn.get("message") or "").strip()
                    if not turn_text:
                        continue
                    spk = "Customer" if role == "user" else "Agent"
                    cursor.execute(
                        "INSERT INTO transcripts (session_id, speaker, text) VALUES (?, ?, ?)",
                        (call_id, spk, turn_text)
                    )
                    conn.commit()
                    ws_server.broadcast("transcript_update", {"session_id": call_id, "speaker": spk, "text": turn_text})
                    # Run sentiment on customer turns
                    if spk == "Customer":
                        try:
                            _m = ConversationMemory(_session_memory_path(call_id))
                            _m.data.setdefault("conversation", []).append({"role": "customer", "content": turn_text})
                            _stage = _m.data.get("qualification_stage", "discovery")
                            _sent = SentimentAgent(_m).analyze(turn_text, stage=_stage)
                            _score = 1.0 if _sent.sentiment in ("excited", "satisfied", "positive") else -1.0 if _sent.sentiment in ("angry", "frustrated") else 0.0
                            cursor.execute(
                                "INSERT INTO sentiment_timeline (session_id, timestamp, score, label) VALUES (?, ?, ?, ?)",
                                (call_id, now, _score, _sent.sentiment)
                            )
                            conn.commit()
                            ws_server.broadcast("sentiment_update", {"session_id": call_id, "sentiment": _sent.sentiment, "score": _score})
                            _m.data["sentiment"] = _sent.sentiment
                            _m.data.setdefault("sentiment_history", []).append({"timestamp": now, "sentiment": _sent.sentiment, "confidence": _sent.confidence})
                            _m.save()
                        except Exception as _se:
                            logger.error("Sentiment on conversation-update failed: %s", _se)
                if new_turns:
                    memory_cvu.data["_conv_update_seen_turns"] = seen_count + len(new_turns)
                    memory_cvu.save()

            # Persist and broadcast transcript turn (from legacy message.transcript field)
            if transcript:
                speaker = "Customer" if event_record.get("role") == "user" else "Agent"
                cursor.execute(
                    """
                    INSERT INTO transcripts (session_id, speaker, text)
                    VALUES (?, ?, ?)
                    """,
                    (call_id, speaker, transcript)
                )
                conn.commit()
                # Broadcast transcript update
                ws_server.broadcast("transcript_update", {"session_id": call_id, "speaker": speaker, "text": transcript})

                # Real-time sentiment, handoff, and escalation evaluation
                try:
                    memory_path = _session_memory_path(call_id)
                    memory = ConversationMemory(memory_path)

                    if "conversation" not in memory.data:
                        memory.data["conversation"] = []

                    if speaker == "Customer":
                        memory.data["conversation"].append({"role": "customer", "content": transcript})
                        stage = memory.data.get("qualification_stage", "discovery")
                        sentiment = SentimentAgent(memory).analyze(transcript, stage=stage)

                        # Save sentiment points
                        if "sentiment_history" not in memory.data:
                            memory.data["sentiment_history"] = []
                        memory.data["sentiment_history"].append({
                            "timestamp": now,
                            "sentiment": sentiment.sentiment,
                            "confidence": sentiment.confidence,
                            "reason": sentiment.reason
                        })
                        memory.data["sentiment"] = sentiment.sentiment
                        memory.data["sentiment_confidence"] = sentiment.confidence
                        memory.data["sentiment_reason"] = sentiment.reason
                        memory.save()

                        # Write to database timeline
                        cursor.execute(
                            """
                            INSERT INTO sentiment_timeline (session_id, timestamp, score, label)
                            VALUES (?, ?, ?, ?)
                            """,
                            (
                                call_id,
                                now,
                                1.0 if sentiment.sentiment in ("excited", "satisfied", "positive") else -1.0 if sentiment.sentiment in ("angry", "frustrated") else 0.0,
                                sentiment.sentiment
                            )
                        )
                        conn.commit()
                        ws_server.broadcast("sentiment_update", {
                            "session_id": call_id,
                            "sentiment": sentiment.sentiment,
                            "score": 1.0 if sentiment.sentiment in ("excited", "satisfied", "positive") else -1.0 if sentiment.sentiment in ("angry", "frustrated") else 0.0
                        })

                        # Check escalations
                        esc = EscalationAgent(memory).evaluate(transcript, sentiment, None)
                        if esc.escalate:
                            _persist_classified_escalation(
                                call_id,
                                esc.to_dict(),
                                memory,
                                esc.summary,
                            )
                    else:
                        memory.data["conversation"].append({"role": "agent", "content": transcript})
                        memory.save()

                except Exception as eval_exc:
                    logger.error("Real-time transcript evaluation error: %s", eval_exc)

            conn.close()
        except Exception as db_exc:
            logger.error("Vapi analytics SQLite pipeline failure call_id=%s error=%s", call_id, db_exc)

    _append_vapi_event_log(event_record)
    logger.info(
        "VAPI_EVENT type=%s call_id=%s status=%s assistant_id=%s phone_number_id=%s ended_reason=%s remote_ip=%s",
        event_type,
        call_id,
        status,
        event_record["assistant_id"],
        event_record["phone_number_id"],
        ended_reason,
        event_record["remote_ip"],
    )
    return event_record


def _persist_vapi_tool_activity(session_id: str) -> None:
    now = _utc_now()
    with _lock:
        path = _session_memory_path(session_id)
        if not path.exists():
            return
        memory = ConversationMemory(path)
        if memory.data.get("call_status_source") != "webhook":
            memory.data["call_status"] = "in-progress"
            memory.data["call_status_source"] = "tool"
            if not memory.data.get("call_started_at"):
                memory.data["call_started_at"] = now
        memory.data["last_call_activity_at"] = now
        memory.save()


def _api_args() -> Namespace:
    no_openai = os.environ.get("NO_OPENAI", "false").lower() == "true"
    return Namespace(model=None, no_openai=no_openai)


def _initial_active_agent(memory: ConversationMemory) -> str:
    return "booking_agent" if memory.data.get("current_workflow") == "booking" else "inbound_agent"


def _get_runtime(session_id: str) -> SessionRuntime:
    clean = _validate_session_id(session_id)
    with _lock:
        existing = _sessions.get(clean)
        if existing is not None:
            return existing

        memory = ConversationMemory(_session_memory_path(clean))
        memory.data["call_id"] = clean
        memory.save()
        inbound = build_inbound_agent(_api_args(), memory)
        runtime = SessionRuntime(
            inbound=inbound,
            active_agent=_initial_active_agent(memory),
        )
        if runtime.active_agent == "booking_agent":
            runtime.booking = BookingAgent(memory)
        _sessions[clean] = runtime
        return runtime


async def _poll_payment_status_task(session_id: str, venue_id: str, booking_id: str):
    logger.info("Starting payment polling background task for session_id=%s, booking_id=%s", session_id, booking_id)
    max_active_polls = 12
    max_total_polls = 24
    
    for i in range(max_total_polls):
        wait_time = 15 if i < max_active_polls else 60
        await asyncio.sleep(wait_time)
        
        try:
            with _lock:
                memory = ConversationMemory(_session_memory_path(session_id))
                booking_status = memory.data.get("booking_status")
                call_id = memory.data.get("call_id")
                call_status = memory.data.get("call_status")
                
            if booking_status == "CONFIRMED":
                logger.info("Payment polling task: booking already CONFIRMED. Stopping.")
                break
                
            runtime = _get_runtime(session_id)
            if runtime.booking is None:
                from src.agents.booking_agent import BookingAgent
                runtime.booking = BookingAgent(runtime.inbound.memory)
                
            pay_status = runtime.booking.orchestrator.check_payment_status(venue_id, booking_id)
            is_paid = pay_status.get("isPaid", False)
            
            if is_paid:
                logger.info("Payment polling task: detected payment success for booking_id=%s", booking_id)
                with _lock:
                    memory = ConversationMemory(_session_memory_path(session_id))
                    memory.data["booking_status"] = "CONFIRMED"
                    memory.save()
                    if runtime is not None:
                        runtime.inbound.memory.data.update(memory.data)
                        
                try:
                    from src.analytics.db import get_db_connection
                    from src.analytics.websocket_server import ws_server
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE calls SET status = 'completed' WHERE session_id = ?",
                        (session_id,)
                    )
                    conn.commit()
                    ws_server.broadcast("booking_confirmed", {"session_id": session_id, "booking_id": booking_id})
                    ws_server.broadcast("call_updated", {"session_id": session_id, "status": "completed"})
                except Exception as dbe:
                    logger.error("Payment polling task: database update failed: %s", dbe)
                    
                vapi_api_key = os.environ.get("VAPI_API_KEY", "")
                if vapi_api_key and call_id and call_status in {"in-progress", "ringing", "forwarding"}:
                    logger.info("Payment polling task: sending Vapi speak request to active call_id=%s", call_id)
                    speak_url = f"https://api.vapi.ai/call/{call_id}/speak"
                    payload = json.dumps({
                        "message": "Got it. I see the payment went through and your booking is confirmed! You're all set."
                    }).encode("utf-8")
                    try:
                        req = urllib.request.Request(
                            speak_url,
                            data=payload,
                            headers={
                                "Authorization": f"Bearer {vapi_api_key}",
                                "Content-Type": "application/json"
                            },
                            method="POST"
                        )
                        with urllib.request.urlopen(req, timeout=5) as resp:
                            logger.info("Vapi speak response: status=%s", resp.status)
                    except Exception as ve:
                        logger.error("Payment polling task: failed to notify Vapi call: %s", ve)
                break
        except Exception as e:
            logger.error("Error in payment polling iteration %s: %s", i, e)

def _persist_classified_escalation(
    session_id: str,
    escalation: dict[str, Any],
    memory: ConversationMemory,
    handoff_summary: dict[str, Any] | str | None = None,
) -> bool:
    """Persist one escalation transition regardless of the inbound channel."""
    if not escalation.get("escalate"):
        return False

    ticket_id = str(
        escalation.get("support_ticket_id")
        or f"{session_id}:{escalation.get('trigger', 'manual_review')}"
    )
    transfer_status = str(escalation.get("transfer_status") or "pending_configuration")
    persistence_key = f"{ticket_id}:{transfer_status}"
    if memory.data.get("analytics_escalation_key") == persistence_key:
        return False

    from src.analytics.analytics_service import AnalyticsService
    from src.analytics.db import get_db_connection
    from src.analytics.websocket_server import ws_server

    reason = str(escalation.get("reason") or "Human review requested")
    priority = str(escalation.get("priority") or "medium")
    category = str(escalation.get("category") or escalation.get("trigger") or "support")
    now = _utc_now()
    AnalyticsService.ingest_event(
        session_id,
        "escalated",
        {
            "reason": reason,
            "priority": priority,
            "handoff_to": category,
            "support_ticket_id": ticket_id,
            "transfer_status": transfer_status,
        },
    )

    if isinstance(handoff_summary, dict):
        summary_text = str(
            handoff_summary.get("summary")
            or handoff_summary.get("ai_summary")
            or escalation.get("summary")
            or reason
        )
    else:
        summary_text = str(handoff_summary or escalation.get("summary") or reason)

    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO calls (session_id, direction, status, started_at)
            VALUES (?, 'inbound', 'escalated', ?)
            """,
            (session_id, now),
        )
        conn.execute("UPDATE calls SET status = 'escalated' WHERE session_id = ?", (session_id,))
        conn.execute(
            """
            INSERT OR REPLACE INTO notes (id, session_id, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (f"esc-note-{ticket_id}", session_id, f"Escalation summary: {summary_text}", now),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO activities (id, session_id, type, description, timestamp)
            VALUES (?, ?, 'escalation', ?, ?)
            """,
            (f"esc-activity-{ticket_id}", session_id, f"Escalation triggered: {reason}.", now),
        )
        conn.commit()

    memory.data["analytics_escalation_key"] = persistence_key
    memory.data["analytics_escalation_persisted_at"] = now
    memory.save()
    ws_server.broadcast(
        "escalated",
        {
            "session_id": session_id,
            "reason": reason,
            "priority": priority,
            "category": category,
            "support_ticket_id": ticket_id,
            "transfer_status": transfer_status,
        },
    )
    ws_server.broadcast(
        "call_updated",
        {"session_id": session_id, "status": "escalated"},
    )
    return True


def _dispatch_chat_message(session_id: str, message: str) -> AgentResponse:
    runtime = _get_runtime(session_id)
    with _lock:
        had_booking_id = runtime.inbound.memory.data.get("booking_id")
        was_pending = runtime.inbound.memory.data.get("booking_status") in {"PAYMENT_PENDING", "PENDING"}
        
        result, booking, active_agent = dispatch(
            message,
            runtime.inbound,
            runtime.booking,
            runtime.active_agent,
        )
        runtime.booking = booking
        runtime.active_agent = active_agent
        
        new_booking_id = runtime.inbound.memory.data.get("booking_id")
        is_pending = runtime.inbound.memory.data.get("booking_status") in {"PAYMENT_PENDING", "PENDING"}
        
        if new_booking_id and is_pending and (not had_booking_id or not was_pending):
            location_name = runtime.inbound.memory.data.get("location", "")
            venue_id = runtime.booking.orchestrator.get_venue_id_for_name(location_name)
            asyncio.create_task(_poll_payment_status_task(session_id, venue_id, new_booking_id))

        try:
            _persist_classified_escalation(
                _validate_session_id(session_id),
                result.escalation,
                runtime.inbound.memory,
                result.handoff_summary,
            )
        except Exception as exc:
            logger.error(
                "Escalation persistence failed session_id=%s error=%s",
                session_id,
                exc,
            )
            
    return result


_whatsapp_service_instance = None


def _get_whatsapp_service():
    global _whatsapp_service_instance
    if _whatsapp_service_instance is None:
        from src.channels.whatsapp import WhatsAppAgentService, WhatsAppEventStore

        _whatsapp_service_instance = WhatsAppAgentService(
            dispatcher=_dispatch_chat_message,
            event_store=WhatsAppEventStore(BASE_DIR / "memory" / "whatsapp_events"),
        )
    return _whatsapp_service_instance


def _verify_wati_webhook(request: Request) -> None:
    expected = os.environ.get("WHATSAPP_WEBHOOK_TOKEN", "").strip()
    if not expected:
        return
    supplied = str(
        request.headers.get("x-wati-secret")
        or request.headers.get("x-webhook-secret")
        or request.headers.get("authorization")
        or ""
    ).strip()
    supplied = re.sub(r"^Bearer\s+", "", supplied, flags=re.IGNORECASE)
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid WATI webhook credential.")


def _nested_get(data: Any, path: tuple[str, ...], default: Any = "") -> Any:
    current = data
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _extract_vapi_tool_payload(payload: Any) -> tuple[str, str, str]:
    if not isinstance(payload, dict):
        return "vapi-default", "", ""

    message_obj = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    top_level_message = payload.get("message") if isinstance(payload.get("message"), str) else ""
    tool_calls = (
        message_obj.get("toolCalls")
        or message_obj.get("tool_calls")
        or payload.get("toolCalls")
        or payload.get("tool_calls")
        or []
    )
    tool_call = tool_calls[0] if isinstance(tool_calls, list) and tool_calls else {}
    function_obj = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
    arguments = function_obj.get("arguments") or tool_call.get("arguments") or payload.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {"message": arguments}
    if not isinstance(arguments, dict):
        arguments = {}

    session_id = (
        arguments.get("session_id")
        or arguments.get("sessionId")
        or payload.get("session_id")
        or payload.get("sessionId")
        or _nested_get(message_obj, ("call", "id"))
        or _nested_get(payload, ("call", "id"))
        or message_obj.get("callId")
        or payload.get("callId")
        or "vapi-default"
    )
    user_message = (
        arguments.get("message")
        or arguments.get("query")
        or arguments.get("user_message")
        or arguments.get("userMessage")
        or top_level_message
        or message_obj.get("transcript")
        or message_obj.get("text")
        or payload.get("messageText")
        or payload.get("text")
        or ""
    )
    tool_call_id = str(tool_call.get("id") or payload.get("toolCallId") or "breakout-agent")
    return str(session_id), str(user_message), tool_call_id


def _normalize_inbound_phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if len(digits) == 10 and digits[0] in "6789" else ""


def _vapi_caller_phone(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    message = _vapi_message(payload)
    call = _vapi_call(payload)
    customer = call.get("customer") if isinstance(call.get("customer"), dict) else {}
    message_customer = message.get("customer") if isinstance(message.get("customer"), dict) else {}
    return _normalize_inbound_phone(
        customer.get("number")
        or message_customer.get("number")
        or payload.get("customerNumber")
    )


def _seed_vapi_caller_phone(session_id: str, payload: Any) -> str:
    phone = _vapi_caller_phone(payload)
    if not phone:
        return ""
    runtime = _get_runtime(session_id)
    with _lock:
        if not runtime.inbound.memory.data.get("phone"):
            runtime.inbound.memory.data["phone"] = phone
            runtime.inbound.memory.data["has_phone"] = True
            runtime.inbound.memory.data["phone_source"] = "vapi_caller_id"
            runtime.inbound.memory.save()
    return phone


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


@app.post("/vapi/webhook")
async def vapi_webhook(request: Request) -> dict[str, Any]:
    _verify_vapi_webhook(request)
    try:
        payload = await request.json()
    except Exception as exc:
        logger.warning("VAPI_WEBHOOK_INVALID_JSON remote_ip=%s error=%s", request.client.host if request.client else "", exc)
        raise HTTPException(status_code=400, detail="Webhook body must be valid JSON.") from exc

    event_type = _vapi_event_type(payload, request)
    call_id = _vapi_call_id(payload)
    logger.info(
        "VAPI_WEBHOOK_REQUEST type=%s call_id=%s content_length=%s",
        event_type or "unknown",
        call_id or "missing",
        request.headers.get("content-length", ""),
    )
    event = _persist_vapi_event(payload, request)

    if event_type == "assistant-request":
        assistant_id = os.environ.get("VAPI_ASSISTANT_ID", "").strip()
        if assistant_id:
            logger.info("VAPI_ASSISTANT_RESOLUTION call_id=%s source=environment", event["call_id"])
            return {"assistantId": assistant_id}
        logger.error("VAPI_ASSISTANT_RESOLUTION_FAILED call_id=%s VAPI_ASSISTANT_ID is empty", event["call_id"])
        return {"error": "No inbound assistant is configured for this phone number."}

    return {"received": True, "call_id": event["call_id"], "event_type": event["type"]}


@app.get("/vapi/events")
def vapi_events(limit: int = 50) -> dict[str, Any]:
    bounded = max(1, min(limit, 200))
    events: list[dict[str, Any]] = []
    if VAPI_EVENT_LOG_DIR.exists():
        for path in sorted(VAPI_EVENT_LOG_DIR.glob("*.jsonl"), reverse=True):
            for line in reversed(path.read_text(encoding="utf-8").splitlines()):
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if len(events) >= bounded:
                    break
            if len(events) >= bounded:
                break
    return {"count": len(events), "events": [_public_vapi_event(event) for event in events]}


@app.get("/vapi/calls/live")
def vapi_live_calls() -> dict[str, Any]:
    active_statuses = {"scheduled", "queued", "ringing", "in-progress", "forwarding"}
    calls: list[dict[str, Any]] = []
    API_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(API_MEMORY_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(data.get("call_status", "")).lower() not in active_statuses:
            continue
        calls.append({
            "call_id": data.get("call_id") or path.stem,
            "status": data.get("call_status"),
            "status_source": data.get("call_status_source"),
            "started_at": data.get("call_started_at"),
            "last_activity_at": data.get("last_call_activity_at"),
            "assistant_id": data.get("vapi_assistant_id"),
            "phone_number_id": data.get("vapi_phone_number_id"),
            "listen_websocket_available": bool(data.get("vapi_monitor_listen_url")),
            "control_url_available": bool(data.get("vapi_monitor_control_url")),
            "event_count": data.get("vapi_event_count", 0),
            "sentiment": {
                "sentiment": data.get("sentiment", "neutral"),
                "confidence": data.get("sentiment_confidence", 0.0),
                "reason": data.get("sentiment_reason", ""),
            }
        })
    return {"count": len(calls), "calls": calls}


@app.get("/vapi/diagnostics")
def vapi_diagnostics() -> dict[str, Any]:
    event_files = sorted(VAPI_EVENT_LOG_DIR.glob("*.jsonl"), reverse=True) if VAPI_EVENT_LOG_DIR.exists() else []
    latest_event: dict[str, Any] = {}
    if event_files:
        lines = event_files[0].read_text(encoding="utf-8").splitlines()
        if lines:
            try:
                latest_event = json.loads(lines[-1])
            except json.JSONDecodeError:
                latest_event = {}
    live = vapi_live_calls()
    return {
        "configuration": {
            "api_key_configured": bool(os.environ.get("VAPI_API_KEY")),
            "assistant_id_configured": bool(os.environ.get("VAPI_ASSISTANT_ID")),
            "phone_number_id_configured": bool(os.environ.get("VAPI_PHONE_NUMBER_ID")),
            "webhook_auth_configured": bool(os.environ.get("VAPI_WEBHOOK_TOKEN")),
            "environment": os.environ.get("VAPI_ENVIRONMENT", "unset"),
            "region": os.environ.get("VAPI_REGION", "unset"),
            "webhook_path": "/vapi/webhook",
            "tool_path": "/vapi/tool",
        },
        "live_call_count": live["count"],
        "latest_event": _public_vapi_event(latest_event),
    }


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    logger.info("PARSED_REQUEST=%s", _json_log_value(request.model_dump()))
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message cannot be empty.")

    result = _dispatch_chat_message(request.session_id, message)
    response = ChatResponse(
        response=result.response,
        next_agent=result.next_agent,
        escalation=result.escalation,
        sentiment_analysis=result.sentiment_analysis,
        scoring=result.scoring,
        learning_metrics=result.learning_metrics,
        metrics_report=result.metrics_report,
        handoff_summary=result.handoff_summary,
        call_intelligence=getattr(result, "call_intelligence", {}) or {},
        ai_summary=getattr(result, "ai_summary", {}) or {},
        customer_profile=getattr(result, "customer_profile", {}) or {},
        timeline_events=getattr(result, "timeline_events", []) or [],
        follow_up_recommendations=getattr(result, "follow_up_recommendations", []) or [],
        transcript=getattr(result, "transcript", []) or [],
        recording=getattr(result, "recording", {}) or {},
    )
    logger.info("CHAT_RESPONSE=%s", _json_log_value(response.model_dump()))
    return response


@app.post("/vapi/tool", response_model=VapiToolResponse)
async def vapi_tool(payload: Any = Body(...)) -> VapiToolResponse:
    logger.info("VAPI_TOOL_REQUEST=%s", _json_log_value(payload))
    session_id, message, tool_call_id = _extract_vapi_tool_payload(payload)
    _seed_vapi_caller_phone(session_id, payload)
    result = None
    if not message.strip():
        response_text = "Sorry, I didn't catch that. Could you repeat it?"
    else:
        result = _dispatch_chat_message(session_id, message.strip())
        response_text = result.response
        _persist_vapi_tool_activity(session_id)
    response = VapiToolResponse(
        results=[
            {
                "toolCallId": tool_call_id,
                "result": response_text,
            }
        ],
        response=response_text,
        session_id=session_id,
        escalation=result.escalation if result is not None else {},
        handoff_summary=result.handoff_summary if result is not None else None,
        transfer={
            "required": bool(result and result.escalation.get("transfer_required")),
            "status": result.escalation.get("transfer_status", "not_required") if result else "not_required",
            "support_ticket_id": result.escalation.get("support_ticket_id", "") if result else "",
        },
    )
    logger.info("VAPI_TOOL_RESPONSE=%s", _json_log_value(response.model_dump()))
    return response


@app.post("/vapi/llm/chat/completions")
async def vapi_custom_llm(payload: Any = Body(...)):
    """OpenAI-compatible adapter that makes the existing agent own every Vapi turn."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be an object.")
    session_id, message = _vapi_custom_llm_turn(payload)
    _seed_vapi_caller_phone(session_id, payload)
    logger.info("VAPI_CUSTOM_LLM_REQUEST session_id=%s message_length=%s", session_id, len(message))
    result = _dispatch_chat_message(session_id, message)
    _persist_vapi_tool_activity(session_id)
    model = str(payload.get("model") or "breakout-inbound-agent")
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    if payload.get("stream") is False:
        return _openai_completion_payload(result.response, model, completion_id)
    return StreamingResponse(
        _openai_completion_stream(result.response, model, completion_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get("/whatsapp/health")
def whatsapp_health() -> dict[str, Any]:
    service = _get_whatsapp_service()
    return {
        "status": "configured" if service.sender.configured else "not_configured",
        "provider": "wati",
        "enabled": os.environ.get("WHATSAPP_ENABLED", "false").lower() == "true",
        "webhook_auth_configured": bool(os.environ.get("WHATSAPP_WEBHOOK_TOKEN")),
    }


@app.post("/whatsapp/messages")
def whatsapp_message(
    request: WhatsAppMessageRequest,
    x_whatsapp_api_key: str = Header(default=""),
) -> dict[str, Any]:
    from src.channels.whatsapp import WhatsAppInboundMessage

    expected = os.environ.get("WHATSAPP_INTERNAL_API_KEY", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Direct WhatsApp messaging is not enabled.")
    if not hmac.compare_digest(x_whatsapp_api_key.strip(), expected):
        raise HTTPException(status_code=401, detail="Invalid WhatsApp API credential.")
    result = _get_whatsapp_service().handle(
        WhatsAppInboundMessage(
            phone=request.phone,
            text=request.message.strip(),
            message_id=request.message_id,
            contact_name=request.contact_name,
            event_type="direct",
        )
    )
    return result.to_dict()


@app.post("/wati/webhook")
async def wati_webhook_old(request: Request) -> dict[str, Any]:
    from src.channels.whatsapp import parse_wati_webhook

    _verify_wati_webhook(request)
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Webhook body must be valid JSON.") from exc
    inbound = parse_wati_webhook(payload)
    if inbound is None:
        return {"received": True, "ignored": True}
    result = _get_whatsapp_service().handle(inbound)
    logger.info(
        "WATI_INBOUND message_id=%s session_id=%s intent=%s duplicate=%s sent=%s",
        inbound.message_id,
        result.session_id,
        result.intent,
        result.duplicate,
        result.delivery.sent,
    )
    return {"received": True, **result.to_dict()}


def _first_text(value: Any, keys: set[str]) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in keys and item not in (None, ""):
                if isinstance(item, dict) and "body" in item:
                    return str(item["body"]).strip()
                if not isinstance(item, (dict, list)):
                    return str(item).strip()
            nested = _first_text(item, keys)
            if nested:
                return nested
    if isinstance(value, list):
        for item in value:
            nested = _first_text(item, keys)
            if nested:
                return nested
    return ""


def _extract_wati_message(payload: dict[str, Any]) -> tuple[str, str, bool]:
    event_type = str(
        payload.get("eventType")
        or payload.get("event")
        or payload.get("type")
        or ""
    ).lower()
    if event_type in {"message_status", "sentmessage", "templatemessage", "status"}:
        return "", "", True
    if payload.get("fromMe") is True or payload.get("isFromMe") is True:
        return "", "", True

    phone = _first_text(
        payload,
        {
            "waid", "whatsappnumber", "whatsapp_number", "sender", "from",
            "phone", "mobilenumber", "mobile", "contactnumber",
        },
    )
    message = ""
    for key in ("text", "body", "message", "messageText", "textMessage"):
        value = payload.get(key)
        if isinstance(value, dict):
            message = str(value.get("body") or value.get("text") or "").strip()
        elif value not in (None, ""):
            message = str(value).strip()
        if message:
            break
    if not message:
        message = _first_text(payload, {"body", "messagetext", "textmessage"})
    return phone, message, False


def _whatsapp_session_id(phone: str) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) == 10:
        digits = f"91{digits}"
    if not digits:
        raise HTTPException(status_code=400, detail="WhatsApp sender phone is required.")
    return _validate_session_id(f"whatsapp:{digits}")


@app.post("/webhooks/wati", response_model=WhatsAppWebhookResponse)
@app.post("/api/v1/whatsapp/wati/webhook", response_model=WhatsAppWebhookResponse)
async def wati_webhook(request: Request) -> WhatsAppWebhookResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="WATI webhook payload must be a JSON object.")
    logger.info("WATI_INBOUND_WEBHOOK=%s", _json_log_value(payload))

    phone, message, ignored = _extract_wati_message(payload)
    if ignored:
        return WhatsAppWebhookResponse(success=True, ignored=True)
    message = message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="WhatsApp message text is required.")

    session_id = _whatsapp_session_id(phone)
    runtime = _get_runtime(session_id)
    with _lock:
        runtime.inbound.memory.data["channel"] = "whatsapp"
        runtime.inbound.memory.data["whatsapp_number"] = "".join(ch for ch in str(phone) if ch.isdigit())
        runtime.inbound.memory.save()
        result, booking, active_agent = dispatch(
            message,
            runtime.inbound,
            runtime.booking,
            runtime.active_agent,
        )
        runtime.booking = booking
        runtime.active_agent = active_agent
        runtime.inbound.memory.data["channel"] = "whatsapp"
        runtime.inbound.memory.data["last_whatsapp_inbound"] = {
            "phone": runtime.inbound.memory.data.get("whatsapp_number", ""),
            "message": message,
            "received_at": _now_iso(),
        }
        runtime.inbound.memory.save()

    from src.services.wati_client import WatiClient
    wati_result = WatiClient().send_session_message(
        phone,
        result.response,
        context={
            "session_id": session_id,
            "next_agent": result.next_agent,
            "channel": "whatsapp",
        },
    )
    wati_delivery = wati_result.to_dict()
    with _lock:
        runtime.inbound.memory.data["last_whatsapp_reply"] = {
            "message": result.response,
            "delivery": wati_delivery,
            "sent_at": _now_iso(),
        }
        runtime.inbound.memory.save()

    response = WhatsAppWebhookResponse(
        success=True,
        session_id=session_id,
        response=result.response,
        next_agent=result.next_agent,
        wati=wati_delivery,
    )
    logger.info("WATI_WEBHOOK_RESPONSE=%s", _json_log_value(response.model_dump()))
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


@app.get("/metrics/{session_id}", response_model=MetricsResponse)
def metrics(session_id: str) -> MetricsResponse:
    runtime = _get_runtime(session_id)
    return MetricsResponse(
        session_id=_validate_session_id(session_id),
        report=LearningAgent(runtime.inbound.memory).build_report(),
    )

@app.api_route("/observability", methods=["GET", "POST"])
async def observability(request: Request):
    payload = None

    if request.method == "POST":
        try:
            payload = await request.json()
            logger.info(
                "OBSERVABILITY_EVENT=%s",
                _json_log_value(payload)
            )
        except Exception:
            logger.exception("Failed to parse observability payload")

    return {
        "status": "ok",
        "received": payload is not None
    }


@app.get("/intelligence/{session_id}")
def intelligence(session_id: str) -> dict[str, Any]:
    runtime = _get_runtime(session_id)
    data = runtime.inbound.memory.data
    payload = data.get("conversation_intelligence")
    if not isinstance(payload, dict) or not payload:
        payload = ConversationIntelligenceAgent().analyze(
            data,
            data.get("escalation_state", {}),
        )
        data["conversation_intelligence"] = payload
        runtime.inbound.memory.save()
    return {
        "session_id": _validate_session_id(session_id),
        **payload,
    }


# ---------------------------------------------------------------------------
# Closira CRM API contract (Mock implementation)
# ---------------------------------------------------------------------------

api_v1 = APIRouter(prefix="/api/v1")

LIST_QUERY_KEYS = {"page", "limit", "sort", "order"}
ANALYTICS_PERIODS = {"7d", "30d", "last_quarter", "this_year"}
MANAGER_ROLES = {"sales_manager", "admin"}


@dataclass(frozen=True)
class ApiUser:
    org_id: str
    user_id: int
    agent_id: int
    role: str
    name: str = ""


class ClosiraStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.orgs: dict[str, dict[str, Any]] = {}

    def org(self, org_id: str) -> dict[str, Any]:
        with self._lock:
            if org_id not in self.orgs:
                self.orgs[org_id] = self._seed_org(org_id)
            return self.orgs[org_id]

    @staticmethod
    def _seed_org(org_id: str) -> dict[str, Any]:
        now = _now_iso()
        return {
            "ids": {
                "contacts": 3,
                "leads": 3,
                "calls": 3,
                "escalations": 2,
                "bookings": 2,
                "agents": 4,
                "teams": 2,
                "notes": 2,
                "stages": 4,
                "lost_leads": 2,
                "followups": 2,
                "notifications": 3,
                "messages": 2,
            },
            "contacts": [
                {
                    "id": 1, "org_id": org_id, "first_name": "Priya", "last_name": "Mehta",
                    "name": "Priya Mehta", "phone": "9845012367", "email": "priya@example.com",
                    "type": "contact", "priority": "high", "stage": "qualified",
                    "assigned_to": 1, "source": "voice", "channel": "voice",
                    "value": 12000, "last_activity": now, "created_at": now,
                },
                {
                    "id": 2, "org_id": org_id, "first_name": "Siddharth", "last_name": "Khandelwal",
                    "name": "Siddharth Khandelwal", "phone": "9982151357", "email": "siddharth@example.com",
                    "type": "buyer", "priority": "medium", "stage": "new",
                    "assigned_to": 2, "source": "voice", "channel": "voice",
                    "value": 8000, "last_activity": now, "created_at": now,
                },
            ],
            "leads": [
                {
                    "id": 1, "org_id": org_id, "contact_id": 1, "first_name": "Priya", "last_name": "Mehta",
                    "phone": "9845012367", "location": "Whitefield", "event_type": "Escape Room",
                    "estimated_value": 12000, "party_size": 4, "lead_source": "voice",
                    "channel": "voice", "priority": "high", "event_date": now,
                    "pipeline_stage": "qualified", "stage_id": 2, "assigned_to": 1, "notes": "",
                    "created_at": now, "updated_at": now,
                },
                {
                    "id": 2, "org_id": org_id, "contact_id": 2, "first_name": "Siddharth", "last_name": "Khandelwal",
                    "phone": "9982151357", "location": "Koramangala", "event_type": "Escape Room",
                    "estimated_value": 8000, "party_size": 2, "lead_source": "voice",
                    "channel": "voice", "priority": "medium", "event_date": now,
                    "pipeline_stage": "new", "stage_id": 1, "assigned_to": 2, "notes": "",
                    "created_at": now, "updated_at": now,
                },
            ],
            "calls": [
                {
                    "id": 1, "org_id": org_id, "agent_id": 1, "assigned_to": 1, "contact_id": 1,
                    "status": "answered", "category": "booking", "is_ai": True,
                    "intent": "Booking", "summary": "Customer booked Murder Mystery.",
                    "conversation_summary": {"summary": "Customer booked Murder Mystery."},
                    "transcript": [
                        {"sequence": 1, "speaker_type": "customer", "text": "I want to book.", "spoken_at_second": 1.2},
                        {"sequence": 2, "speaker_type": "agent", "text": "I can help.", "spoken_at_second": 2.4},
                    ],
                    "recording_url": "", "sentiment": "positive", "started_at": now,
                    "occurred_at": now, "live_status": "Answered", "live_duration_seconds": 87,
                },
                {
                    "id": 2, "org_id": org_id, "agent_id": 2, "assigned_to": 2, "contact_id": 2,
                    "status": "answered", "category": "inquiry", "is_ai": True,
                    "intent": "Inquiry", "summary": "Customer compared rooms.",
                    "conversation_summary": {"summary": "Customer compared rooms."},
                    "transcript": [],
                    "recording_url": "", "sentiment": "neutral", "started_at": now,
                    "occurred_at": now, "live_status": "In Progress", "live_duration_seconds": 42,
                },
            ],
            "escalations": [
                {
                    "id": 1, "org_id": org_id, "call_id": 2, "contact_id": 2, "assigned_to": 2,
                    "escalation_status": "open", "target_group": "sales_manager",
                    "reason": "Customer requested human", "resolution_notes": "", "created_at": now,
                    "handoff_summary": {"summary": "Customer requested human", "action_items": ["Call customer"]},
                }
            ],
            "bookings": [
                {
                    "id": 1, "org_id": org_id, "contact_id": 1, "lead_id": 1, "assigned_to": 1,
                    "event_type": "Escape Room", "location": "Whitefield", "party_size": 4,
                    "event_date": now, "total_amount": 12000.0, "paid_amount": 0.0,
                    "payment_status": "PAYMENT_PENDING", "source": "voice", "channel": "voice",
                    "notes": "Payment link sent", "created_at": now,
                }
            ],
            "agents": [
                {"id": 1, "org_id": org_id, "name": "Priya Agent", "role": "sales_agent", "team_id": 1, "active": True},
                {"id": 2, "org_id": org_id, "name": "Siddharth Agent", "role": "sales_agent", "team_id": 1, "active": True},
                {"id": 3, "org_id": org_id, "name": "Manager", "role": "sales_manager", "team_id": 1, "active": True},
            ],
            "teams": [{"id": 1, "org_id": org_id, "name": "Closira Sales", "manager_id": 3, "created_at": now}],
            "stages": [
                {"id": 1, "org_id": org_id, "name": "new", "position": 1},
                {"id": 2, "org_id": org_id, "name": "qualified", "position": 2},
                {"id": 3, "org_id": org_id, "name": "booked", "position": 3},
            ],
            "lost_leads": [{"id": 1, "org_id": org_id, "lead_id": 2, "reason": "No response", "created_at": now}],
            "notes": [
                {"id": 1, "org_id": org_id, "contact_id": 1, "body": "Asked for Whitefield.", "created_by": 1, "created_at": now}
            ],
            "followups": [{"id": 1, "org_id": org_id, "lead_id": 1, "assigned_to": 1, "due_at": now, "status": "open"}],
            "conversations": [
                {"id": 1, "org_id": org_id, "contact_id": 1, "assigned_to": 1, "channel": "whatsapp", "messages": []}
            ],
            "message_templates": [{"id": 1, "org_id": org_id, "name": "Payment Link", "body": "Here is your payment link."}],
            "layouts": {},
            "notifications": [
                {"id": 1, "org_id": org_id, "user_id": 1, "title": "Follow up due", "read": False, "created_at": now},
                {"id": 2, "org_id": org_id, "user_id": 2, "title": "Escalation assigned", "read": False, "created_at": now},
            ],
        }

    def next_id(self, org: dict[str, Any], key: str) -> int:
        org["ids"][key] = int(org["ids"].get(key, 0)) + 1
        return org["ids"][key]


closira_store = ClosiraStore()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        raise HTTPException(status_code=401, detail="Invalid bearer token.")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token payload.") from exc


def _current_user(authorization: str = Header(default="")) -> ApiUser:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required.")
    payload = _decode_jwt_payload(authorization.split(" ", 1)[1].strip())
    org_id = str(payload.get("org_id") or "").strip()
    role = str(payload.get("role") or "").strip()
    if not org_id or role not in {"sales_agent", "sales_manager", "admin"}:
        raise HTTPException(status_code=403, detail="Token must include org_id and a supported role.")
    user_id = int(payload.get("sub") or payload.get("user_id") or payload.get("agent_id") or 0)
    agent_id = int(payload.get("agent_id") or user_id or 0)
    return ApiUser(org_id=org_id, user_id=user_id, agent_id=agent_id, role=role, name=str(payload.get("name") or ""))


def _require_manager(user: ApiUser) -> None:
    if user.role not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Manager or admin role required.")


def _require_admin_or_manager(user: ApiUser) -> None:
    _require_manager(user)


def _scoped(items: list[dict[str, Any]], user: ApiUser, mode: str = "assigned") -> list[dict[str, Any]]:
    if user.role in MANAGER_ROLES:
        return [item for item in items if str(item.get("org_id")) == user.org_id]
    scoped: list[dict[str, Any]] = []
    for item in items:
        if str(item.get("org_id")) != user.org_id:
            continue
        owner = item.get("assigned_to", item.get("agent_id", item.get("user_id")))
        if mode == "self":
            owner = item.get("id")
        if owner in (user.agent_id, user.user_id):
            scoped.append(item)
    return scoped


def _get_visible(items: list[dict[str, Any]], item_id: int, user: ApiUser, mode: str = "assigned") -> dict[str, Any]:
    for item in _scoped(items, user, mode=mode):
        if int(item.get("id", 0)) == int(item_id):
            return item
    raise HTTPException(status_code=404, detail="Resource not found.")


def _paginate(items: list[dict[str, Any]], request: Request, default_sort: str = "created_at") -> dict[str, Any]:
    query = request.query_params
    filtered = list(items)
    for key, value in query.multi_items():
        if key in LIST_QUERY_KEYS or key in {"from", "to", "period", "tab"}:
            continue
        if value == "":
            continue
        filtered = [item for item in filtered if str(item.get(key, "")).lower() == value.lower()]
    sort_key = query.get("sort") or default_sort
    reverse = query.get("order", "desc").lower() != "asc"
    filtered.sort(key=lambda item: str(item.get(sort_key, "")), reverse=reverse)
    page = max(int(query.get("page", 1)), 1)
    limit = max(min(int(query.get("limit", 25)), 100), 1)
    start = (page - 1) * limit
    return {"items": filtered[start:start + limit], "page": page, "limit": limit, "total": len(filtered)}


def _analytics_scope_user(request: Request, user: ApiUser) -> int | None:
    agent_id = request.query_params.get("agent_id")
    period = request.query_params.get("period")
    if period and period not in ANALYTICS_PERIODS:
        raise HTTPException(status_code=400, detail="Unsupported analytics period.")
    if user.role not in MANAGER_ROLES:
        return user.agent_id
    return int(agent_id) if agent_id else None


def _name_from_payload(payload: dict[str, Any]) -> str:
    name = payload.get("name")
    if name:
        return str(name)
    return " ".join(str(payload.get(key, "")).strip() for key in ("first_name", "last_name")).strip()


def _csv_response(rows: list[dict[str, Any]], filename: str) -> Response:
    output = io.StringIO()
    fieldnames = sorted({key for row in rows for key in row.keys()}) or ["empty"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fieldnames})
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api_v1.get("/me")
def api_me(user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    return {"id": user.user_id, "agent_id": user.agent_id, "org_id": user.org_id, "role": user.role, "name": user.name}


@api_v1.get("/me/layout/{screen}")
def get_layout(screen: str, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    org = closira_store.org(user.org_id)
    key = f"{user.user_id}:{screen}:{user.role}"
    return org["layouts"].get(key, {"screen": screen, "role": user.role, "layout": [], "widgets": []})


@api_v1.put("/me/layout/{screen}")
def put_layout(screen: str, payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    org = closira_store.org(user.org_id)
    key = f"{user.user_id}:{screen}:{payload.get('role') or user.role}"
    stored = {"screen": screen, "role": payload.get("role") or user.role, "layout": payload.get("layout", []), "widgets": payload.get("widgets", [])}
    org["layouts"][key] = stored
    return stored


@api_v1.get("/contacts")
def list_contacts(request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    return _paginate(_scoped(closira_store.org(user.org_id)["contacts"], user), request, "last_activity")


@api_v1.post("/contacts")
def create_contact(payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    org = closira_store.org(user.org_id)
    contact = {"id": closira_store.next_id(org, "contacts"), "org_id": user.org_id, "assigned_to": payload.get("assigned_to") or user.agent_id, "created_at": _now_iso(), "last_activity": _now_iso(), **payload}
    contact["name"] = _name_from_payload(contact)
    org["contacts"].append(contact)
    return contact


@api_v1.get("/contacts/{contact_id}")
def get_contact(contact_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    return _get_visible(closira_store.org(user.org_id)["contacts"], contact_id, user)


@api_v1.patch("/contacts/{contact_id}")
def patch_contact(contact_id: int, payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    contact = _get_visible(closira_store.org(user.org_id)["contacts"], contact_id, user)
    contact.update(payload)
    contact["name"] = _name_from_payload(contact)
    contact["last_activity"] = _now_iso()
    return contact


@api_v1.get("/contacts/{contact_id}/leads")
def contact_leads(contact_id: int, request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _get_visible(closira_store.org(user.org_id)["contacts"], contact_id, user)
    return _paginate([lead for lead in closira_store.org(user.org_id)["leads"] if lead.get("contact_id") == contact_id], request)


@api_v1.get("/contacts/{contact_id}/calls")
def contact_calls(contact_id: int, request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _get_visible(closira_store.org(user.org_id)["contacts"], contact_id, user)
    return _paginate([call for call in closira_store.org(user.org_id)["calls"] if call.get("contact_id") == contact_id], request, "occurred_at")


@api_v1.get("/contacts/{contact_id}/bookings")
def contact_bookings(contact_id: int, request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _get_visible(closira_store.org(user.org_id)["contacts"], contact_id, user)
    return _paginate([booking for booking in closira_store.org(user.org_id)["bookings"] if booking.get("contact_id") == contact_id], request)


@api_v1.get("/contacts/{contact_id}/activity")
def contact_activity(contact_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _get_visible(closira_store.org(user.org_id)["contacts"], contact_id, user)
    calls = [
        {
            "type": "call", "direction": "inbound", "call_status": call.get("status"),
            "sentiment": call.get("sentiment"), "intent": call.get("intent"),
            "summary": call.get("summary"), "occurred_at": call.get("occurred_at"),
        }
        for call in closira_store.org(user.org_id)["calls"] if call.get("contact_id") == contact_id
    ]
    return {"items": calls}


@api_v1.get("/contacts/{contact_id}/stats")
def contact_stats(contact_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _get_visible(closira_store.org(user.org_id)["contacts"], contact_id, user)
    org = closira_store.org(user.org_id)
    calls = [call for call in org["calls"] if call.get("contact_id") == contact_id]
    bookings = [booking for booking in org["bookings"] if booking.get("contact_id") == contact_id]
    spend = sum(float(booking.get("total_amount") or 0) for booking in bookings)
    return {"total_calls": len(calls), "total_bookings": len(bookings), "conversion_rate": (len(bookings) / len(calls)) if calls else 0.0, "lifetime_spend": spend}


@api_v1.get("/contacts/{contact_id}/notes")
def contact_notes(contact_id: int, request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _get_visible(closira_store.org(user.org_id)["contacts"], contact_id, user)
    return _paginate([note for note in closira_store.org(user.org_id)["notes"] if note.get("contact_id") == contact_id], request)


@api_v1.post("/contacts/{contact_id}/notes")
def create_contact_note(contact_id: int, payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _get_visible(closira_store.org(user.org_id)["contacts"], contact_id, user)
    org = closira_store.org(user.org_id)
    note = {"id": closira_store.next_id(org, "notes"), "org_id": user.org_id, "contact_id": contact_id, "created_by": user.user_id, "created_at": _now_iso(), **payload}
    org["notes"].append(note)
    return note


@api_v1.patch("/contacts/{contact_id}/notes/{note_id}")
def patch_contact_note(contact_id: int, note_id: int, payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _get_visible(closira_store.org(user.org_id)["contacts"], contact_id, user)
    note = next(
        (
            note
            for note in closira_store.org(user.org_id)["notes"]
            if note.get("contact_id") == contact_id
            and note.get("id") == note_id
            and str(note.get("org_id")) == user.org_id
        ),
        None,
    )
    if note is None:
        raise HTTPException(status_code=404, detail="Resource not found.")
    note.update(payload)
    return note


@api_v1.delete("/contacts/{contact_id}/notes/{note_id}")
def delete_contact_note(contact_id: int, note_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _get_visible(closira_store.org(user.org_id)["contacts"], contact_id, user)
    org = closira_store.org(user.org_id)
    before = len(org["notes"])
    org["notes"] = [note for note in org["notes"] if not (note.get("id") == note_id and note.get("contact_id") == contact_id)]
    if len(org["notes"]) == before:
        raise HTTPException(status_code=404, detail="Resource not found.")
    return {"deleted": True}


@api_v1.get("/leads/lost")
def list_lost_leads(request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    return _paginate(closira_store.org(user.org_id)["lost_leads"], request)


@api_v1.post("/leads/lost")
def create_lost_lead(payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    org = closira_store.org(user.org_id)
    lost = {"id": closira_store.next_id(org, "lost_leads"), "org_id": user.org_id, "created_at": _now_iso(), **payload}
    org["lost_leads"].append(lost)
    return lost


@api_v1.delete("/leads/lost/{lost_id}")
def delete_lost_lead(lost_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    org = closira_store.org(user.org_id)
    org["lost_leads"] = [item for item in org["lost_leads"] if item.get("id") != lost_id]
    return {"deleted": True}


@api_v1.get("/leads")
def list_leads(request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    return _paginate(_scoped(closira_store.org(user.org_id)["leads"], user), request)


@api_v1.post("/leads")
def create_lead(payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    org = closira_store.org(user.org_id)
    lead = {"id": closira_store.next_id(org, "leads"), "org_id": user.org_id, "assigned_to": payload.get("assigned_to") or user.agent_id, "created_at": _now_iso(), "updated_at": _now_iso(), **payload}
    org["leads"].append(lead)
    return lead


@api_v1.get("/leads/{lead_id}")
def get_lead(lead_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    return _get_visible(closira_store.org(user.org_id)["leads"], lead_id, user)


@api_v1.patch("/leads/{lead_id}")
def patch_lead(lead_id: int, payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    lead = _get_visible(closira_store.org(user.org_id)["leads"], lead_id, user)
    allowed = {"first_name", "last_name", "email", "phone", "company_name", "location", "event_type", "estimated_value", "party_size", "lead_source", "channel", "priority", "event_date", "pipeline_stage", "notes", "assigned_to"}
    lead.update({key: value for key, value in payload.items() if key in allowed})
    lead["updated_at"] = _now_iso()
    return lead


@api_v1.patch("/leads/{lead_id}/stage")
def patch_lead_stage(lead_id: int, payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    lead = _get_visible(closira_store.org(user.org_id)["leads"], lead_id, user)
    lead["pipeline_stage"] = payload.get("pipeline_stage") or payload.get("stage") or lead.get("pipeline_stage")
    if payload.get("stage_id"):
        lead["stage_id"] = payload["stage_id"]
    lead["updated_at"] = _now_iso()
    return lead


@api_v1.get("/calls/live")
def live_calls(user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    org = closira_store.org(user.org_id)
    contacts = {contact["id"]: contact for contact in org["contacts"]}
    agents = {agent["id"]: agent for agent in org["agents"]}
    items = [
        {
            "id": call["id"],
            "agent": {"id": call.get("agent_id"), "name": agents.get(call.get("agent_id"), {}).get("name", "")},
            "contact": {"id": call.get("contact_id"), "name": contacts.get(call.get("contact_id"), {}).get("name", "")},
            "live_status": call.get("live_status", "Answered"),
            "intent": call.get("intent"),
            "live_duration_seconds": call.get("live_duration_seconds", 0),
            "started_at": call.get("started_at"),
        }
        for call in org["calls"] if call.get("live_status") in {"Answered", "In Progress"}
    ]
    return {"items": items}


@api_v1.get("/calls")
def list_calls(request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    return _paginate(_scoped(closira_store.org(user.org_id)["calls"], user, mode="own"), request, "occurred_at")


@api_v1.get("/calls/{call_id}")
def get_call(call_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    return _get_visible(closira_store.org(user.org_id)["calls"], call_id, user)


@api_v1.get("/calls/{call_id}/summary")
def call_summary(call_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    call = _get_visible(closira_store.org(user.org_id)["calls"], call_id, user)
    return {"call_id": call_id, "summary": call.get("conversation_summary") or {"summary": call.get("summary", "")}}


@api_v1.get("/calls/{call_id}/transcript")
def call_transcript(call_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    call = _get_visible(closira_store.org(user.org_id)["calls"], call_id, user)
    return {"call_id": call_id, "lines": call.get("transcript", [])}


@api_v1.get("/calls/{call_id}/transcript/live")
def call_live_transcript(call_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    call = _get_visible(closira_store.org(user.org_id)["calls"], call_id, user)
    return {"call_id": call_id, "lines": call.get("transcript", [])}


@api_v1.get("/calls/{call_id}/recording")
def call_recording(call_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    call = _get_visible(closira_store.org(user.org_id)["calls"], call_id, user)
    return {"call_id": call_id, "recording_url": call.get("recording_url", ""), "available": bool(call.get("recording_url"))}


@api_v1.post("/calls/{call_id}/csat")
def call_csat(call_id: int, payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    call = _get_visible(closira_store.org(user.org_id)["calls"], call_id, user)
    call["csat"] = payload
    return {"saved": True, "call_id": call_id}


@api_v1.get("/escalations")
def list_escalations(request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    return _paginate(_scoped(closira_store.org(user.org_id)["escalations"], user, mode="own"), request)


@api_v1.get("/escalations/{escalation_id}")
def get_escalation(escalation_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    return _get_visible(closira_store.org(user.org_id)["escalations"], escalation_id, user)


@api_v1.patch("/escalations/{escalation_id}")
def patch_escalation(escalation_id: int, payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    escalation = _get_visible(closira_store.org(user.org_id)["escalations"], escalation_id, user)
    allowed = {"escalation_status", "assigned_to", "target_group", "resolution_notes"}
    escalation.update({key: value for key, value in payload.items() if key in allowed})
    return escalation


@api_v1.get("/pipeline/stages")
def list_stages(request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    return _paginate(closira_store.org(user.org_id)["stages"], request, "position")


@api_v1.post("/pipeline/stages")
def create_stage(payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    org = closira_store.org(user.org_id)
    stage = {"id": closira_store.next_id(org, "stages"), "org_id": user.org_id, "position": len(org["stages"]) + 1, **payload}
    org["stages"].append(stage)
    return stage


@api_v1.patch("/pipeline/stages/reorder")
def reorder_stages(payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    order = payload.get("order") or payload.get("stage_ids") or []
    positions = {int(stage_id): idx + 1 for idx, stage_id in enumerate(order)}
    for stage in closira_store.org(user.org_id)["stages"]:
        if stage["id"] in positions:
            stage["position"] = positions[stage["id"]]
    return {"items": sorted(closira_store.org(user.org_id)["stages"], key=lambda stage: stage["position"])}


@api_v1.patch("/pipeline/stages/{stage_id}")
def patch_stage(stage_id: int, payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    stage = _get_visible(closira_store.org(user.org_id)["stages"], stage_id, user)
    stage.update(payload)
    return stage


@api_v1.delete("/pipeline/stages/{stage_id}")
def delete_stage(stage_id: int, payload: dict[str, Any] = Body(...), user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    target_stage_id = int(payload.get("target_stage_id") or 0)
    if not target_stage_id or target_stage_id == stage_id:
        raise HTTPException(status_code=400, detail="target_stage_id is required.")
    org = closira_store.org(user.org_id)
    if not any(stage["id"] == target_stage_id for stage in org["stages"]):
        raise HTTPException(status_code=404, detail="Target stage not found.")
    if not any(stage["id"] == stage_id for stage in org["stages"]):
        raise HTTPException(status_code=404, detail="Source stage not found.")
    for lead in org["leads"]:
        if lead.get("stage_id") == stage_id:
            lead["stage_id"] = target_stage_id
    org["stages"] = [stage for stage in org["stages"] if stage["id"] != stage_id]
    return {"deleted": True, "migrated_to": target_stage_id}


@api_v1.get("/bookings")
def list_bookings(request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    rows = [dict(booking, balance_amount=float(booking.get("total_amount") or 0) - float(booking.get("paid_amount") or 0)) for booking in _scoped(closira_store.org(user.org_id)["bookings"], user, mode="own")]
    return _paginate(rows, request)


@api_v1.get("/bookings/{booking_id}")
def get_booking(booking_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    booking = dict(_get_visible(closira_store.org(user.org_id)["bookings"], booking_id, user))
    booking["balance_amount"] = float(booking.get("total_amount") or 0) - float(booking.get("paid_amount") or 0)
    return booking


@api_v1.post("/bookings")
def create_booking(payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    org = closira_store.org(user.org_id)
    with closira_store._lock:
        contact_id = payload.get("contact_id")
        if not contact_id:
            contact_payload = {
                "first_name": payload.get("first_name", ""),
                "last_name": payload.get("last_name", ""),
                "name": payload.get("customer_name") or "Guest",
                "phone": payload.get("phone", ""),
                "email": payload.get("email", ""),
                "type": "buyer",
                "priority": "medium",
                "stage": "booked",
                "source": payload.get("source", "crm"),
                "channel": payload.get("channel", "crm"),
                "assigned_to": payload.get("assigned_to") or user.agent_id,
                "org_id": user.org_id,
                "created_at": _now_iso(),
                "last_activity": _now_iso(),
            }
            contact_payload["id"] = closira_store.next_id(org, "contacts")
            if contact_payload["name"] == "Guest":
                contact_payload["name"] = _name_from_payload(contact_payload) or "Guest"
            org["contacts"].append(contact_payload)
            contact_id = contact_payload["id"]
        booking = {"id": closira_store.next_id(org, "bookings"), "org_id": user.org_id, "contact_id": int(contact_id), "assigned_to": payload.get("assigned_to") or user.agent_id, "created_at": _now_iso(), **payload}
        booking["contact_id"] = int(contact_id)
        booking["balance_amount"] = float(booking.get("total_amount") or 0) - float(booking.get("paid_amount") or 0)
        org["bookings"].append(booking)
        return booking


@api_v1.patch("/bookings/{booking_id}")
def patch_booking(booking_id: int, payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    booking = _get_visible(closira_store.org(user.org_id)["bookings"], booking_id, user)
    booking.update(payload)
    booking["balance_amount"] = float(booking.get("total_amount") or 0) - float(booking.get("paid_amount") or 0)
    return booking


@api_v1.get("/agents/assignable")
def assignable_agents(user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    return {"items": [agent for agent in closira_store.org(user.org_id)["agents"] if agent.get("active")]}


@api_v1.get("/agents")
def list_agents(request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    return _paginate(closira_store.org(user.org_id)["agents"], request, "name")


@api_v1.post("/agents")
def create_agent(payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    org = closira_store.org(user.org_id)
    agent = {"id": closira_store.next_id(org, "agents"), "org_id": user.org_id, "active": True, **payload}
    org["agents"].append(agent)
    return agent


@api_v1.get("/agents/{agent_id}")
def get_agent(agent_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    if user.role == "sales_agent" and agent_id not in {user.agent_id, user.user_id}:
        raise HTTPException(status_code=403, detail="Self access only.")
    return _get_visible(closira_store.org(user.org_id)["agents"], agent_id, user, mode="self")


@api_v1.patch("/agents/{agent_id}")
def patch_agent(agent_id: int, payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    agent = _get_visible(closira_store.org(user.org_id)["agents"], agent_id, user, mode="self")
    agent.update(payload)
    return agent


@api_v1.get("/teams")
def list_teams(request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    return _paginate(closira_store.org(user.org_id)["teams"], request, "name")


@api_v1.post("/teams")
def create_team(payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    org = closira_store.org(user.org_id)
    team = {"id": closira_store.next_id(org, "teams"), "org_id": user.org_id, "created_at": _now_iso(), **payload}
    org["teams"].append(team)
    return team


@api_v1.get("/teams/{team_id}")
def get_team(team_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    return _get_visible(closira_store.org(user.org_id)["teams"], team_id, user)


@api_v1.patch("/teams/{team_id}")
def patch_team(team_id: int, payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    team = _get_visible(closira_store.org(user.org_id)["teams"], team_id, user)
    team.update(payload)
    return team


def _agent_dashboard() -> dict[str, Any]:
    return {
        "calls_today": {"current": 21, "target": 25},
        "conversion_rate": {"current": 0.42, "target": 0.50},
        "pipeline_deals": {"current": 8, "target": 10},
        "rank": {"position": 2, "total_agents": 8},
        "performance_score": 87.5,
        "targets": [{"metric": "calls_per_day", "current": 21, "target": 25}],
    }


def _manager_dashboard() -> dict[str, Any]:
    return {
        "booking_rate_by_room": [{"room": "Murder Mystery", "rate": 0.45}],
        "talk_listen_ratio": {"talk": 0.55, "listen": 0.45},
        "customer_satisfaction": {"very_satisfied": 32, "satisfied": 28, "somewhat_satisfied": 15, "neutral": 10, "dissatisfied": 5},
        "channel_distribution": {"voice": 0.6, "whatsapp": 0.25, "email": 0.15},
        "escalation_tickets": {"resolved": 18, "unresolved": 7, "by_category": [{"category": "Billing", "count": 10}]},
        "pipeline_conversion_rate": 0.38,
    }


@api_v1.get("/analytics/dashboard")
def analytics_dashboard(request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _analytics_scope_user(request, user)
    if user.role == "sales_agent":
        return _agent_dashboard()
    payload = _manager_dashboard()
    if user.role == "admin":
        payload.update({
            "revenue_this_month": 820000,
            "ai_projected_revenue": 82000,
            "annual_revenue_target": 5000000,
            "monthly_revenue_target": 416667,
            "ai_revenue_potential": 95000,
            "top_performers": [{"agent_id": 3, "name": "Krati Surana", "revenue": 120000}],
            "pipeline_ai_conversion_projection": {"rate": 0.28, "by_quarter": "Q3 2025"},
        })
    return payload


@api_v1.get("/analytics/information")
def analytics_information(request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    agent_scope = _analytics_scope_user(request, user)
    return {"scope_agent_id": agent_scope, "summary": "Conversation, booking, and lead analytics are available."}


@api_v1.get("/analytics/live")
def analytics_live(user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    return {"active_calls": len([call for call in closira_store.org(user.org_id)["calls"] if call.get("live_status") == "In Progress"])}


@api_v1.get("/analytics/data")
def analytics_data(request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    agent_scope = _analytics_scope_user(request, user)
    calls = closira_store.org(user.org_id)["calls"]
    if agent_scope:
        calls = [call for call in calls if call.get("agent_id") == agent_scope]
    tab = request.query_params.get("tab", "all")
    return {"tab": tab, "items": calls}


@api_v1.get("/analytics/team-performance")
def analytics_team_performance(request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    _analytics_scope_user(request, user)
    return {"items": [{"team_id": 1, "conversion_rate": 0.38, "calls": 92}]}


@api_v1.get("/analytics/agent/{agent_id}")
def analytics_agent(agent_id: int, request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    if user.role == "sales_agent" and agent_id not in {user.agent_id, user.user_id}:
        raise HTTPException(status_code=403, detail="Self access only.")
    _analytics_scope_user(request, user)
    return {
        "lead_conversion": [{"stage": "new", "value": 12, "goal": 15}],
        "communication_stats": {"daily_avg": 18, "last_week": 92, "last_24h": 21, "abandon_rate": 0.05, "abandon_rate_change": -0.01},
        "calls_chart": [{"category": "resolved", "count": 74}],
        "targets": [{"channel": "voice", "target": 100, "performance": 87}],
        "lead_source_distribution": {"Instagram": 0.4, "Google": 0.3, "Referral": 0.3},
        "revenue_trend": [{"month": "2025-05", "actual": 80000, "projected": 95000}],
    }


@api_v1.get("/analytics/team/{team_id}")
def analytics_team(team_id: int, request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    _require_manager(user)
    _analytics_scope_user(request, user)
    return {"team_id": team_id, "lead_conversion": 0.38, "revenue": 240000, "calls": 160}


@api_v1.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    return _get_visible(closira_store.org(user.org_id)["conversations"], conversation_id, user)


@api_v1.post("/conversations/{conversation_id}/messages")
def post_conversation_message(conversation_id: int, payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    conversation = _get_visible(closira_store.org(user.org_id)["conversations"], conversation_id, user)
    message = {"id": closira_store.next_id(closira_store.org(user.org_id), "messages"), "sent_by": user.user_id, "sent_at": _now_iso(), **payload}
    conversation.setdefault("messages", []).append(message)
    return message


@api_v1.get("/message-templates")
def message_templates(user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    return {"items": closira_store.org(user.org_id)["message_templates"]}


@api_v1.post("/follow-ups")
def create_followup(payload: dict[str, Any], user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    org = closira_store.org(user.org_id)
    if user.role == "sales_agent" and payload.get("lead_id"):
        _get_visible(org["leads"], int(payload["lead_id"]), user)
    followup = {"id": closira_store.next_id(org, "followups"), "org_id": user.org_id, "assigned_to": payload.get("assigned_to") or user.agent_id, "created_at": _now_iso(), **payload}
    org["followups"].append(followup)
    return followup


@api_v1.get("/follow-ups")
def list_followups(request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    return _paginate(_scoped(closira_store.org(user.org_id)["followups"], user, mode="own"), request)


@api_v1.get("/search")
def search(q: str = "", type: str = "all", user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    org = closira_store.org(user.org_id)
    collections = ["contacts", "leads", "calls", "bookings"] if type == "all" else [type]
    results: list[dict[str, Any]] = []
    for collection in collections:
        for item in _scoped(org.get(collection, []), user, mode="own" if collection in {"calls", "bookings"} else "assigned"):
            haystack = json.dumps(item, default=str).lower()
            if not q or q.lower() in haystack:
                results.append({"type": collection.rstrip("s"), "item": item})
    return {"items": results}


@api_v1.get("/notifications")
def notifications(request: Request, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    rows = [note for note in closira_store.org(user.org_id)["notifications"] if note.get("user_id") in {user.user_id, user.agent_id}]
    return _paginate(rows, request)


@api_v1.patch("/notifications/{notification_id}/read")
def read_notification(notification_id: int, user: ApiUser = Depends(_current_user)) -> dict[str, Any]:
    note = _get_visible(closira_store.org(user.org_id)["notifications"], notification_id, user, mode="own")
    note["read"] = True
    return note


@api_v1.get("/export/lost-leads")
def export_lost_leads(user: ApiUser = Depends(_current_user)) -> Response:
    _require_manager(user)
    return _csv_response(closira_store.org(user.org_id)["lost_leads"], "lost-leads.csv")


@api_v1.get("/export/calls")
def export_calls(user: ApiUser = Depends(_current_user)) -> Response:
    return _csv_response(_scoped(closira_store.org(user.org_id)["calls"], user, mode="own"), "calls.csv")


@api_v1.get("/export/contacts")
def export_contacts(user: ApiUser = Depends(_current_user)) -> Response:
    return _csv_response(_scoped(closira_store.org(user.org_id)["contacts"], user), "contacts.csv")


def format_whatsapp_followup(memory_data: dict[str, Any]) -> str:
    name = memory_data.get("customer_name") or "there"
    is_escalated = bool(memory_data.get("escalation_state", {}).get("escalate"))
    
    if is_escalated:
        msg = f"Hi {name},\n\nThanks for calling Breakout! We'll connect you with someone shortly.\n\nHere is a summary of your inquiry:\n"
    else:
        msg = f"Hi {name},\n\nThanks for calling Breakout! 🌟\n\nHere is a summary of your inquiry:\n"
    
    room = memory_data.get("room") or memory_data.get("recommended_option")
    if room:
         msg += f"- *Theme/Room*: {room}\n"
    
    loc = memory_data.get("location")
    if loc:
         msg += f"- *Location*: {loc}\n"
         
    players = memory_data.get("participants") or memory_data.get("company_size")
    if players:
         msg += f"- *Players*: {players}\n"
         
    date = memory_data.get("preferred_date")
    if date:
         msg += f"- *Preferred Date*: {date}\n"
    time_pref = memory_data.get("preferred_time") or memory_data.get("preferred_period")
    if time_pref:
         msg += f"- *Time*: {time_pref}\n"
         
    booking_id = memory_data.get("booking_id")
    if booking_id:
         payment_link = memory_data.get("payment_link")
         if payment_link:
             msg += f"- *Payment Link*: {payment_link}\n"
         
    msg += "\nWe look forward to hosting your escape room adventure! If you have any questions, feel free to reply to this chat.\n\nWarm regards,\nBreakout Escape Rooms Team"
    return msg


def _trigger_whatsapp_followup(memory: Any) -> None:
    memory_data = memory.data
    if memory_data.get("whatsapp_followup_sent"):
        logger.info("WhatsApp follow-up already sent for this session.")
        return
        
    if memory_data.get("whatsapp_followup_consent") is True:
        whatsapp_phone = memory_data.get("whatsapp_followup_phone") or memory_data.get("phone")
        if not whatsapp_phone or whatsapp_phone == "current_number":
            whatsapp_phone = "8217008407"
            logger.warning("WhatsApp follow-up consent granted but no phone number found.")
            return
            
        from src.services.wati_client import WatiClient
        client = WatiClient.from_env()
        if not client or not client.configured():
            logger.warning("WhatsApp follow-up skipped: Wati client is not configured in environment.")
            return
            
        content = format_whatsapp_followup(memory_data)
        try:
            delivery = client.send_session_message(whatsapp_phone, content)
            if delivery.sent:
                logger.info("WhatsApp follow-up successfully sent to %s", whatsapp_phone)
                memory_data["whatsapp_followup_sent"] = True
                memory.save()
            else:
                logger.warning("WhatsApp follow-up failed for %s. Reason: %s, Response: %s", whatsapp_phone, delivery.reason, delivery.response)
        except Exception as e:
            logger.error("Failed to send WhatsApp follow-up message to %s: %s", whatsapp_phone, e)


app.include_router(api_v1)
