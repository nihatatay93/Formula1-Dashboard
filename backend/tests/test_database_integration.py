import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import Connection
from psycopg.errors import CheckViolation, UniqueViolation

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="TEST_DATABASE_URL is required for database integration tests",
)


@pytest.fixture
def connection() -> Iterator[Connection[tuple]]:
    assert TEST_DATABASE_URL is not None
    with psycopg.connect(TEST_DATABASE_URL) as database_connection:
        try:
            yield database_connection
        finally:
            database_connection.rollback()


def test_control_plane_constraints_and_indexes(
    connection: Connection[tuple],
) -> None:
    season_year = 32000
    connection.execute("INSERT INTO seasons (year) VALUES (%s)", (season_year,))
    event_id = connection.execute(
        """
        INSERT INTO events (
            season_year,
            round_number,
            event_name,
            source
        )
        VALUES (%s, 1, 'Schema Test Grand Prix', 'fastf1_archive')
        RETURNING id
        """,
        (season_year,),
    ).fetchone()[0]
    session_id = connection.execute(
        """
        INSERT INTO sessions (
            event_id,
            session_key,
            session_name,
            source
        )
        VALUES (%s, 'race', 'Race', 'fastf1_archive')
        RETURNING id
        """,
        (event_id,),
    ).fetchone()[0]

    with pytest.raises(UniqueViolation):
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO events (
                    season_year,
                    round_number,
                    event_name,
                    source
                )
                VALUES (%s, 1, 'Duplicate', 'fastf1_archive')
                """,
                (season_year,),
            )

    with pytest.raises(UniqueViolation):
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO sessions (
                    event_id,
                    session_key,
                    session_name,
                    source
                )
                VALUES (%s, 'race', 'Duplicate', 'fastf1_archive')
                """,
                (event_id,),
            )

    active_job_id = uuid.uuid4()
    connection.execute(
        """
        INSERT INTO backfill_jobs (id, season_year, status, request_reason)
        VALUES (%s, %s, 'pending', 'missing')
        """,
        (active_job_id, season_year),
    )
    with pytest.raises(UniqueViolation):
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO backfill_jobs (
                    id,
                    season_year,
                    status,
                    request_reason
                )
                VALUES (%s, %s, 'running', 'manual')
                """,
                (uuid.uuid4(), season_year),
            )

    connection.execute(
        """
        INSERT INTO backfill_jobs (id, season_year, status, request_reason)
        VALUES (%s, %s, 'completed', 'manual')
        """,
        (uuid.uuid4(), season_year),
    )
    connection.execute(
        """
        INSERT INTO backfill_jobs (id, season_year, status, request_reason)
        VALUES (%s, %s, 'failed', 'manual')
        """,
        (uuid.uuid4(), season_year),
    )

    connection.execute(
        """
        INSERT INTO backfill_job_sessions (job_id, session_id)
        VALUES (%s, %s)
        """,
        (active_job_id, session_id),
    )

    with pytest.raises(CheckViolation):
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO session_ingestions (
                    session_id,
                    status,
                    source,
                    record_state
                )
                VALUES (%s, 'unknown', 'fastf1_archive', 'finalized')
                """,
                (session_id,),
            )

    with pytest.raises(CheckViolation):
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO session_ingestions (
                    session_id,
                    status,
                    source,
                    record_state
                )
                VALUES (%s, 'pending', 'unknown', 'finalized')
                """,
                (session_id,),
            )

    with pytest.raises(CheckViolation):
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO session_ingestions (
                    session_id,
                    status,
                    source,
                    record_state
                )
                VALUES (%s, 'pending', 'fastf1_archive', 'unknown')
                """,
                (session_id,),
            )

    index_names = {
        row[0]
        for row in connection.execute(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename IN (
                  'backfill_jobs',
                  'backfill_job_sessions',
                  'session_ingestions'
              )
            """
        )
    }
    assert {
        "uq_backfill_jobs_active_season",
        "ix_backfill_job_sessions_status_next_retry_at_queued_at",
        "ix_session_ingestions_status_next_retry_at",
    } <= index_names

    connection.execute("SET LOCAL enable_seqscan = off")
    claim_plan = "\n".join(
        row[0]
        for row in connection.execute(
            """
            EXPLAIN (COSTS OFF)
            SELECT job_id, session_id
            FROM backfill_job_sessions
            WHERE status = 'pending'
              AND (next_retry_at IS NULL OR next_retry_at <= now())
            ORDER BY next_retry_at NULLS FIRST, queued_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
    )
    assert "ix_backfill_job_sessions_status_next_retry_at_queued_at" in claim_plan
