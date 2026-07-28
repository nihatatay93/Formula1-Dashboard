from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.ingestion.freshness_policy import (
    ArchiveEligibilityReason,
    CoverageRefreshReason,
    evaluate_archive_ingestion,
    evaluate_season_coverage,
)
from app.ingestion.runtime_policy import (
    BackfillRuntimePolicyError,
    BackfillRuntimeSettings,
)

DATABASE_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
SESSION_END = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)


def test_missing_current_season_coverage_requires_six_hour_refresh() -> None:
    decision = evaluate_season_coverage(
        season_year=2026,
        coverage_valid_until=None,
        database_now=DATABASE_NOW,
    )

    assert decision.refresh_required is True
    assert decision.reason is CoverageRefreshReason.MISSING
    assert decision.is_current_season is True
    assert decision.ttl == timedelta(hours=6)
    assert decision.successful_refresh_valid_until == (
        DATABASE_NOW + timedelta(hours=6)
    )


def test_historical_season_coverage_uses_thirty_day_ttl() -> None:
    decision = evaluate_season_coverage(
        season_year=2025,
        coverage_valid_until=DATABASE_NOW + timedelta(days=1),
        database_now=DATABASE_NOW,
    )

    assert decision.refresh_required is False
    assert decision.reason is CoverageRefreshReason.FRESH
    assert decision.is_current_season is False
    assert decision.ttl == timedelta(days=30)
    assert decision.successful_refresh_valid_until == (
        DATABASE_NOW + timedelta(days=30)
    )


def test_coverage_becomes_stale_at_exact_expiry() -> None:
    decision = evaluate_season_coverage(
        season_year=2026,
        coverage_valid_until=DATABASE_NOW,
        database_now=DATABASE_NOW,
    )

    assert decision.refresh_required is True
    assert decision.reason is CoverageRefreshReason.STALE


def test_current_season_is_derived_from_utc_calendar_year() -> None:
    local_new_year = datetime(
        2027,
        1,
        1,
        1,
        0,
        tzinfo=timezone(timedelta(hours=3)),
    )

    decision = evaluate_season_coverage(
        season_year=2026,
        coverage_valid_until=None,
        database_now=local_new_year,
    )

    assert decision.is_current_season is True
    assert decision.ttl == timedelta(hours=6)


def test_custom_coverage_ttls_are_used() -> None:
    settings = BackfillRuntimeSettings(
        current_season_coverage_ttl_seconds=60,
        historical_season_coverage_ttl_seconds=120,
    )

    current = evaluate_season_coverage(
        season_year=2026,
        coverage_valid_until=None,
        database_now=DATABASE_NOW,
        settings=settings,
    )
    historical = evaluate_season_coverage(
        season_year=2025,
        coverage_valid_until=None,
        database_now=DATABASE_NOW,
        settings=settings,
    )

    assert current.ttl == timedelta(seconds=60)
    assert historical.ttl == timedelta(seconds=120)


@pytest.mark.parametrize("season_year", [1949, True, 2026.0])
def test_rejects_invalid_season_year(season_year: object) -> None:
    with pytest.raises(BackfillRuntimePolicyError, match="season_year"):
        evaluate_season_coverage(
            season_year=season_year,  # type: ignore[arg-type]
            coverage_valid_until=None,
            database_now=DATABASE_NOW,
        )


@pytest.mark.parametrize(
    ("database_now", "coverage_valid_until", "field"),
    [
        (datetime(2026, 7, 28, 12, 0), None, "database_now"),
        (
            DATABASE_NOW,
            datetime(2026, 7, 28, 13, 0),
            "coverage_valid_until",
        ),
    ],
)
def test_rejects_naive_coverage_timestamps(
    database_now: datetime,
    coverage_valid_until: datetime | None,
    field: str,
) -> None:
    with pytest.raises(BackfillRuntimePolicyError, match=field):
        evaluate_season_coverage(
            season_year=2026,
            coverage_valid_until=coverage_valid_until,
            database_now=database_now,
        )


def test_missing_schedule_end_is_not_automatically_eligible() -> None:
    decision = evaluate_archive_ingestion(
        scheduled_end_at=None,
        completed_at=None,
        database_now=DATABASE_NOW,
    )

    assert decision.eligible is False
    assert decision.reason is ArchiveEligibilityReason.SCHEDULE_END_MISSING
    assert decision.eligible_at is None
    assert decision.correction_checkpoint_at is None


def test_initial_archive_waits_for_two_hour_grace_period() -> None:
    database_now = SESSION_END + timedelta(hours=2) - timedelta(microseconds=1)

    decision = evaluate_archive_ingestion(
        scheduled_end_at=SESSION_END,
        completed_at=None,
        database_now=database_now,
    )

    assert decision.eligible is False
    assert decision.reason is ArchiveEligibilityReason.AVAILABILITY_GRACE
    assert decision.eligible_at == SESSION_END + timedelta(hours=2)


def test_initial_archive_is_eligible_at_exact_grace_boundary() -> None:
    decision = evaluate_archive_ingestion(
        scheduled_end_at=SESSION_END,
        completed_at=None,
        database_now=SESSION_END + timedelta(hours=2),
    )

    assert decision.eligible is True
    assert decision.reason is ArchiveEligibilityReason.INITIAL_ARCHIVE
    assert decision.eligible_at == SESSION_END + timedelta(hours=2)
    assert decision.correction_checkpoint_at is None


def test_completed_archive_waits_for_first_correction_checkpoint() -> None:
    decision = evaluate_archive_ingestion(
        scheduled_end_at=SESSION_END,
        completed_at=SESSION_END + timedelta(hours=3),
        database_now=SESSION_END + timedelta(hours=12),
    )

    expected_checkpoint = SESSION_END + timedelta(hours=24)
    assert decision.eligible is False
    assert decision.reason is ArchiveEligibilityReason.CHECKPOINT_PENDING
    assert decision.eligible_at == expected_checkpoint
    assert decision.correction_checkpoint_at == expected_checkpoint


def test_first_correction_is_eligible_at_exact_checkpoint() -> None:
    checkpoint = SESSION_END + timedelta(hours=24)

    decision = evaluate_archive_ingestion(
        scheduled_end_at=SESSION_END,
        completed_at=SESSION_END + timedelta(hours=3),
        database_now=checkpoint,
    )

    assert decision.eligible is True
    assert decision.reason is ArchiveEligibilityReason.CORRECTION_CHECKPOINT
    assert decision.eligible_at == checkpoint
    assert decision.correction_checkpoint_at == checkpoint


def test_completion_at_checkpoint_satisfies_that_checkpoint() -> None:
    first_checkpoint = SESSION_END + timedelta(hours=24)
    final_checkpoint = SESSION_END + timedelta(days=7)

    decision = evaluate_archive_ingestion(
        scheduled_end_at=SESSION_END,
        completed_at=first_checkpoint,
        database_now=first_checkpoint,
    )

    assert decision.eligible is False
    assert decision.reason is ArchiveEligibilityReason.CHECKPOINT_PENDING
    assert decision.eligible_at == final_checkpoint
    assert decision.correction_checkpoint_at == final_checkpoint


def test_late_scan_uses_latest_due_checkpoint() -> None:
    final_checkpoint = SESSION_END + timedelta(days=7)

    decision = evaluate_archive_ingestion(
        scheduled_end_at=SESSION_END,
        completed_at=SESSION_END + timedelta(hours=3),
        database_now=final_checkpoint,
    )

    assert decision.eligible is True
    assert decision.reason is ArchiveEligibilityReason.CORRECTION_CHECKPOINT
    assert decision.eligible_at == final_checkpoint
    assert decision.correction_checkpoint_at == final_checkpoint


def test_archive_completed_after_first_checkpoint_waits_for_final_checkpoint() -> None:
    final_checkpoint = SESSION_END + timedelta(days=7)

    decision = evaluate_archive_ingestion(
        scheduled_end_at=SESSION_END,
        completed_at=SESSION_END + timedelta(days=2),
        database_now=SESSION_END + timedelta(days=3),
    )

    assert decision.eligible is False
    assert decision.reason is ArchiveEligibilityReason.CHECKPOINT_PENDING
    assert decision.eligible_at == final_checkpoint


def test_archive_is_stable_after_success_at_final_checkpoint() -> None:
    completion = SESSION_END + timedelta(days=7)

    decision = evaluate_archive_ingestion(
        scheduled_end_at=SESSION_END,
        completed_at=completion,
        database_now=completion,
    )

    assert decision.eligible is False
    assert decision.reason is ArchiveEligibilityReason.STABLE
    assert decision.eligible_at is None
    assert decision.correction_checkpoint_at is None


def test_custom_grace_and_checkpoints_are_used() -> None:
    settings = BackfillRuntimeSettings(
        archive_availability_grace_seconds=60,
        archive_correction_checkpoints_seconds=(120, 180),
    )

    initial = evaluate_archive_ingestion(
        scheduled_end_at=SESSION_END,
        completed_at=None,
        database_now=SESSION_END + timedelta(seconds=60),
        settings=settings,
    )
    correction = evaluate_archive_ingestion(
        scheduled_end_at=SESSION_END,
        completed_at=SESSION_END + timedelta(seconds=90),
        database_now=SESSION_END + timedelta(seconds=120),
        settings=settings,
    )

    assert initial.eligible is True
    assert initial.reason is ArchiveEligibilityReason.INITIAL_ARCHIVE
    assert correction.eligible is True
    assert correction.reason is ArchiveEligibilityReason.CORRECTION_CHECKPOINT
    assert correction.correction_checkpoint_at == (
        SESSION_END + timedelta(seconds=120)
    )


@pytest.mark.parametrize(
    ("scheduled_end_at", "completed_at", "database_now", "field"),
    [
        (
            datetime(2026, 7, 20, 15, 0),
            None,
            DATABASE_NOW,
            "scheduled_end_at",
        ),
        (
            SESSION_END,
            datetime(2026, 7, 20, 18, 0),
            DATABASE_NOW,
            "completed_at",
        ),
        (
            SESSION_END,
            None,
            datetime(2026, 7, 20, 18, 0),
            "database_now",
        ),
    ],
)
def test_rejects_naive_archive_timestamps(
    scheduled_end_at: datetime | None,
    completed_at: datetime | None,
    database_now: datetime,
    field: str,
) -> None:
    with pytest.raises(BackfillRuntimePolicyError, match=field):
        evaluate_archive_ingestion(
            scheduled_end_at=scheduled_end_at,
            completed_at=completed_at,
            database_now=database_now,
        )


def test_rejects_future_completion_timestamp() -> None:
    with pytest.raises(BackfillRuntimePolicyError, match="completed_at"):
        evaluate_archive_ingestion(
            scheduled_end_at=SESSION_END,
            completed_at=DATABASE_NOW + timedelta(seconds=1),
            database_now=DATABASE_NOW,
        )
