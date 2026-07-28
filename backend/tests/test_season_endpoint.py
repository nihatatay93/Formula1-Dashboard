from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from app.api.contracts import (
    SeasonCounts,
    SeasonCoverage,
    SeasonOverviewResponse,
    SeasonStatus,
)
from app.api.dependencies import get_database_session_factory
from app.api.errors import ApiError
from app.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _missing_overview(year: int) -> SeasonOverviewResponse:
    return SeasonOverviewResponse(
        year=year,
        status=SeasonStatus.MISSING,
        coverage=SeasonCoverage(
            checked_at=None,
            valid_until=None,
            is_stale=True,
        ),
        counts=SeasonCounts(
            events=0,
            sessions=0,
            archive_eligible=0,
            data_available=0,
            pending=0,
            running=0,
            completed=0,
            failed=0,
        ),
        active_job=None,
        events=(),
    )


def test_season_endpoint_returns_contract_and_disables_caching(
    client: TestClient,
    monkeypatch,
) -> None:
    sentinel_factory = object()
    calls: list[tuple[int, object]] = []
    app.dependency_overrides[get_database_session_factory] = (
        lambda: sentinel_factory
    )

    def stub_read_season_overview(*, season_year, session_factory):
        calls.append((season_year, session_factory))
        return _missing_overview(season_year)

    monkeypatch.setattr(
        "app.api.seasons.read_season_overview",
        stub_read_season_overview,
    )

    response = client.get("/api/v1/seasons/2024")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "year": 2024,
        "status": "missing",
        "coverage": {
            "checked_at": None,
            "valid_until": None,
            "is_stale": True,
        },
        "counts": {
            "events": 0,
            "sessions": 0,
            "archive_eligible": 0,
            "data_available": 0,
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
        },
        "active_job": None,
        "events": [],
        "deferred_future_events": [],
    }
    assert calls == [(2024, sentinel_factory)]


@pytest.mark.parametrize("season_year", [2017, datetime.now(UTC).year + 1])
def test_season_endpoint_rejects_out_of_range_year_with_stable_error(
    client: TestClient,
    season_year: int,
) -> None:
    app.dependency_overrides[get_database_session_factory] = lambda: object()

    response = client.get(f"/api/v1/seasons/{season_year}")

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "season_year_out_of_range",
            "message": "Season year is outside the supported range.",
        }
    }


def test_season_endpoint_keeps_fastapi_validation_for_malformed_year(
    client: TestClient,
) -> None:
    app.dependency_overrides[get_database_session_factory] = lambda: object()

    response = client.get("/api/v1/seasons/not-a-year")

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_season_endpoint_maps_database_failure_without_leaking_details(
    client: TestClient,
    monkeypatch,
) -> None:
    app.dependency_overrides[get_database_session_factory] = lambda: object()

    def fail_read_season_overview(**_kwargs):
        raise SQLAlchemyError("RAW-DATABASE-ERROR-SENTINEL")

    monkeypatch.setattr(
        "app.api.seasons.read_season_overview",
        fail_read_season_overview,
    )

    response = client.get("/api/v1/seasons/2024")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "database_unavailable",
            "message": "The database is temporarily unavailable.",
        }
    }
    assert "RAW-DATABASE-ERROR-SENTINEL" not in response.text


@pytest.mark.parametrize("error_type", [RuntimeError, ArgumentError])
def test_database_session_factory_maps_invalid_configuration(
    monkeypatch,
    error_type,
) -> None:
    def invalid_configuration():
        raise error_type("RAW-CONFIGURATION-ERROR-SENTINEL")

    monkeypatch.setattr(
        "app.api.dependencies.create_session_factory",
        invalid_configuration,
    )

    with pytest.raises(ApiError) as raised:
        get_database_session_factory()

    assert raised.value.status_code == 500
    assert raised.value.detail == {
        "code": "server_configuration_error",
        "message": "Server database configuration is invalid.",
    }


def test_openapi_documents_season_response_and_stable_errors() -> None:
    operation = app.openapi()["paths"]["/api/v1/seasons/{season_year}"]["get"]

    assert operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/SeasonOverviewResponse"}
    assert operation["responses"]["500"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/ErrorResponse"}
    assert operation["responses"]["503"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/ErrorResponse"}
