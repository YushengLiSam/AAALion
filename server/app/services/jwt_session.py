"""Minimal HS256 JWT for the session token — stdlib only, no PyJWT dependency.

The login endpoints already return an opaque `token` (== user_id in the demo).
This issues a real **signed, expiring** JWT alongside it (field `jwt`, verified
by `POST /auth/verify`), so the session credential is cryptographically
verifiable for production — additive and backward-compatible: existing clients
ignore the extra field and keep sending the opaque token unchanged.

Secret + TTL come from the environment so the demo VM can set a real secret in
its (gitignored) .env without a code change:
  LIONPICK_JWT_SECRET   signing key (HMAC-SHA256)
  LIONPICK_JWT_TTL_SEC  token lifetime in seconds (default 7 days)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

_SECRET = os.environ.get(
    "LIONPICK_JWT_SECRET", "lionpick-demo-secret-change-in-prod"
).encode()
_TTL = int(os.environ.get("LIONPICK_JWT_TTL_SEC", str(7 * 24 * 3600)))
_ALG = "HS256"


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def issue(user_id: str, *, ttl: int | None = None) -> str:
    """Return a signed HS256 JWT carrying `sub=user_id` and an expiry."""
    now = int(time.time())
    header = {"alg": _ALG, "typ": "JWT"}
    payload = {"sub": user_id, "iat": now, "exp": now + (ttl if ttl is not None else _TTL)}
    signing_input = (
        _b64u(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64u(json.dumps(payload, separators=(",", ":")).encode())
    )
    sig = hmac.new(_SECRET, signing_input.encode("ascii"), hashlib.sha256).digest()
    return signing_input + "." + _b64u(sig)


def verify(token: str) -> dict | None:
    """Return the payload if the signature is valid and the token is unexpired,
    else None. Constant-time signature comparison."""
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}"
        expected = hmac.new(_SECRET, signing_input.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64u_decode(sig_b64)):
            return None
        payload = json.loads(_b64u_decode(payload_b64))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None
