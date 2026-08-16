"""Dashboard access policy.

This authenticates a *reader to this dashboard*. It is unrelated to
``app.live.f1_auth``, which authenticates *this dashboard to Formula 1* — the
two never share a secret, a lifetime, or a failure mode.

There is one operator, so there is no user table: a single password grants a
session. The password is stored as a PBKDF2 hash rather than in plaintext, so a
leaked environment does not hand over a credential the operator may have reused
elsewhere.

Access is required unless it is explicitly switched off. A control that fails
open when unconfigured is worse than no control, because it looks present.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

#: OWASP's floor for PBKDF2-HMAC-SHA256 at the time of writing.
PBKDF2_ITERATIONS = 600_000
PBKDF2_SALT_BYTES = 16
HASH_PREFIX = "pbkdf2_sha256"

#: Fields are separated with "." rather than the conventional "$".
#:
#: The hash is carried in an environment variable, and "$" is a substitution
#: character almost everywhere it would be written: Docker Compose interpolates
#: it inside an env file, and so do most shells. A "$"-separated hash pasted
#: into deploy/.env silently loses everything after the first field, and the
#: only symptom is that the correct password stops being accepted. Base64url
#: uses "-" and "_" but never ".", so "." separates the fields unambiguously.
FIELD_SEPARATOR = "."

#: A browser session. Short enough that a stolen cookie expires, long enough
#: that a race weekend does not need a re-login.
DEFAULT_SESSION_TTL_HOURS = 24 * 7

#: A native client holds a bearer token instead of a cookie and cannot be
#: prompted mid-use, so its token outlives a browser session.
DEFAULT_TOKEN_TTL_DAYS = 90

MIN_SECRET_KEY_LENGTH = 32


class AuthConfigurationError(RuntimeError):
    """Raised when access control is required but not usably configured."""


@dataclass(frozen=True, slots=True)
class AuthSettings:
    required: bool = True
    password_hash: str | None = None
    secret_key: str | None = None
    session_ttl_hours: int = DEFAULT_SESSION_TTL_HOURS
    token_ttl_days: int = DEFAULT_TOKEN_TTL_DAYS
    #: Cookies are Secure by default; a plain-HTTP local run turns this off.
    secure_cookies: bool = True

    def __post_init__(self) -> None:
        if not self.required:
            return
        if not self.password_hash or not self.password_hash.strip():
            raise AuthConfigurationError(
                "DASHBOARD_PASSWORD_HASH is required when access control is on"
            )
        if not self.secret_key or len(self.secret_key) < MIN_SECRET_KEY_LENGTH:
            raise AuthConfigurationError(
                "DASHBOARD_SECRET_KEY must be at least "
                f"{MIN_SECRET_KEY_LENGTH} characters when access control is on"
            )
        if self.session_ttl_hours < 1:
            raise AuthConfigurationError("session TTL must be at least one hour")
        if self.token_ttl_days < 1:
            raise AuthConfigurationError("token TTL must be at least one day")

    @property
    def session_ttl(self) -> timedelta:
        return timedelta(hours=self.session_ttl_hours)

    @property
    def token_ttl(self) -> timedelta:
        return timedelta(days=self.token_ttl_days)

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> AuthSettings:
        values = os.environ if environ is None else environ
        defaults = cls(required=False)
        return cls(
            required=_flag(values, "DASHBOARD_AUTH_REQUIRED", True),
            password_hash=values.get("DASHBOARD_PASSWORD_HASH") or None,
            secret_key=values.get("DASHBOARD_SECRET_KEY") or None,
            session_ttl_hours=_integer(
                values,
                "DASHBOARD_SESSION_TTL_HOURS",
                defaults.session_ttl_hours,
            ),
            token_ttl_days=_integer(
                values,
                "DASHBOARD_TOKEN_TTL_DAYS",
                defaults.token_ttl_days,
            ),
            secure_cookies=_flag(values, "DASHBOARD_SECURE_COOKIES", True),
        )


def hash_password(password: str) -> str:
    """Hash a password for storage in ``DASHBOARD_PASSWORD_HASH``."""
    if not isinstance(password, str) or len(password) < 12:
        raise AuthConfigurationError("password must be at least 12 characters")
    salt = secrets.token_bytes(PBKDF2_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return FIELD_SEPARATOR.join(
        (HASH_PREFIX, str(PBKDF2_ITERATIONS), _encode(salt), _encode(digest))
    )


def verify_password(password: object, encoded: str | None) -> bool:
    """Check a supplied password against a stored hash.

    Returns False for every malformed input rather than raising, so a caller
    cannot distinguish "no password configured" from "wrong password" by the
    shape of the failure.
    """
    if not isinstance(password, str) or not encoded:
        return False
    try:
        # "$" is still read, so a hash generated before the separator changed
        # keeps working rather than failing as a wrong password.
        prefix, iterations, salt, digest = encoded.replace("$", FIELD_SEPARATOR).split(
            FIELD_SEPARATOR
        )
        if prefix != HASH_PREFIX:
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _decode(salt),
            int(iterations),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, _decode(digest))


def _encode(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _flag(values: Mapping[str, str], key: str, default: bool) -> bool:
    raw = values.get(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _integer(values: Mapping[str, str], key: str, default: int) -> int:
    raw = values.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise AuthConfigurationError(f"{key} must be an integer") from error
