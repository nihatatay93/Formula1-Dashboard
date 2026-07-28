import pytest

from app.api.contracts import SeasonStatus
from app.api.season_status import (
    SeasonStatusFacts,
    SeasonStatusPolicyError,
    derive_season_status,
)


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (SeasonStatusFacts(), SeasonStatus.MISSING),
        (
            SeasonStatusFacts(
                data_available_count=1,
                required_pending_count=1,
                coverage_is_stale=False,
            ),
            SeasonStatus.PARTIAL,
        ),
        (
            SeasonStatusFacts(
                data_available_count=1,
                required_running_count=1,
                coverage_is_stale=True,
            ),
            SeasonStatus.PARTIAL,
        ),
        (
            SeasonStatusFacts(required_running_count=1),
            SeasonStatus.RUNNING,
        ),
        (
            SeasonStatusFacts(
                required_pending_count=1,
                required_failed_count=1,
            ),
            SeasonStatus.PENDING,
        ),
        (
            SeasonStatusFacts(
                required_failed_count=1,
                has_active_job=True,
            ),
            SeasonStatus.PENDING,
        ),
        (
            SeasonStatusFacts(required_failed_count=1),
            SeasonStatus.FAILED,
        ),
        (
            SeasonStatusFacts(
                data_available_count=1,
                coverage_is_stale=True,
            ),
            SeasonStatus.STALE,
        ),
        (
            SeasonStatusFacts(
                data_available_count=1,
                coverage_is_stale=False,
            ),
            SeasonStatus.COMPLETED,
        ),
        (
            SeasonStatusFacts(
                required_refresh_count=1,
                coverage_is_stale=False,
            ),
            SeasonStatus.MISSING,
        ),
    ],
)
def test_derived_season_status_precedence(
    facts: SeasonStatusFacts,
    expected: SeasonStatus,
) -> None:
    assert derive_season_status(facts) is expected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("data_available_count", -1),
        ("required_pending_count", True),
        ("required_running_count", 1.5),
        ("required_failed_count", "1"),
        ("required_refresh_count", None),
        ("has_active_job", 1),
        ("coverage_is_stale", "yes"),
    ],
)
def test_season_status_facts_reject_invalid_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises(SeasonStatusPolicyError, match=field):
        SeasonStatusFacts(**{field: value})

