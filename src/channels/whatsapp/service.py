from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable

from src.core.agent_response import AgentResponse

from .models import WhatsAppAgentResult, WhatsAppInboundMessage, WhatsAppIntent, WatiDeliveryResult
from .wati_adapter import WatiChannelClient


class WhatsAppEventStore:
    """Small persistent idempotency store for inbound WATI message IDs."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def seen(self, message_id: str) -> bool:
        return bool(message_id and self._path(message_id).exists())

    def record(self, message_id: str, payload: dict) -> None:
        if not message_id:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(message_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")
        temporary.replace(path)

    def _path(self, message_id: str) -> Path:
        digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.json"


def parse_wati_webhook(payload: object) -> WhatsAppInboundMessage | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    if not message and isinstance(data.get("message"), dict):
        message = data["message"]
    if any(
        value is True
        for value in (
            payload.get("owner"),
            payload.get("fromMe"),
            data.get("owner"),
            data.get("fromMe"),
            message.get("fromMe"),
        )
    ):
        return None

    event_type = str(
        payload.get("eventType")
        or payload.get("event_type")
        or payload.get("type")
        or data.get("eventType")
        or "message"
    ).strip()
    lowered_type = event_type.lower()
    if any(marker in lowered_type for marker in ("delivery", "sent", "read", "failed", "status")):
        return None

    text = _first_text(
        payload.get("text"),
        payload.get("messageText"),
        data.get("text"),
        data.get("messageText"),
        message.get("text"),
        message.get("body"),
    )
    phone = _first_string(
        payload.get("waId"),
        payload.get("whatsappNumber"),
        payload.get("from"),
        payload.get("senderPhone"),
        data.get("waId"),
        data.get("from"),
        message.get("from"),
        message.get("waId"),
    )
    if not text or not phone:
        return None
    message_id = _first_string(
        payload.get("id"),
        payload.get("messageId"),
        payload.get("localMessageId"),
        data.get("id"),
        data.get("messageId"),
        message.get("id"),
    )
    contact_name = _first_string(
        payload.get("senderName"),
        payload.get("contactName"),
        data.get("senderName"),
        message.get("senderName"),
    )
    return WhatsAppInboundMessage(
        phone=phone,
        text=text,
        message_id=message_id,
        contact_name=contact_name,
        event_type=event_type,
    )


def _first_string(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            text = value.get("body") or value.get("text") or value.get("content")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


class WhatsAppResponseFormatter:
    _OPENERS = re.compile(
        r"^(?:great|awesome|perfect|amazing|excellent choice|wonderful)[!,.\s-]*",
        re.IGNORECASE,
    )

    def format(self, response: str, state: dict) -> str:
        readiness = self.booking_readiness_summary(state)
        if readiness:
            return readiness
        text = re.sub(r"[ \t]+", " ", str(response or "")).strip()
        if self._OPENERS.match(text):
            text = "Got it. " + self._OPENERS.sub("", text).lstrip()
        sentences = re.split(r"(?<=[.!?])\s+", text)
        kept: list[str] = []
        questions = 0
        for sentence in sentences:
            if not sentence:
                continue
            if "?" in sentence:
                questions += 1
                if questions > 2:
                    continue
            kept.append(sentence)
        text = " ".join(kept).strip()
        if len(text) <= 320 or "\n" in text:
            return text
        midpoint = next(
            (match.end() for match in re.finditer(r"[.!?] ", text) if match.end() >= len(text) // 2),
            0,
        )
        return f"{text[:midpoint].strip()}\n\n{text[midpoint:].strip()}" if midpoint else text

    @staticmethod
    def booking_readiness_summary(state: dict) -> str:
        if not isinstance(state, dict):
            return ""
        if state.get("booking_id") or state.get("payment_link") or state.get("selected_slot"):
            return ""
        location = str(state.get("location") or "").strip()
        date = str(state.get("preferred_date") or "").strip()
        preferred_time = str(state.get("preferred_time") or state.get("preferred_period") or "").strip()
        players = str(state.get("participants") or "").strip()
        package = str(
            state.get("room")
            or state.get("recommended_option")
            or state.get("event_type")
            or ""
        ).strip()
        name = str(state.get("customer_name") or "").strip()
        experience = str(state.get("experience_level") or "").strip()
        if not all((location, date, preferred_time, players, package, name, experience)):
            return ""
        return (
            "Got it. Here's what I have:\n\n"
            f"Location: {location}\n"
            f"Date: {date}\n"
            f"Time: {preferred_time}\n"
            f"Players: {players}\n"
            f"Room/package: {package}\n"
            f"Name: {name}\n\n"
            "Should I go ahead and check availability?"
        )


class WhatsAppAgentService:
    def __init__(
        self,
        dispatcher: Callable[[str, str], AgentResponse],
        sender: WatiChannelClient | None = None,
        event_store: WhatsAppEventStore | None = None,
        formatter: WhatsAppResponseFormatter | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.sender = sender or WatiChannelClient()
        self.event_store = event_store or WhatsAppEventStore(Path("memory") / "whatsapp_events")
        self.formatter = formatter or WhatsAppResponseFormatter()

    def handle(self, inbound: WhatsAppInboundMessage) -> WhatsAppAgentResult:
        session_id = self.session_id(inbound.phone)
        intent = self.detect_intent(inbound.text)
        if self.event_store.seen(inbound.message_id):
            return WhatsAppAgentResult(
                session_id=session_id,
                intent=intent.value,
                agent_intent="",
                response="",
                duplicate=True,
            )

        result = self.dispatcher(session_id, inbound.text)
        state = result.state if isinstance(result.state, dict) else {}
        response = self.formatter.format(result.response, state)
        delivery = self.sender.send_text(inbound.phone, response)
        output = WhatsAppAgentResult(
            session_id=session_id,
            intent=intent.value,
            agent_intent=result.intent,
            response=response,
            delivery=delivery,
            state=state,
        )
        self.event_store.record(inbound.message_id, output.to_dict())
        return output

    @staticmethod
    def session_id(phone: str) -> str:
        digits = WatiChannelClient.normalise_phone(phone) or re.sub(r"\D", "", phone)
        digest = hashlib.sha256(digits.encode("utf-8")).hexdigest()[:24]
        return f"wa-{digest}"

    @staticmethod
    def detect_intent(message: str) -> WhatsAppIntent:
        lowered = message.lower()
        if re.search(r"\b(?:what is an? escape room|how (?:does|do) (?:an? )?escape room)", lowered):
            return WhatsAppIntent.GENERAL_FAQ
        rules = (
            (WhatsAppIntent.EXISTING_BOOKING, r"\b(existing|my booking|booking id|reschedule|cancel)\b"),
            (WhatsAppIntent.LATE_ARRIVAL, r"\b(late|running late|traffic|delay|arrive late)\b"),
            (WhatsAppIntent.PAYMENT_CONFIRMATION, r"\b(payment|paid|payment link|confirmation|confirmed)\b"),
            (WhatsAppIntent.BIRTHDAY_CELEBRATION, r"\b(birthday|celebration|party)\b"),
            (WhatsAppIntent.CORPORATE_OUTING, r"\b(corporate|team outing|office outing|employees|team building)\b"),
            (WhatsAppIntent.PRICE_ENQUIRY, r"\b(price|pricing|cost|rate|discount|offer)\b"),
            (WhatsAppIntent.ROOM_ENQUIRY, r"\b(room|theme|game|murder mystery|hostage|difficulty)\b"),
            (WhatsAppIntent.LOCATION_ENQUIRY, r"\b(location|branch|where are you|koramangala|whitefield|jp nagar)\b"),
            (WhatsAppIntent.NEW_BOOKING, r"\b(book|booking|reserve|slot|availability)\b"),
        )
        for intent, pattern in rules:
            if re.search(pattern, lowered):
                return intent
        return WhatsAppIntent.GENERAL_FAQ
