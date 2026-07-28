from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import ApiError
from app.db.session import create_session_factory
from app.ingestion.fastf1_loader import FastF1LoaderConfigurationError
from app.ingestion.fastf1_schedule import (
    FastF1ScheduleLoaderProtocol,
    create_fastf1_schedule_loader,
)
from app.ingestion.request_budget import FastF1RequestBudget
from app.ingestion.runtime_policy import BackfillRuntimeSettings

MINIMUM_SEASON_YEAR = 2018


class SeasonYearOutOfRangeError(ValueError):
    """Raised when an API season year falls outside the supported range."""


def validate_supported_season_year(
    season_year: int,
    *,
    current_year: int | None = None,
) -> int:
    resolved_current_year = current_year or datetime.now(UTC).year
    if (
        isinstance(season_year, bool)
        or not isinstance(season_year, int)
        or season_year < MINIMUM_SEASON_YEAR
        or season_year > resolved_current_year
    ):
        raise SeasonYearOutOfRangeError(
            "season year is outside the supported range"
        )
    return season_year


def require_supported_season_year(season_year: int) -> int:
    try:
        return validate_supported_season_year(season_year)
    except SeasonYearOutOfRangeError:
        raise ApiError(
            status_code=422,
            code="season_year_out_of_range",
            message="Season year is outside the supported range.",
        ) from None


def get_database_session_factory() -> sessionmaker[Session]:
    try:
        return create_session_factory()
    except (ArgumentError, RuntimeError):
        raise ApiError(
            status_code=500,
            code="server_configuration_error",
            message="Server database configuration is invalid.",
        ) from None


def get_fastf1_schedule_loader(
    session_factory: Annotated[
        sessionmaker[Session],
        Depends(get_database_session_factory),
    ],
) -> FastF1ScheduleLoaderProtocol:
    try:
        request_budget = FastF1RequestBudget(
            session_factory=session_factory,
            operation="schedule",
            settings=BackfillRuntimeSettings.from_environment(),
        )
        return create_fastf1_schedule_loader(
            request_budget=request_budget,
        )
    except FastF1LoaderConfigurationError:
        raise ApiError(
            status_code=500,
            code="server_configuration_error",
            message="Server cache configuration is invalid.",
        ) from None
