from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import (
    Driver,
    Lap,
    RaceSession,
    SessionEntry,
    SessionIngestion,
    SessionResult,
)
from app.ingestion.fastf1_normalization import (
    ARCHIVE_SOURCE,
    FINALIZED_STATE,
    NormalizedDriver,
    NormalizedLap,
    NormalizedSession,
    NormalizedSessionEntry,
    NormalizedSessionResult,
)

LAP_UPSERT_BATCH_SIZE = 500


class ArchivePersistenceError(RuntimeError):
    """Base error for failures detected before archive snapshot writes."""


class ArchivePersistenceContractError(ArchivePersistenceError):
    """Raised when a normalized snapshot violates the persistence contract."""


class ArchivePersistenceTransactionError(ArchivePersistenceError):
    """Raised when the persistence function cannot own the transaction."""


class ArchiveSessionNotFoundError(ArchivePersistenceError):
    """Raised when the target database session does not exist."""


class ArchiveSourceConflictError(ArchivePersistenceError):
    """Raised when another data source already owns session ingestion data."""


@dataclass(frozen=True, slots=True)
class ArchivePersistenceSummary:
    session_id: int
    drivers_upserted: int
    entries_upserted: int
    results_upserted: int
    laps_upserted: int
    stale_entries_deleted: int
    stale_results_deleted: int
    stale_laps_deleted: int


def replace_archive_session(
    database: Session,
    *,
    session_id: int,
    snapshot: NormalizedSession,
    completed_at: datetime | None = None,
    source_updated_at: datetime | None = None,
) -> ArchivePersistenceSummary:
    """Atomically replace one session's FastF1 archive-owned sporting snapshot."""

    if database.in_transaction():
        raise ArchivePersistenceTransactionError(
            "replace_archive_session must own a new database transaction"
        )

    _validate_snapshot(snapshot)
    _validate_timestamp(completed_at, "completed_at")
    _validate_timestamp(source_updated_at, "source_updated_at")

    completion_time = completed_at or datetime.now(UTC)

    with database.begin():
        _lock_target_session(database, session_id)
        _ensure_archive_ownership(database, session_id)

        driver_ids = _upsert_drivers(database, snapshot.drivers)
        _clear_entry_unique_values(database, session_id)
        entry_ids = _upsert_entries(
            database,
            session_id=session_id,
            entries=snapshot.entries,
            driver_ids=driver_ids,
        )
        _upsert_results(database, snapshot.results, entry_ids)
        _upsert_laps(database, snapshot.laps, entry_ids)

        stale_laps_deleted = _delete_stale_laps(
            database,
            session_id=session_id,
            laps=snapshot.laps,
            entry_ids=entry_ids,
        )
        stale_results_deleted = _delete_stale_results(
            database,
            session_id=session_id,
            entry_ids=entry_ids,
        )
        stale_entries_deleted = _delete_stale_entries(
            database,
            session_id=session_id,
            entry_ids=entry_ids,
        )
        _mark_ingestion_completed(
            database,
            session_id=session_id,
            completed_at=completion_time,
            source_updated_at=source_updated_at,
        )

    return ArchivePersistenceSummary(
        session_id=session_id,
        drivers_upserted=len(snapshot.drivers),
        entries_upserted=len(snapshot.entries),
        results_upserted=len(snapshot.results),
        laps_upserted=len(snapshot.laps),
        stale_entries_deleted=stale_entries_deleted,
        stale_results_deleted=stale_results_deleted,
        stale_laps_deleted=stale_laps_deleted,
    )


def _validate_snapshot(snapshot: NormalizedSession) -> None:
    if not snapshot.entries:
        raise ArchivePersistenceContractError(
            "an archive snapshot must contain at least one session entry"
        )

    driver_ids = [driver.jolpica_driver_id for driver in snapshot.drivers]
    if len(driver_ids) != len(set(driver_ids)):
        raise ArchivePersistenceContractError("archive driver IDs must be unique")

    entry_keys = [entry.entry_key for entry in snapshot.entries]
    if len(entry_keys) != len(set(entry_keys)):
        raise ArchivePersistenceContractError("archive entry keys must be unique")
    entry_key_set = set(entry_keys)

    entry_driver_ids = {
        entry.jolpica_driver_id
        for entry in snapshot.entries
        if entry.jolpica_driver_id is not None
    }
    if entry_driver_ids != set(driver_ids):
        raise ArchivePersistenceContractError(
            "archive drivers must exactly match identified session entries"
        )

    result_keys = [result.entry_key for result in snapshot.results]
    if len(result_keys) != len(set(result_keys)) or set(result_keys) != entry_key_set:
        raise ArchivePersistenceContractError(
            "archive results must contain exactly one row for every session entry"
        )

    lap_keys = [(lap.entry_key, lap.lap_number) for lap in snapshot.laps]
    if len(lap_keys) != len(set(lap_keys)):
        raise ArchivePersistenceContractError(
            "archive laps must be unique by entry key and lap number"
        )
    if any(lap.entry_key not in entry_key_set for lap in snapshot.laps):
        raise ArchivePersistenceContractError(
            "every archive lap must reference a snapshot entry"
        )

    owned_records = (*snapshot.entries, *snapshot.results, *snapshot.laps)
    if any(record.source != ARCHIVE_SOURCE for record in owned_records):
        raise ArchivePersistenceContractError(
            "every persisted snapshot record must be owned by fastf1_archive"
        )
    if any(record.record_state != FINALIZED_STATE for record in owned_records):
        raise ArchivePersistenceContractError(
            "every persisted snapshot record must be finalized"
        )


def _validate_timestamp(value: datetime | None, field: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ArchivePersistenceContractError(f"{field} must include a timezone")


def _lock_target_session(database: Session, session_id: int) -> None:
    locked_session_id = database.scalar(
        select(RaceSession.id)
        .where(RaceSession.id == session_id)
        .with_for_update()
    )
    if locked_session_id is None:
        raise ArchiveSessionNotFoundError(
            f"database session {session_id} does not exist"
        )


def _ensure_archive_ownership(database: Session, session_id: int) -> None:
    conflict_queries = (
        select(SessionEntry.id)
        .where(
            SessionEntry.session_id == session_id,
            SessionEntry.source != ARCHIVE_SOURCE,
        )
        .limit(1),
        select(SessionResult.session_entry_id)
        .join(
            SessionEntry,
            SessionEntry.id == SessionResult.session_entry_id,
        )
        .where(
            SessionEntry.session_id == session_id,
            SessionResult.source != ARCHIVE_SOURCE,
        )
        .limit(1),
        select(Lap.id)
        .join(
            SessionEntry,
            SessionEntry.id == Lap.session_entry_id,
        )
        .where(
            SessionEntry.session_id == session_id,
            Lap.source != ARCHIVE_SOURCE,
        )
        .limit(1),
        select(SessionIngestion.session_id)
        .where(
            SessionIngestion.session_id == session_id,
            SessionIngestion.source != ARCHIVE_SOURCE,
        )
        .limit(1),
    )

    if any(database.scalar(query) is not None for query in conflict_queries):
        raise ArchiveSourceConflictError(
            f"database session {session_id} contains non-archive ingestion data"
        )


def _upsert_drivers(
    database: Session,
    drivers: tuple[NormalizedDriver, ...],
) -> dict[str, int]:
    if not drivers:
        return {}

    values = [
        {
            "jolpica_driver_id": driver.jolpica_driver_id,
            "given_name": driver.given_name,
            "family_name": driver.family_name,
            "full_name": driver.full_name,
            "country_code": driver.country_code,
        }
        for driver in drivers
    ]
    statement = insert(Driver).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=[Driver.jolpica_driver_id],
        set_={
            "given_name": statement.excluded.given_name,
            "family_name": statement.excluded.family_name,
            "full_name": statement.excluded.full_name,
            "country_code": statement.excluded.country_code,
            "updated_at": func.now(),
        },
    ).returning(Driver.id, Driver.jolpica_driver_id)

    rows = database.execute(statement).all()
    return {
        jolpica_driver_id: driver_id
        for driver_id, jolpica_driver_id in rows
        if jolpica_driver_id is not None
    }


def _clear_entry_unique_values(database: Session, session_id: int) -> None:
    database.execute(
        update(SessionEntry)
        .where(
            SessionEntry.session_id == session_id,
            SessionEntry.source == ARCHIVE_SOURCE,
        )
        .values(
            driver_id=None,
            racing_number=None,
            updated_at=func.now(),
        )
    )


def _upsert_entries(
    database: Session,
    *,
    session_id: int,
    entries: tuple[NormalizedSessionEntry, ...],
    driver_ids: dict[str, int],
) -> dict[str, int]:
    values = [
        {
            "session_id": session_id,
            "driver_id": (
                driver_ids[entry.jolpica_driver_id]
                if entry.jolpica_driver_id is not None
                else None
            ),
            "entry_key": entry.entry_key,
            "racing_number": entry.racing_number,
            "abbreviation": entry.abbreviation,
            "broadcast_name": entry.broadcast_name,
            "display_name": entry.display_name,
            "team_jolpica_id": entry.team_jolpica_id,
            "team_name": entry.team_name,
            "team_color": entry.team_color,
            "source": entry.source,
            "record_state": entry.record_state,
        }
        for entry in entries
    ]
    statement = insert(SessionEntry).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=[SessionEntry.session_id, SessionEntry.entry_key],
        set_={
            "driver_id": statement.excluded.driver_id,
            "racing_number": statement.excluded.racing_number,
            "abbreviation": statement.excluded.abbreviation,
            "broadcast_name": statement.excluded.broadcast_name,
            "display_name": statement.excluded.display_name,
            "team_jolpica_id": statement.excluded.team_jolpica_id,
            "team_name": statement.excluded.team_name,
            "team_color": statement.excluded.team_color,
            "source": statement.excluded.source,
            "record_state": statement.excluded.record_state,
            "updated_at": func.now(),
        },
    ).returning(SessionEntry.id, SessionEntry.entry_key)

    rows = database.execute(statement).all()
    return {entry_key: entry_id for entry_id, entry_key in rows}


def _upsert_results(
    database: Session,
    results: tuple[NormalizedSessionResult, ...],
    entry_ids: dict[str, int],
) -> None:
    values = [
        {
            "session_entry_id": entry_ids[result.entry_key],
            "position": result.position,
            "classified_position": result.classified_position,
            "grid_position": result.grid_position,
            "points": result.points,
            "status": result.status,
            "laps_completed": result.laps_completed,
            "q1_time_us": result.q1_time_us,
            "q2_time_us": result.q2_time_us,
            "q3_time_us": result.q3_time_us,
            "elapsed_time_us": result.elapsed_time_us,
            "gap_to_leader_us": result.gap_to_leader_us,
            "gap_to_leader_laps": result.gap_to_leader_laps,
            "source": result.source,
            "record_state": result.record_state,
        }
        for result in results
    ]
    statement = insert(SessionResult).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=[SessionResult.session_entry_id],
        set_={
            "position": statement.excluded.position,
            "classified_position": statement.excluded.classified_position,
            "grid_position": statement.excluded.grid_position,
            "points": statement.excluded.points,
            "status": statement.excluded.status,
            "laps_completed": statement.excluded.laps_completed,
            "q1_time_us": statement.excluded.q1_time_us,
            "q2_time_us": statement.excluded.q2_time_us,
            "q3_time_us": statement.excluded.q3_time_us,
            "elapsed_time_us": statement.excluded.elapsed_time_us,
            "gap_to_leader_us": statement.excluded.gap_to_leader_us,
            "gap_to_leader_laps": statement.excluded.gap_to_leader_laps,
            "source": statement.excluded.source,
            "record_state": statement.excluded.record_state,
            "updated_at": func.now(),
        },
    )
    database.execute(statement)


def _upsert_laps(
    database: Session,
    laps: tuple[NormalizedLap, ...],
    entry_ids: dict[str, int],
) -> None:
    values = [
        {
            "session_entry_id": entry_ids[lap.entry_key],
            "lap_number": lap.lap_number,
            "stint_number": lap.stint_number,
            "session_time_us": lap.session_time_us,
            "lap_time_us": lap.lap_time_us,
            "lap_start_time_us": lap.lap_start_time_us,
            "pit_out_time_us": lap.pit_out_time_us,
            "pit_in_time_us": lap.pit_in_time_us,
            "sector_1_time_us": lap.sector_1_time_us,
            "sector_2_time_us": lap.sector_2_time_us,
            "sector_3_time_us": lap.sector_3_time_us,
            "sector_1_session_time_us": lap.sector_1_session_time_us,
            "sector_2_session_time_us": lap.sector_2_session_time_us,
            "sector_3_session_time_us": lap.sector_3_session_time_us,
            "speed_i1_kph": lap.speed_i1_kph,
            "speed_i2_kph": lap.speed_i2_kph,
            "speed_fl_kph": lap.speed_fl_kph,
            "speed_st_kph": lap.speed_st_kph,
            "is_personal_best": lap.is_personal_best,
            "compound": lap.compound,
            "tyre_life_laps": lap.tyre_life_laps,
            "fresh_tyre": lap.fresh_tyre,
            "track_status": lap.track_status,
            "position": lap.position,
            "deleted": lap.deleted,
            "deleted_reason": lap.deleted_reason,
            "fastf1_generated": lap.fastf1_generated,
            "is_accurate": lap.is_accurate,
            "source": lap.source,
            "record_state": lap.record_state,
        }
        for lap in laps
    ]

    for batch in _batches(values, LAP_UPSERT_BATCH_SIZE):
        statement = insert(Lap).values(batch)
        statement = statement.on_conflict_do_update(
            index_elements=[Lap.session_entry_id, Lap.lap_number],
            set_={
                "stint_number": statement.excluded.stint_number,
                "session_time_us": statement.excluded.session_time_us,
                "lap_time_us": statement.excluded.lap_time_us,
                "lap_start_time_us": statement.excluded.lap_start_time_us,
                "pit_out_time_us": statement.excluded.pit_out_time_us,
                "pit_in_time_us": statement.excluded.pit_in_time_us,
                "sector_1_time_us": statement.excluded.sector_1_time_us,
                "sector_2_time_us": statement.excluded.sector_2_time_us,
                "sector_3_time_us": statement.excluded.sector_3_time_us,
                "sector_1_session_time_us": (
                    statement.excluded.sector_1_session_time_us
                ),
                "sector_2_session_time_us": (
                    statement.excluded.sector_2_session_time_us
                ),
                "sector_3_session_time_us": (
                    statement.excluded.sector_3_session_time_us
                ),
                "speed_i1_kph": statement.excluded.speed_i1_kph,
                "speed_i2_kph": statement.excluded.speed_i2_kph,
                "speed_fl_kph": statement.excluded.speed_fl_kph,
                "speed_st_kph": statement.excluded.speed_st_kph,
                "is_personal_best": statement.excluded.is_personal_best,
                "compound": statement.excluded.compound,
                "tyre_life_laps": statement.excluded.tyre_life_laps,
                "fresh_tyre": statement.excluded.fresh_tyre,
                "track_status": statement.excluded.track_status,
                "position": statement.excluded.position,
                "deleted": statement.excluded.deleted,
                "deleted_reason": statement.excluded.deleted_reason,
                "fastf1_generated": statement.excluded.fastf1_generated,
                "is_accurate": statement.excluded.is_accurate,
                "source": statement.excluded.source,
                "record_state": statement.excluded.record_state,
                "updated_at": func.now(),
            },
        )
        database.execute(statement)


def _delete_stale_laps(
    database: Session,
    *,
    session_id: int,
    laps: tuple[NormalizedLap, ...],
    entry_ids: dict[str, int],
) -> int:
    session_entry_ids = select(SessionEntry.id).where(
        SessionEntry.session_id == session_id
    )
    statement = delete(Lap).where(
        Lap.session_entry_id.in_(session_entry_ids),
        Lap.source == ARCHIVE_SOURCE,
    )

    incoming_keys = [
        (entry_ids[lap.entry_key], lap.lap_number)
        for lap in laps
    ]
    if incoming_keys:
        statement = statement.where(
            tuple_(Lap.session_entry_id, Lap.lap_number).not_in(incoming_keys)
        )

    return _affected_rows(database.execute(statement))


def _delete_stale_results(
    database: Session,
    *,
    session_id: int,
    entry_ids: dict[str, int],
) -> int:
    session_entry_ids = select(SessionEntry.id).where(
        SessionEntry.session_id == session_id
    )
    statement = delete(SessionResult).where(
        SessionResult.session_entry_id.in_(session_entry_ids),
        SessionResult.session_entry_id.not_in(list(entry_ids.values())),
        SessionResult.source == ARCHIVE_SOURCE,
    )
    return _affected_rows(database.execute(statement))


def _delete_stale_entries(
    database: Session,
    *,
    session_id: int,
    entry_ids: dict[str, int],
) -> int:
    statement = delete(SessionEntry).where(
        SessionEntry.session_id == session_id,
        SessionEntry.id.not_in(list(entry_ids.values())),
        SessionEntry.source == ARCHIVE_SOURCE,
    )
    return _affected_rows(database.execute(statement))


def _mark_ingestion_completed(
    database: Session,
    *,
    session_id: int,
    completed_at: datetime,
    source_updated_at: datetime | None,
) -> None:
    statement = insert(SessionIngestion).values(
        session_id=session_id,
        status="completed",
        source=ARCHIVE_SOURCE,
        record_state=FINALIZED_STATE,
        completed_at=completed_at,
        source_updated_at=source_updated_at,
        heartbeat_at=None,
        next_retry_at=None,
        last_error_code=None,
        last_error_message=None,
    )
    update_values: dict[str, Any] = {
        "status": "completed",
        "source": ARCHIVE_SOURCE,
        "record_state": FINALIZED_STATE,
        "completed_at": completed_at,
        "heartbeat_at": None,
        "next_retry_at": None,
        "last_error_code": None,
        "last_error_message": None,
        "updated_at": func.now(),
    }
    if source_updated_at is not None:
        update_values["source_updated_at"] = source_updated_at

    database.execute(
        statement.on_conflict_do_update(
            index_elements=[SessionIngestion.session_id],
            set_=update_values,
        )
    )


def _batches(
    values: list[dict[str, Any]],
    batch_size: int,
) -> Iterator[list[dict[str, Any]]]:
    for start in range(0, len(values), batch_size):
        yield values[start : start + batch_size]


def _affected_rows(result: Any) -> int:
    rowcount = result.rowcount
    return rowcount if rowcount is not None and rowcount > 0 else 0
