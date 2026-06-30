from __future__ import annotations
import psutil
import json
import hmac
import logging
import os
import re
import threading
from argparse import Namespace
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
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
        if customer.get("number") and not memory.data.get("phone"):
            memory.data["phone"] = str(customer["number"])
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
                            cursor.execute(
                                """
                                INSERT OR REPLACE INTO escalations (session_id, timestamp, reason, priority, status, handoff_to)
                                VALUES (?, ?, ?, ?, 'pending', ?)
                                """,
                                (call_id, now, esc.reason, esc.priority, esc.category or "support")
                            )
                            cursor.execute(
                                """
                                UPDATE calls
                                SET status = 'escalated'
                                WHERE session_id = ?
                                """,
                                (call_id,)
                            )
                            conn.commit()

                            summary_text = esc.summary or HandoffSummaryAgent().generate_summary(memory)
                            memory.data["handoff_summary"] = summary_text
                            memory.save()

                            # Add notes
                            cursor.execute(
                                """
                                INSERT INTO notes (id, session_id, content, created_at)
                                VALUES (?, ?, ?, ?)
                                """,
                                (f"note_{int(datetime.now(timezone.utc).timestamp())}", call_id, f"Escalation summary: {summary_text}", now)
                            )
                            # Add activity
                            cursor.execute(
                                """
                                INSERT INTO activities (id, session_id, type, description, timestamp)
                                VALUES (?, ?, 'escalation', ?, ?)
                                """,
                                (f"act_{int(datetime.now(timezone.utc).timestamp())}", call_id, f"Escalation triggered: {esc.reason}.", now)
                            )
                            conn.commit()

                            ws_server.broadcast("escalated", {"session_id": call_id, "reason": esc.reason, "priority": esc.priority})
                            ws_server.broadcast("handoff_summary", {"session_id": call_id, "summary": summary_text})
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


def _dispatch_chat_message(session_id: str, message: str) -> AgentResponse:
    runtime = _get_runtime(session_id)
    with _lock:
        result, booking, active_agent = dispatch(
            message,
            runtime.inbound,
            runtime.booking,
            runtime.active_agent,
        )
        runtime.booking = booking
        runtime.active_agent = active_agent
    return result


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
    )
    logger.info("CHAT_RESPONSE=%s", _json_log_value(response.model_dump()))
    return response


@app.post("/vapi/tool", response_model=VapiToolResponse)
async def vapi_tool(payload: Any = Body(...)) -> VapiToolResponse:
    logger.info("VAPI_TOOL_REQUEST=%s", _json_log_value(payload))
    session_id, message, tool_call_id = _extract_vapi_tool_payload(payload)
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
