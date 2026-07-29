import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.live.collector import RawFrame
from app.live.policy import LiveTimingSettings
from app.live.service import LiveService
from app.live.state import set_live_service
from app.main import app

NOW = datetime(2026, 8, 21, 13, 0, 0, tzinfo=UTC)
SESSION_BODY = {
    "session_date": "2026-08-21",
    "event_name": "Dutch Grand Prix",
    "session_key": "qualifying",
}


class SingleFrameFeed:
    async def stream(self) -> AsyncIterator[RawFrame]:
        yield RawFrame("TimingData", {"Lines": {"1": {"Position": "1"}}}, initial=True)
        await asyncio.Event().wait()

    async def close(self) -> None:
        return None


def build_service(tmp_path: Path, *, configured: bool = True) -> LiveService:
    return LiveService(
        settings=LiveTimingSettings(log_directory=str(tmp_path)),
        feed_factory=SingleFrameFeed if configured else None,
        clock=lambda: NOW,
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    set_live_service(build_service(tmp_path))
    with TestClient(app) as test_client:
        yield test_client
    set_live_service(None)
    app.dependency_overrides.clear()


@pytest.fixture
def unconfigured_client(tmp_path: Path) -> Iterator[TestClient]:
    set_live_service(build_service(tmp_path, configured=False))
    with TestClient(app) as test_client:
        yield test_client
    set_live_service(None)
    app.dependency_overrides.clear()


def test_status_reports_no_active_session_and_disables_caching(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/live/session")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["active"] is False
    assert body["record_state"] == "unconfirmed_live"
    assert body["session"] is None


def test_starting_a_session_reports_it_active(client: TestClient) -> None:
    response = client.post("/api/v1/live/session", json=SESSION_BODY)

    assert response.status_code == 200
    body = response.json()
    assert body["active"] is True
    assert body["session"]["session"]["event_name"] == "Dutch Grand Prix"

    client.delete("/api/v1/live/session")


def test_starting_the_same_session_twice_is_idempotent(client: TestClient) -> None:
    client.post("/api/v1/live/session", json=SESSION_BODY)
    response = client.post("/api/v1/live/session", json=SESSION_BODY)

    assert response.status_code == 200
    assert response.json()["active"] is True

    client.delete("/api/v1/live/session")


def test_starting_a_different_session_conflicts(client: TestClient) -> None:
    client.post("/api/v1/live/session", json=SESSION_BODY)

    response = client.post(
        "/api/v1/live/session",
        json={**SESSION_BODY, "session_key": "race"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "live_session_conflict"

    client.delete("/api/v1/live/session")


def test_starting_without_a_configured_feed_returns_service_unavailable(
    unconfigured_client: TestClient,
) -> None:
    response = unconfigured_client.post("/api/v1/live/session", json=SESSION_BODY)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "live_feed_unconfigured"


def test_stopping_returns_the_idle_status(client: TestClient) -> None:
    client.post("/api/v1/live/session", json=SESSION_BODY)

    response = client.delete("/api/v1/live/session")

    assert response.status_code == 200
    assert response.json()["active"] is False


def test_stopping_when_idle_is_not_an_error(client: TestClient) -> None:
    assert client.delete("/api/v1/live/session").status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"session_date": "not-a-date", "event_name": "x", "session_key": "y"},
        {"session_date": "2026-08-21", "event_name": "", "session_key": "y"},
        {**SESSION_BODY, "unexpected": "field"},
    ],
)
def test_invalid_start_requests_are_rejected(
    client: TestClient,
    body: dict[str, object],
) -> None:
    assert client.post("/api/v1/live/session", json=body).status_code == 422


def test_stream_sends_a_snapshot_then_live_updates(client: TestClient) -> None:
    client.post("/api/v1/live/session", json=SESSION_BODY)

    with client.websocket_connect("/api/v1/live/stream") as websocket:
        snapshot = websocket.receive_json()

        assert snapshot["type"] == "snapshot"
        assert snapshot["record_state"] == "unconfirmed_live"
        assert snapshot["state"]["topics"]["TimingData"]["snapshots"] == 1

    client.delete("/api/v1/live/session")


def test_stream_without_an_active_session_reports_an_error_and_closes(
    client: TestClient,
) -> None:
    with client.websocket_connect("/api/v1/live/stream") as websocket:
        message = websocket.receive_json()

    assert message["type"] == "error"
    assert message["code"] == "no_active_live_session"


def test_live_endpoints_do_not_touch_the_historical_contract(
    client: TestClient,
) -> None:
    schema = client.get("/openapi.json").json()
    live_paths = [path for path in schema["paths"] if path.startswith("/api/v1/live")]

    assert sorted(live_paths) == ["/api/v1/live/session"]
    # The historical session route is unchanged and still documented.
    assert "/api/v1/sessions/{session_id}" in schema["paths"]
