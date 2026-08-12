"""Dashboard access control.

Every test here builds an app with the gate switched on, because the rest of
the suite runs with it off.
"""

import hmac
import json
from base64 import urlsafe_b64encode
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth.api import (
    SESSION_COOKIE,
    AuthenticationMiddleware,
    reset_attempt_limiter,
)
from app.auth.policy import (
    AuthConfigurationError,
    AuthSettings,
    hash_password,
    verify_password,
)
from app.auth.tokens import InvalidTokenError, issue_token, read_token
from app.main import create_app

PASSWORD = "a-long-enough-password"
SECRET = "s" * 48


def _encode(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def settings(**overrides: object) -> AuthSettings:
    values: dict[str, object] = {
        "required": True,
        "password_hash": hash_password(PASSWORD),
        "secret_key": SECRET,
        "secure_cookies": False,
    }
    values.update(overrides)
    return AuthSettings(**values)  # type: ignore[arg-type]


@pytest.fixture
def client() -> Iterator[TestClient]:
    reset_attempt_limiter()
    with TestClient(create_app(settings())) as test_client:
        yield test_client
    reset_attempt_limiter()


class TestConfiguration:
    def test_requiring_access_without_a_password_refuses_to_start(self) -> None:
        # Failing closed: a gate that silently disables itself is worse than none.
        with pytest.raises(AuthConfigurationError):
            AuthSettings(required=True, secret_key=SECRET)

    def test_requiring_access_without_a_secret_refuses_to_start(self) -> None:
        with pytest.raises(AuthConfigurationError):
            AuthSettings(required=True, password_hash=hash_password(PASSWORD))

    def test_a_short_secret_key_is_refused(self) -> None:
        with pytest.raises(AuthConfigurationError):
            AuthSettings(
                required=True,
                password_hash=hash_password(PASSWORD),
                secret_key="tooshort",
            )

    def test_an_empty_environment_fails_closed(self) -> None:
        # Required by default, so a deployment that configures nothing refuses
        # to start rather than coming up wide open.
        with pytest.raises(AuthConfigurationError):
            AuthSettings.from_environment({})

    def test_it_can_be_switched_off_explicitly(self) -> None:
        resolved = AuthSettings.from_environment({"DASHBOARD_AUTH_REQUIRED": "false"})

        assert resolved.required is False


class TestPasswordHashing:
    def test_the_right_password_verifies(self) -> None:
        assert verify_password(PASSWORD, hash_password(PASSWORD)) is True

    def test_a_wrong_password_does_not(self) -> None:
        assert verify_password("wrong-password-here", hash_password(PASSWORD)) is False

    def test_the_hash_never_contains_the_password(self) -> None:
        assert PASSWORD not in hash_password(PASSWORD)

    def test_two_hashes_of_one_password_differ(self) -> None:
        # Salted, so a stolen hash cannot be matched against a rainbow table.
        assert hash_password(PASSWORD) != hash_password(PASSWORD)

    @pytest.mark.parametrize("value", [None, 17, b"bytes", "", "short"])
    def test_unusable_passwords_are_refused_not_accepted(self, value: object) -> None:
        assert verify_password(value, hash_password(PASSWORD)) is False

    @pytest.mark.parametrize(
        "stored", [None, "", "garbage", "pbkdf2_sha256$notanint$a$b", "x$1$a$b"]
    )
    def test_a_malformed_stored_hash_never_grants_access(self, stored: object) -> None:
        assert verify_password(PASSWORD, stored) is False  # type: ignore[arg-type]

    def test_the_hash_survives_an_env_file(self) -> None:
        """It is carried in an environment variable, so it must be inert there.

        The conventional "$" separator is a substitution character in Docker
        Compose env files and in most shells: a "$"-separated hash pasted into
        deploy/.env silently loses everything after the first field, and the
        only symptom is that the correct password stops working.
        """
        encoded = hash_password(PASSWORD)

        assert "$" not in encoded
        assert "`" not in encoded and '"' not in encoded and "'" not in encoded

    def test_a_hash_using_the_old_separator_still_verifies(self) -> None:
        # Anyone who generated one before the separator changed keeps working.
        encoded = hash_password(PASSWORD)
        legacy = encoded.replace(".", "$", 3)

        assert verify_password(PASSWORD, legacy) is True

    def test_a_short_password_cannot_be_set(self) -> None:
        with pytest.raises(AuthConfigurationError):
            hash_password("short")


class TestTokens:
    def test_a_token_round_trips(self) -> None:
        token = issue_token(
            kind="bearer", secret_key=SECRET, lifetime=timedelta(days=1)
        )

        assert read_token(token, secret_key=SECRET).kind == "bearer"

    def test_another_secret_does_not_verify(self) -> None:
        token = issue_token(
            kind="session", secret_key=SECRET, lifetime=timedelta(days=1)
        )

        with pytest.raises(InvalidTokenError):
            read_token(token, secret_key="d" * 48)

    def test_a_tampered_payload_is_refused(self) -> None:
        token = issue_token(
            kind="session", secret_key=SECRET, lifetime=timedelta(days=1)
        )
        payload, _, signature = token.partition(".")
        forged = f"{payload[:-2]}XY.{signature}"

        with pytest.raises(InvalidTokenError):
            read_token(forged, secret_key=SECRET)

    def test_an_expired_token_is_refused(self) -> None:
        issued = datetime(2026, 1, 1, tzinfo=UTC)
        token = issue_token(
            kind="session",
            secret_key=SECRET,
            lifetime=timedelta(hours=1),
            now=issued,
        )

        with pytest.raises(InvalidTokenError):
            read_token(token, secret_key=SECRET, now=issued + timedelta(hours=2))

    @pytest.mark.parametrize(
        "token", [None, "", "no-separator", ".", "a.", ".b", "x" * 5000, 42]
    )
    def test_unusable_tokens_are_refused(self, token: object) -> None:
        with pytest.raises(InvalidTokenError):
            read_token(token, secret_key=SECRET)

    @pytest.mark.parametrize("token", ["\xff\xff.sig", "abc.\xff\xff", "\xff"])
    def test_non_ascii_tokens_are_refused_not_crashed_on(self, token: str) -> None:
        """Headers arrive latin-1 decoded, so high bytes reach this function.

        Without the ASCII guard these hit str.encode("ascii") or
        hmac.compare_digest, which raise UnicodeEncodeError and TypeError —
        neither an InvalidTokenError, so the gate would 500 rather than refuse.
        """
        with pytest.raises(InvalidTokenError):
            read_token(token, secret_key=SECRET)

    @pytest.mark.parametrize("exp", [10**20, -(10**20)])
    def test_an_out_of_range_expiry_is_refused(self, exp: int) -> None:
        # datetime.fromtimestamp raises OverflowError past the platform range.
        payload = _encode(
            json.dumps({"kind": "session", "iat": 1, "exp": exp}).encode("utf-8")
        )
        signature = _encode(
            hmac.new(SECRET.encode(), payload.encode("ascii"), sha256).digest()
        )

        with pytest.raises(InvalidTokenError):
            read_token(f"{payload}.{signature}", secret_key=SECRET)


class TestTheGate:
    def test_an_unauthenticated_request_is_refused(self, client: TestClient) -> None:
        response = client.get("/api/v1/seasons/2026")

        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "not_authenticated"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/live/session",
            "/api/v1/live/recordings",
            "/api/v1/upstreams/fastf1/usage",
            "/api/v1/sessions/1",
            "/openapi.json",
            "/",
        ],
    )
    def test_every_route_is_closed_by_default(
        self, client: TestClient, path: str
    ) -> None:
        # The gate is middleware rather than a per-route dependency precisely so
        # that a new route is protected because it exists.
        assert client.get(path).status_code == 401

    @pytest.mark.parametrize("path", ["/api/health/live", "/api/health/ready"])
    def test_health_probes_stay_reachable(
        self, client: TestClient, path: str
    ) -> None:
        # The platform probes these before anyone can sign in.
        assert client.get(path).status_code != 401

    def test_signing_in_sets_a_session_cookie_and_returns_a_token(
        self, client: TestClient
    ) -> None:
        response = client.post("/api/v1/auth/login", json={"password": PASSWORD})

        assert response.status_code == 200
        body = response.json()
        assert body["authenticated"] is True
        assert body["token"]
        assert SESSION_COOKIE in response.cookies

    def test_the_cookie_then_opens_the_rest_of_the_api(
        self, client: TestClient
    ) -> None:
        client.post("/api/v1/auth/login", json={"password": PASSWORD})

        assert client.get("/api/v1/live/session").status_code == 200

    def test_a_bearer_token_opens_it_for_a_native_client(
        self, client: TestClient
    ) -> None:
        token = client.post(
            "/api/v1/auth/login", json={"password": PASSWORD}
        ).json()["token"]
        client.cookies.clear()

        response = client.get(
            "/api/v1/live/session",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

    def test_the_session_cookie_is_http_only(self, client: TestClient) -> None:
        response = client.post("/api/v1/auth/login", json={"password": PASSWORD})

        assert "httponly" in response.headers["set-cookie"].lower()

    def test_a_wrong_password_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/login", json={"password": "not-the-password"}
        )

        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "invalid_credentials"

    def test_a_rejected_password_is_never_echoed(self, client: TestClient) -> None:
        secret = "u" * 300

        response = client.post("/api/v1/auth/login", json={"password": secret})

        assert response.status_code == 401
        assert secret not in response.text

    def test_an_oversized_body_is_never_echoed(self, client: TestClient) -> None:
        # Pydantic would put the offending input in its validation error; this
        # endpoint parses the body by hand for exactly that reason.
        secret = "v" * 40_000

        response = client.post("/api/v1/auth/login", json={"password": secret})

        assert secret not in response.text

    def test_unexpected_fields_are_named_but_not_valued(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/api/v1/auth/login",
            json={"password": PASSWORD, "surprise": "leak-me"},
        )

        assert response.status_code == 422
        assert "surprise" in response.text
        assert "leak-me" not in response.text

    def test_repeated_failures_lock_out_further_guesses(
        self, client: TestClient
    ) -> None:
        for _ in range(8):
            client.post("/api/v1/auth/login", json={"password": "wrong-password"})

        response = client.post("/api/v1/auth/login", json={"password": "another-wrong"})

        assert response.status_code == 429
        assert response.json()["detail"]["code"] == "too_many_attempts"

    def test_a_lockout_cannot_shut_the_owner_out(self, client: TestClient) -> None:
        """The lockout must refuse guesses, not the operator.

        Evaluating it before the password would let anyone deny the owner
        access indefinitely by guessing wrongly every few minutes.
        """
        for _ in range(20):
            client.post("/api/v1/auth/login", json={"password": "wrong-password"})

        response = client.post("/api/v1/auth/login", json={"password": PASSWORD})

        assert response.status_code == 200
        assert response.json()["authenticated"] is True

    def test_signing_out_clears_the_session(self, client: TestClient) -> None:
        client.post("/api/v1/auth/login", json={"password": PASSWORD})

        client.post("/api/v1/auth/logout")

        assert client.get("/api/v1/live/session").status_code == 401

    def test_the_session_endpoint_reports_state(self, client: TestClient) -> None:
        before = client.get("/api/v1/auth/session")
        assert before.status_code == 200
        assert before.json() == {
            "authenticated": False,
            "required": True,
            "kind": None,
            "expires_at": None,
        }

        client.post("/api/v1/auth/login", json={"password": PASSWORD})
        after = client.get("/api/v1/auth/session").json()

        assert after["authenticated"] is True
        assert after["kind"] == "session"

    def test_the_live_stream_handshake_is_refused_without_a_session(
        self, client: TestClient
    ) -> None:
        """The gate runs for WebSocket scopes, not only HTTP ones.

        Asserted as a policy-violation close rather than "something raised":
        the first version of this test accepted any exception, and passed while
        the gate was in fact crashing on every WebSocket because ``Request``
        cannot wrap a WebSocket scope.
        """
        with pytest.raises(WebSocketDisconnect) as refused:
            with client.websocket_connect("/api/v1/live/stream"):
                pass  # pragma: no cover - the handshake never completes

        # 1008 is "policy violation".
        assert refused.value.code == 1008

    def test_a_signed_in_reader_may_open_the_live_stream(
        self, client: TestClient
    ) -> None:
        client.post("/api/v1/auth/login", json={"password": PASSWORD})

        with client.websocket_connect("/api/v1/live/stream") as stream:
            assert stream.receive_json()["type"] in ("snapshot", "error")


class TestWithoutAccessControl:
    def test_an_ungated_deployment_serves_normally(self) -> None:
        with TestClient(create_app(AuthSettings(required=False))) as ungated:
            assert ungated.get("/api/v1/live/session").status_code == 200

    def test_signing_in_is_rejected_as_meaningless(self) -> None:
        with TestClient(create_app(AuthSettings(required=False))) as ungated:
            response = ungated.post(
                "/api/v1/auth/login", json={"password": PASSWORD}
            )

            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "auth_not_required"

    def test_session_state_reports_that_none_is_needed(self) -> None:
        with TestClient(create_app(AuthSettings(required=False))) as ungated:
            body = ungated.get("/api/v1/auth/session").json()

            assert body == {
                "authenticated": True,
                "required": False,
                "kind": None,
                "expires_at": None,
            }


@pytest.mark.asyncio
class TestHostileHeadersAtTheWire:
    """Driven at ASGI level, because an HTTP client will not send these.

    Header bytes are latin-1 decoded by the server, so a hand-written client
    can put raw high bytes in Authorization. httpx refuses to, which is why
    these cannot be expressed through TestClient.
    """

    async def gate_status(self, raw_header: bytes) -> int:
        settings_with_gate = settings()

        async def downstream(scope, receive, send) -> None:  # noqa: ANN001
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = AuthenticationMiddleware(
            downstream, settings=settings_with_gate
        )
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/live/session",
            "headers": [(b"authorization", raw_header)],
        }
        sent: list[dict] = []

        async def send(message: dict) -> None:
            sent.append(message)

        async def receive() -> dict:
            return {"type": "http.request"}

        await middleware(scope, receive, send)
        return next(
            message["status"]
            for message in sent
            if message["type"] == "http.response.start"
        )

    @pytest.mark.parametrize(
        "raw_header",
        [
            b"Bearer abc.def",
            b"Bearer \xff\xff.sig",
            b"Bearer abc.\xff\xff",
            b"Bearer \xc3\x28",
            b"Basic abc",
        ],
    )
    async def test_raw_bytes_are_refused_not_a_server_error(
        self, raw_header: bytes
    ) -> None:
        assert await self.gate_status(raw_header) == 401
