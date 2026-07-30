import asyncio
import time
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


class SingleFrameFeed:
    async def stream(self) -> AsyncIterator[RawFrame]:
        # SessionInfo is what names the session; the feed states it, not the user.
        yield RawFrame(
            "SessionInfo",
            {
                "Meeting": {"Name": "Dutch Grand Prix"},
                "Name": "Qualifying",
                "Type": "Qualifying",
                "StartDate": "2026-08-21T14:00:00",
            },
            initial=True,
        )
        yield RawFrame("TimingData", {"Lines": {"1": {"Position": "1"}}}, initial=True)
        await asyncio.Event().wait()

    async def close(self) -> None:
        return None


def build_service(tmp_path: Path, *, configured: bool = True) -> LiveService:
    return LiveService(
        settings=LiveTimingSettings(
            log_directory=str(tmp_path),
            token_path=str(tmp_path / "auth" / "f1-token.json"),
        ),
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


def test_starting_a_session_needs_no_identity(client: TestClient) -> None:
    response = client.post("/api/v1/live/session")

    assert response.status_code == 200
    assert response.json()["active"] is True

    client.delete("/api/v1/live/session")


def test_the_session_names_itself_from_the_feed(client: TestClient) -> None:
    client.post("/api/v1/live/session")

    # The identity is unknown until SessionInfo arrives, then it is the feed's.
    for _ in range(50):
        session = client.get("/api/v1/live/session").json()["session"]
        if session and session.get("session"):
            break
        time.sleep(0.05)

    assert session["session"]["event_name"] == "Dutch Grand Prix"
    assert session["session"]["session_key"] == "Qualifying"
    assert session["session"]["session_date"] == "2026-08-21"

    client.delete("/api/v1/live/session")


def test_starting_the_same_session_twice_is_idempotent(client: TestClient) -> None:
    client.post("/api/v1/live/session")
    response = client.post("/api/v1/live/session")

    assert response.status_code == 200
    assert response.json()["active"] is True

    client.delete("/api/v1/live/session")


def test_starting_again_reuses_the_running_session(client: TestClient) -> None:
    client.post("/api/v1/live/session")

    # Only one session can be live, so a second start is a reuse.
    response = client.post("/api/v1/live/session")

    assert response.status_code == 200
    assert response.json()["active"] is True

    client.delete("/api/v1/live/session")


def test_starting_without_a_configured_feed_returns_service_unavailable(
    unconfigured_client: TestClient,
) -> None:
    response = unconfigured_client.post("/api/v1/live/session")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "live_feed_unconfigured"


def test_stopping_returns_the_idle_status(client: TestClient) -> None:
    client.post("/api/v1/live/session")

    response = client.delete("/api/v1/live/session")

    assert response.status_code == 200
    assert response.json()["active"] is False


def test_stopping_when_idle_is_not_an_error(client: TestClient) -> None:
    assert client.delete("/api/v1/live/session").status_code == 200


def test_a_start_request_ignores_any_body(client: TestClient) -> None:
    # Older clients may still post an identity; it is simply not used.
    response = client.post(
        "/api/v1/live/session",
        json={"event_name": "ignored"},
    )

    assert response.status_code == 200

    client.delete("/api/v1/live/session")


def test_stream_sends_a_display_ready_board(client: TestClient) -> None:
    client.post("/api/v1/live/session")

    with client.websocket_connect("/api/v1/live/stream") as websocket:
        snapshot = websocket.receive_json()

        assert snapshot["type"] == "snapshot"
        assert snapshot["record_state"] == "unconfirmed_live"
        # The client receives normalised rows, not raw topic payloads.
        board = snapshot["board"]
        assert board["drivers"][0]["racing_number"] == "1"
        assert board["drivers"][0]["position"] == 1
        assert "Lines" not in board

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

    assert sorted(live_paths) == [
        "/api/v1/live/auth",
        "/api/v1/live/session",
    ]
    # The historical session route is unchanged and still documented.
    assert "/api/v1/sessions/{session_id}" in schema["paths"]


TOKEN = "abc123def456ghi789jkl012mno345"


def test_auth_status_starts_unauthenticated(client: TestClient) -> None:
    response = client.get("/api/v1/live/auth")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["authenticated"] is False
    assert body["expires_at"] is None
    assert body["token_source"] is None
    # A one-click entry point for the companion extension, carrying our port.
    assert body["companion_url"] == "https://f1login.fastf1.dev?port=8000"


def test_storing_a_token_reports_authenticated(client: TestClient) -> None:
    response = client.post("/api/v1/live/auth", json={"login_session": TOKEN})

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["expires_at"] is not None


def test_the_extension_camel_case_key_is_accepted(client: TestClient) -> None:
    response = client.post("/api/v1/live/auth", json={"loginSession": TOKEN})

    assert response.status_code == 200
    assert response.json()["authenticated"] is True


def test_the_extension_posts_to_the_root_auth_path(client: TestClient) -> None:
    # The FastF1 companion extension posts to http://localhost:{port}/auth.
    response = client.post("/auth", json={"loginSession": TOKEN})

    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert client.get("/api/v1/live/auth").json()["authenticated"] is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"login_session": ""},
        {"login_session": "short"},
        {"login_session": "has space in it here"},
        {"login_session": TOKEN, "unexpected": "field"},
    ],
)
def test_invalid_tokens_are_rejected(
    client: TestClient,
    body: dict[str, object],
) -> None:
    response = client.post("/api/v1/live/auth", json=body)

    assert response.status_code in (422,)
    assert client.get("/api/v1/live/auth").json()["authenticated"] is False


def test_signing_out_forgets_the_token(client: TestClient) -> None:
    client.post("/api/v1/live/auth", json={"login_session": TOKEN})

    response = client.delete("/api/v1/live/auth")

    assert response.status_code == 200
    assert response.json()["authenticated"] is False
    assert client.get("/api/v1/live/auth").json()["authenticated"] is False


def test_signing_out_when_never_authenticated_is_not_an_error(
    client: TestClient,
) -> None:
    assert client.delete("/api/v1/live/auth").status_code == 200


def test_the_token_value_is_never_returned_by_any_live_endpoint(
    client: TestClient,
) -> None:
    client.post("/api/v1/live/auth", json={"login_session": TOKEN})

    bodies = [
        client.get("/api/v1/live/auth").text,
        client.post("/api/v1/live/auth", json={"login_session": TOKEN}).text,
        client.get("/api/v1/live/session").text,
        client.delete("/api/v1/live/auth").text,
    ]

    for body in bodies:
        assert TOKEN not in body


def test_session_status_reports_authentication(client: TestClient) -> None:
    before = client.get("/api/v1/live/session").json()
    assert before["authentication"]["authenticated"] is False
    # The dashboard reads the sign-in link from here, not from /live/auth.
    assert (
        before["authentication"]["companion_url"]
        == "https://f1login.fastf1.dev?port=8000"
    )

    client.post("/api/v1/live/auth", json={"login_session": TOKEN})

    after = client.get("/api/v1/live/session").json()
    assert after["authentication"]["authenticated"] is True


def test_a_rejected_token_is_not_echoed_back(client: TestClient) -> None:
    secret = "s" * 40000

    response = client.post("/api/v1/live/auth", json={"login_session": secret})

    assert response.status_code == 422
    assert secret not in response.text
