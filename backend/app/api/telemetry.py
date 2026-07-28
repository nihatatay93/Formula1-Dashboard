from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.api.contracts import (
    EnsureLapTelemetryResponse,
    ErrorResponse,
    LapTelemetryResponse,
)
from app.api.dependencies import get_database_session_factory
from app.api.errors import ApiError
from app.api.telemetry_data import (
    TelemetryNotRequestedError,
    read_lap_telemetry,
)
from app.ingestion.telemetry_ingestion import (
    TelemetryDataUnavailableError,
    TelemetryTargetNotFoundError,
    ensure_lap_telemetry,
)

router = APIRouter(prefix="/sessions", tags=["telemetry"])
PositiveId = Annotated[int, Path(ge=1)]
DatabaseSessionFactory = Annotated[
    sessionmaker[Session],
    Depends(get_database_session_factory),
]
_ERROR = {"model": ErrorResponse}


@router.post(
    "/{session_id}/entries/{session_entry_id}/laps/{lap_number}/telemetry",
    response_model=EnsureLapTelemetryResponse,
    responses={404: _ERROR, 409: _ERROR, 503: _ERROR},
    summary="Ensure bounded historical lap telemetry",
)
def post_lap_telemetry(
    response: Response,
    session_id: PositiveId,
    session_entry_id: PositiveId,
    lap_number: PositiveId,
    session_factory: DatabaseSessionFactory,
) -> EnsureLapTelemetryResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        with session_factory() as database:
            result = ensure_lap_telemetry(
                database,
                session_id=session_id,
                session_entry_id=session_entry_id,
                lap_number=lap_number,
            )
    except TelemetryTargetNotFoundError:
        raise _not_found() from None
    except TelemetryDataUnavailableError:
        raise _unavailable() from None
    except SQLAlchemyError:
        raise _database_unavailable() from None

    location = (
        f"/api/v1/sessions/{session_id}/entries/{session_entry_id}/"
        f"laps/{lap_number}/telemetry"
    )
    response.headers["Location"] = location
    if result.action == "available":
        response.status_code = 200
    else:
        response.status_code = 202
        response.headers["Retry-After"] = "2"
    return EnsureLapTelemetryResponse(
        session_id=session_id,
        session_entry_id=session_entry_id,
        lap_id=result.lap_id,
        lap_number=lap_number,
        action=result.action,
        status=result.status,
        source_snapshot_completed_at=(
            result.source_snapshot_completed_at
        ),
    )


@router.get(
    "/{session_id}/entries/{session_entry_id}/laps/{lap_number}/telemetry",
    response_model=LapTelemetryResponse,
    responses={404: _ERROR, 409: _ERROR, 503: _ERROR},
    summary="Read bounded historical lap telemetry",
)
def get_lap_telemetry(
    response: Response,
    session_id: PositiveId,
    session_entry_id: PositiveId,
    lap_number: PositiveId,
    session_factory: DatabaseSessionFactory,
    after_sample: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> LapTelemetryResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return read_lap_telemetry(
            session_id=session_id,
            session_entry_id=session_entry_id,
            lap_number=lap_number,
            after_sample=after_sample,
            limit=limit,
            session_factory=session_factory,
        )
    except TelemetryTargetNotFoundError:
        raise _not_found() from None
    except TelemetryDataUnavailableError:
        raise _unavailable() from None
    except TelemetryNotRequestedError:
        raise ApiError(
            status_code=409,
            code="telemetry_not_requested",
            message="Telemetry has not been requested for this lap.",
        ) from None
    except SQLAlchemyError:
        raise _database_unavailable() from None


def _not_found() -> ApiError:
    return ApiError(
        status_code=404,
        code="lap_not_found",
        message="The requested session entry lap was not found.",
    )


def _unavailable() -> ApiError:
    return ApiError(
        status_code=409,
        code="session_data_unavailable",
        message="Historical data is not available for this session.",
    )


def _database_unavailable() -> ApiError:
    return ApiError(
        status_code=503,
        code="database_unavailable",
        message="The database is temporarily unavailable.",
    )
