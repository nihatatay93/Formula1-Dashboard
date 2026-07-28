from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from threading import Event, Lock, Thread

from sqlalchemy import select

from app.db.models import BackfillJob
from app.ingestion.archive_ingestion import (
    ArchiveIngestionSummary,
    FastF1SessionLoaderProtocol,
    SessionFactory,
    ingest_fastf1_archive_session,
)
from app.ingestion.archive_persistence import (
    ArchivePersistenceOwnershipError,
)
from app.ingestion.backfill_orchestration import (
    ArchiveJobFailureTransition,
    BackfillClaimOwnershipError,
    BackfillJobAggregation,
    ClaimedArchiveJobSession,
    RecoveredArchiveLease,
    aggregate_backfill_job,
    claim_next_archive_job_session,
    heartbeat_archive_job_session,
    recover_stale_archive_job_sessions,
    transition_archive_job_failure,
)
from app.ingestion.runtime_policy import (
    BackfillRuntimeSettings,
    RetryDisposition,
    classify_retry,
)

logger = logging.getLogger("formula1_dashboard.worker")


class WorkerSessionOutcome(StrEnum):
    COMPLETED = "completed"
    RETRY_PENDING = "retry_pending"
    FAILED = "failed"
    OWNERSHIP_LOST = "ownership_lost"


@dataclass(frozen=True, slots=True)
class ProcessedArchiveJobSession:
    claim: ClaimedArchiveJobSession
    outcome: WorkerSessionOutcome
    ingestion: ArchiveIngestionSummary | None
    failure_transition: ArchiveJobFailureTransition | None
    aggregation: BackfillJobAggregation


@dataclass(frozen=True, slots=True)
class WorkerMaintenanceSummary:
    recovered: tuple[RecoveredArchiveLease, ...]
    aggregations: tuple[BackfillJobAggregation, ...]


class ClaimHeartbeatMonitor:
    """Refresh one claim in a separate thread during blocking FastF1 work."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        claim: ClaimedArchiveJobSession,
        interval: timedelta,
    ) -> None:
        if interval <= timedelta(0):
            raise ValueError("heartbeat interval must be positive")
        self._session_factory = session_factory
        self._claim = claim
        self._interval_seconds = interval.total_seconds()
        self._stop = Event()
        self._failure_ready = Event()
        self._failure_lock = Lock()
        self._failure: Exception | None = None
        self._thread = Thread(
            target=self._run,
            name=f"backfill-heartbeat-{claim.session_id}",
            daemon=True,
        )

    @property
    def failure(self) -> Exception | None:
        with self._failure_lock:
            return self._failure

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def raise_if_failed(self) -> None:
        if not self._failure_ready.is_set():
            return
        failure = self.failure
        if failure is not None:
            raise failure

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                with self._session_factory() as database:
                    heartbeat_archive_job_session(
                        database,
                        claim=self._claim,
                    )
            except Exception as error:
                with self._failure_lock:
                    self._failure = error
                self._failure_ready.set()
                return


def process_next_archive_job_session(
    *,
    session_factory: SessionFactory,
    loader: FastF1SessionLoaderProtocol,
    settings: BackfillRuntimeSettings | None = None,
    heartbeat_interval: timedelta | None = None,
    jitter_fraction_factory: Callable[[], float] | None = None,
) -> ProcessedArchiveJobSession | None:
    """Claim and fully process at most one archive job-session."""

    runtime_settings = settings or BackfillRuntimeSettings()
    with session_factory() as database:
        claim = claim_next_archive_job_session(
            database,
            settings=runtime_settings,
        )
    if claim is None:
        return None

    monitor = ClaimHeartbeatMonitor(
        session_factory=session_factory,
        claim=claim,
        interval=heartbeat_interval or runtime_settings.heartbeat_interval,
    )
    monitor.start()
    ingestion: ArchiveIngestionSummary | None = None
    ingestion_error: Exception | None = None
    try:
        ingestion = ingest_fastf1_archive_session(
            session_id=claim.session_id,
            session_factory=session_factory,
            loader=loader,
            claim=claim,
            before_persist=monitor.raise_if_failed,
        )
    except Exception as error:
        ingestion_error = error
    finally:
        monitor.stop()

    failure_transition: ArchiveJobFailureTransition | None = None
    if ingestion_error is None:
        outcome = WorkerSessionOutcome.COMPLETED
    else:
        effective_error = monitor.failure or ingestion_error
        if isinstance(
            effective_error,
            (
                BackfillClaimOwnershipError,
                ArchivePersistenceOwnershipError,
            ),
        ):
            outcome = WorkerSessionOutcome.OWNERSHIP_LOST
        else:
            jitter_fraction = (
                (jitter_fraction_factory or random.random)()
                if classify_retry(effective_error)
                is RetryDisposition.RETRYABLE
                else 0.0
            )
            with session_factory() as database:
                try:
                    failure_transition = transition_archive_job_failure(
                        database,
                        claim=claim,
                        error=effective_error,
                        jitter_fraction=jitter_fraction,
                        settings=runtime_settings,
                    )
                except BackfillClaimOwnershipError:
                    outcome = WorkerSessionOutcome.OWNERSHIP_LOST
                else:
                    outcome = (
                        WorkerSessionOutcome.RETRY_PENDING
                        if failure_transition.status == "pending"
                        else WorkerSessionOutcome.FAILED
                    )

    with session_factory() as database:
        aggregation = aggregate_backfill_job(
            database,
            job_id=claim.job_id,
        )

    return ProcessedArchiveJobSession(
        claim=claim,
        outcome=outcome,
        ingestion=ingestion,
        failure_transition=failure_transition,
        aggregation=aggregation,
    )


def perform_worker_maintenance(
    *,
    session_factory: SessionFactory,
    settings: BackfillRuntimeSettings | None = None,
    recovery_batch_size: int = 10,
    jitter_fraction_factory: Callable[[], float] | None = None,
) -> WorkerMaintenanceSummary:
    """Recover stale leases and reconcile every active parent job."""

    runtime_settings = settings or BackfillRuntimeSettings()
    with session_factory() as database:
        recovered = recover_stale_archive_job_sessions(
            database,
            settings=runtime_settings,
            batch_size=recovery_batch_size,
            jitter_fraction_factory=jitter_fraction_factory,
        )

    with session_factory() as database:
        active_job_ids = tuple(
            database.scalars(
                select(BackfillJob.id)
                .where(BackfillJob.status.in_(("pending", "running")))
                .order_by(BackfillJob.requested_at, BackfillJob.id)
            ).all()
        )

    aggregations = []
    for job_id in active_job_ids:
        with session_factory() as database:
            aggregations.append(
                aggregate_backfill_job(database, job_id=job_id)
            )

    return WorkerMaintenanceSummary(
        recovered=recovered,
        aggregations=tuple(aggregations),
    )


class ArchiveBackfillWorker:
    """Single-concurrency worker loop with periodic lease maintenance."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        loader: FastF1SessionLoaderProtocol,
        settings: BackfillRuntimeSettings | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        process_next: Callable[[], ProcessedArchiveJobSession | None] | None = None,
        maintenance: Callable[[], WorkerMaintenanceSummary] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.loader = loader
        self.settings = settings or BackfillRuntimeSettings()
        self._monotonic = monotonic
        self._process_next = process_next or self._process_next_default
        self._maintenance = maintenance or self._maintenance_default

    def run(self, stop_event: Event) -> None:
        next_maintenance_at = 0.0
        while not stop_event.is_set():
            now = self._monotonic()
            if now >= next_maintenance_at:
                try:
                    summary = self._maintenance()
                except Exception as error:
                    _log_operation_error("maintenance", error)
                    next_maintenance_at = (
                        self._monotonic()
                        + self.settings.worker_poll_interval_seconds
                    )
                else:
                    if summary.recovered:
                        logger.info(
                            "Recovered %s stale archive lease(s).",
                            len(summary.recovered),
                        )
                    next_maintenance_at = (
                        self._monotonic()
                        + self.settings.recovery_scan_interval_seconds
                    )

            if stop_event.is_set():
                break

            try:
                processed = self._process_next()
            except Exception as error:
                _log_operation_error("session processing", error)
                processed = None

            if processed is not None:
                logger.info(
                    "Processed job %s session %s with outcome %s.",
                    processed.claim.job_id,
                    processed.claim.session_id,
                    processed.outcome,
                )
                continue

            until_maintenance = max(
                0.0,
                next_maintenance_at - self._monotonic(),
            )
            wait_seconds = min(
                float(self.settings.worker_poll_interval_seconds),
                until_maintenance,
            )
            stop_event.wait(wait_seconds)

    def _process_next_default(self) -> ProcessedArchiveJobSession | None:
        return process_next_archive_job_session(
            session_factory=self.session_factory,
            loader=self.loader,
            settings=self.settings,
        )

    def _maintenance_default(self) -> WorkerMaintenanceSummary:
        return perform_worker_maintenance(
            session_factory=self.session_factory,
            settings=self.settings,
        )


def _log_operation_error(operation: str, error: Exception) -> None:
    logger.error(
        "Backfill worker %s failed with %s; details were not logged.",
        operation,
        type(error).__name__,
    )
