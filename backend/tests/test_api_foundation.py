import pytest

from app.api.dependencies import (
    SeasonYearOutOfRangeError,
    require_supported_season_year,
    validate_supported_season_year,
)
from app.api.errors import ApiError
from app.api.router import api_v1_router
from app.main import app


def test_versioned_router_uses_accepted_prefix() -> None:
    assert api_v1_router.prefix == "/api/v1"


def test_versioned_router_is_mounted_without_changing_health_paths() -> None:
    paths = app.openapi()["paths"]

    assert "/" in paths
    assert "/api/health/live" in paths
    assert "/api/health/ready" in paths


@pytest.mark.parametrize("season_year", [2018, 2026])
def test_supported_season_year_boundaries(season_year: int) -> None:
    assert (
        validate_supported_season_year(season_year, current_year=2026)
        == season_year
    )


@pytest.mark.parametrize("season_year", [True, 2017, 2027])
def test_unsupported_season_years_are_rejected(season_year: int) -> None:
    with pytest.raises(
        SeasonYearOutOfRangeError,
        match="season year is outside the supported range",
    ):
        validate_supported_season_year(season_year, current_year=2026)


def test_season_year_dependency_raises_stable_api_error() -> None:
    with pytest.raises(ApiError) as raised:
        require_supported_season_year(2017)

    assert raised.value.status_code == 422
    assert raised.value.detail == {
        "code": "season_year_out_of_range",
        "message": "Season year is outside the supported range.",
    }

