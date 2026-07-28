from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
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


def _nonnegative_decimal_string(value: object) -> str:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("decimal value must be an exact non-negative number")
    if isinstance(value, Decimal):
        decimal_value = value
    elif isinstance(value, int):
        decimal_value = Decimal(value)
    elif isinstance(value, str) and value and value == value.strip():
        try:
            decimal_value = Decimal(value)
        except InvalidOperation as error:
            raise ValueError(
                "decimal value must be an exact non-negative number"
            ) from error
    else:
        raise ValueError("decimal value must be an exact non-negative number")

    if not decimal_value.is_finite() or decimal_value < 0:
        raise ValueError("decimal value must be an exact non-negative number")
    if decimal_value == 0:
        decimal_value = decimal_value.copy_abs()
    return format(decimal_value, "f")


DecimalIdentifier = Annotated[str, BeforeValidator(_decimal_identifier)]
NonnegativeDecimalString = Annotated[
    str,
    BeforeValidator(_nonnegative_decimal_string),
]
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


class DataSource(StrEnum):
    LIVE_SIGNALR = "live_signalr"
    FASTF1_ARCHIVE = "fastf1_archive"
    JOLPICA = "jolpica"


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


class DeferredFutureEvent(ApiModel):
    round_number: int = Field(ge=1)
    event_name: str = Field(min_length=1)
    scheduled_start_at: UtcDatetime


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


class SessionSnapshot(ApiModel):
    data_available: bool
    source: DataSource | None
    record_state: RecordState | None
    completed_at: UtcDatetime | None
    source_updated_at: UtcDatetime | None

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        required_values = (
            self.source,
            self.record_state,
            self.completed_at,
        )
        if self.data_available and any(value is None for value in required_values):
            raise ValueError(
                "available snapshot requires source, record state, and completion time"
            )
        if not self.data_available and any(
            value is not None
            for value in (*required_values, self.source_updated_at)
        ):
            raise ValueError(
                "unavailable snapshot cannot expose completed snapshot metadata"
            )
        return self


class SessionDetailIngestion(ApiModel):
    status: IngestionStatus
    source: DataSource
    record_state: RecordState
    attempt_count: int = Field(ge=0)
    completed_at: UtcDatetime | None
    next_retry_at: UtcDatetime | None
    last_error: LastError | None


class SessionDetailEvent(ApiModel):
    id: DecimalIdentifier
    season_year: int = Field(ge=2018)
    round_number: int = Field(ge=1)
    official_name: str | None
    event_name: str = Field(min_length=1)
    country: str | None
    location: str | None
    event_format: str | None


class SessionDetailCounts(ApiModel):
    entries: int = Field(ge=0)
    results: int = Field(ge=0)
    laps: int = Field(ge=0)


class SessionDetailResponse(ApiModel):
    id: DecimalIdentifier
    session_key: str = Field(min_length=1)
    session_name: str = Field(min_length=1)
    scheduled_start_at: UtcDatetime | None
    scheduled_end_at: UtcDatetime | None
    event: SessionDetailEvent
    snapshot: SessionSnapshot
    ingestion: SessionDetailIngestion | None
    counts: SessionDetailCounts

    @model_validator(mode="after")
    def validate_unavailable_counts(self) -> Self:
        if not self.snapshot.data_available and any(
            (
                self.counts.entries,
                self.counts.results,
                self.counts.laps,
            )
        ):
            raise ValueError("unavailable session snapshot must have zero counts")
        return self


class SessionResultDriver(ApiModel):
    id: DecimalIdentifier
    jolpica_driver_id: str | None
    given_name: str | None
    family_name: str | None
    full_name: str = Field(min_length=1)
    country_code: str | None


class SessionResultData(ApiModel):
    position: int | None = Field(ge=1)
    classified_position: str | None
    grid_position: int | None = Field(ge=0)
    points: NonnegativeDecimalString | None
    status: str | None
    laps_completed: int | None = Field(ge=0)
    q1_time_us: int | None = Field(ge=0)
    q2_time_us: int | None = Field(ge=0)
    q3_time_us: int | None = Field(ge=0)
    elapsed_time_us: int | None = Field(ge=0)
    gap_to_leader_us: int | None = Field(ge=0)
    gap_to_leader_laps: int | None = Field(ge=0)
    source: DataSource
    record_state: RecordState


class SessionEntryResult(ApiModel):
    session_entry_id: DecimalIdentifier
    driver: SessionResultDriver | None
    racing_number: str | None
    abbreviation: str | None
    broadcast_name: str | None
    display_name: str = Field(min_length=1)
    team_jolpica_id: str | None
    team_name: str | None
    team_color_hex: str | None = Field(pattern=r"^#[0-9A-F]{6}$")
    source: DataSource
    record_state: RecordState
    result: SessionResultData | None


class SessionResultsResponse(ApiModel):
    session_id: DecimalIdentifier
    snapshot: SessionSnapshot
    items: tuple[SessionEntryResult, ...]

    @model_validator(mode="after")
    def validate_available_ordered_snapshot(self) -> Self:
        if not self.snapshot.data_available:
            raise ValueError("result response requires an available snapshot")

        entry_ids = [item.session_entry_id for item in self.items]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("result items must have unique session entry IDs")

        ordered_items = sorted(
            self.items,
            key=lambda item: (
                item.result is None or item.result.position is None,
                (
                    item.result.position
                    if item.result is not None
                    and item.result.position is not None
                    else 0
                ),
                int(item.session_entry_id),
            ),
        )
        if list(self.items) != ordered_items:
            raise ValueError(
                "result items must be ordered by position and session entry"
            )
        return self


class LapSummaryQuery(ApiModel):
    after_lap: int | None = Field(default=None, ge=0)
    limit: int = Field(default=50, ge=1, le=100)
    lap_from: int | None = Field(default=None, ge=1)
    lap_to: int | None = Field(default=None, ge=1)
    stint_number: int | None = Field(default=None, ge=1)
    include_deleted: bool = True

    @model_validator(mode="after")
    def validate_lap_range(self) -> Self:
        if (
            self.lap_from is not None
            and self.lap_to is not None
            and self.lap_from > self.lap_to
        ):
            raise ValueError("lap_from cannot be greater than lap_to")
        return self


class LapSummaryFilters(ApiModel):
    lap_from: int | None = Field(ge=1)
    lap_to: int | None = Field(ge=1)
    stint_number: int | None = Field(ge=1)
    include_deleted: bool

    @model_validator(mode="after")
    def validate_lap_range(self) -> Self:
        if (
            self.lap_from is not None
            and self.lap_to is not None
            and self.lap_from > self.lap_to
        ):
            raise ValueError("lap_from cannot be greater than lap_to")
        return self


class LapSummaryPage(ApiModel):
    limit: int = Field(ge=1, le=100)
    has_more: bool
    next_after_lap: int | None = Field(ge=1)

    @model_validator(mode="after")
    def validate_cursor(self) -> Self:
        if self.has_more != (self.next_after_lap is not None):
            raise ValueError(
                "next lap cursor presence must agree with has_more"
            )
        return self


class LapSummary(ApiModel):
    id: DecimalIdentifier
    lap_number: int = Field(ge=1)
    stint_number: int | None = Field(ge=1)
    session_time_us: int | None = Field(ge=0)
    lap_time_us: int | None = Field(ge=0)
    lap_start_time_us: int | None = Field(ge=0)
    pit_out_time_us: int | None = Field(ge=0)
    pit_in_time_us: int | None = Field(ge=0)
    sector_1_time_us: int | None = Field(ge=0)
    sector_2_time_us: int | None = Field(ge=0)
    sector_3_time_us: int | None = Field(ge=0)
    sector_1_session_time_us: int | None = Field(ge=0)
    sector_2_session_time_us: int | None = Field(ge=0)
    sector_3_session_time_us: int | None = Field(ge=0)
    speed_i1_kph: float | None = Field(ge=0, allow_inf_nan=False)
    speed_i2_kph: float | None = Field(ge=0, allow_inf_nan=False)
    speed_fl_kph: float | None = Field(ge=0, allow_inf_nan=False)
    speed_st_kph: float | None = Field(ge=0, allow_inf_nan=False)
    is_personal_best: bool | None
    compound: str | None
    tyre_life_laps: int | None = Field(ge=0)
    fresh_tyre: bool | None
    track_status: str | None
    position: int | None = Field(ge=1)
    deleted: bool | None
    deleted_reason: str | None
    fastf1_generated: bool
    is_accurate: bool
    source: DataSource
    record_state: RecordState


class LapSummaryResponse(ApiModel):
    session_id: DecimalIdentifier
    session_entry_id: DecimalIdentifier
    snapshot: SessionSnapshot
    filters: LapSummaryFilters
    page: LapSummaryPage
    items: tuple[LapSummary, ...]

    @model_validator(mode="after")
    def validate_page(self) -> Self:
        if not self.snapshot.data_available:
            raise ValueError("lap response requires an available snapshot")
        if len(self.items) > self.page.limit:
            raise ValueError("lap item count cannot exceed the page limit")

        lap_numbers = [item.lap_number for item in self.items]
        if lap_numbers != sorted(set(lap_numbers)):
            raise ValueError("lap items must have unique ascending lap numbers")

        if self.page.has_more:
            if not self.items:
                raise ValueError("a continued lap page must contain items")
            if self.page.next_after_lap != self.items[-1].lap_number:
                raise ValueError(
                    "next lap cursor must equal the last returned lap"
                )

        for item in self.items:
            if (
                self.filters.lap_from is not None
                and item.lap_number < self.filters.lap_from
            ):
                raise ValueError("lap item is below the response lower bound")
            if (
                self.filters.lap_to is not None
                and item.lap_number > self.filters.lap_to
            ):
                raise ValueError("lap item is above the response upper bound")
            if (
                self.filters.stint_number is not None
                and item.stint_number != self.filters.stint_number
            ):
                raise ValueError("lap item does not match the response stint")
            if not self.filters.include_deleted and item.deleted is True:
                raise ValueError("deleted lap cannot appear when excluded")
        return self


class TelemetryCommandAction(StrEnum):
    QUEUED = "queued"
    REUSED = "reused"
    AVAILABLE = "available"


class EnsureLapTelemetryResponse(ApiModel):
    session_id: DecimalIdentifier
    session_entry_id: DecimalIdentifier
    lap_id: DecimalIdentifier
    lap_number: int = Field(ge=1)
    action: TelemetryCommandAction
    status: IngestionStatus
    source_snapshot_completed_at: UtcDatetime


class LapTelemetryIngestionState(ApiModel):
    status: IngestionStatus
    attempt_count: int = Field(ge=0)
    sample_count: int = Field(ge=0)
    requested_at: UtcDatetime
    heartbeat_at: UtcDatetime | None
    next_retry_at: UtcDatetime | None
    completed_at: UtcDatetime | None
    last_error: LastError | None


class LapTelemetrySnapshot(ApiModel):
    compatible: bool
    source_snapshot_completed_at: UtcDatetime
    current_snapshot_completed_at: UtcDatetime


class LapTelemetryPage(ApiModel):
    limit: int = Field(ge=1, le=1000)
    has_more: bool
    next_after_sample: int | None = Field(ge=0)

    @model_validator(mode="after")
    def validate_cursor(self) -> Self:
        if self.has_more != (self.next_after_sample is not None):
            raise ValueError(
                "next sample cursor presence must agree with has_more"
            )
        return self


class LapTelemetrySampleData(ApiModel):
    sample_index: int = Field(ge=0)
    lap_time_us: int = Field(ge=0)
    session_time_us: int | None = Field(ge=0)
    distance_m: float | None = Field(ge=0, allow_inf_nan=False)
    relative_distance: float | None = Field(
        ge=0,
        le=1.01,
        allow_inf_nan=False,
    )
    speed_kph: float | None = Field(ge=0, allow_inf_nan=False)
    rpm: int | None = Field(ge=0)
    gear: int | None = Field(ge=0, le=20)
    throttle_percent: float | None = Field(
        ge=0,
        le=100,
        allow_inf_nan=False,
    )
    brake: bool | None
    drs: int | None = Field(ge=0, le=20)
    x: float | None = Field(allow_inf_nan=False)
    y: float | None = Field(allow_inf_nan=False)
    z: float | None = Field(allow_inf_nan=False)


class LapTelemetryResponse(ApiModel):
    session_id: DecimalIdentifier
    session_entry_id: DecimalIdentifier
    lap_id: DecimalIdentifier
    lap_number: int = Field(ge=1)
    data_available: bool
    snapshot: LapTelemetrySnapshot
    ingestion: LapTelemetryIngestionState
    page: LapTelemetryPage
    items: tuple[LapTelemetrySampleData, ...]

    @model_validator(mode="after")
    def validate_page(self) -> Self:
        if len(self.items) > self.page.limit:
            raise ValueError("telemetry item count cannot exceed page limit")
        indices = [item.sample_index for item in self.items]
        if indices != sorted(set(indices)):
            raise ValueError(
                "telemetry samples must have unique ascending indices"
            )
        if not self.data_available and self.items:
            raise ValueError(
                "unavailable telemetry cannot return sample rows"
            )
        if self.page.has_more:
            if not self.items:
                raise ValueError("a continued telemetry page requires items")
            if self.page.next_after_sample != self.items[-1].sample_index:
                raise ValueError(
                    "next sample cursor must equal the last returned sample"
                )
        return self


class EnsureBackfillResponse(ApiModel):
    season_year: int = Field(ge=2018)
    action: BackfillAction
    coverage: BackfillCoverage
    job: ActiveJobSummary | None
    eligible_session_count: int = Field(ge=0)
    newly_queued_session_count: int = Field(ge=0)
    deferred_future_events: tuple[DeferredFutureEvent, ...] = ()

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
    telemetry_requests: int = Field(ge=0)
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
            self.archive_requests
            + self.schedule_requests
            + self.telemetry_requests
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
