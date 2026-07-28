import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.contracts import SeasonStatus
from app.api.season_overview import (
    SeasonOverviewReadError,
    read_season_overview,
)
from app.db.engine import sqlalchemy_database_url

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@dataclass(frozen=True, slots=True)
class SeasonOverviewTarget:
    engine: Engine
    session_factory: sessionmaker[Session]
    season_year: int
    missing_year: int
    active_job_id: uuid.UUID
    latest_discovered_at: datetime


@pytest.fixture
def season_overview_target() -> Iterator[SeasonOverviewTarget]:
    if TEST_DATABASE_URL is None:
        pytest.skip("TEST_DATABASE_URL is required for season overview tests")
    engine = create_engine(sqlalchemy_database_url(TEST_DATABASE_URL))
    active_job_id = uuid.uuid4()

    with engine.begin() as connection:
        years = tuple(
            connection.scalars(
                text(
                    """
                    SELECT candidate
                    FROM generate_series(25000, 26999) AS candidate
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM seasons
                        WHERE year = candidate
                    )
                    ORDER BY candidate
                    LIMIT 2
                    """
                )
            )
        )
        assert len(years) == 2
        season_year, missing_year = years
        database_now = connection.scalar(text("SELECT now()"))
        assert database_now is not None
        latest_discovered_at = database_now - timedelta(minutes=1)
        older_discovered_at = latest_discovered_at - timedelta(days=1)

        connection.execute(
            text(
                """
                INSERT INTO seasons (
                    year,
                    coverage_checked_at,
                    coverage_valid_until
                )
                VALUES (
                    :year,
                    :coverage_checked_at,
                    :coverage_valid_until
                )
                """
            ),
            {
                "year": season_year,
                "coverage_checked_at": latest_discovered_at,
                "coverage_valid_until": database_now + timedelta(days=1),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO deferred_season_events (
                    season_year,
                    round_number,
                    event_name,
                    scheduled_start_at,
                    discovered_at
                )
                VALUES
                    (
                        :year, 3, 'Future Read Model Grand Prix',
                        :current_start, :current_discovered
                    ),
                    (
                        :year, 4, 'Stale Deferred Grand Prix',
                        :stale_start, :stale_discovered
                    )
                """
            ),
            {
                "year": season_year,
                "current_start": database_now + timedelta(days=10),
                "current_discovered": latest_discovered_at,
                "stale_start": database_now + timedelta(days=20),
                "stale_discovered": older_discovered_at,
            },
        )
        current_event_id = connection.scalar(
            text(
                """
                INSERT INTO events (
                    season_year,
                    round_number,
                    official_name,
                    event_name,
                    country,
                    location,
                    event_format,
                    starts_at,
                    ends_at,
                    last_discovered_at,
                    source
                )
                VALUES (
                    :season_year,
                    2,
                    'FORMULA 1 READ MODEL GRAND PRIX',
                    'Read Model Grand Prix',
                    'Test Country',
                    'Test Circuit',
                    'conventional',
                    :starts_at,
                    :ends_at,
                    :last_discovered_at,
                    'fastf1_archive'
                )
                RETURNING id
                """
            ),
            {
                "season_year": season_year,
                "starts_at": database_now - timedelta(days=11),
                "ends_at": database_now,
                "last_discovered_at": latest_discovered_at,
            },
        )
        assert current_event_id is not None
        removed_event_id = connection.scalar(
            text(
                """
                INSERT INTO events (
                    season_year,
                    round_number,
                    event_name,
                    last_discovered_at,
                    source
                )
                VALUES (
                    :season_year,
                    1,
                    'Removed Grand Prix',
                    :last_discovered_at,
                    'fastf1_archive'
                )
                RETURNING id
                """
            ),
            {
                "season_year": season_year,
                "last_discovered_at": older_discovered_at,
            },
        )
        assert removed_event_id is not None

        failed_session_id = _insert_session(
            connection,
            event_id=current_event_id,
            session_key="race",
            session_name="Race",
            scheduled_start_at=database_now - timedelta(days=10, hours=2),
            scheduled_end_at=database_now - timedelta(days=10),
            discovered_at=latest_discovered_at,
        )
        running_session_id = _insert_session(
            connection,
            event_id=current_event_id,
            session_key="qualifying",
            session_name="Qualifying",
            scheduled_start_at=database_now - timedelta(hours=5),
            scheduled_end_at=database_now - timedelta(hours=3),
            discovered_at=latest_discovered_at,
        )
        _insert_session(
            connection,
            event_id=current_event_id,
            session_key="sprint",
            session_name="Sprint",
            scheduled_start_at=database_now - timedelta(hours=2),
            scheduled_end_at=database_now - timedelta(hours=1),
            discovered_at=latest_discovered_at,
        )
        _insert_session(
            connection,
            event_id=removed_event_id,
            session_key="race",
            session_name="Removed Race",
            scheduled_start_at=database_now - timedelta(days=20),
            scheduled_end_at=database_now - timedelta(days=20),
            discovered_at=older_discovered_at,
        )

        connection.execute(
            text(
                """
                INSERT INTO session_ingestions (
                    session_id,
                    status,
                    source,
                    record_state,
                    attempt_count,
                    completed_at,
                    last_error_code,
                    last_error_message
                )
                VALUES (
                    :session_id,
                    'failed',
                    'fastf1_archive',
                    'finalized',
                    4,
                    :completed_at,
                    'fastf1_normalization_failed',
                    'FastF1 session normalization failed.'
                )
                """
            ),
            {
                "session_id": failed_session_id,
                "completed_at": database_now - timedelta(days=8),
            },
        )
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
                    1
                )
                """
            ),
            {"session_id": running_session_id},
        )
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
                    'running',
                    'stale'
                )
                """
            ),
            {
                "job_id": active_job_id,
                "season_year": season_year,
            },
        )

    target = SeasonOverviewTarget(
        engine=engine,
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
        season_year=season_year,
        missing_year=missing_year,
        active_job_id=active_job_id,
        latest_discovered_at=latest_discovered_at,
    )
    try:
        yield target
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM backfill_jobs WHERE season_year = :year"
                ),
                {"year": season_year},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM session_ingestions
                    WHERE session_id IN (
                        SELECT sessions.id
                        FROM sessions
                        JOIN events ON events.id = sessions.event_id
                        WHERE events.season_year = :year
                    )
                    """
                ),
                {"year": season_year},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM sessions
                    WHERE event_id IN (
                        SELECT id FROM events WHERE season_year = :year
                    )
                    """
                ),
                {"year": season_year},
            )
            connection.execute(
                text("DELETE FROM events WHERE season_year = :year"),
                {"year": season_year},
            )
            connection.execute(
                text("DELETE FROM seasons WHERE year = :year"),
                {"year": season_year},
            )
        engine.dispose()


def _insert_session(
    connection,
    *,
    event_id: int,
    session_key: str,
    session_name: str,
    scheduled_start_at: datetime,
    scheduled_end_at: datetime,
    discovered_at: datetime,
) -> int:
    session_id = connection.scalar(
        text(
            """
            INSERT INTO sessions (
                event_id,
                session_key,
                session_name,
                scheduled_start_at,
                scheduled_end_at,
                last_discovered_at,
                source
            )
            VALUES (
                :event_id,
                :session_key,
                :session_name,
                :scheduled_start_at,
                :scheduled_end_at,
                :last_discovered_at,
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
            "scheduled_end_at": scheduled_end_at,
            "last_discovered_at": discovered_at,
        },
    )
    assert session_id is not None
    return session_id


def test_missing_season_returns_domain_state_without_creating_rows(
    season_overview_target: SeasonOverviewTarget,
) -> None:
    response = read_season_overview(
        season_year=season_overview_target.missing_year,
        session_factory=season_overview_target.session_factory,
    )

    assert response.status is SeasonStatus.MISSING
    assert response.coverage.checked_at is None
    assert response.coverage.valid_until is None
    assert response.coverage.is_stale is True
    assert response.counts.model_dump() == {
        "events": 0,
        "sessions": 0,
        "archive_eligible": 0,
        "data_available": 0,
        "pending": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
    }
    assert response.active_job is None
    assert response.events == ()
    assert response.deferred_future_events == ()

    with season_overview_target.engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM seasons WHERE year = :year"
                ),
                {"year": season_overview_target.missing_year},
            )
            == 0
        )


def test_overview_uses_latest_membership_and_preserves_usable_failed_snapshot(
    season_overview_target: SeasonOverviewTarget,
) -> None:
    response = read_season_overview(
        season_year=season_overview_target.season_year,
        session_factory=season_overview_target.session_factory,
    )

    assert response.status is SeasonStatus.PARTIAL
    assert response.coverage.checked_at == (
        season_overview_target.latest_discovered_at
    )
    assert response.coverage.is_stale is False
    assert response.counts.model_dump() == {
        "events": 1,
        "sessions": 3,
        "archive_eligible": 2,
        "data_available": 1,
        "pending": 0,
        "running": 1,
        "completed": 0,
        "failed": 1,
    }
    assert response.active_job is not None
    assert response.active_job.id == season_overview_target.active_job_id
    assert response.active_job.status.value == "running"
    assert len(response.deferred_future_events) == 1
    assert response.deferred_future_events[0].round_number == 3
    assert (
        response.deferred_future_events[0].event_name
        == "Future Read Model Grand Prix"
    )

    assert tuple(event.event_name for event in response.events) == (
        "Read Model Grand Prix",
    )
    sessions = response.events[0].sessions
    assert tuple(session.session_key for session in sessions) == (
        "race",
        "qualifying",
        "sprint",
    )

    failed, running, grace = sessions
    assert failed.data_available is True
    assert failed.ingestion is not None
    assert failed.ingestion.status.value == "failed"
    assert failed.ingestion.last_error is not None
    assert failed.ingestion.last_error.model_dump() == {
        "code": "fastf1_normalization_failed",
        "message": "FastF1 session normalization failed.",
    }
    assert failed.archive_eligibility.eligible is True
    assert failed.archive_eligibility.reason.value == "correction_checkpoint"

    assert running.data_available is False
    assert running.archive_eligibility.eligible is True
    assert running.archive_eligibility.reason.value == "initial_archive"

    assert grace.ingestion is None
    assert grace.archive_eligibility.eligible is False
    assert grace.archive_eligibility.reason.value == "availability_grace"


def test_overview_executes_inside_a_read_only_transaction(
    season_overview_target: SeasonOverviewTarget,
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
        season_overview_target.engine,
        "before_cursor_execute",
        capture_statement,
    )
    try:
        read_season_overview(
            season_year=season_overview_target.season_year,
            session_factory=season_overview_target.session_factory,
        )
    finally:
        event.remove(
            season_overview_target.engine,
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


@pytest.mark.parametrize("season_year", [True, 2017, "2024"])
def test_overview_rejects_invalid_internal_year_without_opening_database(
    season_year: object,
) -> None:
    def forbidden_session_factory() -> Session:
        raise AssertionError("database must not be opened")

    with pytest.raises(SeasonOverviewReadError, match="season_year"):
        read_season_overview(
            season_year=season_year,
            session_factory=forbidden_session_factory,
        )
