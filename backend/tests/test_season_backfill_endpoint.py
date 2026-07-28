import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import (
    get_database_session_factory,
    get_fastf1_schedule_loader,
)
from app.ingestion.fastf1_loader import FastF1LoaderConfigurationError
from app.ingestion.fastf1_schedule import (
    FastF1ScheduleLoadError,
    FastF1ScheduleNormalizationError,
)
from app.ingestion.freshness_policy import CoverageRefreshReason
from app.ingestion.season_backfill import (
    SeasonBackfillError,
    SeasonBackfillPlan,
    SeasonBackfillSnapshotError,
    SeasonBackfillSourceConflictError,
)
from app.main import app

JOB_ID = uuid.UUID("3e18c9fd-a8eb-458f-b317-55867afdc53f")
CHECKED_AT = datetime(2026, 7, 28, 12, tzinfo=UTC)
VALID_UNTIL = CHECKED_AT + timedelta(days=30)


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _plan(
    *,
    job_id: uuid.UUID | None = JOB_ID,
    job_status: str | None = "pending",
    job_created: bool = True,
    coverage_reason: CoverageRefreshReason = CoverageRefreshReason.MISSING,
    coverage_refreshed: bool = True,
    newly_queued_session_ids: tuple[int, ...] = (101, 102),
) -> SeasonBackfillPlan:
    return SeasonBackfillPlan(
        season_year=2024,
        coverage_reason=coverage_reason,
        coverage_refreshed=coverage_refreshed,
        coverage_checked_at=CHECKED_AT,
        coverage_valid_until=VALID_UNTIL,
        job_id=job_id,
        job_status=job_status,
        job_created=job_created,
        eligible_session_ids=(101, 102),
        newly_queued_session_ids=newly_queued_session_ids,
    )


def _override_command_dependencies() -> tuple[object, object]:
    sentinel_factory = object()
    sentinel_loader = object()
    app.dependency_overrides[get_database_session_factory] = (
        lambda: sentinel_factory
    )
    app.dependency_overrides[get_fastf1_schedule_loader] = (
        lambda: sentinel_loader
    )
    return sentinel_factory, sentinel_loader


def test_backfill_endpoint_returns_accepted_job_contract_and_headers(
    client: TestClient,
    monkeypatch,
) -> None:
    sentinel_factory, sentinel_loader = _override_command_dependencies()
    calls: list[tuple[int, object, object]] = []

    def stub_ensure_season_backfill(
        *,
        season_year,
        session_factory,
        schedule_loader,
    ):
        calls.append((season_year, session_factory, schedule_loader))
        return _plan()

    monkeypatch.setattr(
        "app.api.seasons.ensure_season_backfill",
        stub_ensure_season_backfill,
    )

    response = client.post("/api/v1/seasons/2024/backfill")

    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["location"] == f"/api/v1/backfill-jobs/{JOB_ID}"
    assert response.headers["retry-after"] == "2"
    assert response.json() == {
        "season_year": 2024,
        "action": "job_created",
        "coverage": {
            "refresh_reason": "missing",
            "refreshed": True,
            "checked_at": "2026-07-28T12:00:00Z",
            "valid_until": "2026-08-27T12:00:00Z",
        },
        "job": {
            "id": str(JOB_ID),
            "status": "pending",
        },
        "eligible_session_count": 2,
        "newly_queued_session_count": 2,
    }
    assert calls == [(2024, sentinel_factory, sentinel_loader)]


@pytest.mark.parametrize(
    ("plan", "expected_status", "expected_action", "has_job"),
    [
        (
            _plan(
                job_created=False,
                coverage_reason=CoverageRefreshReason.FRESH,
                coverage_refreshed=False,
                newly_queued_session_ids=(),
            ),
            202,
            "job_reused",
            True,
        ),
        (
            _plan(
                job_id=None,
                job_status=None,
                job_created=False,
                newly_queued_session_ids=(),
            ),
            200,
            "coverage_refreshed",
            False,
        ),
        (
            _plan(
                job_id=None,
                job_status=None,
                job_created=False,
                coverage_reason=CoverageRefreshReason.FRESH,
                coverage_refreshed=False,
                newly_queued_session_ids=(),
            ),
            200,
            "no_action",
            False,
        ),
    ],
)
def test_backfill_endpoint_maps_planner_actions(
    client: TestClient,
    monkeypatch,
    plan: SeasonBackfillPlan,
    expected_status: int,
    expected_action: str,
    has_job: bool,
) -> None:
    _override_command_dependencies()
    monkeypatch.setattr(
        "app.api.seasons.ensure_season_backfill",
        lambda **_kwargs: plan,
    )

    response = client.post("/api/v1/seasons/2024/backfill")

    assert response.status_code == expected_status
    assert response.json()["action"] == expected_action
    assert (response.json()["job"] is not None) is has_job
    assert ("location" in response.headers) is has_job
    assert ("retry-after" in response.headers) is has_job


@pytest.mark.parametrize(
    ("error", "status_code", "code", "message"),
    [
        (
            SeasonBackfillSourceConflictError("RAW-SOURCE-CONFLICT"),
            409,
            "calendar_source_conflict",
            "Stored calendar data belongs to another source.",
        ),
        (
            SeasonBackfillError("RAW-PLANNING-CONFLICT"),
            409,
            "season_planning_conflict",
            "Season backfill planning could not be completed safely.",
        ),
        (
            SeasonBackfillSnapshotError("RAW-SNAPSHOT-ERROR"),
            502,
            "invalid_schedule_snapshot",
            "The upstream season schedule is invalid.",
        ),
        (
            FastF1ScheduleNormalizationError("RAW-NORMALIZATION-ERROR"),
            502,
            "invalid_schedule_snapshot",
            "The upstream season schedule is invalid.",
        ),
        (
            FastF1ScheduleLoadError("RAW-SCHEDULE-ERROR"),
            503,
            "schedule_unavailable",
            "Season schedule data is temporarily unavailable.",
        ),
        (
            FastF1LoaderConfigurationError("RAW-CACHE-ERROR"),
            500,
            "server_configuration_error",
            "Server cache configuration is invalid.",
        ),
        (
            SQLAlchemyError("RAW-DATABASE-ERROR"),
            503,
            "database_unavailable",
            "The database is temporarily unavailable.",
        ),
    ],
)
def test_backfill_endpoint_maps_failures_without_leaking_details(
    client: TestClient,
    monkeypatch,
    error: Exception,
    status_code: int,
    code: str,
    message: str,
) -> None:
    _override_command_dependencies()

    def fail_planning(**_kwargs):
        raise error

    monkeypatch.setattr(
        "app.api.seasons.ensure_season_backfill",
        fail_planning,
    )

    response = client.post("/api/v1/seasons/2024/backfill")

    assert response.status_code == status_code
    assert response.json() == {
        "detail": {
            "code": code,
            "message": message,
        }
    }
    assert "RAW-" not in response.text


@pytest.mark.parametrize("season_year", [2017, datetime.now(UTC).year + 1])
def test_backfill_endpoint_rejects_unsupported_year_before_planning(
    client: TestClient,
    monkeypatch,
    season_year: int,
) -> None:
    _override_command_dependencies()

    def forbidden_planning(**_kwargs):
        raise AssertionError("planner must not be called")

    monkeypatch.setattr(
        "app.api.seasons.ensure_season_backfill",
        forbidden_planning,
    )

    response = client.post(f"/api/v1/seasons/{season_year}/backfill")

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "season_year_out_of_range",
            "message": "Season year is outside the supported range.",
        }
    }


def test_schedule_loader_dependency_maps_invalid_cache_configuration(
    client: TestClient,
    monkeypatch,
) -> None:
    app.dependency_overrides[get_database_session_factory] = lambda: object()

    def invalid_configuration():
        raise FastF1LoaderConfigurationError(
            "RAW-CACHE-CONFIGURATION-SENTINEL"
        )

    monkeypatch.setattr(
        "app.api.dependencies.create_fastf1_schedule_loader",
        invalid_configuration,
    )

    response = client.post("/api/v1/seasons/2024/backfill")

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "code": "server_configuration_error",
            "message": "Server cache configuration is invalid.",
        }
    }
    assert "RAW-CACHE-CONFIGURATION-SENTINEL" not in response.text


def test_openapi_documents_backfill_command_responses_and_headers() -> None:
    operation = app.openapi()["paths"][
        "/api/v1/seasons/{season_year}/backfill"
    ]["post"]

    assert operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/EnsureBackfillResponse"}
    assert operation["responses"]["202"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/EnsureBackfillResponse"}
    assert set(operation["responses"]["202"]["headers"]) == {
        "Location",
        "Retry-After",
    }
    for status_code in ("409", "500", "502", "503"):
        assert operation["responses"][status_code]["content"][
            "application/json"
        ]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
