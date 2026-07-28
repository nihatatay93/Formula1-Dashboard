import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.api.backfill_job import BackfillJobNotFoundError
from app.api.contracts import (
    BackfillJobResponse,
    BackfillJobSession,
    IngestionStatus,
    JobProgress,
)
from app.api.dependencies import get_database_session_factory
from app.main import app

JOB_ID = uuid.UUID("3e18c9fd-a8eb-458f-b317-55867afdc53f")
REQUESTED_AT = datetime(2026, 7, 28, 12, tzinfo=UTC)


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _job_response() -> BackfillJobResponse:
    return BackfillJobResponse(
        id=JOB_ID,
        season_year=2024,
        status=IngestionStatus.RUNNING,
        request_reason="missing",
        requested_at=REQUESTED_AT,
        started_at=REQUESTED_AT + timedelta(seconds=2),
        heartbeat_at=REQUESTED_AT + timedelta(seconds=32),
        completed_at=None,
        last_error=None,
        progress=JobProgress(
            total=1,
            pending=0,
            running=1,
            completed=0,
            failed=0,
            terminal=0,
        ),
        sessions=(
            BackfillJobSession(
                session_id=210,
                round_number=1,
                event_name="Bahrain Grand Prix",
                session_key="race",
                session_name="Race",
                status=IngestionStatus.RUNNING,
                attempt_count=1,
                queued_at=REQUESTED_AT,
                started_at=REQUESTED_AT + timedelta(seconds=2),
                heartbeat_at=REQUESTED_AT + timedelta(seconds=32),
                next_retry_at=None,
                completed_at=None,
                last_error=None,
            ),
        ),
    )


def test_backfill_job_endpoint_returns_contract_and_disables_caching(
    client: TestClient,
    monkeypatch,
) -> None:
    sentinel_factory = object()
    calls: list[tuple[uuid.UUID, object]] = []
    app.dependency_overrides[get_database_session_factory] = (
        lambda: sentinel_factory
    )

    def stub_read_backfill_job(*, job_id, session_factory):
        calls.append((job_id, session_factory))
        return _job_response()

    monkeypatch.setattr(
        "app.api.backfill_jobs.read_backfill_job",
        stub_read_backfill_job,
    )

    response = client.get(f"/api/v1/backfill-jobs/{JOB_ID}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "id": str(JOB_ID),
        "season_year": 2024,
        "status": "running",
        "request_reason": "missing",
        "requested_at": "2026-07-28T12:00:00Z",
        "started_at": "2026-07-28T12:00:02Z",
        "heartbeat_at": "2026-07-28T12:00:32Z",
        "completed_at": None,
        "last_error": None,
        "progress": {
            "total": 1,
            "pending": 0,
            "running": 1,
            "completed": 0,
            "failed": 0,
            "terminal": 0,
        },
        "sessions": [
            {
                "session_id": "210",
                "round_number": 1,
                "event_name": "Bahrain Grand Prix",
                "session_key": "race",
                "session_name": "Race",
                "status": "running",
                "attempt_count": 1,
                "queued_at": "2026-07-28T12:00:00Z",
                "started_at": "2026-07-28T12:00:02Z",
                "heartbeat_at": "2026-07-28T12:00:32Z",
                "next_retry_at": None,
                "completed_at": None,
                "last_error": None,
            }
        ],
    }
    assert calls == [(JOB_ID, sentinel_factory)]


def test_backfill_job_endpoint_returns_stable_not_found(
    client: TestClient,
    monkeypatch,
) -> None:
    app.dependency_overrides[get_database_session_factory] = lambda: object()

    def missing_job(**_kwargs):
        raise BackfillJobNotFoundError(
            "RAW-NOT-FOUND-DIAGNOSTIC-SENTINEL"
        )

    monkeypatch.setattr(
        "app.api.backfill_jobs.read_backfill_job",
        missing_job,
    )

    response = client.get(f"/api/v1/backfill-jobs/{JOB_ID}")

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "backfill_job_not_found",
            "message": "Backfill job was not found.",
        }
    }
    assert "RAW-NOT-FOUND-DIAGNOSTIC-SENTINEL" not in response.text


def test_backfill_job_endpoint_keeps_fastapi_validation_for_malformed_uuid(
    client: TestClient,
    monkeypatch,
) -> None:
    app.dependency_overrides[get_database_session_factory] = lambda: object()

    def forbidden_read(**_kwargs):
        raise AssertionError("read service must not be called")

    monkeypatch.setattr(
        "app.api.backfill_jobs.read_backfill_job",
        forbidden_read,
    )

    response = client.get("/api/v1/backfill-jobs/not-a-uuid")

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_backfill_job_endpoint_maps_database_failure_without_leaking_details(
    client: TestClient,
    monkeypatch,
) -> None:
    app.dependency_overrides[get_database_session_factory] = lambda: object()

    def fail_read_backfill_job(**_kwargs):
        raise SQLAlchemyError("RAW-DATABASE-ERROR-SENTINEL")

    monkeypatch.setattr(
        "app.api.backfill_jobs.read_backfill_job",
        fail_read_backfill_job,
    )

    response = client.get(f"/api/v1/backfill-jobs/{JOB_ID}")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "database_unavailable",
            "message": "The database is temporarily unavailable.",
        }
    }
    assert "RAW-DATABASE-ERROR-SENTINEL" not in response.text


def test_backfill_job_endpoint_maps_invalid_configuration(
    client: TestClient,
    monkeypatch,
) -> None:
    def invalid_configuration():
        raise RuntimeError("RAW-CONFIGURATION-ERROR-SENTINEL")

    monkeypatch.setattr(
        "app.api.dependencies.create_session_factory",
        invalid_configuration,
    )

    response = client.get(f"/api/v1/backfill-jobs/{JOB_ID}")

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "code": "server_configuration_error",
            "message": "Server database configuration is invalid.",
        }
    }
    assert "RAW-CONFIGURATION-ERROR-SENTINEL" not in response.text


def test_openapi_documents_backfill_job_response_and_stable_errors() -> None:
    operation = app.openapi()["paths"]["/api/v1/backfill-jobs/{job_id}"]["get"]

    assert operation["parameters"][0]["schema"]["format"] == "uuid"
    assert operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/BackfillJobResponse"}
    for status_code in ("404", "500", "503"):
        assert operation["responses"][status_code]["content"][
            "application/json"
        ]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
