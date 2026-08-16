"""Schema constraints and idempotent upsert keys, against a real database.

Nothing here is committed: the fixture rolls back, so the rows exist only for
the length of the test and no teardown is needed. What the rollback cannot do
is hide rows that are already committed, and a unique constraint sees those
from inside an open transaction. So every identifier this test inserts has to
be one the archive cannot already hold -- a real database has seasons 2018 and
2026 in it, and drivers keyed `max_verstappen` and `norris`.
"""

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

# Years no real season can occupy. Sibling modules reserve their own bands --
# 25000-26999, 28000-29999, 30000-31999 and 32000-32999 are taken -- and this
# is the free one between them.
#
# `seasons.year` is a smallint, so nothing above 32767 can be inserted at all.
SYNTHETIC_YEAR_RANGE = (27000, 27999)


@pytest.fixture
def connection() -> Iterator[Connection[tuple]]:
    assert TEST_DATABASE_URL is not None
    with psycopg.connect(TEST_DATABASE_URL) as database_connection:
        try:
            yield database_connection
        finally:
            # Every row this test writes disappears here. The constraints it
            # exercises are checked at statement time, so none of them need
            # the transaction to commit.
            database_connection.rollback()


def reserve_years(connection: Connection[tuple], *, count: int) -> list[int]:
    """Pick unused synthetic season years, so a populated archive cannot clash."""

    rows = connection.execute(
        """
        SELECT candidate
        -- Casts are required: generate_series is overloaded, and an untyped
        -- parameter leaves Postgres unable to pick between them.
        FROM generate_series(%s::int, %s::int) AS candidate
        WHERE NOT EXISTS (
            SELECT 1 FROM seasons WHERE year = candidate
        )
        ORDER BY candidate
        LIMIT %s
        """,
        (*SYNTHETIC_YEAR_RANGE, count),
    ).fetchall()
    years = [row[0] for row in rows]
    assert len(years) == count, "no free synthetic season year is available"
    return years


def create_race_session(
    connection: Connection[tuple],
    *,
    year: int,
    event_name: str,
) -> int:
    connection.execute("INSERT INTO seasons (year) VALUES (%s)", (year,))
    event_id = connection.execute(
        """
        INSERT INTO events (
            season_year,
            round_number,
            event_name,
            source
        )
        VALUES (%s, 1, %s, 'fastf1_archive')
        RETURNING id
        """,
        (year, event_name),
    ).fetchone()[0]
    return connection.execute(
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


def test_sporting_data_constraints_and_idempotent_keys(
    connection: Connection[tuple],
) -> None:
    first_year, second_year = reserve_years(connection, count=2)
    # `drivers.jolpica_driver_id` is unique across every season, so a real
    # archive already holds `max_verstappen`. The suffix is what keeps this
    # test from depending on an empty database.
    suffix = uuid.uuid4().hex
    verstappen_key = f"max_verstappen-{suffix}"
    norris_key = f"norris-{suffix}"

    first_session = create_race_session(
        connection,
        year=first_year,
        event_name=f"{first_year} Schema Test Grand Prix",
    )
    second_session = create_race_session(
        connection,
        year=second_year,
        event_name=f"{second_year} Schema Test Grand Prix",
    )

    verstappen_id = connection.execute(
        """
        INSERT INTO drivers (
            jolpica_driver_id,
            live_reference,
            given_name,
            family_name,
            full_name,
            country_code
        )
        VALUES (
            %s,
            %s,
            'Max',
            'Verstappen',
            'Max Verstappen',
            'NED'
        )
        RETURNING id
        """,
        (verstappen_key, verstappen_key),
    ).fetchone()[0]
    norris_id = connection.execute(
        """
        INSERT INTO drivers (
            jolpica_driver_id,
            live_reference,
            given_name,
            family_name,
            full_name,
            country_code
        )
        VALUES (
            %s,
            %s,
            'Lando',
            'Norris',
            'Lando Norris',
            'GBR'
        )
        RETURNING id
        """,
        (norris_key, norris_key),
    ).fetchone()[0]

    with pytest.raises(UniqueViolation):
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO drivers (jolpica_driver_id, full_name)
                VALUES (%s, 'Duplicate Driver')
                """,
                (verstappen_key,),
            )

    reingested_verstappen_id = connection.execute(
        """
        INSERT INTO drivers (
            jolpica_driver_id,
            live_reference,
            full_name
        )
        VALUES (
            %s,
            %s,
            'Max Verstappen'
        )
        ON CONFLICT (jolpica_driver_id)
        DO UPDATE SET full_name = EXCLUDED.full_name
        RETURNING id
        """,
        (verstappen_key, verstappen_key),
    ).fetchone()[0]
    assert reingested_verstappen_id == verstappen_id

    verstappen_entry = connection.execute(
        """
        INSERT INTO session_entries (
            session_id,
            driver_id,
            entry_key,
            racing_number,
            abbreviation,
            broadcast_name,
            display_name,
            team_jolpica_id,
            team_name,
            team_color,
            source,
            record_state
        )
        VALUES (
            %s,
            %s,
            'jolpica:max_verstappen',
            '1',
            'VER',
            'M VERSTAPPEN',
            'Max Verstappen',
            'red_bull',
            'Red Bull Racing',
            '3671C6',
            'fastf1_archive',
            'finalized'
        )
        RETURNING id
        """,
        (first_session, verstappen_id),
    ).fetchone()[0]
    reingested_verstappen_entry = connection.execute(
        """
        INSERT INTO session_entries (
            session_id,
            driver_id,
            entry_key,
            racing_number,
            display_name,
            source,
            record_state
        )
        VALUES (
            %s,
            %s,
            'jolpica:max_verstappen',
            '1',
            'Max Verstappen',
            'fastf1_archive',
            'finalized'
        )
        ON CONFLICT (session_id, entry_key)
        DO UPDATE SET display_name = EXCLUDED.display_name
        RETURNING id
        """,
        (first_session, verstappen_id),
    ).fetchone()[0]
    assert reingested_verstappen_entry == verstappen_entry

    unresolved_entry = connection.execute(
        """
        INSERT INTO session_entries (
            session_id,
            driver_id,
            entry_key,
            racing_number,
            display_name,
            source,
            record_state
        )
        VALUES (
            %s,
            NULL,
            'live:number:99',
            '99',
            'Unresolved Driver',
            'live_signalr',
            'provisional'
        )
        RETURNING id
        """,
        (first_session,),
    ).fetchone()[0]

    norris_entry = connection.execute(
        """
        INSERT INTO session_entries (
            session_id,
            driver_id,
            entry_key,
            racing_number,
            abbreviation,
            display_name,
            source,
            record_state
        )
        VALUES (
            %s,
            %s,
            'jolpica:norris',
            '1',
            'NOR',
            'Lando Norris',
            'fastf1_archive',
            'finalized'
        )
        RETURNING id
        """,
        (second_session, norris_id),
    ).fetchone()[0]

    assert unresolved_entry is not None
    assert norris_entry is not None

    with pytest.raises(UniqueViolation):
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO session_entries (
                    session_id,
                    entry_key,
                    racing_number,
                    display_name,
                    source,
                    record_state
                )
                VALUES (
                    %s,
                    'duplicate:number',
                    '1',
                    'Duplicate Number',
                    'fastf1_archive',
                    'finalized'
                )
                """,
                (first_session,),
            )

    with pytest.raises(UniqueViolation):
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO session_entries (
                    session_id,
                    driver_id,
                    entry_key,
                    racing_number,
                    display_name,
                    source,
                    record_state
                )
                VALUES (
                    %s,
                    %s,
                    'duplicate:driver',
                    '33',
                    'Duplicate Driver Entry',
                    'fastf1_archive',
                    'finalized'
                )
                """,
                (first_session, verstappen_id),
            )

    with pytest.raises(UniqueViolation):
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO session_entries (
                    session_id,
                    entry_key,
                    racing_number,
                    display_name,
                    source,
                    record_state
                )
                VALUES (
                    %s,
                    'jolpica:max_verstappen',
                    '33',
                    'Duplicate Entry Key',
                    'fastf1_archive',
                    'finalized'
                )
                """,
                (first_session,),
            )

    with pytest.raises(CheckViolation):
        with connection.transaction():
            connection.execute(
                """
                UPDATE session_entries
                SET source = 'unknown'
                WHERE id = %s
                """,
                (unresolved_entry,),
            )

    connection.execute(
        """
        INSERT INTO session_results (
            session_entry_id,
            position,
            classified_position,
            grid_position,
            points,
            status,
            laps_completed,
            elapsed_time_us,
            gap_to_leader_us,
            gap_to_leader_laps,
            source,
            record_state
        )
        VALUES (
            %s,
            1,
            '1',
            1,
            26.000,
            'Finished',
            57,
            5504742000,
            0,
            0,
            'fastf1_archive',
            'finalized'
        )
        """,
        (verstappen_entry,),
    )
    connection.execute(
        """
        INSERT INTO session_results (
            session_entry_id,
            source,
            record_state
        )
        VALUES (%s, 'live_signalr', 'provisional')
        """,
        (unresolved_entry,),
    )

    with pytest.raises(CheckViolation):
        with connection.transaction():
            connection.execute(
                """
                UPDATE session_results
                SET elapsed_time_us = -1
                WHERE session_entry_id = %s
                """,
                (verstappen_entry,),
            )

    with pytest.raises(CheckViolation):
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO laps (
                    session_entry_id,
                    lap_number,
                    is_personal_best,
                    fastf1_generated,
                    is_accurate,
                    source,
                    record_state
                )
                VALUES (
                    %s,
                    0,
                    FALSE,
                    FALSE,
                    FALSE,
                    'fastf1_archive',
                    'finalized'
                )
                """,
                (verstappen_entry,),
            )

    with pytest.raises(CheckViolation):
        with connection.transaction():
            connection.execute(
                """
                UPDATE session_results
                SET record_state = 'unknown'
                WHERE session_entry_id = %s
                """,
                (verstappen_entry,),
            )

    connection.execute(
        """
        INSERT INTO session_results (
            session_entry_id,
            position,
            classified_position,
            grid_position,
            points,
            status,
            laps_completed,
            elapsed_time_us,
            gap_to_leader_us,
            gap_to_leader_laps,
            source,
            record_state
        )
        VALUES (
            %s,
            1,
            '1',
            1,
            26.000,
            'Finalized',
            57,
            5504742000,
            0,
            0,
            'fastf1_archive',
            'finalized'
        )
        ON CONFLICT (session_entry_id)
        DO UPDATE SET status = EXCLUDED.status
        """,
        (verstappen_entry,),
    )

    connection.execute(
        """
        INSERT INTO laps (
            session_entry_id,
            lap_number,
            stint_number,
            session_time_us,
            lap_time_us,
            lap_start_time_us,
            sector_1_time_us,
            sector_2_time_us,
            sector_3_time_us,
            speed_i1_kph,
            speed_i2_kph,
            speed_fl_kph,
            speed_st_kph,
            is_personal_best,
            compound,
            tyre_life_laps,
            fresh_tyre,
            track_status,
            position,
            deleted,
            fastf1_generated,
            is_accurate,
            source,
            record_state
        )
        VALUES (
            %s,
            1,
            1,
            90000000,
            90000000,
            0,
            30000000,
            30000000,
            30000000,
            285.5,
            291.0,
            280.0,
            315.2,
            TRUE,
            'SOFT',
            1,
            TRUE,
            '1',
            1,
            NULL,
            FALSE,
            TRUE,
            'fastf1_archive',
            'finalized'
        )
        """,
        (verstappen_entry,),
    )
    connection.execute(
        """
        INSERT INTO laps (
            session_entry_id,
            lap_number,
            is_personal_best,
            fastf1_generated,
            is_accurate,
            source,
            record_state
        )
        VALUES (
            %s,
            2,
            FALSE,
            FALSE,
            FALSE,
            'fastf1_archive',
            'finalized'
        )
        """,
        (verstappen_entry,),
    )

    with pytest.raises(UniqueViolation):
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO laps (
                    session_entry_id,
                    lap_number,
                    is_personal_best,
                    fastf1_generated,
                    is_accurate,
                    source,
                    record_state
                )
                VALUES (
                    %s,
                    1,
                    FALSE,
                    FALSE,
                    FALSE,
                    'fastf1_archive',
                    'finalized'
                )
                """,
                (verstappen_entry,),
            )

    with pytest.raises(CheckViolation):
        with connection.transaction():
            connection.execute(
                """
                UPDATE laps
                SET lap_time_us = -1
                WHERE session_entry_id = %s
                  AND lap_number = 1
                """,
                (verstappen_entry,),
            )

    with pytest.raises(CheckViolation):
        with connection.transaction():
            connection.execute(
                """
                UPDATE laps
                SET source = 'unknown'
                WHERE session_entry_id = %s
                  AND lap_number = 1
                """,
                (verstappen_entry,),
            )

    connection.execute(
        """
        INSERT INTO laps (
            session_entry_id,
            lap_number,
            lap_time_us,
            is_personal_best,
            fastf1_generated,
            is_accurate,
            source,
            record_state
        )
        VALUES (
            %s,
            1,
            89500000,
            TRUE,
            FALSE,
            TRUE,
            'fastf1_archive',
            'finalized'
        )
        ON CONFLICT (session_entry_id, lap_number)
        DO UPDATE SET lap_time_us = EXCLUDED.lap_time_us
        """,
        (verstappen_entry,),
    )

    result_count = connection.execute(
        """
        SELECT count(*)
        FROM session_results
        WHERE session_entry_id = %s
        """,
        (verstappen_entry,),
    ).fetchone()[0]
    lap_count, lap_time_us = connection.execute(
        """
        SELECT count(*), max(lap_time_us)
        FROM laps
        WHERE session_entry_id = %s
          AND lap_number = 1
        """,
        (verstappen_entry,),
    ).fetchone()
    assert result_count == 1
    assert lap_count == 1
    assert lap_time_us == 89500000

    index_names = {
        row[0]
        for row in connection.execute(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'session_entries'
            """
        )
    }
    assert {
        "uq_session_entries_session_driver",
        "uq_session_entries_session_racing_number",
    } <= index_names
