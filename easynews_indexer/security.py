from __future__ import annotations

import base64
import hashlib
import hmac
import json


def sign_payload(payload: dict, secret: str) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{body}.{signature}"


def decode_signed(token: str, secret: str, allow_legacy: bool = False) -> dict:
    if "." not in token:
        if not allow_legacy:
            raise ValueError("Unsigned result id rejected")
        body = token
    else:
        body, supplied = token.rsplit(".", 1)
        expected_raw = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        expected = base64.urlsafe_b64encode(expected_raw).decode().rstrip("=")
        if not hmac.compare_digest(expected, supplied):
            raise ValueError("Invalid result id signature")
    raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    value = json.loads(raw.decode())
    if not isinstance(value, dict):
        raise ValueError("Invalid result id payload")
    return value
