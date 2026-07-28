from contextlib import nullcontext
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.contracts import (
    IngestionStatus,
    LapTelemetryIngestionState,
    LapTelemetryPage,
    LapTelemetryResponse,
    LapTelemetrySnapshot,
)
from app.api.dependencies import get_database_session_factory
from app.ingestion.telemetry_ingestion import TelemetryCommandResult
from app.main import app

COMPLETED_AT = datetime(2026, 7, 28, 12, tzinfo=UTC)
PATH = "/api/v1/sessions/10/entries/20/laps/3/telemetry"


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_command_queues_once_and_exposes_polling_headers(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = object()
    app.dependency_overrides[get_database_session_factory] = (
        lambda: lambda: nullcontext(database)
    )
    monkeypatch.setattr(
        "app.api.telemetry.ensure_lap_telemetry",
        lambda db, **_kwargs: (
            TelemetryCommandResult(
                lap_id=30,
                action="queued",
                status="pending",
                source_snapshot_completed_at=COMPLETED_AT,
            )
            if db is database
            else None
        ),
    )

    response = client.post(PATH)

    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "2"
    assert response.headers["location"] == PATH
    assert response.json()["lap_id"] == "30"
    assert response.json()["action"] == "queued"


def test_read_contract_is_bounded_and_no_store(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    app.dependency_overrides[get_database_session_factory] = lambda: sentinel
    calls: list[tuple[int | None, int]] = []

    def read(**kwargs):
        assert kwargs["session_factory"] is sentinel
        calls.append((kwargs["after_sample"], kwargs["limit"]))
        return LapTelemetryResponse(
            session_id=10,
            session_entry_id=20,
            lap_id=30,
            lap_number=3,
            data_available=False,
            snapshot=LapTelemetrySnapshot(
                compatible=True,
                source_snapshot_completed_at=COMPLETED_AT,
                current_snapshot_completed_at=COMPLETED_AT,
            ),
            ingestion=LapTelemetryIngestionState(
                status=IngestionStatus.PENDING,
                attempt_count=0,
                sample_count=0,
                requested_at=COMPLETED_AT,
                heartbeat_at=None,
                next_retry_at=None,
                completed_at=None,
                last_error=None,
            ),
            page=LapTelemetryPage(
                limit=kwargs["limit"],
                has_more=False,
                next_after_sample=None,
            ),
            items=(),
        )

    monkeypatch.setattr("app.api.telemetry.read_lap_telemetry", read)

    response = client.get(PATH, params={"after_sample": 9, "limit": 25})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["data_available"] is False
    assert calls == [(9, 25)]


def test_read_rejects_page_sizes_above_hard_limit(client: TestClient) -> None:
    app.dependency_overrides[get_database_session_factory] = lambda: object()
    response = client.get(PATH, params={"limit": 1001})

    assert response.status_code == 422


def test_openapi_documents_both_lap_telemetry_operations(
    client: TestClient,
) -> None:
    operation = client.get("/openapi.json").json()["paths"][
        "/api/v1/sessions/{session_id}/entries/{session_entry_id}/"
        "laps/{lap_number}/telemetry"
    ]

    assert set(operation) >= {"get", "post"}
    assert (
        operation["get"]["parameters"][-1]["schema"]["maximum"] == 1000
    )
