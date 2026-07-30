"""Storage for the F1 TV ``login-session`` token used by live timing.

Authentication is performed by the user's own browser against
``account.formula1.com``. This module only receives the resulting
``login-session`` cookie and holds it for the live SignalR connection, which
mirrors how the FastF1 companion extension works: the browser handles the login,
including any bot protection or multi-factor step, and hands over a
short-lived session cookie afterwards.

No password ever reaches this application. The token value is never returned
through the API, never logged, and never written to a session log; only its
presence and expiry are observable.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import stat
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

#: A cookie value, not free text. Bounds and a control-character check keep
#: obviously wrong input out of the store.
MIN_TOKEN_LENGTH = 16
MAX_TOKEN_LENGTH = 32768

#: An expiry claim further out than this is not trusted; the configured TTL is
#: used instead, so a malformed or hostile claim cannot pin a token open.
MAX_TRUSTED_TTL = timedelta(days=14)

#: Claims the dashboard may show, mapped to the names it uses. This is an
#: allowlist rather than a denylist: the token also carries subscriber
#: identifiers, entitlements and a session id, and none of those are ever
#: surfaced. Anything not listed here stays inside the token.
DISPLAYABLE_CLAIMS: dict[str, str] = {
    "SubscribedProduct": "product",
    "SubscriptionStatus": "status",
    "FirstName": "first_name",
}

MAX_CLAIM_LENGTH = 80


class F1AuthError(ValueError):
    """Base error for F1 TV token handling."""


class InvalidF1TokenError(F1AuthError):
    """Raised when a supplied token is not a plausible session cookie."""


@dataclass(frozen=True, slots=True)
class StoredToken:
    obtained_at: datetime
    expires_at: datetime
    #: Whether the expiry came from the token's own claim or the configured TTL.
    expiry_source: str
    #: Whether the caller supplied the cookie wrapper or the token directly.
    token_source: str = "direct"

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def seconds_remaining(self, now: datetime) -> int:
        return max(0, int((self.expires_at - now).total_seconds()))


def extract_subscription_token(value: str) -> tuple[str, str]:
    """Pull the subscription token out of a ``login-session`` cookie.

    The cookie is URL-encoded JSON whose ``data.subscriptionToken`` holds the
    actual RS256 JWT used for live access; the cookie itself is a wrapper. A
    value that is not that wrapper is treated as the token already, so pasting
    either the cookie or the bare token works.

    Returns the token and where it came from.
    """
    try:
        decoded = json.loads(urllib.parse.unquote(value))
    except ValueError:
        return value, "direct"
    if not isinstance(decoded, dict):
        return value, "direct"
    data = decoded.get("data")
    if not isinstance(data, dict):
        return value, "direct"
    token = data.get("subscriptionToken")
    if isinstance(token, str) and token.strip():
        return token.strip(), "login_session_cookie"
    return value, "direct"


def validate_login_session(value: object) -> str:
    """Validate an untrusted ``login-session`` value without echoing it."""
    if not isinstance(value, str):
        raise InvalidF1TokenError("login session must be a string")
    candidate = value.strip()
    if not candidate:
        raise InvalidF1TokenError("login session must not be empty")
    if len(candidate) < MIN_TOKEN_LENGTH:
        raise InvalidF1TokenError("login session is too short to be valid")
    if len(candidate) > MAX_TOKEN_LENGTH:
        raise InvalidF1TokenError("login session is too long to be valid")
    if any(character.isspace() or ord(character) < 0x20 for character in candidate):
        raise InvalidF1TokenError("login session contains invalid characters")
    return candidate


def _decode_claims(token: str) -> dict[str, object] | None:
    """Decode a JWT payload without verifying it.

    Signature verification is F1's job at connection time; this only reads
    claims for display and expiry, so every step is defensive and any failure
    simply yields no claims.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    try:
        padded = payload + "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        claims = json.loads(decoded)
    except (ValueError, binascii.Error, UnicodeEncodeError):
        return None
    return claims if isinstance(claims, dict) else None


def read_jwt_expiry(token: str) -> datetime | None:
    """Return a JWT ``exp`` claim, or None when the value is not a usable JWT."""
    claims = _decode_claims(token)
    if claims is None:
        return None
    expiry = claims.get("exp")
    if isinstance(expiry, bool) or not isinstance(expiry, int | float):
        return None
    try:
        return datetime.fromtimestamp(float(expiry), tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def read_subscription_claims(token: str) -> dict[str, str]:
    """Allowlisted, display-safe claims from the token.

    Only ``DISPLAYABLE_CLAIMS`` are returned, and only when they are short
    strings, so an unexpected or hostile claim cannot become a large payload in
    the dashboard.
    """
    claims = _decode_claims(token)
    if claims is None:
        return {}
    displayable: dict[str, str] = {}
    for claim, name in DISPLAYABLE_CLAIMS.items():
        value = claims.get(claim)
        if isinstance(value, str) and 0 < len(value) <= MAX_CLAIM_LENGTH:
            displayable[name] = value
    return displayable


class F1TokenStore:
    """Reads and writes one token file with owner-only permissions."""

    def __init__(self, path: Path, *, default_ttl: timedelta) -> None:
        if default_ttl <= timedelta(0):
            raise F1AuthError("default_ttl must be positive")
        self._path = path
        self._default_ttl = default_ttl

    @property
    def path(self) -> Path:
        return self._path

    def save(self, login_session: object, *, now: datetime) -> StoredToken:
        """Validate and persist a token, returning its metadata."""
        supplied = validate_login_session(login_session)
        token, token_source = extract_subscription_token(supplied)
        claimed = read_jwt_expiry(token)
        fallback = now + self._default_ttl
        if claimed is not None and now < claimed <= now + MAX_TRUSTED_TTL:
            expires_at, source = claimed, "token_claim"
        else:
            expires_at, source = fallback, "configured_ttl"

        record = {
            "login_session": token,
            "obtained_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "expiry_source": source,
            "token_source": token_source,
            "subscription": read_subscription_claims(token),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Create with owner-only permissions before any content is written, so
        # the token is never briefly readable by other accounts.
        descriptor = os.open(
            self._path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)

        return StoredToken(
            obtained_at=now,
            expires_at=expires_at,
            expiry_source=source,
            token_source=token_source,
        )

    def load(self) -> StoredToken | None:
        record = self._read()
        if record is None:
            return None
        obtained_at = _parse(record.get("obtained_at"))
        expires_at = _parse(record.get("expires_at"))
        if obtained_at is None or expires_at is None:
            return None
        source = record.get("expiry_source")
        token_source = record.get("token_source")
        return StoredToken(
            obtained_at=obtained_at,
            expires_at=expires_at,
            expiry_source=source if isinstance(source, str) else "configured_ttl",
            token_source=(
                token_source if isinstance(token_source, str) else "direct"
            ),
        )

    def login_session(self, *, now: datetime) -> str | None:
        """The stored token, or None when absent or expired.

        This is the only accessor that exposes the value, and it exists for the
        live SignalR connection. It is never reachable through the HTTP API.
        """
        stored = self.load()
        if stored is None or stored.is_expired(now):
            return None
        record = self._read()
        if record is None:
            return None
        value = record.get("login_session")
        return value if isinstance(value, str) and value else None

    def clear(self) -> bool:
        """Delete the stored token. Returns False when there was none."""
        try:
            self._path.unlink()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise F1AuthError("stored token could not be removed") from error
        return True

    def status(self, *, now: datetime) -> dict[str, object]:
        """Observable auth state. Never includes the token value."""
        stored = self.load()
        if stored is None:
            return {
                "authenticated": False,
                "expired": False,
                "expires_at": None,
                "seconds_remaining": 0,
                "expiry_source": None,
                "token_source": None,
                "subscription": {},
            }
        expired = stored.is_expired(now)
        return {
            "authenticated": not expired,
            "expired": expired,
            "expires_at": stored.expires_at.isoformat(),
            "seconds_remaining": stored.seconds_remaining(now),
            "expiry_source": stored.expiry_source,
            "token_source": stored.token_source,
            "subscription": self._subscription(),
        }

    def _subscription(self) -> dict[str, str]:
        """Display claims for the stored token.

        The record holds a cached copy, but the token is the source of truth, so
        a record written before claims were extracted still yields them rather
        than forcing the user to sign in again.
        """
        record = self._read() or {}
        cached = record.get("subscription")
        if isinstance(cached, dict):
            allowed = {
                name: claim
                for name, claim in cached.items()
                if name in DISPLAYABLE_CLAIMS.values() and isinstance(claim, str)
            }
            if allowed:
                return allowed
        token = record.get("login_session")
        return read_subscription_claims(token) if isinstance(token, str) else {}

    def _read(self) -> dict[str, object] | None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as error:
            raise F1AuthError("stored token could not be read") from error
        try:
            decoded = json.loads(raw)
        except ValueError:
            return None
        return decoded if isinstance(decoded, dict) else None


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
