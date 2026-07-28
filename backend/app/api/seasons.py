from datetime import UTC, datetime
from math import ceil
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.api.contracts import (
    ActiveJobSummary,
    BackfillAction,
    BackfillCoverage,
    EnsureBackfillResponse,
    ErrorResponse,
    SeasonOverviewResponse,
)
from app.api.dependencies import (
    get_database_session_factory,
    get_fastf1_schedule_loader,
    require_supported_season_year,
)
from app.api.errors import ApiError
from app.api.season_overview import read_season_overview
from app.ingestion.fastf1_loader import FastF1LoaderConfigurationError
from app.ingestion.fastf1_schedule import (
    FastF1ScheduleLoaderProtocol,
    FastF1ScheduleLoadError,
    FastF1ScheduleNormalizationError,
)
from app.ingestion.request_budget_errors import (
    FastF1RequestBudgetExhaustedError,
)
from app.ingestion.season_backfill import (
    SeasonBackfillError,
    SeasonBackfillPlan,
    SeasonBackfillSnapshotError,
    SeasonBackfillSourceConflictError,
    ensure_season_backfill,
)

router = APIRouter(prefix="/seasons", tags=["seasons"])


@router.post(
    "/{season_year}/backfill",
    response_model=EnsureBackfillResponse,
    responses={
        202: {
            "model": EnsureBackfillResponse,
            "description": "A backfill job was created or reused.",
            "headers": {
                "Location": {
                    "description": "Relative URL of the active backfill job.",
                    "schema": {"type": "string"},
                },
                "Retry-After": {
                    "description": "Suggested polling delay in seconds.",
                    "schema": {"type": "integer"},
                },
            },
        },
        409: {
            "model": ErrorResponse,
            "description": "Stored state conflicts with backfill planning.",
        },
        429: {
            "model": ErrorResponse,
            "description": "The local FastF1 request budget is paused.",
            "headers": {
                "Retry-After": {
                    "description": (
                        "Seconds until the rolling request budget has capacity."
                    ),
                    "schema": {"type": "integer"},
                },
            },
        },
        500: {
            "model": ErrorResponse,
            "description": "Server configuration is invalid.",
        },
        502: {
            "model": ErrorResponse,
            "description": "The upstream schedule snapshot is invalid.",
        },
        503: {
            "model": ErrorResponse,
            "description": "Schedule or database access is unavailable.",
        },
    },
    summary="Ensure historical season backfill",
)
def post_season_backfill(
    response: Response,
    season_year: Annotated[int, Depends(require_supported_season_year)],
    session_factory: Annotated[
        sessionmaker[Session],
        Depends(get_database_session_factory),
    ],
    schedule_loader: Annotated[
        FastF1ScheduleLoaderProtocol,
        Depends(get_fastf1_schedule_loader),
    ],
) -> EnsureBackfillResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        plan = ensure_season_backfill(
            season_year=season_year,
            session_factory=session_factory,
            schedule_loader=schedule_loader,
        )
    except FastF1RequestBudgetExhaustedError as error:
        retry_seconds = max(
            1,
            ceil((error.retry_at - datetime.now(UTC)).total_seconds()),
        )
        raise ApiError(
            status_code=429,
            code="fastf1_request_budget_paused",
            message="FastF1 requests are paused by the local safety budget.",
            headers={"Retry-After": str(retry_seconds)},
        ) from None
    except SeasonBackfillSourceConflictError:
        raise ApiError(
            status_code=409,
            code="calendar_source_conflict",
            message="Stored calendar data belongs to another source.",
        ) from None
    except SeasonBackfillSnapshotError:
        raise ApiError(
            status_code=502,
            code="invalid_schedule_snapshot",
            message="The upstream season schedule is invalid.",
        ) from None
    except FastF1ScheduleNormalizationError:
        raise ApiError(
            status_code=502,
            code="invalid_schedule_snapshot",
            message="The upstream season schedule is invalid.",
        ) from None
    except FastF1ScheduleLoadError:
        raise ApiError(
            status_code=503,
            code="schedule_unavailable",
            message="Season schedule data is temporarily unavailable.",
        ) from None
    except FastF1LoaderConfigurationError:
        raise ApiError(
            status_code=500,
            code="server_configuration_error",
            message="Server cache configuration is invalid.",
        ) from None
    except SeasonBackfillError:
        raise ApiError(
            status_code=409,
            code="season_planning_conflict",
            message="Season backfill planning could not be completed safely.",
        ) from None
    except SQLAlchemyError:
        raise ApiError(
            status_code=503,
            code="database_unavailable",
            message="The database is temporarily unavailable.",
        ) from None

    payload = _backfill_response(plan)
    if payload.job is not None:
        response.status_code = 202
        response.headers["Location"] = (
            f"/api/v1/backfill-jobs/{payload.job.id}"
        )
        response.headers["Retry-After"] = "2"
    return payload


@router.get(
    "/{season_year}",
    response_model=SeasonOverviewResponse,
    responses={
        500: {
            "model": ErrorResponse,
            "description": "Server configuration is invalid.",
        },
        503: {
            "model": ErrorResponse,
            "description": "The database is temporarily unavailable.",
        },
    },
    summary="Read a historical season overview",
)
def get_season_overview(
    response: Response,
    season_year: Annotated[int, Depends(require_supported_season_year)],
    session_factory: Annotated[
        sessionmaker[Session],
        Depends(get_database_session_factory),
    ],
) -> SeasonOverviewResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return read_season_overview(
            season_year=season_year,
            session_factory=session_factory,
        )
    except SQLAlchemyError:
        raise ApiError(
            status_code=503,
            code="database_unavailable",
            message="The database is temporarily unavailable.",
        ) from None


def _backfill_response(plan: SeasonBackfillPlan) -> EnsureBackfillResponse:
    if plan.job_id is not None:
        action = (
            BackfillAction.JOB_CREATED
            if plan.job_created
            else BackfillAction.JOB_REUSED
        )
        job = ActiveJobSummary(
            id=plan.job_id,
            status=plan.job_status,
        )
    else:
        action = (
            BackfillAction.COVERAGE_REFRESHED
            if plan.coverage_refreshed
            else BackfillAction.NO_ACTION
        )
        job = None

    return EnsureBackfillResponse(
        season_year=plan.season_year,
        action=action,
        coverage=BackfillCoverage(
            refresh_reason=plan.coverage_reason,
            refreshed=plan.coverage_refreshed,
            checked_at=plan.coverage_checked_at,
            valid_until=plan.coverage_valid_until,
        ),
        job=job,
        eligible_session_count=len(plan.eligible_session_ids),
        newly_queued_session_count=len(plan.newly_queued_session_ids),
        deferred_future_events=tuple(
            {
                "round_number": event.round_number,
                "event_name": event.event_name,
                "scheduled_start_at": event.scheduled_start_at,
            }
            for event in plan.deferred_future_events
        ),
    )
