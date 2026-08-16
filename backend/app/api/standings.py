"""Championship standings, aggregated from stored session results.

Nothing is recomputed from a scoring table. ``session_results.points`` is what
the upstream reported for that session, so summing it is correct across every
era's scoring system — including the years sprints paid differently, and the
2010 change from 10-8-6 to 25-18-15. A points table of our own would silently
disagree with history.

For the same reason no session type is hard-coded as "scoring". Practice and
qualifying rows carry no points, so summing every session in the season yields
the championship without an era-specific list of which sessions counted.

A standing is only as complete as the archive behind it. Sessions that have not
been ingested are absent rather than zero, and the response reports how many
sessions it was computed from so a caller can tell a mid-season table from an
incomplete one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal

from sqlalchemy import Numeric, and_, case, cast, func, select
from sqlalchemy.orm import Session

from app.api.contracts import (
    ConstructorStanding,
    ConstructorStandingsResponse,
    DriverStanding,
    DriverStandingsResponse,
    StandingsRound,
    StandingsRoundPoints,
)
from app.db.models import (
    Driver,
    Event,
    RaceSession,
    SessionEntry,
    SessionIngestion,
    SessionResult,
)

SessionFactory = Callable[[], Session]

#: A session counts once its ingestion has completed. This is the same rule the
#: season overview uses for ``data_available``; the two must not drift.
_COMPLETED = SessionIngestion.completed_at.is_not(None)

#: Only a numeric classified position means the driver was classified. A
#: retirement past ninety per cent of the race distance still is, which is why
#: this is read from the data rather than inferred from the status text — of 46
#: rows reading "Retired" in the archive, six carry a classified position.
#:
#: Wrapped in coalesce because the column is NULL for the clearest
#: non-finishes, and in SQL "NOT NULL" is NULL rather than true: without this
#: a driver who did not finish at all was counted as neither classified nor
#: retired, and vanished from the DNF tally entirely.
_CLASSIFIED = func.coalesce(
    SessionResult.classified_position.op("~")("^[0-9]+$"), False
)

_IS_RACE = RaceSession.session_key == "race"
_IS_QUALIFYING = RaceSession.session_key == "qualifying"


def _points() -> object:
    return func.coalesce(func.sum(SessionResult.points), 0)


def _count_when(condition: object) -> object:
    return func.count(case((condition, 1)))


def _scoring_rounds(database: Session, season_year: int) -> list[StandingsRound]:
    """Every ingested session of the season that awarded points, in order."""
    rows = database.execute(
        select(
            Event.round_number,
            Event.event_name,
            RaceSession.session_key,
            RaceSession.id,
        )
        .join(RaceSession, RaceSession.event_id == Event.id)
        .join(SessionIngestion, SessionIngestion.session_id == RaceSession.id)
        .join(SessionEntry, SessionEntry.session_id == RaceSession.id)
        .join(SessionResult, SessionResult.session_entry_id == SessionEntry.id)
        .where(Event.season_year == season_year, _COMPLETED)
        .group_by(Event.round_number, Event.event_name, RaceSession.session_key, RaceSession.id)
        .having(func.coalesce(func.sum(SessionResult.points), 0) > 0)
        .order_by(
            Event.round_number, RaceSession.scheduled_start_at, RaceSession.id
        )
    ).all()
    return [
        StandingsRound(
            round_number=row.round_number,
            event_name=row.event_name,
            session_key=row.session_key,
            session_id=str(row.id),
        )
        for row in rows
    ]


def _per_round(
    database: Session,
    season_year: int,
    *,
    group_by_team: bool,
) -> dict[str, list[StandingsRoundPoints]]:
    """Points per competitor per session, so a caller can chart the season."""
    key = SessionEntry.team_name if group_by_team else cast(SessionEntry.driver_id, Numeric)
    rows = database.execute(
        select(
            key.label("key"),
            Event.round_number,
            RaceSession.session_key,
            RaceSession.id.label("session_id"),
            func.coalesce(func.sum(SessionResult.points), 0).label("points"),
            func.min(SessionResult.position).label("position"),
        )
        .select_from(SessionResult)
        .join(SessionEntry, SessionEntry.id == SessionResult.session_entry_id)
        .join(RaceSession, RaceSession.id == SessionEntry.session_id)
        .join(Event, Event.id == RaceSession.event_id)
        .join(SessionIngestion, SessionIngestion.session_id == RaceSession.id)
        .where(Event.season_year == season_year, _COMPLETED)
        .group_by(key, Event.round_number, RaceSession.session_key, RaceSession.id)
        .having(func.coalesce(func.sum(SessionResult.points), 0) > 0)
        # Chronological within a round, so a sprint precedes the grand prix it
        # supports rather than sorting arbitrarily beside it.
        .order_by(Event.round_number, RaceSession.scheduled_start_at, RaceSession.id)
    ).all()

    grouped: dict[str, list[StandingsRoundPoints]] = {}
    for row in rows:
        if row.key is None:
            continue
        identifier = str(int(row.key)) if not group_by_team else str(row.key)
        grouped.setdefault(identifier, []).append(
            StandingsRoundPoints(
                round_number=row.round_number,
                session_key=row.session_key,
                points=Decimal(row.points),
                position=row.position,
            )
        )
    return grouped


def read_driver_standings(
    *,
    season_year: int,
    session_factory: SessionFactory,
) -> DriverStandingsResponse:
    with session_factory() as database:
        rounds = _scoring_rounds(database, season_year)
        per_round = _per_round(database, season_year, group_by_team=False)

        rows = database.execute(
            select(
                Driver.id,
                func.max(Driver.full_name).label("display_name"),
                func.max(SessionEntry.abbreviation).label("abbreviation"),
                _points().label("points"),
                _count_when(and_(_IS_RACE, SessionResult.position == 1)).label("wins"),
                _count_when(and_(_IS_RACE, SessionResult.position <= 3)).label("podiums"),
                _count_when(
                    and_(_IS_QUALIFYING, SessionResult.position == 1)
                ).label("poles"),
                _count_when(_IS_RACE).label("starts"),
                _count_when(and_(_IS_RACE, ~_CLASSIFIED)).label("dnfs"),
                func.min(
                    case((and_(_IS_RACE, _CLASSIFIED), SessionResult.position))
                ).label("best_finish"),
                # The season's latest entry decides the team shown, because a
                # driver may change team mid-season.
                func.max(
                    case((_IS_RACE, Event.round_number)), else_=0
                ).label("last_round"),
            )
            .select_from(SessionResult)
            .join(SessionEntry, SessionEntry.id == SessionResult.session_entry_id)
            .join(Driver, Driver.id == SessionEntry.driver_id)
            .join(RaceSession, RaceSession.id == SessionEntry.session_id)
            .join(Event, Event.id == RaceSession.event_id)
            .join(SessionIngestion, SessionIngestion.session_id == RaceSession.id)
            .where(Event.season_year == season_year, _COMPLETED)
            .group_by(Driver.id)
        ).all()

        teams = _latest_team_by_driver(database, season_year)

        standings = sorted(
            rows,
            key=lambda row: (
                -Decimal(row.points),
                -row.wins,
                -row.podiums,
                row.best_finish if row.best_finish is not None else 10**6,
                row.display_name or "",
            ),
        )

        items = []
        for index, row in enumerate(standings, start=1):
            team = teams.get(row.id, (None, None))
            items.append(
                DriverStanding(
                    position=index,
                    driver_id=str(row.id),
                    display_name=row.display_name or "Unknown driver",
                    abbreviation=row.abbreviation,
                    team_name=team[0],
                    team_color=team[1],
                    points=Decimal(row.points),
                    wins=row.wins,
                    podiums=row.podiums,
                    poles=row.poles,
                    starts=row.starts,
                    dnfs=row.dnfs,
                    best_finish=row.best_finish,
                    rounds=tuple(per_round.get(str(row.id), ())),
                )
            )

        return DriverStandingsResponse(
            season_year=season_year,
            scoring_sessions=len(rounds),
            rounds=tuple(rounds),
            items=tuple(items),
        )


def _latest_team_by_driver(
    database: Session,
    season_year: int,
) -> dict[int, tuple[str | None, str | None]]:
    rows = database.execute(
        select(
            SessionEntry.driver_id,
            SessionEntry.team_name,
            SessionEntry.team_color,
            Event.round_number,
        )
        .join(RaceSession, RaceSession.id == SessionEntry.session_id)
        .join(Event, Event.id == RaceSession.event_id)
        .join(SessionIngestion, SessionIngestion.session_id == RaceSession.id)
        .where(Event.season_year == season_year, _COMPLETED)
        .order_by(SessionEntry.driver_id, Event.round_number)
    ).all()
    latest: dict[int, tuple[str | None, str | None]] = {}
    for row in rows:
        if row.driver_id is not None:
            latest[row.driver_id] = (row.team_name, row.team_color)
    return latest


def read_constructor_standings(
    *,
    season_year: int,
    session_factory: SessionFactory,
) -> ConstructorStandingsResponse:
    with session_factory() as database:
        rounds = _scoring_rounds(database, season_year)
        per_round = _per_round(database, season_year, group_by_team=True)

        rows = database.execute(
            select(
                SessionEntry.team_name,
                func.max(SessionEntry.team_color).label("team_color"),
                _points().label("points"),
                _count_when(and_(_IS_RACE, SessionResult.position == 1)).label("wins"),
                _count_when(and_(_IS_RACE, SessionResult.position <= 3)).label("podiums"),
                _count_when(
                    and_(_IS_QUALIFYING, SessionResult.position == 1)
                ).label("poles"),
                func.min(
                    case((and_(_IS_RACE, _CLASSIFIED), SessionResult.position))
                ).label("best_finish"),
            )
            .select_from(SessionResult)
            .join(SessionEntry, SessionEntry.id == SessionResult.session_entry_id)
            .join(RaceSession, RaceSession.id == SessionEntry.session_id)
            .join(Event, Event.id == RaceSession.event_id)
            .join(SessionIngestion, SessionIngestion.session_id == RaceSession.id)
            .where(
                Event.season_year == season_year,
                _COMPLETED,
                SessionEntry.team_name.is_not(None),
            )
            .group_by(SessionEntry.team_name)
        ).all()

        drivers = _drivers_by_team(database, season_year)

        standings = sorted(
            rows,
            key=lambda row: (
                -Decimal(row.points),
                -row.wins,
                -row.podiums,
                row.best_finish if row.best_finish is not None else 10**6,
                row.team_name or "",
            ),
        )

        items = [
            ConstructorStanding(
                position=index,
                team_name=row.team_name,
                team_color=row.team_color,
                points=Decimal(row.points),
                wins=row.wins,
                podiums=row.podiums,
                poles=row.poles,
                best_finish=row.best_finish,
                drivers=tuple(drivers.get(row.team_name, ())),
                rounds=tuple(per_round.get(row.team_name, ())),
            )
            for index, row in enumerate(standings, start=1)
        ]

        return ConstructorStandingsResponse(
            season_year=season_year,
            scoring_sessions=len(rounds),
            rounds=tuple(rounds),
            items=tuple(items),
        )


def _drivers_by_team(
    database: Session,
    season_year: int,
) -> dict[str, Sequence[str]]:
    rows = database.execute(
        select(SessionEntry.team_name, Driver.full_name)
        .join(Driver, Driver.id == SessionEntry.driver_id)
        .join(RaceSession, RaceSession.id == SessionEntry.session_id)
        .join(Event, Event.id == RaceSession.event_id)
        .join(SessionIngestion, SessionIngestion.session_id == RaceSession.id)
        .where(Event.season_year == season_year, _COMPLETED)
        .group_by(SessionEntry.team_name, Driver.full_name)
        .order_by(SessionEntry.team_name, Driver.full_name)
    ).all()
    grouped: dict[str, list[str]] = {}
    for row in rows:
        if row.team_name and row.full_name:
            grouped.setdefault(row.team_name, []).append(row.full_name)
    return grouped
