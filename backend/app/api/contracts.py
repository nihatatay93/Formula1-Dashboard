from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)


def _decimal_identifier(value: object) -> str:
    if isinstance(value, bool):
        raise ValueError("identifier must be a positive decimal integer")
    if isinstance(value, int):
        if value < 1:
            raise ValueError("identifier must be a positive decimal integer")
        return str(value)
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError("identifier must be a positive decimal integer")
    if value == "0" or value.startswith("0"):
        raise ValueError("identifier must use canonical positive decimal form")
    return value


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC)


DecimalIdentifier = Annotated[str, BeforeValidator(_decimal_identifier)]
UtcDatetime = Annotated[datetime, AfterValidator(_utc_datetime)]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IngestionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RecordState(StrEnum):
    PROVISIONAL = "provisional"
    FINALIZED = "finalized"


class SeasonStatus(StrEnum):
    MISSING = "missing"
    PENDING = "pending"
    RUNNING = "running"
    PARTIAL = "partial"
    COMPLETED = "completed"
    STALE = "stale"
    FAILED = "failed"


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


class BackfillAction(StrEnum):
    JOB_CREATED = "job_created"
    JOB_REUSED = "job_reused"
    COVERAGE_REFRESHED = "coverage_refreshed"
    NO_ACTION = "no_action"


class BackfillExecutionPhase(StrEnum):
    READY = "ready"
    FETCHING = "fetching"
    PACING = "pacing"
    RATE_LIMIT_COOLDOWN = "rate_limit_cooldown"
    REQUEST_BUDGET_COOLDOWN = "request_budget_cooldown"
    RETRY_BACKOFF = "retry_backoff"
    IDLE = "idle"
    TERMINAL = "terminal"


class RequestBudgetStatus(StrEnum):
    AVAILABLE = "available"
    WARNING = "warning"
    PAUSED = "paused"
    RATE_LIMITED = "rate_limited"


class ErrorDetail(ApiModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ErrorResponse(ApiModel):
    detail: ErrorDetail


class LastError(ApiModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class SeasonCoverage(ApiModel):
    checked_at: UtcDatetime | None
    valid_until: UtcDatetime | None
    is_stale: bool


class BackfillCoverage(ApiModel):
    refresh_reason: CoverageRefreshReason
    refreshed: bool
    checked_at: UtcDatetime | None
    valid_until: UtcDatetime | None


class ActiveJobSummary(ApiModel):
    id: uuid.UUID
    status: IngestionStatus


class SeasonCounts(ApiModel):
    events: int = Field(ge=0)
    sessions: int = Field(ge=0)
    archive_eligible: int = Field(ge=0)
    data_available: int = Field(ge=0)
    pending: int = Field(ge=0)
    running: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)


class ArchiveEligibility(ApiModel):
    eligible: bool
    reason: ArchiveEligibilityReason
    eligible_at: UtcDatetime | None


class SessionIngestion(ApiModel):
    status: IngestionStatus
    record_state: RecordState
    attempt_count: int = Field(ge=0)
    completed_at: UtcDatetime | None
    next_retry_at: UtcDatetime | None
    last_error: LastError | None


class SeasonSession(ApiModel):
    id: DecimalIdentifier
    session_key: str = Field(min_length=1)
    session_name: str = Field(min_length=1)
    scheduled_start_at: UtcDatetime | None
    scheduled_end_at: UtcDatetime | None
    archive_eligibility: ArchiveEligibility
    ingestion: SessionIngestion | None
    data_available: bool


class SeasonEvent(ApiModel):
    id: DecimalIdentifier
    round_number: int = Field(ge=1)
    official_name: str | None
    event_name: str = Field(min_length=1)
    country: str | None
    location: str | None
    event_format: str | None
    starts_at: UtcDatetime | None
    ends_at: UtcDatetime | None
    sessions: tuple[SeasonSession, ...]


class SeasonOverviewResponse(ApiModel):
    year: int = Field(ge=2018)
    status: SeasonStatus
    coverage: SeasonCoverage
    counts: SeasonCounts
    active_job: ActiveJobSummary | None
    events: tuple[SeasonEvent, ...]


class EnsureBackfillResponse(ApiModel):
    season_year: int = Field(ge=2018)
    action: BackfillAction
    coverage: BackfillCoverage
    job: ActiveJobSummary | None
    eligible_session_count: int = Field(ge=0)
    newly_queued_session_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_action_job_consistency(self) -> Self:
        action_requires_job = self.action in {
            BackfillAction.JOB_CREATED,
            BackfillAction.JOB_REUSED,
        }
        if action_requires_job != (self.job is not None):
            raise ValueError("job presence must agree with the backfill action")
        if self.newly_queued_session_count > self.eligible_session_count:
            raise ValueError(
                "newly queued session count cannot exceed eligible session count"
            )
        return self


class JobProgress(ApiModel):
    total: int = Field(ge=0)
    pending: int = Field(ge=0)
    running: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    terminal: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.total != self.pending + self.running + self.completed + self.failed:
            raise ValueError("job progress counts must add up to total")
        if self.terminal != self.completed + self.failed:
            raise ValueError("terminal count must equal completed plus failed")
        return self


class BackfillJobSession(ApiModel):
    session_id: DecimalIdentifier
    round_number: int = Field(ge=1)
    event_name: str = Field(min_length=1)
    session_key: str = Field(min_length=1)
    session_name: str = Field(min_length=1)
    status: IngestionStatus
    attempt_count: int = Field(ge=0)
    queued_at: UtcDatetime
    started_at: UtcDatetime | None
    heartbeat_at: UtcDatetime | None
    next_retry_at: UtcDatetime | None
    completed_at: UtcDatetime | None
    last_error: LastError | None


class BackfillSessionReference(ApiModel):
    session_id: DecimalIdentifier
    round_number: int = Field(ge=1)
    event_name: str = Field(min_length=1)
    session_name: str = Field(min_length=1)


class BackfillExecution(ApiModel):
    observed_at: UtcDatetime
    phase: BackfillExecutionPhase
    current_session: BackfillSessionReference | None
    next_session: BackfillSessionReference | None
    last_completed_session: BackfillSessionReference | None
    next_action_at: UtcDatetime | None


class BackfillJobResponse(ApiModel):
    id: uuid.UUID
    season_year: int = Field(ge=2018)
    status: IngestionStatus
    request_reason: str = Field(min_length=1)
    requested_at: UtcDatetime
    started_at: UtcDatetime | None
    heartbeat_at: UtcDatetime | None
    completed_at: UtcDatetime | None
    last_error: LastError | None
    progress: JobProgress
    execution: BackfillExecution
    sessions: tuple[BackfillJobSession, ...]


class FastF1RequestBudgetResponse(ApiModel):
    source: str = Field(pattern="^fastf1$")
    window_seconds: int = Field(gt=0)
    observed_at: UtcDatetime
    observed_requests: int = Field(ge=0)
    archive_requests: int = Field(ge=0)
    schedule_requests: int = Field(ge=0)
    library_limit: int = Field(gt=0)
    operational_ceiling: int = Field(gt=0)
    warning_threshold: int = Field(gt=0)
    remaining_before_pause: int = Field(ge=0)
    next_capacity_at: UtcDatetime | None
    cooldown_until: UtcDatetime | None
    cooldown_reason: str | None
    status: RequestBudgetStatus
    authoritative: bool

    @model_validator(mode="after")
    def validate_request_counts(self) -> Self:
        if self.observed_requests != (
            self.archive_requests + self.schedule_requests
        ):
            raise ValueError(
                "observed requests must equal archive plus schedule requests"
            )
        if not (
            self.warning_threshold
            < self.operational_ceiling
            < self.library_limit
        ):
            raise ValueError("request-budget thresholds are inconsistent")
        return self
