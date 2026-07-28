from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from app.api.contracts import (
    ActiveJobSummary,
    BackfillAction,
    BackfillCoverage,
    CoverageRefreshReason,
    DecimalIdentifier,
    EnsureBackfillResponse,
    FastF1RequestBudgetResponse,
    IngestionStatus,
    JobProgress,
    SeasonCoverage,
)


def test_database_bigint_identifiers_serialize_as_decimal_strings() -> None:
    adapter = TypeAdapter(DecimalIdentifier)
    identifier = adapter.validate_python(9_007_199_254_740_993)

    assert adapter.dump_python(identifier, mode="json") == "9007199254740993"


def test_uuid_identifiers_serialize_canonically() -> None:
    job = ActiveJobSummary(
        id=UUID("3e18c9fd-a8eb-458f-b317-55867afdc53f"),
        status=IngestionStatus.PENDING,
    )
    coverage = BackfillCoverage(
        refresh_reason=CoverageRefreshReason.MISSING,
        refreshed=True,
        checked_at=datetime(2026, 7, 28, 12, tzinfo=UTC),
        valid_until=datetime(2026, 8, 27, 12, tzinfo=UTC),
    )
    response = EnsureBackfillResponse(
        season_year=2024,
        action=BackfillAction.JOB_CREATED,
        coverage=coverage,
        job=job,
        eligible_session_count=72,
        newly_queued_session_count=72,
    )

    assert response.model_dump(mode="json")["job"] == {
        "id": "3e18c9fd-a8eb-458f-b317-55867afdc53f",
        "status": "pending",
    }


def test_contract_timestamps_are_normalized_to_utc() -> None:
    coverage = SeasonCoverage(
        checked_at=datetime(
            2026,
            7,
            28,
            15,
            tzinfo=timezone(timedelta(hours=3)),
        ),
        valid_until=None,
        is_stale=False,
    )

    assert coverage.checked_at == datetime(2026, 7, 28, 12, tzinfo=UTC)


def test_contract_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="timestamp must include a timezone"):
        SeasonCoverage(
            checked_at=datetime(2026, 7, 28, 12),
            valid_until=None,
            is_stale=False,
        )


def test_contract_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SeasonCoverage(
            checked_at=None,
            valid_until=None,
            is_stale=True,
            secret="must-not-pass",
        )


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (
            {
                "total": 2,
                "pending": 1,
                "running": 0,
                "completed": 0,
                "failed": 0,
                "terminal": 0,
            },
            "job progress counts must add up to total",
        ),
        (
            {
                "total": 1,
                "pending": 0,
                "running": 0,
                "completed": 1,
                "failed": 0,
                "terminal": 0,
            },
            "terminal count must equal completed plus failed",
        ),
    ],
)
def test_job_progress_requires_internally_consistent_counts(
    values: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        JobProgress(**values)


def test_request_budget_requires_consistent_request_counts() -> None:
    with pytest.raises(
        ValidationError,
        match="observed requests must equal",
    ):
        FastF1RequestBudgetResponse(
            source="fastf1",
            window_seconds=3600,
            observed_at=datetime(2026, 7, 28, 12, tzinfo=UTC),
            observed_requests=3,
            archive_requests=1,
            schedule_requests=1,
            library_limit=500,
            operational_ceiling=450,
            warning_threshold=400,
            remaining_before_pause=447,
            next_capacity_at=None,
            cooldown_until=None,
            cooldown_reason=None,
            status="available",
            authoritative=False,
        )


def test_backfill_action_requires_matching_job_presence() -> None:
    with pytest.raises(
        ValidationError,
        match="job presence must agree with the backfill action",
    ):
        EnsureBackfillResponse(
            season_year=2024,
            action=BackfillAction.NO_ACTION,
            coverage=BackfillCoverage(
                refresh_reason=CoverageRefreshReason.FRESH,
                refreshed=False,
                checked_at=None,
                valid_until=None,
            ),
            job=ActiveJobSummary(
                id=UUID("3e18c9fd-a8eb-458f-b317-55867afdc53f"),
                status=IngestionStatus.PENDING,
            ),
            eligible_session_count=0,
            newly_queued_session_count=0,
        )
