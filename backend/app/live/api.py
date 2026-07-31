"""HTTP and WebSocket surface for live timing.

A separate ``/api/v1/live`` namespace. The historical endpoints are untouched and
continue to serve only finalized archive data, and nothing here reads or writes
PostgreSQL. Responses are explicitly marked unconfirmed so a reader never
mistakes live data for the archive record.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
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
from app.live.board import board_to_dict, build_board
from app.live.f1_auth import InvalidF1TokenError
from app.live.recordings import RecordingNotFoundError
from app.live.service import (
    LiveFeedUnconfiguredError,
    LiveService,
    LiveSessionBusyError,
    LiveUnauthenticatedError,
)
from app.live.state import get_live_service

router = APIRouter(prefix="/live", tags=["live"])

#: Mounted at the application root as well as under /api/v1/live, because the
#: FastF1 companion extension posts to http://localhost:{port}/auth.
compat_router = APIRouter(tags=["live"])

RECORD_STATE = "unconfirmed_live"

#: Deltas arrive faster than a reader can use. Updates are coalesced to at most
#: one board per interval, which keeps the payload small during a live session
#: without the client having to reconstruct state from raw deltas.
BOARD_MIN_INTERVAL_SECONDS = 0.25

#: Upper bound on replay speed. Pacing is what keeps a replay from emitting a
#: whole session in one burst, and the live path shares this event loop.
MAX_REPLAY_SPEED = 120.0


class AuthStatusResponse(BaseModel):
    """Observable authentication state. Never carries the token value."""

    model_config = ConfigDict(extra="forbid")

    authenticated: bool
    expired: bool
    expires_at: str | None = None
    seconds_remaining: int = 0
    expiry_source: str | None = None
    token_source: str | None = None
    #: Allowlisted, display-safe claims only. Subscriber identifiers,
    #: entitlements and the session id are never included.
    subscription: dict[str, str] = Field(default_factory=dict)
    #: One-click entry point that primes the FastF1 companion extension with the
    #: port this API is reachable on, then sends the browser to formula1.com.
    companion_url: str | None = None


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
    requires_authentication: bool = False
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
    response: Response,
    service: Annotated[LiveService, Depends(get_live_service)],
) -> LiveStatusResponse:
    """Start collecting. The feed states which session it is."""
    response.headers["Cache-Control"] = "no-store"
    try:
        await service.start_session()
    except LiveFeedUnconfiguredError:
        raise ApiError(
            status_code=503,
            code="live_feed_unconfigured",
            message="No live timing feed provider is configured.",
        ) from None
    except LiveUnauthenticatedError:
        raise ApiError(
            status_code=403,
            code="live_not_authenticated",
            message="Connect an F1 TV account before starting a live session.",
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


class RecordingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    event_name: str
    session_key: str
    session_date: str | None = None
    size_bytes: int
    modified_at: str


class RecordingListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_state: str = RECORD_STATE
    retention_days: int
    items: list[RecordingResponse]


class ReplayRequest(BaseModel):
    """A recording to replay. The name is validated against the log directory."""

    model_config = ConfigDict(extra="forbid")

    name: str
    #: Bounded so a replay cannot be asked to emit a whole session in one tick,
    #: which would starve the event loop the live path shares.
    speed: float | None = Field(default=None, gt=0, le=MAX_REPLAY_SPEED)


@router.get(
    "/recordings",
    response_model=RecordingListResponse,
    summary="List recorded sessions available for replay",
)
def read_live_recordings(
    response: Response,
    service: Annotated[LiveService, Depends(get_live_service)],
) -> RecordingListResponse:
    response.headers["Cache-Control"] = "no-store"
    return RecordingListResponse(
        retention_days=service.settings.retention_days,
        items=[
            RecordingResponse(**recording.as_dict())
            for recording in service.recordings()
        ],
    )


@router.post(
    "/replay",
    response_model=LiveStatusResponse,
    summary="Replay a recorded session through the live pipeline",
)
async def start_live_replay(
    payload: ReplayRequest,
    response: Response,
    service: Annotated[LiveService, Depends(get_live_service)],
) -> LiveStatusResponse:
    """Replay is read-only: it writes no session log and needs no F1 TV token."""
    response.headers["Cache-Control"] = "no-store"
    try:
        await service.start_replay(payload.name, speed=payload.speed)
    except RecordingNotFoundError:
        raise ApiError(
            status_code=404,
            code="recording_not_found",
            message="No such recording is available for replay.",
        ) from None
    except LiveSessionBusyError:
        raise ApiError(
            status_code=409,
            code="live_session_busy",
            message="Stop the running session before starting a replay.",
        ) from None
    return _status(service)


def _auth_status(service: LiveService) -> AuthStatusResponse:
    return AuthStatusResponse(**service.authentication_status())


def _allow_extension_origin(response: Response) -> None:
    """Permit the companion extension's cross-origin POST.

    The extension fetches ``http://localhost:{port}/auth`` from its own
    extension origin, which is a different origin and triggers a preflight.
    Extension origins are per-install identifiers that cannot be pinned, so any
    origin is allowed on this route only, exactly as FastF1's own local auth
    server does. The API is bound to loopback and this route returns no
    sensitive data; the residual risk is that a page could plant a token, which
    the user can clear by signing out.
    """
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"


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
    return _auth_status(service)


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
    return _auth_status(service)


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
    return _auth_status(service)


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
    _allow_extension_origin(response)
    return _save_token(service, await read_login_session(request))


@compat_router.options(
    "/auth",
    include_in_schema=False,
    summary="Preflight for the companion extension",
)
def preflight_live_auth_compat(response: Response) -> Response:
    _allow_extension_origin(response)
    response.status_code = 200
    return response


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

    def board_message(kind: str) -> dict[str, object]:
        return {
            "type": kind,
            "record_state": RECORD_STATE,
            "session": collector.status()["session"],
            "board": board_to_dict(
                build_board(
                    {
                        topic: state.payload
                        for topic, state in collector.view.topics.items()
                    },
                    positions=collector.positions,
                )
            ),
        }

    try:
        await websocket.send_json(board_message("snapshot"))
        while True:
            await queue.get()
            # Drain whatever else arrived; one rebuilt board reflects them all.
            while not queue.empty():
                queue.get_nowait()
            await websocket.send_json(board_message("board"))
            await asyncio.sleep(BOARD_MIN_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        pass
    finally:
        collector.unsubscribe(queue)
