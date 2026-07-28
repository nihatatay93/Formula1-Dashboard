import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.engine import sqlalchemy_database_url
from app.ingestion.runtime_policy import BackfillRuntimeSettings
from app.worker import plan_current_season

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="TEST_DATABASE_URL is required for automatic-planning tests",
)


class ForbiddenScheduleLoader:
    def load(self, season_year: int) -> object:
        raise AssertionError(
            f"fresh coverage must not load the {season_year} schedule"
        )


def test_automatic_planning_queues_due_work_idempotently_without_refresh() -> None:
    assert TEST_DATABASE_URL is not None
    engine = create_engine(sqlalchemy_database_url(TEST_DATABASE_URL))
    factory = sessionmaker(engine, expire_on_commit=False)
    with engine.begin() as connection:
        season_year = connection.scalar(
            text(
                """
                SELECT candidate
                FROM generate_series(9000, 9999) AS candidate
                WHERE NOT EXISTS (
                    SELECT 1 FROM seasons WHERE year = candidate
                )
                ORDER BY candidate
                LIMIT 1
                """
            )
        )
        assert season_year is not None
        database_now = connection.scalar(text("SELECT clock_timestamp()"))
        assert database_now is not None
        marker = database_now
        connection.execute(
            text(
                """
                INSERT INTO seasons (
                    year, coverage_checked_at, coverage_valid_until
                )
                VALUES (:year, :marker, :valid_until)
                """
            ),
            {
                "year": season_year,
                "marker": marker,
                "valid_until": database_now
                + BackfillRuntimeSettings().historical_season_coverage_ttl,
            },
        )
        event_id = connection.scalar(
            text(
                """
                INSERT INTO events (
                    season_year, round_number, event_name,
                    last_discovered_at, source
                )
                VALUES (
                    :year, 1, 'Automatic Planning GP',
                    :marker, 'fastf1_archive'
                )
                RETURNING id
                """
            ),
            {"year": season_year, "marker": marker},
        )
        missing_session_id = connection.scalar(
            text(
                """
                INSERT INTO sessions (
                    event_id, session_key, session_name,
                    scheduled_start_at, scheduled_end_at,
                    last_discovered_at, source
                )
                VALUES (
                    :event_id, 'qualifying', 'Qualifying',
                    :start_at, :end_at, :marker, 'fastf1_archive'
                )
                RETURNING id
                """
            ),
            {
                "event_id": event_id,
                "start_at": database_now
                - BackfillRuntimeSettings().archive_availability_grace
                - BackfillRuntimeSettings().archive_availability_grace,
                "end_at": database_now
                - BackfillRuntimeSettings().archive_availability_grace,
                "marker": marker,
            },
        )
        correction_session_id = connection.scalar(
            text(
                """
                INSERT INTO sessions (
                    event_id, session_key, session_name,
                    scheduled_start_at, scheduled_end_at,
                    last_discovered_at, source
                )
                VALUES (
                    :event_id, 'race', 'Race',
                    :start_at, :end_at, :marker, 'fastf1_archive'
                )
                RETURNING id
                """
            ),
            {
                "event_id": event_id,
                "start_at": database_now
                - BackfillRuntimeSettings()
                .archive_correction_checkpoints[0]
                - BackfillRuntimeSettings().archive_availability_grace,
                "end_at": database_now
                - BackfillRuntimeSettings()
                .archive_correction_checkpoints[0],
                "marker": marker,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO session_ingestions (
                    session_id, status, source, record_state, completed_at
                )
                VALUES (
                    :session_id, 'completed', 'fastf1_archive',
                    'finalized', :completed_at
                )
                """
            ),
            {
                "session_id": correction_session_id,
                "completed_at": database_now
                - BackfillRuntimeSettings()
                .archive_correction_checkpoints[0]
                + BackfillRuntimeSettings().archive_availability_grace,
            },
        )

    try:
        first = plan_current_season(
            session_factory=factory,
            schedule_loader=ForbiddenScheduleLoader(),
            settings=BackfillRuntimeSettings(),
            now_provider=lambda: datetime(
                season_year,
                7,
                28,
                tzinfo=UTC,
            ),
        )
        second = plan_current_season(
            session_factory=factory,
            schedule_loader=ForbiddenScheduleLoader(),
            settings=BackfillRuntimeSettings(),
            now_provider=lambda: datetime(
                season_year,
                7,
                28,
                tzinfo=UTC,
            ),
        )

        assert first.job_id is not None
        assert second.job_id == first.job_id
        assert first.job_created is True
        assert second.job_created is False
        assert set(first.newly_queued_session_ids) == {
            missing_session_id,
            correction_session_id,
        }
        assert second.newly_queued_session_ids == ()
        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM backfill_job_sessions
                    WHERE job_id = :job_id
                    """
                ),
                {"job_id": first.job_id},
            ) == 2
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
