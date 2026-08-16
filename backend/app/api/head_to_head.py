"""Two drivers against each other, and how repeatable a driver's pace is.

Both answers are scoped to one season and never span two. Regulations, cars and
tyres change between seasons, so a record built across them compares machinery
rather than drivers.

The qualifying record is read from the qualifying session's own finishing
position, not from ``grid_position``: that column is populated only on race and
sprint results -- it is NULL on every qualifying row in the archive -- and a
grid slot reflects penalties as much as pace.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from sqlalchemy import Float, and_, case, cast, func, select
from sqlalchemy.orm import Session

from app.api.contracts import (
    ComparedDriver,
    ConsistencyResponse,
    ConsistencyRow,
    HeadToHeadRecord,
    HeadToHeadResponse,
    SeasonTotals,
)
from app.api.season_rules import CLASSIFIED, COMPLETED, IS_QUALIFYING, IS_RACE
from app.api.session_data import CLEAN_LAP, CLEAN_LAP_DEFINITION
from app.db.models import (
    Driver,
    Event,
    Lap,
    RaceSession,
    SessionEntry,
    SessionIngestion,
    SessionResult,
)

SessionFactory = Callable[[], Session]

QUALIFYING_BASIS = (
    "Finishing position in the qualifying session. Sessions where either "
    "driver has no position -- they set no time, or did not take part -- are "
    "excluded, because the pair cannot be ordered."
)
RACE_BASIS = (
    "Finishing position in the race, counting only races both drivers were "
    "classified in. A retirement still carries a position -- it is the order "
    "cars stopped, not a result -- and a driver who did not start carries one "
    "too, so comparing raw positions would score a race someone never took "
    "part in as a loss. Retirements are excluded here and counted as DNFs in "
    "the season totals instead."
)
CONSISTENCY_BASIS = (
    "Race sessions only, each clean lap expressed as a percentage of the best "
    "clean lap of that same session. Practice laps reach 221% of a session "
    "best in this archive, and an absolute season median would mix Monaco "
    "with Monza; normalising per session keeps a whole season comparable."
)


class HeadToHeadReadError(ValueError):
    """Raised when a comparison violates its service contract."""


class DriverNotFoundError(HeadToHeadReadError):
    """Raised when a requested driver has no entry in the season."""


def read_head_to_head(
    *,
    season_year: int,
    driver_a: int,
    driver_b: int,
    session_factory: SessionFactory,
) -> HeadToHeadResponse:
    if driver_a == driver_b:
        raise HeadToHeadReadError("a driver cannot be compared with themselves")

    with session_factory() as database:
        drivers = _compared_drivers(
            database, season_year=season_year, driver_ids=(driver_a, driver_b)
        )
        for driver_id in (driver_a, driver_b):
            if driver_id not in drivers:
                raise DriverNotFoundError(
                    f"driver {driver_id} has no entry in season {season_year}"
                )

        qualifying = _record(
            database,
            season_year=season_year,
            driver_a=driver_a,
            driver_b=driver_b,
            session_filter=IS_QUALIFYING,
            basis=QUALIFYING_BASIS,
            require_classified=False,
        )
        race = _record(
            database,
            season_year=season_year,
            driver_a=driver_a,
            driver_b=driver_b,
            session_filter=IS_RACE,
            basis=RACE_BASIS,
            require_classified=True,
        )
        totals = _season_totals(
            database, season_year=season_year, driver_ids=(driver_a, driver_b)
        )

        return HeadToHeadResponse(
            season_year=season_year,
            driver_a=drivers[driver_a],
            driver_b=drivers[driver_b],
            qualifying=qualifying,
            race=race,
            totals_a=totals[driver_a],
            totals_b=totals[driver_b],
            never_met=(
                qualifying.compared == 0
                and race.compared == 0
                and qualifying.excluded == 0
                and race.excluded == 0
            ),
        )


def _record(
    database: Session,
    *,
    season_year: int,
    driver_a: int,
    driver_b: int,
    session_filter: object,
    basis: str,
    require_classified: bool,
) -> HeadToHeadRecord:
    """Count sessions both drivers entered, and who finished ahead in each."""

    # One row per session per driver, then self-joined on the session so only
    # sessions both entered survive. Comparing counts of wins separately would
    # score a session one of them never entered.
    entered = (
        select(
            RaceSession.id.label("session_id"),
            SessionEntry.driver_id.label("driver_id"),
            (
                # A retirement or a non-start carries a position that orders
                # cars rather than ranks them, so it must not be comparable.
                case((CLASSIFIED, SessionResult.position))
                if require_classified
                else SessionResult.position
            ).label("position"),
        )
        .select_from(SessionEntry)
        .join(RaceSession, RaceSession.id == SessionEntry.session_id)
        .join(Event, Event.id == RaceSession.event_id)
        .join(SessionIngestion, SessionIngestion.session_id == RaceSession.id)
        .outerjoin(
            SessionResult,
            SessionResult.session_entry_id == SessionEntry.id,
        )
        .where(
            Event.season_year == season_year,
            COMPLETED,
            session_filter,
            SessionEntry.driver_id.in_((driver_a, driver_b)),
        )
        .subquery()
    )

    side_a = entered.alias("side_a")
    side_b = entered.alias("side_b")
    row = database.execute(
        select(
            func.count().label("shared"),
            func.count(
                case(
                    (
                        and_(
                            side_a.c.position.is_not(None),
                            side_b.c.position.is_not(None),
                        ),
                        1,
                    )
                )
            ).label("comparable"),
            func.count(
                case((side_a.c.position < side_b.c.position, 1))
            ).label("a_ahead"),
            func.count(
                case((side_b.c.position < side_a.c.position, 1))
            ).label("b_ahead"),
        ).select_from(
            side_a.join(
                side_b,
                and_(
                    side_a.c.session_id == side_b.c.session_id,
                    side_a.c.driver_id == driver_a,
                    side_b.c.driver_id == driver_b,
                ),
            )
        )
    ).one()

    return HeadToHeadRecord(
        basis=basis,
        a_ahead=row.a_ahead,
        b_ahead=row.b_ahead,
        compared=row.a_ahead + row.b_ahead,
        # A session both entered but which cannot be ordered -- one of them has
        # no position, or the two positions are equal, which the archive does
        # not produce but the schema permits.
        excluded=row.shared - (row.a_ahead + row.b_ahead),
    )


def _compared_drivers(
    database: Session,
    *,
    season_year: int,
    driver_ids: tuple[int, ...],
) -> dict[int, ComparedDriver]:
    rows = database.execute(
        select(
            Driver.id,
            func.max(Driver.full_name).label("display_name"),
            func.max(SessionEntry.abbreviation).label("abbreviation"),
        )
        .select_from(SessionEntry)
        .join(Driver, Driver.id == SessionEntry.driver_id)
        .join(RaceSession, RaceSession.id == SessionEntry.session_id)
        .join(Event, Event.id == RaceSession.event_id)
        .where(Event.season_year == season_year, Driver.id.in_(driver_ids))
        .group_by(Driver.id)
    ).all()

    teams = _latest_team(database, season_year=season_year, driver_ids=driver_ids)
    return {
        row.id: ComparedDriver(
            driver_id=row.id,
            display_name=row.display_name,
            abbreviation=row.abbreviation,
            team_name=teams.get(row.id, (None, None))[0],
            team_color_hex=teams.get(row.id, (None, None))[1],
        )
        for row in rows
    }


def _latest_team(
    database: Session,
    *,
    season_year: int,
    driver_ids: tuple[int, ...] | None = None,
) -> dict[int, tuple[str | None, str | None]]:
    """The team of each driver's latest round, since a driver may move."""

    ranked = (
        select(
            SessionEntry.driver_id.label("driver_id"),
            SessionEntry.team_name.label("team_name"),
            SessionEntry.team_color.label("team_color"),
            func.row_number()
            .over(
                partition_by=SessionEntry.driver_id,
                order_by=(Event.round_number.desc(), RaceSession.id.desc()),
            )
            .label("recency"),
        )
        .select_from(SessionEntry)
        .join(RaceSession, RaceSession.id == SessionEntry.session_id)
        .join(Event, Event.id == RaceSession.event_id)
        .where(
            Event.season_year == season_year,
            SessionEntry.driver_id.is_not(None),
        )
    )
    if driver_ids is not None:
        ranked = ranked.where(SessionEntry.driver_id.in_(driver_ids))
    latest = ranked.subquery()

    return {
        row.driver_id: (row.team_name, _team_color(row.team_color))
        for row in database.execute(
            select(latest).where(latest.c.recency == 1)
        ).all()
    }


def _season_totals(
    database: Session,
    *,
    season_year: int,
    driver_ids: tuple[int, ...],
) -> dict[int, SeasonTotals]:
    rows = database.execute(
        select(
            SessionEntry.driver_id.label("driver_id"),
            func.coalesce(func.sum(SessionResult.points), 0).label("points"),
            func.count(
                case((and_(IS_RACE, SessionResult.position == 1), 1))
            ).label("wins"),
            func.count(
                case((and_(IS_RACE, SessionResult.position <= 3), 1))
            ).label("podiums"),
            func.count(
                case((and_(IS_QUALIFYING, SessionResult.position == 1), 1))
            ).label("poles"),
            func.count(case((IS_RACE, 1))).label("starts"),
            func.count(case((and_(IS_RACE, ~CLASSIFIED), 1))).label("dnfs"),
            func.min(
                case((and_(IS_RACE, CLASSIFIED), SessionResult.position))
            ).label("best_finish"),
        )
        .select_from(SessionResult)
        .join(SessionEntry, SessionEntry.id == SessionResult.session_entry_id)
        .join(RaceSession, RaceSession.id == SessionEntry.session_id)
        .join(Event, Event.id == RaceSession.event_id)
        .join(SessionIngestion, SessionIngestion.session_id == RaceSession.id)
        .where(
            Event.season_year == season_year,
            COMPLETED,
            SessionEntry.driver_id.in_(driver_ids),
        )
        .group_by(SessionEntry.driver_id)
    ).all()

    totals = {
        row.driver_id: SeasonTotals(
            points=row.points,
            wins=row.wins,
            podiums=row.podiums,
            poles=row.poles,
            starts=row.starts,
            dnfs=row.dnfs,
            best_finish=row.best_finish,
        )
        for row in rows
    }
    # A driver who entered the season but has no completed result yet still
    # needs totals, or the response would be missing a side.
    empty = SeasonTotals(
        points=Decimal(0),
        wins=0,
        podiums=0,
        poles=0,
        starts=0,
        dnfs=0,
        best_finish=None,
    )
    return {
        driver_id: totals.get(driver_id, empty) for driver_id in driver_ids
    }


def read_consistency(
    *,
    season_year: int,
    session_factory: SessionFactory,
) -> ConsistencyResponse:
    """How repeatable each driver's race pace was across the season."""

    with session_factory() as database:
        # The reference each lap is measured against: the best clean lap of the
        # session it was set in.
        session_best = (
            select(
                SessionEntry.session_id.label("session_id"),
                func.min(Lap.lap_time_us).label("best_us"),
            )
            .select_from(Lap)
            .join(SessionEntry, SessionEntry.id == Lap.session_entry_id)
            .where(CLEAN_LAP)
            .group_by(SessionEntry.session_id)
            .subquery()
        )

        percent = cast(Lap.lap_time_us, Float) * 100.0 / cast(
            session_best.c.best_us, Float
        )
        pace = database.execute(
            select(
                SessionEntry.driver_id.label("driver_id"),
                func.count().label("clean_laps"),
                func.percentile_cont(0.5)
                .within_group(percent)
                .label("median_percent"),
                func.stddev_samp(percent).label("std_dev_percent"),
                (
                    func.percentile_cont(0.75).within_group(percent)
                    - func.percentile_cont(0.25).within_group(percent)
                ).label("iqr_percent"),
            )
            .select_from(Lap)
            .join(SessionEntry, SessionEntry.id == Lap.session_entry_id)
            .join(RaceSession, RaceSession.id == SessionEntry.session_id)
            .join(Event, Event.id == RaceSession.event_id)
            .join(SessionIngestion, SessionIngestion.session_id == RaceSession.id)
            .join(
                session_best,
                session_best.c.session_id == SessionEntry.session_id,
            )
            .where(
                Event.season_year == season_year,
                COMPLETED,
                IS_RACE,
                CLEAN_LAP,
                SessionEntry.driver_id.is_not(None),
            )
            .group_by(SessionEntry.driver_id)
        ).all()

        finishes = database.execute(
            select(
                SessionEntry.driver_id.label("driver_id"),
                func.count().label("started"),
                func.count(case((CLASSIFIED, 1))).label("classified"),
            )
            .select_from(SessionResult)
            .join(
                SessionEntry, SessionEntry.id == SessionResult.session_entry_id
            )
            .join(RaceSession, RaceSession.id == SessionEntry.session_id)
            .join(Event, Event.id == RaceSession.event_id)
            .join(SessionIngestion, SessionIngestion.session_id == RaceSession.id)
            .where(
                Event.season_year == season_year,
                COMPLETED,
                IS_RACE,
                SessionEntry.driver_id.is_not(None),
            )
            .group_by(SessionEntry.driver_id)
        ).all()

        names = database.execute(
            select(
                Driver.id,
                func.max(Driver.full_name).label("display_name"),
                func.max(SessionEntry.abbreviation).label("abbreviation"),
            )
            .select_from(SessionEntry)
            .join(Driver, Driver.id == SessionEntry.driver_id)
            .join(RaceSession, RaceSession.id == SessionEntry.session_id)
            .join(Event, Event.id == RaceSession.event_id)
            .where(Event.season_year == season_year)
            .group_by(Driver.id)
        ).all()

        teams = _latest_team(database, season_year=season_year)
        pace_by_driver = {row.driver_id: row for row in pace}
        finish_by_driver = {row.driver_id: row for row in finishes}

        items = []
        for name in names:
            measured = pace_by_driver.get(name.id)
            finished = finish_by_driver.get(name.id)
            started = finished.started if finished is not None else 0
            classified = finished.classified if finished is not None else 0
            team_name, team_color = teams.get(name.id, (None, None))
            items.append(
                ConsistencyRow(
                    driver_id=name.id,
                    display_name=name.display_name,
                    abbreviation=name.abbreviation,
                    team_name=team_name,
                    team_color_hex=team_color,
                    clean_laps=measured.clean_laps if measured else 0,
                    median_percent=_rounded(
                        measured.median_percent if measured else None
                    ),
                    # A single clean lap has no spread: stddev_samp is NULL
                    # there, which is the honest answer rather than zero.
                    std_dev_percent=_rounded(
                        measured.std_dev_percent if measured else None
                    ),
                    iqr_percent=_rounded(
                        measured.iqr_percent if measured else None
                    ),
                    races_started=started,
                    races_classified=classified,
                    finish_rate=(
                        round(classified / started, 4) if started else None
                    ),
                )
            )

        # Most consistent first. A driver with no measurable spread is listed
        # last rather than treated as perfectly consistent.
        items.sort(
            key=lambda row: (
                row.std_dev_percent is None,
                row.std_dev_percent or 0.0,
                row.display_name,
            )
        )
        return ConsistencyResponse(
            season_year=season_year,
            clean_lap_definition=CLEAN_LAP_DEFINITION,
            basis=CONSISTENCY_BASIS,
            items=tuple(items),
        )


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def _team_color(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip().removeprefix("#")
    if len(candidate) != 6 or any(
        character not in "0123456789abcdefABCDEF" for character in candidate
    ):
        return None
    return f"#{candidate.upper()}"
