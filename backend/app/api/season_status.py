from __future__ import annotations

from dataclasses import dataclass, fields

from app.api.contracts import SeasonStatus


class SeasonStatusPolicyError(ValueError):
    """Raised when aggregated season facts violate the policy input contract."""


@dataclass(frozen=True, slots=True)
class SeasonStatusFacts:
    data_available_count: int = 0
    required_pending_count: int = 0
    required_running_count: int = 0
    required_failed_count: int = 0
    required_refresh_count: int = 0
    has_active_job: bool = False
    coverage_is_stale: bool = True

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name in {"has_active_job", "coverage_is_stale"}:
                if not isinstance(value, bool):
                    raise SeasonStatusPolicyError(
                        f"{field.name} must be a boolean"
                    )
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise SeasonStatusPolicyError(
                    f"{field.name} must be a non-negative integer"
                )


def derive_season_status(facts: SeasonStatusFacts) -> SeasonStatus:
    """Derive the accepted season status from aggregate read-model facts."""

    has_data = facts.data_available_count > 0
    has_required_gap = any(
        count > 0
        for count in (
            facts.required_pending_count,
            facts.required_running_count,
            facts.required_failed_count,
            facts.required_refresh_count,
        )
    )

    if has_data and has_required_gap:
        return SeasonStatus.PARTIAL
    if facts.required_running_count > 0:
        return SeasonStatus.RUNNING
    if facts.has_active_job or facts.required_pending_count > 0:
        return SeasonStatus.PENDING
    if facts.required_failed_count > 0:
        return SeasonStatus.FAILED
    if has_data and facts.coverage_is_stale:
        return SeasonStatus.STALE
    if has_data:
        return SeasonStatus.COMPLETED
    return SeasonStatus.MISSING

