"""Outbound Closiro CRM webhook integration."""

from .client import ClosiroClient, ClosiroSyncResult, get_default_closiro_client
from .payload_builder import build_call_ended_payload, build_escalation_context, build_escalation_payload
from .signer import sign_payload

__all__ = [
    "ClosiroClient",
    "ClosiroSyncResult",
    "build_call_ended_payload",
    "build_escalation_context",
    "build_escalation_payload",
    "get_default_closiro_client",
    "sign_payload",
]
