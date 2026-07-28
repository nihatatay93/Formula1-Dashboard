from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Event as ThreadEvent
from threading import Lock, Thread
from typing import Protocol

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    Event,
    Lap,
    LapTelemetryIngestion,
    LapTelemetrySample,
    RaceSession,
    SessionEntry,
    SessionIngestion,
)
from app.ingestion.fastf1_loader import (
    FastF1SessionLoadError,
    FastF1TelemetryRequest,
    LoadedFastF1Telemetry,
)
from app.ingestion.request_budget_errors import (
    FastF1RequestBudgetExhaustedError,
)
from app.ingestion.runtime_policy import (
    BackfillRuntimeSettings,
    calculate_retry_schedule,
)
from app.ingestion.telemetry_normalization import (
    NormalizedTelemetrySample,
    TelemetryNormalizationError,
    normalize_fastf1_telemetry,
)

SessionFactory = Callable[[], Session]


class FastF1TelemetryLoaderProtocol(Protocol):
    def load_telemetry(
        self,
        request: FastF1TelemetryRequest,
    ) -> LoadedFastF1Telemetry: ...


class TelemetryIngestionError(RuntimeError):
    """Base error for bounded telemetry orchestration."""


class TelemetryTargetNotFoundError(TelemetryIngestionError):
    """Raised when the requested session, entry, or lap does not exist."""


class TelemetryDataUnavailableError(TelemetryIngestionError):
    """Raised when the owning sporting snapshot is not completed."""


class TelemetryIdentityUnavailableError(TelemetryIngestionError):
    """Raised when an entry has no usable FastF1 driver identity."""


class TelemetryClaimOwnershipError(TelemetryIngestionError):
    """Raised when a worker no longer owns the claimed telemetry attempt."""


@dataclass(frozen=True, slots=True)
class TelemetryCommandResult:
    lap_id: int
    action: str
    status: str
    source_snapshot_completed_at: datetime


@dataclass(frozen=True, slots=True)
class ClaimedTelemetryLap:
    lap_id: int
    attempt_token: int
    claimed_at: datetime
    source_snapshot_completed_at: datetime
    request: FastF1TelemetryRequest


@dataclass(frozen=True, slots=True)
class TelemetryPersistenceSummary:
    lap_id: int
    sample_count: int
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class TelemetryFailureTransition:
    lap_id: int
    status: str
    next_retry_at: datetime | None
    error_code: str
    error_message: str


@dataclass(frozen=True, slots=True)
class ProcessedTelemetryLap:
    claim: ClaimedTelemetryLap
    status: str
    sample_count: int


class TelemetryClaimHeartbeatMonitor:
    """Refresh one telemetry claim while FastF1 performs blocking work."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        claim: ClaimedTelemetryLap,
        interval: timedelta,
    ) -> None:
        if interval <= timedelta(0):
            raise ValueError("heartbeat interval must be positive")
        self._session_factory = session_factory
        self._claim = claim
        self._seconds = interval.total_seconds()
        self._stop = ThreadEvent()
        self._lock = Lock()
        self._failure: Exception | None = None
        self._thread = Thread(
            target=self._run,
            name=f"telemetry-heartbeat-{claim.lap_id}",
            daemon=True,
        )

    @property
    def failure(self) -> Exception | None:
        with self._lock:
            return self._failure

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def raise_if_failed(self) -> None:
        failure = self.failure
        if failure is not None:
            raise failure

    def _run(self) -> None:
        while not self._stop.wait(self._seconds):
            try:
                with self._session_factory() as database:
                    heartbeat_telemetry_lap(database, claim=self._claim)
            except Exception as error:
                with self._lock:
                    self._failure = error
                return


def ensure_lap_telemetry(
    database: Session,
    *,
    session_id: int,
    session_entry_id: int,
    lap_number: int,
) -> TelemetryCommandResult:
    """Create or reuse the one persistent telemetry request for a stored lap."""

    _require_new_transaction(database)
    _positive(session_id, "session_id")
    _positive(session_entry_id, "session_entry_id")
    _positive(lap_number, "lap_number")

    with database.begin():
        row = database.execute(
            select(Lap, SessionIngestion)
            .join(SessionEntry, SessionEntry.id == Lap.session_entry_id)
            .join(RaceSession, RaceSession.id == SessionEntry.session_id)
            .outerjoin(
                SessionIngestion,
                SessionIngestion.session_id == RaceSession.id,
            )
            .where(
                RaceSession.id == session_id,
                SessionEntry.id == session_entry_id,
                Lap.lap_number == lap_number,
            )
            .with_for_update(of=Lap)
        ).one_or_none()
        if row is None:
            raise TelemetryTargetNotFoundError(
                "the requested session entry lap does not exist"
            )
        lap, sporting_ingestion = row
        snapshot = _completed_snapshot(sporting_ingestion)
        state = database.get(
            LapTelemetryIngestion,
            lap.id,
            with_for_update=True,
        )
        now = _database_now(database)
        if state is None:
            state = LapTelemetryIngestion(
                lap_id=lap.id,
                status="pending",
                source="fastf1_archive",
                record_state="finalized",
                requested_at=now,
                source_snapshot_completed_at=snapshot,
            )
            database.add(state)
            database.flush()
            action = "queued"
        elif (
            state.status == "completed"
            and state.source_snapshot_completed_at == snapshot
            and state.sample_count > 0
        ):
            action = "available"
        elif (
            state.status in {"pending", "running"}
            and state.source_snapshot_completed_at == snapshot
        ):
            action = "reused"
        else:
            state.status = "pending"
            state.attempt_count = 0
            state.sample_count = 0
            state.requested_at = now
            state.first_started_at = None
            state.last_started_at = None
            state.heartbeat_at = None
            state.next_retry_at = None
            state.completed_at = None
            state.source_snapshot_completed_at = snapshot
            state.last_error_code = None
            state.last_error_message = None
            action = "queued"

        return TelemetryCommandResult(
            lap_id=lap.id,
            action=action,
            status=state.status,
            source_snapshot_completed_at=snapshot,
        )


def claim_next_telemetry_lap(
    database: Session,
    *,
    settings: BackfillRuntimeSettings | None = None,
) -> ClaimedTelemetryLap | None:
    """Claim the oldest retry-eligible telemetry request with row fencing."""

    _require_new_transaction(database)
    runtime_settings = settings or BackfillRuntimeSettings()
    with database.begin():
        state = database.scalar(
            select(LapTelemetryIngestion)
            .join(Lap, Lap.id == LapTelemetryIngestion.lap_id)
            .join(SessionEntry, SessionEntry.id == Lap.session_entry_id)
            .join(RaceSession, RaceSession.id == SessionEntry.session_id)
            .join(
                SessionIngestion,
                SessionIngestion.session_id == RaceSession.id,
            )
            .where(
                LapTelemetryIngestion.status == "pending",
                LapTelemetryIngestion.attempt_count
                < runtime_settings.max_attempts,
                or_(
                    LapTelemetryIngestion.next_retry_at.is_(None),
                    LapTelemetryIngestion.next_retry_at <= func.now(),
                ),
                SessionIngestion.status == "completed",
                SessionIngestion.completed_at.is_not(None),
                LapTelemetryIngestion.source_snapshot_completed_at
                == SessionIngestion.completed_at,
            )
            .order_by(
                LapTelemetryIngestion.next_retry_at.asc().nulls_first(),
                LapTelemetryIngestion.requested_at,
                LapTelemetryIngestion.lap_id,
            )
            .with_for_update(skip_locked=True, of=LapTelemetryIngestion)
            .limit(1)
        )
        if state is None:
            return None

        target = database.execute(
            select(
                Lap,
                SessionEntry,
                RaceSession,
                Event,
                SessionIngestion,
            )
            .join(SessionEntry, SessionEntry.id == Lap.session_entry_id)
            .join(RaceSession, RaceSession.id == SessionEntry.session_id)
            .join(Event, Event.id == RaceSession.event_id)
            .join(
                SessionIngestion,
                SessionIngestion.session_id == RaceSession.id,
            )
            .where(Lap.id == state.lap_id)
        ).one()
        lap, entry, race_session, event, sporting_ingestion = target
        driver_identifier = entry.racing_number or entry.abbreviation
        if driver_identifier is None or not driver_identifier.strip():
            state.status = "failed"
            state.last_error_code = "telemetry_identity_unavailable"
            state.last_error_message = (
                "The stored entry has no usable archive driver identity."
            )
            state.next_retry_at = None
            return None

        claimed_at = _database_now(database)
        state.status = "running"
        state.attempt_count += 1
        state.first_started_at = state.first_started_at or claimed_at
        state.last_started_at = claimed_at
        state.heartbeat_at = claimed_at
        state.next_retry_at = None
        state.completed_at = None
        state.last_error_code = None
        state.last_error_message = None

        return ClaimedTelemetryLap(
            lap_id=lap.id,
            attempt_token=state.attempt_count,
            claimed_at=claimed_at,
            source_snapshot_completed_at=sporting_ingestion.completed_at,
            request=FastF1TelemetryRequest(
                season_year=event.season_year,
                round_number=event.round_number,
                session_identifier=race_session.session_name,
                driver_identifier=driver_identifier,
                lap_number=lap.lap_number,
            ),
        )


def heartbeat_telemetry_lap(
    database: Session,
    *,
    claim: ClaimedTelemetryLap,
) -> datetime:
    _require_new_transaction(database)
    with database.begin():
        state = _owned_state(database, claim)
        now = _database_now(database)
        state.heartbeat_at = now
        return now


def replace_lap_telemetry(
    database: Session,
    *,
    claim: ClaimedTelemetryLap,
    samples: tuple[NormalizedTelemetrySample, ...],
) -> TelemetryPersistenceSummary:
    """Atomically replace samples and complete the exact claimed snapshot."""

    _require_new_transaction(database)
    if not samples:
        raise TelemetryNormalizationError(
            "normalized telemetry must contain samples"
        )
    with database.begin():
        state = _owned_state(database, claim)
        current_snapshot = database.scalar(
            select(SessionIngestion.completed_at)
            .join(
                RaceSession,
                RaceSession.id == SessionIngestion.session_id,
            )
            .join(
                SessionEntry,
                SessionEntry.session_id == RaceSession.id,
            )
            .join(Lap, Lap.session_entry_id == SessionEntry.id)
            .where(Lap.id == claim.lap_id)
        )
        if current_snapshot != claim.source_snapshot_completed_at:
            raise TelemetryClaimOwnershipError(
                "the sporting snapshot changed before telemetry persistence"
            )

        database.execute(
            delete(LapTelemetrySample).where(
                LapTelemetrySample.lap_id == claim.lap_id
            )
        )
        database.add_all(
            [
                LapTelemetrySample(
                    lap_id=claim.lap_id,
                    sample_index=sample.sample_index,
                    lap_time_us=sample.lap_time_us,
                    session_time_us=sample.session_time_us,
                    distance_m=sample.distance_m,
                    relative_distance=sample.relative_distance,
                    speed_kph=sample.speed_kph,
                    rpm=sample.rpm,
                    gear=sample.gear,
                    throttle_percent=sample.throttle_percent,
                    brake=sample.brake,
                    drs=sample.drs,
                    x=sample.x,
                    y=sample.y,
                    z=sample.z,
                    source="fastf1_archive",
                    record_state="finalized",
                )
                for sample in samples
            ]
        )
        completed_at = _database_now(database)
        state.status = "completed"
        state.sample_count = len(samples)
        state.heartbeat_at = None
        state.next_retry_at = None
        state.completed_at = completed_at
        state.last_error_code = None
        state.last_error_message = None
        return TelemetryPersistenceSummary(
            lap_id=claim.lap_id,
            sample_count=len(samples),
            completed_at=completed_at,
        )


def transition_telemetry_failure(
    database: Session,
    *,
    claim: ClaimedTelemetryLap,
    error: Exception,
    settings: BackfillRuntimeSettings | None = None,
    jitter_fraction: float = 0.5,
) -> TelemetryFailureTransition:
    _require_new_transaction(database)
    runtime_settings = settings or BackfillRuntimeSettings()
    with database.begin():
        state = _owned_state(database, claim)
        now = _database_now(database)
        retryable = isinstance(
            error,
            (FastF1SessionLoadError, FastF1RequestBudgetExhaustedError),
        )
        status = "failed"
        next_retry_at = None
        if retryable and state.attempt_count < runtime_settings.max_attempts:
            status = "pending"
            next_retry_at = calculate_retry_schedule(
                database_now=now,
                failed_attempt=state.attempt_count,
                jitter_fraction=jitter_fraction,
                settings=runtime_settings,
            ).next_retry_at
        if isinstance(error, TelemetryNormalizationError):
            code = "telemetry_invalid_snapshot"
            message = "The upstream telemetry snapshot was invalid."
        elif retryable:
            code = "telemetry_upstream_unavailable"
            message = "The telemetry source is temporarily unavailable."
        else:
            code = "telemetry_processing_failed"
            message = "Telemetry processing failed."
        state.status = status
        state.heartbeat_at = None
        state.next_retry_at = next_retry_at
        state.last_error_code = code
        state.last_error_message = message
        return TelemetryFailureTransition(
            lap_id=claim.lap_id,
            status=status,
            next_retry_at=next_retry_at,
            error_code=code,
            error_message=message,
        )


def recover_stale_telemetry_leases(
    database: Session,
    *,
    settings: BackfillRuntimeSettings | None = None,
    batch_size: int = 10,
    jitter_fraction_factory: Callable[[], float] | None = None,
) -> int:
    _require_new_transaction(database)
    _positive(batch_size, "batch_size")
    runtime_settings = settings or BackfillRuntimeSettings()
    jitter = jitter_fraction_factory or random.random
    with database.begin():
        states = database.scalars(
            select(LapTelemetryIngestion)
            .where(
                LapTelemetryIngestion.status == "running",
                LapTelemetryIngestion.heartbeat_at.is_not(None),
                LapTelemetryIngestion.heartbeat_at
                < func.clock_timestamp() - runtime_settings.lease_timeout,
            )
            .order_by(LapTelemetryIngestion.heartbeat_at)
            .with_for_update(skip_locked=True)
            .limit(batch_size)
        ).all()
        for state in states:
            now = _database_now(database)
            status = "failed"
            next_retry_at = None
            if state.attempt_count < runtime_settings.max_attempts:
                status = "pending"
                next_retry_at = calculate_retry_schedule(
                    database_now=now,
                    failed_attempt=state.attempt_count,
                    jitter_fraction=jitter(),
                    settings=runtime_settings,
                ).next_retry_at
            state.status = status
            state.heartbeat_at = None
            state.next_retry_at = next_retry_at
            state.last_error_code = "worker_lease_expired"
            state.last_error_message = (
                "The worker lease expired before telemetry ingestion completed."
            )
        return len(states)


def process_next_telemetry_lap(
    *,
    session_factory: SessionFactory,
    loader: FastF1TelemetryLoaderProtocol,
    settings: BackfillRuntimeSettings | None = None,
    heartbeat_interval: timedelta | None = None,
) -> ProcessedTelemetryLap | None:
    runtime_settings = settings or BackfillRuntimeSettings()
    with session_factory() as database:
        claim = claim_next_telemetry_lap(database, settings=runtime_settings)
    if claim is None:
        return None
    monitor = TelemetryClaimHeartbeatMonitor(
        session_factory=session_factory,
        claim=claim,
        interval=heartbeat_interval or runtime_settings.heartbeat_interval,
    )
    monitor.start()
    processing_error: Exception | None = None
    samples: tuple[NormalizedTelemetrySample, ...] = ()
    try:
        loaded = loader.load_telemetry(claim.request)
        if loaded.request != claim.request:
            raise TelemetryNormalizationError(
                "loader returned telemetry for a different request"
            )
        samples = normalize_fastf1_telemetry(loaded.telemetry)
        monitor.raise_if_failed()
    except Exception as error:
        processing_error = error
    finally:
        monitor.stop()

    effective_error = monitor.failure or processing_error
    if effective_error is None:
        try:
            with session_factory() as database:
                summary = replace_lap_telemetry(
                    database,
                    claim=claim,
                    samples=samples,
                )
        except TelemetryClaimOwnershipError:
            return ProcessedTelemetryLap(
                claim=claim,
                status="ownership_lost",
                sample_count=0,
            )
        return ProcessedTelemetryLap(
            claim=claim,
            status="completed",
            sample_count=summary.sample_count,
        )
    if isinstance(effective_error, TelemetryClaimOwnershipError):
        return ProcessedTelemetryLap(
            claim=claim,
            status="ownership_lost",
            sample_count=0,
        )
    with session_factory() as database:
        try:
            transition = transition_telemetry_failure(
                database,
                claim=claim,
                error=effective_error,
                settings=runtime_settings,
            )
        except TelemetryClaimOwnershipError:
            return ProcessedTelemetryLap(
                claim=claim,
                status="ownership_lost",
                sample_count=0,
            )
    return ProcessedTelemetryLap(
        claim=claim,
        status=transition.status,
        sample_count=0,
    )


def _owned_state(
    database: Session,
    claim: ClaimedTelemetryLap,
) -> LapTelemetryIngestion:
    state = database.get(
        LapTelemetryIngestion,
        claim.lap_id,
        with_for_update=True,
    )
    if (
        state is None
        or state.status != "running"
        or state.attempt_count != claim.attempt_token
        or state.source_snapshot_completed_at
        != claim.source_snapshot_completed_at
    ):
        raise TelemetryClaimOwnershipError(
            "the worker no longer owns the telemetry claim"
        )
    return state


def _completed_snapshot(ingestion: SessionIngestion | None) -> datetime:
    if (
        ingestion is None
        or ingestion.status != "completed"
        or ingestion.completed_at is None
    ):
        raise TelemetryDataUnavailableError(
            "the owning historical snapshot is not completed"
        )
    return ingestion.completed_at


def _database_now(database: Session) -> datetime:
    value = database.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise TelemetryIngestionError("database did not return a timestamp")
    return value


def _require_new_transaction(database: Session) -> None:
    if database.in_transaction():
        raise TelemetryIngestionError(
            "telemetry operation must own a new database transaction"
        )


def _positive(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TelemetryIngestionError(f"{field} must be a positive integer")
