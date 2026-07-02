from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import base64
import csv
import io
from argparse import Namespace
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from main import BASE_DIR, build_inbound_agent, dispatch
from src.config.env_loader import booking_provider_label
from src.agents.booking_agent import BookingAgent
from src.agents.conversation_intelligence_agent import ConversationIntelligenceAgent
from src.agents.evaluation_agent import EvaluationAgent
from src.agents.inbound_agent import InboundAgent
from src.memory.conversation_memory import ConversationMemory
from src.services.wati_client import WatiClient


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
API_MEMORY_DIR = BASE_DIR / "memory" / "api_sessions"
API_VERSION = "1.1.0"
WEB_CHAT_DIR = BASE_DIR / "web_chat"
DEFAULT_CLOSIRO_ORG_ID = os.environ.get("CLOSIRO_DEFAULT_ORG_ID", "org_test")
DEFAULT_CLOSIRO_AGENT_ID = int(os.environ.get("CLOSIRO_DEFAULT_AGENT_ID", "1"))


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
    media: list[dict[str, Any]] = Field(default_factory=list)
    booking: dict[str, Any] = Field(default_factory=dict)
    payment: dict[str, Any] = Field(default_factory=dict)
    price_breakdown: dict[str, Any] = Field(default_factory=dict)
    escalation: dict[str, Any] = Field(default_factory=dict)
    handoff_summary: Optional[dict[str, Any]] = None
    sentiment_analysis: dict[str, Any] = Field(default_factory=dict)
    call_intelligence: dict[str, Any] = Field(default_factory=dict)
    ai_summary: dict[str, Any] = Field(default_factory=dict)
    customer_profile: dict[str, Any] = Field(default_factory=dict)
    timeline_events: list[dict[str, Any]] = Field(default_factory=list)
    follow_up_recommendations: list[str] = Field(default_factory=list)
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    recording: dict[str, Any] = Field(default_factory=dict)
    conversation_summary: dict[str, Any] = Field(default_factory=dict)
    evaluation: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    csat: dict[str, Any] = Field(default_factory=dict)
    agent_score: float = 0.0
    sentiment: dict[str, Any] = Field(default_factory=dict)
    sentiment_graph: list[dict[str, Any]] = Field(default_factory=list)
    learning: dict[str, Any] = Field(default_factory=dict)
    follow_up: dict[str, Any] = Field(default_factory=dict)
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)


class ResetRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=80)


class ResetResponse(BaseModel):
    success: bool


class MemoryResponse(BaseModel):
    session_id: str
    memory: dict[str, Any]


class WhatsAppWebhookResponse(BaseModel):
    success: bool
    ignored: bool = False
    session_id: str = ""
    response: str = ""
    next_agent: str = "inbound_agent"
    wati: dict[str, Any] = Field(default_factory=dict)


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
if WEB_CHAT_DIR.is_dir():
    app.mount("/web-chat/assets", StaticFiles(directory=WEB_CHAT_DIR), name="web_chat_assets")
api_v1 = APIRouter(prefix="/api/v1")


def _json_log_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)


# ---------------------------------------------------------------------------
# Developer execution trace  (stdout, gated by BREAKOUT_DEBUG=true)
# ---------------------------------------------------------------------------
_TRACE_SEP = "=" * 60
_TRACE_DIV = "-" * 60
_TRACE_SLOT_KEYS = (
    "intent", "participants", "location", "age_group",
    "preferred_date", "room", "customer_name", "phone",
    "current_workflow", "booking_started",
)


def _dev_trace(
    session_id: str,
    message: str,
    result: Any,
    memory_before: dict,
    memory_after: dict,
    latency_ms: float,
) -> None:
    """Print a bordered developer execution trace to stdout.
    Only active when the environment variable BREAKOUT_DEBUG=true.
    Never called in production; zero impact on business logic.
    """
    if os.environ.get("BREAKOUT_DEBUG", "false").lower() != "true":
        return

    intent          = getattr(result, "intent", "") or ""
    next_agent      = getattr(result, "next_agent", "") or ""
    should_handoff  = getattr(result, "should_handoff", False)
    missing_fields  = getattr(result, "missing_fields", []) or []
    recommendation  = getattr(result, "recommendation", {}) or {}
    booking_result  = getattr(result, "booking_result", None)
    sentiment       = getattr(result, "sentiment_analysis", {}) or {}
    escalation      = getattr(result, "escalation", {}) or {}
    debug           = getattr(result, "debug", {}) or {}
    response_text   = getattr(result, "response", "") or ""

    rec_option  = recommendation.get("option", "")  if isinstance(recommendation, dict) else ""
    rec_reason  = recommendation.get("reason", "")  if isinstance(recommendation, dict) else ""

    extracted       = debug.get("extracted_entities", {}) or {}
    reasoner        = debug.get("reasoner_decision", {}) or {}
    guard_fired     = bool(debug.get("guard_fired"))
    guard_reason    = debug.get("guard_reason", "")

    # Pre-compute non-ASCII constants so Python 3.9 f-strings stay backslash-free
    _na      = "---"
    _ellip   = "..."

    # Booking / Kreeda tool call summary
    tool_section: list[str] = []
    if booking_result and isinstance(booking_result, dict):
        loc_v  = memory_after.get("location", "")
        date_v = memory_after.get("preferred_date", "")
        room_v = memory_after.get("room", "")
        bref   = booking_result.get("booking_ref") or booking_result.get("booking_id", "")
        bstat  = booking_result.get("status", "?")
        tool_section = [
            "  Tool         : Kreeda availability / booking",
            f"  Input slots  : location={loc_v!r}  date={date_v!r}  room={room_v!r}",
            f"  Output       : status={bstat}  ref={bref}",
        ]
    else:
        tool_section = ["  Tool Calls   : none this turn"]

    mem_b = {k: memory_before.get(k, "") for k in _TRACE_SLOT_KEYS}
    mem_a = {k: memory_after.get(k, "")  for k in _TRACE_SLOT_KEYS}

    resp_display = response_text[:200] + (_ellip if len(response_text) > 200 else "")

    rec_opt_display = rec_option or _na
    rec_rsn_display = rec_reason or _na
    rsn_action      = reasoner.get("action", _na)
    rsn_conf        = float(reasoner.get("confidence", 0))
    sent_label      = sentiment.get("sentiment", _na) if isinstance(sentiment, dict) else _na
    sent_conf       = float(sentiment.get("confidence", 0.0)) if isinstance(sentiment, dict) else 0.0
    esc_active      = bool(escalation.get("escalate")) if isinstance(escalation, dict) else False
    esc_rsn_display = (escalation.get("reason") if isinstance(escalation, dict) else "") or _na
    guard_label     = ("FIRED  reason=" + guard_reason) if guard_fired else "pass"

    lines = [
        _TRACE_SEP,
        "[DEV TRACE]  POST /chat",
        _TRACE_SEP,
        f"Session ID      : {session_id}",
        f"Customer (ASR)  : {message}",
        _TRACE_DIV,
        "dispatch()",
        f"  Conversation Guard : {guard_label}",
        f"  Conversation Mgr   : routed to {next_agent}  (handoff={should_handoff})",
        f"  Recommendation     : {rec_opt_display}",
        f"    Reason           : {rec_rsn_display}",
        f"  Reasoner Action    : {rsn_action}  (conf={rsn_conf:.2f})",
        _TRACE_DIV,
        "Booking Agent",
        *tool_section,
        _TRACE_DIV,
        f"Intent          : {intent}  (missing: {', '.join(missing_fields) or 'none'})",
        f"Sentiment       : {sent_label}  (confidence={sent_conf:.2f})",
        f"Escalation      : {esc_active}  reason={esc_rsn_display}",
        _TRACE_DIV,
        "Memory BEFORE",
        *[f"  {k:<20}: {v}" for k, v in mem_b.items()],
        "Memory AFTER",
        *[f"  {k:<20}: {v}" for k, v in mem_a.items()],
        "Extracted Entities",
        *(  [f"  {k}: {v}" for k, v in extracted.items()] if extracted else ["  (none)"]  ),
        _TRACE_DIV,
        f"Final Response  : {resp_display}",
        _TRACE_DIV,
        f"Latency         : {latency_ms:.1f} ms",
        _TRACE_SEP,
    ]
    print("\n".join(lines), flush=True)



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


def _whatsapp_session_id(phone: str) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) == 10:
        digits = f"91{digits}"
    if not digits:
        raise HTTPException(status_code=400, detail="WhatsApp sender phone is required.")
    return _validate_session_id(f"whatsapp:{digits}")


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


def _normalise_media_item(item: Any, fallback_type: str = "") -> dict[str, Any] | None:
    if isinstance(item, str):
        url = item.strip()
        if not url:
            return None
        return {"type": fallback_type or _infer_media_type(url), "url": url, "title": ""}
    if not isinstance(item, dict):
        return None
    url = str(item.get("url") or item.get("href") or item.get("src") or "").strip()
    if not url:
        return None
    media_type = str(item.get("type") or item.get("media_type") or fallback_type or _infer_media_type(url)).strip()
    return {
        "type": media_type,
        "url": url,
        "title": str(item.get("title") or item.get("name") or item.get("label") or "").strip(),
        "thumbnail_url": str(item.get("thumbnail_url") or item.get("thumbnail") or "").strip(),
        "mime_type": str(item.get("mime_type") or item.get("mime") or "").strip(),
    }


def _infer_media_type(url: str) -> str:
    lowered = url.lower().split("?", 1)[0]
    if lowered.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif")):
        return "image"
    if lowered.endswith((".mp4", ".webm", ".mov", ".m4v")):
        return "video"
    if lowered.endswith(".pdf"):
        return "document"
    return "link"


def _extract_media_payload(result: Any) -> list[dict[str, Any]]:
    state = getattr(result, "state", {}) or {}
    candidates: list[dict[str, Any]] = []
    for key, fallback_type in (
        ("media", ""),
        ("attachments", ""),
        ("images", "image"),
        ("image_urls", "image"),
        ("videos", "video"),
        ("video_urls", "video"),
        ("documents", "document"),
        ("document_urls", "document"),
    ):
        raw = state.get(key)
        if not raw:
            continue
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            media = _normalise_media_item(item, fallback_type)
            if media:
                candidates.append(media)
    seen: set[str] = set()
    media_items: list[dict[str, Any]] = []
    for item in candidates:
        url = item["url"]
        if url in seen:
            continue
        seen.add(url)
        media_items.append(item)
    return media_items


def _extract_booking_payload(result: Any) -> dict[str, Any]:
    state = getattr(result, "state", {}) or {}
    booking_result = getattr(result, "booking_result", None) or getattr(result, "booking", None) or {}
    if not isinstance(booking_result, dict):
        booking_result = {}
    booking_id = str(state.get("booking_id") or booking_result.get("booking_id") or "").strip()
    if not booking_id:
        return {}
    price_breakdown = _extract_price_breakdown(result)
    return {
        "booking_id": booking_id,
        "reference": str(state.get("booking_ref") or booking_result.get("booking_reference") or "").strip(),
        "order_id": str(state.get("orderId") or state.get("order_id") or booking_result.get("order_id") or "").strip(),
        "status": str(state.get("bookingStatus") or state.get("booking_status") or booking_result.get("status") or "").strip(),
        "room": str(state.get("room") or state.get("recommended_option") or booking_result.get("room") or "").strip(),
        "location": str(state.get("location") or booking_result.get("location") or "").strip(),
        "date": str(state.get("preferred_date") or booking_result.get("date") or "").strip(),
        "time": str(state.get("selected_slot") or booking_result.get("slot") or "").strip(),
        "participants": state.get("participants") or state.get("company_size") or booking_result.get("participants") or "",
        "price_breakdown": price_breakdown,
    }


def _extract_price_breakdown(result: Any) -> dict[str, Any]:
    state = getattr(result, "state", {}) or {}
    booking_result = getattr(result, "booking_result", None) or getattr(result, "booking", None) or {}
    candidates = (
        state.get("price_breakdown"),
        booking_result.get("price_breakdown") if isinstance(booking_result, dict) else None,
    )
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("final_price") is not None:
            return dict(candidate)
    return {}


def _extract_payment_payload(result: Any) -> dict[str, Any]:
    state = getattr(result, "state", {}) or {}
    booking_result = getattr(result, "booking_result", None) or getattr(result, "booking", None) or {}
    if not isinstance(booking_result, dict):
        booking_result = {}
    payment_url = str(
        state.get("paymentUrl")
        or state.get("payment_url")
        or state.get("payment_link")
        or booking_result.get("payment_url")
        or booking_result.get("paymentUrl")
        or ""
    ).strip()
    payment_status = str(state.get("paymentStatus") or state.get("payment_status") or booking_result.get("payment_status") or "").strip()
    deadline = str(state.get("paymentDeadline") or state.get("payment_deadline") or booking_result.get("payment_deadline") or "").strip()
    if not (payment_url or payment_status or deadline):
        return {}
    return {
        "status": payment_status,
        "payment_url": payment_url,
        "payment_deadline": deadline,
        "booking_status": str(state.get("bookingStatus") or state.get("booking_status") or booking_result.get("status") or "").strip(),
    }


def _reporting_payload(
    session_id: str,
    result: Any,
    memory: dict[str, Any],
    latency_ms: float,
) -> dict[str, Any]:
    transcript = list(getattr(result, "transcript", []) or [])
    sentiment = dict(getattr(result, "sentiment_analysis", {}) or {})
    escalation = dict(getattr(result, "escalation", {}) or {})
    handoff_summary = getattr(result, "handoff_summary", None)
    evaluation_result = EvaluationAgent().evaluate(
        {
            "memory": memory,
            "transcript": transcript,
            "escalation": escalation,
            "handoff_summary": handoff_summary,
        }
    )
    evaluation = {
        "score": evaluation_result.score,
        "category_scores": evaluation_result.category_scores,
        "reasoning": evaluation_result.reasoning,
        "flags": evaluation_result.flags,
    }
    sentiment_graph: list[dict[str, Any]] = []
    for index, item in enumerate(memory.get("sentiment_history") or [], start=1):
        label = str(item.get("sentiment") or item.get("label") or "neutral").lower()
        score = 0.18
        if label in {"positive", "happy"}:
            score = 0.75
        elif label in {"negative", "frustrated", "angry"}:
            score = -0.72
        sentiment_graph.append({"turn": index, "score": score, "label": label.title()})
    if not sentiment_graph:
        sentiment_graph = [{"turn": 1, "score": 0.18, "label": "Neutral"}]

    escalated = bool(escalation.get("escalate") or escalation.get("required"))
    booking_id = str(memory.get("booking_id") or "")
    final_sentiment = str(memory.get("sentiment") or "neutral").lower()
    csat_score = 4.2 + (0.5 if booking_id else 0.0) - (0.4 if escalated else 0.0)
    if final_sentiment in {"negative", "frustrated", "angry"}:
        csat_score -= 1.0
    csat = {
        "score": max(1.0, min(5.0, round(csat_score, 1))),
        "confidence": 0.72,
        "reason": "Predicted from final sentiment, escalation state, and booking completion.",
    }
    ai_summary = dict(getattr(result, "ai_summary", {}) or {})
    conversation_summary = {
        "conversation_id": session_id,
        "customer_name": str(memory.get("customer_name") or ""),
        "phone": str(memory.get("phone") or ""),
        "intent": str(getattr(result, "intent", "") or memory.get("intent") or ""),
        "outcome": "escalated" if escalated else ("booking_reserved" if booking_id else "in_progress"),
        "turn_count": len(transcript),
        "venue": str(memory.get("location") or ""),
        "room": str(memory.get("room") or memory.get("recommended_option") or ""),
        "date": str(memory.get("preferred_date") or ""),
        "time": str(memory.get("selected_slot") or ""),
        "booking_id": booking_id,
        "payment_status": str(memory.get("paymentStatus") or memory.get("payment_status") or ""),
        "escalation_status": "escalated" if escalated else "none",
        "final_sentiment": final_sentiment,
        "summary": str(ai_summary.get("summary") or getattr(result, "response", "") or ""),
    }
    customer_turns = [turn for turn in transcript if str(turn.get("role") or "").lower() == "customer"]
    joined = "\n".join(str(turn.get("text") or "") for turn in customer_turns).lower()
    metrics = {
        "turn_count": len(transcript),
        "customer_turn_count": len(customer_turns),
        "response_time_ms": round(latency_ms, 1),
        "api_call_count": len(customer_turns),
        "booking_completed": bool(booking_id),
        "payment_link_sent": bool(memory.get("paymentUrl") or memory.get("payment_url")),
        "escalation_requested": bool(re.search(r"\b(?:human|real person|transfer me|connect me)\b", joined)),
        "escalated": escalated,
        "faq_count": sum(joined.count(term) for term in ("parking", "food", "price", "birthday", "cancel", "reschedul")),
    }
    topics = [term for term in ("parking", "food", "pricing", "birthday", "cancellation", "reschedule", "payment") if term in joined]
    learning = {
        "observed_topics": topics,
        "drop_off_stage": "escalation" if escalated else ("payment_pending" if booking_id else "in_progress"),
        "conversation_loops_found": [],
        "backend_errors": [],
    }
    follow_up = {"recommendations": list(getattr(result, "follow_up_recommendations", []) or [])}
    payload = {
        "conversation_summary": conversation_summary,
        "evaluation": evaluation,
        "metrics": metrics,
        "csat": csat,
        "agent_score": float(evaluation_result.score),
        "sentiment": sentiment,
        "sentiment_graph": sentiment_graph,
        "learning": learning,
        "follow_up": follow_up,
        "conversation_history": transcript,
    }
    memory.update(payload)
    return payload


def _crm_org() -> dict[str, Any]:
    return closira_store.org(os.environ.get("CLOSIRO_DEFAULT_ORG_ID", DEFAULT_CLOSIRO_ORG_ID))


def _crm_agent_id() -> int:
    try:
        return int(os.environ.get("CLOSIRO_DEFAULT_AGENT_ID", str(DEFAULT_CLOSIRO_AGENT_ID)))
    except (TypeError, ValueError):
        return DEFAULT_CLOSIRO_AGENT_ID


def _find_or_create_contact(org: dict[str, Any], memory: dict[str, Any], channel: str) -> int:
    org_id = org.get("org_id") or os.environ.get("CLOSIRO_DEFAULT_ORG_ID", DEFAULT_CLOSIRO_ORG_ID)
    phone = "".join(ch for ch in str(memory.get("phone") or memory.get("whatsapp_number") or "") if ch.isdigit())
    name = str(memory.get("customer_name") or "").strip()
    for contact in org["contacts"]:
        contact_phone = "".join(ch for ch in str(contact.get("phone") or "") if ch.isdigit())
        if phone and contact_phone.endswith(phone[-10:]):
            contact["last_activity"] = _now_iso()
            if name and not contact.get("name"):
                contact["name"] = name
            return int(contact["id"])
    contact_id = closira_store.next_id(org, "contacts")
    first_name = str(memory.get("first_name") or (name.split(" ", 1)[0] if name else "")).strip()
    last_name = str(memory.get("last_name") or (name.split(" ", 1)[1] if " " in name else "")).strip()
    contact = {
        "id": contact_id,
        "org_id": org_id,
        "first_name": first_name,
        "last_name": last_name,
        "name": name or phone or "Web Chat Visitor",
        "phone": phone,
        "email": str(memory.get("email") or ""),
        "type": "contact",
        "priority": "medium",
        "stage": "active",
        "assigned_to": _crm_agent_id(),
        "source": channel,
        "channel": channel,
        "value": 0,
        "last_activity": _now_iso(),
        "created_at": _now_iso(),
    }
    org["contacts"].append(contact)
    return contact_id


def _transcript_lines(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if transcript:
        return [
            {
                "sequence": int(item.get("sequence") or item.get("index") or idx + 1),
                "speaker_type": str(item.get("speaker_type") or item.get("role") or item.get("speaker") or "").lower() or "unknown",
                "text": str(item.get("text") or ""),
                "spoken_at_second": float(item.get("spoken_at_second") or idx * 2.0),
            }
            for idx, item in enumerate(transcript)
        ]
    return []


def _persist_chat_result(session_id: str, runtime: SessionRuntime, result: Any, channel: str = "web_chat") -> None:
    org = _crm_org()
    org_id = os.environ.get("CLOSIRO_DEFAULT_ORG_ID", DEFAULT_CLOSIRO_ORG_ID)
    memory = runtime.inbound.memory.data
    contact_id = _find_or_create_contact(org, memory, channel)
    conversation = memory.get("conversation") if isinstance(memory.get("conversation"), list) else []
    started_at = conversation[0].get("timestamp") if conversation and isinstance(conversation[0], dict) else _now_iso()
    transcript = _transcript_lines(getattr(result, "transcript", []) or [])
    if not transcript and conversation:
        transcript = [
            {
                "sequence": idx + 1,
                "speaker_type": str(turn.get("role") or "unknown"),
                "text": str(turn.get("text") or turn.get("message") or ""),
                "spoken_at_second": float(idx * 2),
            }
            for idx, turn in enumerate(conversation)
            if isinstance(turn, dict)
        ]
    summary = getattr(result, "ai_summary", None) or {}
    if not isinstance(summary, dict):
        summary = {}
    summary_text = str(summary.get("summary") or getattr(result, "response", "") or "")
    existing = next((call for call in org["calls"] if call.get("session_id") == session_id), None)
    if existing is None:
        existing = {
            "id": closira_store.next_id(org, "calls"),
            "org_id": org_id,
            "session_id": session_id,
            "agent_id": _crm_agent_id(),
            "assigned_to": _crm_agent_id(),
            "contact_id": contact_id,
            "status": "answered",
            "category": "booking" if memory.get("booking_id") else "inquiry",
            "is_ai": True,
            "recording_url": "",
            "started_at": started_at,
            "occurred_at": _now_iso(),
            "live_status": "Answered",
            "live_duration_seconds": max(len(transcript) * 8, 1),
            "channel": channel,
        }
        org["calls"].append(existing)
    existing.update(
        {
            "contact_id": contact_id,
            "intent": str(getattr(result, "intent", "") or memory.get("intent") or "Inquiry"),
            "summary": summary_text,
            "conversation_summary": summary or {"summary": summary_text},
            "transcript": transcript,
            "sentiment": str(memory.get("sentiment") or "neutral"),
            "occurred_at": _now_iso(),
            "live_duration_seconds": max(len(transcript) * 8, existing.get("live_duration_seconds", 1)),
            "category": "booking" if memory.get("booking_id") else existing.get("category", "inquiry"),
        }
    )
    booking_payload = _extract_booking_payload(result)
    payment_payload = _extract_payment_payload(result)
    if booking_payload and not any(item.get("external_booking_id") == booking_payload["booking_id"] for item in org["bookings"]):
        org["bookings"].append(
            {
                "id": closira_store.next_id(org, "bookings"),
                "org_id": org_id,
                "contact_id": contact_id,
                "lead_id": None,
                "assigned_to": _crm_agent_id(),
                "event_type": str(memory.get("event_type") or "Escape Room"),
                "location": booking_payload.get("location", ""),
                "party_size": int(memory.get("participants") or memory.get("company_size") or 0),
                "event_date": booking_payload.get("date", ""),
                "total_amount": float((booking_payload.get("price_breakdown") or {}).get("final_price") or 0.0),
                "paid_amount": 0.0,
                "payment_status": payment_payload.get("status") or memory.get("paymentStatus") or "UNPAID",
                "source": channel,
                "channel": channel,
                "notes": f"Payment URL: {payment_payload.get('payment_url', '')}",
                "external_booking_id": booking_payload["booking_id"],
                "created_at": _now_iso(),
            }
        )
    escalation = getattr(result, "escalation", None) or {}
    should_escalate = bool(
        getattr(result, "should_handoff", False)
        or escalation.get("escalate")
        or escalation.get("required")
        or getattr(result, "next_agent", "") == "escalation_agent"
    )
    if should_escalate and not any(item.get("session_id") == session_id for item in org["escalations"]):
        org["escalations"].append(
            {
                "id": closira_store.next_id(org, "escalations"),
                "org_id": org_id,
                "session_id": session_id,
                "call_id": existing["id"],
                "contact_id": contact_id,
                "assigned_to": _crm_agent_id(),
                "escalation_status": "open",
                "target_group": "sales_manager",
                "reason": str(escalation.get("reason") or "Escalation requested"),
                "resolution_notes": "",
                "created_at": _now_iso(),
                "handoff_summary": getattr(result, "handoff_summary", None) or {},
                "conversation_summary": summary or {"summary": summary_text},
                "timeline": getattr(result, "timeline_events", []) or [],
                "customer_profile": getattr(result, "customer_profile", {}) or {},
                "action_items": (summary.get("action_items") if isinstance(summary, dict) else []) or [],
                "sentiment": getattr(result, "sentiment_analysis", {}) or {},
            }
        )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        booking_provider=booking_provider_label(),
        version=API_VERSION,
    )


@app.get("/")
def web_chat_home() -> FileResponse:
    return FileResponse(WEB_CHAT_DIR / "index.html")


@app.get("/web-chat")
def web_chat() -> FileResponse:
    return FileResponse(WEB_CHAT_DIR / "index.html")


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
    _mem_before = dict(runtime.inbound.memory.data)   # snapshot BEFORE dispatch
    _t0 = time.perf_counter()
    with _lock:
        result, booking, active_agent = dispatch(
            message,
            runtime.inbound,
            runtime.booking,
            runtime.active_agent,
        )
        runtime.booking = booking
        runtime.active_agent = active_agent
    _latency_ms = (time.perf_counter() - _t0) * 1000
    _mem_after = dict(runtime.inbound.memory.data)    # snapshot AFTER dispatch
    _dev_trace(request.session_id, message, result, _mem_before, _mem_after, _latency_ms)
    reporting = _reporting_payload(
        request.session_id,
        result,
        runtime.inbound.memory.data,
        _latency_ms,
    )
    runtime.inbound.memory.save()
    response = ChatResponse(
        response=result.response,
        next_agent=result.next_agent,
        media=_extract_media_payload(result),
        booking=_extract_booking_payload(result),
        payment=_extract_payment_payload(result),
        price_breakdown=_extract_price_breakdown(result),
        escalation=result.escalation,
        handoff_summary=result.handoff_summary,
        sentiment_analysis=result.sentiment_analysis,
        call_intelligence=result.call_intelligence,
        ai_summary=result.ai_summary,
        customer_profile=result.customer_profile,
        timeline_events=result.timeline_events,
        follow_up_recommendations=result.follow_up_recommendations,
        transcript=result.transcript,
        recording=result.recording,
        **reporting,
    )
    _persist_chat_result(request.session_id, runtime, result, channel="web_chat")
    logger.info("CHAT_RESPONSE=%s", _json_log_value(response.model_dump()))
    return response


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
# Closira CRM API contract
# ---------------------------------------------------------------------------

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


app.include_router(api_v1)
