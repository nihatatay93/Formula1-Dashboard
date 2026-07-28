from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.ingestion.runtime_policy import (
    BackfillRuntimePolicyError,
    BackfillRuntimeSettings,
)


class CoverageRefreshReason(StrEnum):
    FRESH = "fresh"
    MISSING = "missing"
    STALE = "stale"


class ArchiveEligibilityReason(StrEnum):
    SCHEDULE_END_MISSING = "schedule_end_missing"
    AVAILABILITY_GRACE = "availability_grace"
    INITIAL_ARCHIVE = "initial_archive"
    CHECKPOINT_PENDING = "checkpoint_pending"
    CORRECTION_CHECKPOINT = "correction_checkpoint"
    STABLE = "stable"


@dataclass(frozen=True, slots=True)
class SeasonCoverageEligibility:
    season_year: int
    is_current_season: bool
    refresh_required: bool
    reason: CoverageRefreshReason
    ttl: timedelta
    successful_refresh_valid_until: datetime


@dataclass(frozen=True, slots=True)
class ArchiveIngestionEligibility:
    eligible: bool
    reason: ArchiveEligibilityReason
    eligible_at: datetime | None
    correction_checkpoint_at: datetime | None


def evaluate_season_coverage(
    *,
    season_year: int,
    coverage_valid_until: datetime | None,
    database_now: datetime,
    settings: BackfillRuntimeSettings | None = None,
) -> SeasonCoverageEligibility:
    """Evaluate schedule-coverage freshness using the database clock."""

    _season_year(season_year)
    _timezone_aware(database_now, "database_now")
    if coverage_valid_until is not None:
        _timezone_aware(coverage_valid_until, "coverage_valid_until")

    resolved_settings = settings or BackfillRuntimeSettings()
    is_current_season = season_year == database_now.astimezone(UTC).year
    ttl = (
        resolved_settings.current_season_coverage_ttl
        if is_current_season
        else resolved_settings.historical_season_coverage_ttl
    )

    if coverage_valid_until is None:
        refresh_required = True
        reason = CoverageRefreshReason.MISSING
    elif coverage_valid_until <= database_now:
        refresh_required = True
        reason = CoverageRefreshReason.STALE
    else:
        refresh_required = False
        reason = CoverageRefreshReason.FRESH

    return SeasonCoverageEligibility(
        season_year=season_year,
        is_current_season=is_current_season,
        refresh_required=refresh_required,
        reason=reason,
        ttl=ttl,
        successful_refresh_valid_until=database_now + ttl,
    )


def evaluate_archive_ingestion(
    *,
    scheduled_end_at: datetime | None,
    completed_at: datetime | None,
    database_now: datetime,
    settings: BackfillRuntimeSettings | None = None,
) -> ArchiveIngestionEligibility:
    """Evaluate initial archive and correction-checkpoint eligibility."""

    _timezone_aware(database_now, "database_now")
    if scheduled_end_at is None:
        if completed_at is not None:
            _timezone_aware(completed_at, "completed_at")
            _not_in_future(completed_at, database_now, "completed_at")
        return ArchiveIngestionEligibility(
            eligible=False,
            reason=ArchiveEligibilityReason.SCHEDULE_END_MISSING,
            eligible_at=None,
            correction_checkpoint_at=None,
        )

    _timezone_aware(scheduled_end_at, "scheduled_end_at")
    if completed_at is not None:
        _timezone_aware(completed_at, "completed_at")
        _not_in_future(completed_at, database_now, "completed_at")

    resolved_settings = settings or BackfillRuntimeSettings()
    initial_eligible_at = (
        scheduled_end_at + resolved_settings.archive_availability_grace
    )

    if completed_at is None:
        if database_now < initial_eligible_at:
            return ArchiveIngestionEligibility(
                eligible=False,
                reason=ArchiveEligibilityReason.AVAILABILITY_GRACE,
                eligible_at=initial_eligible_at,
                correction_checkpoint_at=None,
            )
        return ArchiveIngestionEligibility(
            eligible=True,
            reason=ArchiveEligibilityReason.INITIAL_ARCHIVE,
            eligible_at=initial_eligible_at,
            correction_checkpoint_at=None,
        )

    correction_checkpoints = tuple(
        scheduled_end_at + offset
        for offset in resolved_settings.archive_correction_checkpoints
    )
    unsatisfied_checkpoints = tuple(
        checkpoint
        for checkpoint in correction_checkpoints
        if completed_at < checkpoint
    )
    due_checkpoints = tuple(
        checkpoint
        for checkpoint in unsatisfied_checkpoints
        if checkpoint <= database_now
    )

    if due_checkpoints:
        checkpoint = due_checkpoints[-1]
        return ArchiveIngestionEligibility(
            eligible=True,
            reason=ArchiveEligibilityReason.CORRECTION_CHECKPOINT,
            eligible_at=checkpoint,
            correction_checkpoint_at=checkpoint,
        )
    if unsatisfied_checkpoints:
        checkpoint = unsatisfied_checkpoints[0]
        return ArchiveIngestionEligibility(
            eligible=False,
            reason=ArchiveEligibilityReason.CHECKPOINT_PENDING,
            eligible_at=checkpoint,
            correction_checkpoint_at=checkpoint,
        )
    return ArchiveIngestionEligibility(
        eligible=False,
        reason=ArchiveEligibilityReason.STABLE,
        eligible_at=None,
        correction_checkpoint_at=None,
    )


def _season_year(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1950:
        raise BackfillRuntimePolicyError(
            "season_year must be an integer greater than or equal to 1950"
        )


def _timezone_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BackfillRuntimePolicyError(
            f"{field} must include a timezone"
        )


def _not_in_future(
    value: datetime,
    database_now: datetime,
    field: str,
) -> None:
    if value > database_now:
        raise BackfillRuntimePolicyError(
            f"{field} must not be later than database_now"
        )
