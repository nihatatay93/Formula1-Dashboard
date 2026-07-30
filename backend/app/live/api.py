"""HTTP and WebSocket surface for live timing.

A separate ``/api/v1/live`` namespace. The historical endpoints are untouched and
continue to serve only finalized archive data, and nothing here reads or writes
PostgreSQL. Responses are explicitly marked unconfirmed so a reader never
mistakes live data for the archive record.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, ConfigDict, Field

from app.api.errors import ApiError
from app.live.collector import LiveSessionIdentity
from app.live.f1_auth import InvalidF1TokenError
from app.live.service import (
    LiveFeedUnconfiguredError,
    LiveService,
    LiveSessionConflictError,
)
from app.live.state import get_live_service

router = APIRouter(prefix="/live", tags=["live"])

#: Mounted at the application root as well as under /api/v1/live, because the
#: FastF1 companion extension posts to http://localhost:{port}/auth.
compat_router = APIRouter(tags=["live"])

RECORD_STATE = "unconfirmed_live"


class LiveSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_date: date
    event_name: str = Field(min_length=1, max_length=120)
    session_key: str = Field(min_length=1, max_length=60)

    def to_identity(self) -> LiveSessionIdentity:
        return LiveSessionIdentity(
            session_date=self.session_date,
            event_name=self.event_name,
            session_key=self.session_key,
        )


class AuthStatusResponse(BaseModel):
    """Observable authentication state. Never carries the token value."""

    model_config = ConfigDict(extra="forbid")

    authenticated: bool
    expired: bool
    expires_at: str | None = None
    seconds_remaining: int = 0
    expiry_source: str | None = None


#: Accepted spellings for the token field. The companion extension sends
#: camelCase; a manual paste may use either.
LOGIN_SESSION_KEYS = ("login_session", "loginSession")


async def read_login_session(request: Request) -> object:
    """Extract the token field without letting a rejection echo its value.

    The request body is parsed here rather than through a Pydantic model on
    purpose. Pydantic's validation errors include the offending ``input``, which
    for this endpoint is the credential itself, so a constraint failure would
    reflect the token straight back to the caller and into any error log.
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
    unexpected = sorted(set(body) - set(LOGIN_SESSION_KEYS))
    if unexpected:
        # Field names only; values are never included.
        raise ApiError(
            status_code=422,
            code="unexpected_fields",
            message=f"Unexpected fields: {', '.join(unexpected)}.",
        )
    for key in LOGIN_SESSION_KEYS:
        if key in body:
            return body[key]
    raise ApiError(
        status_code=422,
        code="missing_login_session",
        message="A login_session value is required.",
    )


class LiveStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_state: str = RECORD_STATE
    active: bool
    feed_configured: bool
    retention_days: int
    log_directory_bytes: int
    max_directory_bytes: int
    authentication: AuthStatusResponse
    session: dict | None = None


def _status(service: LiveService) -> LiveStatusResponse:
    return LiveStatusResponse(**service.status())


@router.get(
    "/session",
    response_model=LiveStatusResponse,
    summary="Read live-session status",
)
def read_live_session(
    response: Response,
    service: Annotated[LiveService, Depends(get_live_service)],
) -> LiveStatusResponse:
    response.headers["Cache-Control"] = "no-store"
    return _status(service)


@router.post(
    "/session",
    response_model=LiveStatusResponse,
    summary="Start collecting a live session on demand",
)
async def start_live_session(
    request: LiveSessionRequest,
    response: Response,
    service: Annotated[LiveService, Depends(get_live_service)],
) -> LiveStatusResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        await service.start_session(request.to_identity())
    except LiveFeedUnconfiguredError:
        raise ApiError(
            status_code=503,
            code="live_feed_unconfigured",
            message="No live timing feed provider is configured.",
        ) from None
    except LiveSessionConflictError:
        raise ApiError(
            status_code=409,
            code="live_session_conflict",
            message="A different live session is already active.",
        ) from None
    return _status(service)


@router.delete(
    "/session",
    response_model=LiveStatusResponse,
    summary="Stop the active live session",
)
async def stop_live_session(
    response: Response,
    service: Annotated[LiveService, Depends(get_live_service)],
) -> LiveStatusResponse:
    response.headers["Cache-Control"] = "no-store"
    await service.stop_session()
    return _status(service)


def _save_token(service: LiveService, login_session: object) -> AuthStatusResponse:
    try:
        service.save_token(login_session)
    except InvalidF1TokenError as error:
        # The message never echoes the supplied value.
        raise ApiError(
            status_code=422,
            code="invalid_login_session",
            message=str(error),
        ) from None
    except OSError:
        raise ApiError(
            status_code=500,
            code="token_store_unavailable",
            message="The token could not be stored.",
        ) from None
    return AuthStatusResponse(**service.authentication_status())


@router.get(
    "/auth",
    response_model=AuthStatusResponse,
    summary="Read F1 TV authentication status",
)
def read_live_auth(
    response: Response,
    service: Annotated[LiveService, Depends(get_live_service)],
) -> AuthStatusResponse:
    response.headers["Cache-Control"] = "no-store"
    return AuthStatusResponse(**service.authentication_status())


@router.post(
    "/auth",
    response_model=AuthStatusResponse,
    summary="Store an F1 TV login-session token",
)
async def store_live_auth(
    request: Request,
    response: Response,
    service: Annotated[LiveService, Depends(get_live_service)],
) -> AuthStatusResponse:
    response.headers["Cache-Control"] = "no-store"
    return _save_token(service, await read_login_session(request))


@router.delete(
    "/auth",
    response_model=AuthStatusResponse,
    summary="Forget the stored F1 TV token",
)
def clear_live_auth(
    response: Response,
    service: Annotated[LiveService, Depends(get_live_service)],
) -> AuthStatusResponse:
    response.headers["Cache-Control"] = "no-store"
    service.clear_token()
    return AuthStatusResponse(**service.authentication_status())


@compat_router.post(
    "/auth",
    response_model=AuthStatusResponse,
    include_in_schema=False,
    summary="FastF1 companion extension compatibility endpoint",
)
async def store_live_auth_compat(
    request: Request,
    response: Response,
    service: Annotated[LiveService, Depends(get_live_service)],
) -> AuthStatusResponse:
    """Same contract the companion extension already posts to."""
    response.headers["Cache-Control"] = "no-store"
    return _save_token(service, await read_login_session(request))


@router.websocket("/stream")
async def stream_live_session(
    websocket: WebSocket,
    service: Annotated[LiveService, Depends(get_live_service)],
) -> None:
    await websocket.accept()
    collector = service.active
    if collector is None:
        await websocket.send_json(
            {
                "type": "error",
                "code": "no_active_live_session",
                "message": "No live session is currently being collected.",
            }
        )
        await websocket.close()
        return

    queue = collector.subscribe()
    try:
        await websocket.send_json(
            {
                "type": "snapshot",
                "record_state": RECORD_STATE,
                "session": collector.status()["session"],
                "state": collector.view.snapshot(),
            }
        )
        while True:
            await websocket.send_json(dict(await queue.get()))
    except WebSocketDisconnect:
        pass
    finally:
        collector.unsubscribe(queue)
