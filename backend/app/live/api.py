"""HTTP and WebSocket surface for live timing.

A separate ``/api/v1/live`` namespace. The historical endpoints are untouched and
continue to serve only finalized archive data, and nothing here reads or writes
PostgreSQL. Responses are explicitly marked unconfirmed so a reader never
mistakes live data for the archive record.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from app.api.errors import ApiError
from app.live.collector import LiveSessionIdentity
from app.live.service import (
    LiveFeedUnconfiguredError,
    LiveService,
    LiveSessionConflictError,
)
from app.live.state import get_live_service

router = APIRouter(prefix="/live", tags=["live"])

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


class LiveStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_state: str = RECORD_STATE
    active: bool
    feed_configured: bool
    retention_days: int
    log_directory_bytes: int
    max_directory_bytes: int
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
