"""Signed, self-contained access tokens.

Signed rather than stored, because there is one operator and no session table
to keep. The consequence is stated rather than hidden: a token cannot be
revoked individually, and rotating ``DASHBOARD_SECRET_KEY`` is what invalidates
every issued token at once.

The format is deliberately small — ``payload.signature``, both base64url — and
is not a JWT. Nothing here needs algorithm negotiation, and an ``alg`` field is
a well-known way to get it wrong.
"""

from __future__ import annotations

import hmac
import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal

TokenKind = Literal["session", "bearer"]

#: Refuse absurd input before doing any work on it.
MAX_TOKEN_LENGTH = 4096


class InvalidTokenError(ValueError):
    """Raised when a token is missing, malformed, mis-signed, or expired."""


@dataclass(frozen=True, slots=True)
class AccessToken:
    kind: TokenKind
    issued_at: datetime
    expires_at: datetime


def issue_token(
    *,
    kind: TokenKind,
    secret_key: str,
    lifetime: timedelta,
    now: datetime | None = None,
) -> str:
    moment = now if now is not None else datetime.now(tz=UTC)
    payload = {
        "kind": kind,
        "iat": int(moment.timestamp()),
        "exp": int((moment + lifetime).timestamp()),
    }
    encoded = _encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return f"{encoded}.{_sign(encoded, secret_key)}"


def read_token(
    token: object,
    *,
    secret_key: str,
    now: datetime | None = None,
) -> AccessToken:
    """Verify a token and return its claims, or raise ``InvalidTokenError``."""
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        raise InvalidTokenError("token is missing or unusable")
    # A base64url token is ASCII by construction. Headers arrive latin-1
    # decoded from the wire, so without this an unauthenticated caller could
    # put raw high bytes in Authorization or the cookie and reach either
    # str.encode("ascii") or hmac.compare_digest, both of which raise outside
    # InvalidTokenError and would surface as a 500 rather than a refusal.
    if not token.isascii():
        raise InvalidTokenError("token contains non-ASCII characters")
    encoded, separator, signature = token.partition(".")
    if not separator or not encoded or not signature:
        raise InvalidTokenError("token is malformed")
    # Compared before decoding: an unsigned payload is never parsed.
    if not hmac.compare_digest(_sign(encoded, secret_key), signature):
        raise InvalidTokenError("token signature does not match")
    try:
        payload = json.loads(_decode(encoded))
    except (ValueError, TypeError) as error:
        raise InvalidTokenError("token payload is malformed") from error
    if not isinstance(payload, dict):
        raise InvalidTokenError("token payload is malformed")

    kind = payload.get("kind")
    if kind not in ("session", "bearer"):
        raise InvalidTokenError("token kind is unrecognised")
    try:
        issued_at = datetime.fromtimestamp(int(payload["iat"]), tz=UTC)
        expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
    except (KeyError, TypeError, ValueError, OSError, OverflowError) as error:
        # OverflowError included: a timestamp past the platform's time_t range
        # is malformed input, not an internal fault.
        raise InvalidTokenError("token timestamps are malformed") from error

    moment = now if now is not None else datetime.now(tz=UTC)
    if expires_at <= moment:
        raise InvalidTokenError("token has expired")
    return AccessToken(kind=kind, issued_at=issued_at, expires_at=expires_at)


def _sign(encoded: str, secret_key: str) -> str:
    return _encode(
        hmac.new(secret_key.encode("utf-8"), encoded.encode("ascii"), sha256).digest()
    )


def _encode(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return urlsafe_b64decode(value + "=" * (-len(value) % 4))
