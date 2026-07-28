from __future__ import annotations

import re
from collections.abc import Callable

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.contracts import (
    LapSummary,
    LapSummaryFilters,
    LapSummaryPage,
    LapSummaryQuery,
    LapSummaryResponse,
    LastError,
    SessionDetailCounts,
    SessionDetailEvent,
    SessionDetailIngestion,
    SessionDetailResponse,
    SessionEntryResult,
    SessionResultData,
    SessionResultDriver,
    SessionResultsResponse,
    SessionSnapshot,
)
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

_READ_ONLY_TRANSACTION = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
)
_TEAM_COLOR_PATTERN = re.compile(r"^[0-9A-Fa-f]{6}$")


class SessionDataReadError(ValueError):
    """Raised when a historical session read violates its service contract."""


class SessionNotFoundError(SessionDataReadError):
    """Raised when the requested session does not exist."""


class SessionEntryNotFoundError(SessionDataReadError):
    """Raised when an entry does not belong to the requested session."""


class SessionDataUnavailableError(SessionDataReadError):
    """Raised when no completed historical snapshot is available."""


def read_session_detail(
    *,
    session_id: int,
    session_factory: SessionFactory,
) -> SessionDetailResponse:
    """Read bounded session metadata and snapshot counts without writes."""

    _validate_identifier(session_id, "session_id")

    with session_factory() as database, database.begin():
        _begin_read_only(database)
        race_session, event, ingestion = _session_context(
            database,
            session_id=session_id,
        )
        snapshot = _snapshot(ingestion)
        counts = (
            _sporting_counts(database, session_id=session_id)
            if snapshot.data_available
            else SessionDetailCounts(entries=0, results=0, laps=0)
        )

        return SessionDetailResponse(
            id=race_session.id,
            session_key=race_session.session_key,
            session_name=race_session.session_name,
            scheduled_start_at=race_session.scheduled_start_at,
            scheduled_end_at=race_session.scheduled_end_at,
            event=SessionDetailEvent(
                id=event.id,
                season_year=event.season_year,
                round_number=event.round_number,
                official_name=event.official_name,
                event_name=event.event_name,
                country=event.country,
                location=event.location,
                event_format=event.event_format,
            ),
            snapshot=snapshot,
            ingestion=_ingestion(ingestion),
            counts=counts,
        )


def read_session_results(
    *,
    session_id: int,
    session_factory: SessionFactory,
) -> SessionResultsResponse:
    """Read every session entry and optional result in stable order."""

    _validate_identifier(session_id, "session_id")

    with session_factory() as database, database.begin():
        _begin_read_only(database)
        _, _, ingestion = _session_context(
            database,
            session_id=session_id,
        )
        snapshot = _require_snapshot(ingestion)
        rows = database.execute(
            select(SessionEntry, Driver, SessionResult)
            .outerjoin(Driver, Driver.id == SessionEntry.driver_id)
            .outerjoin(
                SessionResult,
                SessionResult.session_entry_id == SessionEntry.id,
            )
            .where(SessionEntry.session_id == session_id)
            .order_by(
                SessionResult.position.asc().nulls_last(),
                SessionEntry.id,
            )
        ).all()

        return SessionResultsResponse(
            session_id=session_id,
            snapshot=snapshot,
            items=tuple(
                _entry_result(
                    entry=entry,
                    driver=driver,
                    result=result,
                )
                for entry, driver, result in rows
            ),
        )


def read_session_laps(
    *,
    session_id: int,
    session_entry_id: int,
    query: LapSummaryQuery,
    session_factory: SessionFactory,
) -> LapSummaryResponse:
    """Read one stable keyset page of lap summaries for a session entry."""

    _validate_identifier(session_id, "session_id")
    _validate_identifier(session_entry_id, "session_entry_id")
    if not isinstance(query, LapSummaryQuery):
        raise SessionDataReadError("query must be a LapSummaryQuery")

    with session_factory() as database, database.begin():
        _begin_read_only(database)
        _, _, ingestion = _session_context(
            database,
            session_id=session_id,
        )
        entry_exists = database.scalar(
            select(SessionEntry.id).where(
                SessionEntry.id == session_entry_id,
                SessionEntry.session_id == session_id,
            )
        )
        if entry_exists is None:
            raise SessionEntryNotFoundError(
                f"session entry {session_entry_id} does not belong to session "
                f"{session_id}"
            )
        snapshot = _require_snapshot(ingestion)

        statement = select(Lap).where(
            Lap.session_entry_id == session_entry_id
        )
        if query.after_lap is not None:
            statement = statement.where(Lap.lap_number > query.after_lap)
        if query.lap_from is not None:
            statement = statement.where(Lap.lap_number >= query.lap_from)
        if query.lap_to is not None:
            statement = statement.where(Lap.lap_number <= query.lap_to)
        if query.stint_number is not None:
            statement = statement.where(
                Lap.stint_number == query.stint_number
            )
        if not query.include_deleted:
            statement = statement.where(Lap.deleted.is_not(True))

        rows = list(
            database.scalars(
                statement.order_by(Lap.lap_number).limit(query.limit + 1)
            )
        )
        has_more = len(rows) > query.limit
        page_rows = rows[: query.limit]
        items = tuple(_lap_summary(lap) for lap in page_rows)

        return LapSummaryResponse(
            session_id=session_id,
            session_entry_id=session_entry_id,
            snapshot=snapshot,
            filters=LapSummaryFilters(
                lap_from=query.lap_from,
                lap_to=query.lap_to,
                stint_number=query.stint_number,
                include_deleted=query.include_deleted,
            ),
            page=LapSummaryPage(
                limit=query.limit,
                has_more=has_more,
                next_after_lap=(
                    page_rows[-1].lap_number if has_more else None
                ),
            ),
            items=items,
        )


def _begin_read_only(database: Session) -> None:
    database.execute(text(_READ_ONLY_TRANSACTION))


def _session_context(
    database: Session,
    *,
    session_id: int,
) -> tuple[RaceSession, Event, SessionIngestion | None]:
    row = database.execute(
        select(RaceSession, Event, SessionIngestion)
        .join(Event, Event.id == RaceSession.event_id)
        .outerjoin(
            SessionIngestion,
            SessionIngestion.session_id == RaceSession.id,
        )
        .where(RaceSession.id == session_id)
    ).one_or_none()
    if row is None:
        raise SessionNotFoundError(f"session {session_id} does not exist")
    return row


def _snapshot(ingestion: SessionIngestion | None) -> SessionSnapshot:
    if ingestion is None or ingestion.completed_at is None:
        return SessionSnapshot(
            data_available=False,
            source=None,
            record_state=None,
            completed_at=None,
            source_updated_at=None,
        )
    return SessionSnapshot(
        data_available=True,
        source=ingestion.source,
        record_state=ingestion.record_state,
        completed_at=ingestion.completed_at,
        source_updated_at=ingestion.source_updated_at,
    )


def _require_snapshot(
    ingestion: SessionIngestion | None,
) -> SessionSnapshot:
    snapshot = _snapshot(ingestion)
    if not snapshot.data_available:
        raise SessionDataUnavailableError(
            "historical session data is not available"
        )
    return snapshot


def _ingestion(
    ingestion: SessionIngestion | None,
) -> SessionDetailIngestion | None:
    if ingestion is None:
        return None
    return SessionDetailIngestion(
        status=ingestion.status,
        source=ingestion.source,
        record_state=ingestion.record_state,
        attempt_count=ingestion.attempt_count,
        completed_at=ingestion.completed_at,
        next_retry_at=ingestion.next_retry_at,
        last_error=_last_error(ingestion),
    )


def _last_error(ingestion: SessionIngestion) -> LastError | None:
    if (
        ingestion.last_error_code is None
        or ingestion.last_error_message is None
    ):
        return None
    return LastError(
        code=ingestion.last_error_code,
        message=ingestion.last_error_message,
    )


def _sporting_counts(
    database: Session,
    *,
    session_id: int,
) -> SessionDetailCounts:
    entry_count = (
        select(func.count(SessionEntry.id))
        .where(SessionEntry.session_id == session_id)
        .scalar_subquery()
    )
    result_count = (
        select(func.count(SessionResult.session_entry_id))
        .join(
            SessionEntry,
            SessionEntry.id == SessionResult.session_entry_id,
        )
        .where(SessionEntry.session_id == session_id)
        .scalar_subquery()
    )
    lap_count = (
        select(func.count(Lap.id))
        .join(
            SessionEntry,
            SessionEntry.id == Lap.session_entry_id,
        )
        .where(SessionEntry.session_id == session_id)
        .scalar_subquery()
    )
    counts = database.execute(
        select(
            entry_count.label("entries"),
            result_count.label("results"),
            lap_count.label("laps"),
        )
    ).one()
    return SessionDetailCounts(
        entries=counts.entries,
        results=counts.results,
        laps=counts.laps,
    )


def _entry_result(
    *,
    entry: SessionEntry,
    driver: Driver | None,
    result: SessionResult | None,
) -> SessionEntryResult:
    return SessionEntryResult(
        session_entry_id=entry.id,
        driver=(
            SessionResultDriver(
                id=driver.id,
                jolpica_driver_id=driver.jolpica_driver_id,
                given_name=driver.given_name,
                family_name=driver.family_name,
                full_name=driver.full_name,
                country_code=driver.country_code,
            )
            if driver is not None
            else None
        ),
        racing_number=entry.racing_number,
        abbreviation=entry.abbreviation,
        broadcast_name=entry.broadcast_name,
        display_name=entry.display_name,
        team_jolpica_id=entry.team_jolpica_id,
        team_name=entry.team_name,
        team_color_hex=_team_color(entry.team_color),
        source=entry.source,
        record_state=entry.record_state,
        result=(
            SessionResultData(
                position=result.position,
                classified_position=result.classified_position,
                grid_position=result.grid_position,
                points=result.points,
                status=result.status,
                laps_completed=result.laps_completed,
                q1_time_us=result.q1_time_us,
                q2_time_us=result.q2_time_us,
                q3_time_us=result.q3_time_us,
                elapsed_time_us=result.elapsed_time_us,
                gap_to_leader_us=result.gap_to_leader_us,
                gap_to_leader_laps=result.gap_to_leader_laps,
                source=result.source,
                record_state=result.record_state,
            )
            if result is not None
            else None
        ),
    )


def _team_color(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip().removeprefix("#")
    if _TEAM_COLOR_PATTERN.fullmatch(candidate) is None:
        return None
    return f"#{candidate.upper()}"


def _lap_summary(lap: Lap) -> LapSummary:
    return LapSummary(
        id=lap.id,
        lap_number=lap.lap_number,
        stint_number=lap.stint_number,
        session_time_us=lap.session_time_us,
        lap_time_us=lap.lap_time_us,
        lap_start_time_us=lap.lap_start_time_us,
        pit_out_time_us=lap.pit_out_time_us,
        pit_in_time_us=lap.pit_in_time_us,
        sector_1_time_us=lap.sector_1_time_us,
        sector_2_time_us=lap.sector_2_time_us,
        sector_3_time_us=lap.sector_3_time_us,
        sector_1_session_time_us=lap.sector_1_session_time_us,
        sector_2_session_time_us=lap.sector_2_session_time_us,
        sector_3_session_time_us=lap.sector_3_session_time_us,
        speed_i1_kph=lap.speed_i1_kph,
        speed_i2_kph=lap.speed_i2_kph,
        speed_fl_kph=lap.speed_fl_kph,
        speed_st_kph=lap.speed_st_kph,
        is_personal_best=lap.is_personal_best,
        compound=lap.compound,
        tyre_life_laps=lap.tyre_life_laps,
        fresh_tyre=lap.fresh_tyre,
        track_status=lap.track_status,
        position=lap.position,
        deleted=lap.deleted,
        deleted_reason=lap.deleted_reason,
        fastf1_generated=lap.fastf1_generated,
        is_accurate=lap.is_accurate,
        source=lap.source,
        record_state=lap.record_state,
    )


def _validate_identifier(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SessionDataReadError(
            f"{name} must be a positive integer"
        )
