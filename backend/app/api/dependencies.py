from datetime import UTC, datetime

from app.api.errors import ApiError

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

