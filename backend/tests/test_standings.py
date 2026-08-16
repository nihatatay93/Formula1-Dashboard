"""Championship standings aggregated from stored results.

Database-backed, because the whole of this feature is one SQL aggregation and a
mocked session would prove nothing about it.

The seeded season is small but deliberately awkward: a driver who changes team
mid-season, a sprint that pays fewer points, a retirement that is still
classified, one that is not, a session whose ingestion never completed, and a
tie that has to be broken.
"""

import os
from collections.abc import Iterator
from decimal import Decimal

import psycopg
import pytest
from psycopg import Connection
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.standings import (
    read_constructor_standings,
    read_driver_standings,
)
from app.db.engine import sqlalchemy_database_url

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="TEST_DATABASE_URL is required for standings tests",
)

SEASON = 31990


@pytest.fixture
def connection() -> Iterator[Connection[tuple]]:
    assert TEST_DATABASE_URL is not None
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as database:
        _purge(database)
        try:
            yield database
        finally:
            _purge(database)


def _purge(database: Connection[tuple]) -> None:
    # Committed rather than rolled back, because the read services open their
    # own connections and would not see an uncommitted transaction. Deleted
    # bottom-up: these tables are related by foreign keys without cascade.
    sessions = (
        "SELECT s.id FROM sessions s JOIN events e ON e.id = s.event_id"
        f" WHERE e.season_year = {SEASON}"
    )
    entries = f"SELECT id FROM session_entries WHERE session_id IN ({sessions})"
    for statement in (
        f"DELETE FROM session_results WHERE session_entry_id IN ({entries})",
        f"DELETE FROM session_entries WHERE session_id IN ({sessions})",
        f"DELETE FROM session_ingestions WHERE session_id IN ({sessions})",
        f"DELETE FROM sessions WHERE id IN ({sessions})",
        f"DELETE FROM events WHERE season_year = {SEASON}",
        f"DELETE FROM seasons WHERE year = {SEASON}",
        "DELETE FROM drivers WHERE full_name LIKE 'Standings Test%'",
    ):
        database.execute(statement)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker]:
    assert TEST_DATABASE_URL is not None
    engine = create_engine(sqlalchemy_database_url(TEST_DATABASE_URL))
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()


def _driver(database: Connection[tuple], name: str) -> int:
    return database.execute(
        "INSERT INTO drivers (full_name) VALUES (%s) RETURNING id",
        (f"Standings Test {name}",),
    ).fetchone()[0]


def _session(
    database: Connection[tuple],
    *,
    round_number: int,
    session_key: str,
    completed: bool = True,
) -> int:
    database.execute(
        "INSERT INTO seasons (year) VALUES (%s) ON CONFLICT DO NOTHING",
        (SEASON,),
    )
    event_id = database.execute(
        "INSERT INTO events (season_year, round_number, event_name, source)"
        " VALUES (%s, %s, %s, 'fastf1_archive') ON CONFLICT DO NOTHING RETURNING id",
        (SEASON, round_number, f"Round {round_number}"),
    ).fetchone()
    if event_id is None:
        event_id = database.execute(
            "SELECT id FROM events WHERE season_year = %s AND round_number = %s",
            (SEASON, round_number),
        ).fetchone()
    session_id = database.execute(
        "INSERT INTO sessions (event_id, session_key, session_name, source)"
        " VALUES (%s, %s, %s, 'fastf1_archive') RETURNING id",
        (event_id[0], session_key, session_key.title()),
    ).fetchone()[0]
    database.execute(
        "INSERT INTO session_ingestions"
        " (session_id, status, source, record_state, completed_at)"
        " VALUES (%s, %s, 'fastf1_archive', 'finalized',"
        "         CASE WHEN %s THEN now() ELSE NULL END)",
        (session_id, "completed" if completed else "running", completed),
    )
    return session_id


def _result(
    database: Connection[tuple],
    *,
    session_id: int,
    driver_id: int,
    team: str,
    colour: str,
    position: int | None,
    points: str,
    classified: str | None,
    status: str = "Finished",
) -> None:
    entry_id = database.execute(
        "INSERT INTO session_entries"
        " (session_id, driver_id, entry_key, display_name, abbreviation,"
        "  team_name, team_color, source, record_state)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, 'fastf1_archive', 'finalized')"
        " RETURNING id",
        (
            session_id,
            driver_id,
            f"{session_id}-{driver_id}",
            f"Driver {driver_id}",
            f"D{driver_id % 100:02d}",
            team,
            colour,
        ),
    ).fetchone()[0]
    database.execute(
        "INSERT INTO session_results"
        " (session_entry_id, position, classified_position, points, status,"
        "  source, record_state)"
        " VALUES (%s, %s, %s, %s, %s, 'fastf1_archive', 'finalized')",
        (entry_id, position, classified, Decimal(points), status),
    )


@pytest.fixture
def seeded(connection: Connection[tuple]) -> dict[str, int]:
    """A three-round season with the awkward cases baked in."""
    alice = _driver(connection, "Alice")
    bob = _driver(connection, "Bob")
    carol = _driver(connection, "Carol")

    r1 = _session(connection, round_number=1, session_key="race")
    _result(connection, session_id=r1, driver_id=alice, team="Alpha", colour="FF0000",
            position=1, points="25", classified="1")
    _result(connection, session_id=r1, driver_id=bob, team="Beta", colour="0000FF",
            position=2, points="18", classified="2")
    # Retired but still classified: past ninety per cent of the distance.
    _result(connection, session_id=r1, driver_id=carol, team="Beta", colour="0000FF",
            position=3, points="15", classified="3", status="Retired")

    q1 = _session(connection, round_number=1, session_key="qualifying")
    _result(connection, session_id=q1, driver_id=bob, team="Beta", colour="0000FF",
            position=1, points="0", classified="1")
    _result(connection, session_id=q1, driver_id=alice, team="Alpha", colour="FF0000",
            position=2, points="0", classified="2")

    s2 = _session(connection, round_number=2, session_key="sprint")
    _result(connection, session_id=s2, driver_id=bob, team="Beta", colour="0000FF",
            position=1, points="8", classified="1")

    r2 = _session(connection, round_number=2, session_key="race")
    _result(connection, session_id=r2, driver_id=bob, team="Beta", colour="0000FF",
            position=1, points="25", classified="1")
    # Did not finish, and not classified.
    _result(connection, session_id=r2, driver_id=alice, team="Alpha", colour="FF0000",
            position=20, points="0", classified=None, status="Collision")

    # Round three was never ingested, so nothing from it may count.
    r3 = _session(connection, round_number=3, session_key="race", completed=False)
    _result(connection, session_id=r3, driver_id=alice, team="Alpha", colour="FF0000",
            position=1, points="25", classified="1")

    return {"alice": alice, "bob": bob, "carol": carol}


class TestDriverStandings:
    def test_points_come_from_stored_results(self, seeded, session_factory) -> None:
        standing = read_driver_standings(
            season_year=SEASON, session_factory=session_factory
        )
        by_name = {item.driver_id: item for item in standing.items}

        # Bob: 18 + 8 sprint + 25 = 51. Alice: 25 only, because round three
        # was never ingested.
        assert by_name[str(seeded["bob"])].points == Decimal("51")
        assert by_name[str(seeded["alice"])].points == Decimal("25")

    def test_an_uningested_round_is_absent_not_zero(
        self, seeded, session_factory
    ) -> None:
        standing = read_driver_standings(
            season_year=SEASON, session_factory=session_factory
        )

        # Alice would lead on 50 if the incomplete round counted.
        assert standing.items[0].driver_id == str(seeded["bob"])
        assert {round_.round_number for round_ in standing.rounds} == {1, 2}

    def test_ordering_is_by_points(self, seeded, session_factory) -> None:
        standing = read_driver_standings(
            season_year=SEASON, session_factory=session_factory
        )

        assert [item.position for item in standing.items] == [1, 2, 3]
        assert [item.points for item in standing.items] == [
            Decimal("51"),
            Decimal("25"),
            Decimal("15"),
        ]

    def test_wins_and_podiums_count_races_only(
        self, seeded, session_factory
    ) -> None:
        standing = read_driver_standings(
            season_year=SEASON, session_factory=session_factory
        )
        bob = next(i for i in standing.items if i.driver_id == str(seeded["bob"]))

        # One race win. The sprint victory is not a grand prix win.
        assert bob.wins == 1
        assert bob.podiums == 2

    def test_poles_come_from_qualifying_not_the_grid(
        self, seeded, session_factory
    ) -> None:
        # Pole is first in qualifying; a grid position is what survives
        # penalties and is a different thing.
        standing = read_driver_standings(
            season_year=SEASON, session_factory=session_factory
        )
        bob = next(i for i in standing.items if i.driver_id == str(seeded["bob"]))
        alice = next(i for i in standing.items if i.driver_id == str(seeded["alice"]))

        assert bob.poles == 1
        assert alice.poles == 0

    def test_a_classified_retirement_is_not_a_dnf(
        self, seeded, session_factory
    ) -> None:
        """The ninety-per-cent rule, read from the data rather than the status.

        Carol retired and was still classified third; Alice was not classified
        at all. Only the second is a DNF.
        """
        standing = read_driver_standings(
            season_year=SEASON, session_factory=session_factory
        )
        carol = next(i for i in standing.items if i.driver_id == str(seeded["carol"]))
        alice = next(i for i in standing.items if i.driver_id == str(seeded["alice"]))

        assert carol.dnfs == 0
        assert alice.dnfs == 1

    def test_best_finish_ignores_unclassified_positions(
        self, seeded, session_factory
    ) -> None:
        standing = read_driver_standings(
            season_year=SEASON, session_factory=session_factory
        )
        alice = next(i for i in standing.items if i.driver_id == str(seeded["alice"]))

        assert alice.best_finish == 1

    def test_per_round_points_are_returned_for_charting(
        self, seeded, session_factory
    ) -> None:
        standing = read_driver_standings(
            season_year=SEASON, session_factory=session_factory
        )
        bob = next(i for i in standing.items if i.driver_id == str(seeded["bob"]))

        assert [(r.round_number, r.session_key, r.points) for r in bob.rounds] == [
            (1, "race", Decimal("18")),
            (2, "sprint", Decimal("8")),
            (2, "race", Decimal("25")),
        ]

    def test_an_empty_season_is_an_empty_table_not_an_error(
        self, session_factory
    ) -> None:
        standing = read_driver_standings(
            season_year=SEASON + 1, session_factory=session_factory
        )

        assert standing.items == ()
        assert standing.scoring_sessions == 0


class TestConstructorStandings:
    def test_team_points_are_the_sum_of_their_drivers(
        self, seeded, session_factory
    ) -> None:
        standing = read_constructor_standings(
            season_year=SEASON, session_factory=session_factory
        )
        by_team = {item.team_name: item for item in standing.items}

        # Beta: Bob 51 + Carol 15.
        assert by_team["Beta"].points == Decimal("66")
        assert by_team["Alpha"].points == Decimal("25")

    def test_teams_are_ordered_by_points(self, seeded, session_factory) -> None:
        standing = read_constructor_standings(
            season_year=SEASON, session_factory=session_factory
        )

        assert [item.team_name for item in standing.items] == ["Beta", "Alpha"]
        assert [item.position for item in standing.items] == [1, 2]

    def test_a_team_lists_its_drivers(self, seeded, session_factory) -> None:
        standing = read_constructor_standings(
            season_year=SEASON, session_factory=session_factory
        )
        beta = next(i for i in standing.items if i.team_name == "Beta")

        assert len(beta.drivers) == 2

    def test_an_empty_season_is_an_empty_table(self, session_factory) -> None:
        standing = read_constructor_standings(
            season_year=SEASON + 1, session_factory=session_factory
        )

        assert standing.items == ()
