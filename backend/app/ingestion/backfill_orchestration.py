from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    BackfillJob,
    BackfillJobSession,
    RaceSession,
    SessionIngestion,
)
from app.ingestion.archive_attempt import (
    SanitizedArchiveFailure,
    sanitize_archive_failure,
)
from app.ingestion.archive_persistence import (
    ArchiveSessionNotFoundError,
    ArchiveSourceConflictError,
)
from app.ingestion.fastf1_normalization import ARCHIVE_SOURCE, FINALIZED_STATE
from app.ingestion.runtime_policy import (
    BackfillRuntimeSettings,
    RetryDisposition,
    calculate_retry_schedule,
    classify_retry,
)


class BackfillOrchestrationError(RuntimeError):
    """Base error for invalid backfill orchestration operations."""


class BackfillOrchestrationTransactionError(BackfillOrchestrationError):
    """Raised when an orchestration operation cannot own its transaction."""


class BackfillPersistentStateConflictError(BackfillOrchestrationError):
    """Raised when persistent session state cannot be claimed safely."""


class BackfillClaimOwnershipError(BackfillOrchestrationError):
    """Raised when a failure transition no longer owns its claimed rows."""


@dataclass(frozen=True, slots=True)
class ClaimedArchiveJobSession:
    job_id: uuid.UUID
    session_id: int
    job_attempt_count: int
    session_attempt_token: int
    claimed_at: datetime


@dataclass(frozen=True, slots=True)
class ArchiveJobFailureTransition:
    claim: ClaimedArchiveJobSession
    disposition: RetryDisposition
    status: str
    failed_at: datetime
    next_retry_at: datetime | None
    failure: SanitizedArchiveFailure


@dataclass(frozen=True, slots=True)
class ArchiveJobHeartbeat:
    claim: ClaimedArchiveJobSession
    heartbeat_at: datetime


def claim_next_archive_job_session(
    database: Session,
    *,
    settings: BackfillRuntimeSettings | None = None,
) -> ClaimedArchiveJobSession | None:
    """Claim one eligible archive job-session and synchronize persistent state."""

    _require_new_transaction(database)
    runtime_settings = settings or BackfillRuntimeSettings()

    with database.begin():
        job_session = database.scalar(
            select(BackfillJobSession)
            .join(
                BackfillJob,
                BackfillJob.id == BackfillJobSession.job_id,
            )
            .where(
                BackfillJob.status.in_(("pending", "running")),
                BackfillJobSession.status == "pending",
                BackfillJobSession.attempt_count
                < runtime_settings.max_attempts,
                or_(
                    BackfillJobSession.next_retry_at.is_(None),
                    BackfillJobSession.next_retry_at <= func.now(),
                ),
            )
            .order_by(
                BackfillJobSession.next_retry_at.asc().nulls_first(),
                BackfillJobSession.queued_at,
                BackfillJobSession.job_id,
                BackfillJobSession.session_id,
            )
            .with_for_update(
                skip_locked=True,
                of=BackfillJobSession,
            )
            .limit(1)
        )
        if job_session is None:
            return None

        job = _get_job_for_update(database, job_session.job_id)
        if job is None or job.status not in {"pending", "running"}:
            raise BackfillClaimOwnershipError(
                f"backfill job {job_session.job_id} is no longer active"
            )

        _lock_target_session(database, job_session.session_id)
        ingestion = _get_ingestion_for_update(
            database,
            job_session.session_id,
        )
        if ingestion is not None:
            if ingestion.source != ARCHIVE_SOURCE:
                raise ArchiveSourceConflictError(
                    f"session {job_session.session_id} ingestion belongs "
                    "to another source"
                )
            if ingestion.status == "running":
                raise BackfillPersistentStateConflictError(
                    f"session {job_session.session_id} is already running"
                )

        claimed_at = _database_now(database)
        job_attempt_count = job_session.attempt_count + 1
        session_attempt_token = (
            ingestion.attempt_count + 1
            if ingestion is not None
            else 1
        )

        job.status = "running"
        job.started_at = job.started_at or claimed_at
        job.heartbeat_at = claimed_at

        job_session.status = "running"
        job_session.attempt_count = job_attempt_count
        job_session.started_at = claimed_at
        job_session.heartbeat_at = claimed_at
        job_session.next_retry_at = None
        job_session.completed_at = None

        if ingestion is None:
            ingestion = SessionIngestion(
                session_id=job_session.session_id,
                status="running",
                source=ARCHIVE_SOURCE,
                record_state=FINALIZED_STATE,
                attempt_count=session_attempt_token,
                first_started_at=claimed_at,
                last_started_at=claimed_at,
                heartbeat_at=claimed_at,
            )
            database.add(ingestion)
        else:
            ingestion.status = "running"
            ingestion.record_state = FINALIZED_STATE
            ingestion.attempt_count = session_attempt_token
            ingestion.first_started_at = (
                ingestion.first_started_at or claimed_at
            )
            ingestion.last_started_at = claimed_at
            ingestion.heartbeat_at = claimed_at
            ingestion.next_retry_at = None

        database.flush()
        return ClaimedArchiveJobSession(
            job_id=job_session.job_id,
            session_id=job_session.session_id,
            job_attempt_count=job_attempt_count,
            session_attempt_token=session_attempt_token,
            claimed_at=claimed_at,
        )


def heartbeat_archive_job_session(
    database: Session,
    *,
    claim: ClaimedArchiveJobSession,
) -> ArchiveJobHeartbeat:
    """Refresh all heartbeat fields while the claim still owns both states."""

    _require_new_transaction(database)

    with database.begin():
        job_session = _get_job_session_for_update(database, claim)
        job = _get_job_for_update(database, claim.job_id)
        if job is None or job.status != "running":
            raise BackfillClaimOwnershipError(
                f"backfill job {claim.job_id} no longer owns the claim"
            )

        ingestion = _get_ingestion_for_update(database, claim.session_id)
        if (
            ingestion is None
            or ingestion.source != ARCHIVE_SOURCE
            or ingestion.status != "running"
            or ingestion.attempt_count != claim.session_attempt_token
        ):
            raise BackfillClaimOwnershipError(
                f"session attempt {claim.session_attempt_token} no longer "
                f"owns session {claim.session_id}"
            )

        heartbeat_at = _database_now(database)
        job_session.heartbeat_at = heartbeat_at
        job.heartbeat_at = heartbeat_at
        ingestion.heartbeat_at = heartbeat_at

        return ArchiveJobHeartbeat(
            claim=claim,
            heartbeat_at=heartbeat_at,
        )


def transition_archive_job_failure(
    database: Session,
    *,
    claim: ClaimedArchiveJobSession,
    error: Exception,
    jitter_fraction: float,
    settings: BackfillRuntimeSettings | None = None,
) -> ArchiveJobFailureTransition:
    """Record one claimed failure and synchronize retry or terminal state."""

    _require_new_transaction(database)
    runtime_settings = settings or BackfillRuntimeSettings()
    disposition = classify_retry(error)
    failure = sanitize_archive_failure(error)

    with database.begin():
        job_session = _get_job_session_for_update(database, claim)
        job = _get_job_for_update(database, claim.job_id)
        if job is None or job.status != "running":
            raise BackfillClaimOwnershipError(
                f"backfill job {claim.job_id} no longer owns the claim"
            )

        _lock_target_session(database, claim.session_id)
        ingestion = _get_ingestion_for_update(database, claim.session_id)
        if (
            ingestion is None
            or ingestion.source != ARCHIVE_SOURCE
            or ingestion.status != "running"
            or ingestion.attempt_count != claim.session_attempt_token
        ):
            raise BackfillClaimOwnershipError(
                f"session attempt {claim.session_attempt_token} no longer "
                f"owns session {claim.session_id}"
            )

        failed_at = _database_now(database)
        next_retry_at: datetime | None = None
        status = "failed"
        if (
            disposition is RetryDisposition.RETRYABLE
            and claim.job_attempt_count < runtime_settings.max_attempts
        ):
            schedule = calculate_retry_schedule(
                database_now=failed_at,
                failed_attempt=claim.job_attempt_count,
                jitter_fraction=jitter_fraction,
                settings=runtime_settings,
            )
            status = "pending"
            next_retry_at = schedule.next_retry_at

        job_session.status = status
        job_session.heartbeat_at = None
        job_session.next_retry_at = next_retry_at
        job_session.last_error_code = failure.code
        job_session.last_error_message = failure.message

        ingestion.status = status
        ingestion.record_state = FINALIZED_STATE
        ingestion.heartbeat_at = None
        ingestion.next_retry_at = next_retry_at
        ingestion.last_error_code = failure.code
        ingestion.last_error_message = failure.message

        return ArchiveJobFailureTransition(
            claim=claim,
            disposition=disposition,
            status=status,
            failed_at=failed_at,
            next_retry_at=next_retry_at,
            failure=failure,
        )


def _require_new_transaction(database: Session) -> None:
    if database.in_transaction():
        raise BackfillOrchestrationTransactionError(
            "backfill orchestration operations must own a new transaction"
        )


def _get_job_session_for_update(
    database: Session,
    claim: ClaimedArchiveJobSession,
) -> BackfillJobSession:
    job_session = database.scalar(
        select(BackfillJobSession)
        .where(
            BackfillJobSession.job_id == claim.job_id,
            BackfillJobSession.session_id == claim.session_id,
        )
        .with_for_update()
    )
    if (
        job_session is None
        or job_session.status != "running"
        or job_session.attempt_count != claim.job_attempt_count
    ):
        raise BackfillClaimOwnershipError(
            f"job attempt {claim.job_attempt_count} no longer owns "
            f"session {claim.session_id}"
        )
    return job_session


def _get_job_for_update(
    database: Session,
    job_id: uuid.UUID,
) -> BackfillJob | None:
    return database.scalar(
        select(BackfillJob)
        .where(BackfillJob.id == job_id)
        .with_for_update()
    )


def _lock_target_session(database: Session, session_id: int) -> None:
    target_id = database.scalar(
        select(RaceSession.id)
        .where(RaceSession.id == session_id)
        .with_for_update()
    )
    if target_id is None:
        raise ArchiveSessionNotFoundError(
            f"database session {session_id} does not exist"
        )


def _get_ingestion_for_update(
    database: Session,
    session_id: int,
) -> SessionIngestion | None:
    return database.scalar(
        select(SessionIngestion)
        .where(SessionIngestion.session_id == session_id)
        .with_for_update()
    )


def _database_now(database: Session) -> datetime:
    value = database.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise BackfillOrchestrationError(
            "PostgreSQL did not return a timestamp"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise BackfillOrchestrationError(
            "PostgreSQL returned a timestamp without a timezone"
        )
    return value.astimezone(UTC)
