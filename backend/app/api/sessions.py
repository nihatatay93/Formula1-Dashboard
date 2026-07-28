from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.api.contracts import (
    ErrorResponse,
    LapSummaryQuery,
    LapSummaryResponse,
    SessionDetailResponse,
    SessionResultsResponse,
)
from app.api.dependencies import get_database_session_factory
from app.api.errors import ApiError
from app.api.session_data import (
    SessionDataUnavailableError,
    SessionEntryNotFoundError,
    SessionNotFoundError,
    read_session_detail,
    read_session_laps,
    read_session_results,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])

SessionId = Annotated[int, Path(ge=1)]
SessionEntryId = Annotated[int, Path(ge=1)]
DatabaseSessionFactory = Annotated[
    sessionmaker[Session],
    Depends(get_database_session_factory),
]

_CONFIGURATION_ERROR_RESPONSE = {
    "model": ErrorResponse,
    "description": "Server configuration is invalid.",
}
_DATABASE_ERROR_RESPONSE = {
    "model": ErrorResponse,
    "description": "The database is temporarily unavailable.",
}
_SESSION_NOT_FOUND_RESPONSE = {
    "model": ErrorResponse,
    "description": "The historical session does not exist.",
}
_SESSION_ENTRY_NOT_FOUND_RESPONSE = {
    "model": ErrorResponse,
    "description": "The session or session entry does not exist.",
}
_SESSION_DATA_UNAVAILABLE_RESPONSE = {
    "model": ErrorResponse,
    "description": "The session has no completed historical snapshot.",
}


def _lap_summary_query(
    after_lap: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    lap_from: Annotated[int | None, Query(ge=1)] = None,
    lap_to: Annotated[int | None, Query(ge=1)] = None,
    stint_number: Annotated[int | None, Query(ge=1)] = None,
    include_deleted: bool = True,
) -> LapSummaryQuery:
    if (
        lap_from is not None
        and lap_to is not None
        and lap_from > lap_to
    ):
        raise ApiError(
            status_code=422,
            code="invalid_lap_range",
            message="The requested lap range is invalid.",
        )
    return LapSummaryQuery(
        after_lap=after_lap,
        limit=limit,
        lap_from=lap_from,
        lap_to=lap_to,
        stint_number=stint_number,
        include_deleted=include_deleted,
    )


@router.get(
    "/{session_id}",
    response_model=SessionDetailResponse,
    responses={
        404: _SESSION_NOT_FOUND_RESPONSE,
        500: _CONFIGURATION_ERROR_RESPONSE,
        503: _DATABASE_ERROR_RESPONSE,
    },
    summary="Read historical session detail",
)
def get_session_detail(
    response: Response,
    session_id: SessionId,
    session_factory: DatabaseSessionFactory,
) -> SessionDetailResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return read_session_detail(
            session_id=session_id,
            session_factory=session_factory,
        )
    except SessionNotFoundError:
        raise _session_not_found_error() from None
    except SQLAlchemyError:
        raise _database_unavailable_error() from None


@router.get(
    "/{session_id}/results",
    response_model=SessionResultsResponse,
    responses={
        404: _SESSION_NOT_FOUND_RESPONSE,
        409: _SESSION_DATA_UNAVAILABLE_RESPONSE,
        500: _CONFIGURATION_ERROR_RESPONSE,
        503: _DATABASE_ERROR_RESPONSE,
    },
    summary="Read historical session results",
)
def get_session_results(
    response: Response,
    session_id: SessionId,
    session_factory: DatabaseSessionFactory,
) -> SessionResultsResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return read_session_results(
            session_id=session_id,
            session_factory=session_factory,
        )
    except SessionNotFoundError:
        raise _session_not_found_error() from None
    except SessionDataUnavailableError:
        raise _session_data_unavailable_error() from None
    except SQLAlchemyError:
        raise _database_unavailable_error() from None


@router.get(
    "/{session_id}/entries/{session_entry_id}/laps",
    response_model=LapSummaryResponse,
    responses={
        404: _SESSION_ENTRY_NOT_FOUND_RESPONSE,
        409: _SESSION_DATA_UNAVAILABLE_RESPONSE,
        422: {
            "description": "Request validation or lap-range failure.",
            "content": {
                "application/json": {
                    "schema": {
                        "oneOf": [
                            {
                                "$ref": (
                                    "#/components/schemas/"
                                    "HTTPValidationError"
                                )
                            },
                            {
                                "$ref": (
                                    "#/components/schemas/ErrorResponse"
                                )
                            },
                        ]
                    }
                }
            },
        },
        500: _CONFIGURATION_ERROR_RESPONSE,
        503: _DATABASE_ERROR_RESPONSE,
    },
    summary="Read historical lap summaries for a session entry",
)
def get_session_laps(
    response: Response,
    session_id: SessionId,
    session_entry_id: SessionEntryId,
    query: Annotated[LapSummaryQuery, Depends(_lap_summary_query)],
    session_factory: DatabaseSessionFactory,
) -> LapSummaryResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return read_session_laps(
            session_id=session_id,
            session_entry_id=session_entry_id,
            query=query,
            session_factory=session_factory,
        )
    except SessionNotFoundError:
        raise _session_not_found_error() from None
    except SessionEntryNotFoundError:
        raise ApiError(
            status_code=404,
            code="session_entry_not_found",
            message="Session entry was not found.",
        ) from None
    except SessionDataUnavailableError:
        raise _session_data_unavailable_error() from None
    except SQLAlchemyError:
        raise _database_unavailable_error() from None


def _session_not_found_error() -> ApiError:
    return ApiError(
        status_code=404,
        code="session_not_found",
        message="Historical session was not found.",
    )


def _session_data_unavailable_error() -> ApiError:
    return ApiError(
        status_code=409,
        code="session_data_unavailable",
        message="Historical data is not available for this session.",
    )


def _database_unavailable_error() -> ApiError:
    return ApiError(
        status_code=503,
        code="database_unavailable",
        message="The database is temporarily unavailable.",
    )
