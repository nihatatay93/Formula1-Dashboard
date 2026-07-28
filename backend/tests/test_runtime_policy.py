from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from app.ingestion.archive_ingestion import ArchiveSessionIdentityError
from app.ingestion.archive_persistence import (
    ArchivePersistenceTargetChangedError,
    ArchiveSourceConflictError,
)
from app.ingestion.fastf1_loader import (
    FastF1LoaderConfigurationError,
    FastF1SessionLoadError,
)
from app.ingestion.fastf1_normalization import FastF1NormalizationError
from app.ingestion.runtime_policy import (
    BackfillRuntimePolicyError,
    BackfillRuntimeSettings,
    RetryBudgetExhaustedError,
    RetryDisposition,
    calculate_retry_schedule,
    classify_retry,
)


class DatabaseFailure(Exception):
    def __init__(self, sqlstate: str | None) -> None:
        super().__init__("RAW-DATABASE-ERROR")
        self.sqlstate = sqlstate


def database_error(
    error_type: type[OperationalError] | type[IntegrityError],
    *,
    sqlstate: str | None,
    connection_invalidated: bool = False,
) -> OperationalError | IntegrityError:
    error = error_type(
        "controlled statement",
        {},
        DatabaseFailure(sqlstate),
    )
    error.connection_invalidated = connection_invalidated
    return error


def test_default_settings_match_the_accepted_policy() -> None:
    settings = BackfillRuntimeSettings()

    assert settings.worker_poll_interval_seconds == 2
    assert settings.archive_session_min_interval == timedelta(seconds=90)
    assert settings.fastf1_rate_limit_cooldown == timedelta(hours=1)
    assert settings.max_attempts == 4
    assert settings.backoff_base_seconds == 60
    assert settings.backoff_multiplier == 2
    assert settings.backoff_cap_seconds == 900
    assert settings.jitter_min_ratio == 0.5
    assert settings.heartbeat_interval == timedelta(seconds=30)
    assert settings.lease_timeout == timedelta(minutes=5)
    assert settings.recovery_scan_interval == timedelta(seconds=30)
    assert settings.current_season_coverage_ttl == timedelta(hours=6)
    assert settings.historical_season_coverage_ttl == timedelta(days=30)
    assert settings.archive_availability_grace == timedelta(hours=2)
    assert settings.archive_correction_checkpoints == (
        timedelta(hours=24),
        timedelta(days=7),
    )


def test_empty_environment_uses_the_accepted_defaults() -> None:
    assert (
        BackfillRuntimeSettings.from_environment({})
        == BackfillRuntimeSettings()
    )


def test_partial_environment_preserves_other_defaults() -> None:
    settings = BackfillRuntimeSettings.from_environment(
        {"BACKFILL_MAX_ATTEMPTS": "5"}
    )

    assert settings.max_attempts == 5
    assert settings.backoff_base_seconds == 60
    assert settings.archive_correction_checkpoints_seconds == (
        86_400,
        604_800,
    )


def test_environment_overrides_are_typed() -> None:
    settings = BackfillRuntimeSettings.from_environment(
        {
            "BACKFILL_WORKER_POLL_INTERVAL_SECONDS": "1",
            "FASTF1_ARCHIVE_SESSION_MIN_INTERVAL_SECONDS": "120",
            "FASTF1_RATE_LIMIT_COOLDOWN_SECONDS": "7200",
            "BACKFILL_MAX_ATTEMPTS": "6",
            "BACKFILL_BACKOFF_BASE_SECONDS": "10",
            "BACKFILL_BACKOFF_MULTIPLIER": "3",
            "BACKFILL_BACKOFF_CAP_SECONDS": "300",
            "BACKFILL_JITTER_MIN_RATIO": "0.75",
            "BACKFILL_HEARTBEAT_INTERVAL_SECONDS": "15",
            "BACKFILL_LEASE_TIMEOUT_SECONDS": "180",
            "BACKFILL_RECOVERY_SCAN_INTERVAL_SECONDS": "20",
            "CURRENT_SEASON_COVERAGE_TTL_SECONDS": "3600",
            "HISTORICAL_SEASON_COVERAGE_TTL_SECONDS": "86400",
            "ARCHIVE_AVAILABILITY_GRACE_SECONDS": "1200",
            "ARCHIVE_CORRECTION_CHECKPOINTS_SECONDS": "7200,14400",
        }
    )

    assert settings == BackfillRuntimeSettings(
        worker_poll_interval_seconds=1,
        archive_session_min_interval_seconds=120,
        fastf1_rate_limit_cooldown_seconds=7200,
        max_attempts=6,
        backoff_base_seconds=10,
        backoff_multiplier=3,
        backoff_cap_seconds=300,
        jitter_min_ratio=0.75,
        heartbeat_interval_seconds=15,
        lease_timeout_seconds=180,
        recovery_scan_interval_seconds=20,
        current_season_coverage_ttl_seconds=3600,
        historical_season_coverage_ttl_seconds=86400,
        archive_availability_grace_seconds=1200,
        archive_correction_checkpoints_seconds=(7200, 14400),
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"worker_poll_interval_seconds": 0},
        {"archive_session_min_interval_seconds": 0},
        {
            "archive_session_min_interval_seconds": 90,
            "fastf1_rate_limit_cooldown_seconds": 90,
        },
        {"max_attempts": 0},
        {"backoff_base_seconds": True},
        {"backoff_base_seconds": 60, "backoff_cap_seconds": 59},
        {"jitter_min_ratio": 0},
        {"jitter_min_ratio": float("nan")},
        {"heartbeat_interval_seconds": 30, "lease_timeout_seconds": 30},
        {"lease_timeout_seconds": 300, "recovery_scan_interval_seconds": 301},
        {"archive_correction_checkpoints_seconds": ()},
        {"archive_correction_checkpoints_seconds": (7200, 86_400)},
        {"archive_correction_checkpoints_seconds": (604_800, 86_400)},
    ],
)
def test_rejects_invalid_settings(settings: dict[str, object]) -> None:
    with pytest.raises(BackfillRuntimePolicyError):
        BackfillRuntimeSettings(**settings)


@pytest.mark.parametrize(
    "environment",
    [
        {"BACKFILL_MAX_ATTEMPTS": "four"},
        {"FASTF1_ARCHIVE_SESSION_MIN_INTERVAL_SECONDS": "slow"},
        {"BACKFILL_JITTER_MIN_RATIO": "half"},
        {"ARCHIVE_CORRECTION_CHECKPOINTS_SECONDS": "86400,seven-days"},
    ],
)
def test_rejects_malformed_environment_values(
    environment: dict[str, str],
) -> None:
    with pytest.raises(BackfillRuntimePolicyError):
        BackfillRuntimeSettings.from_environment(environment)


@pytest.mark.parametrize(
    "error",
    [
        FastF1SessionLoadError("controlled"),
        ArchivePersistenceTargetChangedError("controlled"),
        SQLAlchemyTimeoutError("controlled"),
        database_error(OperationalError, sqlstate="08006"),
        database_error(OperationalError, sqlstate="40001"),
        database_error(OperationalError, sqlstate="40P01"),
        database_error(OperationalError, sqlstate="53300"),
        database_error(OperationalError, sqlstate="55P03"),
        database_error(OperationalError, sqlstate="57P03"),
        database_error(
            OperationalError,
            sqlstate=None,
            connection_invalidated=True,
        ),
    ],
)
def test_classifies_accepted_transient_failures_as_retryable(
    error: Exception,
) -> None:
    assert classify_retry(error) is RetryDisposition.RETRYABLE


@pytest.mark.parametrize(
    "error",
    [
        FastF1LoaderConfigurationError("controlled"),
        FastF1NormalizationError("controlled"),
        ArchiveSessionIdentityError("controlled"),
        ArchiveSourceConflictError("controlled"),
        database_error(IntegrityError, sqlstate="23505"),
        database_error(OperationalError, sqlstate="28P01"),
        RuntimeError("controlled"),
    ],
)
def test_classifies_deterministic_and_unknown_failures_as_terminal(
    error: Exception,
) -> None:
    assert classify_retry(error) is RetryDisposition.TERMINAL


@pytest.mark.parametrize(
    ("failed_attempt", "jitter_fraction", "nominal", "delay"),
    [
        (1, 0.0, 60, 30),
        (1, 1.0, 60, 60),
        (2, 0.0, 120, 60),
        (2, 0.5, 120, 90),
        (3, 1.0, 240, 240),
    ],
)
def test_calculates_equal_jitter_backoff_deterministically(
    failed_attempt: int,
    jitter_fraction: float,
    nominal: int,
    delay: int,
) -> None:
    database_now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)

    schedule = calculate_retry_schedule(
        database_now=database_now,
        failed_attempt=failed_attempt,
        jitter_fraction=jitter_fraction,
        settings=BackfillRuntimeSettings(),
    )

    assert schedule.failed_attempt == failed_attempt
    assert schedule.nominal_delay == timedelta(seconds=nominal)
    assert schedule.delay == timedelta(seconds=delay)
    assert schedule.next_retry_at == database_now + timedelta(seconds=delay)


def test_caps_nominal_backoff() -> None:
    settings = BackfillRuntimeSettings(max_attempts=10)

    schedule = calculate_retry_schedule(
        database_now=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        failed_attempt=6,
        jitter_fraction=1,
        settings=settings,
    )

    assert schedule.nominal_delay == timedelta(minutes=15)
    assert schedule.delay == timedelta(minutes=15)


@pytest.mark.parametrize(
    ("failed_attempt", "jitter_fraction", "error_type"),
    [
        (4, 0.5, RetryBudgetExhaustedError),
        (0, 0.5, BackfillRuntimePolicyError),
        (1, -0.1, BackfillRuntimePolicyError),
        (1, 1.1, BackfillRuntimePolicyError),
        (1, float("nan"), BackfillRuntimePolicyError),
    ],
)
def test_rejects_invalid_or_exhausted_retry_requests(
    failed_attempt: int,
    jitter_fraction: float,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        calculate_retry_schedule(
            database_now=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
            failed_attempt=failed_attempt,
            jitter_fraction=jitter_fraction,
            settings=BackfillRuntimeSettings(),
        )


def test_rejects_naive_database_time() -> None:
    with pytest.raises(BackfillRuntimePolicyError, match="timezone"):
        calculate_retry_schedule(
            database_now=datetime(2026, 7, 28, 12, 0),
            failed_attempt=1,
            jitter_fraction=0.5,
            settings=BackfillRuntimeSettings(),
        )
