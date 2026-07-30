import base64
import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.live.f1_auth import (
    MAX_TOKEN_LENGTH,
    MAX_TRUSTED_TTL,
    F1AuthError,
    F1TokenStore,
    InvalidF1TokenError,
    extract_subscription_token,
    read_jwt_expiry,
    validate_login_session,
)

NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
TTL = timedelta(hours=96)
PLAUSIBLE = "abc123def456ghi789jkl012mno345"


def store(tmp_path: Path, *, ttl: timedelta = TTL) -> F1TokenStore:
    return F1TokenStore(tmp_path / "auth" / "f1-token.json", default_ttl=ttl)


def jwt_with(claims: object) -> str:
    def segment(payload: object) -> str:
        raw = json.dumps(payload).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{segment({'alg': 'HS256'})}.{segment(claims)}.signature"


class TestValidation:
    def test_accepts_and_strips_a_plausible_value(self) -> None:
        assert validate_login_session(f"  {PLAUSIBLE}  ") == PLAUSIBLE

    @pytest.mark.parametrize(
        "value",
        [None, 42, b"bytes", ["x"], {"a": 1}],
    )
    def test_non_string_is_rejected(self, value: object) -> None:
        with pytest.raises(InvalidF1TokenError, match="string"):
            validate_login_session(value)

    def test_empty_is_rejected(self) -> None:
        with pytest.raises(InvalidF1TokenError, match="empty"):
            validate_login_session("   ")

    def test_too_short_is_rejected(self) -> None:
        with pytest.raises(InvalidF1TokenError, match="too short"):
            validate_login_session("abc")

    def test_too_long_is_rejected(self) -> None:
        with pytest.raises(InvalidF1TokenError, match="too long"):
            validate_login_session("a" * (MAX_TOKEN_LENGTH + 1))

    @pytest.mark.parametrize(
        "value",
        [
            "abc123def456ghi789 jkl012",
            "abc123def456ghi789\njkl012",
            "abc123def456ghi789\tjkl012",
            "abc123def456ghi789\x00jkl012",
        ],
    )
    def test_whitespace_or_control_characters_are_rejected(self, value: str) -> None:
        with pytest.raises(InvalidF1TokenError, match="invalid characters"):
            validate_login_session(value)

    def test_the_rejected_value_is_never_echoed(self) -> None:
        secret = "s" * (MAX_TOKEN_LENGTH + 1)
        with pytest.raises(InvalidF1TokenError) as caught:
            validate_login_session(secret)

        assert secret not in str(caught.value)


class TestJwtExpiry:
    def test_reads_a_numeric_exp_claim(self) -> None:
        expiry = NOW + timedelta(days=3)
        token = jwt_with({"exp": int(expiry.timestamp())})

        assert read_jwt_expiry(token) == expiry.replace(microsecond=0)

    @pytest.mark.parametrize(
        "token",
        [
            "not-a-jwt",
            "only.two",
            "a.b.c.d",
            "header.!!!notbase64!!!.signature",
        ],
    )
    def test_a_non_jwt_yields_no_expiry(self, token: str) -> None:
        assert read_jwt_expiry(token) is None

    @pytest.mark.parametrize("claims", [[1, 2], "string", 7])
    def test_non_object_claims_yield_no_expiry(self, claims: object) -> None:
        assert read_jwt_expiry(jwt_with(claims)) is None

    @pytest.mark.parametrize("exp", [None, "soon", True, [1]])
    def test_a_missing_or_non_numeric_exp_yields_none(self, exp: object) -> None:
        assert read_jwt_expiry(jwt_with({"exp": exp})) is None

    def test_an_out_of_range_exp_yields_none(self) -> None:
        assert read_jwt_expiry(jwt_with({"exp": 1e30})) is None


class TestTokenStore:
    def test_saving_reports_expiry_from_the_configured_ttl(self, tmp_path: Path) -> None:
        stored = store(tmp_path).save(PLAUSIBLE, now=NOW)

        assert stored.expires_at == NOW + TTL
        assert stored.expiry_source == "configured_ttl"
        assert stored.obtained_at == NOW

    def test_a_sane_token_claim_wins_over_the_configured_ttl(
        self,
        tmp_path: Path,
    ) -> None:
        expiry = NOW + timedelta(days=2)

        stored = store(tmp_path).save(
            jwt_with({"exp": int(expiry.timestamp())}),
            now=NOW,
        )

        assert stored.expiry_source == "token_claim"
        assert stored.expires_at == expiry.replace(microsecond=0)

    def test_an_absurdly_distant_claim_falls_back_to_the_ttl(
        self,
        tmp_path: Path,
    ) -> None:
        far = NOW + MAX_TRUSTED_TTL + timedelta(days=365)

        stored = store(tmp_path).save(
            jwt_with({"exp": int(far.timestamp())}),
            now=NOW,
        )

        # A hostile or malformed claim must not pin the token open.
        assert stored.expiry_source == "configured_ttl"
        assert stored.expires_at == NOW + TTL

    def test_an_already_past_claim_falls_back_to_the_ttl(self, tmp_path: Path) -> None:
        past = NOW - timedelta(days=1)

        stored = store(tmp_path).save(
            jwt_with({"exp": int(past.timestamp())}),
            now=NOW,
        )

        assert stored.expiry_source == "configured_ttl"

    def test_the_token_file_is_owner_only(self, tmp_path: Path) -> None:
        keeper = store(tmp_path)
        keeper.save(PLAUSIBLE, now=NOW)

        mode = stat.S_IMODE(keeper.path.stat().st_mode)

        assert mode == stat.S_IRUSR | stat.S_IWUSR

    def test_saving_creates_a_missing_parent_directory(self, tmp_path: Path) -> None:
        keeper = store(tmp_path)

        keeper.save(PLAUSIBLE, now=NOW)

        assert keeper.path.exists()

    def test_an_invalid_token_is_never_written(self, tmp_path: Path) -> None:
        keeper = store(tmp_path)

        with pytest.raises(InvalidF1TokenError):
            keeper.save("short", now=NOW)

        assert not keeper.path.exists()

    def test_saving_replaces_a_previous_token(self, tmp_path: Path) -> None:
        keeper = store(tmp_path)
        keeper.save(PLAUSIBLE, now=NOW)
        later = NOW + timedelta(hours=1)

        keeper.save("zzz999yyy888xxx777www666", now=later)

        assert keeper.login_session(now=later) == "zzz999yyy888xxx777www666"

    def test_login_session_returns_the_value_while_valid(self, tmp_path: Path) -> None:
        keeper = store(tmp_path)
        keeper.save(PLAUSIBLE, now=NOW)

        assert keeper.login_session(now=NOW + timedelta(hours=1)) == PLAUSIBLE

    def test_login_session_is_withheld_once_expired(self, tmp_path: Path) -> None:
        keeper = store(tmp_path)
        keeper.save(PLAUSIBLE, now=NOW)

        assert keeper.login_session(now=NOW + TTL) is None

    def test_login_session_without_a_stored_token_is_none(self, tmp_path: Path) -> None:
        assert store(tmp_path).login_session(now=NOW) is None

    def test_a_corrupt_token_file_reads_as_absent(self, tmp_path: Path) -> None:
        keeper = store(tmp_path)
        keeper.save(PLAUSIBLE, now=NOW)
        keeper.path.write_text("not json", encoding="utf-8")

        assert keeper.load() is None
        assert keeper.status(now=NOW)["authenticated"] is False

    def test_clear_removes_the_token_and_reports_whether_there_was_one(
        self,
        tmp_path: Path,
    ) -> None:
        keeper = store(tmp_path)
        keeper.save(PLAUSIBLE, now=NOW)

        assert keeper.clear() is True
        assert keeper.clear() is False
        assert not keeper.path.exists()

    def test_a_non_positive_ttl_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(F1AuthError, match="default_ttl"):
            F1TokenStore(tmp_path / "t.json", default_ttl=timedelta(0))


class TestStatus:
    def test_status_without_a_token(self, tmp_path: Path) -> None:
        assert store(tmp_path).status(now=NOW) == {
            "authenticated": False,
            "expired": False,
            "expires_at": None,
            "seconds_remaining": 0,
            "expiry_source": None,
            "token_source": None,
        }

    def test_status_while_authenticated(self, tmp_path: Path) -> None:
        keeper = store(tmp_path)
        keeper.save(PLAUSIBLE, now=NOW)

        status = keeper.status(now=NOW + timedelta(hours=24))

        assert status["authenticated"] is True
        assert status["expired"] is False
        assert status["seconds_remaining"] == int(timedelta(hours=72).total_seconds())
        assert status["expiry_source"] == "configured_ttl"

    def test_status_once_expired(self, tmp_path: Path) -> None:
        keeper = store(tmp_path)
        keeper.save(PLAUSIBLE, now=NOW)

        status = keeper.status(now=NOW + TTL + timedelta(hours=1))

        assert status["authenticated"] is False
        assert status["expired"] is True
        assert status["seconds_remaining"] == 0

    def test_status_never_contains_the_token_value(self, tmp_path: Path) -> None:
        keeper = store(tmp_path)
        keeper.save(PLAUSIBLE, now=NOW)

        rendered = json.dumps(keeper.status(now=NOW))

        assert PLAUSIBLE not in rendered
        # The value is on disk, so the guarantee is about what status exposes.
        assert PLAUSIBLE in keeper.path.read_text(encoding="utf-8")


def login_session_cookie(token: str) -> str:
    """The wrapper the browser actually holds: URL-encoded JSON."""
    import urllib.parse

    return urllib.parse.quote(json.dumps({"data": {"subscriptionToken": token}}))


class TestSubscriptionTokenExtraction:
    def test_the_token_is_unwrapped_from_the_cookie(self) -> None:
        token, source = extract_subscription_token(login_session_cookie(PLAUSIBLE))

        assert token == PLAUSIBLE
        assert source == "login_session_cookie"

    def test_a_bare_token_passes_through(self) -> None:
        assert extract_subscription_token(PLAUSIBLE) == (PLAUSIBLE, "direct")

    @pytest.mark.parametrize(
        "wrapper",
        [
            '{"data": {}}',
            '{"data": {"subscriptionToken": ""}}',
            '{"data": {"subscriptionToken": 7}}',
            '{"data": "not-an-object"}',
            '{"other": 1}',
            "[1, 2, 3]",
            '"just a string"',
        ],
    )
    def test_a_wrapper_without_a_usable_token_is_treated_as_direct(
        self,
        wrapper: str,
    ) -> None:
        token, source = extract_subscription_token(wrapper)

        assert token == wrapper
        assert source == "direct"

    def test_storing_the_cookie_persists_only_the_inner_token(
        self,
        tmp_path: Path,
    ) -> None:
        keeper = store(tmp_path)
        cookie = login_session_cookie(PLAUSIBLE)

        stored = keeper.save(cookie, now=NOW)

        assert stored.token_source == "login_session_cookie"
        assert keeper.login_session(now=NOW) == PLAUSIBLE
        # The wrapper itself is not what gets used for the live connection.
        assert cookie not in keeper.path.read_text(encoding="utf-8")

    def test_the_inner_jwt_expiry_is_used(self, tmp_path: Path) -> None:
        expiry = NOW + timedelta(days=4)
        cookie = login_session_cookie(jwt_with({"exp": int(expiry.timestamp())}))

        stored = store(tmp_path).save(cookie, now=NOW)

        # This is the case the wrapper previously hid: the expiry claim lives in
        # the inner token, so unwrapping is what makes it readable at all.
        assert stored.expiry_source == "token_claim"
        assert stored.expires_at == expiry.replace(microsecond=0)

    def test_status_reports_where_the_token_came_from(self, tmp_path: Path) -> None:
        keeper = store(tmp_path)
        keeper.save(login_session_cookie(PLAUSIBLE), now=NOW)

        assert keeper.status(now=NOW)["token_source"] == "login_session_cookie"
