import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.contracts import LapSummaryQuery
from app.api.session_data import (
    SessionDataReadError,
    SessionDataUnavailableError,
    SessionEntryNotFoundError,
    SessionNotFoundError,
    read_session_detail,
    read_session_laps,
    read_session_results,
)
from app.db.engine import sqlalchemy_database_url

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@dataclass(frozen=True, slots=True)
class SessionDataTarget:
    engine: Engine
    session_factory: sessionmaker[Session]
    season_year: int
    available_session_id: int
    unavailable_session_id: int
    other_session_id: int
    first_entry_id: int
    second_entry_id: int
    unresolved_entry_id: int
    provisional_entry_id: int
    other_entry_id: int
    completed_at: datetime
    source_updated_at: datetime


@pytest.fixture(scope="module")
def session_data_target() -> Iterator[SessionDataTarget]:
    if TEST_DATABASE_URL is None:
        pytest.skip("TEST_DATABASE_URL is required for session-data tests")

    engine = create_engine(sqlalchemy_database_url(TEST_DATABASE_URL))
    driver_suffix = uuid.uuid4().hex

    with engine.begin() as connection:
        season_year = connection.scalar(
            text(
                """
                SELECT candidate
                FROM generate_series(31000, 31999) AS candidate
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
        completed_at = database_now - timedelta(days=1)
        source_updated_at = completed_at - timedelta(minutes=5)

        connection.execute(
            text(
                """
                INSERT INTO seasons (
                    year,
                    coverage_checked_at,
                    coverage_valid_until
                )
                VALUES (:year, :checked_at, :valid_until)
                """
            ),
            {
                "year": season_year,
                "checked_at": database_now,
                "valid_until": database_now + timedelta(days=30),
            },
        )
        event_id = connection.scalar(
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
                    1,
                    'FORMULA 1 SESSION DATA GRAND PRIX',
                    'Session Data Grand Prix',
                    'Test Country',
                    'Test Circuit',
                    'conventional',
                    :starts_at,
                    :ends_at,
                    :discovered_at,
                    'fastf1_archive'
                )
                RETURNING id
                """
            ),
            {
                "season_year": season_year,
                "starts_at": database_now - timedelta(days=3),
                "ends_at": database_now - timedelta(days=1),
                "discovered_at": database_now,
            },
        )
        assert event_id is not None

        available_session_id = _insert_session(
            connection,
            event_id=event_id,
            session_key="practice-2",
            session_name="Practice 2",
            starts_at=database_now - timedelta(days=2, hours=2),
            ends_at=database_now - timedelta(days=2, hours=1),
            discovered_at=database_now,
        )
        unavailable_session_id = _insert_session(
            connection,
            event_id=event_id,
            session_key="qualifying",
            session_name="Qualifying",
            starts_at=database_now - timedelta(hours=2),
            ends_at=database_now - timedelta(hours=1),
            discovered_at=database_now,
        )
        other_session_id = _insert_session(
            connection,
            event_id=event_id,
            session_key="race",
            session_name="Race",
            starts_at=database_now + timedelta(days=1),
            ends_at=database_now + timedelta(days=1, hours=2),
            discovered_at=database_now,
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
                    source_updated_at,
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
                    :source_updated_at,
                    'fastf1_load_failed',
                    'FastF1 session loading failed.'
                )
                """
            ),
            {
                "session_id": available_session_id,
                "completed_at": completed_at,
                "source_updated_at": source_updated_at,
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
            {"session_id": unavailable_session_id},
        )

        first_driver_id = _insert_driver(
            connection,
            jolpica_driver_id=f"first_{driver_suffix}",
            full_name="First Driver",
        )
        second_driver_id = _insert_driver(
            connection,
            jolpica_driver_id=f"second_{driver_suffix}",
            full_name="Second Driver",
        )
        second_entry_id = _insert_entry(
            connection,
            session_id=available_session_id,
            driver_id=second_driver_id,
            entry_key=f"driver:jolpica:second_{driver_suffix}",
            racing_number="22",
            display_name="Second Driver",
            team_color="#FF00AA",
        )
        first_entry_id = _insert_entry(
            connection,
            session_id=available_session_id,
            driver_id=first_driver_id,
            entry_key=f"driver:jolpica:first_{driver_suffix}",
            racing_number="11",
            display_name="First Driver",
            team_color="3671c6",
        )
        unresolved_entry_id = _insert_entry(
            connection,
            session_id=available_session_id,
            driver_id=None,
            entry_key="car-number:99",
            racing_number="99",
            display_name="Unresolved Driver",
            team_color="not-a-color",
        )
        provisional_entry_id = _insert_entry(
            connection,
            session_id=unavailable_session_id,
            driver_id=None,
            entry_key="live:44",
            racing_number="44",
            display_name="Provisional Driver",
            team_color=None,
            source="live_signalr",
            record_state="provisional",
        )
        other_entry_id = _insert_entry(
            connection,
            session_id=other_session_id,
            driver_id=None,
            entry_key="car-number:77",
            racing_number="77",
            display_name="Other Driver",
            team_color=None,
        )

        _insert_result(
            connection,
            session_entry_id=second_entry_id,
            position=2,
            points="12.500",
            gap_to_leader_us=125_000,
        )
        _insert_result(
            connection,
            session_entry_id=first_entry_id,
            position=1,
            points="26.000",
            gap_to_leader_us=0,
        )

        for lap_number, stint_number, deleted in (
            (1, 1, False),
            (2, 1, False),
            (3, 2, False),
            (4, 2, True),
            (5, 2, None),
            (6, 3, False),
        ):
            _insert_lap(
                connection,
                session_entry_id=first_entry_id,
                lap_number=lap_number,
                stint_number=stint_number,
                deleted=deleted,
            )

    target = SessionDataTarget(
        engine=engine,
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
        season_year=season_year,
        available_session_id=available_session_id,
        unavailable_session_id=unavailable_session_id,
        other_session_id=other_session_id,
        first_entry_id=first_entry_id,
        second_entry_id=second_entry_id,
        unresolved_entry_id=unresolved_entry_id,
        provisional_entry_id=provisional_entry_id,
        other_entry_id=other_entry_id,
        completed_at=completed_at,
        source_updated_at=source_updated_at,
    )
    try:
        yield target
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM laps
                    WHERE session_entry_id IN (
                        SELECT session_entries.id
                        FROM session_entries
                        JOIN sessions
                          ON sessions.id = session_entries.session_id
                        JOIN events ON events.id = sessions.event_id
                        WHERE events.season_year = :season_year
                    )
                    """
                ),
                {"season_year": season_year},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM session_results
                    WHERE session_entry_id IN (
                        SELECT session_entries.id
                        FROM session_entries
                        JOIN sessions
                          ON sessions.id = session_entries.session_id
                        JOIN events ON events.id = sessions.event_id
                        WHERE events.season_year = :season_year
                    )
                    """
                ),
                {"season_year": season_year},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM session_entries
                    WHERE session_id IN (
                        SELECT sessions.id
                        FROM sessions
                        JOIN events ON events.id = sessions.event_id
                        WHERE events.season_year = :season_year
                    )
                    """
                ),
                {"season_year": season_year},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM session_ingestions
                    WHERE session_id IN (
                        SELECT sessions.id
                        FROM sessions
                        JOIN events ON events.id = sessions.event_id
                        WHERE events.season_year = :season_year
                    )
                    """
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
                text(
                    "DELETE FROM events WHERE season_year = :season_year"
                ),
                {"season_year": season_year},
            )
            connection.execute(
                text(
                    "DELETE FROM seasons WHERE year = :season_year"
                ),
                {"season_year": season_year},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM drivers
                    WHERE jolpica_driver_id IN (:first_id, :second_id)
                    """
                ),
                {
                    "first_id": f"first_{driver_suffix}",
                    "second_id": f"second_{driver_suffix}",
                },
            )
        engine.dispose()


def _insert_session(
    connection,
    *,
    event_id: int,
    session_key: str,
    session_name: str,
    starts_at: datetime,
    ends_at: datetime,
    discovered_at: datetime,
) -> int:
    value = connection.scalar(
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
                :starts_at,
                :ends_at,
                :discovered_at,
                'fastf1_archive'
            )
            RETURNING id
            """
        ),
        {
            "event_id": event_id,
            "session_key": session_key,
            "session_name": session_name,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "discovered_at": discovered_at,
        },
    )
    assert value is not None
    return value


def _insert_driver(
    connection,
    *,
    jolpica_driver_id: str,
    full_name: str,
) -> int:
    value = connection.scalar(
        text(
            """
            INSERT INTO drivers (
                jolpica_driver_id,
                given_name,
                family_name,
                full_name,
                country_code
            )
            VALUES (
                :jolpica_driver_id,
                :given_name,
                'Driver',
                :full_name,
                'GBR'
            )
            RETURNING id
            """
        ),
        {
            "jolpica_driver_id": jolpica_driver_id,
            "given_name": full_name.split()[0],
            "full_name": full_name,
        },
    )
    assert value is not None
    return value


def _insert_entry(
    connection,
    *,
    session_id: int,
    driver_id: int | None,
    entry_key: str,
    racing_number: str,
    display_name: str,
    team_color: str | None,
    source: str = "fastf1_archive",
    record_state: str = "finalized",
) -> int:
    value = connection.scalar(
        text(
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
                :session_id,
                :driver_id,
                :entry_key,
                :racing_number,
                :abbreviation,
                :broadcast_name,
                :display_name,
                'example_team',
                'Example Team',
                :team_color,
                :source,
                :record_state
            )
            RETURNING id
            """
        ),
        {
            "session_id": session_id,
            "driver_id": driver_id,
            "entry_key": entry_key,
            "racing_number": racing_number,
            "abbreviation": display_name[:3].upper(),
            "broadcast_name": display_name.upper(),
            "display_name": display_name,
            "team_color": team_color,
            "source": source,
            "record_state": record_state,
        },
    )
    assert value is not None
    return value


def _insert_result(
    connection,
    *,
    session_entry_id: int,
    position: int,
    points: str,
    gap_to_leader_us: int,
) -> None:
    connection.execute(
        text(
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
                :session_entry_id,
                :position,
                CAST(:position AS TEXT),
                :position,
                :points,
                'Finished',
                57,
                :elapsed_time_us,
                :gap_to_leader_us,
                0,
                'fastf1_archive',
                'finalized'
            )
            """
        ),
        {
            "session_entry_id": session_entry_id,
            "position": position,
            "points": points,
            "elapsed_time_us": 5_504_742_000,
            "gap_to_leader_us": gap_to_leader_us,
        },
    )


def _insert_lap(
    connection,
    *,
    session_entry_id: int,
    lap_number: int,
    stint_number: int,
    deleted: bool | None,
) -> None:
    connection.execute(
        text(
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
                deleted_reason,
                fastf1_generated,
                is_accurate,
                source,
                record_state
            )
            VALUES (
                :session_entry_id,
                :lap_number,
                :stint_number,
                :session_time_us,
                :lap_time_us,
                :lap_start_time_us,
                30000000,
                40000000,
                20000000,
                280.1,
                300.2,
                275.3,
                315.4,
                :is_personal_best,
                'MEDIUM',
                :lap_number,
                :fresh_tyre,
                '1',
                1,
                :deleted,
                :deleted_reason,
                FALSE,
                :is_accurate,
                'fastf1_archive',
                'finalized'
            )
            """
        ),
        {
            "session_entry_id": session_entry_id,
            "lap_number": lap_number,
            "stint_number": stint_number,
            "session_time_us": lap_number * 90_000_000,
            "lap_time_us": 90_000_000 + (lap_number * 100_000),
            "lap_start_time_us": (lap_number - 1) * 90_000_000,
            "is_personal_best": lap_number == 3,
            "fresh_tyre": lap_number in {1, 3, 6},
            "deleted": deleted,
            "deleted_reason": (
                "Track limits" if deleted is True else None
            ),
            "is_accurate": deleted is not True,
        },
    )


def test_session_detail_maps_event_snapshot_ingestion_and_counts(
    session_data_target: SessionDataTarget,
) -> None:
    response = read_session_detail(
        session_id=session_data_target.available_session_id,
        session_factory=session_data_target.session_factory,
    )

    assert int(response.id) == session_data_target.available_session_id
    assert response.session_key == "practice-2"
    assert response.event.season_year == session_data_target.season_year
    assert response.event.round_number == 1
    assert response.event.event_name == "Session Data Grand Prix"
    assert response.snapshot.data_available is True
    assert response.snapshot.completed_at == session_data_target.completed_at
    assert response.snapshot.source_updated_at == (
        session_data_target.source_updated_at
    )
    assert response.ingestion is not None
    assert response.ingestion.status.value == "failed"
    assert response.ingestion.last_error is not None
    assert response.ingestion.last_error.model_dump() == {
        "code": "fastf1_load_failed",
        "message": "FastF1 session loading failed.",
    }
    assert response.counts.model_dump() == {
        "entries": 3,
        "results": 2,
        "laps": 6,
    }


def test_unavailable_session_detail_hides_provisional_rows_from_archive_counts(
    session_data_target: SessionDataTarget,
) -> None:
    response = read_session_detail(
        session_id=session_data_target.unavailable_session_id,
        session_factory=session_data_target.session_factory,
    )

    assert response.snapshot.data_available is False
    assert response.snapshot.completed_at is None
    assert response.ingestion is not None
    assert response.ingestion.status.value == "running"
    assert response.counts.model_dump() == {
        "entries": 0,
        "results": 0,
        "laps": 0,
    }


def test_session_results_order_and_preserve_nullable_identity(
    session_data_target: SessionDataTarget,
) -> None:
    response = read_session_results(
        session_id=session_data_target.available_session_id,
        session_factory=session_data_target.session_factory,
    )

    assert tuple(int(item.session_entry_id) for item in response.items) == (
        session_data_target.first_entry_id,
        session_data_target.second_entry_id,
        session_data_target.unresolved_entry_id,
    )
    first, second, unresolved = response.items
    assert first.result is not None
    assert first.result.position == 1
    assert first.result.points == "26.000"
    assert first.team_color_hex == "#3671C6"
    assert first.driver is not None
    assert first.driver.full_name == "First Driver"
    assert second.result is not None
    assert second.result.position == 2
    assert second.result.points == "12.500"
    assert second.team_color_hex == "#FF00AA"
    assert unresolved.driver is None
    assert unresolved.result is None
    assert unresolved.team_color_hex is None


def test_session_laps_use_stable_keyset_pagination(
    session_data_target: SessionDataTarget,
) -> None:
    first_page = read_session_laps(
        session_id=session_data_target.available_session_id,
        session_entry_id=session_data_target.first_entry_id,
        query=LapSummaryQuery(limit=2),
        session_factory=session_data_target.session_factory,
    )
    second_page = read_session_laps(
        session_id=session_data_target.available_session_id,
        session_entry_id=session_data_target.first_entry_id,
        query=LapSummaryQuery(
            after_lap=first_page.page.next_after_lap,
            limit=2,
        ),
        session_factory=session_data_target.session_factory,
    )
    final_page = read_session_laps(
        session_id=session_data_target.available_session_id,
        session_entry_id=session_data_target.first_entry_id,
        query=LapSummaryQuery(
            after_lap=second_page.page.next_after_lap,
            limit=2,
        ),
        session_factory=session_data_target.session_factory,
    )

    assert tuple(lap.lap_number for lap in first_page.items) == (1, 2)
    assert first_page.page.has_more is True
    assert first_page.page.next_after_lap == 2
    assert tuple(lap.lap_number for lap in second_page.items) == (3, 4)
    assert second_page.page.has_more is True
    assert second_page.page.next_after_lap == 4
    assert tuple(lap.lap_number for lap in final_page.items) == (5, 6)
    assert final_page.page.has_more is False
    assert final_page.page.next_after_lap is None


def test_session_laps_apply_bounds_stint_and_deleted_filters(
    session_data_target: SessionDataTarget,
) -> None:
    response = read_session_laps(
        session_id=session_data_target.available_session_id,
        session_entry_id=session_data_target.first_entry_id,
        query=LapSummaryQuery(
            lap_from=2,
            lap_to=5,
            stint_number=2,
            include_deleted=False,
        ),
        session_factory=session_data_target.session_factory,
    )

    assert tuple(lap.lap_number for lap in response.items) == (3, 5)
    assert response.items[0].is_personal_best is True
    assert response.items[1].deleted is None
    assert response.filters.model_dump() == {
        "lap_from": 2,
        "lap_to": 5,
        "stint_number": 2,
        "include_deleted": False,
    }


def test_session_laps_return_a_stable_empty_terminal_page(
    session_data_target: SessionDataTarget,
) -> None:
    response = read_session_laps(
        session_id=session_data_target.available_session_id,
        session_entry_id=session_data_target.first_entry_id,
        query=LapSummaryQuery(after_lap=999, limit=2),
        session_factory=session_data_target.session_factory,
    )

    assert response.items == ()
    assert response.page.limit == 2
    assert response.page.has_more is False
    assert response.page.next_after_lap is None


def test_unavailable_results_and_laps_raise_dedicated_error(
    session_data_target: SessionDataTarget,
) -> None:
    with pytest.raises(
        SessionDataUnavailableError,
        match="not available",
    ):
        read_session_results(
            session_id=session_data_target.unavailable_session_id,
            session_factory=session_data_target.session_factory,
        )

    with pytest.raises(
        SessionDataUnavailableError,
        match="not available",
    ):
        read_session_laps(
            session_id=session_data_target.unavailable_session_id,
            session_entry_id=session_data_target.provisional_entry_id,
            query=LapSummaryQuery(),
            session_factory=session_data_target.session_factory,
        )


def test_unknown_session_and_cross_session_entry_raise_dedicated_errors(
    session_data_target: SessionDataTarget,
) -> None:
    unknown_session_id = 9_007_199_254_740_000

    with pytest.raises(SessionNotFoundError, match=str(unknown_session_id)):
        read_session_detail(
            session_id=unknown_session_id,
            session_factory=session_data_target.session_factory,
        )

    with pytest.raises(
        SessionEntryNotFoundError,
        match=str(session_data_target.other_entry_id),
    ):
        read_session_laps(
            session_id=session_data_target.available_session_id,
            session_entry_id=session_data_target.other_entry_id,
            query=LapSummaryQuery(),
            session_factory=session_data_target.session_factory,
        )


@pytest.mark.parametrize("reader_name", ["detail", "results", "laps"])
def test_session_reads_execute_inside_read_only_transactions(
    session_data_target: SessionDataTarget,
    reader_name: str,
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

    readers: dict[str, Callable[[], object]] = {
        "detail": lambda: read_session_detail(
            session_id=session_data_target.available_session_id,
            session_factory=session_data_target.session_factory,
        ),
        "results": lambda: read_session_results(
            session_id=session_data_target.available_session_id,
            session_factory=session_data_target.session_factory,
        ),
        "laps": lambda: read_session_laps(
            session_id=session_data_target.available_session_id,
            session_entry_id=session_data_target.first_entry_id,
            query=LapSummaryQuery(limit=2),
            session_factory=session_data_target.session_factory,
        ),
    }

    event.listen(
        session_data_target.engine,
        "before_cursor_execute",
        capture_statement,
    )
    try:
        readers[reader_name]()
    finally:
        event.remove(
            session_data_target.engine,
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


@pytest.mark.parametrize(
    ("reader", "identifier_name"),
    [
        (
            lambda factory: read_session_detail(
                session_id=0,
                session_factory=factory,
            ),
            "session_id",
        ),
        (
            lambda factory: read_session_results(
                session_id=True,
                session_factory=factory,
            ),
            "session_id",
        ),
        (
            lambda factory: read_session_laps(
                session_id=1,
                session_entry_id="2",
                query=LapSummaryQuery(),
                session_factory=factory,
            ),
            "session_entry_id",
        ),
    ],
)
def test_invalid_identifiers_are_rejected_before_database_access(
    reader: Callable[[Callable[[], Session]], object],
    identifier_name: str,
) -> None:
    def forbidden_session_factory() -> Session:
        raise AssertionError("database must not be opened")

    with pytest.raises(SessionDataReadError, match=identifier_name):
        reader(forbidden_session_factory)


def test_invalid_lap_query_is_rejected_before_database_access() -> None:
    def forbidden_session_factory() -> Session:
        raise AssertionError("database must not be opened")

    with pytest.raises(SessionDataReadError, match="LapSummaryQuery"):
        read_session_laps(
            session_id=1,
            session_entry_id=2,
            query=None,
            session_factory=forbidden_session_factory,
        )
