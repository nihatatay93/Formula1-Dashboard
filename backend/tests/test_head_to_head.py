"""Two drivers against each other, and the spread of a driver's race pace.

The fixture is built so that every exclusion rule has a case: a race one driver
did not start, a race one retired from, a qualifying session where a driver set
no time, and a pair who never shared a session at all. Those are the situations
where a naive comparison quietly reports a result that never happened.
"""

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.head_to_head import (
    DriverNotFoundError,
    HeadToHeadReadError,
    read_consistency,
    read_head_to_head,
)
from app.db.engine import sqlalchemy_database_url

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

# Session best of the first race, and the reference its percentages use.
BEST_US = 90_000_000


@dataclass(frozen=True, slots=True)
class Target:
    session_factory: sessionmaker[Session]
    season_year: int
    alice: int
    bruno: int
    chen: int
    dara: int


@pytest.fixture(scope="module")
def target() -> Iterator[Target]:
    if TEST_DATABASE_URL is None:
        pytest.skip("TEST_DATABASE_URL is required for head-to-head tests")

    engine = create_engine(sqlalchemy_database_url(TEST_DATABASE_URL))
    suffix = uuid.uuid4().hex

    with engine.begin() as connection:
        # `seasons.year` is a smallint; every band above is reserved by a
        # sibling module, so this one sits below them.
        season_year = connection.scalar(
            text(
                """
                SELECT candidate
                FROM generate_series(24000, 24999) AS candidate
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

        alice = _driver(connection, key=f"alice-{suffix}", name="Alice Adams")
        bruno = _driver(connection, key=f"bruno-{suffix}", name="Bruno Bello")
        chen = _driver(connection, key=f"chen-{suffix}", name="Chen Chu")
        dara = _driver(connection, key=f"dara-{suffix}", name="Dara Doyle")

        rounds = {}
        for number in (1, 2, 3):
            event_id = connection.scalar(
                text(
                    """
                    INSERT INTO events (
                        season_year, round_number, official_name, event_name,
                        country, location, event_format, starts_at, ends_at,
                        last_discovered_at, source
                    )
                    VALUES (
                        :year, :number, :official, :name, 'Testland',
                        'Test Circuit', 'conventional', :starts_at, :ends_at,
                        :now, 'fastf1_archive'
                    )
                    RETURNING id
                    """
                ),
                {
                    "year": season_year,
                    "number": number,
                    "official": f"ROUND {number} GRAND PRIX",
                    "name": f"Round {number} Grand Prix",
                    "starts_at": now - timedelta(days=30 - number),
                    "ends_at": now - timedelta(days=29 - number),
                    "now": now,
                },
            )
            rounds[number] = {
                "qualifying": _session(
                    connection, event_id=event_id, key="qualifying", now=now
                ),
                "race": _session(
                    connection, event_id=event_id, key="race", now=now
                ),
            }

        # --- Round 1: both drivers complete both sessions ------------------
        _entry_result(
            connection,
            session_id=rounds[1]["qualifying"],
            driver_id=alice,
            suffix=suffix,
            number="1",
            name="Alice Adams",
            position=1,
            classified="1",
        )
        _entry_result(
            connection,
            session_id=rounds[1]["qualifying"],
            driver_id=bruno,
            suffix=suffix,
            number="2",
            name="Bruno Bello",
            position=2,
            classified="2",
        )
        alice_race_1 = _entry_result(
            connection,
            session_id=rounds[1]["race"],
            driver_id=alice,
            suffix=suffix,
            number="1",
            name="Alice Adams",
            position=1,
            classified="1",
            points="25.000",
        )
        _entry_result(
            connection,
            session_id=rounds[1]["race"],
            driver_id=bruno,
            suffix=suffix,
            number="2",
            name="Bruno Bello",
            position=2,
            classified="2",
            points="18.000",
        )

        # Alice's clean laps are 100% and 110% of the session best, which she
        # set herself. Every consistency figure below is hand-computable from
        # exactly these two numbers.
        _lap(connection, entry_id=alice_race_1, lap_number=1, lap_time_us=BEST_US)
        _lap(
            connection,
            entry_id=alice_race_1,
            lap_number=2,
            lap_time_us=int(BEST_US * 1.1),
        )
        # Neither of these may reach the statistics: one is an in lap, the
        # other ran under yellow flags.
        _lap(
            connection,
            entry_id=alice_race_1,
            lap_number=3,
            lap_time_us=int(BEST_US * 1.5),
            pit_in=True,
        )
        _lap(
            connection,
            entry_id=alice_race_1,
            lap_number=4,
            lap_time_us=int(BEST_US * 1.4),
            track_status="12",
        )

        # --- Round 2: Alice does not start the race ------------------------
        _entry_result(
            connection,
            session_id=rounds[2]["qualifying"],
            driver_id=alice,
            suffix=suffix,
            number="1",
            name="Alice Adams",
            position=2,
            classified="2",
        )
        _entry_result(
            connection,
            session_id=rounds[2]["qualifying"],
            driver_id=bruno,
            suffix=suffix,
            number="2",
            name="Bruno Bello",
            position=1,
            classified="1",
        )
        # A non-start still carries a position -- it orders the cars, it does
        # not rank them -- so a raw comparison would score this as a loss.
        _entry_result(
            connection,
            session_id=rounds[2]["race"],
            driver_id=alice,
            suffix=suffix,
            number="1",
            name="Alice Adams",
            position=20,
            classified="W",
            status="Did not start",
        )
        _entry_result(
            connection,
            session_id=rounds[2]["race"],
            driver_id=bruno,
            suffix=suffix,
            number="2",
            name="Bruno Bello",
            position=1,
            classified="1",
            points="25.000",
        )
        # Chen shares round 2 only, and set no qualifying time.
        _entry_result(
            connection,
            session_id=rounds[2]["qualifying"],
            driver_id=chen,
            suffix=suffix,
            number="3",
            name="Chen Chu",
            position=None,
            classified=None,
        )
        _entry_result(
            connection,
            session_id=rounds[2]["race"],
            driver_id=chen,
            suffix=suffix,
            number="3",
            name="Chen Chu",
            position=2,
            classified="2",
            points="18.000",
        )

        # --- Round 3: Dara alone, so she shares nothing with Alice ---------
        _entry_result(
            connection,
            session_id=rounds[3]["qualifying"],
            driver_id=dara,
            suffix=suffix,
            number="4",
            name="Dara Doyle",
            position=1,
            classified="1",
        )
        _entry_result(
            connection,
            session_id=rounds[3]["race"],
            driver_id=dara,
            suffix=suffix,
            number="4",
            name="Dara Doyle",
            position=1,
            classified="1",
            points="25.000",
        )

    yield Target(
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
        season_year=season_year,
        alice=alice,
        bruno=bruno,
        chen=chen,
        dara=dara,
    )

    _purge(engine, season_year=season_year, suffix=suffix)
    engine.dispose()


def _read(target: Target, a: int, b: int):
    return read_head_to_head(
        season_year=target.season_year,
        driver_a=a,
        driver_b=b,
        session_factory=target.session_factory,
    )


def test_qualifying_record_counts_every_shared_session(target: Target) -> None:
    record = _read(target, target.alice, target.bruno).qualifying

    # Round 1 to Alice, round 2 to Bruno.
    assert (record.a_ahead, record.b_ahead) == (1, 1)
    assert record.compared == 2
    assert record.excluded == 0


def test_a_race_one_driver_did_not_start_is_excluded(target: Target) -> None:
    record = _read(target, target.alice, target.bruno).race

    # Only round 1 can be compared. Round 2 is excluded rather than scored
    # against Alice, who never took the start.
    assert (record.a_ahead, record.b_ahead) == (1, 0)
    assert record.compared == 1
    assert record.excluded == 1


def test_the_exclusion_is_explained_in_the_response(target: Target) -> None:
    comparison = _read(target, target.alice, target.bruno)

    assert "classified" in comparison.race.basis
    assert "no position" in comparison.qualifying.basis


def test_a_qualifying_session_without_a_time_is_excluded(target: Target) -> None:
    record = _read(target, target.alice, target.chen).qualifying

    # They share exactly one qualifying session, and Chen set no time in it.
    assert record.compared == 0
    assert record.excluded == 1


def test_a_pair_who_never_met_returns_zeroes_rather_than_an_error(
    target: Target,
) -> None:
    comparison = _read(target, target.alice, target.dara)

    assert comparison.never_met is True
    assert comparison.qualifying.compared == 0
    assert comparison.race.compared == 0
    assert comparison.qualifying.excluded == 0
    # The season totals are still populated for both sides.
    assert comparison.totals_b.wins == 1


def test_a_pair_who_met_but_could_not_be_compared_is_not_never_met(
    target: Target,
) -> None:
    comparison = _read(target, target.alice, target.chen)

    # They shared sessions; none could be ordered. That is a different fact
    # from never having raced together, and the flag must not conflate them.
    assert comparison.never_met is False


def test_season_totals_count_a_non_start_as_a_dnf(target: Target) -> None:
    totals = _read(target, target.alice, target.bruno).totals_a

    assert totals.starts == 2
    assert totals.dnfs == 1
    assert totals.wins == 1
    assert totals.best_finish == 1
    assert totals.points == "25.000"


def test_a_driver_cannot_be_compared_with_themselves(target: Target) -> None:
    with pytest.raises(HeadToHeadReadError):
        _read(target, target.alice, target.alice)


def test_a_driver_outside_the_season_is_reported(target: Target) -> None:
    with pytest.raises(DriverNotFoundError):
        _read(target, target.alice, 2_147_483_000)


def test_consistency_matches_a_hand_computed_spread(target: Target) -> None:
    rows = {
        row.driver_id: row
        for row in read_consistency(
            season_year=target.season_year,
            session_factory=target.session_factory,
        ).items
    }
    alice = rows[str(target.alice)]

    # Alice's two clean laps are 100% and 110% of the session best. Median is
    # 105; the sample standard deviation of {100, 110} is sqrt(50); the
    # quartiles interpolate to 102.5 and 107.5.
    assert alice.clean_laps == 2
    assert alice.median_percent == pytest.approx(105.0)
    assert alice.std_dev_percent == pytest.approx(50**0.5, abs=1e-3)
    assert alice.iqr_percent == pytest.approx(5.0)


def test_consistency_ignores_pit_and_yellow_flag_laps(target: Target) -> None:
    rows = {
        row.driver_id: row
        for row in read_consistency(
            season_year=target.season_year,
            session_factory=target.session_factory,
        ).items
    }

    # Alice set four laps; the in lap and the yellow-flag lap are far slower
    # and would dominate the spread if they counted.
    assert rows[str(target.alice)].clean_laps == 2


def test_consistency_reports_the_finish_rate(target: Target) -> None:
    rows = {
        row.driver_id: row
        for row in read_consistency(
            season_year=target.season_year,
            session_factory=target.session_factory,
        ).items
    }

    alice = rows[str(target.alice)]
    assert alice.races_started == 2
    assert alice.races_classified == 1
    assert alice.finish_rate == pytest.approx(0.5)


def test_a_driver_with_no_clean_lap_is_listed_last_not_first(
    target: Target,
) -> None:
    items = read_consistency(
        season_year=target.season_year,
        session_factory=target.session_factory,
    ).items

    # No laps means no spread, which must not read as perfect consistency.
    assert items[0].std_dev_percent is not None
    assert items[-1].std_dev_percent is None
    assert items[-1].clean_laps == 0


def test_consistency_states_what_the_percentages_are_relative_to(
    target: Target,
) -> None:
    response = read_consistency(
        season_year=target.season_year,
        session_factory=target.session_factory,
    )

    assert "percentage of the best clean lap" in response.basis
    assert "track was green" in response.clean_lap_definition


def _driver(connection, *, key: str, name: str) -> int:
    value = connection.scalar(
        text(
            """
            INSERT INTO drivers (
                jolpica_driver_id, given_name, family_name, full_name,
                country_code
            )
            VALUES (:key, :given, 'Driver', :name, 'GBR')
            RETURNING id
            """
        ),
        {"key": key, "given": name.split()[0], "name": name},
    )
    assert value is not None
    return value


def _session(connection, *, event_id: int, key: str, now) -> int:
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
            "name": key.title(),
            "starts_at": now - timedelta(days=20),
            "ends_at": now - timedelta(days=20) + timedelta(hours=2),
            "now": now,
        },
    )
    assert value is not None
    connection.execute(
        text(
            """
            INSERT INTO session_ingestions (
                session_id, status, source, record_state, attempt_count,
                completed_at, source_updated_at
            )
            VALUES (
                :session_id, 'completed', 'fastf1_archive', 'finalized', 1,
                :now, :now
            )
            """
        ),
        {"session_id": value, "now": now},
    )
    return value


def _entry_result(
    connection,
    *,
    session_id: int,
    driver_id: int,
    suffix: str,
    number: str,
    name: str,
    position: int | None,
    classified: str | None,
    points: str = "0.000",
    status: str = "Finished",
) -> int:
    entry_id = connection.scalar(
        text(
            """
            INSERT INTO session_entries (
                session_id, driver_id, entry_key, racing_number, abbreviation,
                broadcast_name, display_name, team_jolpica_id, team_name,
                team_color, source, record_state
            )
            VALUES (
                :session_id, :driver_id, :entry_key, :number, :abbreviation,
                :broadcast, :name, 'example_team', 'Example Team', '27F4D2',
                'fastf1_archive', 'finalized'
            )
            RETURNING id
            """
        ),
        {
            "session_id": session_id,
            "driver_id": driver_id,
            "entry_key": f"{name.split()[0].lower()}-{suffix}",
            "number": number,
            "abbreviation": name[:3].upper(),
            "broadcast": name.upper(),
            "name": name,
        },
    )
    assert entry_id is not None
    connection.execute(
        text(
            """
            INSERT INTO session_results (
                session_entry_id, position, classified_position, points,
                status, source, record_state
            )
            VALUES (
                :entry_id, :position, :classified, :points, :status,
                'fastf1_archive', 'finalized'
            )
            """
        ),
        {
            "entry_id": entry_id,
            "position": position,
            "classified": classified,
            "points": points,
            "status": status,
        },
    )
    return entry_id


def _lap(
    connection,
    *,
    entry_id: int,
    lap_number: int,
    lap_time_us: int,
    track_status: str = "1",
    pit_in: bool = False,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO laps (
                session_entry_id, lap_number, stint_number, lap_time_us,
                pit_in_time_us, is_personal_best, compound, tyre_life_laps,
                fresh_tyre, track_status, position, deleted, fastf1_generated,
                is_accurate, source, record_state
            )
            VALUES (
                :entry_id, :lap_number, 1, :lap_time_us, :pit_in_time_us,
                FALSE, 'MEDIUM', :lap_number, FALSE, :track_status, 1, FALSE,
                FALSE, TRUE, 'fastf1_archive', 'finalized'
            )
            """
        ),
        {
            "entry_id": entry_id,
            "lap_number": lap_number,
            "lap_time_us": lap_time_us,
            "pit_in_time_us": 95_000_000 if pit_in else None,
            "track_status": track_status,
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
            text("DELETE FROM drivers WHERE jolpica_driver_id LIKE :pattern"),
            {"pattern": f"%-{suffix}"},
        )
