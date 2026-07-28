from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from app.ingestion.archive_ingestion import ArchiveSessionIdentityError
from app.ingestion.archive_persistence import (
    ArchivePersistenceTargetChangedError,
)
from app.ingestion.fastf1_loader import FastF1SessionLoadError

DEFAULT_CORRECTION_CHECKPOINTS_SECONDS = (86_400, 604_800)

TRANSIENT_SQLSTATE_PREFIXES = frozenset({"08", "40", "53"})
TRANSIENT_SQLSTATES = frozenset(
    {
        "55P03",  # lock_not_available
        "57P01",  # admin_shutdown
        "57P02",  # crash_shutdown
        "57P03",  # cannot_connect_now
    }
)


class BackfillRuntimePolicyError(ValueError):
    """Base error for invalid runtime policy input."""


class RetryBudgetExhaustedError(BackfillRuntimePolicyError):
    """Raised when a failed attempt has no automatic retry remaining."""


class RetryDisposition(StrEnum):
    RETRYABLE = "retryable"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class BackfillRuntimeSettings:
    worker_poll_interval_seconds: int = 2
    max_attempts: int = 4
    backoff_base_seconds: int = 60
    backoff_multiplier: int = 2
    backoff_cap_seconds: int = 900
    jitter_min_ratio: float = 0.5
    heartbeat_interval_seconds: int = 30
    lease_timeout_seconds: int = 300
    recovery_scan_interval_seconds: int = 30
    current_season_coverage_ttl_seconds: int = 21_600
    historical_season_coverage_ttl_seconds: int = 2_592_000
    archive_availability_grace_seconds: int = 7_200
    archive_correction_checkpoints_seconds: tuple[int, ...] = (
        DEFAULT_CORRECTION_CHECKPOINTS_SECONDS
    )

    def __post_init__(self) -> None:
        _positive_integer(
            self.worker_poll_interval_seconds,
            "worker_poll_interval_seconds",
        )
        _positive_integer(self.max_attempts, "max_attempts")
        _positive_integer(self.backoff_base_seconds, "backoff_base_seconds")
        _positive_integer(self.backoff_multiplier, "backoff_multiplier")
        _positive_integer(self.backoff_cap_seconds, "backoff_cap_seconds")
        _positive_integer(
            self.heartbeat_interval_seconds,
            "heartbeat_interval_seconds",
        )
        _positive_integer(self.lease_timeout_seconds, "lease_timeout_seconds")
        _positive_integer(
            self.recovery_scan_interval_seconds,
            "recovery_scan_interval_seconds",
        )
        _positive_integer(
            self.current_season_coverage_ttl_seconds,
            "current_season_coverage_ttl_seconds",
        )
        _positive_integer(
            self.historical_season_coverage_ttl_seconds,
            "historical_season_coverage_ttl_seconds",
        )
        _positive_integer(
            self.archive_availability_grace_seconds,
            "archive_availability_grace_seconds",
        )

        if self.backoff_cap_seconds < self.backoff_base_seconds:
            raise BackfillRuntimePolicyError(
                "backoff_cap_seconds must be at least backoff_base_seconds"
            )
        if (
            isinstance(self.jitter_min_ratio, bool)
            or not isinstance(self.jitter_min_ratio, int | float)
            or not math.isfinite(self.jitter_min_ratio)
            or not 0 < self.jitter_min_ratio <= 1
        ):
            raise BackfillRuntimePolicyError(
                "jitter_min_ratio must be greater than zero and at most one"
            )
        if self.lease_timeout_seconds <= self.heartbeat_interval_seconds:
            raise BackfillRuntimePolicyError(
                "lease_timeout_seconds must exceed heartbeat_interval_seconds"
            )
        if self.recovery_scan_interval_seconds > self.lease_timeout_seconds:
            raise BackfillRuntimePolicyError(
                "recovery_scan_interval_seconds must not exceed lease_timeout_seconds"
            )

        checkpoints = self.archive_correction_checkpoints_seconds
        if (
            not isinstance(checkpoints, tuple)
            or not checkpoints
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= self.archive_availability_grace_seconds
                for value in checkpoints
            )
            or tuple(sorted(set(checkpoints))) != checkpoints
        ):
            raise BackfillRuntimePolicyError(
                "archive correction checkpoints must be unique increasing integers "
                "greater than archive_availability_grace_seconds"
            )

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> BackfillRuntimeSettings:
        values = os.environ if environ is None else environ
        defaults = cls()
        return cls(
            worker_poll_interval_seconds=_environment_integer(
                values,
                "BACKFILL_WORKER_POLL_INTERVAL_SECONDS",
                defaults.worker_poll_interval_seconds,
            ),
            max_attempts=_environment_integer(
                values,
                "BACKFILL_MAX_ATTEMPTS",
                defaults.max_attempts,
            ),
            backoff_base_seconds=_environment_integer(
                values,
                "BACKFILL_BACKOFF_BASE_SECONDS",
                defaults.backoff_base_seconds,
            ),
            backoff_multiplier=_environment_integer(
                values,
                "BACKFILL_BACKOFF_MULTIPLIER",
                defaults.backoff_multiplier,
            ),
            backoff_cap_seconds=_environment_integer(
                values,
                "BACKFILL_BACKOFF_CAP_SECONDS",
                defaults.backoff_cap_seconds,
            ),
            jitter_min_ratio=_environment_float(
                values,
                "BACKFILL_JITTER_MIN_RATIO",
                defaults.jitter_min_ratio,
            ),
            heartbeat_interval_seconds=_environment_integer(
                values,
                "BACKFILL_HEARTBEAT_INTERVAL_SECONDS",
                defaults.heartbeat_interval_seconds,
            ),
            lease_timeout_seconds=_environment_integer(
                values,
                "BACKFILL_LEASE_TIMEOUT_SECONDS",
                defaults.lease_timeout_seconds,
            ),
            recovery_scan_interval_seconds=_environment_integer(
                values,
                "BACKFILL_RECOVERY_SCAN_INTERVAL_SECONDS",
                defaults.recovery_scan_interval_seconds,
            ),
            current_season_coverage_ttl_seconds=_environment_integer(
                values,
                "CURRENT_SEASON_COVERAGE_TTL_SECONDS",
                defaults.current_season_coverage_ttl_seconds,
            ),
            historical_season_coverage_ttl_seconds=_environment_integer(
                values,
                "HISTORICAL_SEASON_COVERAGE_TTL_SECONDS",
                defaults.historical_season_coverage_ttl_seconds,
            ),
            archive_availability_grace_seconds=_environment_integer(
                values,
                "ARCHIVE_AVAILABILITY_GRACE_SECONDS",
                defaults.archive_availability_grace_seconds,
            ),
            archive_correction_checkpoints_seconds=_environment_integer_tuple(
                values,
                "ARCHIVE_CORRECTION_CHECKPOINTS_SECONDS",
                DEFAULT_CORRECTION_CHECKPOINTS_SECONDS,
            ),
        )

    @property
    def heartbeat_interval(self) -> timedelta:
        return timedelta(seconds=self.heartbeat_interval_seconds)

    @property
    def lease_timeout(self) -> timedelta:
        return timedelta(seconds=self.lease_timeout_seconds)

    @property
    def recovery_scan_interval(self) -> timedelta:
        return timedelta(seconds=self.recovery_scan_interval_seconds)

    @property
    def current_season_coverage_ttl(self) -> timedelta:
        return timedelta(seconds=self.current_season_coverage_ttl_seconds)

    @property
    def historical_season_coverage_ttl(self) -> timedelta:
        return timedelta(seconds=self.historical_season_coverage_ttl_seconds)

    @property
    def archive_availability_grace(self) -> timedelta:
        return timedelta(seconds=self.archive_availability_grace_seconds)

    @property
    def archive_correction_checkpoints(self) -> tuple[timedelta, ...]:
        return tuple(
            timedelta(seconds=value)
            for value in self.archive_correction_checkpoints_seconds
        )


@dataclass(frozen=True, slots=True)
class RetrySchedule:
    failed_attempt: int
    nominal_delay: timedelta
    delay: timedelta
    next_retry_at: datetime


def classify_retry(error: Exception) -> RetryDisposition:
    """Classify an original in-process exception under the accepted policy."""

    if isinstance(
        error,
        (
            FastF1SessionLoadError,
            ArchivePersistenceTargetChangedError,
        ),
    ):
        return RetryDisposition.RETRYABLE
    if isinstance(error, ArchiveSessionIdentityError):
        return RetryDisposition.TERMINAL
    if isinstance(error, SQLAlchemyTimeoutError):
        return RetryDisposition.RETRYABLE
    if isinstance(error, DBAPIError):
        if error.connection_invalidated:
            return RetryDisposition.RETRYABLE
        sqlstate = _sqlstate(error)
        if (
            sqlstate is not None
            and (
                sqlstate[:2] in TRANSIENT_SQLSTATE_PREFIXES
                or sqlstate in TRANSIENT_SQLSTATES
            )
        ):
            return RetryDisposition.RETRYABLE
    return RetryDisposition.TERMINAL


def calculate_retry_schedule(
    *,
    database_now: datetime,
    failed_attempt: int,
    jitter_fraction: float,
    settings: BackfillRuntimeSettings,
) -> RetrySchedule:
    """Calculate a deterministic equal-jitter retry schedule."""

    _timezone_aware(database_now, "database_now")
    _positive_integer(failed_attempt, "failed_attempt")
    if failed_attempt >= settings.max_attempts:
        raise RetryBudgetExhaustedError(
            f"attempt {failed_attempt} has exhausted the retry budget"
        )
    if (
        isinstance(jitter_fraction, bool)
        or not isinstance(jitter_fraction, int | float)
        or not math.isfinite(jitter_fraction)
        or not 0 <= jitter_fraction <= 1
    ):
        raise BackfillRuntimePolicyError(
            "jitter_fraction must be between zero and one"
        )

    nominal_seconds = min(
        settings.backoff_base_seconds
        * settings.backoff_multiplier ** (failed_attempt - 1),
        settings.backoff_cap_seconds,
    )
    minimum_seconds = nominal_seconds * settings.jitter_min_ratio
    delay_seconds = minimum_seconds + (
        nominal_seconds - minimum_seconds
    ) * jitter_fraction
    nominal_delay = timedelta(seconds=nominal_seconds)
    delay = timedelta(seconds=delay_seconds)

    return RetrySchedule(
        failed_attempt=failed_attempt,
        nominal_delay=nominal_delay,
        delay=delay,
        next_retry_at=database_now + delay,
    )


def _sqlstate(error: DBAPIError) -> str | None:
    original = error.orig
    value = getattr(original, "sqlstate", None)
    if value is None:
        value = getattr(original, "pgcode", None)
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized or None


def _positive_integer(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BackfillRuntimePolicyError(
            f"{field} must be a positive integer"
        )


def _timezone_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BackfillRuntimePolicyError(
            f"{field} must include a timezone"
        )


def _environment_integer(
    environ: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    raw_value = environ.get(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError:
        raise BackfillRuntimePolicyError(
            f"{name} must be an integer"
        ) from None


def _environment_float(
    environ: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    raw_value = environ.get(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError:
        raise BackfillRuntimePolicyError(
            f"{name} must be a number"
        ) from None


def _environment_integer_tuple(
    environ: Mapping[str, str],
    name: str,
    default: tuple[int, ...],
) -> tuple[int, ...]:
    raw_value = environ.get(name)
    if raw_value is None:
        return default
    try:
        return tuple(
            int(part.strip())
            for part in raw_value.split(",")
            if part.strip()
        )
    except ValueError:
        raise BackfillRuntimePolicyError(
            f"{name} must be a comma-separated list of integers"
        ) from None
