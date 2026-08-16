"""Sign-in surface and the gate that protects every other route."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.errors import ApiError
from app.auth.policy import AuthSettings, verify_password
from app.auth.tokens import InvalidTokenError, issue_token, read_token

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_COOKIE = "dashboard_session"

#: Paths reachable without credentials. Health is exempt because the platform
#: probes it before anyone can sign in; sign-in itself obviously cannot require
#: a session; reading session state has to answer "do I need to sign in?" to a
#: caller who by definition has not yet; and signing out must clear a cookie
#: even when the session behind it has already lapsed. Nothing else is listed,
#: including the schema. None of the four reveals anything about the archive.
PUBLIC_PATHS = frozenset(
    {
        "/api/health/live",
        "/api/health/ready",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/session",
    }
)

#: The only path that can answer a CORS preflight, for the FastF1 companion
#: extension. Exempting OPTIONS everywhere would leave the gate open on any
#: route that later grows an OPTIONS handler, so the exemption is named — and
#: the caller passes it only when that route is actually mounted.
COMPANION_PREFLIGHT_PATHS = frozenset({"/auth"})

#: A single password is guessable given enough attempts, so attempts are
#: bounded. State is per process and resets on restart, which is acceptable
#: because the deployment runs one API instance.
MAX_FAILED_ATTEMPTS = 8
LOCKOUT_SECONDS = 300.0


@dataclass
class AttemptLimiter:
    """Counts consecutive failures and refuses sign-in once they pile up."""

    max_attempts: int = MAX_FAILED_ATTEMPTS
    lockout_seconds: float = LOCKOUT_SECONDS
    clock: Callable[[], float] = time.monotonic
    _failures: int = field(default=0, init=False)
    _locked_until: float = field(default=0.0, init=False)

    def seconds_remaining(self) -> int:
        return max(0, int(self._locked_until - self.clock()))

    def locked(self) -> bool:
        if self._locked_until == 0.0:
            return False
        if self.clock() >= self._locked_until:
            # The window elapsed; start counting again from clean.
            self._failures = 0
            self._locked_until = 0.0
            return False
        return True

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.max_attempts:
            self._locked_until = self.clock() + self.lockout_seconds

    def record_success(self) -> None:
        self._failures = 0
        self._locked_until = 0.0


_limiter = AttemptLimiter()


def get_auth_settings(request: Request) -> AuthSettings:
    settings = getattr(request.app.state, "auth_settings", None)
    if settings is None:
        settings = AuthSettings.from_environment()
        request.app.state.auth_settings = settings
    return settings


class LoginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authenticated: bool
    #: Bearer token for native clients. A browser uses the cookie instead and
    #: can ignore this.
    token: str
    expires_at: str


class SessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authenticated: bool
    required: bool
    kind: str | None = None
    expires_at: str | None = None


async def read_password(request: Request) -> object:
    """Pull the password out of the body without letting a rejection echo it.

    Parsed by hand rather than through a Pydantic model on purpose: validation
    errors carry the offending ``input``, which here is the credential, and
    would reflect it back to the caller and into any error log.
    """
    try:
        body = await request.json()
    except ValueError:
        raise ApiError(
            status_code=422,
            code="invalid_request_body",
            message="Expected a JSON object.",
        ) from None
    if not isinstance(body, Mapping):
        raise ApiError(
            status_code=422,
            code="invalid_request_body",
            message="Expected a JSON object.",
        )
    unexpected = sorted(set(body) - {"password"})
    if unexpected:
        # Field names only; values are never included.
        raise ApiError(
            status_code=422,
            code="unexpected_fields",
            message=f"Unexpected fields: {', '.join(unexpected)}.",
        )
    if "password" not in body:
        raise ApiError(
            status_code=422,
            code="missing_password",
            message="A password is required.",
        )
    return body["password"]


@router.post("/login", response_model=LoginResponse, summary="Sign in")
async def login(
    request: Request,
    response: Response,
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
) -> LoginResponse:
    response.headers["Cache-Control"] = "no-store"
    if not settings.required:
        raise ApiError(
            status_code=409,
            code="auth_not_required",
            message="This deployment does not require sign-in.",
        )
    password = await read_password(request)

    # The password is checked even while locked out, so the lockout can only
    # ever refuse a wrong password. Checking it first instead would let anyone
    # keep the operator permanently locked out of their own dashboard by
    # guessing wrongly every few minutes — a denial of service that costs the
    # attacker nothing. Guessing is still bounded: each attempt pays for a
    # 600,000-iteration hash, and a wrong one is refused outright while locked.
    if verify_password(password, settings.password_hash):
        _limiter.record_success()
    else:
        _limiter.record_failure()
        if _limiter.locked():
            raise ApiError(
                status_code=429,
                code="too_many_attempts",
                message=(
                    "Too many failed sign-in attempts. Try again in "
                    f"{_limiter.seconds_remaining()} seconds."
                ),
            )
        # Deliberately identical for a wrong password and a malformed one.
        raise ApiError(
            status_code=401,
            code="invalid_credentials",
            message="That password was not accepted.",
        )

    assert settings.secret_key is not None
    session = issue_token(
        kind="session",
        secret_key=settings.secret_key,
        lifetime=settings.session_ttl,
    )
    bearer = issue_token(
        kind="bearer",
        secret_key=settings.secret_key,
        lifetime=settings.token_ttl,
    )
    response.set_cookie(
        SESSION_COOKIE,
        session,
        httponly=True,
        max_age=int(settings.session_ttl.total_seconds()),
        samesite="lax",
        secure=settings.secure_cookies,
    )
    claims = read_token(bearer, secret_key=settings.secret_key)
    return LoginResponse(
        authenticated=True,
        token=bearer,
        expires_at=claims.expires_at.isoformat(),
    )


@router.post("/logout", response_model=SessionResponse, summary="Sign out")
def logout(
    response: Response,
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
) -> SessionResponse:
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
    )
    # A bearer token cannot be withdrawn here; rotating the secret key is what
    # invalidates issued tokens.
    return SessionResponse(authenticated=False, required=settings.required)


@router.get("/session", response_model=SessionResponse, summary="Read sign-in state")
def read_session(
    request: Request,
    response: Response,
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
) -> SessionResponse:
    response.headers["Cache-Control"] = "no-store"
    if not settings.required:
        return SessionResponse(authenticated=True, required=False)
    token = _presented_token(request)
    try:
        assert settings.secret_key is not None
        claims = read_token(token, secret_key=settings.secret_key)
    except InvalidTokenError:
        return SessionResponse(authenticated=False, required=True)
    return SessionResponse(
        authenticated=True,
        required=True,
        kind=claims.kind,
        expires_at=claims.expires_at.isoformat(),
    )


def _presented_token(connection: HTTPConnection) -> str | None:
    """The bearer header wins; a browser falls back to its cookie.

    Typed as ``HTTPConnection`` rather than ``Request`` because the gate also
    runs for WebSocket scopes, and ``Request`` refuses to wrap those.

    A browser WebSocket cannot set headers, so the cookie is what authenticates
    the live stream. A native client can set the header on the handshake and
    needs no cookie at all.
    """
    header = connection.headers.get("Authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return connection.cookies.get(SESSION_COOKIE)


class AuthenticationMiddleware:
    """Refuses every request that is not signed in.

    A gate rather than a per-route dependency: a new route is protected because
    it exists, not because someone remembered to decorate it. Anything
    deliberately open is named in ``PUBLIC_PATHS``.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: AuthSettings,
        preflight_paths: frozenset[str] = frozenset(),
    ) -> None:
        self._app = app
        self._settings = settings
        # Empty unless a route that needs a CORS preflight is mounted, so an
        # unauthenticated OPTIONS has nowhere to land by default.
        self._preflight_paths = preflight_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not self._settings.required:
            await self._app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in PUBLIC_PATHS or (
            path in self._preflight_paths
            and scope["type"] == "http"
            and scope.get("method") == "OPTIONS"
        ):
            await self._app(scope, receive, send)
            return

        connection = HTTPConnection(scope)
        try:
            assert self._settings.secret_key is not None
            read_token(
                _presented_token(connection),
                secret_key=self._settings.secret_key,
            )
        except InvalidTokenError:
            await self._reject(scope, receive, send)
            return
        await self._app(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            # 1008 is "policy violation"; the handshake is refused outright.
            await send({"type": "websocket.close", "code": 1008})
            return
        response = JSONResponse(
            status_code=401,
            content={
                "detail": {
                    "code": "not_authenticated",
                    "message": "Sign in to use this dashboard.",
                }
            },
            headers={"Cache-Control": "no-store"},
        )
        await response(scope, receive, send)


def reset_attempt_limiter() -> None:
    """Clear the process-wide lockout. Intended for tests."""
    _limiter.record_success()

