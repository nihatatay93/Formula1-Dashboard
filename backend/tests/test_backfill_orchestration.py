import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import sqlalchemy_database_url
from app.db.models import BackfillJob, BackfillJobSession, SessionIngestion
from app.ingestion.archive_persistence import ArchiveSourceConflictError
from app.ingestion.backfill_orchestration import (
    BackfillClaimOwnershipError,
    BackfillOrchestrationTransactionError,
    BackfillPersistentStateConflictError,
    claim_next_archive_job_session,
    transition_archive_job_failure,
)
from app.ingestion.fastf1_loader import FastF1SessionLoadError
from app.ingestion.fastf1_normalization import FastF1NormalizationError
from app.ingestion.runtime_policy import BackfillRuntimeSettings, RetryDisposition

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="TEST_DATABASE_URL is required for backfill orchestration tests",
)


@dataclass(frozen=True, slots=True)
class OrchestrationTarget:
    engine: Engine
    session_factory: sessionmaker[Session]
    season_year: int
    event_id: int
    session_ids: tuple[int, ...]
    job_id: uuid.UUID


@pytest.fixture
def orchestration_target() -> Iterator[OrchestrationTarget]:
    assert TEST_DATABASE_URL is not None
    engine = create_engine(sqlalchemy_database_url(TEST_DATABASE_URL))
    token = uuid.uuid4().hex
    job_id = uuid.uuid4()

    with engine.begin() as connection:
        season_year = connection.scalar(
            text(
                """
                SELECT candidate
                FROM generate_series(30000, 31999) AS candidate
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM seasons
                    WHERE year = candidate
                )
                ORDER BY candidate
                LIMIT 1
                """
            )
        )
        assert season_year is not None
        connection.execute(
            text("INSERT INTO seasons (year) VALUES (:year)"),
            {"year": season_year},
        )
        event_id = connection.scalar(
            text(
                """
                INSERT INTO events (
                    season_year,
                    round_number,
                    event_name,
                    source
                )
                VALUES (
                    :season_year,
                    1,
                    :event_name,
                    'fastf1_archive'
                )
                RETURNING id
                """
            ),
            {
                "season_year": season_year,
                "event_name": f"Orchestration Test {token}",
            },
        )
        assert event_id is not None

        session_ids = tuple(
            connection.scalar(
                text(
                    """
                    INSERT INTO sessions (
                        event_id,
                        session_key,
                        session_name,
                        source
                    )
                    VALUES (
                        :event_id,
                        :session_key,
                        :session_name,
                        'fastf1_archive'
                    )
                    RETURNING id
                    """
                ),
                {
                    "event_id": event_id,
                    "session_key": f"test_{index}",
                    "session_name": f"Test Session {index}",
                },
            )
            for index in range(3)
        )
        assert all(session_id is not None for session_id in session_ids)

        connection.execute(
            text(
                """
                INSERT INTO backfill_jobs (
                    id,
                    season_year,
                    status,
                    request_reason
                )
                VALUES (
                    :job_id,
                    :season_year,
                    'pending',
                    'missing'
                )
                """
            ),
            {
                "job_id": job_id,
                "season_year": season_year,
            },
        )

    target = OrchestrationTarget(
        engine=engine,
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
        season_year=season_year,
        event_id=event_id,
        session_ids=session_ids,
        job_id=job_id,
    )
    try:
        yield target
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM backfill_jobs
                    WHERE id = :job_id
                    """
                ),
                {"job_id": job_id},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM session_ingestions
                    WHERE session_id = ANY(:session_ids)
                    """
                ),
                {"session_ids": list(session_ids)},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM sessions
                    WHERE id = ANY(:session_ids)
                    """
                ),
                {"session_ids": list(session_ids)},
            )
            connection.execute(
                text("DELETE FROM events WHERE id = :event_id"),
                {"event_id": event_id},
            )
            connection.execute(
                text("DELETE FROM seasons WHERE year = :season_year"),
                {"season_year": season_year},
            )
        engine.dispose()


def queue_session(
    target: OrchestrationTarget,
    session_id: int,
    *,
    attempt_count: int = 0,
    next_retry_at: datetime | None = None,
) -> None:
    with target.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO backfill_job_sessions (
                    job_id,
                    session_id,
                    attempt_count,
                    next_retry_at
                )
                VALUES (
                    :job_id,
                    :session_id,
                    :attempt_count,
                    :next_retry_at
                )
                """
            ),
            {
                "job_id": target.job_id,
                "session_id": session_id,
                "attempt_count": attempt_count,
                "next_retry_at": next_retry_at,
            },
        )


def test_claim_synchronizes_job_and_persistent_session_state(
    orchestration_target: OrchestrationTarget,
) -> None:
    session_id = orchestration_target.session_ids[0]
    queue_session(orchestration_target, session_id)

    with orchestration_target.session_factory() as database:
        claim = claim_next_archive_job_session(database)

    assert claim is not None
    assert claim.job_id == orchestration_target.job_id
    assert claim.session_id == session_id
    assert claim.job_attempt_count == 1
    assert claim.session_attempt_token == 1
    assert claim.claimed_at.tzinfo is not None

    with orchestration_target.session_factory() as database:
        job = database.get(BackfillJob, orchestration_target.job_id)
        job_session = database.get(
            BackfillJobSession,
            (orchestration_target.job_id, session_id),
        )
        ingestion = database.get(SessionIngestion, session_id)

        assert job is not None
        assert job.status == "running"
        assert job.started_at == claim.claimed_at
        assert job.heartbeat_at == claim.claimed_at

        assert job_session is not None
        assert job_session.status == "running"
        assert job_session.attempt_count == 1
        assert job_session.started_at == claim.claimed_at
        assert job_session.heartbeat_at == claim.claimed_at
        assert job_session.next_retry_at is None

        assert ingestion is not None
        assert ingestion.status == "running"
        assert ingestion.source == "fastf1_archive"
        assert ingestion.record_state == "finalized"
        assert ingestion.attempt_count == 1
        assert ingestion.first_started_at == claim.claimed_at
        assert ingestion.last_started_at == claim.claimed_at
        assert ingestion.heartbeat_at == claim.claimed_at
        assert ingestion.next_retry_at is None


def test_retryable_failure_synchronizes_pending_state_and_preserves_snapshot(
    orchestration_target: OrchestrationTarget,
) -> None:
    session_id = orchestration_target.session_ids[0]
    queue_session(orchestration_target, session_id)
    first_started_at = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    completed_at = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)
    source_updated_at = datetime(2026, 7, 20, 13, 30, tzinfo=UTC)

    with orchestration_target.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO session_ingestions (
                    session_id,
                    status,
                    source,
                    record_state,
                    attempt_count,
                    first_started_at,
                    last_started_at,
                    completed_at,
                    source_updated_at,
                    last_error_code,
                    last_error_message
                )
                VALUES (
                    :session_id,
                    'completed',
                    'fastf1_archive',
                    'finalized',
                    7,
                    :first_started_at,
                    :first_started_at,
                    :completed_at,
                    :source_updated_at,
                    'previous_failure',
                    'Previous fixed failure.'
                )
                """
            ),
            {
                "session_id": session_id,
                "first_started_at": first_started_at,
                "completed_at": completed_at,
                "source_updated_at": source_updated_at,
            },
        )

    with orchestration_target.session_factory() as database:
        claim = claim_next_archive_job_session(database)
    assert claim is not None
    assert claim.session_attempt_token == 8

    with orchestration_target.session_factory() as database:
        transition = transition_archive_job_failure(
            database,
            claim=claim,
            error=FastF1SessionLoadError("SECRET-UPSTREAM-DETAIL"),
            jitter_fraction=0.5,
        )

    assert transition.disposition is RetryDisposition.RETRYABLE
    assert transition.status == "pending"
    assert transition.next_retry_at == transition.failed_at + timedelta(
        seconds=45
    )
    assert transition.failure.code == "fastf1_load_failed"
    assert transition.failure.message == "FastF1 session loading failed."

    with orchestration_target.session_factory() as database:
        job_session = database.get(
            BackfillJobSession,
            (orchestration_target.job_id, session_id),
        )
        ingestion = database.get(SessionIngestion, session_id)

        assert job_session is not None
        assert job_session.status == "pending"
        assert job_session.attempt_count == 1
        assert job_session.heartbeat_at is None
        assert job_session.next_retry_at == transition.next_retry_at
        assert job_session.last_error_code == "fastf1_load_failed"
        assert job_session.last_error_message == (
            "FastF1 session loading failed."
        )

        assert ingestion is not None
        assert ingestion.status == "pending"
        assert ingestion.attempt_count == 8
        assert ingestion.first_started_at == first_started_at
        assert ingestion.completed_at == completed_at
        assert ingestion.source_updated_at == source_updated_at
        assert ingestion.heartbeat_at is None
        assert ingestion.next_retry_at == transition.next_retry_at
        assert ingestion.last_error_code == "fastf1_load_failed"
        assert "SECRET-UPSTREAM-DETAIL" not in (
            ingestion.last_error_message or ""
        )


def test_terminal_failure_synchronizes_failed_state(
    orchestration_target: OrchestrationTarget,
) -> None:
    session_id = orchestration_target.session_ids[0]
    queue_session(orchestration_target, session_id)

    with orchestration_target.session_factory() as database:
        claim = claim_next_archive_job_session(database)
    assert claim is not None

    with orchestration_target.session_factory() as database:
        transition = transition_archive_job_failure(
            database,
            claim=claim,
            error=FastF1NormalizationError("controlled"),
            jitter_fraction=0.5,
        )

    assert transition.disposition is RetryDisposition.TERMINAL
    assert transition.status == "failed"
    assert transition.next_retry_at is None

    with orchestration_target.session_factory() as database:
        job_session = database.get(
            BackfillJobSession,
            (orchestration_target.job_id, session_id),
        )
        ingestion = database.get(SessionIngestion, session_id)

        assert job_session is not None
        assert job_session.status == "failed"
        assert job_session.next_retry_at is None
        assert job_session.last_error_code == "fastf1_normalization_failed"

        assert ingestion is not None
        assert ingestion.status == "failed"
        assert ingestion.next_retry_at is None
        assert ingestion.last_error_code == "fastf1_normalization_failed"


def test_fourth_retryable_failure_exhausts_the_job_budget(
    orchestration_target: OrchestrationTarget,
) -> None:
    session_id = orchestration_target.session_ids[0]
    queue_session(
        orchestration_target,
        session_id,
        attempt_count=3,
    )
    with orchestration_target.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO session_ingestions (
                    session_id,
                    status,
                    source,
                    record_state,
                    attempt_count
                )
                VALUES (
                    :session_id,
                    'pending',
                    'fastf1_archive',
                    'finalized',
                    11
                )
                """
            ),
            {"session_id": session_id},
        )

    with orchestration_target.session_factory() as database:
        claim = claim_next_archive_job_session(database)
    assert claim is not None
    assert claim.job_attempt_count == 4
    assert claim.session_attempt_token == 12

    with orchestration_target.session_factory() as database:
        transition = transition_archive_job_failure(
            database,
            claim=claim,
            error=FastF1SessionLoadError("controlled"),
            jitter_fraction=0.5,
        )

    assert transition.disposition is RetryDisposition.RETRYABLE
    assert transition.status == "failed"
    assert transition.next_retry_at is None


def test_claim_skips_locked_job_session(
    orchestration_target: OrchestrationTarget,
) -> None:
    first_session_id, second_session_id = orchestration_target.session_ids[:2]
    queue_session(orchestration_target, first_session_id)
    queue_session(orchestration_target, second_session_id)

    with orchestration_target.session_factory() as locker:
        with locker.begin():
            locked = locker.scalar(
                select(BackfillJobSession)
                .where(
                    BackfillJobSession.job_id
                    == orchestration_target.job_id,
                    BackfillJobSession.session_id == first_session_id,
                )
                .with_for_update()
            )
            assert locked is not None

            with orchestration_target.session_factory() as claimant:
                claim = claim_next_archive_job_session(claimant)

    assert claim is not None
    assert claim.session_id == second_session_id

    with orchestration_target.session_factory() as database:
        first = database.get(
            BackfillJobSession,
            (orchestration_target.job_id, first_session_id),
        )
        assert first is not None
        assert first.status == "pending"
        assert first.attempt_count == 0


def test_claim_ignores_delayed_and_exhausted_rows(
    orchestration_target: OrchestrationTarget,
) -> None:
    first_session_id, second_session_id = orchestration_target.session_ids[:2]
    queue_session(
        orchestration_target,
        first_session_id,
        next_retry_at=datetime.now(UTC) + timedelta(days=1),
    )
    queue_session(
        orchestration_target,
        second_session_id,
        attempt_count=4,
    )

    with orchestration_target.session_factory() as database:
        claim = claim_next_archive_job_session(
            database,
            settings=BackfillRuntimeSettings(),
        )

    assert claim is None
    with orchestration_target.session_factory() as database:
        job = database.get(BackfillJob, orchestration_target.job_id)
        assert job is not None
        assert job.status == "pending"
        assert database.scalar(
            select(SessionIngestion.session_id).where(
                SessionIngestion.session_id.in_(
                    (first_session_id, second_session_id)
                )
            )
        ) is None


def test_claim_preserves_non_archive_persistent_state(
    orchestration_target: OrchestrationTarget,
) -> None:
    session_id = orchestration_target.session_ids[0]
    queue_session(orchestration_target, session_id)
    with orchestration_target.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO session_ingestions (
                    session_id,
                    status,
                    source,
                    record_state,
                    attempt_count
                )
                VALUES (
                    :session_id,
                    'running',
                    'live_signalr',
                    'provisional',
                    3
                )
                """
            ),
            {"session_id": session_id},
        )

    with orchestration_target.session_factory() as database:
        with pytest.raises(ArchiveSourceConflictError, match="another source"):
            claim_next_archive_job_session(database)

    with orchestration_target.session_factory() as database:
        job = database.get(BackfillJob, orchestration_target.job_id)
        job_session = database.get(
            BackfillJobSession,
            (orchestration_target.job_id, session_id),
        )
        ingestion = database.get(SessionIngestion, session_id)

        assert job is not None
        assert job.status == "pending"
        assert job_session is not None
        assert job_session.status == "pending"
        assert job_session.attempt_count == 0
        assert ingestion is not None
        assert ingestion.status == "running"
        assert ingestion.source == "live_signalr"
        assert ingestion.attempt_count == 3


def test_claim_rejects_an_existing_archive_running_state(
    orchestration_target: OrchestrationTarget,
) -> None:
    session_id = orchestration_target.session_ids[0]
    queue_session(orchestration_target, session_id)
    with orchestration_target.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO session_ingestions (
                    session_id,
                    status,
                    source,
                    record_state,
                    attempt_count
                )
                VALUES (
                    :session_id,
                    'running',
                    'fastf1_archive',
                    'finalized',
                    4
                )
                """
            ),
            {"session_id": session_id},
        )

    with orchestration_target.session_factory() as database:
        with pytest.raises(
            BackfillPersistentStateConflictError,
            match="already running",
        ):
            claim_next_archive_job_session(database)


@pytest.mark.parametrize(
    "ownership_field",
    ["job_attempt_count", "session_attempt_token"],
)
def test_failure_transition_rejects_a_stale_ownership_token(
    orchestration_target: OrchestrationTarget,
    ownership_field: str,
) -> None:
    session_id = orchestration_target.session_ids[0]
    queue_session(orchestration_target, session_id)

    with orchestration_target.session_factory() as database:
        claim = claim_next_archive_job_session(database)
    assert claim is not None
    stale_claim = replace(
        claim,
        **{
            ownership_field: getattr(claim, ownership_field) + 1,
        },
    )

    with orchestration_target.session_factory() as database:
        with pytest.raises(
            BackfillClaimOwnershipError,
            match="no longer owns",
        ):
            transition_archive_job_failure(
                database,
                claim=stale_claim,
                error=FastF1SessionLoadError("controlled"),
                jitter_fraction=0.5,
            )

    with orchestration_target.session_factory() as database:
        job_session = database.get(
            BackfillJobSession,
            (orchestration_target.job_id, session_id),
        )
        ingestion = database.get(SessionIngestion, session_id)
        assert job_session is not None
        assert job_session.status == "running"
        assert job_session.last_error_code is None
        assert ingestion is not None
        assert ingestion.status == "running"
        assert ingestion.attempt_count == claim.session_attempt_token
        assert ingestion.last_error_code is None


def test_orchestration_operations_require_transaction_ownership(
    orchestration_target: OrchestrationTarget,
) -> None:
    queue_session(
        orchestration_target,
        orchestration_target.session_ids[0],
    )

    with orchestration_target.session_factory() as database:
        with database.begin():
            with pytest.raises(
                BackfillOrchestrationTransactionError,
                match="own",
            ):
                claim_next_archive_job_session(database)
