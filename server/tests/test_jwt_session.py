"""Tests for the stdlib HS256 session JWT — issue / verify / tamper / expiry."""

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # server/
from app.services import jwt_session as J  # noqa: E402


def test_roundtrip_carries_subject():
    token = J.issue("phone:13800138000")
    payload = J.verify(token)
    assert payload is not None
    assert payload["sub"] == "phone:13800138000"
    assert payload["exp"] > payload["iat"]


def test_tampered_signature_rejected():
    head, payload, sig = J.issue("u1").split(".")
    forged = f"{head}.{payload}.{sig[:-3]}xyz"
    assert J.verify(forged) is None


def test_tampered_payload_rejected():
    head, _, sig = J.issue("u1").split(".")
    evil = base64.urlsafe_b64encode(
        json.dumps({"sub": "admin", "exp": 9999999999}).encode()
    ).rstrip(b"=").decode()
    assert J.verify(f"{head}.{evil}.{sig}") is None  # sig no longer matches payload


def test_expired_rejected():
    assert J.verify(J.issue("u1", ttl=-10)) is None


def test_garbage_rejected():
    for bad in ("not.a.jwt", "", "a.b", "x.y.z"):
        assert J.verify(bad) is None
