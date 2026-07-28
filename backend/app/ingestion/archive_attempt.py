from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import RaceSession, SessionIngestion
from app.ingestion.archive_ingestion import (
    ArchiveIngestionSummary,
    ArchiveSessionIdentityError,
    FastF1SessionLoaderProtocol,
    ingest_fastf1_archive_session,
)
from app.ingestion.archive_persistence import (
    ArchivePersistenceError,
    ArchivePersistenceTargetChangedError,
    ArchiveSessionNotFoundError,
    ArchiveSourceConflictError,
)
from app.ingestion.fastf1_loader import (
    FastF1LoaderConfigurationError,
    FastF1SessionLoadError,
)
from app.ingestion.fastf1_normalization import (
    ARCHIVE_SOURCE,
    FINALIZED_STATE,
    FastF1NormalizationError,
)

SessionFactory = Callable[[], Session]


class ArchiveIngestionStateError(RuntimeError):
    """Base error for invalid archive ingestion-state transitions."""


class ArchiveIngestionAlreadyRunningError(ArchiveIngestionStateError):
    """Raised when another attempt already owns the session."""


class ArchiveIngestionStateConflictError(ArchiveIngestionStateError):
    """Raised when an attempt no longer owns the state it is updating."""


@dataclass(frozen=True, slots=True)
class SanitizedArchiveFailure:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ArchiveIngestionState:
    session_id: int
    status: str
    attempt_count: int
    first_started_at: datetime | None
    last_started_at: datetime | None
    completed_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None


@dataclass(frozen=True, slots=True)
class ArchiveIngestionAttemptSummary:
    attempt_count: int
    ingestion: ArchiveIngestionSummary


def mark_archive_ingestion_pending(
    *,
    session_id: int,
    session_factory: SessionFactory,
) -> ArchiveIngestionState:
    """Create or reset archive ingestion state without starting an attempt."""

    with session_factory() as database, database.begin():
        _lock_target_session(database, session_id)
        ingestion = _get_ingestion_for_update(database, session_id)
        if ingestion is None:
            ingestion = SessionIngestion(
                session_id=session_id,
                status="pending",
                source=ARCHIVE_SOURCE,
                record_state=FINALIZED_STATE,
                attempt_count=0,
            )
            database.add(ingestion)
        else:
            _ensure_archive_state(ingestion)
            if ingestion.status == "running":
                raise ArchiveIngestionAlreadyRunningError(
                    f"archive ingestion for session {session_id} is already running"
                )
            ingestion.status = "pending"
            ingestion.record_state = FINALIZED_STATE
            ingestion.heartbeat_at = None
            ingestion.next_retry_at = None
        database.flush()
        return _state_snapshot(ingestion)


def run_fastf1_archive_ingestion_attempt(
    *,
    session_id: int,
    session_factory: SessionFactory,
    loader: FastF1SessionLoaderProtocol,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    source_updated_at: datetime | None = None,
) -> ArchiveIngestionAttemptSummary:
    """Run one observable archive attempt and persist a sanitized failure."""

    attempt_started_at = started_at or datetime.now(UTC)
    _validate_timestamp(attempt_started_at, "started_at")
    attempt_count = _mark_running(
        session_id=session_id,
        session_factory=session_factory,
        started_at=attempt_started_at,
    )

    try:
        ingestion = ingest_fastf1_archive_session(
            session_id=session_id,
            session_factory=session_factory,
            loader=loader,
            completed_at=completed_at,
            source_updated_at=source_updated_at,
        )
    except Exception as error:
        failure = sanitize_archive_failure(error)
        try:
            _mark_failed(
                session_id=session_id,
                session_factory=session_factory,
                attempt_count=attempt_count,
                failure=failure,
            )
        except Exception:
            error.add_note(
                "The archive ingestion failure could not be recorded."
            )
        raise

    return ArchiveIngestionAttemptSummary(
        attempt_count=attempt_count,
        ingestion=ingestion,
    )


def sanitize_archive_failure(error: Exception) -> SanitizedArchiveFailure:
    """Map an exception to fixed, secret-free persisted diagnostics."""

    mappings: tuple[
        tuple[type[Exception], SanitizedArchiveFailure],
        ...,
    ] = (
        (
            FastF1LoaderConfigurationError,
            SanitizedArchiveFailure(
                "fastf1_configuration_failed",
                "FastF1 loader configuration failed.",
            ),
        ),
        (
            FastF1SessionLoadError,
            SanitizedArchiveFailure(
                "fastf1_load_failed",
                "FastF1 session loading failed.",
            ),
        ),
        (
            FastF1NormalizationError,
            SanitizedArchiveFailure(
                "fastf1_normalization_failed",
                "FastF1 session normalization failed.",
            ),
        ),
        (
            ArchiveSessionIdentityError,
            SanitizedArchiveFailure(
                "archive_identity_mismatch",
                "Loaded archive identity did not match the database session.",
            ),
        ),
        (
            ArchivePersistenceTargetChangedError,
            SanitizedArchiveFailure(
                "archive_target_changed",
                "Archive target identity changed before persistence.",
            ),
        ),
        (
            ArchiveSourceConflictError,
            SanitizedArchiveFailure(
                "archive_source_conflict",
                "Archive replacement conflicted with another data source.",
            ),
        ),
        (
            ArchiveSessionNotFoundError,
            SanitizedArchiveFailure(
                "archive_target_missing",
                "The archive target session no longer exists.",
            ),
        ),
        (
            ArchivePersistenceError,
            SanitizedArchiveFailure(
                "archive_persistence_failed",
                "Archive snapshot persistence failed.",
            ),
        ),
        (
            SQLAlchemyError,
            SanitizedArchiveFailure(
                "database_operation_failed",
                "A database operation failed during archive ingestion.",
            ),
        ),
    )

    for error_type, failure in mappings:
        if isinstance(error, error_type):
            return failure

    return SanitizedArchiveFailure(
        "archive_ingestion_failed",
        "Archive session ingestion failed.",
    )


def _mark_running(
    *,
    session_id: int,
    session_factory: SessionFactory,
    started_at: datetime,
) -> int:
    with session_factory() as database, database.begin():
        _lock_target_session(database, session_id)
        ingestion = _get_ingestion_for_update(database, session_id)
        if ingestion is None:
            ingestion = SessionIngestion(
                session_id=session_id,
                status="running",
                source=ARCHIVE_SOURCE,
                record_state=FINALIZED_STATE,
                attempt_count=1,
                first_started_at=started_at,
                last_started_at=started_at,
            )
            database.add(ingestion)
        else:
            _ensure_archive_state(ingestion)
            if ingestion.status == "running":
                raise ArchiveIngestionAlreadyRunningError(
                    f"archive ingestion for session {session_id} is already running"
                )
            ingestion.status = "running"
            ingestion.record_state = FINALIZED_STATE
            ingestion.attempt_count += 1
            ingestion.first_started_at = (
                ingestion.first_started_at or started_at
            )
            ingestion.last_started_at = started_at
            ingestion.heartbeat_at = None
            ingestion.next_retry_at = None
        database.flush()
        return ingestion.attempt_count


def _mark_failed(
    *,
    session_id: int,
    session_factory: SessionFactory,
    attempt_count: int,
    failure: SanitizedArchiveFailure,
) -> None:
    with session_factory() as database, database.begin():
        _lock_target_session(database, session_id)
        ingestion = _get_ingestion_for_update(database, session_id)
        if (
            ingestion is None
            or ingestion.source != ARCHIVE_SOURCE
            or ingestion.status != "running"
            or ingestion.attempt_count != attempt_count
        ):
            raise ArchiveIngestionStateConflictError(
                f"archive attempt {attempt_count} no longer owns session {session_id}"
            )
        ingestion.status = "failed"
        ingestion.record_state = FINALIZED_STATE
        ingestion.heartbeat_at = None
        ingestion.next_retry_at = None
        ingestion.last_error_code = failure.code
        ingestion.last_error_message = failure.message


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


def _ensure_archive_state(ingestion: SessionIngestion) -> None:
    if ingestion.source != ARCHIVE_SOURCE:
        raise ArchiveSourceConflictError(
            f"session {ingestion.session_id} ingestion belongs to another source"
        )


def _state_snapshot(ingestion: SessionIngestion) -> ArchiveIngestionState:
    return ArchiveIngestionState(
        session_id=ingestion.session_id,
        status=ingestion.status,
        attempt_count=ingestion.attempt_count,
        first_started_at=ingestion.first_started_at,
        last_started_at=ingestion.last_started_at,
        completed_at=ingestion.completed_at,
        last_error_code=ingestion.last_error_code,
        last_error_message=ingestion.last_error_message,
    )


def _validate_timestamp(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ArchiveIngestionStateError(f"{field} must include a timezone")
