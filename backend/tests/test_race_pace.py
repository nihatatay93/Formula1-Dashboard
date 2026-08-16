"""Race pace reads: what counts as a representative lap, and what does not.

The session seeded here is deliberately awkward. Every way a lap can fail to
represent pace appears at least once -- an out lap, an in lap, a yellow-flag
lap, a deleted lap, one FastF1 marked inaccurate, and one with no time at all
-- because the value of this endpoint is entirely in which laps it excludes.
"""

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.contracts import RacePaceQuery
from app.api.session_data import (
    CLEAN_LAP,
    SessionDataUnavailableError,
    SessionNotFoundError,
    is_clean_lap,
    read_race_pace,
)
from app.db.engine import sqlalchemy_database_url
from app.db.models import Lap, SessionEntry

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

# The best clean lap of the seeded session, and the reference every cutoff is a
# percentage of.
BEST_US = 90_000_000


@dataclass(frozen=True, slots=True)
class RacePaceTarget:
    engine: Engine
    session_factory: sessionmaker[Session]
    season_year: int
    session_id: int
    unavailable_session_id: int
    leader_entry_id: int
    trailing_entry_id: int
    lapless_entry_id: int


@pytest.fixture(scope="module")
def target() -> Iterator[RacePaceTarget]:
    if TEST_DATABASE_URL is None:
        pytest.skip("TEST_DATABASE_URL is required for race-pace tests")

    engine = create_engine(sqlalchemy_database_url(TEST_DATABASE_URL))
    suffix = uuid.uuid4().hex

    with engine.begin() as connection:
        season_year = connection.scalar(
            text(
                """
                SELECT candidate
                FROM generate_series(32000, 32999) AS candidate
                WHERE NOT EXISTS (
                    SELECT 1 FROM seasons WHERE year = candidate
                )
                ORDER BY candidate
                LIMIT 1
                """
            )
        )
        assert season_year is not None
        now = connection.scalar(text("SELECT now()"))
        assert now is not None

        connection.execute(
            text("INSERT INTO seasons (year) VALUES (:year)"),
            {"year": season_year},
        )
        event_id = connection.scalar(
            text(
                """
                INSERT INTO events (
                    season_year, round_number, official_name, event_name,
                    country, location, event_format, starts_at, ends_at,
                    last_discovered_at, source
                )
                VALUES (
                    :season_year, 1, 'FORMULA 1 RACE PACE GRAND PRIX',
                    'Race Pace Grand Prix', 'Test Country', 'Test Circuit',
                    'conventional', :starts_at, :ends_at, :now,
                    'fastf1_archive'
                )
                RETURNING id
                """
            ),
            {
                "season_year": season_year,
                "starts_at": now - timedelta(days=3),
                "ends_at": now - timedelta(days=1),
                "now": now,
            },
        )
        assert event_id is not None

        session_id = _insert_session(
            connection, event_id=event_id, key="race", name="Race", now=now
        )
        unavailable_session_id = _insert_session(
            connection,
            event_id=event_id,
            key="qualifying",
            name="Qualifying",
            now=now,
        )
        _insert_ingestion(connection, session_id=session_id, completed=True, now=now)
        _insert_ingestion(
            connection,
            session_id=unavailable_session_id,
            completed=False,
            now=now,
        )

        leader_driver = _insert_driver(
            connection, key=f"leader-{suffix}", name="Ada Leader"
        )
        trailing_driver = _insert_driver(
            connection, key=f"trailing-{suffix}", name="Bo Trailing"
        )
        lapless_driver = _insert_driver(
            connection, key=f"lapless-{suffix}", name="Cy Lapless"
        )

        leader_entry_id = _insert_entry(
            connection,
            session_id=session_id,
            driver_id=leader_driver,
            key=f"leader-{suffix}",
            number="1",
            name="Ada Leader",
            colour="27F4D2",
        )
        trailing_entry_id = _insert_entry(
            connection,
            session_id=session_id,
            driver_id=trailing_driver,
            key=f"trailing-{suffix}",
            number="2",
            name="Bo Trailing",
            colour="not-a-colour",
        )
        lapless_entry_id = _insert_entry(
            connection,
            session_id=session_id,
            driver_id=lapless_driver,
            key=f"lapless-{suffix}",
            number="3",
            name="Cy Lapless",
            colour=None,
        )

        # Finishing order is the reverse of insertion order, so a test that
        # asserts ordering cannot pass by accident.
        _insert_result(connection, entry_id=leader_entry_id, position=1)
        _insert_result(connection, entry_id=trailing_entry_id, position=2)

        # The leader: one clean stint bracketed by pit laps, interrupted by a
        # yellow, and ending on a lap far enough off to fall outside 107%.
        _insert_lap(
            connection,
            entry_id=leader_entry_id,
            lap_number=1,
            lap_time_us=99_000_000,
            pit_out=True,
        )
        _insert_lap(
            connection, entry_id=leader_entry_id, lap_number=2, lap_time_us=BEST_US
        )
        _insert_lap(
            connection,
            entry_id=leader_entry_id,
            lap_number=3,
            lap_time_us=90_900_000,
        )
        _insert_lap(
            connection,
            entry_id=leader_entry_id,
            lap_number=4,
            lap_time_us=91_000_000,
            # Green, then yellow. FastF1 concatenates every status of the lap.
            track_status="12",
        )
        _insert_lap(
            connection,
            entry_id=leader_entry_id,
            lap_number=5,
            lap_time_us=105_000_000,
            pit_in=True,
        )
        # 8.1% off the best: outside a 107% cutoff, inside a 110% one.
        _insert_lap(
            connection,
            entry_id=leader_entry_id,
            lap_number=6,
            lap_time_us=97_300_000,
        )

        # The trailing car exists to carry the remaining ways a lap is unusable.
        _insert_lap(
            connection,
            entry_id=trailing_entry_id,
            lap_number=1,
            lap_time_us=92_000_000,
            deleted=True,
        )
        _insert_lap(
            connection,
            entry_id=trailing_entry_id,
            lap_number=2,
            lap_time_us=93_000_000,
            is_accurate=False,
        )
        _insert_lap(
            connection,
            entry_id=trailing_entry_id,
            lap_number=3,
            lap_time_us=None,
        )
        # A faster time than the leader's best, but behind a safety car, so it
        # must not become the session reference.
        _insert_lap(
            connection,
            entry_id=trailing_entry_id,
            lap_number=4,
            lap_time_us=80_000_000,
            track_status="4",
        )

    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield RacePaceTarget(
        engine=engine,
        session_factory=session_factory,
        season_year=season_year,
        session_id=session_id,
        unavailable_session_id=unavailable_session_id,
        leader_entry_id=leader_entry_id,
        trailing_entry_id=trailing_entry_id,
        lapless_entry_id=lapless_entry_id,
    )

    _purge(engine, season_year=season_year, suffix=suffix)
    engine.dispose()


def _read(target: RacePaceTarget, **kwargs: object) -> object:
    return read_race_pace(
        session_id=target.session_id,
        query=RacePaceQuery(**kwargs),  # type: ignore[arg-type]
        session_factory=target.session_factory,
    )


def test_every_entry_arrives_in_one_response(target: RacePaceTarget) -> None:
    response = _read(target)

    # Twenty drivers must not become twenty paginated walks.
    assert len(response.items) == 3
    assert [item.display_name for item in response.items] == [
        "Ada Leader",
        "Bo Trailing",
        "Cy Lapless",
    ]


def test_an_entry_that_never_ran_still_appears(target: RacePaceTarget) -> None:
    response = _read(target)
    lapless = response.items[-1]

    # Absent from the chart, present in the field list: a driver who did not
    # run is a fact about the session, not a gap in the response.
    assert lapless.display_name == "Cy Lapless"
    assert lapless.laps == ()


def test_pit_laps_are_not_clean(target: RacePaceTarget) -> None:
    laps = _leader_laps(_read(target))

    assert laps[1].is_clean is False, "an out lap is not representative"
    assert laps[5].is_clean is False, "an in lap is not representative"


def test_a_yellow_flag_lap_is_not_clean(target: RacePaceTarget) -> None:
    laps = _leader_laps(_read(target))

    # Track status "12" contains a "1" but was not green throughout.
    assert laps[4].is_clean is False


def test_deleted_inaccurate_and_untimed_laps_are_not_clean(
    target: RacePaceTarget,
) -> None:
    response = _read(target)
    trailing = response.items[1]
    by_number = {lap.lap_number: lap for lap in trailing.laps}

    assert by_number[1].is_clean is False, "deleted"
    assert by_number[2].is_clean is False, "inaccurate"
    assert by_number[3].is_clean is False, "no recorded time"


def test_the_session_best_comes_from_clean_laps_only(
    target: RacePaceTarget,
) -> None:
    response = _read(target)

    # The trailing car's 80s lap was set behind a safety car. Letting it define
    # the reference would drag every cutoff with it.
    assert response.session_best_lap_time_us == BEST_US


def test_the_cutoff_is_a_percentage_of_the_session_best(
    target: RacePaceTarget,
) -> None:
    response = _read(target)

    assert response.outlier_cutoff_lap_time_us == int(BEST_US * 1.07)

    laps = _leader_laps(response)
    assert laps[3].beyond_cutoff is False, "1% off the best is normal pace"
    assert laps[6].beyond_cutoff is True, "8% off the best is not"


def test_a_wider_cutoff_admits_the_slow_lap(target: RacePaceTarget) -> None:
    laps = _leader_laps(_read(target, outlier_cutoff=110.0))

    assert laps[6].beyond_cutoff is False


def test_outliers_are_flagged_rather_than_removed(target: RacePaceTarget) -> None:
    response = _read(target)

    # A lap far off the reference is the interesting thing on an evolution
    # chart. The caller decides whether to draw it.
    assert any(lap.beyond_cutoff for lap in response.items[0].laps)
    assert len(response.items[0].laps) == 6


def test_clean_only_narrows_the_payload(target: RacePaceTarget) -> None:
    full = _read(target)
    clean = _read(target, clean_only=True)

    assert [lap.lap_number for lap in _leader_laps(full).values()] == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]
    assert [lap.lap_number for lap in clean.items[0].laps] == [2, 3, 6]
    # An entry whose every lap was filtered away keeps its place in the field.
    assert len(clean.items) == 3
    assert clean.items[1].laps == ()


def test_the_clean_definition_travels_with_the_response(
    target: RacePaceTarget,
) -> None:
    response = _read(target)

    assert "track was green" in response.clean_lap_definition


def test_the_sql_and_python_clean_rules_agree(target: RacePaceTarget) -> None:
    """The one test that keeps the two halves of the definition together.

    ``CLEAN_LAP`` filters in SQL and ``is_clean_lap`` flags in Python. They are
    written twice because one runs in the database and one over loaded rows;
    if they ever disagree, a chart would draw a different set of laps than a
    ``clean_only`` export of the same session.
    """

    with target.session_factory() as database:
        laps = list(
            database.scalars(
                select(Lap)
                .join(SessionEntry, SessionEntry.id == Lap.session_entry_id)
                .where(SessionEntry.session_id == target.session_id)
            )
        )
        clean_by_sql = set(
            database.scalars(
                select(Lap.id)
                .join(SessionEntry, SessionEntry.id == Lap.session_entry_id)
                .where(SessionEntry.session_id == target.session_id, CLEAN_LAP)
            )
        )

    clean_by_python = {lap.id for lap in laps if is_clean_lap(lap)}

    assert clean_by_sql == clean_by_python
    assert clean_by_sql, "the fixture must contain at least one clean lap"


def test_team_colour_is_validated_not_echoed(target: RacePaceTarget) -> None:
    response = _read(target)

    assert response.items[0].team_color_hex == "#27F4D2"
    # A stored value that is not a colour must not reach a style attribute.
    assert response.items[1].team_color_hex is None


def test_an_unknown_session_is_reported(target: RacePaceTarget) -> None:
    with pytest.raises(SessionNotFoundError):
        read_race_pace(
            session_id=2_147_483_000,
            query=RacePaceQuery(),
            session_factory=target.session_factory,
        )


def test_a_session_without_a_snapshot_is_reported(
    target: RacePaceTarget,
) -> None:
    with pytest.raises(SessionDataUnavailableError):
        read_race_pace(
            session_id=target.unavailable_session_id,
            query=RacePaceQuery(),
            session_factory=target.session_factory,
        )


def _leader_laps(response: object) -> dict[int, object]:
    return {lap.lap_number: lap for lap in response.items[0].laps}  # type: ignore[attr-defined]


def _insert_session(
    connection, *, event_id: int, key: str, name: str, now: datetime
) -> int:
    value = connection.scalar(
        text(
            """
            INSERT INTO sessions (
                event_id, session_key, session_name, scheduled_start_at,
                scheduled_end_at, last_discovered_at, source
            )
            VALUES (
                :event_id, :key, :name, :starts_at, :ends_at, :now,
                'fastf1_archive'
            )
            RETURNING id
            """
        ),
        {
            "event_id": event_id,
            "key": key,
            "name": name,
            "starts_at": now - timedelta(days=2),
            "ends_at": now - timedelta(days=2) + timedelta(hours=2),
            "now": now,
        },
    )
    assert value is not None
    return value


def _insert_ingestion(
    connection, *, session_id: int, completed: bool, now: datetime
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO session_ingestions (
                session_id, status, source, record_state, attempt_count,
                completed_at, source_updated_at
            )
            VALUES (
                :session_id, :status, 'fastf1_archive', 'finalized', 1,
                :completed_at, :source_updated_at
            )
            """
        ),
        {
            "session_id": session_id,
            "status": "completed" if completed else "pending",
            "completed_at": now - timedelta(days=1) if completed else None,
            "source_updated_at": now - timedelta(days=1) if completed else None,
        },
    )


def _insert_driver(connection, *, key: str, name: str) -> int:
    value = connection.scalar(
        text(
            """
            INSERT INTO drivers (
                jolpica_driver_id, given_name, family_name, full_name,
                country_code
            )
            VALUES (:key, :given_name, 'Driver', :name, 'GBR')
            RETURNING id
            """
        ),
        {"key": key, "given_name": name.split()[0], "name": name},
    )
    assert value is not None
    return value


def _insert_entry(
    connection,
    *,
    session_id: int,
    driver_id: int,
    key: str,
    number: str,
    name: str,
    colour: str | None,
) -> int:
    value = connection.scalar(
        text(
            """
            INSERT INTO session_entries (
                session_id, driver_id, entry_key, racing_number, abbreviation,
                broadcast_name, display_name, team_jolpica_id, team_name,
                team_color, source, record_state
            )
            VALUES (
                :session_id, :driver_id, :key, :number, :abbreviation,
                :broadcast_name, :name, 'example_team', 'Example Team',
                :colour, 'fastf1_archive', 'finalized'
            )
            RETURNING id
            """
        ),
        {
            "session_id": session_id,
            "driver_id": driver_id,
            "key": key,
            "number": number,
            "abbreviation": name[:3].upper(),
            "broadcast_name": name.upper(),
            "name": name,
            "colour": colour,
        },
    )
    assert value is not None
    return value


def _insert_result(connection, *, entry_id: int, position: int) -> None:
    connection.execute(
        text(
            """
            INSERT INTO session_results (
                session_entry_id, position, classified_position, points,
                source, record_state
            )
            VALUES (
                :entry_id, :position, :classified_position, 0,
                'fastf1_archive', 'finalized'
            )
            """
        ),
        {
            "entry_id": entry_id,
            "position": position,
            "classified_position": str(position),
        },
    )


def _insert_lap(
    connection,
    *,
    entry_id: int,
    lap_number: int,
    lap_time_us: int | None,
    track_status: str = "1",
    deleted: bool = False,
    is_accurate: bool = True,
    pit_in: bool = False,
    pit_out: bool = False,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO laps (
                session_entry_id, lap_number, stint_number, session_time_us,
                lap_time_us, lap_start_time_us, pit_in_time_us,
                pit_out_time_us, is_personal_best, compound, tyre_life_laps,
                fresh_tyre, track_status, position, deleted, deleted_reason,
                fastf1_generated, is_accurate, source, record_state
            )
            VALUES (
                :entry_id, :lap_number, :stint_number, :session_time_us,
                :lap_time_us, :lap_start_time_us, :pit_in_time_us,
                :pit_out_time_us, FALSE, 'MEDIUM', :lap_number, FALSE,
                :track_status, 1, :deleted, :deleted_reason, FALSE,
                :is_accurate, 'fastf1_archive', 'finalized'
            )
            """
        ),
        {
            "entry_id": entry_id,
            "lap_number": lap_number,
            # A pit stop ends a stint, so the laps after one belong to the next.
            "stint_number": 2 if lap_number > 5 else 1,
            "session_time_us": lap_number * 90_000_000,
            "lap_time_us": lap_time_us,
            "lap_start_time_us": (lap_number - 1) * 90_000_000,
            "pit_in_time_us": 95_000_000 if pit_in else None,
            "pit_out_time_us": 5_000_000 if pit_out else None,
            "track_status": track_status,
            "deleted": deleted,
            "deleted_reason": "Track limits" if deleted else None,
            "is_accurate": is_accurate,
        },
    )


def _purge(engine: Engine, *, season_year: int, suffix: str) -> None:
    with engine.begin() as connection:
        for statement in (
            """
            DELETE FROM laps WHERE session_entry_id IN (
                SELECT se.id FROM session_entries se
                JOIN sessions s ON s.id = se.session_id
                JOIN events e ON e.id = s.event_id
                WHERE e.season_year = :season_year
            )
            """,
            """
            DELETE FROM session_results WHERE session_entry_id IN (
                SELECT se.id FROM session_entries se
                JOIN sessions s ON s.id = se.session_id
                JOIN events e ON e.id = s.event_id
                WHERE e.season_year = :season_year
            )
            """,
            """
            DELETE FROM session_entries WHERE session_id IN (
                SELECT s.id FROM sessions s
                JOIN events e ON e.id = s.event_id
                WHERE e.season_year = :season_year
            )
            """,
            """
            DELETE FROM session_ingestions WHERE session_id IN (
                SELECT s.id FROM sessions s
                JOIN events e ON e.id = s.event_id
                WHERE e.season_year = :season_year
            )
            """,
            """
            DELETE FROM sessions WHERE event_id IN (
                SELECT id FROM events WHERE season_year = :season_year
            )
            """,
            "DELETE FROM events WHERE season_year = :season_year",
            "DELETE FROM seasons WHERE year = :season_year",
        ):
            connection.execute(text(statement), {"season_year": season_year})
        connection.execute(
            text(
                "DELETE FROM drivers WHERE jolpica_driver_id LIKE :pattern"
            ),
            {"pattern": f"%-{suffix}"},
        )
