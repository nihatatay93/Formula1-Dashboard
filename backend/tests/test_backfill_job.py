import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.backfill_job import (
    BackfillJobNotFoundError,
    BackfillJobReadError,
    read_backfill_job,
)
from app.api.contracts import IngestionStatus
from app.db.engine import sqlalchemy_database_url

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@dataclass(frozen=True, slots=True)
class BackfillJobTarget:
    engine: Engine
    session_factory: sessionmaker[Session]
    season_year: int
    job_id: uuid.UUID
    failed_job_id: uuid.UUID
    session_ids_in_expected_order: tuple[int, ...]
    requested_at: datetime


@pytest.fixture
def backfill_job_target() -> Iterator[BackfillJobTarget]:
    if TEST_DATABASE_URL is None:
        pytest.skip("TEST_DATABASE_URL is required for backfill job tests")

    engine = create_engine(sqlalchemy_database_url(TEST_DATABASE_URL))
    job_id = uuid.uuid4()
    failed_job_id = uuid.uuid4()

    with engine.begin() as connection:
        season_year = connection.scalar(
            text(
                """
                SELECT candidate
                FROM generate_series(29000, 30999) AS candidate
                WHERE NOT EXISTS (
                    SELECT 1 FROM seasons WHERE year = candidate
                )
                ORDER BY candidate
                LIMIT 1
                """
            )
        )
        assert season_year is not None
        database_now = connection.scalar(text("SELECT now()"))
        assert database_now is not None
        requested_at = database_now - timedelta(minutes=10)

        connection.execute(
            text("INSERT INTO seasons (year) VALUES (:year)"),
            {"year": season_year},
        )
        round_two_event_id = _insert_event(
            connection,
            season_year=season_year,
            round_number=2,
            event_name="Second Grand Prix",
        )
        round_one_event_id = _insert_event(
            connection,
            season_year=season_year,
            round_number=1,
            event_name="First Grand Prix",
        )

        round_two_race_id = _insert_session(
            connection,
            event_id=round_two_event_id,
            session_key="race",
            session_name="Race",
            scheduled_start_at=database_now + timedelta(days=8),
        )
        round_one_race_id = _insert_session(
            connection,
            event_id=round_one_event_id,
            session_key="race",
            session_name="Race",
            scheduled_start_at=database_now + timedelta(days=1),
        )
        round_one_qualifying_id = _insert_session(
            connection,
            event_id=round_one_event_id,
            session_key="qualifying",
            session_name="Qualifying",
            scheduled_start_at=database_now,
        )
        round_two_qualifying_id = _insert_session(
            connection,
            event_id=round_two_event_id,
            session_key="qualifying",
            session_name="Qualifying",
            scheduled_start_at=database_now + timedelta(days=7),
        )

        connection.execute(
            text(
                """
                INSERT INTO backfill_jobs (
                    id,
                    season_year,
                    status,
                    request_reason,
                    requested_at,
                    started_at,
                    heartbeat_at
                )
                VALUES (
                    :job_id,
                    :season_year,
                    'running',
                    'partial',
                    :requested_at,
                    :started_at,
                    :heartbeat_at
                )
                """
            ),
            {
                "job_id": job_id,
                "season_year": season_year,
                "requested_at": requested_at,
                "started_at": requested_at + timedelta(seconds=2),
                "heartbeat_at": requested_at + timedelta(minutes=9),
            },
        )

        _insert_job_session(
            connection,
            job_id=job_id,
            session_id=round_two_race_id,
            status="pending",
            attempt_count=0,
            queued_at=requested_at,
            next_retry_at=requested_at + timedelta(minutes=5),
        )
        _insert_job_session(
            connection,
            job_id=job_id,
            session_id=round_one_race_id,
            status="completed",
            attempt_count=1,
            queued_at=requested_at,
            completed_at=requested_at + timedelta(minutes=4),
        )
        _insert_job_session(
            connection,
            job_id=job_id,
            session_id=round_one_qualifying_id,
            status="running",
            attempt_count=2,
            queued_at=requested_at,
            started_at=requested_at + timedelta(minutes=5),
            heartbeat_at=requested_at + timedelta(minutes=9),
        )
        _insert_job_session(
            connection,
            job_id=job_id,
            session_id=round_two_qualifying_id,
            status="failed",
            attempt_count=4,
            queued_at=requested_at,
            completed_at=requested_at + timedelta(minutes=8),
            last_error_code="fastf1_load_failed",
            last_error_message="FastF1 session loading failed.",
        )
        connection.execute(
            text(
                """
                INSERT INTO backfill_jobs (
                    id,
                    season_year,
                    status,
                    request_reason,
                    requested_at,
                    started_at,
                    completed_at,
                    last_error_code,
                    last_error_message
                )
                VALUES (
                    :job_id,
                    :season_year,
                    'failed',
                    'stale',
                    :requested_at,
                    :started_at,
                    :completed_at,
                    'backfill_job_failed',
                    'One or more backfill sessions failed.'
                )
                """
            ),
            {
                "job_id": failed_job_id,
                "season_year": season_year,
                "requested_at": requested_at - timedelta(days=1),
                "started_at": (
                    requested_at
                    - timedelta(days=1)
                    + timedelta(seconds=2)
                ),
                "completed_at": requested_at - timedelta(hours=23),
            },
        )
        _insert_job_session(
            connection,
            job_id=failed_job_id,
            session_id=round_two_qualifying_id,
            status="failed",
            attempt_count=4,
            queued_at=requested_at - timedelta(days=1),
            completed_at=requested_at - timedelta(hours=23),
            last_error_code="fastf1_load_failed",
            last_error_message="FastF1 session loading failed.",
        )

    target = BackfillJobTarget(
        engine=engine,
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
        season_year=season_year,
        job_id=job_id,
        failed_job_id=failed_job_id,
        session_ids_in_expected_order=(
            round_one_qualifying_id,
            round_one_race_id,
            round_two_qualifying_id,
            round_two_race_id,
        ),
        requested_at=requested_at,
    )
    try:
        yield target
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM backfill_jobs WHERE season_year = :season_year"
                ),
                {"season_year": season_year},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM sessions
                    WHERE event_id IN (
                        SELECT id FROM events WHERE season_year = :season_year
                    )
                    """
                ),
                {"season_year": season_year},
            )
            connection.execute(
                text("DELETE FROM events WHERE season_year = :season_year"),
                {"season_year": season_year},
            )
            connection.execute(
                text("DELETE FROM seasons WHERE year = :season_year"),
                {"season_year": season_year},
            )
        engine.dispose()


def _insert_event(
    connection,
    *,
    season_year: int,
    round_number: int,
    event_name: str,
) -> int:
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
                :round_number,
                :event_name,
                'fastf1_archive'
            )
            RETURNING id
            """
        ),
        {
            "season_year": season_year,
            "round_number": round_number,
            "event_name": event_name,
        },
    )
    assert event_id is not None
    return event_id


def _insert_session(
    connection,
    *,
    event_id: int,
    session_key: str,
    session_name: str,
    scheduled_start_at: datetime,
) -> int:
    session_id = connection.scalar(
        text(
            """
            INSERT INTO sessions (
                event_id,
                session_key,
                session_name,
                scheduled_start_at,
                source
            )
            VALUES (
                :event_id,
                :session_key,
                :session_name,
                :scheduled_start_at,
                'fastf1_archive'
            )
            RETURNING id
            """
        ),
        {
            "event_id": event_id,
            "session_key": session_key,
            "session_name": session_name,
            "scheduled_start_at": scheduled_start_at,
        },
    )
    assert session_id is not None
    return session_id


def _insert_job_session(
    connection,
    *,
    job_id: uuid.UUID,
    session_id: int,
    status: str,
    attempt_count: int,
    queued_at: datetime,
    started_at: datetime | None = None,
    heartbeat_at: datetime | None = None,
    next_retry_at: datetime | None = None,
    completed_at: datetime | None = None,
    last_error_code: str | None = None,
    last_error_message: str | None = None,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO backfill_job_sessions (
                job_id,
                session_id,
                status,
                attempt_count,
                queued_at,
                started_at,
                heartbeat_at,
                next_retry_at,
                completed_at,
                last_error_code,
                last_error_message
            )
            VALUES (
                :job_id,
                :session_id,
                :status,
                :attempt_count,
                :queued_at,
                :started_at,
                :heartbeat_at,
                :next_retry_at,
                :completed_at,
                :last_error_code,
                :last_error_message
            )
            """
        ),
        {
            "job_id": job_id,
            "session_id": session_id,
            "status": status,
            "attempt_count": attempt_count,
            "queued_at": queued_at,
            "started_at": started_at,
            "heartbeat_at": heartbeat_at,
            "next_retry_at": next_retry_at,
            "completed_at": completed_at,
            "last_error_code": last_error_code,
            "last_error_message": last_error_message,
        },
    )


def test_read_backfill_job_maps_progress_and_orders_sessions(
    backfill_job_target: BackfillJobTarget,
) -> None:
    response = read_backfill_job(
        job_id=backfill_job_target.job_id,
        session_factory=backfill_job_target.session_factory,
    )

    assert response.id == backfill_job_target.job_id
    assert response.season_year == backfill_job_target.season_year
    assert response.status is IngestionStatus.RUNNING
    assert response.request_reason == "partial"
    assert response.requested_at == backfill_job_target.requested_at
    assert response.started_at == (
        backfill_job_target.requested_at + timedelta(seconds=2)
    )
    assert response.heartbeat_at == (
        backfill_job_target.requested_at + timedelta(minutes=9)
    )
    assert response.completed_at is None
    assert response.last_error is None
    assert response.progress.model_dump() == {
        "total": 4,
        "pending": 1,
        "running": 1,
        "completed": 1,
        "failed": 1,
        "terminal": 2,
    }
    assert tuple(
        int(job_session.session_id) for job_session in response.sessions
    ) == backfill_job_target.session_ids_in_expected_order
    assert tuple(
        (job_session.round_number, job_session.session_key)
        for job_session in response.sessions
    ) == (
        (1, "qualifying"),
        (1, "race"),
        (2, "qualifying"),
        (2, "race"),
    )
    assert tuple(
        job_session.status.value for job_session in response.sessions
    ) == ("running", "completed", "failed", "pending")

    failed = response.sessions[2]
    assert failed.attempt_count == 4
    assert failed.last_error is not None
    assert failed.last_error.model_dump() == {
        "code": "fastf1_load_failed",
        "message": "FastF1 session loading failed.",
    }
    pending = response.sessions[3]
    assert pending.next_retry_at == (
        backfill_job_target.requested_at + timedelta(minutes=5)
    )


def test_read_backfill_job_does_not_aggregate_parent_state(
    backfill_job_target: BackfillJobTarget,
) -> None:
    read_backfill_job(
        job_id=backfill_job_target.job_id,
        session_factory=backfill_job_target.session_factory,
    )

    with backfill_job_target.engine.connect() as connection:
        parent = connection.execute(
            text(
                """
                SELECT status, completed_at
                FROM backfill_jobs
                WHERE id = :job_id
                """
            ),
            {"job_id": backfill_job_target.job_id},
        ).one()

    assert parent.status == "running"
    assert parent.completed_at is None


def test_read_backfill_job_maps_sanitized_parent_failure(
    backfill_job_target: BackfillJobTarget,
) -> None:
    response = read_backfill_job(
        job_id=backfill_job_target.failed_job_id,
        session_factory=backfill_job_target.session_factory,
    )

    assert response.status is IngestionStatus.FAILED
    assert response.request_reason == "stale"
    assert response.last_error is not None
    assert response.last_error.model_dump() == {
        "code": "backfill_job_failed",
        "message": "One or more backfill sessions failed.",
    }
    assert response.progress.model_dump() == {
        "total": 1,
        "pending": 0,
        "running": 0,
        "completed": 0,
        "failed": 1,
        "terminal": 1,
    }


def test_read_backfill_job_executes_inside_read_only_transaction(
    backfill_job_target: BackfillJobTarget,
) -> None:
    statements: list[str] = []

    def capture_statement(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        statements.append(statement)

    event.listen(
        backfill_job_target.engine,
        "before_cursor_execute",
        capture_statement,
    )
    try:
        read_backfill_job(
            job_id=backfill_job_target.job_id,
            session_factory=backfill_job_target.session_factory,
        )
    finally:
        event.remove(
            backfill_job_target.engine,
            "before_cursor_execute",
            capture_statement,
        )

    normalized = tuple(statement.strip().upper() for statement in statements)
    assert normalized[0] == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    )
    assert not any(
        statement.startswith(("INSERT ", "UPDATE ", "DELETE "))
        for statement in normalized
    )


def test_read_backfill_job_raises_not_found_without_writes(
    backfill_job_target: BackfillJobTarget,
) -> None:
    unknown_job_id = uuid.uuid4()

    with pytest.raises(
        BackfillJobNotFoundError,
        match=str(unknown_job_id),
    ):
        read_backfill_job(
            job_id=unknown_job_id,
            session_factory=backfill_job_target.session_factory,
        )


@pytest.mark.parametrize("job_id", [None, "not-a-uuid", 123])
def test_read_backfill_job_rejects_invalid_internal_identifier_without_database(
    job_id: object,
) -> None:
    def forbidden_session_factory() -> Session:
        raise AssertionError("database must not be opened")

    with pytest.raises(BackfillJobReadError, match="job_id"):
        read_backfill_job(
            job_id=job_id,
            session_factory=forbidden_session_factory,
        )
