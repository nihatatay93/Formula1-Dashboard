import os
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
    session_2024 = create_race_session(
        connection,
        year=2024,
        event_name="2024 Schema Test Grand Prix",
    )
    session_2026 = create_race_session(
        connection,
        year=2026,
        event_name="2026 Schema Test Grand Prix",
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
            'max_verstappen',
            'max_verstappen',
            'Max',
            'Verstappen',
            'Max Verstappen',
            'NED'
        )
        RETURNING id
        """
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
            'norris',
            'norris',
            'Lando',
            'Norris',
            'Lando Norris',
            'GBR'
        )
        RETURNING id
        """
    ).fetchone()[0]

    with pytest.raises(UniqueViolation):
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO drivers (jolpica_driver_id, full_name)
                VALUES ('max_verstappen', 'Duplicate Driver')
                """
            )

    reingested_verstappen_id = connection.execute(
        """
        INSERT INTO drivers (
            jolpica_driver_id,
            live_reference,
            full_name
        )
        VALUES (
            'max_verstappen',
            'max_verstappen',
            'Max Verstappen'
        )
        ON CONFLICT (jolpica_driver_id)
        DO UPDATE SET full_name = EXCLUDED.full_name
        RETURNING id
        """
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
        (session_2024, verstappen_id),
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
        (session_2024, verstappen_id),
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
        (session_2024,),
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
        (session_2026, norris_id),
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
                (session_2024,),
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
                (session_2024, verstappen_id),
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
                (session_2024,),
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
