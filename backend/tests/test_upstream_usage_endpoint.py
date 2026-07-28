from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import get_database_session_factory
from app.ingestion.request_budget import FastF1RequestBudgetSnapshot
from app.main import app

OBSERVED_AT = datetime(2026, 7, 28, 12, tzinfo=UTC)


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_fastf1_usage_endpoint_returns_local_budget_and_disables_caching(
    client: TestClient,
    monkeypatch,
) -> None:
    sentinel_factory = object()
    app.dependency_overrides[get_database_session_factory] = (
        lambda: sentinel_factory
    )
    calls: list[object] = []

    def stub_read_fastf1_request_budget(*, session_factory, settings):
        calls.append(session_factory)
        assert settings.fastf1_request_operational_ceiling == 450
        return FastF1RequestBudgetSnapshot(
            observed_at=OBSERVED_AT,
            observed_requests=421,
            archive_requests=419,
            schedule_requests=2,
            telemetry_requests=0,
            library_limit=500,
            operational_ceiling=450,
            warning_threshold=400,
            remaining_before_pause=29,
            next_capacity_at=None,
            cooldown_until=None,
            cooldown_reason=None,
            status="warning",
        )

    monkeypatch.setattr(
        "app.api.upstream_usage.read_fastf1_request_budget",
        stub_read_fastf1_request_budget,
    )

    response = client.get("/api/v1/upstreams/fastf1/usage")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "source": "fastf1",
        "window_seconds": 3600,
        "observed_at": "2026-07-28T12:00:00Z",
        "observed_requests": 421,
        "archive_requests": 419,
        "schedule_requests": 2,
        "telemetry_requests": 0,
        "library_limit": 500,
        "operational_ceiling": 450,
        "warning_threshold": 400,
        "remaining_before_pause": 29,
        "next_capacity_at": None,
        "cooldown_until": None,
        "cooldown_reason": None,
        "status": "warning",
        "authoritative": False,
    }
    assert calls == [sentinel_factory]


def test_fastf1_usage_endpoint_maps_database_failure_without_details(
    client: TestClient,
    monkeypatch,
) -> None:
    app.dependency_overrides[get_database_session_factory] = lambda: object()

    def fail_read(**_kwargs):
        raise SQLAlchemyError("RAW-DATABASE-ERROR-SENTINEL")

    monkeypatch.setattr(
        "app.api.upstream_usage.read_fastf1_request_budget",
        fail_read,
    )

    response = client.get("/api/v1/upstreams/fastf1/usage")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "database_unavailable",
            "message": "The database is temporarily unavailable.",
        }
    }
    assert "RAW-DATABASE-ERROR-SENTINEL" not in response.text


def test_fastf1_usage_openapi_contract() -> None:
    operation = app.openapi()["paths"]["/api/v1/upstreams/fastf1/usage"][
        "get"
    ]

    assert operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/FastF1RequestBudgetResponse"}
    for status_code in ("500", "503"):
        assert operation["responses"][status_code]["content"][
            "application/json"
        ]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
