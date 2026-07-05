from __future__ import annotations

import hashlib
import hmac


def sign_payload(body: bytes, secret: str) -> str:
    """Return the Closiro/Vapi HMAC-SHA256 signature for an exact request body."""
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if not secret:
        raise ValueError("VAPI_WEBHOOK_SECRET is required for Closiro signing")
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
