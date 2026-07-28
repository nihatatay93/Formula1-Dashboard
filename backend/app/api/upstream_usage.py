from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.api.contracts import (
    ErrorResponse,
    FastF1RequestBudgetResponse,
)
from app.api.dependencies import get_database_session_factory
from app.api.errors import ApiError
from app.ingestion.request_budget import read_fastf1_request_budget
from app.ingestion.runtime_policy import (
    BackfillRuntimePolicyError,
    BackfillRuntimeSettings,
)

router = APIRouter(prefix="/upstreams", tags=["upstreams"])


@router.get(
    "/fastf1/usage",
    response_model=FastF1RequestBudgetResponse,
    responses={
        500: {
            "model": ErrorResponse,
            "description": "Server policy configuration is invalid.",
        },
        503: {
            "model": ErrorResponse,
            "description": "The database is temporarily unavailable.",
        },
    },
    summary="Read local FastF1 request-budget usage",
)
def get_fastf1_usage(
    response: Response,
    session_factory: Annotated[
        sessionmaker[Session],
        Depends(get_database_session_factory),
    ],
) -> FastF1RequestBudgetResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        settings = BackfillRuntimeSettings.from_environment()
        snapshot = read_fastf1_request_budget(
            session_factory=session_factory,
            settings=settings,
        )
    except BackfillRuntimePolicyError:
        raise ApiError(
            status_code=500,
            code="server_configuration_error",
            message="Server request-budget configuration is invalid.",
        ) from None
    except SQLAlchemyError:
        raise ApiError(
            status_code=503,
            code="database_unavailable",
            message="The database is temporarily unavailable.",
        ) from None

    return FastF1RequestBudgetResponse(
        source="fastf1",
        window_seconds=settings.fastf1_request_window_seconds,
        observed_at=snapshot.observed_at,
        observed_requests=snapshot.observed_requests,
        archive_requests=snapshot.archive_requests,
        schedule_requests=snapshot.schedule_requests,
        telemetry_requests=snapshot.telemetry_requests,
        library_limit=snapshot.library_limit,
        operational_ceiling=snapshot.operational_ceiling,
        warning_threshold=snapshot.warning_threshold,
        remaining_before_pause=snapshot.remaining_before_pause,
        next_capacity_at=snapshot.next_capacity_at,
        cooldown_until=snapshot.cooldown_until,
        cooldown_reason=snapshot.cooldown_reason,
        status=snapshot.status,
        authoritative=False,
    )
